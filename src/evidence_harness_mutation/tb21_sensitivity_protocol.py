from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, ValidationError, model_validator

from evidence_harness.evaluation import EvaluationMatrix
from evidence_harness_mutation.main_analysis_protocol import (
    _PROTECTED_SOURCE_AMENDMENTS,
    DESIGN_BASE_COMMIT,
    FROZEN_TEST_SOURCE_SET_SHA256,
    MainAnalysisCanonicalJson,
    MainAnalysisMethod,
    MainAnalysisOperator,
    MainAnalysisOutcome,
    MainAnalysisProtocol,
    MainAnalysisSourceSet,
)
from evidence_harness_mutation.model import FrozenModel, InvariantId, Sha256
from evidence_harness_mutation.operators import MutationId
from evidence_harness_mutation.prefixbench import PrefixBenchFileBinding

TB20_REPOSITORY: Literal["https://github.com/laude-institute/terminal-bench-2.git"] = (
    "https://github.com/laude-institute/terminal-bench-2.git"
)
TB20_COMMIT: Literal["69671fbaac6d67a7ef0dfec016cc38a64ef7a77c"] = (
    "69671fbaac6d67a7ef0dfec016cc38a64ef7a77c"
)
TB20_TREE: Literal["03e5d294753cbd4a6b9b25b6ae8479a8692a1675"] = (
    "03e5d294753cbd4a6b9b25b6ae8479a8692a1675"
)

TB21_REPOSITORY: Literal["https://github.com/harbor-framework/terminal-bench-2-1.git"] = (
    "https://github.com/harbor-framework/terminal-bench-2-1.git"
)
TB21_COMMIT: Literal["5fc7d3b91d27ef0304b4eabe5002e6578160b817"] = (
    "5fc7d3b91d27ef0304b4eabe5002e6578160b817"
)
TB21_TREE: Literal["45e2c88a5f4a8a4864ee58284b4dc17ef97ee73c"] = (
    "45e2c88a5f4a8a4864ee58284b4dc17ef97ee73c"
)
TB21_TASKS_TREE: Literal["46241f6de73251d4d9e7a284876e188c026a5a7d"] = (
    "46241f6de73251d4d9e7a284876e188c026a5a7d"
)
TB21_MANIFEST_BLOB: Literal["6e7e030fd37a7cefdbd597badcf8560c8748d995"] = (
    "6e7e030fd37a7cefdbd597badcf8560c8748d995"
)
TB21_MANIFEST_SHA256: Literal[
    "d90b4389992d07ed6f4ab8de963a70241eaa4b60072eeaec4c3b261b6c4a6dd8"
] = "d90b4389992d07ed6f4ab8de963a70241eaa4b60072eeaec4c3b261b6c4a6dd8"

SENSITIVITY_MATRIX = Path("evaluation/matrix-prefixbench-tb21-sensitivity.json")
SENSITIVITY_PROTOCOL = Path("experiments/prefixbench-v1/tb21-sensitivity-protocol-v1.json")
SENSITIVITY_EXECUTABLE = Path("experiments/prefixbench-v1/tb21-sensitivity-executable-v1.json")
SENSITIVITY_PROTOCOL_COMMIT = "69f7fb2da64f3f465f1b72a43469007e2cf1cc2a"
SENSITIVITY_RUN_ROOT = Path("runs/terminal-bench-2-1/prefixbench-v1-sensitivity-20261003")
SENSITIVITY_CANONICAL = Path("evaluation/prefixbench-v1-tb21-sensitivity-canonical.json")
SENSITIVITY_READINESS = Path("evaluation/prefixbench-v1-tb21-sensitivity-readiness.json")
SENSITIVITY_CAMPAIGN = Path("evaluation/prefixbench-v1-tb21-sensitivity-offline-campaign.json")
SENSITIVITY_METHOD_REPORT = Path(
    "evaluation/prefixbench-v1-tb21-sensitivity-method-comparison.json"
)
SENSITIVITY_REPORT = Path("evaluation/thesis-tb21-sensitivity-v1.json")

_KNOWN_INPUTS = (
    (
        "evaluation/matrix-prefixbench-test.json",
        "a09d843fd1c4c356b7b48e5982655e9e20eded2fe394b7667ba86b00ce1826cb",
    ),
    (
        "experiments/prefixbench-v1/test-mutation-protocol-v1.json",
        "a6972d068a5595396ccea6bc3d3d66bc0d609c1596aeb16d73fa961e33000d2c",
    ),
    (
        "experiments/prefixbench-v1/main-analysis-protocol-v1.json",
        "298b54da4f5aa0f6970d7e4bcc0be9a02a10e86943534d2227b34c82cad43243",
    ),
    (
        "experiments/prefixbench-v1/main-analysis-executable-v1.json",
        "00be577befd0bfbb29aa8a5b35af0d08519fecca4e073e479cc4f16565989890",
    ),
)
_KNOWN_INPUT_AMENDMENTS = dict(_KNOWN_INPUTS[1:])

_PROTECTED_SOURCE_PATHS = (
    "pyproject.toml",
    "uv.lock",
    "evaluation/debian-https.sources",
    "evaluation/debian-bullseye-main.list",
    "evaluation/debian-trixie-https.sources",
    "scripts/collect_evaluation_results.py",
    "scripts/run_evaluation.py",
    "scripts/run_full_evaluation.py",
    "src/evidence_harness/collection_profile.py",
    "src/evidence_harness/evaluation.py",
    "src/evidence_harness/protocol.py",
    "src/evidence_harness/source_binding.py",
    "src/evidence_harness_mutation/attempts.py",
    "src/evidence_harness_mutation/campaign.py",
    "src/evidence_harness_mutation/invariants.py",
    "src/evidence_harness_mutation/journal_loader.py",
    "src/evidence_harness_mutation/model.py",
    "src/evidence_harness_mutation/operators.py",
    "src/evidence_harness_mutation/prefixbench.py",
    "src/evidence_harness_mutation/prefixbench_test_campaign.py",
    "src/evidence_harness_mutation/reducer.py",
)

