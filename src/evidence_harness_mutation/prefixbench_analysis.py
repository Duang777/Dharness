from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, ValidationError, model_validator

from evidence_harness_mutation.campaign import (
    ApplicableCampaignCase,
    OfflineCampaignCase,
    OfflineCampaignOutcome,
    OfflineCampaignSummary,
    OfflineViolationCase,
)
from evidence_harness_mutation.model import FrozenModel, InvariantId
from evidence_harness_mutation.operators import MutationId
from evidence_harness_mutation.prefixbench import PrefixBenchFileBinding
from evidence_harness_mutation.prefixbench_campaign import (
    PrefixBenchDevelopmentCampaignReport,
)
from evidence_harness_mutation.reducer import ReductionRejections

DEVELOPMENT_CAMPAIGN = Path("evaluation/prefixbench-v1-development-offline-campaign.json")
DEVELOPMENT_ANALYSIS = Path("evaluation/prefixbench-v1-development-offline-analysis.json")
DEVELOPMENT_CAMPAIGN_SHA256 = "395c10022f9682ff31246701e058a94aa983b1f236c9f3a6b164e8dc62c6aa83"

_OPERATOR_INVARIANTS = (
    (MutationId.STALE_EVIDENCE_EPOCH, InvariantId.I1),
    (MutationId.REORDER_CHECK_RECEIPTS, InvariantId.I2),
    (MutationId.REVIEW_TIMEOUT_FALLBACK, InvariantId.I3),
    (MutationId.CROSS_CANDIDATE_EVIDENCE, InvariantId.I4),
)


class ExactFraction(FrozenModel):
    numerator: int = Field(ge=0)
    denominator: int = Field(ge=1)

    @classmethod
    def from_counts(cls, count: int, population: int) -> Self:
        if count < 0:
            raise ValueError("fraction count must be nonnegative")
        if population < 1:
            raise ValueError("fraction population must be positive")
        if count > population:
            raise ValueError("fraction count cannot exceed its population")
        divisor = math.gcd(count, population)
        return cls(
            numerator=count // divisor,
            denominator=population // divisor,
        )

    @model_validator(mode="after")
    def validate_reduced(self) -> Self:
        if self.numerator > self.denominator:
            raise ValueError("fraction cannot exceed one")
        if math.gcd(self.numerator, self.denominator) != 1:
            raise ValueError("fraction must be reduced")
        return self


class ExactRate(FrozenModel):
    count: int = Field(ge=0)
    population: int = Field(ge=1)
    fraction: ExactFraction

    @classmethod
    def from_counts(cls, count: int, population: int) -> Self:
        return cls(
            count=count,
            population=population,
            fraction=ExactFraction.from_counts(count, population),
        )

    @model_validator(mode="after")
    def validate_fraction(self) -> Self:
        if self.count > self.population:
            raise ValueError("rate count cannot exceed its population")
        if self.fraction != ExactFraction.from_counts(self.count, self.population):
            raise ValueError("rate fraction does not match its counts")
        return self


class PrefixBenchAnalysisProtocol(FrozenModel):
    protocol_id: Literal["prefixbench-v1-development-offline-analysis-v1"] = (
        "prefixbench-v1-development-offline-analysis-v1"
    )
    source_policy: Literal["fixed-canonical-development-campaign-sha256"] = (
        "fixed-canonical-development-campaign-sha256"
    )
    case_partition: Literal["scheduled=mutation_not_applicable+offline_invalid+applicable"] = (
        "scheduled=mutation_not_applicable+offline_invalid+applicable"
    )
    applicable_definition: Literal["typed-applicable-campaign-case"] = (
        "typed-applicable-campaign-case"
    )
    applicable_outcome_partition: Literal[
        "applicable=oracle_equivalent+offline_violation+other_oracle_change"
    ] = "applicable=oracle_equivalent+offline_violation+other_oracle_change"
    rate_encoding: Literal["count-population-and-reduced-fraction"] = (
        "count-population-and-reduced-fraction"
    )
    operator_aggregation: Literal["operator-and-expected-invariant-in-protocol-order"] = (
        "operator-and-expected-invariant-in-protocol-order"
    )
    reduction_aggregation: Literal["pooled-sums-over-offline-violation-counterexamples"] = (
        "pooled-sums-over-offline-violation-counterexamples"
    )
    statistics: Literal["descriptive-only-no-inferential-statistics"] = (
        "descriptive-only-no-inferential-statistics"
    )


