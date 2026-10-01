from __future__ import annotations

import json
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, ValidationError, model_validator

from evidence_harness_mutation.attempts import project_completion_attempts
from evidence_harness_mutation.invariants import audit_completion_trace
from evidence_harness_mutation.model import (
    AuditReport,
    FrozenModel,
    InvariantId,
    InvariantStatus,
    InvariantViolation,
    JournalBinding,
    OfflineTrace,
    StatePrefix,
    TraceStructureError,
)
from evidence_harness_mutation.operators import (
    AppliedMutation,
    AttemptAnchor,
    MutationId,
    MutationNotApplicable,
    MutationRequest,
    MutationTarget,
    apply_mutation,
)
from evidence_harness_mutation.reducer import (
    ReducedCounterexample,
    ReductionError,
    reduce_counterexample,
)

_AUDIT_REPETITIONS = 3


class OfflineCampaignOutcome(StrEnum):
    MUTATION_NOT_APPLICABLE = "mutation_not_applicable"
    OFFLINE_INVALID = "offline_invalid"
    ORACLE_EQUIVALENT = "oracle_equivalent"
    OFFLINE_VIOLATION = "offline_violation"
    OTHER_ORACLE_CHANGE = "other_oracle_change"


class OfflineInvalidStage(StrEnum):
    BASELINE_AUDIT = "baseline_audit"
    MUTATION = "mutation"
    MUTATED_AUDIT = "mutated_audit"
    REDUCTION = "reduction"


class OfflineCampaignCaseBase(FrozenModel):
    ordinal: int = Field(ge=1)
    request: MutationRequest


class MutationNotApplicableCase(OfflineCampaignCaseBase):
    outcome: Literal[OfflineCampaignOutcome.MUTATION_NOT_APPLICABLE]
    reason: str = Field(min_length=1)


class OfflineInvalidCase(OfflineCampaignCaseBase):
    outcome: Literal[OfflineCampaignOutcome.OFFLINE_INVALID]
    stage: OfflineInvalidStage
    error: str = Field(min_length=1)


class ApplicableCampaignCase(OfflineCampaignCaseBase):
    expected_invariant: InvariantId
    attempt: AttemptAnchor
    target: MutationTarget
    audit: AuditReport


class OracleEquivalentCase(ApplicableCampaignCase):
    outcome: Literal[OfflineCampaignOutcome.ORACLE_EQUIVALENT]


class OfflineViolationCase(ApplicableCampaignCase):
    outcome: Literal[OfflineCampaignOutcome.OFFLINE_VIOLATION]
    violation: InvariantViolation
    counterexample: ReducedCounterexample

    @model_validator(mode="after")
    def validate_violation(self) -> Self:
        terminal = self.attempt.terminal
        if terminal is None or self.violation.terminal_line != terminal.line:
            raise ValueError("offline violation does not match its attempt terminal")
        if self.violation.invariant is not self.expected_invariant:
            raise ValueError("offline violation does not match its expected invariant")
        if self.violation not in self.audit.result(self.expected_invariant).violations:
            raise ValueError("offline violation is not present in its audit")
        if self.counterexample.provenance.request != self.request:
            raise ValueError("counterexample request does not match its campaign case")
        return self


class OtherOracleChangeCase(ApplicableCampaignCase):
    outcome: Literal[OfflineCampaignOutcome.OTHER_ORACLE_CHANGE]


OfflineCampaignCase = Annotated[
    MutationNotApplicableCase
    | OfflineInvalidCase
    | OracleEquivalentCase
    | OfflineViolationCase
    | OtherOracleChangeCase,
    Field(discriminator="outcome"),
]


class OfflineCampaignSummary(FrozenModel):
    scheduled: int = Field(ge=0)
    mutation_not_applicable: int = Field(ge=0)
    offline_invalid: int = Field(ge=0)
    oracle_equivalent: int = Field(ge=0)
    offline_violation: int = Field(ge=0)
    other_oracle_change: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_total(self) -> Self:
        classified = (
            self.mutation_not_applicable
            + self.offline_invalid
            + self.oracle_equivalent
            + self.offline_violation
            + self.other_oracle_change
        )
        if classified != self.scheduled:
            raise ValueError("offline campaign outcome counts must equal scheduled cases")
        return self

    @classmethod
    def from_cases(cls, cases: tuple[OfflineCampaignCase, ...]) -> Self:
        counts = {
            outcome: sum(case.outcome is outcome for case in cases)
            for outcome in OfflineCampaignOutcome
        }
        return cls(
            scheduled=len(cases),
            mutation_not_applicable=counts[OfflineCampaignOutcome.MUTATION_NOT_APPLICABLE],
            offline_invalid=counts[OfflineCampaignOutcome.OFFLINE_INVALID],
            oracle_equivalent=counts[OfflineCampaignOutcome.ORACLE_EQUIVALENT],
            offline_violation=counts[OfflineCampaignOutcome.OFFLINE_VIOLATION],
            other_oracle_change=counts[OfflineCampaignOutcome.OTHER_ORACLE_CHANGE],
        )