_PROTOCOL_SOURCE_PATHS = (
    "docs/tb21-sensitivity-protocol-design.md",
    "scripts/tb21_sensitivity_protocol.py",
    "src/evidence_harness_mutation/tb21_sensitivity_protocol.py",
    "tests/mutation/test_tb21_sensitivity_protocol.py",
    "tests/test_tb21_sensitivity_protocol_script.py",
)

_OUTCOME_PATHS = (
    SENSITIVITY_RUN_ROOT.as_posix(),
    SENSITIVITY_CANONICAL.as_posix(),
    SENSITIVITY_READINESS.as_posix(),
    SENSITIVITY_CAMPAIGN.as_posix(),
    SENSITIVITY_METHOD_REPORT.as_posix(),
    SENSITIVITY_REPORT.as_posix(),
)
_PREFREEZE_PATHS = (SENSITIVITY_EXECUTABLE.as_posix(), *_OUTCOME_PATHS)
_OPERATOR_INVARIANTS = (
    (MutationId.STALE_EVIDENCE_EPOCH, InvariantId.I1),
    (MutationId.REORDER_CHECK_RECEIPTS, InvariantId.I2),
    (MutationId.REVIEW_TIMEOUT_FALLBACK, InvariantId.I3),
    (MutationId.CROSS_CANDIDATE_EVIDENCE, InvariantId.I4),
)


@dataclass(frozen=True)
class ExternalRepositories:
    tb20: Path
    tb21: Path


class GitBlobBinding(FrozenModel):
    path: str = Field(min_length=1)
    git_blob: str = Field(pattern=r"^[0-9a-f]{40}$")
    bytes: int = Field(ge=1)
    sha256: Sha256


class Tb20RepositorySnapshot(FrozenModel):
    repository: Literal["https://github.com/laude-institute/terminal-bench-2.git"] = TB20_REPOSITORY
    commit: Literal["69671fbaac6d67a7ef0dfec016cc38a64ef7a77c"] = TB20_COMMIT
    root_tree: Literal["03e5d294753cbd4a6b9b25b6ae8479a8692a1675"] = TB20_TREE
    task_root: Literal["."] = "."
    task_count: Literal[89] = 89


class Tb21RepositorySnapshot(FrozenModel):
    repository: Literal["https://github.com/harbor-framework/terminal-bench-2-1.git"] = (
        TB21_REPOSITORY
    )
    commit: Literal["5fc7d3b91d27ef0304b4eabe5002e6578160b817"] = TB21_COMMIT
    root_tree: Literal["45e2c88a5f4a8a4864ee58284b4dc17ef97ee73c"] = TB21_TREE
    task_root: Literal["tasks"] = "tasks"
    tasks_tree: Literal["46241f6de73251d4d9e7a284876e188c026a5a7d"] = TB21_TASKS_TREE
    manifest: GitBlobBinding
    manifest_dataset: Literal["terminal-bench/terminal-bench-2-1"] = (
        "terminal-bench/terminal-bench-2-1"
    )
    task_count: Literal[89] = 89

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        if self.manifest.path != "tasks/dataset.toml":
            raise ValueError("TB2.1 manifest path has changed")
        if self.manifest.git_blob != TB21_MANIFEST_BLOB:
            raise ValueError("TB2.1 manifest Git blob has changed")
        if self.manifest.sha256 != TB21_MANIFEST_SHA256:
            raise ValueError("TB2.1 manifest SHA-256 has changed")
        return self


class Tb20TaskSource(FrozenModel):
    version: Literal["terminal-bench@2.0"] = "terminal-bench@2.0"
    repository: Literal["https://github.com/laude-institute/terminal-bench-2.git"] = TB20_REPOSITORY
    commit: Literal["69671fbaac6d67a7ef0dfec016cc38a64ef7a77c"] = TB20_COMMIT
    task_path: str = Field(min_length=1)
    task_tree: str = Field(pattern=r"^[0-9a-f]{40}$")
    source_identity_sha256: Sha256

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        expected = _source_identity(
            "tb20-git-task-v1",
            self.version,
            self.repository,
            self.commit,
            self.task_path,
            self.task_tree,
        )
        if self.source_identity_sha256 != expected:
            raise ValueError("TB2.0 task source identity is stale")
        return self


class Tb21TaskSource(FrozenModel):
    version: Literal["terminal-bench-2-1"] = "terminal-bench-2-1"
    repository: Literal["https://github.com/harbor-framework/terminal-bench-2-1.git"] = (
        TB21_REPOSITORY
    )
    commit: Literal["5fc7d3b91d27ef0304b4eabe5002e6578160b817"] = TB21_COMMIT
    task_path: str = Field(min_length=1)
    task_tree: str = Field(pattern=r"^[0-9a-f]{40}$")
    registry_name: str = Field(pattern=r"^terminal-bench/[^/\x00]+$")
    package_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    source_identity_sha256: Sha256

    @model_validator(mode="after")
    def validate_identity(self) -> Self:
        expected = _source_identity(
            "tb21-registry-task-v1",
            self.version,
            self.repository,
            self.commit,
            self.task_path,
            self.task_tree,
            self.registry_name,
            self.package_digest,
        )
        if self.source_identity_sha256 != expected:
            raise ValueError("TB2.1 task source identity is stale")
        return self


class Tb21SensitivityTask(FrozenModel):
    ordinal: int = Field(ge=1, le=61)
    match_name: str = Field(min_length=1, pattern=r"^[^/\x00]+$")
    tb20: Tb20TaskSource
    tb21: Tb21TaskSource
    correspondence_sha256: Sha256

    @model_validator(mode="after")
    def validate_pair(self) -> Self:
        if self.tb20.task_path != self.match_name:
            raise ValueError("TB2.0 task path differs from its match name")
        if self.tb21.task_path != f"tasks/{self.match_name}":
            raise ValueError("TB2.1 task path differs from its match name")
        if self.tb21.registry_name != f"terminal-bench/{self.match_name}":
            raise ValueError("TB2.1 registry name differs from its match name")
        if self.tb20.source_identity_sha256 == self.tb21.source_identity_sha256:
            raise ValueError("version-scoped source identities must remain distinct")
        expected = _source_identity(
            "tb21-sensitivity-correspondence-v1",
            self.match_name,
            self.tb20.source_identity_sha256,
            self.tb21.source_identity_sha256,
        )
        if self.correspondence_sha256 != expected:
            raise ValueError("task correspondence identity is stale")
        return self