class PrefixBenchCaseObservations(FrozenModel):
    scheduled_cases: int = Field(ge=1)
    applicable_cases: ExactRate
    mutation_not_applicable_cases: ExactRate
    offline_invalid_cases: ExactRate
    oracle_equivalent_cases: ExactRate
    offline_violations: ExactRate
    other_oracle_changes: ExactRate

    @model_validator(mode="after")
    def validate_partitions(self) -> Self:
        scheduled_rates = (
            self.applicable_cases,
            self.mutation_not_applicable_cases,
            self.offline_invalid_cases,
        )
        if any(rate.population != self.scheduled_cases for rate in scheduled_rates):
            raise ValueError("top-level case rates must use scheduled cases as their population")
        if sum(rate.count for rate in scheduled_rates) != self.scheduled_cases:
            raise ValueError("top-level case outcomes do not partition scheduled cases")

        applicable_rates = (
            self.oracle_equivalent_cases,
            self.offline_violations,
            self.other_oracle_changes,
        )
        if any(rate.population != self.applicable_cases.count for rate in applicable_rates):
            raise ValueError(
                "applicable outcome rates must use applicable cases as their population"
            )
        if sum(rate.count for rate in applicable_rates) != self.applicable_cases.count:
            raise ValueError("applicable outcomes do not partition applicable cases")
        return self


class PrefixBenchTaskObservations(FrozenModel):
    tasks: Literal[28] = 28
    projected_attempts: int = Field(ge=1)
    tasks_with_projected_attempts: ExactRate
    tasks_with_verified_attempts: ExactRate
    verified_attempts_of_projected: ExactRate
    tasks_with_applicable_case: ExactRate
    tasks_with_offline_violation: ExactRate

    @model_validator(mode="after")
    def validate_populations(self) -> Self:
        task_rates = (
            self.tasks_with_projected_attempts,
            self.tasks_with_verified_attempts,
            self.tasks_with_applicable_case,
            self.tasks_with_offline_violation,
        )
        if any(rate.population != self.tasks for rate in task_rates):
            raise ValueError("task rates must use the cohort task count as their population")
        if self.verified_attempts_of_projected.population != self.projected_attempts:
            raise ValueError("verified attempt rate must use projected attempts as its population")
        if self.tasks_with_verified_attempts.count > self.tasks_with_projected_attempts.count:
            raise ValueError("verified-attempt tasks must be a subset of projected-attempt tasks")
        if self.tasks_with_offline_violation.count > self.tasks_with_applicable_case.count:
            raise ValueError("violation tasks must be a subset of applicable-case tasks")
        return self


class PrefixBenchOperatorObservation(FrozenModel):
    operator: MutationId
    expected_invariant: InvariantId
    outcomes: OfflineCampaignSummary
    applicable_cases_of_scheduled: ExactRate
    offline_violations_of_applicable: ExactRate

    @model_validator(mode="after")
    def validate_rates(self) -> Self:
        applicable = (
            self.outcomes.oracle_equivalent
            + self.outcomes.offline_violation
            + self.outcomes.other_oracle_change
        )
        if (
            self.outcomes.scheduled
            != self.outcomes.mutation_not_applicable + self.outcomes.offline_invalid + applicable
        ):
            raise ValueError("operator outcomes do not partition scheduled cases")
        if self.applicable_cases_of_scheduled != ExactRate.from_counts(
            applicable,
            self.outcomes.scheduled,
        ):
            raise ValueError("operator applicability rate does not match its outcomes")
        if self.offline_violations_of_applicable != ExactRate.from_counts(
            self.outcomes.offline_violation,
            applicable,
        ):
            raise ValueError("operator violation rate does not match its outcomes")
        return self


class PrefixBenchReductionDimension(FrozenModel):
    before: int = Field(ge=1)
    after: int = Field(ge=0)
    retained_fraction: ExactFraction

    @classmethod
    def from_counts(cls, *, before: int, after: int) -> Self:
        if after > before:
            raise ValueError("counterexample reduction cannot increase size")
        return cls(
            before=before,
            after=after,
            retained_fraction=ExactFraction.from_counts(after, before),
        )

    @model_validator(mode="after")
    def validate_fraction(self) -> Self:
        if self.after > self.before:
            raise ValueError("counterexample reduction cannot increase size")
        if self.retained_fraction != ExactFraction.from_counts(self.after, self.before):
            raise ValueError("retained fraction does not match reduction counts")
        return self


