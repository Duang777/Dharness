from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import Field, ValidationError, model_validator

from evidence_harness.collection_profile import PREFIXBENCH_V1
from evidence_harness.evaluation import EvaluationMatrix, MatrixTask
from evidence_harness.protocol import ProducerAttestation
from evidence_harness.source_binding import (
    attest_git_runtime_source,
    runtime_source_binding,
)
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
    build_prefixbench_split_matrix,
    check_prefixbench_readiness,
    prefixbench_task_split,
)

TEST_TASKS = 61
FROZEN_MATRIX_COMMIT = "2e3e65868213238d9bbcdbf3e09ce4356c8edfd9"
FROZEN_TEST_MATRIX_SHA256 = "a09d843fd1c4c356b7b48e5982655e9e20eded2fe394b7667ba86b00ce1826cb"
FROZEN_DEVELOPMENT_PROTOCOL_SHA256 = (
    "b394fe0f4529d7bd476d5113411230151c0fa3077414cd23f0df34ea287e8c8d"
)
FROZEN_RUNTIME_SOURCE_SHA256 = "74dc91c82da08d58fddcd73d6ba102d8f8009920a1be9f1d72ed0a377fde336b"

SOURCE_MATRIX = Path("evaluation/matrix-89.json")
SPLIT_READINESS = Path("evaluation/prefixbench-readiness.json")
DEVELOPMENT_MATRIX = Path("evaluation/matrix-prefixbench-development.json")
TEST_MATRIX = Path("evaluation/matrix-prefixbench-test.json")
TEST_CANONICAL = Path("evaluation/prefixbench-v1-test-canonical.json")
TEST_READINESS = Path("evaluation/prefixbench-v1-test-readiness.json")
TEST_CAMPAIGN = Path("evaluation/prefixbench-v1-test-offline-campaign.json")
TEST_PROTOCOL = Path("experiments/prefixbench-v1/test-mutation-protocol-v1.json")
TEST_RUN_ROOT = Path("runs/terminal-bench-2/prefixbench-v1-test-20261002")
TEST_RUN_CONFIG = TEST_RUN_ROOT / "run-config.json"
TEST_PROGRESS = TEST_RUN_ROOT / "progress.jsonl"
DEVELOPMENT_PROTOCOL = Path("experiments/prefixbench-v1/mutation-protocol-v1.json")

TEST_MODEL = "openai/modelhub/gpt-5.6-terra"

_FROZEN_TEST_TASK_ORDER = (
    "bn-fit-modify",
    "break-filter-js-from-html",
    "build-cython-ext",
    "build-pmars",
    "build-pov-ray",
    "caffe-cifar-10",
    "cancel-async-tasks",
    "circuit-fibsqrt",
    "cobol-modernization",
    "code-from-image",
    "constraints-scheduling",
    "count-dataset-tokens",
    "crack-7z-hash",
    "db-wal-recovery",
    "distribution-search",
    "dna-assembly",
    "dna-insert",
    "extract-elf",
    "extract-moves-from-video",
    "feal-differential-cryptanalysis",
    "filter-js-from-html",
    "financial-document-processor",
    "fix-git",
    "fix-ocaml-gc",
    "gcode-to-text",
    "git-multibranch",
    "gpt2-codegolf",
    "headless-terminal",
    "hf-model-inference",
    "large-scale-text-editing",
    "llm-inference-batching-scheduler",
    "log-summary-date-ranges",
    "mailman",
    "make-doom-for-mips",
    "make-mips-interpreter",
    "mcmc-sampling-stan",
    "merge-diff-arc-agi-task",
    "model-extraction-relu-logits",
    "modernize-scientific-stack",
    "mteb-leaderboard",
    "openssl-selfsigned-cert",
    "overfull-hbox",
    "password-recovery",
    "path-tracing-reverse",
    "pypi-server",
    "pytorch-model-cli",
    "pytorch-model-recovery",
    "qemu-startup",
    "query-optimize",
    "raman-fitting",
    "regex-log",
    "rstan-to-pystan",
    "sam-cell-seg",
    "sanitize-git-repo",
    "sparql-university",
    "sqlite-with-gcov",
    "torch-pipeline-parallelism",
    "train-fasttext",
    "vulnerable-secret",
    "winning-avg-corewars",
    "write-compressor",
)
_OPERATOR_INVARIANTS = (
    (MutationId.STALE_EVIDENCE_EPOCH, InvariantId.I1),
    (MutationId.REORDER_CHECK_RECEIPTS, InvariantId.I2),
    (MutationId.REVIEW_TIMEOUT_FALLBACK, InvariantId.I3),
    (MutationId.CROSS_CANDIDATE_EVIDENCE, InvariantId.I4),
)
_TEST_PROTOCOL_SOURCE_PATHS = (
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
_ORCHESTRATOR_SOURCE_PATHS = (
    "pyproject.toml",
    "uv.lock",
    "evaluation/debian-https.sources",
    "evaluation/debian-bullseye-main.list",
    "evaluation/debian-trixie-https.sources",
    "scripts/run_evaluation.py",
    "scripts/run_full_evaluation.py",
)
_TEST_PROTOCOL_COMPONENTS = (
    (
        "split_selection",
        "evidence_harness_mutation.prefixbench:build_prefixbench_split_matrix",
        (
            "src/evidence_harness/evaluation.py",
            "src/evidence_harness/protocol.py",
            "src/evidence_harness_mutation/model.py",
            "src/evidence_harness_mutation/prefixbench.py",
        ),
    ),
    (
        "runtime_attestation",
        "evidence_harness.source_binding:attest_git_runtime_source",
        (
            "pyproject.toml",
            "uv.lock",
            "src/evidence_harness/protocol.py",
            "src/evidence_harness/source_binding.py",
        ),
    ),
    (
        "collection_profile",
        "evidence_harness.collection_profile:PREFIXBENCH_V1",
        (
            "src/evidence_harness/collection_profile.py",
            "src/evidence_harness/protocol.py",
        ),
    ),
    (
        "serial_collection",
        "scripts.run_full_evaluation:run_full_evaluation",
        (
            "pyproject.toml",
            "uv.lock",
            "evaluation/debian-https.sources",
            "evaluation/debian-bullseye-main.list",
            "evaluation/debian-trixie-https.sources",
            "scripts/run_evaluation.py",
            "scripts/run_full_evaluation.py",
            "src/evidence_harness/collection_profile.py",
            "src/evidence_harness/source_binding.py",
        ),
    ),
    (
        "canonical_collection",
        "scripts.collect_evaluation_results:build_manifest",
        (
            "scripts/collect_evaluation_results.py",
            "src/evidence_harness/collection_profile.py",
            "src/evidence_harness/evaluation.py",
            "src/evidence_harness/protocol.py",
        ),
    ),
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
        "held_out_campaign",
        "evidence_harness_mutation.prefixbench_test_campaign:build_prefixbench_test_campaign",
        _TEST_PROTOCOL_SOURCE_PATHS,
    ),
)

JsonScalar = str | int | float | bool | None


class PrefixBenchCanonicalJsonProtocol(FrozenModel):
    ensure_ascii: Literal[True] = True
    indent: Literal[2] = 2
    sort_keys: Literal[True] = True
    trailing_newline: Literal[True] = True


class PrefixBenchProtocolOption(FrozenModel):
    name: str = Field(min_length=1)
    value: JsonScalar


