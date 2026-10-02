from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import Field, ValidationError, model_validator

from evidence_harness.evaluation import EvaluationMatrix
from evidence_harness.protocol import ProducerAttestation
from evidence_harness_mutation.attempts import project_completion_attempts
from evidence_harness_mutation.campaign import (
    OfflineCampaignOutcome,
    OfflineCampaignReport,
    OfflineCampaignSummary,
    run_offline_campaign,
)
from evidence_harness_mutation.journal_loader import load_state_prefix
from evidence_harness_mutation.model import FrozenModel, InvariantId, Sha256, TraceStructureError
from evidence_harness_mutation.operators import MutationId, MutationRequest
from evidence_harness_mutation.prefixbench import (
    PrefixBenchExecutionMode,
    PrefixBenchFileBinding,
    PrefixBenchReadinessV2,
    PrefixBenchSplit,
    PrefixBenchStatus,
    PrefixBenchTaskAssessmentV2,
    check_prefixbench_readiness,
    prefixbench_task_split,
)

DEVELOPMENT_READINESS = Path("evaluation/prefixbench-v1-development-readiness.json")
DEVELOPMENT_CANONICAL = Path("evaluation/prefixbench-v1-development-canonical.json")
DEVELOPMENT_MATRIX = Path("evaluation/matrix-prefixbench-development.json")
DEVELOPMENT_PROTOCOL = Path("experiments/prefixbench-v1/mutation-protocol-v1.json")
DEVELOPMENT_CAMPAIGN = Path("evaluation/prefixbench-v1-development-offline-campaign.json")
DEVELOPMENT_TASKS = 28

_PROTOCOL_SOURCE_PATHS = (
    "pyproject.toml",
    "uv.lock",
    "src/evidence_harness/collection_profile.py",
    "src/evidence_harness/evaluation.py",
    "src/evidence_harness/protocol.py",
    "src/evidence_harness_mutation/attempts.py",
    "src/evidence_harness_mutation/campaign.py",
    "src/evidence_harness_mutation/invariants.py",
    "src/evidence_harness_mutation/journal_loader.py",
    "src/evidence_harness_mutation/model.py",
    "src/evidence_harness_mutation/operators.py",
    "src/evidence_harness_mutation/prefixbench.py",
    "src/evidence_harness_mutation/prefixbench_campaign.py",
    "src/evidence_harness_mutation/reducer.py",
)
_OPERATOR_INVARIANTS = (
    (MutationId.STALE_EVIDENCE_EPOCH, InvariantId.I1),
    (MutationId.REORDER_CHECK_RECEIPTS, InvariantId.I2),
    (MutationId.REVIEW_TIMEOUT_FALLBACK, InvariantId.I3),
    (MutationId.CROSS_CANDIDATE_EVIDENCE, InvariantId.I4),
)
_PROTOCOL_COMPONENTS = (
    (
        "source_admission",
        "evidence_harness_mutation.prefixbench:check_prefixbench_readiness",
        (
            "src/evidence_harness/collection_profile.py",
            "src/evidence_harness/evaluation.py",
            "src/evidence_harness/protocol.py",
            "src/evidence_harness_mutation/model.py",
            "src/evidence_harness_mutation/prefixbench.py",
        ),
    ),
    (
        "journal_loading",
        "evidence_harness_mutation.journal_loader:load_state_prefix",
        (
            "src/evidence_harness/protocol.py",
            "src/evidence_harness_mutation/journal_loader.py",
            "src/evidence_harness_mutation/model.py",
        ),
    ),
    (
        "attempt_projection",
        "evidence_harness_mutation.attempts:project_completion_attempts",
        (
            "src/evidence_harness/protocol.py",
            "src/evidence_harness_mutation/attempts.py",
            "src/evidence_harness_mutation/model.py",
        ),
    ),
    (
        "operators",
        "evidence_harness_mutation.operators:apply_mutation",
        (
            "src/evidence_harness/protocol.py",
            "src/evidence_harness_mutation/attempts.py",
            "src/evidence_harness_mutation/model.py",
            "src/evidence_harness_mutation/operators.py",
        ),
    ),
    (
        "oracle",
        "evidence_harness_mutation.invariants:audit_completion_trace",
        (
            "src/evidence_harness/protocol.py",
            "src/evidence_harness_mutation/attempts.py",
            "src/evidence_harness_mutation/invariants.py",
            "src/evidence_harness_mutation/model.py",
        ),
    ),
    (
        "reducer",
        "evidence_harness_mutation.reducer:reduce_counterexample",
        (
            "src/evidence_harness/protocol.py",
            "src/evidence_harness_mutation/attempts.py",
            "src/evidence_harness_mutation/invariants.py",
            "src/evidence_harness_mutation/model.py",
            "src/evidence_harness_mutation/operators.py",
            "src/evidence_harness_mutation/reducer.py",
        ),
    ),
    (
        "single_prefix_campaign",
        "evidence_harness_mutation.campaign:run_offline_campaign",
        (
            "src/evidence_harness/protocol.py",
            "src/evidence_harness_mutation/attempts.py",
            "src/evidence_harness_mutation/campaign.py",
            "src/evidence_harness_mutation/invariants.py",
            "src/evidence_harness_mutation/model.py",
            "src/evidence_harness_mutation/operators.py",
            "src/evidence_harness_mutation/reducer.py",
        ),
    ),
    (
        "cohort_campaign",
        ("evidence_harness_mutation.prefixbench_campaign:build_prefixbench_development_campaign"),
        _PROTOCOL_SOURCE_PATHS,
    ),
)


