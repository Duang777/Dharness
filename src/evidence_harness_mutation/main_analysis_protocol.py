from __future__ import annotations

import hashlib
import json
import subprocess
from enum import StrEnum
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, ValidationError, model_validator

from evidence_harness_mutation.model import FrozenModel, InvariantId, Sha256
from evidence_harness_mutation.operators import MutationId
from evidence_harness_mutation.prefixbench import PrefixBenchFileBinding

DESIGN_BASE_COMMIT: Literal["f5c463b399201911ce9042db559f90f8a6d91337"] = (
    "f5c463b399201911ce9042db559f90f8a6d91337"
)
TEST_MATRIX_COMMIT: Literal["2e3e65868213238d9bbcdbf3e09ce4356c8edfd9"] = (
    "2e3e65868213238d9bbcdbf3e09ce4356c8edfd9"
)
TEST_PROTOCOL_COMMIT = DESIGN_BASE_COMMIT
MAIN_PROTOCOL_COMMIT = "b2a347ecda0206b01cd5d34de9edd7df1e284cb6"
FROZEN_TEST_SOURCE_SET_SHA256: Literal[
    "5f8c3b622f4b1e58a880489403c403c14a0886c4f5f537eb157da4fbd46dc632"
] = "5f8c3b622f4b1e58a880489403c403c14a0886c4f5f537eb157da4fbd46dc632"

MAIN_PROTOCOL = Path("experiments/prefixbench-v1/main-analysis-protocol-v1.json")
EXECUTABLE_PROTOCOL = Path("experiments/prefixbench-v1/main-analysis-executable-v1.json")
MINISWE_COHORT = Path("experiments/prefixbench-v1/miniswe-cohort-v1.json")

TEST_RUN_ROOT = Path("runs/terminal-bench-2/prefixbench-v1-test-20261002")
TEST_CANONICAL = Path("evaluation/prefixbench-v1-test-canonical.json")
TEST_READINESS = Path("evaluation/prefixbench-v1-test-readiness.json")
TEST_CAMPAIGN = Path("evaluation/prefixbench-v1-test-offline-campaign.json")
RQ2_REPORT = Path("evaluation/prefixbench-v1-test-method-comparison.json")
RQ3_REPORT = Path("evaluation/prefixbench-v1-test-reducer-comparison.json")
RQ4_REPORT = Path("evaluation/miniswe-agent-transfer-v1.json")
MAIN_REPORT = Path("evaluation/thesis-main-analysis-v1.json")
TB21_REPORT = Path("evaluation/thesis-tb21-sensitivity-v1.json")
AUXILIARY_REPORT = Path("evaluation/thesis-auxiliary-v1.json")

_KNOWN_INPUTS = (
    (
        "experiments/historical-7/manifest.json",
        "498959a371dca885792d2b8807c24808c88035c46534f55284b5ac1baf1bf20e",
    ),
    (
        "evaluation/historical-7.json",
        "bac8ac96373332473a5cc5011a43cd7a5d0aad3fca730617b062e60cb0077c97",
    ),
    (
        "evaluation/matrix-prefixbench-development.json",
        "f95bfc0ac1836b49dcab8323e6ff702586edc97c1f71f2986ae3c1e6645a6067",
    ),
    (
        "experiments/prefixbench-v1/mutation-protocol-v1.json",
        "360c09736d24597ffb4e452dd70b9d468a6213df2167d0e546420c22522cd377",
    ),
    (
        "evaluation/prefixbench-v1-development-offline-campaign.json",
        "395c10022f9682ff31246701e058a94aa983b1f236c9f3a6b164e8dc62c6aa83",
    ),
    (
        "evaluation/prefixbench-v1-development-offline-analysis.json",
        "5cac7e32f3540c511a782759efdd25c617a3113f40fb9b9566bc5a40fe3cddb7",
    ),
    (
        "evaluation/matrix-prefixbench-test.json",
        "a09d843fd1c4c356b7b48e5982655e9e20eded2fe394b7667ba86b00ce1826cb",
    ),
    (
        "experiments/prefixbench-v1/test-mutation-protocol-v1.json",
        "a6972d068a5595396ccea6bc3d3d66bc0d609c1596aeb16d73fa961e33000d2c",
    ),
)
_KNOWN_INPUT_AMENDMENTS = {
    "experiments/prefixbench-v1/test-mutation-protocol-v1.json": (
        "a6972d068a5595396ccea6bc3d3d66bc0d609c1596aeb16d73fa961e33000d2c"
    ),
}

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
_PROTECTED_SOURCE_AMENDMENTS = {
    "src/evidence_harness/protocol.py": (
        "07837a2f7cfc108e8e492ad6950a32c813fb9a2d37851d4f4c725991739a0157"
    ),
    "src/evidence_harness_mutation/prefixbench_test_campaign.py": (
        "13a966708af562c16e3169be748d2171e969e6af2d9d918f49c7383829a3c27e"
    ),
}