class PrefixBenchTestArtifactPaths(FrozenModel):
    source_matrix: Literal["evaluation/matrix-89.json"] = "evaluation/matrix-89.json"
    split_readiness: Literal["evaluation/prefixbench-readiness.json"] = (
        "evaluation/prefixbench-readiness.json"
    )
    development_matrix: Literal["evaluation/matrix-prefixbench-development.json"] = (
        "evaluation/matrix-prefixbench-development.json"
    )
    matrix: Literal["evaluation/matrix-prefixbench-test.json"] = (
        "evaluation/matrix-prefixbench-test.json"
    )
    collection_run_root: Literal["runs/terminal-bench-2/prefixbench-v1-test-20261002"] = (
        "runs/terminal-bench-2/prefixbench-v1-test-20261002"
    )
    run_config: Literal["runs/terminal-bench-2/prefixbench-v1-test-20261002/run-config.json"] = (
        "runs/terminal-bench-2/prefixbench-v1-test-20261002/run-config.json"
    )
    progress: Literal["runs/terminal-bench-2/prefixbench-v1-test-20261002/progress.jsonl"] = (
        "runs/terminal-bench-2/prefixbench-v1-test-20261002/progress.jsonl"
    )
    canonical: Literal["evaluation/prefixbench-v1-test-canonical.json"] = (
        "evaluation/prefixbench-v1-test-canonical.json"
    )
    readiness: Literal["evaluation/prefixbench-v1-test-readiness.json"] = (
        "evaluation/prefixbench-v1-test-readiness.json"
    )
    protocol: Literal["experiments/prefixbench-v1/test-mutation-protocol-v1.json"] = (
        "experiments/prefixbench-v1/test-mutation-protocol-v1.json"
    )
    campaign: Literal["evaluation/prefixbench-v1-test-offline-campaign.json"] = (
        "evaluation/prefixbench-v1-test-offline-campaign.json"
    )
    development_protocol: Literal["experiments/prefixbench-v1/mutation-protocol-v1.json"] = (
        "experiments/prefixbench-v1/mutation-protocol-v1.json"
    )


class PrefixBenchTestCollectionProtocol(FrozenModel):
    model: Literal["openai/modelhub/gpt-5.6-terra"] = "openai/modelhub/gpt-5.6-terra"
    profile: Literal["prefixbench-v1"] = "prefixbench-v1"
    journal_schema_version: Literal[2] = 2
    execution: Literal["sequential-matrix-order-one-task-jobs"] = (
        "sequential-matrix-order-one-task-jobs"
    )
    completed_results_per_task: Literal[1] = 1
    retry_policy: Literal["resume-interrupted-only-no-retry-by-reward-status-or-exception"] = (
        "resume-interrupted-only-no-retry-by-reward-status-or-exception"
    )
    result_policy: Literal["accept-every-live-completed-result"] = (
        "accept-every-live-completed-result"
    )
    collector_inputs: Literal["one-fixed-run-root"] = "one-fixed-run-root"
    collector_retry_matrix: Literal["forbidden"] = "forbidden"
    start_at: None = None
    provider_credentials: Literal["external-file-run-config-hash-only"] = (
        "external-file-run-config-hash-only"
    )
    controlled_agent_options: tuple[PrefixBenchProtocolOption, ...]
    agent_kwargs_sha256: Sha256
    orchestration_source_sha256: Sha256

    @model_validator(mode="after")
    def validate_profile(self) -> Self:
        expected = tuple(
            PrefixBenchProtocolOption(name=name, value=value)
            for name, value in PREFIXBENCH_V1.controlled_agent_options
        )
        if self.controlled_agent_options != expected:
            raise ValueError("PrefixBench test collection profile options have changed")
        if self.agent_kwargs_sha256 != _agent_kwargs_sha256():
            raise ValueError("PrefixBench test agent kwargs hash has changed")
        return self


class PrefixBenchTestProducerProtocol(FrozenModel):
    runtime_source_algorithm: Literal["sha256-length-framed-runtime-source-binding-v1"] = (
        "sha256-length-framed-runtime-source-binding-v1"
    )
    runtime_source_description: Literal[
        "pyproject.toml, uv.lock, and src/evidence_harness/**/*.py"
    ] = "pyproject.toml, uv.lock, and src/evidence_harness/**/*.py"
    runtime_source_sha256: Literal[
        "74dc91c82da08d58fddcd73d6ba102d8f8009920a1be9f1d72ed0a377fde336b"
    ] = "74dc91c82da08d58fddcd73d6ba102d8f8009920a1be9f1d72ed0a377fde336b"
    producer_commit_policy: Literal["not-frozen-protocol-must-exist-in-producer-commit"] = (
        "not-frozen-protocol-must-exist-in-producer-commit"
    )
    producer_tree_policy: Literal["not-frozen-all-tasks-must-share-one-tree"] = (
        "not-frozen-all-tasks-must-share-one-tree"
    )


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
    files: tuple[PrefixBenchFileBinding, ...] = Field(
        min_length=len(_TEST_PROTOCOL_SOURCE_PATHS),
        max_length=len(_TEST_PROTOCOL_SOURCE_PATHS),
    )

    @model_validator(mode="after")
    def validate_files(self) -> Self:
        paths = tuple(file.path for file in self.files)
        if paths != _TEST_PROTOCOL_SOURCE_PATHS:
            raise ValueError("PrefixBench test protocol source paths have changed")
        if len(paths) != len(set(paths)):
            raise ValueError("PrefixBench test protocol source paths must be unique")
        if self.bytes != sum(file.bytes for file in self.files):
            raise ValueError("PrefixBench test protocol source byte count is stale")
        return self


class PrefixBenchDevelopmentLineage(FrozenModel):
    protocol_id: Literal["prefixbench-v1-development-offline-mutation-v1"] = (
        "prefixbench-v1-development-offline-mutation-v1"
    )
    file: PrefixBenchFileBinding
    source_set_sha256: Sha256

    @model_validator(mode="after")
    def validate_binding(self) -> Self:
        if self.file.path != DEVELOPMENT_PROTOCOL.as_posix():
            raise ValueError("PrefixBench development protocol path has changed")
        if self.file.sha256 != FROZEN_DEVELOPMENT_PROTOCOL_SHA256:
            raise ValueError("PrefixBench development protocol lineage has changed")
        return self


class PrefixBenchSplitLineage(FrozenModel):
    source_matrix: PrefixBenchFileBinding
    readiness: PrefixBenchFileBinding
    development_matrix: PrefixBenchFileBinding

    @model_validator(mode="after")
    def validate_paths(self) -> Self:
        expected = (
            SOURCE_MATRIX.as_posix(),
            SPLIT_READINESS.as_posix(),
            DEVELOPMENT_MATRIX.as_posix(),
        )
        actual = (
            self.source_matrix.path,
            self.readiness.path,
            self.development_matrix.path,
        )
        if actual != expected:
            raise ValueError("PrefixBench split lineage paths have changed")
        return self


class PrefixBenchTestClaims(FrozenModel):
    scope: Literal["held-out-test-descriptive-offline-campaign-only"] = (
        "held-out-test-descriptive-offline-campaign-only"
    )
    held_out_non_interference: Literal[
        "outcomes-did-not-influence-task-selection-order-retry-policy-operators-oracle-"
        "reducer-formulas-or-claim-scope"
    ] = (
        "outcomes-did-not-influence-task-selection-order-retry-policy-operators-oracle-"
        "reducer-formulas-or-claim-scope"
    )
    oracle_equivalent_definition: Literal[
        "identical-offline-audit-output-not-semantic-equivalence"
    ] = "identical-offline-audit-output-not-semantic-equivalence"
    offline_violation_definition: Literal[
        "new-target-invariant-failure-not-production-mutation-score"
    ] = "new-target-invariant-failure-not-production-mutation-score"
    rq2_baseline_superiority: Literal["not_evaluated"] = "not_evaluated"
    rq3_production_mutation_score: Literal["not_evaluated"] = "not_evaluated"
    rq4_live_cost_savings: Literal["not_evaluated"] = "not_evaluated"
    inferential_statistics: Literal["not_evaluated"] = "not_evaluated"