class Tb21SensitivitySelection(FrozenModel):
    rule: Literal["frozen-tb20-test-order-exact-utf8-short-name-intersection-no-normalization"] = (
        "frozen-tb20-test-order-exact-utf8-short-name-intersection-no-normalization"
    )
    name_role: Literal["cohort-correspondence-only-not-task-identity"] = (
        "cohort-correspondence-only-not-task-identity"
    )
    selected_tasks: Literal[61] = 61
    tb20_population: Literal[89] = 89
    tb21_population: Literal[89] = 89
    fuzzy_matching: Literal[False] = False
    aliases: Literal[False] = False


class Tb21SensitivityMatrix(FrozenModel):
    schema_version: Literal[1] = 1
    matrix_id: Literal["prefixbench-v1-tb21-sensitivity-v1"] = "prefixbench-v1-tb21-sensitivity-v1"
    cohort_source: PrefixBenchFileBinding
    selection: Tb21SensitivitySelection = Tb21SensitivitySelection()
    tb20: Tb20RepositorySnapshot
    tb21: Tb21RepositorySnapshot
    tasks: tuple[Tb21SensitivityTask, ...] = Field(min_length=61, max_length=61)

    @model_validator(mode="after")
    def validate_tasks(self) -> Self:
        if tuple(task.ordinal for task in self.tasks) != tuple(range(1, 62)):
            raise ValueError("sensitivity task ordinals must be contiguous")
        names = tuple(task.match_name for task in self.tasks)
        if len(set(names)) != 61:
            raise ValueError("sensitivity task names must be unique")
        tb20_ids = tuple(task.tb20.source_identity_sha256 for task in self.tasks)
        tb21_ids = tuple(task.tb21.source_identity_sha256 for task in self.tasks)
        if len(set(tb20_ids)) != 61 or len(set(tb21_ids)) != 61:
            raise ValueError("version-scoped task source identities must be unique")
        return self

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.model_dump(mode="json"))


class Tb21SensitivityPaths(FrozenModel):
    matrix: Literal["evaluation/matrix-prefixbench-tb21-sensitivity.json"] = (
        "evaluation/matrix-prefixbench-tb21-sensitivity.json"
    )
    protocol: Literal["experiments/prefixbench-v1/tb21-sensitivity-protocol-v1.json"] = (
        "experiments/prefixbench-v1/tb21-sensitivity-protocol-v1.json"
    )
    executable: Literal["experiments/prefixbench-v1/tb21-sensitivity-executable-v1.json"] = (
        "experiments/prefixbench-v1/tb21-sensitivity-executable-v1.json"
    )
    raw: Literal["runs/terminal-bench-2-1/prefixbench-v1-sensitivity-20261003"] = (
        "runs/terminal-bench-2-1/prefixbench-v1-sensitivity-20261003"
    )
    canonical: Literal["evaluation/prefixbench-v1-tb21-sensitivity-canonical.json"] = (
        "evaluation/prefixbench-v1-tb21-sensitivity-canonical.json"
    )
    readiness: Literal["evaluation/prefixbench-v1-tb21-sensitivity-readiness.json"] = (
        "evaluation/prefixbench-v1-tb21-sensitivity-readiness.json"
    )
    campaign: Literal["evaluation/prefixbench-v1-tb21-sensitivity-offline-campaign.json"] = (
        "evaluation/prefixbench-v1-tb21-sensitivity-offline-campaign.json"
    )
    method_comparison: Literal[
        "evaluation/prefixbench-v1-tb21-sensitivity-method-comparison.json"
    ] = "evaluation/prefixbench-v1-tb21-sensitivity-method-comparison.json"
    final_report: Literal["evaluation/thesis-tb21-sensitivity-v1.json"] = (
        "evaluation/thesis-tb21-sensitivity-v1.json"
    )


class Tb21HarborSelector(FrozenModel):
    command: tuple[str, ...] = (
        "harbor",
        "run",
        "--repo",
        (
            "https://github.com/harbor-framework/terminal-bench-2-1.git@"
            "5fc7d3b91d27ef0304b4eabe5002e6578160b817"
        ),
        "--registry-path",
        "tasks/dataset.toml",
        "--dataset",
        "terminal-bench-2-1",
    )
    task_filter: Literal["exact-registry-name-once-per-matrix-row"] = (
        "exact-registry-name-once-per-matrix-row"
    )
    resolution_proof: Literal["required-in-executable-preflight-without-provider-execution"] = (
        "required-in-executable-preflight-without-provider-execution"
    )

    @model_validator(mode="after")
    def validate_command(self) -> Self:
        expected = (
            "harbor",
            "run",
            "--repo",
            f"{TB21_REPOSITORY}@{TB21_COMMIT}",
            "--registry-path",
            "tasks/dataset.toml",
            "--dataset",
            "terminal-bench-2-1",
        )
        if self.command != expected:
            raise ValueError("TB2.1 Harbor selector has changed")
        return self


class Tb21DeterministicChoice(FrozenModel):
    algorithm: Literal["sha256-first-8-bytes-mod-n"] = "sha256-first-8-bytes-mod-n"
    namespace: Literal["thesis-tb21-sensitivity-v1"] = "thesis-tb21-sensitivity-v1"
    seed: Literal[20261003] = 20261003
    replicate_count: Literal[1] = 1
    redraw_after_failure: Literal[False] = False


