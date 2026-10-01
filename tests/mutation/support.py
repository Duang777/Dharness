from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Callable

from pydantic import JsonValue

from evidence_harness.protocol import (
    ActionKind,
    AgentDecision,
    CheckIsolationEvidence,
    CheckKind,
    CommandMode,
    CommandReceipt,
    CompletionIsolationEvidence,
    FilesystemDelta,
    IsolationCost,
    OutputExcerpt,
    RequirementCoverage,
    ReviewDecision,
    SemanticAssessment,
    SourceAttestation,
    VerificationCheck,
    VerificationReceipt,
)
from evidence_harness_mutation import StatePrefix, load_state_prefix

_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
_SOURCE_COMMIT = "c" * 40
_CANDIDATE = "sha256:" + "d" * 64


def _event(event_type: str, payload: dict[str, object]) -> str:
    return json.dumps(
        {
            "timestamp": "2026-10-01T00:00:00+00:00",
            "type": event_type,
            "payload": payload,
        },
        sort_keys=True,
    )


def _receipt(check: VerificationCheck, sequence: int) -> CommandReceipt:
    output = OutputExcerpt(
        head="",
        tail="",
        total_bytes=0,
        omitted_bytes=0,
        sha256=_EMPTY_SHA256,
    )
    return CommandReceipt(
        sequence=sequence,
        command_id=check.id,
        script=check.script,
        purpose=check.proves,
        cwd=check.cwd,
        mode=CommandMode.OBSERVE,
        work_epoch=1,
        return_code=0,
        duration_sec=0.1,
        stdout=output,
        stderr=output,
        command_fingerprint=f"{sequence}" * 64,
        observation_fingerprint=f"{sequence + 2}" * 64,
    )


def valid_journal() -> bytes:
    checks = (
        VerificationCheck(
            id="behavior",
            kind=CheckKind.BEHAVIOR,
            script="pytest -q",
            proves="the behavior works",
        ),
        VerificationCheck(
            id="build",
            kind=CheckKind.BUILD,
            script="python -m build",
            proves="the package builds",
        ),
    )
    coverage = (
        RequirementCoverage(
            requirement="the artifact is complete",
            check_ids=("behavior", "build"),
        ),
    )
    decision = AgentDecision(
        action=ActionKind.FINISH,
        rationale="verify the completed artifact",
        checks=checks,
        coverage=coverage,
    )
    receipts = tuple(_receipt(check, sequence) for sequence, check in enumerate(checks, start=1))
    isolated = tuple(
        CheckIsolationEvidence(
            check_id=receipt.command_id,
            receipt_sequence=receipt.sequence,
            receipt_observation_sha256=receipt.observation_fingerprint,
            child_id_sha256=f"{receipt.sequence}" * 64,
            started_from_image_id=_CANDIDATE,
            delta=FilesystemDelta(sha256=_EMPTY_SHA256),
            disposed=True,
        )
        for receipt in receipts
    )
    isolation = CompletionIsolationEvidence(
        backend="test-isolation-v1",
        attempt_id=1,
        work_epoch=1,
        candidate_image_id=_CANDIDATE,
        environment_identity_sha256="e" * 64,
        checks=isolated,
        source=SourceAttestation(
            container_id_sha256="f" * 64,
            diff_sha256_before=_EMPTY_SHA256,
            diff_sha256_after=_EMPTY_SHA256,
            remained_paused=True,
            resumed=True,
        ),
        snapshot_image_disposed=True,
        cost=IsolationCost(host_operations=2, child_count=2, duration_sec=0.2),
    )
    verification = VerificationReceipt(
        work_epoch=1,
        checks=receipts,
        coverage=coverage,
        isolation=isolation,
        semantic_assessment=SemanticAssessment(
            accepted=True,
            rationale="the executed evidence proves the requirement",
        ),
        accepted=True,
    )
    events = [
        _event(
            "run_started",
            {
                "journal_schema_version": 2,
                "instruction": "build the artifact",
                "options": {"enable_completion_review": True},
            },
        ),
        _event("agent_decision", decision.model_dump(mode="json")),
        _event(
            "completion_isolation_started",
            {
                "attempt_id": 1,
                "work_epoch": 1,
                "backend": "test-isolation-v1",
                "check_ids": [check.id for check in checks],
            },
        ),
        _event(
            "completion_candidate_committed",
            {
                "attempt_id": 1,
                "candidate_image_id": _CANDIDATE,
                "source_container_id_sha256": "f" * 64,
            },
        ),
    ]
    for ordinal, (check, receipt, record) in enumerate(
        zip(checks, receipts, isolated, strict=True),
        start=1,
    ):
        events.extend(
            (
                _event(
                    "completion_check_started",
                    {
                        "attempt_id": 1,
                        "check_id": check.id,
                        "ordinal": ordinal,
                    },
                ),
                _event("command_receipt", receipt.model_dump(mode="json")),
                _event(
                    "completion_check_isolated",
                    record.model_copy(update={"disposed": False}).model_dump(mode="json"),
                ),
                _event(
                    "completion_check_disposed",
                    {
                        "attempt_id": 1,
                        "check_id": check.id,
                        "child_id_sha256": record.child_id_sha256,
                    },
                ),
            )
        )
    events.extend(
        (
            _event(
                "completion_review",
                ReviewDecision(
                    verdict="accept",
                    rationale="the executed evidence proves the requirement",
                ).model_dump(mode="json"),
            ),
            _event("verification_receipt", verification.model_dump(mode="json")),
            _event("run_finished", {"stop_reason": "verified"}),
        )
    )
    return ("\n".join(events) + "\n").encode()


def load_valid_prefix(*, through_line: int | None = None) -> StatePrefix:
    data = valid_journal()
    return load_state_prefix(
        data,
        source_commit=_SOURCE_COMMIT,
        expected_journal_sha256=hashlib.sha256(data).hexdigest(),
        through_line=through_line,
    )


def mutate_event(
    prefix: StatePrefix,
    event_type: str,
    mutate: Callable[[dict[str, JsonValue]], dict[str, JsonValue]],
    *,
    replacement_type: str | None = None,
) -> StatePrefix:
    events = list(prefix.events)
    index = next(index for index, event in enumerate(events) if event.event_type == event_type)
    event = events[index]
    payload = mutate(copy.deepcopy(event.payload))
    events[index] = event.model_copy(
        update={
            "event_type": replacement_type or event.event_type,
            "payload": payload,
        }
    )
    return prefix.model_copy(update={"events": tuple(events)})
