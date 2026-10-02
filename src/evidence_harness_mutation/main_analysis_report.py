from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Callable
from fractions import Fraction
from functools import partial
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, TypeAdapter, ValidationError, model_validator

from evidence_harness_mutation.campaign import OfflineCampaignCase
from evidence_harness_mutation.journal_loader import load_state_prefix
from evidence_harness_mutation.main_analysis_baselines import (
    RQ2SlotOutcome,
    RQ2TaskResult,
    build_rq2_task,
)
from evidence_harness_mutation.main_analysis_protocol import (
    MAIN_REPORT,
    MINISWE_COHORT,
    RQ2_REPORT,
    RQ3_REPORT,
    RQ4_REPORT,
    MainAnalysisMethod,
    MainAnalysisOutcome,
    MainAnalysisReducer,
    load_main_analysis_protocol,
)
from evidence_harness_mutation.main_analysis_reducers import (
    MainAnalysisReductionComparison,
    MainAnalysisReductionStatus,
    ReductionInput,
    compare_reducers,
)
from evidence_harness_mutation.main_analysis_statistics import (
    ExactInterval,
    ExactProbability,
    ExactValue,
    HolmInput,
    McNemarResult,
    PairedBinaryTask,
    RetainedBytesTask,
    WilcoxonResult,
    bootstrap_binary_risk_difference,
    bootstrap_clustered_retained_difference,
    exact_mcnemar,
    exact_wilcoxon,
    holm_adjust,
)
from evidence_harness_mutation.main_analysis_transfer import (
    TransferCaseResult,
    TransferFamily,
    adapt_miniswe_trajectory,
    evaluate_transfer_family,
)
from evidence_harness_mutation.model import FrozenModel, Sha256
from evidence_harness_mutation.operators import MutationRequest, apply_mutation
from evidence_harness_mutation.prefixbench import PrefixBenchFileBinding
from evidence_harness_mutation.prefixbench_test_campaign import (
    TEST_CAMPAIGN,
    PrefixBenchTestCampaignReport,
)

_RQ2_COMPARATORS = (
    MainAnalysisMethod.RANDOM_JSON,
    MainAnalysisMethod.SCHEMA_VALID_RANDOM,
    MainAnalysisMethod.AGENTCHAOS_STYLE,
    MainAnalysisMethod.STATELESS_SEMANTIC,
)
_RQ3_COMPARATORS = (
    MainAnalysisReducer.NO_REDUCTION,
    MainAnalysisReducer.FLAT_DDMIN,
    MainAnalysisReducer.HDD,
)
_TRANSFER_FAMILIES = tuple(TransferFamily)
_MAPPED_TRANSFER_FAMILIES = (
    TransferFamily.I1,
    TransferFamily.I2,
    TransferFamily.I4,
)
_ALPHA = Fraction(1, 20)


class AnalysisLineage(FrozenModel):
    protocol_sha256: Sha256
    protocol_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    executable_sha256: Sha256
    executable_commit: str = Field(pattern=r"^[0-9a-f]{40}$")


class RQ2OutcomeCounts(FrozenModel):
    input_rejected: int = Field(ge=0)
    target_not_reached: int = Field(ge=0)
    oracle_invalid: int = Field(ge=0)
    oracle_equivalent: int = Field(ge=0)
    target_violation: int = Field(ge=0)
    other_oracle_change: int = Field(ge=0)

    @property
    def scheduled(self) -> int:
        return sum(getattr(self, outcome.value) for outcome in MainAnalysisOutcome)


class RQ2MethodSummary(FrozenModel):
    method: MainAnalysisMethod
    tasks: int = Field(ge=1)
    scheduled_slots: int = Field(ge=1)
    outcomes: RQ2OutcomeCounts
    input_acceptance_rate: ExactProbability
    target_reach_rate: ExactProbability
    target_violation_rate: ExactProbability
    oracle_equivalent_rate: ExactProbability
    other_oracle_change_rate: ExactProbability
    distinct_target_invariants: int = Field(ge=0, le=4)
    target_violations_per_100_slots: ExactValue
    tasks_with_target_violation: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        if self.outcomes.scheduled != self.scheduled_slots:
            raise ValueError("RQ2 outcome counts do not match scheduled slots")
        if self.tasks_with_target_violation > self.tasks:
            raise ValueError("RQ2 detected task count exceeds task count")
        return self


class RQ2Contrast(FrozenModel):
    comparator: MainAnalysisMethod
    mcnemar: McNemarResult
    risk_difference_interval: ExactInterval
    holm_adjusted_p_value: ExactProbability
    disposition: Literal["confirmed", "not_confirmed"]

    @model_validator(mode="after")
    def validate_disposition(self) -> Self:
        confirmed = (
            self.mcnemar.risk_difference.as_fraction() > 0
            and self.holm_adjusted_p_value.as_fraction() < _ALPHA
        )
        if confirmed != (self.disposition == "confirmed"):
            raise ValueError("RQ2 contrast disposition does not match its statistics")
        return self


class RQ2Analysis(FrozenModel):
    methods: tuple[RQ2MethodSummary, ...] = Field(min_length=5, max_length=5)
    contrasts: tuple[RQ2Contrast, ...] = Field(min_length=4, max_length=4)
    disposition: Literal[
        "broad_superiority_confirmed",
        "nearest_baseline_advantage_confirmed",
        "broad_superiority_not_confirmed",
    ]

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        if tuple(item.method for item in self.methods) != tuple(MainAnalysisMethod):
            raise ValueError("RQ2 method summary order has changed")
        if tuple(item.comparator for item in self.contrasts) != _RQ2_COMPARATORS:
            raise ValueError("RQ2 contrast order has changed")
        confirmed = tuple(
            item.comparator for item in self.contrasts if item.disposition == "confirmed"
        )
        expected = _rq2_disposition(confirmed)
        if self.disposition != expected:
            raise ValueError("RQ2 disposition does not match its contrasts")
        return self


