from __future__ import annotations

import hashlib
import json
from collections import Counter
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal, Self

from pydantic import Field, ValidationError, model_validator

from evidence_harness.collection_profile import PREFIXBENCH_V1
from evidence_harness.protocol import (
    CompletionReviewStarted,
    ExecutorTurnStarted,
    FinalizationStarted,
    ProducerAttestation,
    RecoveryRequired,
    WorkBatchStarted,
)
from evidence_harness_mutation.model import FrozenModel, Sha256

PREFIXBENCH_PROFILE = PREFIXBENCH_V1.name
PREFIXBENCH_SPLIT_NAMESPACE = "prefixbench-task-split-v1"
EXPECTED_CANONICAL_TASKS = 89

_PHASE_EVENT_TYPES = {
    "thinking": "executor_turn_started",
    "executing": "work_batch_started",
    "finalizing": "finalization_started",
    "reviewing": "completion_review_started",
    "recovering": "recovery_required",
}
_LEGACY_EVENT_TYPES = {
    "completion_reviews": "completion_review",
    "completion_rejections": "completion_rejected",
    "replans": "replanned",
}
_SOURCE_COMMIT_PATTERN = frozenset((40, 64))

NonNegativeInt = Annotated[int, Field(ge=0)]


class PrefixBenchStatus(StrEnum):
    SOURCE_COHORT_UNAVAILABLE = "source_cohort_unavailable"
    PHASE_COVERAGE_INCOMPLETE = "phase_coverage_incomplete"
    READY = "ready"


class PrefixBenchSplit(StrEnum):
    DEVELOPMENT = "development"
    TEST = "test"


class PrefixBenchExecutionMode(StrEnum):
    LIVE = "live"
    REPLAY = "replay"


class PrefixBenchPhase(StrEnum):
    THINKING = "thinking"
    EXECUTING = "executing"
    FINALIZING = "finalizing"
    REVIEWING = "reviewing"
    RECOVERING = "recovering"


class PrefixBenchExclusion(StrEnum):
    NON_LIVE_RESULT = "non_live_result"
    JOURNAL_NOT_BOUND_BY_CANONICAL = "journal_not_bound_by_canonical"
    UNSUPPORTED_JOURNAL_SCHEMA = "unsupported_journal_schema"
    PRODUCER_COMMIT_UNATTESTED = "producer_commit_unattested"
    PREFIXBENCH_PROFILE_MISSING = "prefixbench_profile_missing"
    PHASE_EVIDENCE_INCOMPLETE = "phase_evidence_incomplete"


class PrefixBenchSourceExclusion(StrEnum):
    NON_LIVE_RESULT = "non_live_result"
    JOURNAL_NOT_BOUND_BY_CANONICAL = "journal_not_bound_by_canonical"
    UNSUPPORTED_JOURNAL_SCHEMA = "unsupported_journal_schema"
    PRODUCER_COMMIT_UNATTESTED = "producer_commit_unattested"
    PREFIXBENCH_PROFILE_MISSING = "prefixbench_profile_missing"
    PROFILE_OPTIONS_MISMATCH = "profile_options_mismatch"


class PrefixBenchFileBinding(FrozenModel):
    path: str = Field(min_length=1)
    bytes: NonNegativeInt
    sha256: Sha256


class PrefixBenchSources(FrozenModel):
    result: PrefixBenchFileBinding
    config: PrefixBenchFileBinding
    journal: PrefixBenchFileBinding | None


class PrefixBenchPhaseCounts(FrozenModel):
    thinking: NonNegativeInt = 0
    executing: NonNegativeInt = 0
    finalizing: NonNegativeInt = 0
    reviewing: NonNegativeInt = 0
    recovering: NonNegativeInt = 0

    @property
    def complete(self) -> bool:
        return all(value > 0 for value in self.model_dump().values())

    def __add__(self, other: PrefixBenchPhaseCounts) -> PrefixBenchPhaseCounts:
        return PrefixBenchPhaseCounts(
            **{
                field: getattr(self, field) + getattr(other, field)
                for field in type(self).model_fields
            }
        )


class PrefixBenchLegacyCounts(FrozenModel):
    finish_proposals: NonNegativeInt = 0
    completion_reviews: NonNegativeInt = 0
    completion_rejections: NonNegativeInt = 0
    replans: NonNegativeInt = 0

    def __add__(self, other: PrefixBenchLegacyCounts) -> PrefixBenchLegacyCounts:
        return PrefixBenchLegacyCounts(
            **{
                field: getattr(self, field) + getattr(other, field)
                for field in type(self).model_fields
            }
        )


class PrefixBenchPhaseWitness(FrozenModel):
    phase: PrefixBenchPhase
    event_type: str = Field(min_length=1)


class PrefixBenchSplitPolicy(FrozenModel):
    namespace: Literal["prefixbench-task-split-v1"] = "prefixbench-task-split-v1"
    algorithm: Literal["sha256_first_8_bytes_mod_10"] = "sha256_first_8_bytes_mod_10"
    development_buckets: tuple[int, ...] = (0, 1, 2)
    test_buckets: tuple[int, ...] = (3, 4, 5, 6, 7, 8, 9)

    @model_validator(mode="after")
    def validate_buckets(self) -> Self:
        if self.development_buckets != (0, 1, 2):
            raise ValueError("PrefixBench development buckets must remain frozen")
        if self.test_buckets != (3, 4, 5, 6, 7, 8, 9):
            raise ValueError("PrefixBench test buckets must remain frozen")
        return self


class PrefixBenchTaskAssessment(FrozenModel):
    index: int = Field(ge=1)
    name: str = Field(min_length=1)
    split: PrefixBenchSplit
    split_bucket: int = Field(ge=0, le=9)
    task_identity_sha256: Sha256
    execution_mode: PrefixBenchExecutionMode
    sources: PrefixBenchSources
    journal_schema_version: int | None
    phase_events: PrefixBenchPhaseCounts
    legacy_events: PrefixBenchLegacyCounts
    admitted: bool
    exclusion_reasons: tuple[PrefixBenchExclusion, ...]

    @model_validator(mode="after")
    def validate_admission(self) -> Self:
        if self.admitted == bool(self.exclusion_reasons):
            raise ValueError("admitted tasks must have no exclusions")
        expected_split = (
            PrefixBenchSplit.DEVELOPMENT
            if self.split_bucket in (0, 1, 2)
            else PrefixBenchSplit.TEST
        )
        if self.split is not expected_split:
            raise ValueError("task split does not match its bucket")
        if self.execution_mode is PrefixBenchExecutionMode.REPLAY:
            if self.sources.journal is not None or self.journal_schema_version is not None:
                raise ValueError("replay assessments cannot bind a live journal")
            if self.exclusion_reasons != (PrefixBenchExclusion.NON_LIVE_RESULT,):
                raise ValueError("replay assessments require only the non-live exclusion")
        return self