class PrefixBenchReductionObservations(FrozenModel):
    counterexamples: int = Field(ge=1)
    events: PrefixBenchReductionDimension
    recursive_payload_members: PrefixBenchReductionDimension
    compact_canonical_json_bytes: PrefixBenchReductionDimension
    candidates_evaluated: int = Field(ge=0)
    audits_executed: int = Field(ge=0)
    accepted_reductions: int = Field(ge=0)
    rejections: ReductionRejections

    @model_validator(mode="after")
    def validate_effort(self) -> Self:
        rejected = sum(
            getattr(self.rejections, field) for field in ReductionRejections.model_fields
        )
        if self.accepted_reductions + rejected != self.candidates_evaluated:
            raise ValueError("reducer candidate outcomes do not match candidates evaluated")
        return self


class PrefixBenchAnalysisClaims(FrozenModel):
    scope: Literal["committed-development-campaign-artifact-only"] = (
        "committed-development-campaign-artifact-only"
    )
    oracle_equivalent_definition: Literal[
        "identical-offline-audit-output-not-semantic-equivalence"
    ] = "identical-offline-audit-output-not-semantic-equivalence"
    offline_invalid_definition: Literal[
        "offline-pipeline-stage-failure-not-general-schema-validity"
    ] = "offline-pipeline-stage-failure-not-general-schema-validity"
    phase_witness_definition: Literal["readiness-metadata-not-campaign-cutoffs"] = (
        "readiness-metadata-not-campaign-cutoffs"
    )
    offline_violation_definition: Literal[
        "new-target-invariant-failure-not-production-mutation-score"
    ] = "new-target-invariant-failure-not-production-mutation-score"
    rq2_baseline_comparison: Literal["not_evaluated"] = "not_evaluated"
    phase_target_reachability: Literal["not_evaluated"] = "not_evaluated"
    rq3_production_mutation_score: Literal["not_evaluated"] = "not_evaluated"
    rq4_live_cost_savings: Literal["not_evaluated"] = "not_evaluated"
    model_call_telemetry: Literal["not_evaluated"] = "not_evaluated"
    container_call_telemetry: Literal["not_evaluated"] = "not_evaluated"
    inferential_statistics: Literal["not_evaluated"] = "not_evaluated"
    test_split_generalization: Literal["not_evaluated"] = "not_evaluated"


class PrefixBenchDevelopmentAnalysisReport(FrozenModel):
    schema_version: Literal[1] = 1
    benchmark: Literal["PrefixBench"] = "PrefixBench"
    dataset: Literal["terminal-bench@2.0"] = "terminal-bench@2.0"
    split: Literal["development"] = "development"
    source_campaign: PrefixBenchFileBinding
    protocol: PrefixBenchAnalysisProtocol
    tasks: PrefixBenchTaskObservations
    cases: PrefixBenchCaseObservations
    operators: tuple[PrefixBenchOperatorObservation, ...] = Field(
        min_length=4,
        max_length=4,
    )
    reduction: PrefixBenchReductionObservations
    claims: PrefixBenchAnalysisClaims

    @model_validator(mode="after")
    def validate_report(self) -> Self:
        if (
            self.source_campaign.path != DEVELOPMENT_CAMPAIGN.as_posix()
            or self.source_campaign.sha256 != DEVELOPMENT_CAMPAIGN_SHA256
        ):
            raise ValueError("analysis source is not the frozen development campaign")
        actual_pairs = tuple(
            (observation.operator, observation.expected_invariant) for observation in self.operators
        )
        if actual_pairs != _OPERATOR_INVARIANTS:
            raise ValueError("analysis operator order or invariant mapping has changed")

        case_summary = _case_outcome_summary(self.cases)
        for field in OfflineCampaignSummary.model_fields:
            operator_total = sum(
                getattr(observation.outcomes, field) for observation in self.operators
            )
            expected = getattr(case_summary, field)
            if operator_total != expected:
                raise ValueError(f"operator {field} total does not match case observations")
        if self.reduction.counterexamples != self.cases.offline_violations.count:
            raise ValueError("reduction count does not match offline violations")
        return self

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.model_dump(mode="json"))


def build_prefixbench_development_analysis(
    project_root: Path,
) -> PrefixBenchDevelopmentAnalysisReport:
    source, campaign = _load_bound_campaign(project_root)
    return _analyze_campaign(source, campaign)