class OfflineCampaignReport(FrozenModel):
    schema_version: Literal[1] = 1
    source: JournalBinding
    through_line: int = Field(ge=1)
    baseline: AuditReport | None
    baseline_error: str | None
    cases: tuple[OfflineCampaignCase, ...]
    summary: OfflineCampaignSummary

    @model_validator(mode="after")
    def validate_report(self) -> Self:
        if (self.baseline is None) == (self.baseline_error is None):
            raise ValueError("campaign requires exactly one of baseline or baseline_error")
        if self.baseline is None and any(
            not isinstance(case, OfflineInvalidCase)
            or case.stage is not OfflineInvalidStage.BASELINE_AUDIT
            for case in self.cases
        ):
            raise ValueError("failed baseline requires baseline-audit invalid cases")
        if self.baseline is not None and any(
            isinstance(case, OfflineInvalidCase)
            and case.stage is OfflineInvalidStage.BASELINE_AUDIT
            for case in self.cases
        ):
            raise ValueError("successful baseline cannot have baseline-audit invalid cases")
        if self.baseline is not None and (
            self.baseline.source != self.source or self.baseline.through_line != self.through_line
        ):
            raise ValueError("campaign baseline does not match its source")
        if tuple(case.ordinal for case in self.cases) != tuple(range(1, len(self.cases) + 1)):
            raise ValueError("campaign case ordinals must be contiguous")
        if self.summary != OfflineCampaignSummary.from_cases(self.cases):
            raise ValueError("campaign summary does not match its cases")
        for case in self.cases:
            if isinstance(case, ApplicableCampaignCase) and (
                case.audit.source != self.source or case.audit.through_line != self.through_line
            ):
                raise ValueError("campaign case audit does not match its source")
        return self

    def canonical_bytes(self) -> bytes:
        return (
            json.dumps(
                self.model_dump(mode="json"),
                ensure_ascii=True,
                indent=2,
                sort_keys=True,
            )
            + "\n"
        ).encode()


class _NondeterministicAuditError(ValueError):
    pass


def run_offline_campaign(
    prefix: StatePrefix,
    *,
    requests: tuple[MutationRequest, ...] | None = None,
) -> OfflineCampaignReport:
    schedule = _schedule(prefix, requests)
    try:
        baseline = _stable_audit(prefix)
    except (TraceStructureError, ValidationError, _NondeterministicAuditError) as exc:
        invalid_cases: tuple[OfflineCampaignCase, ...] = tuple(
            OfflineInvalidCase(
                ordinal=ordinal,
                request=request,
                outcome=OfflineCampaignOutcome.OFFLINE_INVALID,
                stage=OfflineInvalidStage.BASELINE_AUDIT,
                error=_stable_error(exc),
            )
            for ordinal, request in enumerate(schedule, start=1)
        )
        return _report(
            prefix,
            cases=invalid_cases,
            baseline_error=_stable_error(exc),
        )

    cases: tuple[OfflineCampaignCase, ...] = tuple(
        _run_case(prefix, baseline, ordinal, request)
        for ordinal, request in enumerate(schedule, start=1)
    )
    return _report(prefix, cases=cases, baseline=baseline)


def _schedule(
    prefix: StatePrefix,
    requests: tuple[MutationRequest, ...] | None,
) -> tuple[MutationRequest, ...]:
    operator_order = {operator: index for index, operator in enumerate(MutationId)}
    if requests is not None:
        return tuple(
            sorted(
                requests,
                key=lambda request: (
                    request.attempt_ordinal,
                    operator_order[request.operator],
                ),
            )
        )
    try:
        attempt_ordinals = tuple(
            attempt.ordinal for attempt in project_completion_attempts(prefix.events)
        ) or (1,)
    except TraceStructureError:
        attempt_ordinals = (1,)
    return tuple(
        MutationRequest(operator=operator, attempt_ordinal=attempt_ordinal)
        for attempt_ordinal in attempt_ordinals
        for operator in MutationId
    )