class PrefixBenchJournalSchemas(FrozenModel):
    schema_1: NonNegativeInt = 0
    schema_2: NonNegativeInt = 0
    other: NonNegativeInt = 0
    missing: NonNegativeInt = 0


class PrefixBenchExclusionCounts(FrozenModel):
    non_live_result: NonNegativeInt = 0
    journal_not_bound_by_canonical: NonNegativeInt = 0
    unsupported_journal_schema: NonNegativeInt = 0
    producer_commit_unattested: NonNegativeInt = 0
    prefixbench_profile_missing: NonNegativeInt = 0
    phase_evidence_incomplete: NonNegativeInt = 0


class PrefixBenchSummary(FrozenModel):
    tasks: NonNegativeInt
    live: NonNegativeInt
    replay: NonNegativeInt
    admitted: NonNegativeInt
    excluded: NonNegativeInt
    development: NonNegativeInt
    test: NonNegativeInt
    journal_schemas: PrefixBenchJournalSchemas
    phase_events: PrefixBenchPhaseCounts
    legacy_events: PrefixBenchLegacyCounts
    exclusions: PrefixBenchExclusionCounts

    @classmethod
    def from_tasks(
        cls,
        tasks: tuple[PrefixBenchTaskAssessment, ...],
    ) -> PrefixBenchSummary:
        phase_counts = PrefixBenchPhaseCounts()
        legacy_counts = PrefixBenchLegacyCounts()
        for task in tasks:
            phase_counts += task.phase_events
            legacy_counts += task.legacy_events

        exclusion_counts = Counter(
            reason.value for task in tasks for reason in task.exclusion_reasons
        )
        return cls(
            tasks=len(tasks),
            live=sum(task.execution_mode is PrefixBenchExecutionMode.LIVE for task in tasks),
            replay=sum(task.execution_mode is PrefixBenchExecutionMode.REPLAY for task in tasks),
            admitted=sum(task.admitted for task in tasks),
            excluded=sum(not task.admitted for task in tasks),
            development=sum(task.split is PrefixBenchSplit.DEVELOPMENT for task in tasks),
            test=sum(task.split is PrefixBenchSplit.TEST for task in tasks),
            journal_schemas=PrefixBenchJournalSchemas(
                schema_1=sum(task.journal_schema_version == 1 for task in tasks),
                schema_2=sum(task.journal_schema_version == 2 for task in tasks),
                other=sum(task.journal_schema_version not in (None, 1, 2) for task in tasks),
                missing=sum(task.journal_schema_version is None for task in tasks),
            ),
            phase_events=phase_counts,
            legacy_events=legacy_counts,
            exclusions=PrefixBenchExclusionCounts(**exclusion_counts),
        )


class PrefixBenchReadiness(FrozenModel):
    schema_version: Literal[1] = 1
    benchmark: Literal["PrefixBench"] = "PrefixBench"
    status: PrefixBenchStatus
    dataset: str = Field(min_length=1)
    claim_boundary: Literal["readiness_only_no_five_phase_benchmark_results"] = (
        "readiness_only_no_five_phase_benchmark_results"
    )
    sources: dict[Literal["canonical", "matrix"], PrefixBenchFileBinding]
    split_policy: PrefixBenchSplitPolicy
    phase_contract: tuple[PrefixBenchPhaseWitness, ...]
    tasks: tuple[PrefixBenchTaskAssessment, ...]
    summary: PrefixBenchSummary

    @model_validator(mode="after")
    def validate_report(self) -> Self:
        expected_phases = tuple(
            PrefixBenchPhaseWitness(phase=phase, event_type=_PHASE_EVENT_TYPES[phase.value])
            for phase in PrefixBenchPhase
        )
        if self.phase_contract != expected_phases:
            raise ValueError("PrefixBench phase contract has changed")
        if tuple(task.index for task in self.tasks) != tuple(range(1, len(self.tasks) + 1)):
            raise ValueError("PrefixBench task indices must be contiguous")
        if len({task.name for task in self.tasks}) != len(self.tasks):
            raise ValueError("PrefixBench task names must be unique")
        if self.summary != PrefixBenchSummary.from_tasks(self.tasks):
            raise ValueError("PrefixBench summary does not match task assessments")
        expected_status = (
            PrefixBenchStatus.READY
            if self.summary.admitted
            else PrefixBenchStatus.SOURCE_COHORT_UNAVAILABLE
        )
        if self.status is not expected_status:
            raise ValueError("PrefixBench status does not match admitted task count")
        if set(self.sources) != {"canonical", "matrix"}:
            raise ValueError("PrefixBench report requires canonical and matrix bindings")
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


class PrefixBenchSourceAdmission(FrozenModel):
    admitted: bool
    exclusion_reasons: tuple[PrefixBenchSourceExclusion, ...] = ()

    @model_validator(mode="after")
    def validate_admission(self) -> Self:
        if self.admitted == bool(self.exclusion_reasons):
            raise ValueError("source-admitted tasks must have no source exclusions")
        return self


class PrefixBenchPhaseEligibility(FrozenModel):
    thinking: tuple[int, ...] = ()
    executing: tuple[int, ...] = ()
    finalizing: tuple[int, ...] = ()
    reviewing: tuple[int, ...] = ()
    recovering: tuple[int, ...] = ()

    @model_validator(mode="after")
    def validate_lines(self) -> Self:
        for phase, lines in self.model_dump().items():
            if any(line < 1 for line in lines) or tuple(sorted(set(lines))) != lines:
                raise ValueError(f"{phase} witness lines must be unique positive ordinals")
        return self

    def counts(self) -> PrefixBenchPhaseCounts:
        return PrefixBenchPhaseCounts(
            **{field: len(getattr(self, field)) for field in type(self).model_fields}
        )


class PrefixBenchSplitPhaseCoverage(FrozenModel):
    development: PrefixBenchPhaseCounts = Field(default_factory=PrefixBenchPhaseCounts)
    test: PrefixBenchPhaseCounts = Field(default_factory=PrefixBenchPhaseCounts)


class PrefixBenchCoveragePolicy(FrozenModel):
    name: Literal["prefixbench-development-coverage-v1"] = "prefixbench-development-coverage-v1"
    development: PrefixBenchPhaseCounts = Field(
        default_factory=lambda: PrefixBenchPhaseCounts(
            thinking=1,
            executing=1,
            finalizing=1,
            reviewing=1,
            recovering=1,
        )
    )
    test: PrefixBenchPhaseCounts = Field(default_factory=PrefixBenchPhaseCounts)

    @model_validator(mode="after")
    def validate_frozen_policy(self) -> Self:
        if self.development != PrefixBenchPhaseCounts(
            thinking=1,
            executing=1,
            finalizing=1,
            reviewing=1,
            recovering=1,
        ):
            raise ValueError("PrefixBench development coverage policy has changed")
        if self.test != PrefixBenchPhaseCounts():
            raise ValueError("PrefixBench test coverage is reporting-only")
        return self

    def accepts(self, coverage: PrefixBenchSplitPhaseCoverage) -> bool:
        return all(
            getattr(coverage.development, phase) >= getattr(self.development, phase)
            for phase in PrefixBenchPhaseCounts.model_fields
        )