def check_prefixbench_development_analysis(
    project_root: Path,
    *,
    report_path: Path | None = None,
) -> tuple[str, ...]:
    root = project_root.resolve()
    selected_report = root / DEVELOPMENT_ANALYSIS if report_path is None else report_path
    try:
        _require_project_path(selected_report.resolve(), root, label="analysis report")
        report_bytes = _read_regular_file(selected_report, label="analysis report")
        report = PrefixBenchDevelopmentAnalysisReport.model_validate_json(report_bytes)
    except (OSError, ValueError) as exc:
        return (f"invalid PrefixBench analysis artifact: {exc}",)

    errors: list[str] = []
    if report_bytes != report.canonical_bytes():
        errors.append("PrefixBench analysis artifact is not canonical JSON")
    try:
        rebuilt = build_prefixbench_development_analysis(root)
    except (OSError, ValueError) as exc:
        errors.append(f"cannot rebuild PrefixBench analysis artifact: {exc}")
        return tuple(errors)
    if rebuilt.canonical_bytes() != report_bytes:
        errors.append("PrefixBench analysis artifact is stale")
    return tuple(errors)


def _load_bound_campaign(
    project_root: Path,
) -> tuple[PrefixBenchFileBinding, PrefixBenchDevelopmentCampaignReport]:
    root = project_root.resolve()
    if not root.is_dir():
        raise ValueError(f"project root is not a directory: {root}")
    path = root / DEVELOPMENT_CAMPAIGN
    data = _read_regular_file(path, label="development campaign")
    _require_project_path(path.resolve(), root, label="campaign")
    digest = hashlib.sha256(data).hexdigest()
    if digest != DEVELOPMENT_CAMPAIGN_SHA256:
        raise ValueError("development campaign does not match the frozen analysis input")
    try:
        campaign = PrefixBenchDevelopmentCampaignReport.model_validate_json(data)
    except ValidationError as exc:
        raise ValueError("invalid development campaign artifact") from exc
    if data != campaign.canonical_bytes():
        raise ValueError("development campaign artifact is not canonical JSON")
    return (
        PrefixBenchFileBinding(
            path=DEVELOPMENT_CAMPAIGN.as_posix(),
            bytes=len(data),
            sha256=digest,
        ),
        campaign,
    )


def _analyze_campaign(
    source: PrefixBenchFileBinding,
    campaign: PrefixBenchDevelopmentCampaignReport,
) -> PrefixBenchDevelopmentAnalysisReport:
    all_cases = tuple(case for task in campaign.tasks for case in task.campaign.cases)
    applicable_cases = tuple(case for case in all_cases if isinstance(case, ApplicableCampaignCase))
    violation_cases = tuple(
        case for case in applicable_cases if isinstance(case, OfflineViolationCase)
    )
    scheduled = len(all_cases)
    applicable = len(applicable_cases)

    cases = PrefixBenchCaseObservations(
        scheduled_cases=scheduled,
        applicable_cases=ExactRate.from_counts(applicable, scheduled),
        mutation_not_applicable_cases=ExactRate.from_counts(
            _outcome_count(all_cases, OfflineCampaignOutcome.MUTATION_NOT_APPLICABLE),
            scheduled,
        ),
        offline_invalid_cases=ExactRate.from_counts(
            _outcome_count(all_cases, OfflineCampaignOutcome.OFFLINE_INVALID),
            scheduled,
        ),
        oracle_equivalent_cases=ExactRate.from_counts(
            _outcome_count(applicable_cases, OfflineCampaignOutcome.ORACLE_EQUIVALENT),
            applicable,
        ),
        offline_violations=ExactRate.from_counts(len(violation_cases), applicable),
        other_oracle_changes=ExactRate.from_counts(
            _outcome_count(applicable_cases, OfflineCampaignOutcome.OTHER_ORACLE_CHANGE),
            applicable,
        ),
    )

    verified_attempts = tuple(
        task.campaign.baseline.verified_attempts if task.campaign.baseline is not None else 0
        for task in campaign.tasks
    )
    tasks = PrefixBenchTaskObservations(
        projected_attempts=sum(task.projected_attempts for task in campaign.tasks),
        tasks_with_projected_attempts=ExactRate.from_counts(
            sum(task.projected_attempts > 0 for task in campaign.tasks),
            len(campaign.tasks),
        ),
        tasks_with_verified_attempts=ExactRate.from_counts(
            sum(count > 0 for count in verified_attempts),
            len(campaign.tasks),
        ),
        verified_attempts_of_projected=ExactRate.from_counts(
            sum(verified_attempts),
            sum(task.projected_attempts for task in campaign.tasks),
        ),
        tasks_with_applicable_case=ExactRate.from_counts(
            sum(
                any(isinstance(case, ApplicableCampaignCase) for case in task.campaign.cases)
                for task in campaign.tasks
            ),
            len(campaign.tasks),
        ),
        tasks_with_offline_violation=ExactRate.from_counts(
            sum(
                any(isinstance(case, OfflineViolationCase) for case in task.campaign.cases)
                for task in campaign.tasks
            ),
            len(campaign.tasks),
        ),
    )

    operators = tuple(
        _operator_observation(all_cases, operator, invariant)
        for operator, invariant in _OPERATOR_INVARIANTS
    )
    reduction = _reduction_observations(violation_cases)
    return PrefixBenchDevelopmentAnalysisReport(
        source_campaign=source,
        protocol=PrefixBenchAnalysisProtocol(),
        tasks=tasks,
        cases=cases,
        operators=operators,
        reduction=reduction,
        claims=PrefixBenchAnalysisClaims(),
    )