class PrefixBenchCampaignSources(FrozenModel):
    readiness: PrefixBenchFileBinding
    canonical: PrefixBenchFileBinding
    matrix: PrefixBenchFileBinding

    @model_validator(mode="after")
    def validate_paths(self) -> Self:
        expected = (
            DEVELOPMENT_READINESS.as_posix(),
            DEVELOPMENT_CANONICAL.as_posix(),
            DEVELOPMENT_MATRIX.as_posix(),
        )
        actual = (
            self.readiness.path,
            self.canonical.path,
            self.matrix.path,
        )
        if actual != expected:
            raise ValueError("PrefixBench campaign input paths are not the frozen development set")
        return self


class PrefixBenchCanonicalJsonProtocol(FrozenModel):
    ensure_ascii: Literal[True] = True
    indent: Literal[2] = 2
    sort_keys: Literal[True] = True
    trailing_newline: Literal[True] = True


class PrefixBenchOperatorProtocol(FrozenModel):
    operator: MutationId
    expected_invariant: InvariantId


class PrefixBenchProtocolComponent(FrozenModel):
    name: str = Field(min_length=1)
    entrypoint: str = Field(min_length=1)
    source_paths: tuple[str, ...] = Field(min_length=1)


class PrefixBenchProtocolSourceSet(FrozenModel):
    algorithm: Literal["sha256-length-framed-path-content-v1"] = (
        "sha256-length-framed-path-content-v1"
    )
    bytes: int = Field(ge=1)
    sha256: Sha256
    files: tuple[PrefixBenchFileBinding, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_files(self) -> Self:
        paths = tuple(file.path for file in self.files)
        if paths != _PROTOCOL_SOURCE_PATHS:
            raise ValueError("PrefixBench protocol source paths have changed")
        if len(paths) != len(set(paths)):
            raise ValueError("PrefixBench protocol source paths must be unique")
        if self.bytes != sum(file.bytes for file in self.files):
            raise ValueError("PrefixBench protocol source byte count is stale")
        return self


class PrefixBenchMutationProtocol(FrozenModel):
    schema_version: Literal[1] = 1
    protocol_id: Literal["prefixbench-v1-development-offline-mutation-v1"] = (
        "prefixbench-v1-development-offline-mutation-v1"
    )
    cohort: Literal["prefixbench-v1-development"] = "prefixbench-v1-development"
    task_count: Literal[28] = 28
    prefix_policy: Literal["one-full-journal-state-prefix-per-admitted-task"] = (
        "one-full-journal-state-prefix-per-admitted-task"
    )
    phase_witness_policy: Literal["readiness-metadata-only-not-campaign-cutoffs"] = (
        "readiness-metadata-only-not-campaign-cutoffs"
    )
    task_order: Literal["readiness-task-order"] = "readiness-task-order"
    execution: Literal["sequential"] = "sequential"
    schedule: Literal["run-offline-campaign-default"] = "run-offline-campaign-default"
    operators: tuple[PrefixBenchOperatorProtocol, ...] = Field(min_length=4, max_length=4)
    outcomes: tuple[OfflineCampaignOutcome, ...] = Field(min_length=5, max_length=5)
    campaign_audit_runs: Literal[3] = 3
    baseline_rule: Literal["credit-only-new-expected-violation-at-attempt-terminal"] = (
        "credit-only-new-expected-violation-at-attempt-terminal"
    )
    reducer_algorithm: Literal["source-anchored-ddmin-v1"] = "source-anchored-ddmin-v1"
    reducer_minimality: Literal["1-minimal-under-declared-removals"] = (
        "1-minimal-under-declared-removals"
    )
    reducer_audit_runs: Literal[3] = 3
    counterexamples: Literal["inline-for-every-offline-violation"] = (
        "inline-for-every-offline-violation"
    )
    canonical_json: PrefixBenchCanonicalJsonProtocol
    source_set: PrefixBenchProtocolSourceSet
    components: tuple[PrefixBenchProtocolComponent, ...] = Field(min_length=8, max_length=8)

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        expected_operators = tuple(
            PrefixBenchOperatorProtocol(operator=operator, expected_invariant=invariant)
            for operator, invariant in _OPERATOR_INVARIANTS
        )
        if self.operators != expected_operators:
            raise ValueError("PrefixBench operator protocol has changed")
        if self.outcomes != tuple(OfflineCampaignOutcome):
            raise ValueError("PrefixBench campaign outcome order has changed")
        expected_components = tuple(
            PrefixBenchProtocolComponent(
                name=name,
                entrypoint=entrypoint,
                source_paths=source_paths,
            )
            for name, entrypoint, source_paths in _PROTOCOL_COMPONENTS
        )
        if self.components != expected_components:
            raise ValueError("PrefixBench protocol component bindings have changed")
        return self

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.model_dump(mode="json"))