class RQ2Report(FrozenModel):
    schema_version: Literal[1] = 1
    report_id: Literal["prefixbench-v1-test-method-comparison"] = (
        "prefixbench-v1-test-method-comparison"
    )
    dataset: Literal["terminal-bench@2.0"] = "terminal-bench@2.0"
    split: Literal["test"] = "test"
    lineage: AnalysisLineage
    source_campaign: PrefixBenchFileBinding
    tasks: tuple[RQ2TaskResult, ...] = Field(min_length=61, max_length=61)
    analysis: RQ2Analysis

    @model_validator(mode="after")
    def validate_tasks(self) -> Self:
        if len({task.task_identity_sha256 for task in self.tasks}) != len(self.tasks):
            raise ValueError("RQ2 task identities must be unique")
        return self

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.model_dump(mode="json"))


class RQ3ReducerSummary(FrozenModel):
    reducer: MainAnalysisReducer
    cases: int = Field(ge=1)
    tasks: int = Field(ge=1)
    before_bytes: int = Field(ge=1)
    after_bytes: int = Field(ge=0)
    pooled_retained_fraction: ExactValue
    failed_retained: int = Field(ge=0)
    candidates_evaluated: int = Field(ge=0)
    audits_executed: int = Field(ge=0)
    accepted_reductions: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_fraction(self) -> Self:
        if self.after_bytes > self.before_bytes:
            raise ValueError("RQ3 retained bytes cannot exceed input bytes")
        if self.pooled_retained_fraction.as_fraction() != Fraction(
            self.after_bytes, self.before_bytes
        ):
            raise ValueError("RQ3 retained fraction does not match byte totals")
        if self.failed_retained > self.cases:
            raise ValueError("RQ3 failed reduction count exceeds case count")
        return self


class RQ3Contrast(FrozenModel):
    comparator: MainAnalysisReducer
    wilcoxon: WilcoxonResult
    retained_difference_interval: ExactInterval
    holm_adjusted_p_value: ExactProbability
    disposition: Literal["confirmed", "not_confirmed"]

    @model_validator(mode="after")
    def validate_disposition(self) -> Self:
        confirmed = (
            self.wilcoxon.rank_biserial.as_fraction() < 0
            and self.holm_adjusted_p_value.as_fraction() < _ALPHA
        )
        if confirmed != (self.disposition == "confirmed"):
            raise ValueError("RQ3 contrast disposition does not match its statistics")
        return self


class RQ3Analysis(FrozenModel):
    cases: int = Field(ge=0)
    tasks: int = Field(ge=0)
    reducers: tuple[RQ3ReducerSummary, ...]
    status: Literal["sufficient", "insufficient_cases"]
    contrasts: tuple[RQ3Contrast, ...]
    disposition: Literal["reduction_advantage_confirmed", "reduction_advantage_not_confirmed"]

    @model_validator(mode="after")
    def validate_analysis(self) -> Self:
        sufficient = self.cases >= 10 and self.tasks >= 5
        if sufficient != (self.status == "sufficient"):
            raise ValueError("RQ3 sample status does not match its denominators")
        if self.cases == 0 and self.reducers:
            raise ValueError("empty RQ3 analysis cannot contain reducer summaries")
        if self.cases > 0 and tuple(item.reducer for item in self.reducers) != tuple(
            MainAnalysisReducer
        ):
            raise ValueError("RQ3 reducer summary order has changed")
        if sufficient:
            if tuple(item.comparator for item in self.contrasts) != _RQ3_COMPARATORS:
                raise ValueError("RQ3 contrast order has changed")
        elif self.contrasts:
            raise ValueError("insufficient RQ3 analysis cannot contain p-values")
        confirmed = {item.comparator for item in self.contrasts if item.disposition == "confirmed"}
        expected = (
            "reduction_advantage_confirmed"
            if {
                MainAnalysisReducer.FLAT_DDMIN,
                MainAnalysisReducer.HDD,
            }
            <= confirmed
            else "reduction_advantage_not_confirmed"
        )
        if self.disposition != expected:
            raise ValueError("RQ3 disposition does not match its contrasts")
        return self


class RQ3Report(FrozenModel):
    schema_version: Literal[1] = 1
    report_id: Literal["prefixbench-v1-test-reducer-comparison"] = (
        "prefixbench-v1-test-reducer-comparison"
    )
    dataset: Literal["terminal-bench@2.0"] = "terminal-bench@2.0"
    split: Literal["test"] = "test"
    lineage: AnalysisLineage
    source_rq2: PrefixBenchFileBinding
    comparisons: tuple[MainAnalysisReductionComparison, ...]
    analysis: RQ3Analysis

    @model_validator(mode="after")
    def validate_cases(self) -> Self:
        keys = tuple((item.task_identity_sha256, item.case_ordinal) for item in self.comparisons)
        if len(keys) != len(set(keys)):
            raise ValueError("RQ3 comparison identities must be unique")
        return self

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.model_dump(mode="json"))


class RQ4TaskFamily(FrozenModel):
    family: TransferFamily
    status: Literal[
        "unsupported_by_design",
        "adapter_rejected",
        "preexisting_violation",
        "not_applicable",
        "target_violation",
        "other_oracle_change",
    ]
    deterministic_replay: bool = False

    @model_validator(mode="after")
    def validate_family(self) -> Self:
        if self.family is TransferFamily.I3:
            if self.status != "unsupported_by_design":
                raise ValueError("RQ4 I3 must remain unsupported by design")
        elif self.status == "unsupported_by_design":
            raise ValueError("only RQ4 I3 is unsupported by design")
        if self.status == "target_violation" and not self.deterministic_replay:
            raise ValueError("RQ4 target violation must replay deterministically")
        if self.status not in {"target_violation", "other_oracle_change"} and (
            self.deterministic_replay
        ):
            raise ValueError("unexecuted RQ4 mutation cannot have deterministic replay")
        return self