_IMPLEMENTATION_SOURCE_PATHS = (
    "docs/thesis-execution-plan.md",
    "docs/thesis-main-experiment-spec.md",
    "scripts/main_analysis_protocol.py",
    "src/evidence_harness_mutation/main_analysis_protocol.py",
    "tests/mutation/test_main_analysis_protocol.py",
    "tests/test_main_analysis_protocol_script.py",
)

_OUTCOME_PATHS = (
    TEST_RUN_ROOT.as_posix(),
    TEST_CANONICAL.as_posix(),
    TEST_READINESS.as_posix(),
    TEST_CAMPAIGN.as_posix(),
    RQ2_REPORT.as_posix(),
    RQ3_REPORT.as_posix(),
    RQ4_REPORT.as_posix(),
    MAIN_REPORT.as_posix(),
    TB21_REPORT.as_posix(),
    AUXILIARY_REPORT.as_posix(),
)

_OPERATOR_INVARIANTS = (
    (MutationId.STALE_EVIDENCE_EPOCH, InvariantId.I1),
    (MutationId.REORDER_CHECK_RECEIPTS, InvariantId.I2),
    (MutationId.REVIEW_TIMEOUT_FALLBACK, InvariantId.I3),
    (MutationId.CROSS_CANDIDATE_EVIDENCE, InvariantId.I4),
)


class MainAnalysisMethod(StrEnum):
    STATE_AWARE = "state_aware"
    RANDOM_JSON = "random_json"
    SCHEMA_VALID_RANDOM = "schema_valid_random"
    AGENTCHAOS_STYLE = "agentchaos_style"
    STATELESS_SEMANTIC = "stateless_semantic"


class MainAnalysisOutcome(StrEnum):
    INPUT_REJECTED = "input_rejected"
    TARGET_NOT_REACHED = "target_not_reached"
    ORACLE_INVALID = "oracle_invalid"
    ORACLE_EQUIVALENT = "oracle_equivalent"
    TARGET_VIOLATION = "target_violation"
    OTHER_ORACLE_CHANGE = "other_oracle_change"


class MainAnalysisReducer(StrEnum):
    NO_REDUCTION = "no_reduction"
    FLAT_DDMIN = "flat_ddmin"
    HDD = "hdd"
    SOURCE_ANCHORED = "source_anchored"


class MainAnalysisCanonicalJson(FrozenModel):
    ensure_ascii: Literal[True] = True
    indent: Literal[2] = 2
    sort_keys: Literal[True] = True
    trailing_newline: Literal[True] = True


