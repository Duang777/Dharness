from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evidence_harness.completion_contract import CompletionContract
from evidence_harness.protocol import VerificationReceipt
from evidence_harness_mutation.attempts import (
    CompletionAttempt,
    parse_event,
    project_completion_attempts,
)
from evidence_harness_mutation.invariants import audit_completion_trace
from evidence_harness_mutation.model import (
    EventOrdinal,
    FrozenModel,
    JournalBinding,
    OfflineTrace,
    RecordedEvent,
    TraceStructureError,
)


class ControlInvariantId(StrEnum):
    I1 = "I1"
    I2 = "I2"
    I3 = "I3"
    I4 = "I4"
    I5 = "I5"
    I6 = "I6"
    I7 = "I7"
    I8 = "I8"


class ControlInvariantStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    NOT_APPLICABLE = "not_applicable"
    UNSUPPORTED = "unsupported"


class ControlInvariantViolation(FrozenModel):
    invariant: ControlInvariantId
    terminal_line: EventOrdinal
    attempt_ordinal: int = Field(ge=1)
    related_lines: tuple[EventOrdinal, ...] = ()
    details: tuple[str, ...] = Field(min_length=1)


class ControlInvariantResult(FrozenModel):
    invariant: ControlInvariantId
    status: ControlInvariantStatus
    violations: tuple[ControlInvariantViolation, ...] = ()

    @model_validator(mode="after")
    def validate_status(self):
        if self.status is ControlInvariantStatus.FAIL and not self.violations:
            raise ValueError("failed invariant result requires a violation")
        if self.status is not ControlInvariantStatus.FAIL and self.violations:
            raise ValueError("only failed invariant results may contain violations")
        return self


class ControlAuditReport(FrozenModel):
    source: JournalBinding
    through_line: EventOrdinal
    verified_attempts: int = Field(ge=0)
    results: tuple[ControlInvariantResult, ...] = Field(min_length=8, max_length=8)

    @model_validator(mode="after")
    def validate_invariant_order(self):
        if tuple(result.invariant for result in self.results) != tuple(ControlInvariantId):
            raise ValueError("control audit results must contain I1 through I8 in order")
        return self

    @property
    def passed(self) -> bool:
        return self.verified_attempts > 0 and all(
            result.status is ControlInvariantStatus.PASS for result in self.results
        )

    @property
    def violations(self) -> tuple[ControlInvariantViolation, ...]:
        return tuple(violation for result in self.results for violation in result.violations)

    def result(self, invariant: ControlInvariantId) -> ControlInvariantResult:
        return next(result for result in self.results if result.invariant is invariant)


class _EventPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _WorkBatchAdmission(_EventPayload):
    accepted: bool
    command_ids: tuple[str, ...]
    requested_environment_calls: int = Field(ge=1)
    environment_calls_used: int = Field(ge=0)
    environment_calls_remaining: int = Field(ge=0)
    verification_environment_reserve: int = Field(ge=0)
    available_for_work: int
    turns_used: int = Field(ge=0)
    turns_remaining: int


class _WorkBatchStarted(_EventPayload):
    work_epoch: int = Field(ge=1)
    command_ids: tuple[str, ...] = Field(min_length=1)


class _WorkBatchFinished(_EventPayload):
    work_epoch: int = Field(ge=1)
    command_ids: tuple[str, ...] = Field(min_length=1)
    completed_command_ids: tuple[str, ...]
    failed_command_ids: tuple[str, ...]
    successful_change_ids: tuple[str, ...]
    novel_observation_ids: tuple[str, ...]
    progressed: bool
    stagnant_batches_before: int = Field(ge=0)
    stagnant_batches_after: int = Field(ge=0)


class _RepairAdmission(_EventPayload):
    accepted: bool
    repair_count_before: int = Field(ge=0)
    repair_count_after: int = Field(ge=0)
    max_repairs: int = Field(ge=0)
    completion_reviews_used: int = Field(ge=0)
    max_completion_reviews: int = Field(ge=0)


class _RunFinished(_EventPayload):
    repairs_used: int = Field(ge=0)


def audit_control_trace(trace: OfflineTrace) -> ControlAuditReport:
    attempts = tuple(
        attempt for attempt in project_completion_attempts(trace.events) if attempt.verified
    )
    legacy = audit_completion_trace(trace)
    results = [_translate_legacy_result(result) for result in legacy.results]
    results.append(_audit_i5(attempts))

    contract = _control_contract(trace)
    if contract is None:
        results.extend(
            _unsupported(invariant)
            for invariant in (
                ControlInvariantId.I6,
                ControlInvariantId.I7,
                ControlInvariantId.I8,
            )
        )
    elif not attempts:
        results.extend(
            _not_applicable(invariant)
            for invariant in (
                ControlInvariantId.I6,
                ControlInvariantId.I7,
                ControlInvariantId.I8,
            )
        )
    else:
        terminal_line = attempts[-1].terminal_line
        assert terminal_line is not None
        results.extend(
            (
                _audit_i6(trace, contract, terminal_line),
                _audit_i7(trace, terminal_line),
                _audit_i8(trace, contract, terminal_line),
            )
        )

    return ControlAuditReport(
        source=trace.source,
        through_line=trace.through_line,
        verified_attempts=len(attempts),
        results=tuple(results),
    )