class RQ4TaskResult(FrozenModel):
    index: int = Field(ge=1, le=20)
    instance_id: str = Field(min_length=1)
    trajectory: PrefixBenchFileBinding | None
    adapter_error: str | None
    families: tuple[RQ4TaskFamily, ...] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def validate_task(self) -> Self:
        if tuple(item.family for item in self.families) != _TRANSFER_FAMILIES:
            raise ValueError("RQ4 task family order has changed")
        rejected = any(item.status == "adapter_rejected" for item in self.families)
        if rejected != (self.adapter_error is not None):
            raise ValueError("RQ4 adapter error does not match family outcomes")
        return self


class RQ4FamilySummary(FrozenModel):
    family: TransferFamily
    mapping: Literal["mapped", "unsupported_by_design"]
    scheduled: int = Field(ge=0)
    executable: int = Field(ge=0)
    adapter_rejected: int = Field(ge=0)
    preexisting_violation: int = Field(ge=0)
    not_applicable: int = Field(ge=0)
    applicable: int = Field(ge=0)
    target_violation: int = Field(ge=0)
    other_oracle_change: int = Field(ge=0)
    deterministic_replay: int = Field(ge=0)
    disposition: Literal[
        "transfer_supported",
        "transfer_not_confirmed",
        "unsupported_by_design",
    ]

    @model_validator(mode="after")
    def validate_partition(self) -> Self:
        if self.family is TransferFamily.I3:
            if (
                self.mapping,
                self.disposition,
                self.executable,
                self.adapter_rejected,
                self.preexisting_violation,
                self.not_applicable,
                self.applicable,
                self.target_violation,
                self.other_oracle_change,
                self.deterministic_replay,
            ) != (
                "unsupported_by_design",
                "unsupported_by_design",
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
            ):
                raise ValueError("RQ4 I3 summary must remain unsupported")
            return self
        if self.mapping != "mapped":
            raise ValueError("mapped RQ4 family has an invalid mapping")
        if self.executable + self.adapter_rejected != self.scheduled:
            raise ValueError("RQ4 executable and rejected counts do not partition tasks")
        if self.preexisting_violation + self.not_applicable + self.applicable != self.executable:
            raise ValueError("RQ4 source outcomes do not partition executable tasks")
        if self.target_violation + self.other_oracle_change != self.applicable:
            raise ValueError("RQ4 mutation outcomes do not partition applicable tasks")
        if self.deterministic_replay != self.target_violation:
            raise ValueError("RQ4 target violations must all replay deterministically")
        supported = self.applicable >= 5 and self.target_violation >= 2
        if supported != (self.disposition == "transfer_supported"):
            raise ValueError("RQ4 family disposition does not match its thresholds")
        return self


class RQ4Analysis(FrozenModel):
    collection_complete: bool
    families: tuple[RQ4FamilySummary, ...] = Field(min_length=4, max_length=4)
    disposition: Literal["transfer_supported", "transfer_not_confirmed", "incomplete"]

    @model_validator(mode="after")
    def validate_analysis(self) -> Self:
        if tuple(item.family for item in self.families) != _TRANSFER_FAMILIES:
            raise ValueError("RQ4 family summary order has changed")
        if not self.collection_complete:
            expected = "incomplete"
        elif all(
            item.disposition == "transfer_supported"
            for item in self.families
            if item.family in _MAPPED_TRANSFER_FAMILIES
        ):
            expected = "transfer_supported"
        else:
            expected = "transfer_not_confirmed"
        if self.disposition != expected:
            raise ValueError("RQ4 disposition does not match its family summaries")
        return self


class RQ4Report(FrozenModel):
    schema_version: Literal[1] = 1
    report_id: Literal["miniswe-agent-transfer-v1"] = "miniswe-agent-transfer-v1"
    benchmark: Literal["ProgramBench"] = "ProgramBench"
    trajectory_format: Literal["mini-swe-agent-1.1"] = "mini-swe-agent-1.1"
    lineage: AnalysisLineage
    cohort: PrefixBenchFileBinding
    tasks: tuple[RQ4TaskResult, ...] = Field(min_length=20, max_length=20)
    analysis: RQ4Analysis
    claim_boundary: Literal["trajectory-schema-and-control-semantics-transfer-only"] = (
        "trajectory-schema-and-control-semantics-transfer-only"
    )

    @model_validator(mode="after")
    def validate_tasks(self) -> Self:
        if tuple(task.index for task in self.tasks) != tuple(range(1, 21)):
            raise ValueError("RQ4 task indices must be contiguous")
        if len({task.instance_id for task in self.tasks}) != 20:
            raise ValueError("RQ4 task identities must be unique")
        return self

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.model_dump(mode="json"))


class MainAnalysisSourceBindings(FrozenModel):
    historical_7: PrefixBenchFileBinding
    development_analysis: PrefixBenchFileBinding
    rq2: PrefixBenchFileBinding
    rq3: PrefixBenchFileBinding
    rq4: PrefixBenchFileBinding


class MainAnalysisRQ1Result(FrozenModel):
    evidence_role: Literal["locked_retrospective"] = "locked_retrospective"
    case_pairs: Literal[7] = 7
    successful_pairs: int = Field(ge=0, le=7)
    disposition: Literal["threshold_met", "threshold_not_met"]
    inferential_test: Literal["none"] = "none"

    @model_validator(mode="after")
    def validate_disposition(self) -> Self:
        expected = "threshold_met" if self.successful_pairs >= 6 else "threshold_not_met"
        if self.disposition != expected:
            raise ValueError("RQ1 disposition does not match its frozen threshold")
        return self