class MainAnalysisSourceSet(FrozenModel):
    algorithm: Literal["sha256-length-framed-path-content-v1"] = (
        "sha256-length-framed-path-content-v1"
    )
    bytes: int = Field(ge=1)
    sha256: Sha256
    files: tuple[PrefixBenchFileBinding, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_files(self) -> Self:
        paths = tuple(file.path for file in self.files)
        if len(paths) != len(set(paths)):
            raise ValueError("source-set paths must be unique")
        if self.bytes != sum(file.bytes for file in self.files):
            raise ValueError("source-set byte count is stale")
        return self


class MainAnalysisPaths(FrozenModel):
    protocol: Literal["experiments/prefixbench-v1/main-analysis-protocol-v1.json"] = (
        "experiments/prefixbench-v1/main-analysis-protocol-v1.json"
    )
    executable_protocol: Literal["experiments/prefixbench-v1/main-analysis-executable-v1.json"] = (
        "experiments/prefixbench-v1/main-analysis-executable-v1.json"
    )
    miniswe_cohort: Literal["experiments/prefixbench-v1/miniswe-cohort-v1.json"] = (
        "experiments/prefixbench-v1/miniswe-cohort-v1.json"
    )
    test_run_root: Literal["runs/terminal-bench-2/prefixbench-v1-test-20261002"] = (
        "runs/terminal-bench-2/prefixbench-v1-test-20261002"
    )
    test_canonical: Literal["evaluation/prefixbench-v1-test-canonical.json"] = (
        "evaluation/prefixbench-v1-test-canonical.json"
    )
    test_readiness: Literal["evaluation/prefixbench-v1-test-readiness.json"] = (
        "evaluation/prefixbench-v1-test-readiness.json"
    )
    test_campaign: Literal["evaluation/prefixbench-v1-test-offline-campaign.json"] = (
        "evaluation/prefixbench-v1-test-offline-campaign.json"
    )
    rq2_report: Literal["evaluation/prefixbench-v1-test-method-comparison.json"] = (
        "evaluation/prefixbench-v1-test-method-comparison.json"
    )
    rq3_report: Literal["evaluation/prefixbench-v1-test-reducer-comparison.json"] = (
        "evaluation/prefixbench-v1-test-reducer-comparison.json"
    )
    rq4_report: Literal["evaluation/miniswe-agent-transfer-v1.json"] = (
        "evaluation/miniswe-agent-transfer-v1.json"
    )
    main_report: Literal["evaluation/thesis-main-analysis-v1.json"] = (
        "evaluation/thesis-main-analysis-v1.json"
    )
    tb21_report: Literal["evaluation/thesis-tb21-sensitivity-v1.json"] = (
        "evaluation/thesis-tb21-sensitivity-v1.json"
    )
    auxiliary_report: Literal["evaluation/thesis-auxiliary-v1.json"] = (
        "evaluation/thesis-auxiliary-v1.json"
    )


class MainAnalysisChronology(FrozenModel):
    design_base_commit: Literal["f5c463b399201911ce9042db559f90f8a6d91337"] = DESIGN_BASE_COMMIT
    test_matrix_commit: Literal["2e3e65868213238d9bbcdbf3e09ce4356c8edfd9"] = TEST_MATRIX_COMMIT
    test_protocol_commit: Literal["f5c463b399201911ce9042db559f90f8a6d91337"] = TEST_PROTOCOL_COMMIT
    freeze_gate: Literal["filesystem-absence-then-all-refs-path-history"] = (
        "filesystem-absence-then-all-refs-path-history"
    )
    protocol_policy: Literal["protocol-commit-ancestor-of-every-outcome-producer"] = (
        "protocol-commit-ancestor-of-every-outcome-producer"
    )
    executable_policy: Literal[
        "executable-manifest-commit-ancestor-of-every-confirmatory-outcome-producer"
    ] = "executable-manifest-commit-ancestor-of-every-confirmatory-outcome-producer"
    protected_source_policy: Literal["test-source-set-byte-identical-to-design-base"] = (
        "test-source-set-byte-identical-to-design-base"
    )
    claim: Literal["repository-enforced-protocol-and-implementation-precede-bound-outcomes"] = (
        "repository-enforced-protocol-and-implementation-precede-bound-outcomes"
    )
    external_non_observation_claim: Literal["not_provable"] = "not_provable"


class MainAnalysisDevelopmentBoundary(FrozenModel):
    role: Literal["calibration-only-descriptive"] = "calibration-only-descriptive"
    claim_status: Literal["descriptive_not_evaluated"] = "descriptive_not_evaluated"
    pooled_into_main_analysis: Literal[False] = False
    post_protocol_model_selection: Literal["forbidden"] = "forbidden"
    required_not_evaluated_claims: tuple[str, ...] = (
        "container_call_telemetry",
        "inferential_statistics",
        "model_call_telemetry",
        "phase_target_reachability",
        "rq2_baseline_comparison",
        "rq3_production_mutation_score",
        "rq4_live_cost_savings",
        "test_split_generalization",
    )


class MainAnalysisRQ1(FrozenModel):
    question: Literal["historical_7"] = "historical_7"
    evidence_role: Literal["locked_retrospective"] = "locked_retrospective"
    unit: Literal["historical_case_pair"] = "historical_case_pair"
    population: Literal[7] = 7
    pair_success: Literal["vulnerable-survived-and-fixed-killed"] = (
        "vulnerable-survived-and-fixed-killed"
    )
    engineering_threshold: Literal["at-least-6-of-7-pairs"] = "at-least-6-of-7-pairs"
    inferential_test: Literal["none"] = "none"


class MainAnalysisOperator(FrozenModel):
    operator: MutationId
    target_invariant: InvariantId


class MainAnalysisDeterministicChoice(FrozenModel):
    algorithm: Literal["sha256-first-8-bytes-mod-n"] = "sha256-first-8-bytes-mod-n"
    namespace: Literal["thesis-main-analysis-v1"] = "thesis-main-analysis-v1"
    seed: Literal[20261003] = 20261003
    replicate_count: Literal[1] = 1
    redraw_after_failure: Literal[False] = False


class MainAnalysisRQ2(FrozenModel):
    question: Literal["state_aware_vs_four_baselines"] = "state_aware_vs_four_baselines"
    dataset: Literal["terminal-bench@2.0"] = "terminal-bench@2.0"
    split: Literal["test"] = "test"
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
    unit: Literal["task"] = "task"
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
    complete_cohort_required: Literal[True] = True
    choices: MainAnalysisDeterministicChoice = MainAnalysisDeterministicChoice()

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        if self.methods != tuple(MainAnalysisMethod):
            raise ValueError("RQ2 method order has changed")
        expected_operators = tuple(
            MainAnalysisOperator(operator=operator, target_invariant=invariant)
            for operator, invariant in _OPERATOR_INVARIANTS
        )
        if self.operators != expected_operators:
            raise ValueError("RQ2 operator order has changed")
        if self.outcomes != tuple(MainAnalysisOutcome):
            raise ValueError("RQ2 outcome order has changed")
        if self.comparison_order != (
            "random_json",
            "schema_valid_random",
            "agentchaos_style",
            "stateless_semantic",
        ):
            raise ValueError("RQ2 comparison order has changed")
        return self


class MainAnalysisWitness(FrozenModel):
    protected_anchors: tuple[
        Literal["run_started", "proposal", "verified_terminal", "mutation_target"],
        ...,
    ] = (
        "run_started",
        "proposal",
        "verified_terminal",
        "mutation_target",
    )
    same_attempt: Literal[True] = True
    same_terminal: Literal[True] = True
    same_invariant: Literal[True] = True
    same_ordered_details: Literal[True] = True
    deterministic_audits: Literal[3] = 3


class MainAnalysisRQ3(FrozenModel):
    question: Literal["reducer_comparison"] = "reducer_comparison"
    population: Literal["all-rq2-state-aware-target-violations"] = (
        "all-rq2-state-aware-target-violations"
    )
    reducers: tuple[MainAnalysisReducer, ...] = tuple(MainAnalysisReducer)
    common_input: Literal["same-unreduced-applied-mutation"] = "same-unreduced-applied-mutation"
    witness: MainAnalysisWitness = MainAnalysisWitness()
    size_order: tuple[
        Literal["event_count", "payload_member_count", "compact_canonical_json_bytes"],
        ...,
    ] = (
        "event_count",
        "payload_member_count",
        "compact_canonical_json_bytes",
    )
    primary_metric: Literal["task-pooled-retained-byte-fraction"] = (
        "task-pooled-retained-byte-fraction"
    )
    failed_reduction_rule: Literal["retain-full-input-size-and-record-failure"] = (
        "retain-full-input-size-and-record-failure"
    )
    test: Literal["two-sided-exact-wilcoxon-signed-rank"] = "two-sided-exact-wilcoxon-signed-rank"
    effect: Literal["paired-rank-biserial"] = "paired-rank-biserial"
    interval: Literal["task-cluster-bootstrap-percentile-95"] = (
        "task-cluster-bootstrap-percentile-95"
    )
    bootstrap_resamples: Literal[10000] = 10000
    correction: Literal["holm-three-contrasts-alpha-0.05"] = "holm-three-contrasts-alpha-0.05"
    comparison_order: tuple[
        Literal["no_reduction", "flat_ddmin", "hdd"],
        ...,
    ] = ("no_reduction", "flat_ddmin", "hdd")
    minimum_counterexamples: Literal[10] = 10
    minimum_tasks: Literal[5] = 5
    insufficient_rule: Literal["descriptive-only-no-p-values"] = "descriptive-only-no-p-values"
    audit_calls_role: Literal["secondary"] = "secondary"

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        if self.reducers != tuple(MainAnalysisReducer):
            raise ValueError("RQ3 reducer order has changed")
        if self.size_order != (
            "event_count",
            "payload_member_count",
            "compact_canonical_json_bytes",
        ):
            raise ValueError("RQ3 size order has changed")
        if self.comparison_order != ("no_reduction", "flat_ddmin", "hdd"):
            raise ValueError("RQ3 comparison order has changed")
        return self


class MainAnalysisTransferFamily(FrozenModel):
    source_invariant: InvariantId
    target: str | None
    status: Literal["mapped", "unsupported_by_design"]

    @model_validator(mode="after")
    def validate_mapping(self) -> Self:
        if self.status == "mapped" and self.target is None:
            raise ValueError("mapped transfer family requires a target")
        if self.status == "unsupported_by_design" and self.target is not None:
            raise ValueError("unsupported transfer family cannot have a target")
        return self


class MainAnalysisRQ4(FrozenModel):
    question: Literal["mini_swe_agent_transfer"] = "mini_swe_agent_transfer"
    repository: Literal["https://github.com/SWE-agent/mini-swe-agent"] = (
        "https://github.com/SWE-agent/mini-swe-agent"
    )
    commit: Literal["04d809ceab9df28f9adaed044884180159172930"] = (
        "04d809ceab9df28f9adaed044884180159172930"
    )
    trajectory_format: Literal["mini-swe-agent-1.1"] = "mini-swe-agent-1.1"
    benchmark: Literal["ProgramBench"] = "ProgramBench"
    tasks: Literal[20] = 20
    selection: Literal["sha256-ranked-instance-id-first-20"] = "sha256-ranked-instance-id-first-20"
    retry_policy: Literal["resume-interrupted-only-no-outcome-retry"] = (
        "resume-interrupted-only-no-outcome-retry"
    )
    families: tuple[MainAnalysisTransferFamily, ...] = (
        MainAnalysisTransferFamily(
            source_invariant=InvariantId.I1,
            target="workspace-change-invalidates-prior-observation-or-candidate-evidence",
            status="mapped",
        ),
        MainAnalysisTransferFamily(
            source_invariant=InvariantId.I2,
            target="action-observation-association-preserves-order-and-identity",
            status="mapped",
        ),
        MainAnalysisTransferFamily(
            source_invariant=InvariantId.I3,
            target=None,
            status="unsupported_by_design",
        ),
        MainAnalysisTransferFamily(
            source_invariant=InvariantId.I4,
            target="submission-terminal-binds-current-workspace-tree",
            status="mapped",
        ),
    )
    original_family_denominator: Literal[4] = 4
    mapped_family_denominator: Literal[3] = 3
    minimum_applicable_tasks_per_mapped_family: Literal[5] = 5
    minimum_violation_tasks_per_mapped_family: Literal[2] = 2
    deterministic_audits: Literal[3] = 3
    inferential_test: Literal["none"] = "none"
    field_name_reuse: Literal["forbidden"] = "forbidden"

    @model_validator(mode="after")
    def validate_families(self) -> Self:
        if tuple(family.source_invariant for family in self.families) != tuple(InvariantId):
            raise ValueError("RQ4 family order has changed")
        if tuple(family.status for family in self.families) != (
            "mapped",
            "mapped",
            "unsupported_by_design",
            "mapped",
        ):
            raise ValueError("RQ4 family mapping has changed")
        return self


class MainAnalysisOptionalEvidence(FrozenModel):
    terminal_bench_2_1: Literal["sensitivity-only-separate-protocol-not-pooled"] = (
        "sensitivity-only-separate-protocol-not-pooled"
    )
    production_mutation_score: Literal["optional-auxiliary-separate-protocol-or-not_evaluated"] = (
        "optional-auxiliary-separate-protocol-or-not_evaluated"
    )
    live_cost: Literal["optional-auxiliary-separate-protocol-or-not_evaluated"] = (
        "optional-auxiliary-separate-protocol-or-not_evaluated"
    )
    affects_main_conclusions: Literal[False] = False
    affects_multiplicity_families: Literal[False] = False


class MainAnalysisProtocol(FrozenModel):
    schema_version: Literal[1] = 1
    protocol_id: Literal["thesis-main-analysis-v1"] = "thesis-main-analysis-v1"
    paths: MainAnalysisPaths
    chronology: MainAnalysisChronology
    canonical_json: MainAnalysisCanonicalJson
    known_inputs: tuple[PrefixBenchFileBinding, ...] = Field(
        min_length=len(_KNOWN_INPUTS),
        max_length=len(_KNOWN_INPUTS),
    )
    protected_test_source_set_sha256: Literal[
        "5f8c3b622f4b1e58a880489403c403c14a0886c4f5f537eb157da4fbd46dc632"
    ] = FROZEN_TEST_SOURCE_SET_SHA256
    protected_sources: MainAnalysisSourceSet
    protocol_sources: MainAnalysisSourceSet
    planned_outcomes: tuple[str, ...] = Field(
        min_length=len(_OUTCOME_PATHS),
        max_length=len(_OUTCOME_PATHS),
    )
    executable_freeze: Literal["required-before-held-out-or-cross-harness-outcome"] = (
        "required-before-held-out-or-cross-harness-outcome"
    )
    rq_order: tuple[Literal["RQ1", "RQ2", "RQ3", "RQ4"], ...] = (
        "RQ1",
        "RQ2",
        "RQ3",
        "RQ4",
    )
    development: MainAnalysisDevelopmentBoundary
    rq1: MainAnalysisRQ1
    rq2: MainAnalysisRQ2
    rq3: MainAnalysisRQ3
    rq4: MainAnalysisRQ4
    optional_evidence: MainAnalysisOptionalEvidence

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        actual_known = tuple((item.path, item.sha256) for item in self.known_inputs)
        if actual_known != _KNOWN_INPUTS:
            raise ValueError("main-analysis known inputs have changed")
        if tuple(item.path for item in self.protected_sources.files) != _PROTECTED_SOURCE_PATHS:
            raise ValueError("protected test source paths have changed")
        if self.protected_sources.sha256 != self.protected_test_source_set_sha256:
            raise ValueError("protected test source-set hash has changed")
        if tuple(item.path for item in self.protocol_sources.files) != (
            _IMPLEMENTATION_SOURCE_PATHS
        ):
            raise ValueError("main-analysis protocol source paths have changed")
        if self.planned_outcomes != _OUTCOME_PATHS:
            raise ValueError("main-analysis planned outcome paths have changed")
        if len(set(self.planned_outcomes)) != len(self.planned_outcomes):
            raise ValueError("main-analysis planned outcome paths must be unique")
        source_paths = {
            item.path for item in (*self.protected_sources.files, *self.protocol_sources.files)
        }
        if source_paths & set(self.planned_outcomes):
            raise ValueError("outcome path cannot be part of a source set")
        return self

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.model_dump(mode="json"))