def _operator_observation(
    cases: tuple[OfflineCampaignCase, ...],
    operator: MutationId,
    invariant: InvariantId,
) -> PrefixBenchOperatorObservation:
    selected = tuple(
        case
        for case in cases
        if getattr(getattr(case, "request", None), "operator", None) is operator
    )
    typed_cases = tuple(case for case in selected if isinstance(case, ApplicableCampaignCase))
    outcomes = OfflineCampaignSummary.from_cases(selected)
    return PrefixBenchOperatorObservation(
        operator=operator,
        expected_invariant=invariant,
        outcomes=outcomes,
        applicable_cases_of_scheduled=ExactRate.from_counts(
            len(typed_cases),
            len(selected),
        ),
        offline_violations_of_applicable=ExactRate.from_counts(
            sum(isinstance(case, OfflineViolationCase) for case in typed_cases),
            len(typed_cases),
        ),
    )


def _reduction_observations(
    cases: tuple[OfflineViolationCase, ...],
) -> PrefixBenchReductionObservations:
    metrics = tuple(case.counterexample.metrics for case in cases)
    return PrefixBenchReductionObservations(
        counterexamples=len(cases),
        events=PrefixBenchReductionDimension.from_counts(
            before=sum(metric.before.event_count for metric in metrics),
            after=sum(metric.after.event_count for metric in metrics),
        ),
        recursive_payload_members=PrefixBenchReductionDimension.from_counts(
            before=sum(metric.before.payload_member_count for metric in metrics),
            after=sum(metric.after.payload_member_count for metric in metrics),
        ),
        compact_canonical_json_bytes=PrefixBenchReductionDimension.from_counts(
            before=sum(metric.before.canonical_json_bytes for metric in metrics),
            after=sum(metric.after.canonical_json_bytes for metric in metrics),
        ),
        candidates_evaluated=sum(metric.candidates_evaluated for metric in metrics),
        audits_executed=sum(metric.audits_executed for metric in metrics),
        accepted_reductions=sum(metric.accepted_reductions for metric in metrics),
        rejections=ReductionRejections(
            **{
                field: sum(getattr(metric.rejections, field) for metric in metrics)
                for field in ReductionRejections.model_fields
            }
        ),
    )


def _outcome_count(
    cases: tuple[OfflineCampaignCase, ...],
    outcome: OfflineCampaignOutcome,
) -> int:
    return sum(case.outcome is outcome for case in cases)


def _case_outcome_summary(cases: PrefixBenchCaseObservations) -> OfflineCampaignSummary:
    return OfflineCampaignSummary(
        scheduled=cases.scheduled_cases,
        mutation_not_applicable=cases.mutation_not_applicable_cases.count,
        offline_invalid=cases.offline_invalid_cases.count,
        oracle_equivalent=cases.oracle_equivalent_cases.count,
        offline_violation=cases.offline_violations.count,
        other_oracle_change=cases.other_oracle_changes.count,
    )


def _read_regular_file(path: Path, *, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} must be a regular file: {path}")
    return path.read_bytes()


def _require_project_path(path: Path, project_root: Path, *, label: str) -> None:
    try:
        path.relative_to(project_root)
    except ValueError as exc:
        raise ValueError(f"{label} path is outside the project: {path}") from exc


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode()