def _run_case(
    prefix: StatePrefix,
    baseline: AuditReport,
    ordinal: int,
    request: MutationRequest,
) -> OfflineCampaignCase:
    try:
        mutation = apply_mutation(prefix, request)
    except MutationNotApplicable as exc:
        return MutationNotApplicableCase(
            ordinal=ordinal,
            request=request,
            outcome=OfflineCampaignOutcome.MUTATION_NOT_APPLICABLE,
            reason=str(exc),
        )
    except (TraceStructureError, ValidationError) as exc:
        return _invalid_case(
            ordinal,
            request,
            stage=OfflineInvalidStage.MUTATION,
            error=exc,
        )

    try:
        mutated_audit = _stable_audit(mutation.trace)
    except (TraceStructureError, ValidationError, _NondeterministicAuditError) as exc:
        return _invalid_case(
            ordinal,
            request,
            stage=OfflineInvalidStage.MUTATED_AUDIT,
            error=exc,
        )

    if mutated_audit == baseline:
        return OracleEquivalentCase(
            ordinal=ordinal,
            request=request,
            outcome=OfflineCampaignOutcome.ORACLE_EQUIVALENT,
            expected_invariant=mutation.expected_invariant,
            attempt=mutation.attempt,
            target=mutation.target,
            audit=mutated_audit,
        )

    try:
        violation = _new_expected_violation(baseline, mutated_audit, mutation)
    except _NondeterministicAuditError as exc:
        return _invalid_case(
            ordinal,
            request,
            stage=OfflineInvalidStage.MUTATED_AUDIT,
            error=exc,
        )
    if violation is None:
        return OtherOracleChangeCase(
            ordinal=ordinal,
            request=request,
            outcome=OfflineCampaignOutcome.OTHER_ORACLE_CHANGE,
            expected_invariant=mutation.expected_invariant,
            attempt=mutation.attempt,
            target=mutation.target,
            audit=mutated_audit,
        )

    try:
        counterexample = reduce_counterexample(mutation)
    except (ReductionError, TraceStructureError, ValidationError) as exc:
        return _invalid_case(
            ordinal,
            request,
            stage=OfflineInvalidStage.REDUCTION,
            error=exc,
        )
    return OfflineViolationCase(
        ordinal=ordinal,
        request=request,
        outcome=OfflineCampaignOutcome.OFFLINE_VIOLATION,
        expected_invariant=mutation.expected_invariant,
        attempt=mutation.attempt,
        target=mutation.target,
        audit=mutated_audit,
        violation=violation,
        counterexample=counterexample,
    )


def _new_expected_violation(
    baseline: AuditReport,
    mutated: AuditReport,
    mutation: AppliedMutation,
) -> InvariantViolation | None:
    terminal = mutation.attempt.terminal
    if terminal is None:
        return None
    introduced = _violation_at(
        mutated,
        invariant=mutation.expected_invariant,
        terminal_line=terminal.line,
    )
    if introduced is None:
        return None
    preexisting = _violation_at(
        baseline,
        invariant=mutation.expected_invariant,
        terminal_line=terminal.line,
    )
    return introduced if preexisting is None else None


def _violation_at(
    report: AuditReport,
    *,
    invariant: InvariantId,
    terminal_line: int,
) -> InvariantViolation | None:
    result = report.result(invariant)
    if result.status is not InvariantStatus.FAIL:
        return None
    matches = tuple(
        violation for violation in result.violations if violation.terminal_line == terminal_line
    )
    if len(matches) > 1:
        raise _NondeterministicAuditError(
            "offline audit returned duplicate violations for one terminal"
        )
    return matches[0] if matches else None


def _stable_audit(trace: OfflineTrace) -> AuditReport:
    reports = tuple(audit_completion_trace(trace) for _ in range(_AUDIT_REPETITIONS))
    if any(report != reports[0] for report in reports[1:]):
        raise _NondeterministicAuditError("offline audit changed across three consecutive runs")
    return reports[0]


def _invalid_case(
    ordinal: int,
    request: MutationRequest,
    *,
    stage: OfflineInvalidStage,
    error: Exception,
) -> OfflineInvalidCase:
    return OfflineInvalidCase(
        ordinal=ordinal,
        request=request,
        outcome=OfflineCampaignOutcome.OFFLINE_INVALID,
        stage=stage,
        error=_stable_error(error),
    )


def _report(
    prefix: StatePrefix,
    *,
    cases: tuple[OfflineCampaignCase, ...],
    baseline: AuditReport | None = None,
    baseline_error: str | None = None,
) -> OfflineCampaignReport:
    return OfflineCampaignReport(
        source=prefix.source,
        through_line=prefix.through_line,
        baseline=baseline,
        baseline_error=baseline_error,
        cases=cases,
        summary=OfflineCampaignSummary.from_cases(cases),
    )


def _stable_error(error: Exception) -> str:
    compact = " ".join(str(error).split())
    return f"{type(error).__name__}: {compact}"[:1_000]