def _translate_legacy_result(result) -> ControlInvariantResult:
    invariant = ControlInvariantId(result.invariant.value)
    status = ControlInvariantStatus(result.status.value)
    violations = tuple(
        ControlInvariantViolation(
            invariant=invariant,
            terminal_line=violation.terminal_line,
            attempt_ordinal=violation.attempt_ordinal,
            related_lines=violation.related_lines,
            details=violation.details,
        )
        for violation in result.violations
    )
    return ControlInvariantResult(
        invariant=invariant,
        status=status,
        violations=violations,
    )


def _audit_i5(
    attempts: tuple[CompletionAttempt, ...],
) -> ControlInvariantResult:
    invariant = ControlInvariantId.I5
    if not attempts:
        return _not_applicable(invariant)

    violations: list[ControlInvariantViolation] = []
    for attempt in attempts:
        details: list[str] = []
        receipt_events = attempt.of_type("verification_receipt")
        receipts = tuple(parse_event(event, VerificationReceipt) for event in receipt_events)
        accepted = tuple(receipt for receipt in receipts if receipt.accepted)
        if len(accepted) != 1 or accepted[0].isolation is None:
            details.append("verified attempt has no accepted isolated verification receipt")
        else:
            isolation = accepted[0].isolation
            if not isolation.source.unchanged:
                details.append("live candidate changed during isolated verification")
            if not isolation.source.remained_paused:
                details.append("live candidate was not kept paused during isolated verification")
            if not isolation.source.resumed:
                details.append("live candidate was not resumed after isolated verification")
            if not isolation.snapshot_image_disposed:
                details.append("completion snapshot image was not disposed")
            changed_checks = tuple(
                record.check_id
                for record in isolation.checks
                if record.delta.modified or record.delta.deleted
            )
            if changed_checks:
                details.append(
                    f"completion checks changed pre-existing candidate paths: {changed_checks}"
                )
        if details:
            assert attempt.terminal_line is not None
            violations.append(
                ControlInvariantViolation(
                    invariant=invariant,
                    terminal_line=attempt.terminal_line,
                    attempt_ordinal=attempt.ordinal,
                    related_lines=tuple(event.line for event in receipt_events),
                    details=tuple(details),
                )
            )
    return _result(invariant, violations)


def _audit_i6(
    trace: OfflineTrace,
    contract: CompletionContract,
    terminal_line: int,
) -> ControlInvariantResult:
    invariant = ControlInvariantId.I6
    events = _events(trace, "work_batch_admission")
    starts = _events(trace, "work_batch_started")
    details: list[str] = []
    accepted: list[tuple[RecordedEvent, _WorkBatchAdmission]] = []
    for event in events:
        admission = parse_event(event, _WorkBatchAdmission)
        expected_remaining = contract.b_req.max_environment_calls - admission.environment_calls_used
        expected_available = expected_remaining - contract.b_req.verification_environment_reserve
        admissible = (
            admission.requested_environment_calls <= expected_available
            and admission.turns_remaining >= 1
        )
        if admission.environment_calls_remaining != expected_remaining:
            details.append(f"line {event.line} records an incorrect environment-call remainder")
        if admission.verification_environment_reserve != (
            contract.b_req.verification_environment_reserve
        ):
            details.append(f"line {event.line} uses a verification reserve outside B_req")
        if admission.available_for_work != expected_available:
            details.append(f"line {event.line} records an incorrect work allowance")
        expected_turns = contract.b_req.max_turns - admission.turns_used
        if admission.turns_remaining != expected_turns:
            details.append(f"line {event.line} records an incorrect turn remainder")
        if admission.accepted != admissible:
            details.append(f"line {event.line} work admission contradicts the reserved budget")
        if admission.accepted:
            accepted.append((event, admission))

    if len(starts) != len(accepted):
        details.append("accepted work admissions and started work batches differ")
    for (admission_event, admission), start_event in zip(
        accepted,
        starts,
        strict=False,
    ):
        start = parse_event(start_event, _WorkBatchStarted)
        if admission.command_ids != start.command_ids:
            details.append(
                f"work admission on line {admission_event.line} does not bind "
                f"work batch on line {start_event.line}"
            )
        if admission_event.line >= start_event.line:
            details.append("work batch started before its budget admission")

    return _global_result(invariant, details, terminal_line, events + starts)