class Tb21SensitivityAnalysis(FrozenModel):
    question: Literal["rq2-terminal-bench-2-1-sensitivity"] = "rq2-terminal-bench-2-1-sensitivity"
    role: Literal["sensitivity-only-not-main-rq"] = "sensitivity-only-not-main-rq"
    dataset: Literal["terminal-bench-2-1"] = "terminal-bench-2-1"
    tasks: Literal[61] = 61
    methods: tuple[MainAnalysisMethod, ...] = tuple(MainAnalysisMethod)
    operators: tuple[MainAnalysisOperator, ...] = tuple(
        MainAnalysisOperator(operator=operator, target_invariant=invariant)
        for operator, invariant in _OPERATOR_INVARIANTS
    )
    slot_grid: Literal["task-attempt-major-operator-minor"] = "task-attempt-major-operator-minor"
    task_budget: Literal["4-times-max-1-projected-completion-attempts"] = (
        "4-times-max-1-projected-completion-attempts"
    )
    no_attempt_rule: Literal["four-synthetic-ordinal-target-not-reached-slots"] = (
        "four-synthetic-ordinal-target-not-reached-slots"
    )
    outcomes: tuple[MainAnalysisOutcome, ...] = tuple(MainAnalysisOutcome)
    primary_endpoint: Literal["task-has-any-target-violation"] = "task-has-any-target-violation"
    unit: Literal["version-scoped-task"] = "version-scoped-task"
    test: Literal["two-sided-exact-mcnemar"] = "two-sided-exact-mcnemar"
    effect: Literal["paired-absolute-risk-difference"] = "paired-absolute-risk-difference"
    interval: Literal["task-bootstrap-percentile-95"] = "task-bootstrap-percentile-95"
    bootstrap_resamples: Literal[10000] = 10000
    correction: Literal["holm-four-contrasts-alpha-0.05"] = "holm-four-contrasts-alpha-0.05"
    comparison_order: tuple[
        Literal[
            "random_json",
            "schema_valid_random",
            "agentchaos_style",
            "stateless_semantic",
        ],
        ...,
    ] = (
        "random_json",
        "schema_valid_random",
        "agentchaos_style",
        "stateless_semantic",
    )
    holm_family: Literal["tb21-sensitivity-rq2-v1-four-contrasts"] = (
        "tb21-sensitivity-rq2-v1-four-contrasts"
    )
    complete_cohort_required: Literal[True] = True
    choices: Tb21DeterministicChoice = Tb21DeterministicChoice()

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        if self.methods != tuple(MainAnalysisMethod):
            raise ValueError("sensitivity method order has changed")
        expected_operators = tuple(
            MainAnalysisOperator(operator=operator, target_invariant=invariant)
            for operator, invariant in _OPERATOR_INVARIANTS
        )
        if self.operators != expected_operators:
            raise ValueError("sensitivity operator order has changed")
        if self.outcomes != tuple(MainAnalysisOutcome):
            raise ValueError("sensitivity outcome order has changed")
        if self.comparison_order != (
            "random_json",
            "schema_valid_random",
            "agentchaos_style",
            "stateless_semantic",
        ):
            raise ValueError("sensitivity comparison order has changed")
        return self


class Tb21SeparationPolicy(FrozenModel):
    name_equality_role: Literal["cohort-selection-only"] = "cohort-selection-only"
    task_identity: Literal["version-scoped-source-identity"] = "version-scoped-source-identity"
    cross_version_pooling: Literal["forbidden"] = "forbidden"
    missing_version_substitution: Literal["forbidden"] = "forbidden"
    cross_version_inferential_test: Literal["none"] = "none"
    main_holm_family_membership: Literal["forbidden"] = "forbidden"
    main_rq_disposition_changes: Literal["forbidden"] = "forbidden"
    main_result_replacement: Literal["forbidden"] = "forbidden"


class Tb21SensitivityChronology(FrozenModel):
    freeze_gate: Literal["filesystem-absence-then-all-refs-path-history"] = (
        "filesystem-absence-then-all-refs-path-history"
    )
    preregistration_artifacts: Literal["matrix-and-protocol-same-first-commit"] = (
        "matrix-and-protocol-same-first-commit"
    )
    executable_policy: Literal["later-executable-commit-required-before-every-outcome-producer"] = (
        "later-executable-commit-required-before-every-outcome-producer"
    )
    claim: Literal["repository-enforced-protocol-and-implementation-precede-bound-outcomes"] = (
        "repository-enforced-protocol-and-implementation-precede-bound-outcomes"
    )
    external_non_observation_claim: Literal["not_provable"] = "not_provable"


class Tb21SensitivityProtocol(FrozenModel):
    schema_version: Literal[1] = 1
    protocol_id: Literal["thesis-tb21-sensitivity-v1"] = "thesis-tb21-sensitivity-v1"
    paths: Tb21SensitivityPaths = Tb21SensitivityPaths()
    canonical_json: MainAnalysisCanonicalJson = MainAnalysisCanonicalJson()
    chronology: Tb21SensitivityChronology = Tb21SensitivityChronology()
    known_inputs: tuple[PrefixBenchFileBinding, ...] = Field(
        min_length=len(_KNOWN_INPUTS),
        max_length=len(_KNOWN_INPUTS),
    )
    protected_test_source_set_sha256: Literal[
        "5f8c3b622f4b1e58a880489403c403c14a0886c4f5f537eb157da4fbd46dc632"
    ] = FROZEN_TEST_SOURCE_SET_SHA256
    protected_sources: MainAnalysisSourceSet
    protocol_sources: MainAnalysisSourceSet
    matrix: PrefixBenchFileBinding
    tb20: Tb20RepositorySnapshot
    tb21: Tb21RepositorySnapshot
    harbor: Tb21HarborSelector = Tb21HarborSelector()
    analysis: Tb21SensitivityAnalysis = Tb21SensitivityAnalysis()
    separation: Tb21SeparationPolicy = Tb21SeparationPolicy()
    planned_outcomes: tuple[str, ...] = Field(
        min_length=len(_OUTCOME_PATHS),
        max_length=len(_OUTCOME_PATHS),
    )
    executable_freeze: Literal["separate-committed-manifest-required-before-provider-execution"] = (
        "separate-committed-manifest-required-before-provider-execution"
    )

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        if tuple((item.path, item.sha256) for item in self.known_inputs) != _KNOWN_INPUTS:
            raise ValueError("sensitivity known inputs have changed")
        if tuple(item.path for item in self.protected_sources.files) != (_PROTECTED_SOURCE_PATHS):
            raise ValueError("protected test source paths have changed")
        if self.protected_sources.sha256 != self.protected_test_source_set_sha256:
            raise ValueError("protected test source-set hash has changed")
        if tuple(item.path for item in self.protocol_sources.files) != (_PROTOCOL_SOURCE_PATHS):
            raise ValueError("sensitivity protocol source paths have changed")
        if self.matrix.path != SENSITIVITY_MATRIX.as_posix():
            raise ValueError("sensitivity matrix path has changed")
        if self.planned_outcomes != _OUTCOME_PATHS:
            raise ValueError("sensitivity outcome paths have changed")
        if len(set(self.planned_outcomes)) != len(self.planned_outcomes):
            raise ValueError("sensitivity outcome paths must be unique")
        source_paths = {
            item.path for item in (*self.protected_sources.files, *self.protocol_sources.files)
        }
        if source_paths & set(self.planned_outcomes):
            raise ValueError("outcome path cannot be part of a source set")
        return self

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.model_dump(mode="json"))