class PrefixBenchTaskAssessmentV2(FrozenModel):
    index: int = Field(ge=1)
    name: str = Field(min_length=1)
    split: PrefixBenchSplit
    split_bucket: int = Field(ge=0, le=9)
    task_identity_sha256: Sha256
    execution_mode: PrefixBenchExecutionMode
    sources: PrefixBenchSources
    journal_schema_version: int | None
    producer: ProducerAttestation | None
    source_admission: PrefixBenchSourceAdmission
    phase_eligibility: PrefixBenchPhaseEligibility
    phase_errors: tuple[str, ...] = ()
    legacy_events: PrefixBenchLegacyCounts

    @model_validator(mode="after")
    def validate_task(self) -> Self:
        expected_split = (
            PrefixBenchSplit.DEVELOPMENT
            if self.split_bucket in (0, 1, 2)
            else PrefixBenchSplit.TEST
        )
        if self.split is not expected_split:
            raise ValueError("task split does not match its bucket")
        if self.source_admission.admitted:
            if self.sources.journal is None or self.journal_schema_version != 2:
                raise ValueError("source-admitted tasks require a schema-2 journal")
            if self.producer is None:
                raise ValueError("source-admitted tasks require a producer")
        elif self.phase_eligibility != PrefixBenchPhaseEligibility():
            raise ValueError("source-excluded tasks cannot contribute phase witnesses")
        return self


class PrefixBenchSourceExclusionCounts(FrozenModel):
    non_live_result: NonNegativeInt = 0
    journal_not_bound_by_canonical: NonNegativeInt = 0
    unsupported_journal_schema: NonNegativeInt = 0
    producer_commit_unattested: NonNegativeInt = 0
    prefixbench_profile_missing: NonNegativeInt = 0
    profile_options_mismatch: NonNegativeInt = 0


class PrefixBenchSummaryV2(FrozenModel):
    tasks: NonNegativeInt
    source_admitted: NonNegativeInt
    source_excluded: NonNegativeInt
    development: NonNegativeInt
    test: NonNegativeInt
    phase_coverage: PrefixBenchSplitPhaseCoverage
    legacy_events: PrefixBenchLegacyCounts
    source_exclusions: PrefixBenchSourceExclusionCounts

    @classmethod
    def from_tasks(
        cls,
        tasks: tuple[PrefixBenchTaskAssessmentV2, ...],
    ) -> PrefixBenchSummaryV2:
        coverage = {
            PrefixBenchSplit.DEVELOPMENT: PrefixBenchPhaseCounts(),
            PrefixBenchSplit.TEST: PrefixBenchPhaseCounts(),
        }
        legacy = PrefixBenchLegacyCounts()
        for task in tasks:
            legacy += task.legacy_events
            if task.source_admission.admitted:
                coverage[task.split] += task.phase_eligibility.counts()
        exclusions = Counter(
            reason.value for task in tasks for reason in task.source_admission.exclusion_reasons
        )
        return cls(
            tasks=len(tasks),
            source_admitted=sum(task.source_admission.admitted for task in tasks),
            source_excluded=sum(not task.source_admission.admitted for task in tasks),
            development=sum(task.split is PrefixBenchSplit.DEVELOPMENT for task in tasks),
            test=sum(task.split is PrefixBenchSplit.TEST for task in tasks),
            phase_coverage=PrefixBenchSplitPhaseCoverage(
                development=coverage[PrefixBenchSplit.DEVELOPMENT],
                test=coverage[PrefixBenchSplit.TEST],
            ),
            legacy_events=legacy,
            source_exclusions=PrefixBenchSourceExclusionCounts(**exclusions),
        )


class PrefixBenchReadinessV2(FrozenModel):
    schema_version: Literal[2] = 2
    benchmark: Literal["PrefixBench"] = "PrefixBench"
    status: PrefixBenchStatus
    dataset: str = Field(min_length=1)
    collection_profile: Literal["prefixbench-v1"] = "prefixbench-v1"
    claim_boundary: Literal["readiness_only_no_five_phase_benchmark_results"] = (
        "readiness_only_no_five_phase_benchmark_results"
    )
    sources: dict[Literal["canonical", "matrix"], PrefixBenchFileBinding]
    split_policy: PrefixBenchSplitPolicy
    phase_contract: tuple[PrefixBenchPhaseWitness, ...]
    coverage_policy: PrefixBenchCoveragePolicy
    tasks: tuple[PrefixBenchTaskAssessmentV2, ...]
    summary: PrefixBenchSummaryV2

    @model_validator(mode="after")
    def validate_report(self) -> Self:
        expected_phases = tuple(
            PrefixBenchPhaseWitness(phase=phase, event_type=_PHASE_EVENT_TYPES[phase.value])
            for phase in PrefixBenchPhase
        )
        if self.phase_contract != expected_phases:
            raise ValueError("PrefixBench phase contract has changed")
        if tuple(task.index for task in self.tasks) != tuple(range(1, len(self.tasks) + 1)):
            raise ValueError("PrefixBench task indices must be contiguous")
        if len({task.name for task in self.tasks}) != len(self.tasks):
            raise ValueError("PrefixBench task names must be unique")
        if self.summary != PrefixBenchSummaryV2.from_tasks(self.tasks):
            raise ValueError("PrefixBench summary does not match task assessments")
        expected_status = _prefixbench_v2_status(self.summary, self.coverage_policy)
        if self.status is not expected_status:
            raise ValueError("PrefixBench status does not match source and phase evidence")
        if set(self.sources) != {"canonical", "matrix"}:
            raise ValueError("PrefixBench report requires canonical and matrix bindings")
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


type PrefixBenchReadinessReport = PrefixBenchReadiness | PrefixBenchReadinessV2


def prefixbench_task_split(
    *,
    dataset: str,
    name: str,
    task_checksum: str,
    task_git_url: str,
    task_git_commit_id: str,
) -> tuple[PrefixBenchSplit, int, str]:
    fields = (
        PREFIXBENCH_SPLIT_NAMESPACE,
        dataset,
        name,
        task_checksum,
        task_git_url,
        task_git_commit_id,
    )
    digest = hashlib.sha256("\0".join(fields).encode()).digest()
    bucket = int.from_bytes(digest[:8], "big") % 10
    split = PrefixBenchSplit.DEVELOPMENT if bucket < 3 else PrefixBenchSplit.TEST
    return split, bucket, digest.hex()