class BoundMainAnalysisProtocol(FrozenModel):
    file: PrefixBenchFileBinding
    spec: MainAnalysisProtocol
    preregistration_commit: str = Field(pattern=r"^[0-9a-f]{40}$")

    @model_validator(mode="after")
    def validate_path(self) -> Self:
        if self.file.path != MAIN_PROTOCOL.as_posix():
            raise ValueError("main-analysis protocol path has changed")
        return self


class MainAnalysisPreflight(FrozenModel):
    schema_version: Literal[1] = 1
    ready_for_held_out_collection: Literal[True] = True
    protocol: BoundMainAnalysisProtocol
    protected_source_sha256: Sha256
    protocol_source_sha256: Sha256
    planned_outcomes: tuple[str, ...]


def freeze_main_analysis_protocol(project_root: Path) -> MainAnalysisProtocol:
    root = _project_root(project_root)
    _require_preoutcome_state(root)
    return _current_protocol(root)


def load_main_analysis_protocol(project_root: Path) -> BoundMainAnalysisProtocol:
    root = _project_root(project_root)
    path = root / MAIN_PROTOCOL
    data = _read_regular_file(path, label="main-analysis protocol")
    try:
        spec = MainAnalysisProtocol.model_validate_json(data)
    except ValidationError as exc:
        raise ValueError("invalid main-analysis protocol") from exc
    if data != spec.canonical_bytes():
        raise ValueError("main-analysis protocol is not canonical JSON")
    if spec != _current_protocol(root):
        raise ValueError("main-analysis protocol source binding is stale")
    _validate_committed_input(root, MAIN_PROTOCOL, data)
    for binding in (
        *spec.known_inputs,
        *spec.protected_sources.files,
        *spec.protocol_sources.files,
    ):
        source = _read_regular_file(root / binding.path, label=f"bound source {binding.path}")
        _validate_binding(binding, source)
        _validate_committed_input(root, Path(binding.path), source)
    commit = _first_protocol_commit(root, data)
    head = _git_text(root, "rev-parse", "HEAD")
    _require_git_ancestor(root, commit, head)
    return BoundMainAnalysisProtocol(
        file=_file_binding(path, data, root),
        spec=spec,
        preregistration_commit=commit,
    )