class PrefixBenchTestMutationProtocol(FrozenModel):
    schema_version: Literal[1] = 1
    protocol_id: Literal["prefixbench-v1-test-offline-mutation-v1"] = (
        "prefixbench-v1-test-offline-mutation-v1"
    )
    cohort: Literal["prefixbench-v1-test"] = "prefixbench-v1-test"
    task_count: Literal[61] = 61
    paths: PrefixBenchTestArtifactPaths
    frozen_matrix_commit: Literal["2e3e65868213238d9bbcdbf3e09ce4356c8edfd9"] = (
        "2e3e65868213238d9bbcdbf3e09ce4356c8edfd9"
    )
    matrix: PrefixBenchFileBinding
    task_order: tuple[str, ...] = Field(min_length=TEST_TASKS, max_length=TEST_TASKS)
    split_lineage: PrefixBenchSplitLineage
    development_lineage: PrefixBenchDevelopmentLineage
    collection: PrefixBenchTestCollectionProtocol
    producer: PrefixBenchTestProducerProtocol
    prefix_policy: Literal["one-full-journal-state-prefix-per-admitted-task"] = (
        "one-full-journal-state-prefix-per-admitted-task"
    )
    phase_witness_policy: Literal[
        "reporting-only-test-admission-ignores-development-coverage-gate"
    ] = "reporting-only-test-admission-ignores-development-coverage-gate"
    campaign_execution: Literal["sequential"] = "sequential"
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
    summary_formulas: Literal["counts-derived-only-from-nested-campaign-cases"] = (
        "counts-derived-only-from-nested-campaign-cases"
    )
    canonical_json: PrefixBenchCanonicalJsonProtocol
    claims: PrefixBenchTestClaims
    source_set: PrefixBenchProtocolSourceSet
    components: tuple[PrefixBenchProtocolComponent, ...] = Field(
        min_length=len(_TEST_PROTOCOL_COMPONENTS),
        max_length=len(_TEST_PROTOCOL_COMPONENTS),
    )

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        if (
            self.matrix.path != TEST_MATRIX.as_posix()
            or self.matrix.sha256 != FROZEN_TEST_MATRIX_SHA256
        ):
            raise ValueError("PrefixBench test matrix binding has changed")
        if self.task_order != _FROZEN_TEST_TASK_ORDER:
            raise ValueError("PrefixBench test task order has changed")
        expected_operators = tuple(
            PrefixBenchOperatorProtocol(operator=operator, expected_invariant=invariant)
            for operator, invariant in _OPERATOR_INVARIANTS
        )
        if self.operators != expected_operators:
            raise ValueError("PrefixBench test operator protocol has changed")
        if self.outcomes != tuple(OfflineCampaignOutcome):
            raise ValueError("PrefixBench test campaign outcome order has changed")
        expected_components = tuple(
            PrefixBenchProtocolComponent(
                name=name,
                entrypoint=entrypoint,
                source_paths=source_paths,
            )
            for name, entrypoint, source_paths in _TEST_PROTOCOL_COMPONENTS
        )
        if self.components != expected_components:
            raise ValueError("PrefixBench test protocol component bindings have changed")
        source_paths = {file.path for file in self.source_set.files}
        for component in self.components:
            if not set(component.source_paths) <= source_paths:
                raise ValueError(f"component source is outside the source set: {component.name}")
        if "src/evidence_harness_mutation/prefixbench_campaign.py" in source_paths:
            raise ValueError("development adapter cannot enter the test source set")
        return self

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.model_dump(mode="json"))


class BoundPrefixBenchTestMutationProtocol(FrozenModel):
    file: PrefixBenchFileBinding
    spec: PrefixBenchTestMutationProtocol
    preregistration_commit: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")

    @model_validator(mode="after")
    def validate_path(self) -> Self:
        if self.file.path != TEST_PROTOCOL.as_posix():
            raise ValueError("PrefixBench test protocol path has changed")
        return self


class PrefixBenchRuntimeSourceFile(FrozenModel):
    path: str = Field(min_length=1)
    bytes: int = Field(ge=1)
    sha256: Sha256


