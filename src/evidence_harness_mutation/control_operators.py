from __future__ import annotations

from enum import StrEnum

from pydantic import Field, JsonValue

from evidence_harness.protocol import VerificationReceipt
from evidence_harness_mutation.attempts import parse_event
from evidence_harness_mutation.control_invariants import ControlInvariantId
from evidence_harness_mutation.model import (
    FrozenModel,
    RecordedEvent,
    SourceEventAnchor,
    StatePrefix,
)
from evidence_harness_mutation.operators import (
    MutationId,
    MutationNotApplicable,
    MutationRequest,
    apply_mutation,
)


class ControlMutationId(StrEnum):
    STALE_EVIDENCE_EPOCH = "stale_evidence_epoch"
    REORDER_CHECK_RECEIPTS = "reorder_check_receipts"
    REVIEW_TIMEOUT_FALLBACK = "review_timeout_fallback"
    CROSS_CANDIDATE_EVIDENCE = "cross_candidate_evidence"
    LIVE_CANDIDATE_CHANGED = "live_candidate_changed"
    CONSUME_VERIFICATION_RESERVE = "consume_verification_reserve"
    FAILED_COMMAND_PROGRESS = "failed_command_progress"
    REPAIR_OVER_BUDGET = "repair_over_budget"


class ControlMutationRequest(FrozenModel):
    operator: ControlMutationId
    attempt_ordinal: int = Field(default=1, ge=1)


class ControlMutationTarget(FrozenModel):
    source: SourceEventAnchor
    original_event_type: str = Field(min_length=1)
    mutated_event_type: str = Field(min_length=1)


class AppliedControlMutation(FrozenModel):
    request: ControlMutationRequest
    expected_invariant: ControlInvariantId
    target: ControlMutationTarget
    trace: StatePrefix


def apply_control_mutation(
    prefix: StatePrefix,
    request: ControlMutationRequest,
) -> AppliedControlMutation:
    legacy_operator = _legacy_operator(request.operator)
    if legacy_operator is not None:
        applied = apply_mutation(
            prefix,
            MutationRequest(
                operator=legacy_operator,
                attempt_ordinal=request.attempt_ordinal,
            ),
        )
        return AppliedControlMutation(
            request=request,
            expected_invariant=ControlInvariantId(applied.expected_invariant.value),
            target=ControlMutationTarget(
                source=applied.target.source,
                original_event_type=applied.target.original_event_type,
                mutated_event_type=applied.target.mutated_event_type,
            ),
            trace=applied.trace,
        )

    invariant, original, replacement = _apply_new_operator(prefix, request.operator)
    events = tuple(replacement if event.line == original.line else event for event in prefix.events)
    return AppliedControlMutation(
        request=request,
        expected_invariant=invariant,
        target=ControlMutationTarget(
            source=SourceEventAnchor.from_event(original),
            original_event_type=original.event_type,
            mutated_event_type=replacement.event_type,
        ),
        trace=prefix.model_copy(update={"events": events}),
    )


def _legacy_operator(operator: ControlMutationId) -> MutationId | None:
    try:
        return MutationId(operator.value)
    except ValueError:
        return None


def _apply_new_operator(
    prefix: StatePrefix,
    operator: ControlMutationId,
) -> tuple[ControlInvariantId, RecordedEvent, RecordedEvent]:
    if operator is ControlMutationId.LIVE_CANDIDATE_CHANGED:
        original = _single_event(prefix, "verification_receipt")
        receipt = parse_event(original, VerificationReceipt)
        if receipt.isolation is None:
            raise MutationNotApplicable("live-candidate mutation requires isolation evidence")
        isolation = receipt.isolation.model_copy(
            update={
                "source": receipt.isolation.source.model_copy(update={"remained_paused": False})
            }
        )
        replacement = _replace_payload(
            original,
            receipt.model_copy(update={"isolation": isolation}).model_dump(mode="json"),
        )
        return ControlInvariantId.I5, original, replacement

    if operator is ControlMutationId.CONSUME_VERIFICATION_RESERVE:
        original = _single_event(prefix, "work_batch_admission")
        payload = dict(original.payload)
        available = payload.get("available_for_work")
        if not isinstance(available, int) or isinstance(available, bool):
            raise MutationNotApplicable("work admission has no integer allowance")
        payload["accepted"] = True
        payload["requested_environment_calls"] = available + 1
        return ControlInvariantId.I6, original, _replace_payload(original, payload)

    if operator is ControlMutationId.FAILED_COMMAND_PROGRESS:
        original = _single_event(prefix, "work_batch_finished")
        payload = dict(original.payload)
        completed = payload.get("completed_command_ids")
        if not isinstance(completed, list) or not completed:
            raise MutationNotApplicable("work batch has no completed command")
        payload.update(
            {
                "failed_command_ids": [completed[0]],
                "successful_change_ids": [],
                "novel_observation_ids": [],
                "progressed": True,
                "stagnant_batches_after": 0,
            }
        )
        return ControlInvariantId.I7, original, _replace_payload(original, payload)

    if operator is ControlMutationId.REPAIR_OVER_BUDGET:
        original = _single_event(prefix, "run_finished")
        started = prefix.events[0].payload
        contract = started.get("completion_contract")
        if not isinstance(contract, dict):
            raise MutationNotApplicable("control contract is missing")
        budget = contract.get("b_req")
        if not isinstance(budget, dict):
            raise MutationNotApplicable("control budget is missing")
        max_repairs = budget.get("max_repairs")
        if not isinstance(max_repairs, int) or isinstance(max_repairs, bool):
            raise MutationNotApplicable("control repair budget is missing")
        payload = dict(original.payload)
        payload["repairs_used"] = max_repairs + 1
        return ControlInvariantId.I8, original, _replace_payload(original, payload)

    raise AssertionError(f"unhandled control mutation: {operator}")


def _single_event(prefix: StatePrefix, event_type: str) -> RecordedEvent:
    events = tuple(event for event in prefix.events if event.event_type == event_type)
    if len(events) != 1:
        raise MutationNotApplicable(
            f"control mutation requires exactly one {event_type}, found {len(events)}"
        )
    return events[0]


def _replace_payload(
    event: RecordedEvent,
    payload: dict[str, JsonValue],
) -> RecordedEvent:
    return event.model_copy(update={"payload": payload})