class BoundPrefixBenchMutationProtocol(FrozenModel):
    file: PrefixBenchFileBinding
    spec: PrefixBenchMutationProtocol

    @model_validator(mode="after")
    def validate_path(self) -> Self:
        if self.file.path != DEVELOPMENT_PROTOCOL.as_posix():
            raise ValueError("PrefixBench mutation protocol path has changed")
        return self


class PrefixBenchOperatorSummary(FrozenModel):
    operator: MutationId
    expected_invariant: InvariantId
    outcomes: OfflineCampaignSummary


class PrefixBenchCampaignTask(FrozenModel):
    index: int = Field(ge=1, le=DEVELOPMENT_TASKS)
    name: str = Field(min_length=1)
    task_identity_sha256: Sha256
    journal: PrefixBenchFileBinding
    journal_lines: int = Field(ge=1)
    projected_attempts: int = Field(ge=0)
    campaign: OfflineCampaignReport

    @model_validator(mode="after")
    def validate_campaign(self) -> Self:
        if self.campaign.source.journal_sha256 != self.journal.sha256:
            raise ValueError("task campaign does not match its journal hash")
        if self.campaign.source.journal_schema_version != 2:
            raise ValueError("task campaign must use a schema-2 journal")
        if self.campaign.through_line != self.journal_lines:
            raise ValueError("task campaign must use the complete journal")
        attempt_ordinals = (
            tuple(range(1, self.projected_attempts + 1)) if self.projected_attempts else (1,)
        )
        expected_requests = tuple(
            MutationRequest(operator=operator, attempt_ordinal=attempt_ordinal)
            for attempt_ordinal in attempt_ordinals
            for operator in MutationId
        )
        actual_requests = tuple(case.request for case in self.campaign.cases)
        if actual_requests != expected_requests:
            raise ValueError("task campaign does not contain the exact default request schedule")
        if (
            self.campaign.baseline is not None
            and self.campaign.baseline.verified_attempts > self.projected_attempts
        ):
            raise ValueError("verified attempt count exceeds projected attempt count")
        return self


