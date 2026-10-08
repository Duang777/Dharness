from __future__ import annotations

import hashlib
import json

import pytest

from evidence_harness.completion_contract import CompletionContract
from evidence_harness.protocol import LoopOptions
from evidence_harness_mutation import load_state_prefix
from evidence_harness_mutation.control_invariants import (
    ControlInvariantId,
    ControlInvariantStatus,
    audit_control_trace,
)
from evidence_harness_mutation.control_operators import (
    ControlMutationId,
    ControlMutationRequest,
    apply_control_mutation,
)
from mutation.support import load_valid_prefix, valid_journal

_SOURCE_COMMIT = "c" * 40


def _control_prefix():
    events = [json.loads(line) for line in valid_journal().splitlines()]
    options = LoopOptions(
        max_turns=4,
        max_environment_calls=6,
        max_repairs=1,
        max_recoveries=1,
        max_completion_reviews=1,
        max_wall_time_sec=100,
        verification_environment_reserve=2,
    )
    events[0]["payload"].update(
        {
            "control_audit_version": 1,
            "options": {
                **events[0]["payload"]["options"],
                **{
                    name: getattr(options, name)
                    for name in (
                        "max_turns",
                        "max_environment_calls",
                        "max_repairs",
                        "max_recoveries",
                        "max_completion_reviews",
                        "max_wall_time_sec",
                        "verification_environment_reserve",
                    )
                },
            },
            "completion_contract": CompletionContract.from_instruction(
                "build the artifact",
                options,
            ).model_dump(mode="json"),
        }
    )
    events[1:1] = [
        {
            "timestamp": "2026-10-01T00:00:00+00:00",
            "type": "work_batch_admission",
            "payload": {
                "accepted": True,
                "reasons": [],
                "command_ids": ["inspect"],
                "requested_environment_calls": 1,
                "environment_calls_used": 1,
                "environment_calls_remaining": 5,
                "verification_environment_reserve": 2,
                "available_for_work": 3,
                "turns_used": 1,
                "turns_remaining": 3,
                "started_in_finalization": False,
            },
        },
        {
            "timestamp": "2026-10-01T00:00:00+00:00",
            "type": "work_batch_started",
            "payload": {
                "work_epoch": 1,
                "command_ids": ["inspect"],
                "started_in_finalization": False,
            },
        },
        {
            "timestamp": "2026-10-01T00:00:00+00:00",
            "type": "work_batch_finished",
            "payload": {
                "work_epoch": 1,
                "command_ids": ["inspect"],
                "completed_command_ids": ["inspect"],
                "failed_command_ids": [],
                "successful_change_ids": [],
                "novel_observation_ids": ["inspect"],
                "progressed": True,
                "stagnant_batches_before": 0,
                "stagnant_batches_after": 0,
            },
        },
    ]
    events[-1]["payload"].update(
        {
            "turns_used": 2,
            "environment_calls_used": 3,
            "repairs_used": 0,
            "recoveries_used": 0,
        }
    )
    data = ("\n".join(json.dumps(event, sort_keys=True) for event in events) + "\n").encode()
    return load_state_prefix(
        data,
        source_commit=_SOURCE_COMMIT,
        expected_journal_sha256=hashlib.sha256(data).hexdigest(),
    )


def test_control_auditor_reports_i1_through_i8() -> None:
    report = audit_control_trace(_control_prefix())

    assert report.passed is True
    assert [result.invariant for result in report.results] == list(ControlInvariantId)
    assert all(result.status is ControlInvariantStatus.PASS for result in report.results)


def test_legacy_trace_marks_new_fact_dependent_invariants_unsupported() -> None:
    report = audit_control_trace(load_valid_prefix())

    assert all(
        report.result(invariant).status is ControlInvariantStatus.UNSUPPORTED
        for invariant in (
            ControlInvariantId.I6,
            ControlInvariantId.I7,
            ControlInvariantId.I8,
        )
    )


@pytest.mark.parametrize(
    ("operator", "expected_invariant"),
    [
        (ControlMutationId.STALE_EVIDENCE_EPOCH, ControlInvariantId.I1),
        (ControlMutationId.REORDER_CHECK_RECEIPTS, ControlInvariantId.I2),
        (ControlMutationId.REVIEW_TIMEOUT_FALLBACK, ControlInvariantId.I3),
        (ControlMutationId.CROSS_CANDIDATE_EVIDENCE, ControlInvariantId.I4),
        (ControlMutationId.LIVE_CANDIDATE_CHANGED, ControlInvariantId.I5),
        (ControlMutationId.CONSUME_VERIFICATION_RESERVE, ControlInvariantId.I6),
        (ControlMutationId.FAILED_COMMAND_PROGRESS, ControlInvariantId.I7),
        (ControlMutationId.REPAIR_OVER_BUDGET, ControlInvariantId.I8),
    ],
)
def test_control_mutations_are_deterministic_and_detected(
    operator: ControlMutationId,
    expected_invariant: ControlInvariantId,
) -> None:
    prefix = _control_prefix()
    request = ControlMutationRequest(operator=operator)

    first = apply_control_mutation(prefix, request)
    second = apply_control_mutation(prefix, request)
    report = audit_control_trace(first.trace)

    assert first == second
    assert first.expected_invariant is expected_invariant
    assert first.target.source.matches(prefix.events[first.target.source.line - 1])
    assert report.result(expected_invariant).status is ControlInvariantStatus.FAIL