def preflight_main_analysis_protocol(project_root: Path) -> MainAnalysisPreflight:
    root = _project_root(project_root)
    _require_preoutcome_state(root)
    protocol = load_main_analysis_protocol(root)
    return MainAnalysisPreflight(
        protocol=protocol,
        protected_source_sha256=protocol.spec.protected_sources.sha256,
        protocol_source_sha256=protocol.spec.protocol_sources.sha256,
        planned_outcomes=protocol.spec.planned_outcomes,
    )


def check_main_analysis_protocol(project_root: Path) -> tuple[str, ...]:
    try:
        load_main_analysis_protocol(project_root)
    except (OSError, ValueError) as exc:
        return (f"invalid main-analysis protocol: {exc}",)
    return ()


def _current_protocol(project_root: Path) -> MainAnalysisProtocol:
    known_inputs = tuple(
        _read_frozen_binding(project_root, Path(path), expected_sha256)
        for path, expected_sha256 in _KNOWN_INPUTS
    )
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
    protocol_sources = _source_set(project_root, _IMPLEMENTATION_SOURCE_PATHS)
    return MainAnalysisProtocol(
        paths=MainAnalysisPaths(),
        chronology=MainAnalysisChronology(),
        canonical_json=MainAnalysisCanonicalJson(),
        known_inputs=known_inputs,
        protected_sources=protected_sources,
        protocol_sources=protocol_sources,
        planned_outcomes=_OUTCOME_PATHS,
        development=MainAnalysisDevelopmentBoundary(),
        rq1=MainAnalysisRQ1(),
        rq2=MainAnalysisRQ2(),
        rq3=MainAnalysisRQ3(),
        rq4=MainAnalysisRQ4(),
        optional_evidence=MainAnalysisOptionalEvidence(),
    )