class PrefixBenchCampaignSummary(FrozenModel):
    tasks: Literal[28] = 28
    tasks_with_attempts: int = Field(ge=0)
    tasks_with_verified_attempts: int = Field(ge=0)
    projected_attempts: int = Field(ge=0)
    verified_attempts: int = Field(ge=0)
    outcomes: OfflineCampaignSummary
    operators: tuple[PrefixBenchOperatorSummary, ...] = Field(min_length=4, max_length=4)

    @classmethod
    def from_tasks(cls, tasks: tuple[PrefixBenchCampaignTask, ...]) -> Self:
        if len(tasks) != DEVELOPMENT_TASKS:
            raise ValueError("PrefixBench campaign summary requires 28 tasks")
        all_cases = tuple(case for task in tasks for case in task.campaign.cases)
        operator_summaries = tuple(
            PrefixBenchOperatorSummary(
                operator=operator,
                expected_invariant=invariant,
                outcomes=OfflineCampaignSummary.from_cases(
                    tuple(case for case in all_cases if case.request.operator is operator)
                ),
            )
            for operator, invariant in _OPERATOR_INVARIANTS
        )
        verified = tuple(
            task.campaign.baseline.verified_attempts if task.campaign.baseline is not None else 0
            for task in tasks
        )
        return cls(
            tasks=28,
            tasks_with_attempts=sum(task.projected_attempts > 0 for task in tasks),
            tasks_with_verified_attempts=sum(count > 0 for count in verified),
            projected_attempts=sum(task.projected_attempts for task in tasks),
            verified_attempts=sum(verified),
            outcomes=OfflineCampaignSummary.from_cases(all_cases),
            operators=operator_summaries,
        )


class PrefixBenchDevelopmentCampaignReport(FrozenModel):
    schema_version: Literal[1] = 1
    benchmark: Literal["PrefixBench"] = "PrefixBench"
    dataset: Literal["terminal-bench@2.0"] = "terminal-bench@2.0"
    split: Literal["development"] = "development"
    campaign: Literal["completion-offline-mutation-v1"] = "completion-offline-mutation-v1"
    claim_boundary: Literal[
        "development-completion-offline-only-no-test-or-production-execution"
    ] = "development-completion-offline-only-no-test-or-production-execution"
    sources: PrefixBenchCampaignSources
    producer: ProducerAttestation
    protocol: BoundPrefixBenchMutationProtocol
    tasks: tuple[PrefixBenchCampaignTask, ...] = Field(
        min_length=DEVELOPMENT_TASKS,
        max_length=DEVELOPMENT_TASKS,
    )
    summary: PrefixBenchCampaignSummary

    @model_validator(mode="after")
    def validate_report(self) -> Self:
        if tuple(task.index for task in self.tasks) != tuple(range(1, DEVELOPMENT_TASKS + 1)):
            raise ValueError("PrefixBench campaign task indices must be contiguous")
        if len({task.name for task in self.tasks}) != len(self.tasks):
            raise ValueError("PrefixBench campaign task names must be unique")
        if len({task.journal.path for task in self.tasks}) != len(self.tasks):
            raise ValueError("PrefixBench campaign journal paths must be unique")
        if any(task.campaign.source.source_commit != self.producer.commit for task in self.tasks):
            raise ValueError("PrefixBench campaign tasks must use the cohort producer")
        if self.summary != PrefixBenchCampaignSummary.from_tasks(self.tasks):
            raise ValueError("PrefixBench campaign summary does not match its task reports")
        return self

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.model_dump(mode="json"))