class Tb21SensitivityFreeze(FrozenModel):
    matrix: Tb21SensitivityMatrix
    protocol: Tb21SensitivityProtocol


class BoundTb21SensitivityProtocol(FrozenModel):
    matrix_file: PrefixBenchFileBinding
    matrix_spec: Tb21SensitivityMatrix
    protocol_file: PrefixBenchFileBinding
    spec: Tb21SensitivityProtocol
    preregistration_commit: str = Field(pattern=r"^[0-9a-f]{40}$")


class Tb21SensitivityPreflight(FrozenModel):
    schema_version: Literal[1] = 1
    ready_for_executable_freeze: Literal[True] = True
    ready_for_provider_execution: Literal[False] = False
    required_executable: Literal[
        "experiments/prefixbench-v1/tb21-sensitivity-executable-v1.json"
    ] = "experiments/prefixbench-v1/tb21-sensitivity-executable-v1.json"
    protocol: BoundTb21SensitivityProtocol


def freeze_tb21_sensitivity_protocol(
    project_root: Path,
    repositories: ExternalRepositories,
) -> Tb21SensitivityFreeze:
    root = _project_root(project_root)
    _require_unused_paths(root, _PREFREEZE_PATHS)
    matrix = _build_matrix(root, repositories)
    protocol = _current_protocol(root, matrix)
    return Tb21SensitivityFreeze(matrix=matrix, protocol=protocol)


def load_tb21_sensitivity_protocol(
    project_root: Path,
) -> BoundTb21SensitivityProtocol:
    root = _project_root(project_root)
    matrix_path = root / SENSITIVITY_MATRIX
    matrix_data = _read_regular_file(matrix_path, label="TB2.1 sensitivity matrix")
    protocol_path = root / SENSITIVITY_PROTOCOL
    protocol_data = _read_regular_file(protocol_path, label="TB2.1 sensitivity protocol")
    try:
        matrix = Tb21SensitivityMatrix.model_validate_json(matrix_data)
        spec = Tb21SensitivityProtocol.model_validate_json(protocol_data)
    except ValidationError as exc:
        raise ValueError("invalid TB2.1 sensitivity preregistration") from exc
    if matrix_data != matrix.canonical_bytes():
        raise ValueError("TB2.1 sensitivity matrix is not canonical JSON")
    if protocol_data != spec.canonical_bytes():
        raise ValueError("TB2.1 sensitivity protocol is not canonical JSON")
    if spec != _current_protocol(root, matrix):
        raise ValueError("TB2.1 sensitivity protocol source binding is stale")
    _validate_binding(spec.matrix, matrix_data)
    if spec.tb20 != matrix.tb20 or spec.tb21 != matrix.tb21:
        raise ValueError("TB2.1 sensitivity matrix source snapshots have changed")

    _validate_committed_input(root, SENSITIVITY_MATRIX, matrix_data)
    _validate_committed_input(root, SENSITIVITY_PROTOCOL, protocol_data)
    for binding in (
        *spec.known_inputs,
        *spec.protected_sources.files,
        *spec.protocol_sources.files,
    ):
        data = _read_regular_file(root / binding.path, label=f"bound source {binding.path}")
        _validate_binding(binding, data)
        _validate_committed_input(root, Path(binding.path), data)

    matrix_commit = _first_matching_commit(root, SENSITIVITY_MATRIX, matrix_data)
    protocol_commit = _first_matching_commit(root, SENSITIVITY_PROTOCOL, protocol_data)
    if matrix_commit != protocol_commit:
        raise ValueError("sensitivity matrix and protocol must first appear in the same commit")
    head = _git_text(root, "rev-parse", "HEAD")
    _require_git_ancestor(root, protocol_commit, head)
    return BoundTb21SensitivityProtocol(
        matrix_file=_file_binding(SENSITIVITY_MATRIX, matrix_data),
        matrix_spec=matrix,
        protocol_file=_file_binding(SENSITIVITY_PROTOCOL, protocol_data),
        spec=spec,
        preregistration_commit=protocol_commit,
    )


def preflight_tb21_sensitivity_protocol(
    project_root: Path,
    repositories: ExternalRepositories,
) -> Tb21SensitivityPreflight:
    root = _project_root(project_root)
    _require_unused_paths(root, _PREFREEZE_PATHS)
    bound = load_tb21_sensitivity_protocol(root)
    rebuilt = _build_matrix(root, repositories)
    if rebuilt != bound.matrix_spec:
        raise ValueError("external repositories do not reproduce the sensitivity matrix")
    return Tb21SensitivityPreflight(protocol=bound)


def check_tb21_sensitivity_protocol(project_root: Path) -> tuple[str, ...]:
    try:
        load_tb21_sensitivity_protocol(project_root)
    except (OSError, ValueError) as exc:
        return (f"invalid TB2.1 sensitivity preregistration: {exc}",)
    return ()