def inspect_prefixbench(
    canonical_path: Path,
    matrix_path: Path,
    project_root: Path,
    *,
    expected_task_count: int = EXPECTED_CANONICAL_TASKS,
) -> PrefixBenchReadinessReport:
    root = project_root.resolve()
    canonical_bytes, canonical = _read_json_object(canonical_path)
    matrix_bytes, matrix = _read_json_object(matrix_path)
    if canonical.get("schema_version") == 2:
        return _inspect_prefixbench_v2(
            canonical_path=canonical_path,
            canonical_bytes=canonical_bytes,
            canonical=canonical,
            matrix_path=matrix_path,
            matrix_bytes=matrix_bytes,
            matrix=matrix,
            project_root=root,
            expected_task_count=expected_task_count,
        )
    rows, matrix_tasks, dataset = _validate_manifests(
        canonical,
        matrix,
        matrix_bytes,
        expected_task_count=expected_task_count,
    )

    assessments = tuple(
        _inspect_task(
            row,
            matrix_task,
            dataset=dataset,
            project_root=root,
        )
        for row, matrix_task in zip(rows, matrix_tasks, strict=True)
    )
    summary = PrefixBenchSummary.from_tasks(assessments)
    return PrefixBenchReadiness(
        status=(
            PrefixBenchStatus.READY
            if summary.admitted
            else PrefixBenchStatus.SOURCE_COHORT_UNAVAILABLE
        ),
        dataset=dataset,
        sources={
            "canonical": _file_binding(canonical_path, canonical_bytes, root),
            "matrix": _file_binding(matrix_path, matrix_bytes, root),
        },
        split_policy=PrefixBenchSplitPolicy(),
        phase_contract=tuple(
            PrefixBenchPhaseWitness(phase=phase, event_type=_PHASE_EVENT_TYPES[phase.value])
            for phase in PrefixBenchPhase
        ),
        tasks=assessments,
        summary=summary,
    )


def _inspect_prefixbench_v2(
    *,
    canonical_path: Path,
    canonical_bytes: bytes,
    canonical: dict[str, Any],
    matrix_path: Path,
    matrix_bytes: bytes,
    matrix: dict[str, Any],
    project_root: Path,
    expected_task_count: int,
) -> PrefixBenchReadinessV2:
    if canonical.get("collection_profile") != PREFIXBENCH_PROFILE:
        raise ValueError("canonical collection profile does not match PrefixBench")
    rows, matrix_tasks, dataset = _validate_manifests(
        canonical,
        matrix,
        matrix_bytes,
        expected_task_count=expected_task_count,
        canonical_schema=2,
    )
    assessments = tuple(
        _inspect_task_v2(
            row,
            matrix_task,
            dataset=dataset,
            project_root=project_root,
        )
        for row, matrix_task in zip(rows, matrix_tasks, strict=True)
    )
    producers = {
        (
            task.producer.commit,
            task.producer.tree,
            task.producer.source_sha256,
        )
        for task in assessments
        if task.producer is not None
    }
    if len(producers) > 1:
        raise ValueError("PrefixBench source cohort contains multiple producers")
    summary = PrefixBenchSummaryV2.from_tasks(assessments)
    coverage_policy = PrefixBenchCoveragePolicy()
    return PrefixBenchReadinessV2(
        status=_prefixbench_v2_status(summary, coverage_policy),
        dataset=dataset,
        sources={
            "canonical": _file_binding(canonical_path, canonical_bytes, project_root),
            "matrix": _file_binding(matrix_path, matrix_bytes, project_root),
        },
        split_policy=PrefixBenchSplitPolicy(),
        phase_contract=tuple(
            PrefixBenchPhaseWitness(phase=phase, event_type=_PHASE_EVENT_TYPES[phase.value])
            for phase in PrefixBenchPhase
        ),
        coverage_policy=coverage_policy,
        tasks=assessments,
        summary=summary,
    )


def check_prefixbench_readiness(
    *,
    readiness_path: Path,
    canonical_path: Path,
    matrix_path: Path,
    project_root: Path,
    expected_task_count: int = EXPECTED_CANONICAL_TASKS,
) -> tuple[str, ...]:
    try:
        report_bytes = readiness_path.read_bytes()
        raw_report = _object(json.loads(report_bytes), str(readiness_path))
        if raw_report.get("schema_version") == 1:
            report: PrefixBenchReadinessReport = PrefixBenchReadiness.model_validate(raw_report)
        elif raw_report.get("schema_version") == 2:
            report = PrefixBenchReadinessV2.model_validate(raw_report)
        else:
            raise ValueError("unsupported PrefixBench readiness schema")
    except (OSError, ValueError) as exc:
        return (f"invalid PrefixBench readiness artifact: {exc}",)

    errors: list[str] = []
    if report_bytes != report.canonical_bytes():
        errors.append("PrefixBench readiness artifact is not canonical JSON")

    root = project_root.resolve()
    try:
        canonical_bytes = canonical_path.read_bytes()
        matrix_bytes = matrix_path.read_bytes()
        _validate_file_binding(report.sources["canonical"], canonical_path, canonical_bytes, root)
        _validate_file_binding(report.sources["matrix"], matrix_path, matrix_bytes, root)
    except (OSError, ValueError) as exc:
        errors.append(f"invalid PrefixBench manifest binding: {exc}")
        return tuple(errors)

    try:
        raw_paths = tuple(
            _resolve_bound_path(binding, root)
            for task in report.tasks
            for binding in (task.sources.result, task.sources.config, task.sources.journal)
            if binding is not None
        )
    except ValueError as exc:
        errors.append(f"invalid PrefixBench raw-source binding: {exc}")
        return tuple(errors)

    present = tuple(path.is_file() for path in raw_paths)
    if any(present) and not all(present):
        errors.append(
            "PrefixBench raw sources are partially available: "
            f"{sum(present)}/{len(present)} bound files exist"
        )
        return tuple(errors)
    if not any(present):
        return tuple(errors)

    try:
        rebuilt = inspect_prefixbench(
            canonical_path,
            matrix_path,
            root,
            expected_task_count=expected_task_count,
        )
        if rebuilt.canonical_bytes() != report_bytes:
            errors.append("PrefixBench readiness artifact is stale")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        errors.append(f"cannot rebuild PrefixBench readiness artifact: {exc}")
    return tuple(errors)