def _require_preoutcome_state(project_root: Path) -> None:
    existing = tuple(path for path in _OUTCOME_PATHS if (project_root / path).exists())
    if existing:
        raise ValueError(
            "main-analysis preregistration must precede outcome artifacts: " + ", ".join(existing)
        )
    historical = tuple(path for path in _OUTCOME_PATHS if _path_has_git_history(project_root, path))
    if historical:
        raise ValueError(
            "main-analysis outcome path already exists in Git history: " + ", ".join(historical)
        )


def _path_has_git_history(project_root: Path, path: str) -> bool:
    return bool(_git_text(project_root, "log", "--all", "--format=%H", "--", path))


def _read_frozen_binding(
    project_root: Path,
    relative: Path,
    expected_sha256: str,
) -> PrefixBenchFileBinding:
    data = _read_regular_file(project_root / relative, label=f"known input {relative}")
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected_sha256:
        raise ValueError(f"known input hash has changed: {relative}")
    if _KNOWN_INPUT_AMENDMENTS.get(relative.as_posix()) != actual:
        _validate_committed_input(project_root, relative, data)
    return _file_binding(project_root / relative, data, project_root)


def _source_set(project_root: Path, paths: tuple[str, ...]) -> MainAnalysisSourceSet:
    digest = hashlib.sha256()
    bindings: list[PrefixBenchFileBinding] = []
    total_bytes = 0
    for relative in paths:
        data = _read_regular_file(
            project_root / relative,
            label=f"main-analysis source {relative}",
        )
        encoded_path = relative.encode()
        digest.update(len(encoded_path).to_bytes(8, "big"))
        digest.update(encoded_path)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
        total_bytes += len(data)
        bindings.append(_file_binding(project_root / relative, data, project_root))
    return MainAnalysisSourceSet(
        bytes=total_bytes,
        sha256=digest.hexdigest(),
        files=tuple(bindings),
    )