@dataclass(frozen=True)
class _DevelopmentCohort:
    root: Path
    sources: PrefixBenchCampaignSources
    readiness: PrefixBenchReadinessV2
    producer: ProducerAttestation


def build_prefixbench_development_campaign(
    project_root: Path,
) -> PrefixBenchDevelopmentCampaignReport:
    cohort = _load_development_inputs(project_root)
    protocol = _load_bound_protocol(cohort.root)
    tasks = tuple(
        _run_task(task, producer=cohort.producer, project_root=cohort.root)
        for task in cohort.readiness.tasks
    )
    return PrefixBenchDevelopmentCampaignReport(
        sources=cohort.sources,
        producer=cohort.producer,
        protocol=protocol,
        tasks=tasks,
        summary=PrefixBenchCampaignSummary.from_tasks(tasks),
    )


def check_prefixbench_development_campaign(
    project_root: Path,
    *,
    report_path: Path | None = None,
) -> tuple[str, ...]:
    root = project_root.resolve()
    selected_report = root / DEVELOPMENT_CAMPAIGN if report_path is None else report_path.resolve()
    try:
        _require_project_path(selected_report, root, label="campaign report")
        report_bytes = _read_regular_file(selected_report, label="campaign report")
        report = PrefixBenchDevelopmentCampaignReport.model_validate_json(report_bytes)
    except (OSError, ValueError) as exc:
        return (f"invalid PrefixBench campaign artifact: {exc}",)

    errors: list[str] = []
    if report_bytes != report.canonical_bytes():
        errors.append("PrefixBench campaign artifact is not canonical JSON")

    try:
        cohort = _load_development_inputs(root)
        protocol = _load_bound_protocol(root)
    except (OSError, ValueError) as exc:
        errors.append(f"invalid PrefixBench campaign source: {exc}")
        return tuple(errors)

    if report.sources != cohort.sources:
        errors.append("PrefixBench campaign input bindings are stale")
    if report.producer != cohort.producer:
        errors.append("PrefixBench campaign producer binding is stale")
    if report.protocol != protocol:
        errors.append("PrefixBench campaign protocol binding is stale")
    expected_tasks = tuple(
        (
            task.index,
            task.name,
            task.task_identity_sha256,
            task.sources.journal,
        )
        for task in cohort.readiness.tasks
    )
    actual_tasks = tuple(
        (
            task.index,
            task.name,
            task.task_identity_sha256,
            task.journal,
        )
        for task in report.tasks
    )
    if actual_tasks != expected_tasks:
        errors.append("PrefixBench campaign task bindings are stale")
    if errors:
        return tuple(errors)

    try:
        journal_paths = tuple(_resolve_journal_path(task.journal, root) for task in report.tasks)
    except ValueError as exc:
        return (f"invalid PrefixBench campaign journal binding: {exc}",)
    present = tuple(path.is_file() and not path.is_symlink() for path in journal_paths)
    if any(present) and not all(present):
        return (
            "PrefixBench campaign journals are partially available: "
            f"{sum(present)}/{len(present)} bound files exist",
        )
    if not any(present):
        return ()

    try:
        rebuilt = build_prefixbench_development_campaign(root)
    except (OSError, ValueError) as exc:
        return (f"cannot rebuild PrefixBench campaign artifact: {exc}",)
    if rebuilt.canonical_bytes() != report_bytes:
        return ("PrefixBench campaign artifact is stale",)
    return ()