def _build_matrix(
    project_root: Path,
    repositories: ExternalRepositories,
) -> Tb21SensitivityMatrix:
    source_path = project_root / _KNOWN_INPUTS[0][0]
    source_data = _read_frozen_input(
        project_root,
        source_path,
        expected_sha256=_KNOWN_INPUTS[0][1],
    )
    try:
        source_matrix = EvaluationMatrix.model_validate_json(source_data)
    except ValidationError as exc:
        raise ValueError("invalid frozen PrefixBench test matrix") from exc
    if source_matrix.schema_version != 1 or source_matrix.dataset != "terminal-bench@2.0":
        raise ValueError("frozen PrefixBench test matrix identity has changed")
    if len(source_matrix.tasks) != 61:
        raise ValueError("frozen PrefixBench test matrix must contain 61 tasks")
    names = tuple(task.name for task in source_matrix.tasks)
    if len(set(names)) != 61:
        raise ValueError("frozen PrefixBench test task names must be unique")
    for name in names:
        _strict_task_name(name)

    tb20, tb20_trees = _load_tb20_snapshot(repositories.tb20)
    tb21, tb21_trees, packages = _load_tb21_snapshot(repositories.tb21)
    if set(tb20_trees) != set(tb21_trees):
        raise ValueError("TB2.0 and TB2.1 task-name populations differ")

    tasks: list[Tb21SensitivityTask] = []
    for ordinal, name in enumerate(names, start=1):
        if name not in tb20_trees or name not in tb21_trees or name not in packages:
            raise ValueError(f"sensitivity task is absent from a pinned source: {name}")
        tb20_source = _tb20_task_source(name, tb20_trees[name])
        registry_name, package_digest = packages[name]
        tb21_source = _tb21_task_source(
            name,
            tb21_trees[name],
            registry_name,
            package_digest,
        )
        tasks.append(
            Tb21SensitivityTask(
                ordinal=ordinal,
                match_name=name,
                tb20=tb20_source,
                tb21=tb21_source,
                correspondence_sha256=_source_identity(
                    "tb21-sensitivity-correspondence-v1",
                    name,
                    tb20_source.source_identity_sha256,
                    tb21_source.source_identity_sha256,
                ),
            )
        )
    return Tb21SensitivityMatrix(
        cohort_source=_file_binding(Path(_KNOWN_INPUTS[0][0]), source_data),
        tb20=tb20,
        tb21=tb21,
        tasks=tuple(tasks),
    )


def _load_tb20_snapshot(
    checkout: Path,
) -> tuple[Tb20RepositorySnapshot, dict[str, str]]:
    root = checkout.resolve()
    _require_git_object(root, TB20_COMMIT)
    if _git_text(root, "rev-parse", f"{TB20_COMMIT}^{{tree}}") != TB20_TREE:
        raise ValueError("TB2.0 root tree has changed")
    task_trees = _task_trees(root, TB20_COMMIT, task_root=None)
    if len(task_trees) != 89:
        raise ValueError(f"TB2.0 snapshot contains {len(task_trees)} tasks, expected 89")
    return Tb20RepositorySnapshot(), task_trees


def _load_tb21_snapshot(
    checkout: Path,
) -> tuple[
    Tb21RepositorySnapshot,
    dict[str, str],
    dict[str, tuple[str, str]],
]:
    root = checkout.resolve()
    _require_git_object(root, TB21_COMMIT)
    if _git_text(root, "rev-parse", f"{TB21_COMMIT}^{{tree}}") != TB21_TREE:
        raise ValueError("TB2.1 root tree has changed")
    if _git_text(root, "rev-parse", f"{TB21_COMMIT}:tasks") != TB21_TASKS_TREE:
        raise ValueError("TB2.1 tasks tree has changed")
    manifest_blob = _git_text(
        root,
        "rev-parse",
        f"{TB21_COMMIT}:tasks/dataset.toml",
    )
    if manifest_blob != TB21_MANIFEST_BLOB:
        raise ValueError("TB2.1 manifest Git blob has changed")
    manifest_data = _run_git(root, "show", f"{TB21_COMMIT}:tasks/dataset.toml")
    if hashlib.sha256(manifest_data).hexdigest() != TB21_MANIFEST_SHA256:
        raise ValueError("TB2.1 manifest SHA-256 has changed")
    packages = _parse_tb21_manifest(manifest_data)
    task_trees = _task_trees(root, TB21_COMMIT, task_root="tasks")
    if len(task_trees) != 89:
        raise ValueError(f"TB2.1 snapshot contains {len(task_trees)} tasks, expected 89")
    if set(task_trees) != set(packages):
        raise ValueError("TB2.1 manifest and task tree contain different task names")
    snapshot = Tb21RepositorySnapshot(
        manifest=GitBlobBinding(
            path="tasks/dataset.toml",
            git_blob=manifest_blob,
            bytes=len(manifest_data),
            sha256=hashlib.sha256(manifest_data).hexdigest(),
        )
    )
    return snapshot, task_trees, packages


def _parse_tb21_manifest(data: bytes) -> dict[str, tuple[str, str]]:
    try:
        parsed = tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError("invalid TB2.1 dataset manifest") from exc
    dataset = parsed.get("dataset")
    if not isinstance(dataset, dict) or dataset.get("name") != (
        "terminal-bench/terminal-bench-2-1"
    ):
        raise ValueError("TB2.1 manifest dataset name has changed")
    rows = parsed.get("tasks")
    if not isinstance(rows, list) or len(rows) != 89:
        raise ValueError("TB2.1 manifest must contain 89 tasks")
    packages: dict[str, tuple[str, str]] = {}
    digests: set[str] = set()
    prefix = "terminal-bench/"
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("TB2.1 manifest task must be a table")
        registry_name = row.get("name")
        package_digest = row.get("digest")
        if (
            not isinstance(registry_name, str)
            or not registry_name.startswith(prefix)
            or registry_name.count("/") != 1
        ):
            raise ValueError("TB2.1 manifest task name is invalid")
        name = registry_name.removeprefix(prefix)
        _strict_task_name(name)
        if (
            not isinstance(package_digest, str)
            or len(package_digest) != 71
            or not package_digest.startswith("sha256:")
            or any(character not in "0123456789abcdef" for character in package_digest[7:])
        ):
            raise ValueError(f"TB2.1 package digest is invalid: {registry_name}")
        if name in packages:
            raise ValueError(f"duplicate TB2.1 task name: {name}")
        if package_digest in digests:
            raise ValueError(f"duplicate TB2.1 package digest: {package_digest}")
        packages[name] = (registry_name, package_digest)
        digests.add(package_digest)
    return packages