class MainAnalysisDevelopmentContext(FrozenModel):
    status: Literal["descriptive_not_evaluated"] = "descriptive_not_evaluated"
    pooled_into_main_analysis: Literal[False] = False


class MainAnalysisOptionalResult(FrozenModel):
    terminal_bench_2_1: Literal["not_evaluated"] = "not_evaluated"
    production_mutation_score: Literal["not_evaluated"] = "not_evaluated"
    live_cost: Literal["not_evaluated"] = "not_evaluated"


class ThesisMainAnalysisReport(FrozenModel):
    schema_version: Literal[1] = 1
    report_id: Literal["thesis-main-analysis-v1"] = "thesis-main-analysis-v1"
    lineage: AnalysisLineage
    source_bindings: MainAnalysisSourceBindings
    development_context: MainAnalysisDevelopmentContext
    rq1: MainAnalysisRQ1Result
    rq2: RQ2Analysis
    rq3: RQ3Analysis
    rq4: RQ4Analysis
    sensitivity: MainAnalysisOptionalResult
    auxiliary: MainAnalysisOptionalResult
    deviations: tuple[str, ...] = ()

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.model_dump(mode="json"))


def build_rq2_report(project_root: Path) -> RQ2Report:
    root = _project_root(project_root)
    lineage = _analysis_lineage(root)
    campaign_path = root / TEST_CAMPAIGN
    campaign_data = _read_regular_file(campaign_path, "PrefixBench test campaign")
    campaign = _parse_campaign(campaign_data)
    tasks: list[RQ2TaskResult] = []
    for task in campaign.tasks:
        journal_data = _read_bound_file(root, task.journal)
        prefix = load_state_prefix(
            journal_data,
            source_commit=task.campaign.source.source_commit,
            expected_journal_sha256=task.journal.sha256,
        )
        tasks.append(
            build_rq2_task(
                task_name=task.name,
                task_identity_sha256=task.task_identity_sha256,
                prefix=prefix,
                load_state_aware_cases=partial(
                    _fixed_cases,
                    task.campaign.cases,
                ),
            )
        )
    task_tuple = tuple(tasks)
    return RQ2Report(
        lineage=lineage,
        source_campaign=file_binding(campaign_path, root),
        tasks=task_tuple,
        analysis=analyze_rq2(task_tuple),
    )


def build_rq3_report(project_root: Path) -> RQ3Report:
    root = _project_root(project_root)
    lineage = _analysis_lineage(root)
    rq2_path = root / RQ2_REPORT
    rq2_data = _read_regular_file(rq2_path, "RQ2 report")
    rq2 = _parse_rq2(rq2_data)
    campaign_data = _read_regular_file(root / TEST_CAMPAIGN, "PrefixBench test campaign")
    campaign = _parse_campaign(campaign_data)
    source_by_identity = {task.task_identity_sha256: task for task in campaign.tasks}
    comparisons: list[MainAnalysisReductionComparison] = []
    for rq2_task in rq2.tasks:
        source = source_by_identity.get(rq2_task.task_identity_sha256)
        if source is None:
            raise ValueError(f"RQ2 task is absent from source campaign: {rq2_task.task_name}")
        journal_data = _read_bound_file(root, source.journal)
        prefix = load_state_prefix(
            journal_data,
            source_commit=source.campaign.source.source_commit,
            expected_journal_sha256=source.journal.sha256,
        )
        state_aware = next(
            method for method in rq2_task.methods if method.method is MainAnalysisMethod.STATE_AWARE
        )
        case_ordinal = 0
        for outcome in state_aware.outcomes:
            if outcome.outcome is not MainAnalysisOutcome.TARGET_VIOLATION:
                continue
            case_ordinal += 1
            mutation = apply_mutation(
                prefix,
                MutationRequest(
                    operator=outcome.slot.operator,
                    attempt_ordinal=outcome.slot.attempt_ordinal,
                ),
            )
            comparisons.append(
                compare_reducers(
                    ReductionInput(
                        task_identity_sha256=rq2_task.task_identity_sha256,
                        case_ordinal=case_ordinal,
                        mutation=mutation,
                    )
                )
            )
    comparison_tuple = tuple(comparisons)
    return RQ3Report(
        lineage=lineage,
        source_rq2=file_binding(rq2_path, root),
        comparisons=comparison_tuple,
        analysis=analyze_rq3(comparison_tuple),
    )