def _load_development_inputs(project_root: Path) -> _DevelopmentCohort:
    root = project_root.resolve()
    if not root.is_dir():
        raise ValueError(f"project root is not a directory: {root}")

    readiness_path = root / DEVELOPMENT_READINESS
    canonical_path = root / DEVELOPMENT_CANONICAL
    matrix_path = root / DEVELOPMENT_MATRIX
    readiness_bytes = _read_regular_file(readiness_path, label="development readiness")
    canonical_bytes = _read_regular_file(canonical_path, label="development canonical")
    matrix_bytes = _read_regular_file(matrix_path, label="development matrix")
    for relative, data in (
        (DEVELOPMENT_READINESS, readiness_bytes),
        (DEVELOPMENT_CANONICAL, canonical_bytes),
        (DEVELOPMENT_MATRIX, matrix_bytes),
    ):
        _validate_committed_input(root, relative, data)

    try:
        readiness = PrefixBenchReadinessV2.model_validate_json(readiness_bytes)
    except ValidationError as exc:
        raise ValueError("invalid development readiness schema") from exc
    if readiness_bytes != readiness.canonical_bytes():
        raise ValueError("development readiness is not canonical JSON")

    readiness_errors = check_prefixbench_readiness(
        readiness_path=readiness_path,
        canonical_path=canonical_path,
        matrix_path=matrix_path,
        project_root=root,
        expected_task_count=DEVELOPMENT_TASKS,
    )
    if readiness_errors:
        raise ValueError("development readiness failed: " + "; ".join(readiness_errors))

    try:
        canonical = _json_object(canonical_bytes, "development canonical")
        matrix = EvaluationMatrix.model_validate_json(matrix_bytes)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ValueError("invalid development canonical or matrix") from exc

    sources = PrefixBenchCampaignSources(
        readiness=_file_binding(readiness_path, readiness_bytes, root),
        canonical=_file_binding(canonical_path, canonical_bytes, root),
        matrix=_file_binding(matrix_path, matrix_bytes, root),
    )
    _validate_development_cohort(
        readiness=readiness,
        canonical=canonical,
        matrix=matrix,
        sources=sources,
    )
    producers = {
        (
            task.producer.commit,
            task.producer.tree,
            task.producer.source_sha256,
        )
        for task in readiness.tasks
        if task.producer is not None
    }
    if len(producers) != 1:
        raise ValueError("development cohort must contain exactly one producer")
    commit, tree, source_sha256 = next(iter(producers))
    return _DevelopmentCohort(
        root=root,
        sources=sources,
        readiness=readiness,
        producer=ProducerAttestation(
            commit=commit,
            tree=tree,
            source_sha256=source_sha256,
        ),
    )


def _validate_development_cohort(
    *,
    readiness: PrefixBenchReadinessV2,
    canonical: dict[str, Any],
    matrix: EvaluationMatrix,
    sources: PrefixBenchCampaignSources,
) -> None:
    if readiness.status is not PrefixBenchStatus.READY:
        raise ValueError("development readiness status must be ready")
    if readiness.dataset != "terminal-bench@2.0":
        raise ValueError("development readiness dataset has changed")
    summary = readiness.summary
    if (
        summary.tasks,
        summary.source_admitted,
        summary.source_excluded,
        summary.development,
        summary.test,
    ) != (DEVELOPMENT_TASKS, DEVELOPMENT_TASKS, 0, DEVELOPMENT_TASKS, 0):
        raise ValueError("readiness is not the complete 28-task development cohort")
    if readiness.sources["canonical"] != sources.canonical:
        raise ValueError("readiness canonical binding does not match the fixed input")
    if readiness.sources["matrix"] != sources.matrix:
        raise ValueError("readiness matrix binding does not match the fixed input")

    if canonical.get("schema_version") != 2:
        raise ValueError("development canonical must use schema 2")
    if canonical.get("collection_profile") != "prefixbench-v1":
        raise ValueError("development canonical profile has changed")
    if canonical.get("dataset") != readiness.dataset:
        raise ValueError("development canonical dataset does not match readiness")
    if canonical.get("matrix_sha256") != sources.matrix.sha256:
        raise ValueError("development canonical matrix binding is stale")
    if matrix.schema_version != 1 or matrix.dataset != readiness.dataset:
        raise ValueError("development matrix contract has changed")

    rows_value = canonical.get("tasks")
    if not isinstance(rows_value, list) or len(rows_value) != DEVELOPMENT_TASKS:
        raise ValueError("development canonical must contain 28 tasks")
    rows = tuple(_json_row(value, "development canonical task") for value in rows_value)
    if len(matrix.tasks) != DEVELOPMENT_TASKS:
        raise ValueError("development matrix must contain 28 tasks")
    if tuple(task.name for task in matrix.tasks) != tuple(task.name for task in readiness.tasks):
        raise ValueError("development matrix task order does not match readiness")

    for readiness_task, row in zip(readiness.tasks, rows, strict=True):
        _validate_development_task(readiness_task, row, readiness.dataset)