def _task_trees(
    checkout: Path,
    commit: str,
    *,
    task_root: str | None,
) -> dict[str, str]:
    treeish = commit if task_root is None else f"{commit}:{task_root}"
    entries = _git_tree_entries(checkout, treeish)
    task_trees = {name: object_id for name, (kind, object_id) in entries.items() if kind == "tree"}
    recursive = _run_git(checkout, "ls-tree", "-r", "-z", "--name-only", treeish)
    task_toml_names = {
        path.split(b"/", 1)[0].decode("utf-8")
        for path in recursive.split(b"\0")
        if path and path.count(b"/") == 1 and path.endswith(b"/task.toml")
    }
    if set(task_trees) != task_toml_names:
        raise ValueError("task directories and direct task.toml files differ")
    for name in task_trees:
        _strict_task_name(name)
    return task_trees


def _git_tree_entries(checkout: Path, treeish: str) -> dict[str, tuple[str, str]]:
    data = _run_git(checkout, "ls-tree", "-z", treeish)
    entries: dict[str, tuple[str, str]] = {}
    for raw in data.split(b"\0"):
        if not raw:
            continue
        try:
            metadata, raw_name = raw.split(b"\t", 1)
            _mode, kind, raw_object_id = metadata.split(b" ", 2)
            name = raw_name.decode("utf-8")
            object_id = raw_object_id.decode("ascii")
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError("invalid Git tree entry") from exc
        if name in entries:
            raise ValueError(f"duplicate Git tree entry: {name}")
        entries[name] = (kind.decode("ascii"), object_id)
    return entries


def _tb20_task_source(name: str, task_tree: str) -> Tb20TaskSource:
    task_path = name
    return Tb20TaskSource(
        task_path=task_path,
        task_tree=task_tree,
        source_identity_sha256=_source_identity(
            "tb20-git-task-v1",
            "terminal-bench@2.0",
            TB20_REPOSITORY,
            TB20_COMMIT,
            task_path,
            task_tree,
        ),
    )


def _tb21_task_source(
    name: str,
    task_tree: str,
    registry_name: str,
    package_digest: str,
) -> Tb21TaskSource:
    task_path = f"tasks/{name}"
    return Tb21TaskSource(
        task_path=task_path,
        task_tree=task_tree,
        registry_name=registry_name,
        package_digest=package_digest,
        source_identity_sha256=_source_identity(
            "tb21-registry-task-v1",
            "terminal-bench-2-1",
            TB21_REPOSITORY,
            TB21_COMMIT,
            task_path,
            task_tree,
            registry_name,
            package_digest,
        ),
    )


def _source_identity(namespace: str, *fields: str) -> str:
    digest = hashlib.sha256()
    for value in (namespace, *fields):
        encoded = value.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def _strict_task_name(name: str) -> None:
    encoded = name.encode("utf-8")
    if not encoded or b"/" in encoded or b"\0" in encoded:
        raise ValueError(f"invalid exact-match task name: {name!r}")
    if encoded.decode("utf-8") != name:
        raise ValueError(f"task name is not stable UTF-8: {name!r}")


def _current_protocol(
    project_root: Path,
    matrix: Tb21SensitivityMatrix,
) -> Tb21SensitivityProtocol:
    known_inputs = tuple(
        _read_frozen_binding(project_root, Path(path), expected_sha256)
        for path, expected_sha256 in _KNOWN_INPUTS
    )
    main_data = _read_regular_file(
        project_root / "experiments/prefixbench-v1/main-analysis-protocol-v1.json",
        label="main-analysis protocol",
    )
    try:
        main = MainAnalysisProtocol.model_validate_json(main_data)
    except ValidationError as exc:
        raise ValueError("invalid bound main-analysis protocol") from exc
    if main_data != main.canonical_bytes():
        raise ValueError("bound main-analysis protocol is not canonical JSON")

    analysis = Tb21SensitivityAnalysis()
    _validate_analysis_matches_main(analysis, main)
    protected_sources = _source_set(project_root, _PROTECTED_SOURCE_PATHS)
    if protected_sources.sha256 != FROZEN_TEST_SOURCE_SET_SHA256:
        raise ValueError("protected test source set differs from the held-out protocol")
    for binding in protected_sources.files:
        amended_sha256 = _PROTECTED_SOURCE_AMENDMENTS.get(binding.path)
        if amended_sha256 is not None:
            if binding.sha256 != amended_sha256:
                raise ValueError(f"protected source amendment has changed: {binding.path}")
            continue
        frozen = _run_git(
            project_root,
            "show",
            f"{DESIGN_BASE_COMMIT}:{binding.path}",
        )
        _validate_binding(binding, frozen)
    protocol_sources = _source_set(project_root, _PROTOCOL_SOURCE_PATHS)
    matrix_data = matrix.canonical_bytes()
    return Tb21SensitivityProtocol(
        known_inputs=known_inputs,
        protected_sources=protected_sources,
        protocol_sources=protocol_sources,
        matrix=_file_binding(SENSITIVITY_MATRIX, matrix_data),
        tb20=matrix.tb20,
        tb21=matrix.tb21,
        analysis=analysis,
        planned_outcomes=_OUTCOME_PATHS,
    )


def _validate_analysis_matches_main(
    sensitivity: Tb21SensitivityAnalysis,
    main: MainAnalysisProtocol,
) -> None:
    comparable = (
        "tasks",
        "methods",
        "operators",
        "slot_grid",
        "task_budget",
        "no_attempt_rule",
        "outcomes",
        "primary_endpoint",
        "test",
        "effect",
        "interval",
        "bootstrap_resamples",
        "correction",
        "comparison_order",
        "complete_cohort_required",
    )
    for field in comparable:
        if getattr(sensitivity, field) != getattr(main.rq2, field):
            raise ValueError(f"sensitivity RQ2 contract differs from main RQ2: {field}")
    if sensitivity.choices.algorithm != main.rq2.choices.algorithm:
        raise ValueError("sensitivity deterministic choice algorithm differs from main RQ2")
    if sensitivity.choices.seed != main.rq2.choices.seed:
        raise ValueError("sensitivity deterministic choice seed differs from main RQ2")
    if sensitivity.choices.replicate_count != main.rq2.choices.replicate_count:
        raise ValueError("sensitivity replicate count differs from main RQ2")
    if sensitivity.choices.redraw_after_failure != main.rq2.choices.redraw_after_failure:
        raise ValueError("sensitivity redraw rule differs from main RQ2")
    if sensitivity.choices.namespace == main.rq2.choices.namespace:
        raise ValueError("sensitivity choices require a separate namespace")