def _audit_i7(
    trace: OfflineTrace,
    terminal_line: int,
) -> ControlInvariantResult:
    invariant = ControlInvariantId.I7
    events = _events(trace, "work_batch_finished")
    details: list[str] = []
    for event in events:
        batch = parse_event(event, _WorkBatchFinished)
        progress_ids = set(batch.successful_change_ids) | set(batch.novel_observation_ids)
        completed_ids = set(batch.completed_command_ids)
        expected_progress = bool(progress_ids)
        if batch.progressed != expected_progress:
            details.append(f"line {event.line} progress flag does not match successful evidence")
        failed_progress = set(batch.failed_command_ids) & progress_ids
        if failed_progress:
            details.append(
                f"line {event.line} counts failed commands as progress: {sorted(failed_progress)}"
            )
        unknown_results = (set(batch.failed_command_ids) | progress_ids) - completed_ids
        if unknown_results:
            details.append(
                f"line {event.line} classifies commands that did not complete: "
                f"{sorted(unknown_results)}"
            )
        expected_stagnation = 0 if expected_progress else batch.stagnant_batches_before + 1
        if batch.stagnant_batches_after != expected_stagnation:
            details.append(f"line {event.line} records an incorrect stagnation transition")
    return _global_result(invariant, details, terminal_line, events)


def _audit_i8(
    trace: OfflineTrace,
    contract: CompletionContract,
    terminal_line: int,
) -> ControlInvariantResult:
    invariant = ControlInvariantId.I8
    events = _events(trace, "repair_admission")
    details: list[str] = []
    for event in events:
        admission = parse_event(event, _RepairAdmission)
        review_available = (
            admission.completion_reviews_used < admission.max_completion_reviews
            or not trace.completion_review_enabled
        )
        admissible = review_available and admission.repair_count_before < contract.b_req.max_repairs
        expected_after = (
            admission.repair_count_before + 1
            if admission.accepted
            else admission.repair_count_before
        )
        if admission.max_repairs != contract.b_req.max_repairs:
            details.append(f"line {event.line} uses a repair limit outside B_req")
        if admission.accepted != admissible:
            details.append(f"line {event.line} repair admission contradicts the repair budget")
        if admission.repair_count_after != expected_after:
            details.append(f"line {event.line} records an incorrect repair count")

    finished = _events(trace, "run_finished")
    if finished:
        run = parse_event(finished[-1], _RunFinished)
        admitted_repairs = sum(parse_event(event, _RepairAdmission).accepted for event in events)
        if run.repairs_used != admitted_repairs:
            details.append(
                f"run reports {run.repairs_used} repairs but {admitted_repairs} were admitted"
            )
        if run.repairs_used > contract.b_req.max_repairs:
            details.append(
                f"run reports {run.repairs_used} repairs above "
                f"B_req limit {contract.b_req.max_repairs}"
            )
    return _global_result(invariant, details, terminal_line, events + finished[-1:])


def _control_contract(trace: OfflineTrace) -> CompletionContract | None:
    started = trace.events[0]
    version = started.payload.get("control_audit_version")
    if version is None:
        return None
    if version != 1:
        raise TraceStructureError(f"unsupported control audit version: {version!r}")
    payload = started.payload.get("completion_contract")
    if not isinstance(payload, dict):
        raise TraceStructureError("control-audit journal has no completion contract")
    return CompletionContract.model_validate(payload)


def _events(trace: OfflineTrace, event_type: str) -> tuple[RecordedEvent, ...]:
    return tuple(event for event in trace.events if event.event_type == event_type)


def _global_result(
    invariant: ControlInvariantId,
    details: list[str],
    terminal_line: int,
    related: tuple[RecordedEvent, ...],
) -> ControlInvariantResult:
    violations = (
        (
            ControlInvariantViolation(
                invariant=invariant,
                terminal_line=terminal_line,
                attempt_ordinal=1,
                related_lines=tuple(event.line for event in related),
                details=tuple(dict.fromkeys(details)),
            ),
        )
        if details
        else ()
    )
    return _result(invariant, list(violations))


def _result(
    invariant: ControlInvariantId,
    violations: list[ControlInvariantViolation],
) -> ControlInvariantResult:
    return ControlInvariantResult(
        invariant=invariant,
        status=(ControlInvariantStatus.FAIL if violations else ControlInvariantStatus.PASS),
        violations=tuple(violations),
    )


def _unsupported(invariant: ControlInvariantId) -> ControlInvariantResult:
    return ControlInvariantResult(
        invariant=invariant,
        status=ControlInvariantStatus.UNSUPPORTED,
    )


def _not_applicable(invariant: ControlInvariantId) -> ControlInvariantResult:
    return ControlInvariantResult(
        invariant=invariant,
        status=ControlInvariantStatus.NOT_APPLICABLE,
    )