def _validate_development_task(
    task: PrefixBenchTaskAssessmentV2,
    row: dict[str, Any],
    dataset: str,
) -> None:
    if (
        task.execution_mode is not PrefixBenchExecutionMode.LIVE
        or task.split is not PrefixBenchSplit.DEVELOPMENT
        or not task.source_admission.admitted
        or task.journal_schema_version != 2
        or task.sources.journal is None
        or task.producer is None
    ):
        raise ValueError(f"task is not an admitted live development source: {task.name}")
    if _required_int(row, "index") != task.index or _required_string(row, "name") != task.name:
        raise ValueError(f"canonical task identity does not match readiness: {task.name}")
    if row.get("prefixbench_profile") != "prefixbench-v1":
        raise ValueError(f"canonical task profile has changed: {task.name}")
    if (
        _required_string(row, "journal_path") != task.sources.journal.path
        or _required_string(row, "journal_sha256") != task.sources.journal.sha256
    ):
        raise ValueError(f"canonical journal binding does not match readiness: {task.name}")
    row_producer = ProducerAttestation(
        commit=_required_string(row, "producer_commit"),
        tree=_required_string(row, "producer_tree"),
        source_sha256=_required_string(row, "producer_source_sha256"),
    )
    if row_producer != task.producer:
        raise ValueError(f"canonical producer does not match readiness: {task.name}")
    split, bucket, identity = prefixbench_task_split(
        dataset=dataset,
        name=task.name,
        task_checksum=_required_string(row, "task_checksum"),
        task_git_url=_required_string(row, "task_git_url"),
        task_git_commit_id=_required_string(row, "task_git_commit_id"),
    )
    if (
        split is not PrefixBenchSplit.DEVELOPMENT
        or bucket != task.split_bucket
        or identity != task.task_identity_sha256
    ):
        raise ValueError(f"canonical task split identity does not match readiness: {task.name}")


def _run_task(
    task: PrefixBenchTaskAssessmentV2,
    *,
    producer: ProducerAttestation,
    project_root: Path,
) -> PrefixBenchCampaignTask:
    journal = task.sources.journal
    if journal is None:
        raise ValueError(f"development task has no journal binding: {task.name}")
    path = _resolve_journal_path(journal, project_root)
    data = _read_regular_file(path, label=f"journal for {task.name}")
    if len(data) != journal.bytes or hashlib.sha256(data).hexdigest() != journal.sha256:
        raise ValueError(f"journal binding is stale: {task.name}")
    prefix = load_state_prefix(
        data,
        source_commit=producer.commit,
        expected_journal_sha256=journal.sha256,
    )
    if prefix.through_line != len(prefix.events):
        raise AssertionError("full-journal prefix did not retain every event")
    try:
        projected_attempts = len(project_completion_attempts(prefix.events))
    except TraceStructureError:
        projected_attempts = 0
    campaign = run_offline_campaign(prefix)
    return PrefixBenchCampaignTask(
        index=task.index,
        name=task.name,
        task_identity_sha256=task.task_identity_sha256,
        journal=journal,
        journal_lines=len(prefix.events),
        projected_attempts=projected_attempts,
        campaign=campaign,
    )