def _first_protocol_commit(project_root: Path, protocol_bytes: bytes) -> str:
    if _git_path_or_none(project_root, MAIN_PROTOCOL_COMMIT, MAIN_PROTOCOL) is None:
        raise ValueError("main-analysis protocol origin is absent from Git history")
    if not protocol_bytes:
        raise ValueError("main-analysis protocol is empty")
    return MAIN_PROTOCOL_COMMIT


def _git_path_or_none(project_root: Path, commit: str, path: Path) -> bytes | None:
    completed = subprocess.run(
        ("git", "-C", str(project_root), "show", f"{commit}:{path.as_posix()}"),
        check=False,
        capture_output=True,
    )
    if completed.returncode == 0:
        return completed.stdout
    return None


def _validate_committed_input(project_root: Path, relative: Path, data: bytes) -> None:
    committed = _run_git(project_root, "show", f"HEAD:{relative.as_posix()}")
    if committed != data:
        raise ValueError(f"main-analysis input differs from Git HEAD: {relative}")


def _validate_binding(binding: PrefixBenchFileBinding, data: bytes) -> None:
    if len(data) != binding.bytes or hashlib.sha256(data).hexdigest() != binding.sha256:
        raise ValueError(f"file binding is stale: {binding.path}")


def _file_binding(
    path: Path,
    data: bytes,
    project_root: Path,
) -> PrefixBenchFileBinding:
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(project_root)
    except ValueError as exc:
        raise ValueError(f"source path is outside the project: {path}") from exc
    return PrefixBenchFileBinding(
        path=relative.as_posix(),
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
    actual = Path(_git_text(root, "rev-parse", "--show-toplevel")).resolve()
    if actual != root:
        raise ValueError("project root is not the Git top level")
    return root


def _require_git_ancestor(project_root: Path, ancestor: str, descendant: str) -> None:
    completed = subprocess.run(
        ("git", "-C", str(project_root), "merge-base", "--is-ancestor", ancestor, descendant),
        check=False,
        capture_output=True,
    )
    if completed.returncode == 1:
        raise ValueError("main-analysis protocol commit is not an ancestor of HEAD")
    if completed.returncode:
        detail = completed.stderr.decode(errors="replace").strip()
        raise ValueError(f"cannot verify main-analysis protocol ancestry: {detail}")


def _git_text(project_root: Path, *args: str) -> str:
    return _run_git(project_root, *args).decode(errors="strict").strip()


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
