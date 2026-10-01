from __future__ import annotations

import hashlib
from collections.abc import Callable
from enum import StrEnum

from pydantic import Field

from evidence_harness.protocol import VerificationReceipt
from evidence_harness_mutation.attempts import (
    CompletionAttempt,
    parse_event,
    project_completion_attempts,
)
from evidence_harness_mutation.model import (
    FrozenModel,
    InvariantId,
    RecordedEvent,
    SourceEventAnchor,
    StatePrefix,
)


class MutationId(StrEnum):
    STALE_EVIDENCE_EPOCH = "stale_evidence_epoch"
    REORDER_CHECK_RECEIPTS = "reorder_check_receipts"
    REVIEW_TIMEOUT_FALLBACK = "review_timeout_fallback"
    CROSS_CANDIDATE_EVIDENCE = "cross_candidate_evidence"


class MutationRequest(FrozenModel):
    operator: MutationId
    attempt_ordinal: int = Field(default=1, ge=1)


class AttemptAnchor(FrozenModel):
    proposal: SourceEventAnchor
    terminal: SourceEventAnchor | None = None


class MutationTarget(FrozenModel):
    source: SourceEventAnchor
    original_event_type: str = Field(min_length=1)
    mutated_event_type: str = Field(min_length=1)


class AppliedMutation(FrozenModel):
    request: MutationRequest
    expected_invariant: InvariantId
    attempt: AttemptAnchor
    target: MutationTarget
    trace: StatePrefix

    @property
    def target_line(self) -> int:
        return self.target.source.line

    @property
    def source_line_sha256(self) -> str:
        return self.target.source.line_sha256


class MutationNotApplicable(ValueError):
    pass


_Mutation = Callable[[CompletionAttempt], tuple[RecordedEvent, RecordedEvent]]


def apply_mutation(prefix: StatePrefix, request: MutationRequest) -> AppliedMutation:
    attempts = project_completion_attempts(prefix.events)
    attempt = next(
        (candidate for candidate in attempts if candidate.ordinal == request.attempt_ordinal),
        None,
    )
    if attempt is None:
        raise MutationNotApplicable(
            f"completion attempt {request.attempt_ordinal} is not present in the prefix"
        )

    mutations: dict[MutationId, tuple[InvariantId, _Mutation]] = {
        MutationId.STALE_EVIDENCE_EPOCH: (InvariantId.I1, _stale_evidence_epoch),
        MutationId.REORDER_CHECK_RECEIPTS: (InvariantId.I2, _reorder_check_receipts),
        MutationId.REVIEW_TIMEOUT_FALLBACK: (InvariantId.I3, _review_timeout_fallback),
        MutationId.CROSS_CANDIDATE_EVIDENCE: (
            InvariantId.I4,
            _cross_candidate_evidence,
        ),
    }
    expected_invariant, mutate = mutations[request.operator]
    original, replacement = mutate(attempt)
    events = tuple(replacement if event.line == original.line else event for event in prefix.events)
    terminal = (
        SourceEventAnchor.from_event(_event_at_line(attempt, attempt.terminal_line))
        if attempt.terminal_line is not None
        else None
    )
    return AppliedMutation(
        request=request,
        expected_invariant=expected_invariant,
        attempt=AttemptAnchor(
            proposal=SourceEventAnchor.from_event(attempt.proposal_event),
            terminal=terminal,
        ),
        target=MutationTarget(
            source=SourceEventAnchor.from_event(original),
            original_event_type=original.event_type,
            mutated_event_type=replacement.event_type,
        ),
        trace=StatePrefix.model_validate(
            {
                **prefix.model_dump(mode="python"),
                "events": tuple(event.model_dump(mode="python") for event in events),
            }
        ),
    )


def _stale_evidence_epoch(
    attempt: CompletionAttempt,
) -> tuple[RecordedEvent, RecordedEvent]:
    start = _single_event(attempt, "completion_isolation_started")
    work_epoch = start.payload.get("work_epoch")
    if not isinstance(work_epoch, int) or isinstance(work_epoch, bool) or work_epoch < 1:
        raise MutationNotApplicable("stale epoch mutation requires a positive current work epoch")
    target = _single_event(attempt, "verification_receipt")
    receipt = parse_event(target, VerificationReceipt)
    if receipt.isolation is None:
        raise MutationNotApplicable("stale epoch mutation requires isolation evidence")
    stale_epoch = work_epoch - 1
    replacement = receipt.model_copy(
        update={
            "work_epoch": stale_epoch,
            "checks": tuple(
                check.model_copy(update={"work_epoch": stale_epoch}) for check in receipt.checks
            ),
            "isolation": receipt.isolation.model_copy(update={"work_epoch": stale_epoch}),
        }
    )
    return target, _replace_payload(target, replacement.model_dump(mode="json"))


def _reorder_check_receipts(
    attempt: CompletionAttempt,
) -> tuple[RecordedEvent, RecordedEvent]:
    target = _single_event(attempt, "verification_receipt")
    receipt = parse_event(target, VerificationReceipt)
    if len(receipt.checks) < 2:
        raise MutationNotApplicable("check reorder mutation requires at least two receipts")
    replacement = receipt.model_copy(update={"checks": tuple(reversed(receipt.checks))})
    return target, _replace_payload(target, replacement.model_dump(mode="json"))


def _review_timeout_fallback(
    attempt: CompletionAttempt,
) -> tuple[RecordedEvent, RecordedEvent]:
    target = _single_event(attempt, "completion_review")
    replacement = target.model_copy(
        update={
            "event_type": "completion_review_error",
            "payload": {
                "error_type": "TimeoutError",
                "error": "injected completion review timeout",
            },
        }
    )
    return target, replacement


def _cross_candidate_evidence(
    attempt: CompletionAttempt,
) -> tuple[RecordedEvent, RecordedEvent]:
    target = _single_event(attempt, "verification_receipt")
    receipt = parse_event(target, VerificationReceipt)
    if receipt.isolation is None:
        raise MutationNotApplicable("candidate mutation requires isolation evidence")
    original_candidate = receipt.isolation.candidate_image_id
    mutated_candidate = (
        "sha256:"
        + hashlib.sha256(
            f"{original_candidate}:{MutationId.CROSS_CANDIDATE_EVIDENCE}".encode()
        ).hexdigest()
    )
    isolation = receipt.isolation.model_copy(
        update={
            "candidate_image_id": mutated_candidate,
            "checks": tuple(
                check.model_copy(update={"started_from_image_id": mutated_candidate})
                for check in receipt.isolation.checks
            ),
        }
    )
    replacement = receipt.model_copy(update={"isolation": isolation})
    return target, _replace_payload(target, replacement.model_dump(mode="json"))


def _single_event(attempt: CompletionAttempt, event_type: str) -> RecordedEvent:
    events = attempt.of_type(event_type)
    if len(events) != 1:
        raise MutationNotApplicable(
            f"{attempt.ordinal=} requires exactly one {event_type}, found {len(events)}"
        )
    return events[0]


def _event_at_line(attempt: CompletionAttempt, line: int) -> RecordedEvent:
    event = next((event for event in attempt.events if event.line == line), None)
    if event is None:
        raise AssertionError(f"completion attempt has no event on terminal line {line}")
    return event


def _replace_payload(
    event: RecordedEvent,
    payload: dict[str, object],
) -> RecordedEvent:
    return event.model_copy(update={"payload": payload})