class PrefixBenchRuntimeSource(FrozenModel):
    path: Literal["pyproject.toml, uv.lock, and src/evidence_harness/**/*.py"]
    bytes: int = Field(ge=1)
    sha256: Sha256
    files: tuple[PrefixBenchRuntimeSourceFile, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_files(self) -> Self:
        if self.bytes != sum(file.bytes for file in self.files):
            raise ValueError("runtime source byte count is stale")
        paths = tuple(file.path for file in self.files)
        if len(paths) != len(set(paths)):
            raise ValueError("runtime source paths must be unique")
        return self


class PrefixBenchTestPreflight(FrozenModel):
    schema_version: Literal[1] = 1
    ready_for_collection: Literal[True] = True
    task_count: Literal[61] = 61
    matrix: PrefixBenchFileBinding
    runtime_source: PrefixBenchRuntimeSource
    protocol: BoundPrefixBenchTestMutationProtocol
    collection_run_root: Literal["runs/terminal-bench-2/prefixbench-v1-test-20261002"] = (
        "runs/terminal-bench-2/prefixbench-v1-test-20261002"
    )


class PrefixBenchTestCollectionEvidence(FrozenModel):
    run_config: PrefixBenchFileBinding
    progress: PrefixBenchFileBinding
    producer: ProducerAttestation
    completed_tasks: tuple[str, ...] = Field(min_length=TEST_TASKS, max_length=TEST_TASKS)

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        if self.run_config.path != TEST_RUN_CONFIG.as_posix():
            raise ValueError("PrefixBench test run-config path has changed")
        if self.progress.path != TEST_PROGRESS.as_posix():
            raise ValueError("PrefixBench test progress path has changed")
        if self.completed_tasks != _FROZEN_TEST_TASK_ORDER:
            raise ValueError("PrefixBench test completion order has changed")
        return self


class PrefixBenchTestCampaignSources(FrozenModel):
    matrix: PrefixBenchFileBinding
    canonical: PrefixBenchFileBinding
    readiness: PrefixBenchFileBinding
    collection: PrefixBenchTestCollectionEvidence

    @model_validator(mode="after")
    def validate_paths(self) -> Self:
        expected = (
            TEST_MATRIX.as_posix(),
            TEST_CANONICAL.as_posix(),
            TEST_READINESS.as_posix(),
        )
        actual = (
            self.matrix.path,
            self.canonical.path,
            self.readiness.path,
        )
        if actual != expected:
            raise ValueError("PrefixBench test campaign input paths have changed")
        return self


class PrefixBenchTestOperatorSummary(FrozenModel):
    operator: MutationId
    expected_invariant: InvariantId
    outcomes: OfflineCampaignSummary


class PrefixBenchTestCampaignTask(FrozenModel):
    index: int = Field(ge=1, le=TEST_TASKS)
    name: str = Field(min_length=1)
    task_identity_sha256: Sha256
    journal: PrefixBenchFileBinding
    journal_lines: int = Field(ge=1)
    projected_attempts: int = Field(ge=0)
    campaign: OfflineCampaignReport

    @model_validator(mode="after")
    def validate_campaign(self) -> Self:
        if self.campaign.source.journal_sha256 != self.journal.sha256:
            raise ValueError("test task campaign does not match its journal hash")
        if self.campaign.source.journal_schema_version != 2:
            raise ValueError("test task campaign must use a schema-2 journal")
        if self.campaign.through_line != self.journal_lines:
            raise ValueError("test task campaign must use the complete journal")
        attempt_ordinals = (
            tuple(range(1, self.projected_attempts + 1)) if self.projected_attempts else (1,)
        )
        expected_requests = tuple(
            MutationRequest(operator=operator, attempt_ordinal=attempt_ordinal)
            for attempt_ordinal in attempt_ordinals
            for operator in MutationId
        )
        if tuple(case.request for case in self.campaign.cases) != expected_requests:
            raise ValueError(
                "test task campaign does not contain the exact default request schedule"
            )
        if (
            self.campaign.baseline is not None
            and self.campaign.baseline.verified_attempts > self.projected_attempts
        ):
            raise ValueError("verified attempt count exceeds projected attempt count")
        return self


class PrefixBenchTestCampaignSummary(FrozenModel):
    tasks: Literal[61] = 61
    tasks_with_attempts: int = Field(ge=0, le=TEST_TASKS)
    tasks_with_verified_attempts: int = Field(ge=0, le=TEST_TASKS)
    projected_attempts: int = Field(ge=0)
    verified_attempts: int = Field(ge=0)
    outcomes: OfflineCampaignSummary
    operators: tuple[PrefixBenchTestOperatorSummary, ...] = Field(min_length=4, max_length=4)

    @classmethod
    def from_tasks(cls, tasks: tuple[PrefixBenchTestCampaignTask, ...]) -> Self:
        if len(tasks) != TEST_TASKS:
            raise ValueError("PrefixBench test campaign summary requires 61 tasks")
        cases = tuple(case for task in tasks for case in task.campaign.cases)
        verified = tuple(
            task.campaign.baseline.verified_attempts if task.campaign.baseline is not None else 0
            for task in tasks
        )
        return cls(
            tasks=61,
            tasks_with_attempts=sum(task.projected_attempts > 0 for task in tasks),
            tasks_with_verified_attempts=sum(count > 0 for count in verified),
            projected_attempts=sum(task.projected_attempts for task in tasks),
            verified_attempts=sum(verified),
            outcomes=OfflineCampaignSummary.from_cases(cases),
            operators=tuple(
                PrefixBenchTestOperatorSummary(
                    operator=operator,
                    expected_invariant=invariant,
                    outcomes=OfflineCampaignSummary.from_cases(
                        tuple(case for case in cases if case.request.operator is operator)
                    ),
                )
                for operator, invariant in _OPERATOR_INVARIANTS
            ),
        )


class PrefixBenchTestCampaignReport(FrozenModel):
    schema_version: Literal[1] = 1
    benchmark: Literal["PrefixBench"] = "PrefixBench"
    dataset: Literal["terminal-bench@2.0"] = "terminal-bench@2.0"
    split: Literal["test"] = "test"
    campaign: Literal["completion-offline-mutation-v1"] = "completion-offline-mutation-v1"
    readiness_status: PrefixBenchStatus
    sources: PrefixBenchTestCampaignSources
    protocol: BoundPrefixBenchTestMutationProtocol
    producer: ProducerAttestation
    tasks: tuple[PrefixBenchTestCampaignTask, ...] = Field(
        min_length=TEST_TASKS,
        max_length=TEST_TASKS,
    )
    summary: PrefixBenchTestCampaignSummary
    claims: PrefixBenchTestClaims

    @model_validator(mode="after")
    def validate_report(self) -> Self:
        if self.readiness_status not in {
            PrefixBenchStatus.READY,
            PrefixBenchStatus.PHASE_COVERAGE_INCOMPLETE,
        }:
            raise ValueError("test campaign readiness status is not source-admissible")
        if tuple(task.index for task in self.tasks) != tuple(range(1, TEST_TASKS + 1)):
            raise ValueError("PrefixBench test campaign task indices must be contiguous")
        if tuple(task.name for task in self.tasks) != self.protocol.spec.task_order:
            raise ValueError("PrefixBench test campaign task order has changed")
        if len({task.task_identity_sha256 for task in self.tasks}) != TEST_TASKS:
            raise ValueError("PrefixBench test campaign task identities must be unique")
        if len({task.journal.path for task in self.tasks}) != TEST_TASKS:
            raise ValueError("PrefixBench test campaign journal paths must be unique")
        if self.sources.collection.producer != self.producer:
            raise ValueError("test collection producer does not match the report producer")
        if self.producer.source_sha256 != self.protocol.spec.producer.runtime_source_sha256:
            raise ValueError("test report producer differs from the preregistered runtime source")
        if any(task.campaign.source.source_commit != self.producer.commit for task in self.tasks):
            raise ValueError("PrefixBench test campaigns must use the cohort producer")
        if self.summary != PrefixBenchTestCampaignSummary.from_tasks(self.tasks):
            raise ValueError("PrefixBench test campaign summary does not match its tasks")
        return self

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.model_dump(mode="json"))


class _RunCollection(FrozenModel):
    profile: Literal["prefixbench-v1"]
    producer: ProducerAttestation


class _RunConfiguration(FrozenModel):
    schema_version: Literal[2]
    matrix_sha256: Sha256
    model: str = Field(min_length=1)
    env_file_sha256: Sha256
    agent_kwargs_sha256: Sha256
    source_sha256: Sha256
    start_at: str | None
    debian_bookworm_https_tasks: tuple[str, ...]
    debian_bullseye_main_tasks: tuple[str, ...]
    debian_trixie_https_tasks: tuple[str, ...]
    collection: _RunCollection


@dataclass(frozen=True, slots=True)
class _TestArtifacts:
    root: Path
    sources: tuple[PrefixBenchFileBinding, PrefixBenchFileBinding, PrefixBenchFileBinding]
    protocol: BoundPrefixBenchTestMutationProtocol
    readiness: PrefixBenchReadinessV2
    canonical_rows: tuple[dict[str, Any], ...]
    producer: ProducerAttestation


@dataclass(frozen=True, slots=True)
class _TestCohort:
    artifacts: _TestArtifacts
    collection: PrefixBenchTestCollectionEvidence


def freeze_prefixbench_test_protocol(project_root: Path) -> PrefixBenchTestMutationProtocol:
    root = _project_root(project_root)
    _require_precollection_state(root)
    return _current_protocol_manifest(root)


def preflight_prefixbench_test_campaign(project_root: Path) -> PrefixBenchTestPreflight:
    root = _project_root(project_root)
    _require_precollection_state(root)
    protocol = _load_bound_protocol(root)
    producer = attest_git_runtime_source(root)
    if producer.source_sha256 != protocol.spec.producer.runtime_source_sha256:
        raise ValueError("runtime source hash differs from the test preregistration")
    runtime = PrefixBenchRuntimeSource.model_validate(runtime_source_binding(root))
    return PrefixBenchTestPreflight(
        matrix=protocol.spec.matrix,
        runtime_source=runtime,
        protocol=protocol,
    )


def build_prefixbench_test_campaign(
    project_root: Path,
) -> PrefixBenchTestCampaignReport:
    cohort = _load_test_cohort(project_root)
    artifacts = cohort.artifacts
    tasks = tuple(
        _run_task(task, producer=artifacts.producer, project_root=artifacts.root)
        for task in artifacts.readiness.tasks
    )
    matrix, canonical, readiness = artifacts.sources
    sources = PrefixBenchTestCampaignSources(
        matrix=matrix,
        canonical=canonical,
        readiness=readiness,
        collection=cohort.collection,
    )
    return PrefixBenchTestCampaignReport(
        readiness_status=artifacts.readiness.status,
        sources=sources,
        protocol=artifacts.protocol,
        producer=artifacts.producer,
        tasks=tasks,
        summary=PrefixBenchTestCampaignSummary.from_tasks(tasks),
        claims=PrefixBenchTestClaims(),
    )