def build_rq4_report(project_root: Path) -> RQ4Report:
    from evidence_harness_mutation.main_analysis_executable import (
        MiniSweCohort,
    )

    root = _project_root(project_root)
    lineage = _analysis_lineage(root)
    cohort_path = root / MINISWE_COHORT
    cohort_data = _read_regular_file(cohort_path, "mini-swe cohort")
    try:
        cohort = MiniSweCohort.model_validate_json(cohort_data)
    except ValueError as exc:
        raise ValueError("invalid mini-swe cohort") from exc
    if cohort_data != cohort.canonical_bytes():
        raise ValueError("mini-swe cohort is not canonical JSON")

    tasks: list[RQ4TaskResult] = []
    for cohort_task in cohort.tasks:
        trajectory_path = (
            root
            / "runs"
            / "programbench"
            / "miniswe-transfer-v1"
            / cohort_task.instance_id
            / f"{cohort_task.instance_id}.traj.json"
        )
        if not trajectory_path.exists():
            tasks.append(
                rq4_rejected_task(
                    index=cohort_task.index,
                    instance_id=cohort_task.instance_id,
                    trajectory=None,
                    error="missing trajectory",
                )
            )
            continue
        binding = file_binding(trajectory_path, root)
        data = _read_regular_file(trajectory_path, "mini-swe trajectory")
        try:
            trace = adapt_miniswe_trajectory(data)
            if trace.instance_id != cohort_task.instance_id:
                raise ValueError("trajectory instance identity does not match cohort")
            results = tuple(evaluate_transfer_family(trace, family) for family in TransferFamily)
            tasks.append(
                rq4_task_from_transfer(
                    index=cohort_task.index,
                    instance_id=cohort_task.instance_id,
                    trajectory=binding,
                    results=results,
                )
            )
        except ValueError as exc:
            tasks.append(
                rq4_rejected_task(
                    index=cohort_task.index,
                    instance_id=cohort_task.instance_id,
                    trajectory=binding,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
    task_tuple = tuple(tasks)
    return RQ4Report(
        lineage=lineage,
        cohort=file_binding(cohort_path, root),
        tasks=task_tuple,
        analysis=analyze_rq4(task_tuple),
    )


def build_main_analysis_report(project_root: Path) -> ThesisMainAnalysisReport:
    root = _project_root(project_root)
    lineage = _analysis_lineage(root)
    historical_path = root / "evaluation/historical-7.json"
    development_path = root / "evaluation/prefixbench-v1-development-offline-analysis.json"
    rq2_path = root / RQ2_REPORT
    rq3_path = root / RQ3_REPORT
    rq4_path = root / RQ4_REPORT
    historical = _json_object(
        _read_regular_file(historical_path, "Historical-7 report"),
        "Historical-7 report",
    )
    rq2 = _parse_rq2(_read_regular_file(rq2_path, "RQ2 report"))
    rq3 = _parse_rq3(_read_regular_file(rq3_path, "RQ3 report"))
    rq4 = _parse_rq4(_read_regular_file(rq4_path, "RQ4 report"))
    return ThesisMainAnalysisReport(
        lineage=lineage,
        source_bindings=MainAnalysisSourceBindings(
            historical_7=file_binding(historical_path, root),
            development_analysis=file_binding(development_path, root),
            rq2=file_binding(rq2_path, root),
            rq3=file_binding(rq3_path, root),
            rq4=file_binding(rq4_path, root),
        ),
        development_context=MainAnalysisDevelopmentContext(),
        rq1=_historical_result(historical),
        rq2=rq2.analysis,
        rq3=rq3.analysis,
        rq4=rq4.analysis,
        sensitivity=MainAnalysisOptionalResult(),
        auxiliary=MainAnalysisOptionalResult(),
    )


def check_rq2_report(project_root: Path) -> tuple[str, ...]:
    return _check_report(
        project_root,
        RQ2_REPORT,
        RQ2Report,
        build_rq2_report,
        validate=lambda report: report.analysis == analyze_rq2(report.tasks),
        raw_anchor=TEST_CAMPAIGN,
    )


def check_rq3_report(project_root: Path) -> tuple[str, ...]:
    return _check_report(
        project_root,
        RQ3_REPORT,
        RQ3Report,
        build_rq3_report,
        validate=lambda report: report.analysis == analyze_rq3(report.comparisons),
        raw_anchor=RQ2_REPORT,
    )


def check_rq4_report(project_root: Path) -> tuple[str, ...]:
    return _check_report(
        project_root,
        RQ4_REPORT,
        RQ4Report,
        build_rq4_report,
        validate=lambda report: report.analysis == analyze_rq4(report.tasks),
        raw_anchor=Path("runs/programbench/miniswe-transfer-v1"),
    )


def check_main_analysis_report(project_root: Path) -> tuple[str, ...]:
    return _check_report(
        project_root,
        MAIN_REPORT,
        ThesisMainAnalysisReport,
        build_main_analysis_report,
        validate=lambda _report: True,
        raw_anchor=RQ2_REPORT,
    )


def analyze_rq2(tasks: tuple[RQ2TaskResult, ...]) -> RQ2Analysis:
    if not tasks:
        raise ValueError("RQ2 analysis requires at least one task")
    identities = tuple(task.task_identity_sha256 for task in tasks)
    if len(identities) != len(set(identities)):
        raise ValueError("RQ2 analysis requires unique task identities")

    summaries = tuple(_rq2_method_summary(tasks, method) for method in MainAnalysisMethod)
    raw: list[tuple[MainAnalysisMethod, McNemarResult, ExactInterval]] = []
    for comparator in _RQ2_COMPARATORS:
        rows = tuple(
            PairedBinaryTask(
                task_identity=task.task_identity_sha256,
                treatment_detected=_task_detected(task, MainAnalysisMethod.STATE_AWARE),
                comparator_detected=_task_detected(task, comparator),
            )
            for task in tasks
        )
        seed = b"\0".join(
            (
                b"thesis-main-analysis-v1",
                b"RQ2",
                comparator.value.encode(),
            )
        )
        raw.append(
            (
                comparator,
                exact_mcnemar(rows),
                bootstrap_binary_risk_difference(rows, seed=hashlib.sha256(seed).digest()),
            )
        )
    adjusted = holm_adjust(
        tuple(
            HolmInput(comparison=comparator.value, p_value=result.p_value)
            for comparator, result, _interval in raw
        )
    )
    contrasts = tuple(
        RQ2Contrast(
            comparator=comparator,
            mcnemar=result,
            risk_difference_interval=interval,
            holm_adjusted_p_value=holm.adjusted_p_value,
            disposition=(
                "confirmed"
                if result.risk_difference.as_fraction() > 0
                and holm.adjusted_p_value.as_fraction() < _ALPHA
                else "not_confirmed"
            ),
        )
        for (comparator, result, interval), holm in zip(raw, adjusted, strict=True)
    )
    confirmed = tuple(
        contrast.comparator for contrast in contrasts if contrast.disposition == "confirmed"
    )
    return RQ2Analysis(
        methods=summaries,
        contrasts=contrasts,
        disposition=_rq2_disposition(confirmed),
    )


def analyze_rq3(
    comparisons: tuple[MainAnalysisReductionComparison, ...],
) -> RQ3Analysis:
    if not comparisons:
        return RQ3Analysis(
            cases=0,
            tasks=0,
            reducers=(),
            status="insufficient_cases",
            contrasts=(),
            disposition="reduction_advantage_not_confirmed",
        )
    identities = tuple((item.task_identity_sha256, item.case_ordinal) for item in comparisons)
    if len(identities) != len(set(identities)):
        raise ValueError("RQ3 analysis requires unique case identities")
    task_count = len({item.task_identity_sha256 for item in comparisons})
    summaries = tuple(_rq3_reducer_summary(comparisons, reducer) for reducer in MainAnalysisReducer)
    if len(comparisons) < 10 or task_count < 5:
        return RQ3Analysis(
            cases=len(comparisons),
            tasks=task_count,
            reducers=summaries,
            status="insufficient_cases",
            contrasts=(),
            disposition="reduction_advantage_not_confirmed",
        )

    raw: list[tuple[MainAnalysisReducer, WilcoxonResult, ExactInterval]] = []
    for comparator in _RQ3_COMPARATORS:
        rows = _rq3_task_rows(comparisons, comparator)
        seed = b"\0".join(
            (
                b"thesis-main-analysis-v1",
                b"RQ3",
                comparator.value.encode(),
            )
        )
        raw.append(
            (
                comparator,
                exact_wilcoxon(rows),
                bootstrap_clustered_retained_difference(
                    rows,
                    seed=hashlib.sha256(seed).digest(),
                ),
            )
        )
    adjusted = holm_adjust(
        tuple(
            HolmInput(comparison=comparator.value, p_value=result.p_value)
            for comparator, result, _interval in raw
        )
    )
    contrasts = tuple(
        RQ3Contrast(
            comparator=comparator,
            wilcoxon=result,
            retained_difference_interval=interval,
            holm_adjusted_p_value=holm.adjusted_p_value,
            disposition=(
                "confirmed"
                if result.rank_biserial.as_fraction() < 0
                and holm.adjusted_p_value.as_fraction() < _ALPHA
                else "not_confirmed"
            ),
        )
        for (comparator, result, interval), holm in zip(raw, adjusted, strict=True)
    )
    confirmed = {
        contrast.comparator for contrast in contrasts if contrast.disposition == "confirmed"
    }
    return RQ3Analysis(
        cases=len(comparisons),
        tasks=task_count,
        reducers=summaries,
        status="sufficient",
        contrasts=contrasts,
        disposition=(
            "reduction_advantage_confirmed"
            if {
                MainAnalysisReducer.FLAT_DDMIN,
                MainAnalysisReducer.HDD,
            }
            <= confirmed
            else "reduction_advantage_not_confirmed"
        ),
    )


def rq4_task_from_transfer(
    *,
    index: int,
    instance_id: str,
    trajectory: PrefixBenchFileBinding,
    results: tuple[TransferCaseResult, ...],
) -> RQ4TaskResult:
    if tuple(result.family for result in results) != _TRANSFER_FAMILIES:
        raise ValueError("transfer case results do not use the fixed family order")
    families = tuple(
        RQ4TaskFamily(
            family=result.family,
            status=(
                "unsupported_by_design"
                if result.family is TransferFamily.I3
                else result.status.value
            ),
            deterministic_replay=result.deterministic_replay,
        )
        for result in results
    )
    return RQ4TaskResult(
        index=index,
        instance_id=instance_id,
        trajectory=trajectory,
        adapter_error=None,
        families=families,
    )


def rq4_rejected_task(
    *,
    index: int,
    instance_id: str,
    trajectory: PrefixBenchFileBinding | None,
    error: str,
) -> RQ4TaskResult:
    return RQ4TaskResult(
        index=index,
        instance_id=instance_id,
        trajectory=trajectory,
        adapter_error=error,
        families=tuple(
            RQ4TaskFamily(
                family=family,
                status=(
                    "unsupported_by_design" if family is TransferFamily.I3 else "adapter_rejected"
                ),
            )
            for family in TransferFamily
        ),
    )


def analyze_rq4(tasks: tuple[RQ4TaskResult, ...]) -> RQ4Analysis:
    if len(tasks) != 20:
        raise ValueError("RQ4 analysis requires the fixed 20-task denominator")
    collection_complete = all(task.trajectory is not None for task in tasks)
    summaries = tuple(_rq4_family_summary(tasks, family) for family in TransferFamily)
    disposition: Literal["transfer_supported", "transfer_not_confirmed", "incomplete"]
    if not collection_complete:
        disposition = "incomplete"
    elif all(
        summary.disposition == "transfer_supported"
        for summary in summaries
        if summary.family in _MAPPED_TRANSFER_FAMILIES
    ):
        disposition = "transfer_supported"
    else:
        disposition = "transfer_not_confirmed"
    return RQ4Analysis(
        collection_complete=collection_complete,
        families=summaries,
        disposition=disposition,
    )


def file_binding(path: Path, project_root: Path) -> PrefixBenchFileBinding:
    root = project_root.resolve()
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"report source is outside the project: {path}") from exc
    if resolved.is_symlink() or not resolved.is_file():
        raise ValueError(f"report source must be a regular file: {path}")
    data = resolved.read_bytes()
    return PrefixBenchFileBinding(
        path=relative.as_posix(),
        bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def _rq2_method_summary(
    tasks: tuple[RQ2TaskResult, ...],
    method: MainAnalysisMethod,
) -> RQ2MethodSummary:
    task_outcomes = tuple(_method_outcomes(task, method) for task in tasks)
    outcomes = tuple(outcome for row in task_outcomes for outcome in row)
    counts = Counter(outcome.outcome for outcome in outcomes)
    scheduled = len(outcomes)
    accepted = scheduled - counts[MainAnalysisOutcome.INPUT_REJECTED]
    reached = accepted - counts[MainAnalysisOutcome.TARGET_NOT_REACHED]
    violations = counts[MainAnalysisOutcome.TARGET_VIOLATION]
    return RQ2MethodSummary(
        method=method,
        tasks=len(tasks),
        scheduled_slots=scheduled,
        outcomes=RQ2OutcomeCounts(
            **{outcome.value: counts[outcome] for outcome in MainAnalysisOutcome}
        ),
        input_acceptance_rate=ExactProbability.from_fraction(Fraction(accepted, scheduled)),
        target_reach_rate=ExactProbability.from_fraction(Fraction(reached, scheduled)),
        target_violation_rate=ExactProbability.from_fraction(Fraction(violations, scheduled)),
        oracle_equivalent_rate=ExactProbability.from_fraction(
            Fraction(counts[MainAnalysisOutcome.ORACLE_EQUIVALENT], scheduled)
        ),
        other_oracle_change_rate=ExactProbability.from_fraction(
            Fraction(counts[MainAnalysisOutcome.OTHER_ORACLE_CHANGE], scheduled)
        ),
        distinct_target_invariants=len(
            {
                outcome.slot.target_invariant
                for outcome in outcomes
                if outcome.outcome is MainAnalysisOutcome.TARGET_VIOLATION
            }
        ),
        target_violations_per_100_slots=ExactValue.from_fraction(
            Fraction(violations * 100, scheduled)
        ),
        tasks_with_target_violation=sum(
            any(outcome.outcome is MainAnalysisOutcome.TARGET_VIOLATION for outcome in row)
            for row in task_outcomes
        ),
    )


def _task_detected(task: RQ2TaskResult, method: MainAnalysisMethod) -> bool:
    return any(
        outcome.outcome is MainAnalysisOutcome.TARGET_VIOLATION
        for outcome in _method_outcomes(task, method)
    )


def _method_outcomes(
    task: RQ2TaskResult,
    method: MainAnalysisMethod,
) -> tuple[RQ2SlotOutcome, ...]:
    return next(item.outcomes for item in task.methods if item.method is method)


def _rq2_disposition(
    confirmed: tuple[MainAnalysisMethod, ...],
) -> Literal[
    "broad_superiority_confirmed",
    "nearest_baseline_advantage_confirmed",
    "broad_superiority_not_confirmed",
]:
    if confirmed == _RQ2_COMPARATORS:
        return "broad_superiority_confirmed"
    if confirmed == (MainAnalysisMethod.STATELESS_SEMANTIC,):
        return "nearest_baseline_advantage_confirmed"
    return "broad_superiority_not_confirmed"


def _rq3_reducer_summary(
    comparisons: tuple[MainAnalysisReductionComparison, ...],
    reducer: MainAnalysisReducer,
) -> RQ3ReducerSummary:
    results = tuple(
        next(result for result in comparison.results if result.reducer is reducer)
        for comparison in comparisons
    )
    before = sum(result.metrics.before.canonical_json_bytes for result in results)
    after = sum(result.metrics.after.canonical_json_bytes for result in results)
    return RQ3ReducerSummary(
        reducer=reducer,
        cases=len(results),
        tasks=len({comparison.task_identity_sha256 for comparison in comparisons}),
        before_bytes=before,
        after_bytes=after,
        pooled_retained_fraction=ExactValue.from_fraction(Fraction(after, before)),
        failed_retained=sum(
            result.status is MainAnalysisReductionStatus.FAILED_RETAINED for result in results
        ),
        candidates_evaluated=sum(result.metrics.candidates_evaluated for result in results),
        audits_executed=sum(result.metrics.audits_executed for result in results),
        accepted_reductions=sum(result.metrics.accepted_reductions for result in results),
    )


def _rq3_task_rows(
    comparisons: tuple[MainAnalysisReductionComparison, ...],
    comparator: MainAnalysisReducer,
) -> tuple[RetainedBytesTask, ...]:
    grouped: dict[str, list[MainAnalysisReductionComparison]] = defaultdict(list)
    for comparison in comparisons:
        grouped[comparison.task_identity_sha256].append(comparison)
    rows: list[RetainedBytesTask] = []
    for task_identity in sorted(grouped):
        task_cases = grouped[task_identity]
        source = tuple(
            next(
                result
                for result in comparison.results
                if result.reducer is MainAnalysisReducer.SOURCE_ANCHORED
            )
            for comparison in task_cases
        )
        other = tuple(
            next(result for result in comparison.results if result.reducer is comparator)
            for comparison in task_cases
        )
        before = sum(result.metrics.before.canonical_json_bytes for result in source)
        rows.append(
            RetainedBytesTask(
                task_identity=task_identity,
                treatment_after_bytes=sum(
                    result.metrics.after.canonical_json_bytes for result in source
                ),
                treatment_before_bytes=before,
                comparator_after_bytes=sum(
                    result.metrics.after.canonical_json_bytes for result in other
                ),
                comparator_before_bytes=before,
            )
        )
    return tuple(rows)


def _rq4_family_summary(
    tasks: tuple[RQ4TaskResult, ...],
    family: TransferFamily,
) -> RQ4FamilySummary:
    if family is TransferFamily.I3:
        return RQ4FamilySummary(
            family=family,
            mapping="unsupported_by_design",
            scheduled=len(tasks),
            executable=0,
            adapter_rejected=0,
            preexisting_violation=0,
            not_applicable=0,
            applicable=0,
            target_violation=0,
            other_oracle_change=0,
            deterministic_replay=0,
            disposition="unsupported_by_design",
        )
    rows = tuple(next(item for item in task.families if item.family is family) for task in tasks)
    counts = Counter(row.status for row in rows)
    applicable = counts["target_violation"] + counts["other_oracle_change"]
    supported = applicable >= 5 and counts["target_violation"] >= 2
    return RQ4FamilySummary(
        family=family,
        mapping="mapped",
        scheduled=len(rows),
        executable=len(rows) - counts["adapter_rejected"],
        adapter_rejected=counts["adapter_rejected"],
        preexisting_violation=counts["preexisting_violation"],
        not_applicable=counts["not_applicable"],
        applicable=applicable,
        target_violation=counts["target_violation"],
        other_oracle_change=counts["other_oracle_change"],
        deterministic_replay=sum(row.deterministic_replay for row in rows),
        disposition=("transfer_supported" if supported else "transfer_not_confirmed"),
    )


def _analysis_lineage(project_root: Path) -> AnalysisLineage:
    from evidence_harness_mutation.main_analysis_executable import (
        load_main_analysis_executable,
    )

    protocol = load_main_analysis_protocol(project_root)
    executable = load_main_analysis_executable(project_root)
    return AnalysisLineage(
        protocol_sha256=protocol.file.sha256,
        protocol_commit=protocol.preregistration_commit,
        executable_sha256=executable.file.sha256,
        executable_commit=executable.executable_commit,
    )


def _fixed_cases(
    cases: tuple[OfflineCampaignCase, ...],
) -> tuple[OfflineCampaignCase, ...]:
    return cases


def _parse_campaign(data: bytes) -> PrefixBenchTestCampaignReport:
    try:
        report = PrefixBenchTestCampaignReport.model_validate_json(data)
    except ValidationError as exc:
        raise ValueError("invalid PrefixBench test campaign") from exc
    if data != report.canonical_bytes():
        raise ValueError("PrefixBench test campaign is not canonical JSON")
    return report


def _parse_rq2(data: bytes) -> RQ2Report:
    return _parse_canonical_report(data, RQ2Report, "RQ2 report")


def _parse_rq3(data: bytes) -> RQ3Report:
    return _parse_canonical_report(data, RQ3Report, "RQ3 report")


def _parse_rq4(data: bytes) -> RQ4Report:
    return _parse_canonical_report(data, RQ4Report, "RQ4 report")


def _parse_canonical_report[T: BaseModel](
    data: bytes,
    model: type[T],
    label: str,
) -> T:
    try:
        report = TypeAdapter(model).validate_json(data)
    except ValidationError as exc:
        raise ValueError(f"invalid {label}") from exc
    canonical = getattr(report, "canonical_bytes", None)
    if not callable(canonical) or data != canonical():
        raise ValueError(f"{label} is not canonical JSON")
    return report


def _check_report[T: BaseModel](
    project_root: Path,
    relative: Path,
    model: type[T],
    builder: Callable[[Path], T],
    *,
    validate: Callable[[T], bool],
    raw_anchor: Path,
) -> tuple[str, ...]:
    root = project_root.resolve()
    try:
        data = _read_regular_file(root / relative, f"{relative.name} report")
        report = _parse_canonical_report(data, model, relative.name)
        if not validate(report):
            raise ValueError(f"{relative.name} derived analysis is stale")
        _analysis_lineage(root)
        if (root / raw_anchor).exists():
            expected = builder(root)
            canonical = getattr(expected, "canonical_bytes", None)
            if not callable(canonical) or canonical() != data:
                raise ValueError(f"{relative.name} does not match a full rebuild")
    except (OSError, ValueError) as exc:
        return (f"invalid main-analysis report {relative}: {exc}",)
    return ()


def _historical_result(report: dict[str, Any]) -> MainAnalysisRQ1Result:
    if report.get("suite_id") != "historical-7":
        raise ValueError("Historical-7 suite identity has changed")
    results = report.get("results")
    if not isinstance(results, list):
        raise ValueError("Historical-7 results must be a list")
    by_case: dict[str, dict[str, bool]] = defaultdict(dict)
    for value in results:
        if not isinstance(value, dict):
            raise ValueError("Historical-7 result must be an object")
        case_id = value.get("case_id")
        role = value.get("revision_role")
        matched = value.get("matches_expected")
        if (
            not isinstance(case_id, str)
            or role not in {"vulnerable", "fixed"}
            or not isinstance(matched, bool)
        ):
            raise ValueError("Historical-7 result identity is invalid")
        if role in by_case[case_id]:
            raise ValueError("Historical-7 contains a duplicate revision role")
        by_case[case_id][role] = matched
    if len(by_case) != 7 or any(
        set(revisions) != {"vulnerable", "fixed"} for revisions in by_case.values()
    ):
        raise ValueError("Historical-7 does not contain seven complete case pairs")
    successful = sum(all(revisions.values()) for revisions in by_case.values())
    return MainAnalysisRQ1Result(
        successful_pairs=successful,
        disposition="threshold_met" if successful >= 6 else "threshold_not_met",
    )


def _read_bound_file(project_root: Path, binding: PrefixBenchFileBinding) -> bytes:
    path = project_root / binding.path
    data = _read_regular_file(path, f"bound report source {binding.path}")
    if len(data) != binding.bytes or hashlib.sha256(data).hexdigest() != binding.sha256:
        raise ValueError(f"bound report source is stale: {binding.path}")
    return data


def _read_regular_file(path: Path, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} must be a regular file: {path}")
    return path.read_bytes()


def _json_object(data: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(data)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _project_root(project_root: Path) -> Path:
    root = project_root.resolve()
    if not root.is_dir():
        raise ValueError(f"project root is not a directory: {root}")
    return root


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