def _load_bound_protocol(project_root: Path) -> BoundPrefixBenchMutationProtocol:
    path = project_root / DEVELOPMENT_PROTOCOL
    data = _read_regular_file(path, label="mutation protocol")
    try:
        spec = PrefixBenchMutationProtocol.model_validate_json(data)
    except ValidationError as exc:
        raise ValueError("invalid PrefixBench mutation protocol") from exc
    if data != spec.canonical_bytes():
        raise ValueError("PrefixBench mutation protocol is not canonical JSON")
    expected = _current_protocol_manifest(project_root)
    if spec != expected:
        raise ValueError("PrefixBench mutation protocol source binding is stale")
    return BoundPrefixBenchMutationProtocol(
        file=_file_binding(path, data, project_root),
        spec=spec,
    )


def _current_protocol_manifest(project_root: Path) -> PrefixBenchMutationProtocol:
    files: list[PrefixBenchFileBinding] = []
    digest = hashlib.sha256()
    total_bytes = 0
    for relative in _PROTOCOL_SOURCE_PATHS:
        path = project_root / relative
        data = _read_regular_file(path, label=f"protocol source {relative}")
        encoded_path = relative.encode()
        digest.update(len(encoded_path).to_bytes(8, "big"))
        digest.update(encoded_path)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
        total_bytes += len(data)
        files.append(
            PrefixBenchFileBinding(
                path=relative,
                bytes=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
            )
        )
    return PrefixBenchMutationProtocol(
        operators=tuple(
            PrefixBenchOperatorProtocol(operator=operator, expected_invariant=invariant)
            for operator, invariant in _OPERATOR_INVARIANTS
        ),
        outcomes=tuple(OfflineCampaignOutcome),
        canonical_json=PrefixBenchCanonicalJsonProtocol(),
        source_set=PrefixBenchProtocolSourceSet(
            bytes=total_bytes,
            sha256=digest.hexdigest(),
            files=tuple(files),
        ),
        components=tuple(
            PrefixBenchProtocolComponent(
                name=name,
                entrypoint=entrypoint,
                source_paths=source_paths,
            )
            for name, entrypoint, source_paths in _PROTOCOL_COMPONENTS
        ),
    )


def _resolve_journal_path(binding: PrefixBenchFileBinding, project_root: Path) -> Path:
    relative = Path(binding.path)
    if relative.is_absolute() or relative.parts[:2] != ("runs", "terminal-bench-2"):
        raise ValueError(f"journal path is outside the run store: {binding.path}")
    path = project_root / relative
    _require_project_path(path.resolve(), project_root, label="journal")
    return path


def _file_binding(path: Path, data: bytes, project_root: Path) -> PrefixBenchFileBinding:
    resolved = path.resolve()
    _require_project_path(resolved, project_root, label="source")
    return PrefixBenchFileBinding(
        path=resolved.relative_to(project_root).as_posix(),
        bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
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


def _validate_committed_input(project_root: Path, relative: Path, data: bytes) -> None:
    actual_root = Path(_run_git(project_root, "rev-parse", "--show-toplevel").decode().strip())
    if actual_root.resolve() != project_root:
        raise ValueError("project root is not the Git top level")
    committed = _run_git(project_root, "show", f"HEAD:{relative.as_posix()}")
    if committed != data:
        raise ValueError(f"development input differs from Git HEAD: {relative}")


def _run_git(project_root: Path, *args: str) -> bytes:
    try:
        completed = subprocess.run(
            ("git", "-C", str(project_root), *args),
            check=False,
            capture_output=True,
        )
    except OSError as exc:
        raise ValueError(f"cannot execute Git: {exc}") from exc
    if completed.returncode:
        detail = completed.stderr.decode(errors="replace").strip()
        raise ValueError(f"Git {' '.join(args)} failed: {detail}")
    return completed.stdout


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


def _json_object(data: bytes, label: str) -> dict[str, Any]:
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _json_row(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _required_string(value: dict[str, Any], field: str) -> str:
    result = value.get(field)
    if not isinstance(result, str) or not result:
        raise ValueError(f"{field} must be a non-empty string")
    return result


def _required_int(value: dict[str, Any], field: str) -> int:
    result = value.get(field)
    if not isinstance(result, int) or isinstance(result, bool):
        raise ValueError(f"{field} must be an integer")
    return result