def check_prefixbench_test_campaign(
    project_root: Path,
    *,
    report_path: Path | None = None,
) -> tuple[str, ...]:
    root = project_root.resolve()
    selected_report = root / TEST_CAMPAIGN if report_path is None else report_path.resolve()
    try:
        _require_project_path(selected_report, root, label="test campaign report")
        report_bytes = _read_regular_file(selected_report, label="test campaign report")
        report = PrefixBenchTestCampaignReport.model_validate_json(report_bytes)
    except (OSError, ValueError) as exc:
        return (f"invalid PrefixBench test campaign artifact: {exc}",)

    errors: list[str] = []
    if report_bytes != report.canonical_bytes():
        errors.append("PrefixBench test campaign artifact is not canonical JSON")

    try:
        artifacts = _load_test_artifacts(root)
    except (OSError, ValueError) as exc:
        errors.append(f"invalid PrefixBench test campaign source: {exc}")
        return tuple(errors)

    matrix, canonical, readiness = artifacts.sources
    if report.sources.matrix != matrix:
        errors.append("PrefixBench test matrix binding is stale")
    if report.sources.canonical != canonical:
        errors.append("PrefixBench test canonical binding is stale")
    if report.sources.readiness != readiness:
        errors.append("PrefixBench test readiness binding is stale")
    if report.protocol != artifacts.protocol:
        errors.append("PrefixBench test protocol binding is stale")
    if report.producer != artifacts.producer:
        errors.append("PrefixBench test producer binding is stale")
    expected_tasks = tuple(
        (
            task.index,
            task.name,
            task.task_identity_sha256,
            task.sources.journal,
        )
        for task in artifacts.readiness.tasks
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
        errors.append("PrefixBench test task bindings are stale")
    if errors:
        return tuple(errors)

    try:
        raw_paths = _raw_source_paths(artifacts, report.sources.collection)
    except ValueError as exc:
        return (f"invalid PrefixBench test raw-source binding: {exc}",)
    present = tuple(path.is_file() and not path.is_symlink() for path in raw_paths)
    if any(present) and not all(present):
        return (
            "PrefixBench test raw sources are partially available: "
            f"{sum(present)}/{len(present)} bound files exist",
        )
    if not any(present):
        return ()

    try:
        rebuilt = build_prefixbench_test_campaign(root)
    except (OSError, ValueError) as exc:
        return (f"cannot rebuild PrefixBench test campaign artifact: {exc}",)
    if rebuilt.canonical_bytes() != report_bytes:
        return ("PrefixBench test campaign artifact is stale",)
    return ()


def _current_protocol_manifest(project_root: Path) -> PrefixBenchTestMutationProtocol:
    root = _project_root(project_root)
    split_lineage, test_matrix = _validate_split_lineage(root)
    development_bytes = _read_regular_file(
        root / DEVELOPMENT_PROTOCOL,
        label="development mutation protocol",
    )
    _validate_committed_input(root, DEVELOPMENT_PROTOCOL, development_bytes)
    development_json = _json_object(development_bytes, "development mutation protocol")
    if development_bytes != _canonical_json(development_json):
        raise ValueError("development mutation protocol is not canonical JSON")
    if development_json.get("protocol_id") != ("prefixbench-v1-development-offline-mutation-v1"):
        raise ValueError("development mutation protocol ID has changed")
    development_source_set = _json_object(
        development_json.get("source_set"),
        "development mutation protocol source set",
    )

    source_set = _source_set(root)
    runtime = runtime_source_binding(root)
    if runtime["sha256"] != FROZEN_RUNTIME_SOURCE_SHA256:
        raise ValueError("runtime source hash differs from the frozen PrefixBench producer")
    return PrefixBenchTestMutationProtocol(
        paths=PrefixBenchTestArtifactPaths(),
        matrix=_file_binding(root / TEST_MATRIX, test_matrix, root),
        task_order=_FROZEN_TEST_TASK_ORDER,
        split_lineage=split_lineage,
        development_lineage=PrefixBenchDevelopmentLineage(
            file=_file_binding(root / DEVELOPMENT_PROTOCOL, development_bytes, root),
            source_set_sha256=_required_string(development_source_set, "sha256"),
        ),
        collection=PrefixBenchTestCollectionProtocol(
            controlled_agent_options=tuple(
                PrefixBenchProtocolOption(name=name, value=value)
                for name, value in PREFIXBENCH_V1.controlled_agent_options
            ),
            agent_kwargs_sha256=_agent_kwargs_sha256(),
            orchestration_source_sha256=_orchestration_source_sha256(root),
        ),
        producer=PrefixBenchTestProducerProtocol(),
        operators=tuple(
            PrefixBenchOperatorProtocol(operator=operator, expected_invariant=invariant)
            for operator, invariant in _OPERATOR_INVARIANTS
        ),
        outcomes=tuple(OfflineCampaignOutcome),
        canonical_json=PrefixBenchCanonicalJsonProtocol(),
        claims=PrefixBenchTestClaims(),
        source_set=source_set,
        components=tuple(
            PrefixBenchProtocolComponent(
                name=name,
                entrypoint=entrypoint,
                source_paths=source_paths,
            )
            for name, entrypoint, source_paths in _TEST_PROTOCOL_COMPONENTS
        ),
    )


def _load_bound_protocol(project_root: Path) -> BoundPrefixBenchTestMutationProtocol:
    path = project_root / TEST_PROTOCOL
    data = _read_regular_file(path, label="test mutation protocol")
    try:
        spec = PrefixBenchTestMutationProtocol.model_validate_json(data)
    except ValidationError as exc:
        raise ValueError("invalid PrefixBench test mutation protocol") from exc
    if data != spec.canonical_bytes():
        raise ValueError("PrefixBench test mutation protocol is not canonical JSON")
    if spec != _current_protocol_manifest(project_root):
        raise ValueError("PrefixBench test mutation protocol source binding is stale")
    _validate_committed_input(project_root, TEST_PROTOCOL, data)
    for binding in spec.source_set.files:
        source_data = _read_regular_file(project_root / binding.path, label="test protocol source")
        _validate_committed_input(project_root, Path(binding.path), source_data)
    return BoundPrefixBenchTestMutationProtocol(
        file=_file_binding(path, data, project_root),
        spec=spec,
        preregistration_commit=_protocol_commit(project_root, data),
    )


def _validate_split_lineage(
    project_root: Path,
) -> tuple[PrefixBenchSplitLineage, bytes]:
    source_path = project_root / SOURCE_MATRIX
    readiness_path = project_root / SPLIT_READINESS
    development_path = project_root / DEVELOPMENT_MATRIX
    test_path = project_root / TEST_MATRIX
    source_bytes = _read_regular_file(source_path, label="PrefixBench source matrix")
    readiness_bytes = _read_regular_file(readiness_path, label="PrefixBench split readiness")
    development_bytes = _read_regular_file(
        development_path,
        label="PrefixBench development matrix",
    )
    test_bytes = _read_regular_file(test_path, label="PrefixBench test matrix")
    for relative, data in (
        (SOURCE_MATRIX, source_bytes),
        (SPLIT_READINESS, readiness_bytes),
        (DEVELOPMENT_MATRIX, development_bytes),
        (TEST_MATRIX, test_bytes),
    ):
        _validate_committed_input(project_root, relative, data)
    if hashlib.sha256(test_bytes).hexdigest() != FROZEN_TEST_MATRIX_SHA256:
        raise ValueError("PrefixBench test matrix hash has changed")
    frozen = _run_git(project_root, "show", f"{FROZEN_MATRIX_COMMIT}:{TEST_MATRIX.as_posix()}")
    if frozen != test_bytes:
        raise ValueError("PrefixBench test matrix differs from its frozen commit")

    try:
        source = EvaluationMatrix.model_validate_json(source_bytes)
        development = EvaluationMatrix.model_validate_json(development_bytes)
        test = EvaluationMatrix.model_validate_json(test_bytes)
    except ValidationError as exc:
        raise ValueError("invalid PrefixBench split matrix") from exc
    rebuilt_development = build_prefixbench_split_matrix(
        source_path,
        readiness_path,
        PrefixBenchSplit.DEVELOPMENT,
    )
    rebuilt_test = build_prefixbench_split_matrix(
        source_path,
        readiness_path,
        PrefixBenchSplit.TEST,
    )
    if rebuilt_development != development or rebuilt_test != test:
        raise ValueError("PrefixBench split matrices do not match their frozen derivation")
    development_names = tuple(task.name for task in development.tasks)
    test_names = tuple(task.name for task in test.tasks)
    source_names = tuple(task.name for task in source.tasks)
    if (
        len(development_names) != 28
        or len(test_names) != TEST_TASKS
        or set(development_names) & set(test_names)
        or set(development_names) | set(test_names) != set(source_names)
        or len(source_names) != 89
    ):
        raise ValueError("PrefixBench split matrices do not form the frozen 28/61 partition")
    if test_names != _FROZEN_TEST_TASK_ORDER:
        raise ValueError("PrefixBench test matrix order has changed")
    return (
        PrefixBenchSplitLineage(
            source_matrix=_file_binding(source_path, source_bytes, project_root),
            readiness=_file_binding(readiness_path, readiness_bytes, project_root),
            development_matrix=_file_binding(
                development_path,
                development_bytes,
                project_root,
            ),
        ),
        test_bytes,
    )


def _source_set(project_root: Path) -> PrefixBenchProtocolSourceSet:
    digest = hashlib.sha256()
    files: list[PrefixBenchFileBinding] = []
    total_bytes = 0
    for relative in _TEST_PROTOCOL_SOURCE_PATHS:
        data = _read_regular_file(project_root / relative, label=f"test protocol source {relative}")
        encoded_path = relative.encode()
        digest.update(len(encoded_path).to_bytes(8, "big"))
        digest.update(encoded_path)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
        total_bytes += len(data)
        files.append(_file_binding(project_root / relative, data, project_root))
    return PrefixBenchProtocolSourceSet(
        bytes=total_bytes,
        sha256=digest.hexdigest(),
        files=tuple(files),
    )


def _load_test_cohort(project_root: Path) -> _TestCohort:
    artifacts = _load_test_artifacts(project_root)
    collection = _load_collection_evidence(artifacts)
    return _TestCohort(artifacts=artifacts, collection=collection)


def _load_test_artifacts(project_root: Path) -> _TestArtifacts:
    root = _project_root(project_root)
    protocol = _load_bound_protocol(root)
    matrix_path = root / TEST_MATRIX
    canonical_path = root / TEST_CANONICAL
    readiness_path = root / TEST_READINESS
    matrix_bytes = _read_regular_file(matrix_path, label="test matrix")
    canonical_bytes = _read_regular_file(canonical_path, label="test canonical")
    readiness_bytes = _read_regular_file(readiness_path, label="test readiness")
    for relative, data in (
        (TEST_MATRIX, matrix_bytes),
        (TEST_CANONICAL, canonical_bytes),
        (TEST_READINESS, readiness_bytes),
    ):
        _validate_committed_input(root, relative, data)
    try:
        matrix = EvaluationMatrix.model_validate_json(matrix_bytes)
        canonical = _json_object(canonical_bytes, "test canonical")
        readiness = PrefixBenchReadinessV2.model_validate_json(readiness_bytes)
    except ValidationError as exc:
        raise ValueError("invalid PrefixBench test input schema") from exc
    if readiness_bytes != readiness.canonical_bytes():
        raise ValueError("PrefixBench test readiness is not canonical JSON")
    readiness_errors = check_prefixbench_readiness(
        readiness_path=readiness_path,
        canonical_path=canonical_path,
        matrix_path=matrix_path,
        project_root=root,
        expected_task_count=TEST_TASKS,
    )
    if readiness_errors:
        raise ValueError("test readiness failed: " + "; ".join(readiness_errors))
    sources = (
        _file_binding(matrix_path, matrix_bytes, root),
        _file_binding(canonical_path, canonical_bytes, root),
        _file_binding(readiness_path, readiness_bytes, root),
    )
    rows = _canonical_rows(canonical)
    producer = _validate_test_cohort(
        readiness=readiness,
        canonical=canonical,
        rows=rows,
        matrix=matrix,
        sources=sources,
        protocol=protocol.spec,
    )
    _validate_producer_revision(root, protocol, producer)
    return _TestArtifacts(
        root=root,
        sources=sources,
        protocol=protocol,
        readiness=readiness,
        canonical_rows=rows,
        producer=producer,
    )


def _validate_test_cohort(
    *,
    readiness: PrefixBenchReadinessV2,
    canonical: dict[str, Any],
    rows: tuple[dict[str, Any], ...],
    matrix: EvaluationMatrix,
    sources: tuple[PrefixBenchFileBinding, PrefixBenchFileBinding, PrefixBenchFileBinding],
    protocol: PrefixBenchTestMutationProtocol,
) -> ProducerAttestation:
    if readiness.status not in {
        PrefixBenchStatus.READY,
        PrefixBenchStatus.PHASE_COVERAGE_INCOMPLETE,
    }:
        raise ValueError("test readiness has no admitted source cohort")
    if readiness.dataset != "terminal-bench@2.0":
        raise ValueError("test readiness dataset has changed")
    if readiness.collection_profile != "prefixbench-v1":
        raise ValueError("test readiness collection profile has changed")
    summary = readiness.summary
    if (
        summary.tasks,
        summary.source_admitted,
        summary.source_excluded,
        summary.development,
        summary.test,
    ) != (TEST_TASKS, TEST_TASKS, 0, 0, TEST_TASKS):
        raise ValueError("readiness is not the complete 61-task test cohort")
    matrix_binding, canonical_binding, _readiness_binding = sources
    if readiness.sources["matrix"] != matrix_binding:
        raise ValueError("test readiness matrix binding is stale")
    if readiness.sources["canonical"] != canonical_binding:
        raise ValueError("test readiness canonical binding is stale")

    if canonical.get("schema_version") != 2:
        raise ValueError("test canonical must use schema 2")
    if canonical.get("collection_profile") != "prefixbench-v1":
        raise ValueError("test canonical collection profile has changed")
    if canonical.get("dataset") != readiness.dataset:
        raise ValueError("test canonical dataset does not match readiness")
    if canonical.get("matrix_sha256") != matrix_binding.sha256:
        raise ValueError("test canonical matrix binding is stale")
    if matrix.schema_version != 1 or matrix.dataset != readiness.dataset:
        raise ValueError("test matrix contract has changed")
    if matrix_binding != protocol.matrix:
        raise ValueError("test matrix does not match the preregistered matrix")
    if len(rows) != TEST_TASKS or len(matrix.tasks) != TEST_TASKS:
        raise ValueError("test canonical and matrix must contain 61 tasks")
    names = tuple(task.name for task in matrix.tasks)
    if names != protocol.task_order:
        raise ValueError("test matrix order does not match the preregistration")
    if tuple(task.name for task in readiness.tasks) != names:
        raise ValueError("test readiness order does not match the matrix")

    for assessment, row, matrix_task in zip(
        readiness.tasks,
        rows,
        matrix.tasks,
        strict=True,
    ):
        _validate_test_task(
            assessment=assessment,
            row=row,
            matrix_task=matrix_task,
            dataset=readiness.dataset,
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
        raise ValueError("test cohort must contain exactly one producer")
    commit, tree, source_sha256 = next(iter(producers))
    producer = ProducerAttestation(
        commit=commit,
        tree=tree,
        source_sha256=source_sha256,
    )
    if producer.source_sha256 != protocol.producer.runtime_source_sha256:
        raise ValueError("test producer runtime source differs from the preregistration")
    return producer


def _validate_test_task(
    *,
    assessment: PrefixBenchTaskAssessmentV2,
    row: dict[str, Any],
    matrix_task: MatrixTask,
    dataset: str,
) -> None:
    if (
        assessment.execution_mode is not PrefixBenchExecutionMode.LIVE
        or assessment.split is not PrefixBenchSplit.TEST
        or not assessment.source_admission.admitted
        or assessment.source_admission.exclusion_reasons
        or assessment.journal_schema_version != 2
        or assessment.sources.journal is None
        or assessment.producer is None
    ):
        raise ValueError(f"task is not an admitted live test source: {assessment.name}")
    index = _required_int(row, "index")
    name = _required_string(row, "name")
    if (index, name) != (assessment.index, assessment.name):
        raise ValueError(f"test canonical identity does not match readiness: {assessment.name}")
    if matrix_task.name != assessment.name:
        raise ValueError(f"test matrix identity does not match readiness: {assessment.name}")
    if row.get("prefixbench_profile") != "prefixbench-v1":
        raise ValueError(f"test canonical task profile has changed: {assessment.name}")
    if _required_string(row, "run_dir") != TEST_RUN_ROOT.as_posix():
        raise ValueError(f"test canonical task uses another run root: {assessment.name}")

    result_path = _required_run_path(row, "result_path")
    if (
        assessment.sources.result.path != result_path
        or assessment.sources.result.sha256 != _required_string(row, "result_sha256")
    ):
        raise ValueError(
            f"test canonical result binding does not match readiness: {assessment.name}"
        )
    expected_config = (Path(result_path).parent / "config.json").as_posix()
    if (
        assessment.sources.config.path != expected_config
        or assessment.sources.config.sha256 != _required_string(row, "config_sha256")
    ):
        raise ValueError(
            f"test canonical config binding does not match readiness: {assessment.name}"
        )
    journal = assessment.sources.journal
    if journal.path != _required_run_path(
        row, "journal_path"
    ) or journal.sha256 != _required_string(row, "journal_sha256"):
        raise ValueError(
            f"test canonical journal binding does not match readiness: {assessment.name}"
        )
    row_producer = ProducerAttestation(
        commit=_required_string(row, "producer_commit"),
        tree=_required_string(row, "producer_tree"),
        source_sha256=_required_string(row, "producer_source_sha256"),
    )
    if row_producer != assessment.producer:
        raise ValueError(f"test canonical producer does not match readiness: {assessment.name}")
    split, bucket, identity = prefixbench_task_split(
        dataset=dataset,
        name=assessment.name,
        task_checksum=_required_string(row, "task_checksum"),
        task_git_url=_required_string(row, "task_git_url"),
        task_git_commit_id=_required_string(row, "task_git_commit_id"),
    )
    if (
        split is not PrefixBenchSplit.TEST
        or bucket != assessment.split_bucket
        or identity != assessment.task_identity_sha256
    ):
        raise ValueError(f"test task split identity does not match readiness: {assessment.name}")


def _load_collection_evidence(artifacts: _TestArtifacts) -> PrefixBenchTestCollectionEvidence:
    root = artifacts.root
    config_path = root / TEST_RUN_CONFIG
    progress_path = root / TEST_PROGRESS
    config_bytes = _read_regular_file(config_path, label="test run configuration")
    progress_bytes = _read_regular_file(progress_path, label="test progress")
    try:
        config = _RunConfiguration.model_validate_json(config_bytes)
    except ValidationError as exc:
        raise ValueError("invalid PrefixBench test run configuration") from exc
    protocol = artifacts.protocol.spec
    if config.matrix_sha256 != protocol.matrix.sha256:
        raise ValueError("test run matrix hash differs from the preregistration")
    if config.model != protocol.collection.model:
        raise ValueError("test run model differs from the preregistration")
    if config.agent_kwargs_sha256 != protocol.collection.agent_kwargs_sha256:
        raise ValueError("test run agent kwargs differ from the preregistration")
    if config.source_sha256 != protocol.collection.orchestration_source_sha256:
        raise ValueError("test run orchestration source differs from the preregistration")
    if config.start_at is not None:
        raise ValueError("test run must start from the first matrix task")
    if config.collection.profile != protocol.collection.profile:
        raise ValueError("test run collection profile differs from the preregistration")
    if config.collection.producer != artifacts.producer:
        raise ValueError("test run producer differs from the admitted cohort")

    expected_results = {
        _required_string(row, "name"): _result_path_relative_to_run(row)
        for row in artifacts.canonical_rows
    }
    completed_tasks = _validate_progress(
        progress_bytes,
        expected_names=protocol.task_order,
        expected_results=expected_results,
    )
    return PrefixBenchTestCollectionEvidence(
        run_config=_file_binding(config_path, config_bytes, root),
        progress=_file_binding(progress_path, progress_bytes, root),
        producer=config.collection.producer,
        completed_tasks=completed_tasks,
    )


def _validate_progress(
    data: bytes,
    *,
    expected_names: tuple[str, ...],
    expected_results: dict[str, str],
) -> tuple[str, ...]:
    records: list[dict[str, Any]] = []
    for line_number, raw in enumerate(data.splitlines(), start=1):
        if not raw:
            raise ValueError(f"blank test progress line: {line_number}")
        try:
            records.append(_json_object(raw, f"test progress line {line_number}"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid test progress line: {line_number}") from exc
    if not records:
        raise ValueError("test progress is empty")

    completed: list[str] = []
    active: tuple[int, str, str] | None = None
    started = False
    for line_number, record in enumerate(records, start=1):
        event = _required_string(record, "event")
        if event == "run_started":
            if _required_int(record, "total") != TEST_TASKS:
                raise ValueError(f"test progress total changed at line {line_number}")
            if _required_int(record, "completed") != len(completed):
                raise ValueError(f"test progress completed count changed at line {line_number}")
            if record.get("start_at") is not None or _required_int(record, "start_index") != 1:
                raise ValueError(f"test progress used a partial start at line {line_number}")
            active = None
            started = True
            continue
        if not started:
            raise ValueError("test progress must begin with run_started")
        if event == "task_launching":
            if active is not None:
                raise ValueError(f"test progress has overlapping launches at line {line_number}")
            if len(completed) >= TEST_TASKS:
                raise ValueError(f"test progress launched after completion at line {line_number}")
            index = _required_int(record, "index")
            task = _required_string(record, "task")
            job = _required_string(record, "job")
            expected_index = len(completed) + 1
            if (
                _required_int(record, "total") != TEST_TASKS
                or index != expected_index
                or task != expected_names[expected_index - 1]
            ):
                raise ValueError(f"test progress launch order changed at line {line_number}")
            active = index, task, job
            continue
        if event == "run_stopped":
            _require_matching_active(record, active, line_number)
            if "exit_code" not in record and "reason" not in record:
                raise ValueError(f"test progress stop lacks a reason at line {line_number}")
            active = None
            continue
        if event == "task_completed":
            index, task, _job = _require_matching_active(record, active, line_number)
            if "reward" not in record or "exception_type" not in record:
                raise ValueError(f"test progress completion is incomplete at line {line_number}")
            if _required_string(record, "result_path") != expected_results[task]:
                raise ValueError(f"test progress result path changed at line {line_number}")
            if index != len(completed) + 1:
                raise ValueError(f"test progress completion order changed at line {line_number}")
            completed.append(task)
            active = None
            continue
        if event == "run_completed":
            if active is not None:
                raise ValueError(
                    f"test progress completed with an active task at line {line_number}"
                )
            if (
                _required_int(record, "completed") != TEST_TASKS
                or _required_int(record, "total") != TEST_TASKS
                or _required_int(record, "skipped_before_start") != 0
                or tuple(completed) != expected_names
            ):
                raise ValueError(f"test progress final summary changed at line {line_number}")
            continue
        raise ValueError(f"unknown test progress event at line {line_number}: {event}")
    if records[-1].get("event") != "run_completed" or tuple(completed) != expected_names:
        raise ValueError("test progress does not end with the complete 61-task cohort")
    return tuple(completed)


def _require_matching_active(
    record: dict[str, Any],
    active: tuple[int, str, str] | None,
    line_number: int,
) -> tuple[int, str, str]:
    if active is None:
        raise ValueError(f"test progress event has no active launch at line {line_number}")
    actual = (
        _required_int(record, "index"),
        _required_string(record, "task"),
        _required_string(record, "job"),
    )
    if actual != active or _required_int(record, "total") != TEST_TASKS:
        raise ValueError(f"test progress event does not match its launch at line {line_number}")
    return actual


def _run_task(
    task: PrefixBenchTaskAssessmentV2,
    *,
    producer: ProducerAttestation,
    project_root: Path,
) -> PrefixBenchTestCampaignTask:
    journal = task.sources.journal
    if journal is None:
        raise ValueError(f"test task has no journal binding: {task.name}")
    path = _resolve_run_binding(journal, project_root)
    data = _read_regular_file(path, label=f"journal for {task.name}")
    if len(data) != journal.bytes or hashlib.sha256(data).hexdigest() != journal.sha256:
        raise ValueError(f"test journal binding is stale: {task.name}")
    prefix = load_state_prefix(
        data,
        source_commit=producer.commit,
        expected_journal_sha256=journal.sha256,
    )
    if prefix.through_line != len(prefix.events):
        raise AssertionError("full-journal test prefix did not retain every event")
    try:
        projected_attempts = len(project_completion_attempts(prefix.events))
    except TraceStructureError:
        projected_attempts = 0
    campaign = run_offline_campaign(prefix)
    return PrefixBenchTestCampaignTask(
        index=task.index,
        name=task.name,
        task_identity_sha256=task.task_identity_sha256,
        journal=journal,
        journal_lines=len(prefix.events),
        projected_attempts=projected_attempts,
        campaign=campaign,
    )


def _validate_producer_revision(
    project_root: Path,
    protocol: BoundPrefixBenchTestMutationProtocol,
    producer: ProducerAttestation,
) -> None:
    tree = _run_git(project_root, "show", "-s", "--format=%T", producer.commit).decode().strip()
    if tree != producer.tree:
        raise ValueError("test producer tree does not match its commit")
    _require_git_ancestor(project_root, protocol.preregistration_commit, producer.commit)
    for relative in (TEST_PROTOCOL, TEST_MATRIX, DEVELOPMENT_PROTOCOL):
        producer_bytes = _run_git(
            project_root,
            "show",
            f"{producer.commit}:{relative.as_posix()}",
        )
        current_bytes = _read_regular_file(
            project_root / relative, label=f"producer input {relative}"
        )
        if producer_bytes != current_bytes:
            raise ValueError(f"test producer does not contain the preregistered input: {relative}")


def _protocol_commit(project_root: Path, protocol_bytes: bytes) -> str:
    commit = (
        _run_git(
            project_root,
            "log",
            "-1",
            "--format=%H",
            "--",
            TEST_PROTOCOL.as_posix(),
        )
        .decode()
        .strip()
    )
    if not commit:
        raise ValueError("test mutation protocol has no Git history")
    committed = _run_git(project_root, "show", f"{commit}:{TEST_PROTOCOL.as_posix()}")
    if committed != protocol_bytes:
        raise ValueError("test mutation protocol history does not contain its current bytes")
    return commit


def _require_precollection_state(project_root: Path) -> None:
    existing = [
        relative
        for relative in (TEST_RUN_ROOT, TEST_CANONICAL, TEST_READINESS, TEST_CAMPAIGN)
        if (project_root / relative).exists()
    ]
    if existing:
        raise ValueError(
            "PrefixBench test preregistration must precede held-out artifacts: "
            + ", ".join(path.as_posix() for path in existing)
        )


def _raw_source_paths(
    artifacts: _TestArtifacts,
    collection: PrefixBenchTestCollectionEvidence,
) -> tuple[Path, ...]:
    task_paths = tuple(
        _resolve_run_binding(binding, artifacts.root)
        for task in artifacts.readiness.tasks
        for binding in (task.sources.result, task.sources.config, task.sources.journal)
        if binding is not None
    )
    return (
        *task_paths,
        _resolve_run_binding(collection.run_config, artifacts.root),
        _resolve_run_binding(collection.progress, artifacts.root),
    )


def _result_path_relative_to_run(row: dict[str, Any]) -> str:
    path = Path(_required_run_path(row, "result_path"))
    try:
        return path.relative_to(TEST_RUN_ROOT).as_posix()
    except ValueError as exc:
        raise ValueError(f"test result path uses another run root: {path}") from exc


def _required_run_path(row: dict[str, Any], field: str) -> str:
    raw = _required_string(row, field)
    relative = Path(raw)
    if relative.is_absolute() or not relative.is_relative_to(TEST_RUN_ROOT):
        raise ValueError(f"{field} is outside the fixed test run root: {raw}")
    return relative.as_posix()


def _resolve_run_binding(binding: PrefixBenchFileBinding, project_root: Path) -> Path:
    relative = Path(binding.path)
    if relative.is_absolute() or not relative.is_relative_to(TEST_RUN_ROOT):
        raise ValueError(f"bound source is outside the fixed test run root: {binding.path}")
    path = (project_root / relative).resolve()
    _require_project_path(path, project_root, label="test raw source")
    return path


def _canonical_rows(canonical: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    value = canonical.get("tasks")
    if not isinstance(value, list):
        raise ValueError("test canonical tasks must be a list")
    return tuple(_json_object(item, "test canonical task") for item in value)


def _orchestration_source_sha256(project_root: Path) -> str:
    paths = [
        *(project_root / relative for relative in _ORCHESTRATOR_SOURCE_PATHS),
        *sorted((project_root / "src" / "evidence_harness").rglob("*.py")),
    ]
    digest = hashlib.sha256()
    for path in paths:
        data = _read_regular_file(path, label=f"orchestration source {path}")
        digest.update(path.relative_to(project_root).as_posix().encode())
        digest.update(b"\0")
        digest.update(data)
        digest.update(b"\0")
    return digest.hexdigest()


def _agent_kwargs_sha256() -> str:
    payload = json.dumps(
        [],
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


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


def _project_root(project_root: Path) -> Path:
    root = project_root.resolve()
    if not root.is_dir():
        raise ValueError(f"project root is not a directory: {root}")
    actual = Path(_run_git(root, "rev-parse", "--show-toplevel").decode().strip()).resolve()
    if actual != root:
        raise ValueError("project root is not the Git top level")
    return root


def _require_project_path(path: Path, project_root: Path, *, label: str) -> None:
    try:
        path.relative_to(project_root)
    except ValueError as exc:
        raise ValueError(f"{label} path is outside the project: {path}") from exc


def _validate_committed_input(project_root: Path, relative: Path, data: bytes) -> None:
    committed = _run_git(project_root, "show", f"HEAD:{relative.as_posix()}")
    if committed != data:
        raise ValueError(f"test campaign input differs from Git HEAD: {relative}")


def _require_git_ancestor(project_root: Path, ancestor: str, descendant: str) -> None:
    completed = subprocess.run(
        ("git", "-C", str(project_root), "merge-base", "--is-ancestor", ancestor, descendant),
        check=False,
        capture_output=True,
    )
    if completed.returncode == 1:
        raise ValueError("test preregistration commit is not an ancestor of the producer")
    if completed.returncode:
        detail = completed.stderr.decode(errors="replace").strip()
        raise ValueError(f"cannot verify test preregistration ancestry: {detail}")


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


def _json_object(value: bytes | object, label: str) -> dict[str, Any]:
    parsed = json.loads(value) if isinstance(value, bytes) else value
    if not isinstance(parsed, dict):
        raise ValueError(f"{label} must be a JSON object")
    return parsed


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