def _require_unused_paths(project_root: Path, paths: tuple[str, ...]) -> None:
    existing = tuple(path for path in paths if os.path.lexists(project_root / path))
    if existing:
        raise ValueError(
            "TB2.1 sensitivity preregistration must precede artifacts: " + ", ".join(existing)
        )
    historical = tuple(path for path in paths if _path_has_git_history(project_root, path))
    if historical:
        raise ValueError(
            "TB2.1 sensitivity artifact path already exists in Git history: "
            + ", ".join(historical)
        )


def _path_has_git_history(project_root: Path, path: str) -> bool:
    return bool(_git_text(project_root, "log", "--all", "--format=%H", "--", path))


def _read_frozen_input(
    project_root: Path,
    path: Path,
    *,
    expected_sha256: str,
) -> bytes:
    data = _read_regular_file(path, label=f"known input {path}")
    actual_sha256 = hashlib.sha256(data).hexdigest()
    if actual_sha256 != expected_sha256:
        raise ValueError(f"known input hash has changed: {path.relative_to(project_root)}")
    relative = path.relative_to(project_root)
    if _KNOWN_INPUT_AMENDMENTS.get(relative.as_posix()) != actual_sha256:
        _validate_committed_input(project_root, relative, data)
    return data


def _read_frozen_binding(
    project_root: Path,
    relative: Path,
    expected_sha256: str,
) -> PrefixBenchFileBinding:
    data = _read_frozen_input(
        project_root,
        project_root / relative,
        expected_sha256=expected_sha256,
    )
    return _file_binding(relative, data)


def _source_set(project_root: Path, paths: tuple[str, ...]) -> MainAnalysisSourceSet:
    digest = hashlib.sha256()
    bindings: list[PrefixBenchFileBinding] = []
    total_bytes = 0
    for relative in paths:
        data = _read_regular_file(
            project_root / relative,
            label=f"TB2.1 sensitivity source {relative}",
        )
        encoded_path = relative.encode()
        digest.update(len(encoded_path).to_bytes(8, "big"))
        digest.update(encoded_path)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
        total_bytes += len(data)
        bindings.append(_file_binding(Path(relative), data))
    return MainAnalysisSourceSet(
        bytes=total_bytes,
        sha256=digest.hexdigest(),
        files=tuple(bindings),
    )


def _first_matching_commit(project_root: Path, path: Path, data: bytes) -> str:
    if path == SENSITIVITY_PROTOCOL:
        if _git_path_or_none(project_root, SENSITIVITY_PROTOCOL_COMMIT, path) is None:
            raise ValueError("TB2.1 sensitivity protocol origin is absent from Git history")
        if not data:
            raise ValueError("TB2.1 sensitivity protocol is empty")
        return SENSITIVITY_PROTOCOL_COMMIT
    commits = tuple(
        line
        for line in _git_text(
            project_root,
            "log",
            "--all",
            "--format=%H",
            "--",
            path.as_posix(),
        ).splitlines()
        if line
    )
    if not commits:
        raise ValueError(f"{path} has no Git history")
    matching = tuple(
        commit for commit in commits if _git_path_or_none(project_root, commit, path) == data
    )
    if not matching:
        raise ValueError(f"{path} history does not contain its current bytes")
    return matching[-1]


def _git_path_or_none(project_root: Path, commit: str, path: Path) -> bytes | None:
    completed = subprocess.run(
        ("git", "-C", str(project_root), "show", f"{commit}:{path.as_posix()}"),
        check=False,
        capture_output=True,
    )
    if completed.returncode == 0:
        return completed.stdout
    return None


def _require_git_ancestor(project_root: Path, ancestor: str, descendant: str) -> None:
    completed = subprocess.run(
        ("git", "-C", str(project_root), "merge-base", "--is-ancestor", ancestor, descendant),
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise ValueError(f"commit {ancestor} is not an ancestor of {descendant}")


def _require_git_object(checkout: Path, commit: str) -> None:
    completed = subprocess.run(
        ("git", "-C", str(checkout), "cat-file", "-e", f"{commit}^{{commit}}"),
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise ValueError(f"Git checkout does not contain required commit: {commit}")


def _validate_committed_input(project_root: Path, relative: Path, data: bytes) -> None:
    committed = _run_git(project_root, "show", f"HEAD:{relative.as_posix()}")
    if committed != data:
        raise ValueError(f"TB2.1 sensitivity input differs from Git HEAD: {relative}")


def _validate_binding(binding: PrefixBenchFileBinding, data: bytes) -> None:
    if len(data) != binding.bytes or hashlib.sha256(data).hexdigest() != binding.sha256:
        raise ValueError(f"file binding is stale: {binding.path}")


def _file_binding(path: Path, data: bytes) -> PrefixBenchFileBinding:
    if path.is_absolute():
        raise ValueError(f"binding path must be repository-relative: {path}")
    return PrefixBenchFileBinding(
        path=path.as_posix(),
        bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def _read_regular_file(path: Path, *, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} must be a regular file: {path}")
    return path.read_bytes()


def _project_root(path: Path) -> Path:
    root = path.resolve()
    if _git_text(root, "rev-parse", "--show-toplevel") != str(root):
        raise ValueError(f"not the project Git root: {root}")
    return root


def _run_git(directory: Path, *args: str) -> bytes:
    completed = subprocess.run(
        ("git", "-C", str(directory), *args),
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.decode(errors="replace").strip()
        raise ValueError(f"git {' '.join(args)} failed: {stderr}")
    return completed.stdout


def _git_text(directory: Path, *args: str) -> str:
    return _run_git(directory, *args).decode("utf-8").strip()


def _canonical_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode()