def _validate_manifests(
    canonical: dict[str, Any],
    matrix: dict[str, Any],
    matrix_bytes: bytes,
    *,
    expected_task_count: int,
    canonical_schema: int = 1,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    if canonical.get("schema_version") != canonical_schema:
        raise ValueError("unsupported canonical manifest schema")
    if matrix.get("schema_version") != 1:
        raise ValueError("unsupported matrix schema")
    dataset = _required_string(canonical, "dataset")
    if dataset != _required_string(matrix, "dataset"):
        raise ValueError("canonical dataset does not match the matrix")
    if canonical.get("matrix_sha256") != hashlib.sha256(matrix_bytes).hexdigest():
        raise ValueError("canonical matrix hash does not match the matrix")

    rows = [_object(item, "canonical task") for item in _list(canonical, "tasks")]
    matrix_tasks = [_object(item, "matrix task") for item in _list(matrix, "tasks")]
    if len(rows) != expected_task_count:
        raise ValueError(
            f"canonical manifest must contain {expected_task_count} tasks, got {len(rows)}"
        )
    if len(matrix_tasks) != expected_task_count:
        raise ValueError(f"matrix must contain {expected_task_count} tasks")
    indices = [_required_int(row, "index") for row in rows]
    if indices != list(range(1, expected_task_count + 1)):
        raise ValueError("canonical task indices must be contiguous")
    canonical_names = [_required_string(row, "name") for row in rows]
    matrix_names = [_required_string(task, "name") for task in matrix_tasks]
    if canonical_names != matrix_names:
        raise ValueError("canonical task order does not match the matrix")
    if len(set(canonical_names)) != len(canonical_names):
        raise ValueError("canonical task names must be unique")

    counts = _object(canonical.get("counts"), "canonical counts")
    expected_counts = {
        "completed": len(rows),
        "passed": sum(row.get("status") == "passed" for row in rows),
        "failed": sum(row.get("status") == "failed" for row in rows),
        "error": sum(row.get("status") == "error" for row in rows),
    }
    if counts != expected_counts:
        raise ValueError("canonical counts do not match task rows")
    return rows, matrix_tasks, dataset


def _inspect_task(
    row: dict[str, Any],
    matrix_task: dict[str, Any],
    *,
    dataset: str,
    project_root: Path,
) -> PrefixBenchTaskAssessment:
    index = _required_int(row, "index")
    name = _required_string(row, "name")
    if name != _required_string(matrix_task, "name"):
        raise ValueError(f"matrix task does not match canonical row: {name}")

    result_path = _resolve_result_path(row, project_root)
    result_bytes, result = _read_json_object(result_path)
    _validate_hash(result_bytes, _required_string(row, "result_sha256"), f"result: {name}")

    config_path = result_path.parent / "config.json"
    config_bytes, config = _read_json_object(config_path)
    _validate_hash(config_bytes, _required_string(row, "config_sha256"), f"config: {name}")
    _validate_result_identity(row, result, config, name=name)

    task_checksum = _required_string(row, "task_checksum")
    task_git_url = _required_string(row, "task_git_url")
    task_git_commit_id = _required_string(row, "task_git_commit_id")
    split, split_bucket, identity_sha256 = prefixbench_task_split(
        dataset=dataset,
        name=name,
        task_checksum=task_checksum,
        task_git_url=task_git_url,
        task_git_commit_id=task_git_commit_id,
    )

    result_binding = _file_binding(result_path, result_bytes, project_root)
    config_binding = _file_binding(config_path, config_bytes, project_root)
    execution_mode = _execution_mode(result, name=name)
    if execution_mode is PrefixBenchExecutionMode.REPLAY:
        return PrefixBenchTaskAssessment(
            index=index,
            name=name,
            split=split,
            split_bucket=split_bucket,
            task_identity_sha256=identity_sha256,
            execution_mode=execution_mode,
            sources=PrefixBenchSources(
                result=result_binding,
                config=config_binding,
                journal=None,
            ),
            journal_schema_version=None,
            phase_events=PrefixBenchPhaseCounts(),
            legacy_events=PrefixBenchLegacyCounts(),
            admitted=False,
            exclusion_reasons=(PrefixBenchExclusion.NON_LIVE_RESULT,),
        )

    journal_path = result_path.parent / "agent" / "evidence-harness" / "events.jsonl"
    journal_bytes = journal_path.read_bytes()
    events, started_payload = _parse_journal(journal_bytes, journal_path)
    journal_schema_version = _journal_schema(started_payload, journal_path)
    journal_binding = _file_binding(journal_path, journal_bytes, project_root)
    phase_events = PrefixBenchPhaseCounts(
        **{
            phase: sum(event["type"] == event_type for event in events)
            for phase, event_type in _PHASE_EVENT_TYPES.items()
        }
    )
    legacy_events = PrefixBenchLegacyCounts(
        finish_proposals=sum(
            event["type"] == "agent_decision" and event["payload"].get("action") == "finish"
            for event in events
        ),
        **{
            field: sum(event["type"] == event_type for event in events)
            for field, event_type in _LEGACY_EVENT_TYPES.items()
        },
    )

    exclusions: list[PrefixBenchExclusion] = []
    if not _journal_is_canonically_bound(row, journal_binding):
        exclusions.append(PrefixBenchExclusion.JOURNAL_NOT_BOUND_BY_CANONICAL)
    if journal_schema_version != 2:
        exclusions.append(PrefixBenchExclusion.UNSUPPORTED_JOURNAL_SCHEMA)
    if not _producer_is_attested(row, started_payload):
        exclusions.append(PrefixBenchExclusion.PRODUCER_COMMIT_UNATTESTED)
    if started_payload.get("prefixbench_profile") != PREFIXBENCH_PROFILE:
        exclusions.append(PrefixBenchExclusion.PREFIXBENCH_PROFILE_MISSING)
    if not phase_events.complete:
        exclusions.append(PrefixBenchExclusion.PHASE_EVIDENCE_INCOMPLETE)

    return PrefixBenchTaskAssessment(
        index=index,
        name=name,
        split=split,
        split_bucket=split_bucket,
        task_identity_sha256=identity_sha256,
        execution_mode=execution_mode,
        sources=PrefixBenchSources(
            result=result_binding,
            config=config_binding,
            journal=journal_binding,
        ),
        journal_schema_version=journal_schema_version,
        phase_events=phase_events,
        legacy_events=legacy_events,
        admitted=not exclusions,
        exclusion_reasons=tuple(exclusions),
    )


def _inspect_task_v2(
    row: dict[str, Any],
    matrix_task: dict[str, Any],
    *,
    dataset: str,
    project_root: Path,
) -> PrefixBenchTaskAssessmentV2:
    index = _required_int(row, "index")
    name = _required_string(row, "name")
    if name != _required_string(matrix_task, "name"):
        raise ValueError(f"matrix task does not match canonical row: {name}")

    result_path = _resolve_result_path(row, project_root)
    result_bytes, result = _read_json_object(result_path)
    _validate_hash(result_bytes, _required_string(row, "result_sha256"), f"result: {name}")
    config_path = result_path.parent / "config.json"
    config_bytes, config = _read_json_object(config_path)
    _validate_hash(config_bytes, _required_string(row, "config_sha256"), f"config: {name}")
    _validate_result_identity(row, result, config, name=name)

    task_checksum = _required_string(row, "task_checksum")
    task_git_url = _required_string(row, "task_git_url")
    task_git_commit_id = _required_string(row, "task_git_commit_id")
    split, split_bucket, identity_sha256 = prefixbench_task_split(
        dataset=dataset,
        name=name,
        task_checksum=task_checksum,
        task_git_url=task_git_url,
        task_git_commit_id=task_git_commit_id,
    )
    result_binding = _file_binding(result_path, result_bytes, project_root)
    config_binding = _file_binding(config_path, config_bytes, project_root)
    execution_mode = _execution_mode(result, name=name)
    if execution_mode is PrefixBenchExecutionMode.REPLAY:
        return PrefixBenchTaskAssessmentV2(
            index=index,
            name=name,
            split=split,
            split_bucket=split_bucket,
            task_identity_sha256=identity_sha256,
            execution_mode=execution_mode,
            sources=PrefixBenchSources(
                result=result_binding,
                config=config_binding,
                journal=None,
            ),
            journal_schema_version=None,
            producer=None,
            source_admission=PrefixBenchSourceAdmission(
                admitted=False,
                exclusion_reasons=(PrefixBenchSourceExclusion.NON_LIVE_RESULT,),
            ),
            phase_eligibility=PrefixBenchPhaseEligibility(),
            legacy_events=PrefixBenchLegacyCounts(),
        )

    journal_path = result_path.parent / "agent" / "evidence-harness" / "events.jsonl"
    journal_bytes = journal_path.read_bytes()
    events, started_payload = _parse_journal(journal_bytes, journal_path)
    journal_schema_version = _journal_schema(started_payload, journal_path)
    journal_binding = _file_binding(journal_path, journal_bytes, project_root)
    exclusions: list[PrefixBenchSourceExclusion] = []
    if not _journal_is_canonically_bound(row, journal_binding):
        exclusions.append(PrefixBenchSourceExclusion.JOURNAL_NOT_BOUND_BY_CANONICAL)
    if journal_schema_version != 2:
        exclusions.append(PrefixBenchSourceExclusion.UNSUPPORTED_JOURNAL_SCHEMA)
    if (
        row.get("prefixbench_profile") != PREFIXBENCH_PROFILE
        or started_payload.get("prefixbench_profile") != PREFIXBENCH_PROFILE
    ):
        exclusions.append(PrefixBenchSourceExclusion.PREFIXBENCH_PROFILE_MISSING)

    producer = _row_producer(row, name=name)
    if producer is None or started_payload.get("producer") != producer.model_dump(mode="json"):
        exclusions.append(PrefixBenchSourceExclusion.PRODUCER_COMMIT_UNATTESTED)

    expected_options = dict(PREFIXBENCH_V1.controlled_agent_options)
    started_options = started_payload.get("options")
    agent = _optional_object(config.get("agent"))
    agent_options = _optional_object(agent.get("kwargs"))
    profile_options_match = (
        started_options == expected_options
        and _optional_object(result.get("config")) == config
        and agent.get("name") == "evidence_harness.harbor_agent:EvidenceHarnessAgent"
    )
    try:
        PREFIXBENCH_V1.validate_effective_options(agent_options)
    except ValueError:
        profile_options_match = False
    expected_provenance = {
        "prefixbench_profile": PREFIXBENCH_PROFILE,
        "producer_commit": producer.commit if producer is not None else None,
        "producer_tree": producer.tree if producer is not None else None,
        "producer_source_sha256": producer.source_sha256 if producer is not None else None,
    }
    if any(agent_options.get(key) != value for key, value in expected_provenance.items()):
        profile_options_match = False
    if not profile_options_match:
        exclusions.append(PrefixBenchSourceExclusion.PROFILE_OPTIONS_MISMATCH)

    admitted = not exclusions
    if admitted:
        phase_eligibility, phase_errors = validate_phase_trace(events)
    else:
        phase_eligibility = PrefixBenchPhaseEligibility()
        phase_errors = ()
    return PrefixBenchTaskAssessmentV2(
        index=index,
        name=name,
        split=split,
        split_bucket=split_bucket,
        task_identity_sha256=identity_sha256,
        execution_mode=execution_mode,
        sources=PrefixBenchSources(
            result=result_binding,
            config=config_binding,
            journal=journal_binding,
        ),
        journal_schema_version=journal_schema_version,
        producer=producer,
        source_admission=PrefixBenchSourceAdmission(
            admitted=admitted,
            exclusion_reasons=tuple(exclusions),
        ),
        phase_eligibility=phase_eligibility,
        phase_errors=phase_errors,
        legacy_events=_legacy_counts(events),
    )


def validate_phase_trace(
    events: list[dict[str, Any]],
) -> tuple[PrefixBenchPhaseEligibility, tuple[str, ...]]:
    errors: list[str] = []
    witnesses: dict[str, list[int]] = {phase: [] for phase in PrefixBenchPhaseCounts.model_fields}
    finished_lines = [event["line"] for event in events if event["type"] == "run_finished"]
    if len(finished_lines) != 1 or finished_lines[0] != len(events):
        errors.append("journal must contain one trailing run_finished event")
    terminal_line = finished_lines[0] if len(finished_lines) == 1 else len(events) + 1

    finalization_line: int | None = None
    finalization_count = 0
    executor_attempt = 0
    work_epoch = 0
    review_ordinal = 0
    review_attempt = 0
    recovery_ordinal = 0
    recovery_open = False

    for offset, event in enumerate(events):
        line = int(event["line"])
        event_type = event["type"]
        payload = event["payload"]
        if line == 1 or line >= terminal_line:
            continue
        try:
            if event_type == "executor_turn_started":
                executor_entry = ExecutorTurnStarted.model_validate(payload)
                valid = executor_entry.attempt_id > executor_attempt and finalization_line is None
                if not _has_executor_outcome(events, offset):
                    valid = False
                    errors.append(f"line {line} executor turn has no model outcome")
                if executor_entry.attempt_id <= executor_attempt:
                    errors.append(f"line {line} executor attempt id did not increase")
                if finalization_line is not None:
                    errors.append(f"line {line} thinking entry occurs after finalization")
                executor_attempt = max(executor_attempt, executor_entry.attempt_id)
                if valid:
                    witnesses["thinking"].append(line)
            elif event_type == "work_batch_started":
                work_entry = WorkBatchStarted.model_validate(payload)
                valid = work_entry.work_epoch > work_epoch
                if work_entry.work_epoch <= work_epoch:
                    errors.append(f"line {line} work epoch did not increase")
                if not _has_work_outcome(events, offset, work_entry):
                    valid = False
                    errors.append(f"line {line} work batch has no command or policy outcome")
                work_epoch = max(work_epoch, work_entry.work_epoch)
                if valid:
                    witnesses["executing"].append(line)
            elif event_type == "finalization_started":
                FinalizationStarted.model_validate(payload)
                finalization_count += 1
                if finalization_count == 1:
                    finalization_line = line
                    recovery_open = False
                    witnesses["finalizing"].append(line)
                else:
                    errors.append(f"line {line} duplicates finalization entry")
            elif event_type == "completion_review_started":
                review_entry = CompletionReviewStarted.model_validate(payload)
                valid = (
                    review_entry.review_ordinal > review_ordinal
                    and review_entry.attempt_id > review_attempt
                )
                if review_entry.review_ordinal <= review_ordinal:
                    errors.append(f"line {line} review ordinal did not increase")
                if review_entry.attempt_id <= review_attempt:
                    errors.append(f"line {line} review attempt id did not increase")
                if not _has_review_outcome(events, offset):
                    valid = False
                    errors.append(f"line {line} completion review has no outcome")
                review_ordinal = max(review_ordinal, review_entry.review_ordinal)
                review_attempt = max(review_attempt, review_entry.attempt_id)
                if valid:
                    witnesses["reviewing"].append(line)
            elif event_type == "recovery_required":
                recovery_entry = RecoveryRequired.model_validate(payload)
                valid = recovery_entry.recovery_ordinal > recovery_ordinal and not recovery_open
                if recovery_entry.recovery_ordinal <= recovery_ordinal:
                    errors.append(f"line {line} recovery ordinal did not increase")
                if recovery_open:
                    errors.append(f"line {line} duplicates an open recovery episode")
                recovery_ordinal = max(
                    recovery_ordinal,
                    recovery_entry.recovery_ordinal,
                )
                recovery_open = True
                if valid:
                    witnesses["recovering"].append(line)
            elif event_type == "replanned":
                recovery_open = False
        except ValidationError as exc:
            errors.append(f"line {line} invalid {event_type}: {exc.errors()[0]['msg']}")

    return (
        PrefixBenchPhaseEligibility(**{phase: tuple(lines) for phase, lines in witnesses.items()}),
        tuple(errors),
    )


def _has_executor_outcome(events: list[dict[str, Any]], offset: int) -> bool:
    outcomes = {
        "agent_decision",
        "model_protocol_error",
        "model_service_error",
        "model_error",
        "model_decision_interrupted_for_finalization",
    }
    return _has_following_event(
        events,
        offset,
        outcomes=outcomes,
        boundaries={"executor_turn_started", "run_finished"},
    )


def _has_work_outcome(
    events: list[dict[str, Any]],
    offset: int,
    entry: WorkBatchStarted,
) -> bool:
    for event in events[offset + 1 :]:
        if event["type"] in {"work_batch_started", "run_finished"}:
            return False
        if (
            event["type"] == "command_receipt"
            and event["payload"].get("work_epoch") == entry.work_epoch
        ):
            return True
        if (
            event["type"] == "policy_rejection"
            and event["payload"].get("command_id") in entry.command_ids
        ):
            return True
        if event["type"] == "work_batch_interrupted_for_finalization":
            return True
    return False


def _has_review_outcome(events: list[dict[str, Any]], offset: int) -> bool:
    return _has_following_event(
        events,
        offset,
        outcomes={"completion_review", "completion_review_error"},
        boundaries={"completion_review_started", "run_finished"},
    )


def _has_following_event(
    events: list[dict[str, Any]],
    offset: int,
    *,
    outcomes: set[str],
    boundaries: set[str],
) -> bool:
    for event in events[offset + 1 :]:
        if event["type"] in outcomes:
            return True
        if event["type"] in boundaries:
            return False
    return False


def _legacy_counts(events: list[dict[str, Any]]) -> PrefixBenchLegacyCounts:
    return PrefixBenchLegacyCounts(
        finish_proposals=sum(
            event["type"] == "agent_decision" and event["payload"].get("action") == "finish"
            for event in events
        ),
        **{
            field: sum(event["type"] == event_type for event in events)
            for field, event_type in _LEGACY_EVENT_TYPES.items()
        },
    )


def _row_producer(
    row: dict[str, Any],
    *,
    name: str,
) -> ProducerAttestation | None:
    values = {
        "commit": row.get("producer_commit"),
        "tree": row.get("producer_tree"),
        "source_sha256": row.get("producer_source_sha256"),
    }
    if not any(value is not None for value in values.values()):
        return None
    try:
        return ProducerAttestation.model_validate(values)
    except ValidationError as exc:
        raise ValueError(f"invalid producer attestation: {name}") from exc


def _prefixbench_v2_status(
    summary: PrefixBenchSummaryV2,
    coverage_policy: PrefixBenchCoveragePolicy,
) -> PrefixBenchStatus:
    if summary.source_admitted == 0:
        return PrefixBenchStatus.SOURCE_COHORT_UNAVAILABLE
    if not coverage_policy.accepts(summary.phase_coverage):
        return PrefixBenchStatus.PHASE_COVERAGE_INCOMPLETE
    return PrefixBenchStatus.READY


def _validate_result_identity(
    row: dict[str, Any],
    result: dict[str, Any],
    config: dict[str, Any],
    *,
    name: str,
) -> None:
    if _task_name(result) != name:
        raise ValueError(f"canonical result task does not match: {name}")
    result_config = _object(result.get("config"), "result config")
    for section, field in (
        ("agent", "name"),
        ("task", "path"),
        ("task", "git_url"),
        ("task", "git_commit_id"),
    ):
        embedded = _object(result_config.get(section), f"result config {section}").get(field)
        external = _object(config.get(section), f"config {section}").get(field)
        if embedded != external:
            raise ValueError(f"result config does not match config.json: {name}")
    for field in ("job_id", "trial_name"):
        if result_config.get(field) != config.get(field):
            raise ValueError(f"result config does not match config.json: {name}")

    task_id = _optional_object(result.get("task_id"))
    task_config = _object(result_config.get("task"), "result task config")
    expected_identity = {
        "task_checksum": result.get("task_checksum"),
        "task_git_url": task_id.get("git_url") or task_config.get("git_url"),
        "task_git_commit_id": task_id.get("git_commit_id") or task_config.get("git_commit_id"),
    }
    for field, actual in expected_identity.items():
        if row.get(field) != actual:
            raise ValueError(f"canonical {field} does not match result: {name}")

    reward = _reward(result)
    if row.get("reward") != reward:
        raise ValueError(f"canonical reward does not match result: {name}")
    if row.get("status") != _result_status(result, reward):
        raise ValueError(f"canonical status does not match result: {name}")
    if row.get("exception_type") != _exception_type(result):
        raise ValueError(f"canonical exception type does not match result: {name}")


def _execution_mode(
    result: dict[str, Any],
    *,
    name: str,
) -> PrefixBenchExecutionMode:
    agent_result = _object(result.get("agent_result"), "agent_result")
    metadata = _object(agent_result.get("metadata"), "agent result metadata")
    harness = _optional_object(metadata.get("evidence_harness"))
    replay = _optional_object(metadata.get("evidence_harness_replay"))
    stop_reason = harness.get("stop_reason")
    if replay and not stop_reason:
        return PrefixBenchExecutionMode.REPLAY
    if isinstance(stop_reason, str) and stop_reason:
        return PrefixBenchExecutionMode.LIVE
    raise ValueError(f"result is neither a live nor replay Harness run: {name}")


def _parse_journal(
    data: bytes,
    path: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(data.splitlines(keepends=True), start=1):
        if not raw_line.strip():
            raise ValueError(f"blank journal line: {path}:{line_number}")
        try:
            value = json.loads(raw_line)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid journal event: {path}:{line_number}") from exc
        event = _object(value, f"journal event {path}:{line_number}")
        _required_string(event, "timestamp")
        event_type = _required_string(event, "type")
        payload = _object(event.get("payload"), f"journal payload {path}:{line_number}")
        events.append({"line": line_number, "type": event_type, "payload": payload})
    if not events:
        raise ValueError(f"journal is empty: {path}")
    starts = [event for event in events if event["type"] == "run_started"]
    if len(starts) != 1 or events[0]["type"] != "run_started":
        raise ValueError(f"journal must contain one leading run_started event: {path}")
    return events, starts[0]["payload"]


def _journal_schema(payload: dict[str, Any], path: Path) -> int:
    version = payload.get("journal_schema_version", 1)
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ValueError(f"invalid journal schema version: {path}")
    return version


def _journal_is_canonically_bound(
    row: dict[str, Any],
    actual: PrefixBenchFileBinding,
) -> bool:
    path = row.get("journal_path")
    digest = row.get("journal_sha256")
    if path is None and digest is None:
        return False
    if path != actual.path or digest != actual.sha256:
        raise ValueError(f"canonical journal binding does not match: {row.get('name')}")
    return True


def _producer_is_attested(
    row: dict[str, Any],
    started_payload: dict[str, Any],
) -> bool:
    expected = {
        "commit": row.get("producer_commit"),
        "tree": row.get("producer_tree"),
        "source_sha256": row.get("producer_source_sha256"),
    }
    if not all(isinstance(value, str) and value for value in expected.values()):
        return False
    commit = expected["commit"]
    tree = expected["tree"]
    source_sha256 = expected["source_sha256"]
    assert isinstance(commit, str)
    assert isinstance(tree, str)
    assert isinstance(source_sha256, str)
    if (
        len(commit) not in _SOURCE_COMMIT_PATTERN
        or len(tree) not in _SOURCE_COMMIT_PATTERN
        or not _is_lower_hex(commit)
        or not _is_lower_hex(tree)
        or len(source_sha256) != 64
        or not _is_lower_hex(source_sha256)
    ):
        raise ValueError(f"invalid producer attestation: {row.get('name')}")
    return started_payload.get("producer") == expected


def _resolve_result_path(row: dict[str, Any], project_root: Path) -> Path:
    raw = _required_string(row, "result_path")
    relative = Path(raw)
    if relative.is_absolute():
        raise ValueError(f"canonical result path is absolute: {raw}")
    if relative.parts[:2] != ("runs", "terminal-bench-2"):
        raise ValueError(f"canonical result path is outside the run store: {raw}")
    resolved = _resolve_relative_path(relative, project_root)
    run_dir = Path(_required_string(row, "run_dir"))
    if run_dir.is_absolute() or not relative.is_relative_to(run_dir):
        raise ValueError(f"canonical run directory does not match result path: {raw}")
    return resolved


def _resolve_bound_path(binding: PrefixBenchFileBinding, project_root: Path) -> Path:
    relative = Path(binding.path)
    if relative.is_absolute():
        raise ValueError(f"bound source path is absolute: {binding.path}")
    if relative.parts[:2] != ("runs", "terminal-bench-2"):
        raise ValueError(f"bound source path is outside the run store: {binding.path}")
    return _resolve_relative_path(relative, project_root)


def _resolve_relative_path(relative: Path, project_root: Path) -> Path:
    resolved = (project_root / relative).resolve()
    if not resolved.is_relative_to(project_root):
        raise ValueError(f"source path escapes the project: {relative}")
    return resolved


def _file_binding(
    path: Path,
    data: bytes,
    project_root: Path,
) -> PrefixBenchFileBinding:
    resolved = path.resolve()
    if not resolved.is_relative_to(project_root):
        raise ValueError(f"source path is outside the project: {path}")
    return PrefixBenchFileBinding(
        path=str(resolved.relative_to(project_root)),
        bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def _validate_file_binding(
    binding: PrefixBenchFileBinding,
    path: Path,
    data: bytes,
    project_root: Path,
) -> None:
    if binding != _file_binding(path, data, project_root):
        raise ValueError(f"source binding is stale: {binding.path}")


def _validate_hash(data: bytes, expected: str, label: str) -> None:
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise ValueError(f"canonical {label} hash does not match")


def _read_json_object(path: Path) -> tuple[bytes, dict[str, Any]]:
    data = path.read_bytes()
    return data, _object(json.loads(data), str(path))


def _task_name(result: dict[str, Any]) -> str | None:
    config_task = _optional_object(_optional_object(result.get("config")).get("task"))
    task_id = _optional_object(result.get("task_id"))
    for value in (config_task.get("name"), task_id.get("name"), result.get("task_name")):
        if isinstance(value, str) and value:
            return value.rsplit("/", 1)[-1]
    path = config_task.get("path")
    return Path(path).name if isinstance(path, str) and path else None


def _reward(result: dict[str, Any]) -> float:
    verifier = _object(result.get("verifier_result"), "verifier_result")
    rewards = _object(verifier.get("rewards"), "verifier rewards")
    reward = rewards.get("reward")
    if not isinstance(reward, int | float) or isinstance(reward, bool):
        raise ValueError("verifier reward is missing or non-numeric")
    return float(reward)


def _exception_type(result: dict[str, Any]) -> str | None:
    exception = _optional_object(result.get("exception_info"))
    value = exception.get("exception_type") or exception.get("type")
    if value is not None and not isinstance(value, str):
        raise ValueError("result exception type must be a string or null")
    return value


def _result_status(result: dict[str, Any], reward: float) -> str:
    if _exception_type(result):
        return "error"
    return "passed" if reward == 1.0 else "failed"


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _optional_object(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: dict[str, Any], key: str) -> list[Any]:
    items = value.get(key)
    if not isinstance(items, list):
        raise ValueError(f"{key} must be a list")
    return items


def _required_string(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ValueError(f"{key} must be a non-empty string")
    return item


def _required_int(value: dict[str, Any], key: str) -> int:
    item = value.get(key)
    if not isinstance(item, int) or isinstance(item, bool):
        raise ValueError(f"{key} must be an integer")
    return item


def _is_lower_hex(value: str) -> bool:
    return all(character in "0123456789abcdef" for character in value)
