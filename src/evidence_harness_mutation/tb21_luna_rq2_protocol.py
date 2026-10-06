from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, ValidationError, model_validator

from evidence_harness_mutation.main_analysis_protocol import (
    MainAnalysisCanonicalJson,
    MainAnalysisMethod,
    MainAnalysisOperator,
    MainAnalysisOutcome,
    MainAnalysisSourceSet,
)
from evidence_harness_mutation.model import FrozenModel, InvariantId, Sha256
from evidence_harness_mutation.operators import MutationId
from evidence_harness_mutation.prefixbench import PrefixBenchFileBinding
from evidence_harness_mutation.tb21_sensitivity_protocol import (
    SENSITIVITY_MATRIX,
    SENSITIVITY_PROTOCOL,
    Tb21RepositorySnapshot,
    Tb21SensitivityMatrix,
    _file_binding,
    _first_matching_commit,
    _project_root,
    _read_regular_file,
    _require_git_ancestor,
    _require_unused_paths,
    _source_set,
    _validate_binding,
    _validate_committed_input,
    load_tb21_sensitivity_protocol,
)

PROTOCOL_ID: Literal["thesis-tb21-luna-rq2-replication-v1"] = (
    "thesis-tb21-luna-rq2-replication-v1"
)
MODEL: Literal["openai/modelhub/gpt-5.6-luna"] = "openai/modelhub/gpt-5.6-luna"
CHOICE_NAMESPACE: Literal["thesis-tb21-luna-rq2-replication-v1"] = PROTOCOL_ID
BOOTSTRAP_NAMESPACE: Literal[
    "thesis-tb21-luna-rq2-replication-v1/bootstrap-rq2-v1"
] = "thesis-tb21-luna-rq2-replication-v1/bootstrap-rq2-v1"
PROVIDER_ENDPOINT_SHA256: Literal[
    "42c964b5536d7dd77320e537c8717a08c7397f93e3398db3e7dc1ef923400dac"
] = "42c964b5536d7dd77320e537c8717a08c7397f93e3398db3e7dc1ef923400dac"
COHORT_ORIGIN_COMMIT: Literal["69f7fb2da64f3f465f1b72a43469007e2cf1cc2a"] = (
    "69f7fb2da64f3f465f1b72a43469007e2cf1cc2a"
)

LUNA_PROTOCOL = Path(
    "experiments/prefixbench-v1/tb21-luna-rq2-replication-protocol-v1.json"
)
LUNA_EXECUTABLE = Path(
    "experiments/prefixbench-v1/tb21-luna-rq2-replication-executable-v1.json"
)
LUNA_RUN_ROOT = Path(
    "runs/terminal-bench-2-1/prefixbench-v1-luna-rq2-replication-v1"
)
LUNA_CANONICAL = Path("evaluation/prefixbench-v1-tb21-luna-rq2-canonical-v1.json")
LUNA_READINESS = Path("evaluation/prefixbench-v1-tb21-luna-rq2-readiness-v1.json")
LUNA_CAMPAIGN = Path(
    "evaluation/prefixbench-v1-tb21-luna-rq2-offline-campaign-v1.json"
)
LUNA_REPORT = Path("evaluation/thesis-tb21-luna-rq2-replication-v1.json")

LUNA_OUTCOME_PATHS = (
    LUNA_RUN_ROOT.as_posix(),
    LUNA_CANONICAL.as_posix(),
    LUNA_READINESS.as_posix(),
    LUNA_CAMPAIGN.as_posix(),
    LUNA_REPORT.as_posix(),
)
_PREFREEZE_PATHS = (LUNA_EXECUTABLE.as_posix(), *LUNA_OUTCOME_PATHS)
_PROTOCOL_SOURCE_PATHS = (
    "docs/tb21-luna-rq2-replication-protocol.md",
    "scripts/tb21_luna_rq2_protocol.py",
    "src/evidence_harness_mutation/tb21_luna_rq2_protocol.py",
    "tests/mutation/test_tb21_luna_rq2_protocol.py",
    "tests/test_tb21_luna_rq2_protocol_script.py",
)
_IMPORTED_PROTOCOL_SOURCE_PATHS = (
    "src/evidence_harness_mutation/tb21_sensitivity_protocol.py",
)
_OPERATOR_INVARIANTS = (
    (MutationId.STALE_EVIDENCE_EPOCH, InvariantId.I1),
    (MutationId.REORDER_CHECK_RECEIPTS, InvariantId.I2),
    (MutationId.REVIEW_TIMEOUT_FALLBACK, InvariantId.I3),
    (MutationId.CROSS_CANDIDATE_EVIDENCE, InvariantId.I4),
)


class LunaRq2Paths(FrozenModel):
    cohort: Literal["evaluation/matrix-prefixbench-tb21-sensitivity.json"] = (
        SENSITIVITY_MATRIX.as_posix()
    )
    protocol: Literal[
        "experiments/prefixbench-v1/tb21-luna-rq2-replication-protocol-v1.json"
    ] = LUNA_PROTOCOL.as_posix()
    executable: Literal[
        "experiments/prefixbench-v1/tb21-luna-rq2-replication-executable-v1.json"
    ] = LUNA_EXECUTABLE.as_posix()
    raw: Literal[
        "runs/terminal-bench-2-1/prefixbench-v1-luna-rq2-replication-v1"
    ] = LUNA_RUN_ROOT.as_posix()
    canonical: Literal[
        "evaluation/prefixbench-v1-tb21-luna-rq2-canonical-v1.json"
    ] = LUNA_CANONICAL.as_posix()
    readiness: Literal[
        "evaluation/prefixbench-v1-tb21-luna-rq2-readiness-v1.json"
    ] = LUNA_READINESS.as_posix()
    campaign: Literal[
        "evaluation/prefixbench-v1-tb21-luna-rq2-offline-campaign-v1.json"
    ] = LUNA_CAMPAIGN.as_posix()
    final_report: Literal[
        "evaluation/thesis-tb21-luna-rq2-replication-v1.json"
    ] = LUNA_REPORT.as_posix()


class LunaDeterministicChoice(FrozenModel):
    algorithm: Literal["sha256-first-8-bytes-mod-n"] = (
        "sha256-first-8-bytes-mod-n"
    )
    namespace: Literal["thesis-tb21-luna-rq2-replication-v1"] = CHOICE_NAMESPACE
    seed: Literal[20261003] = 20261003
    replicate_count: Literal[1] = 1
    redraw_after_failure: Literal[False] = False


class LunaRq2Analysis(FrozenModel):
    question: Literal["rq2-independent-luna-replication-on-frozen-tb21-cohort"] = (
        "rq2-independent-luna-replication-on-frozen-tb21-cohort"
    )
    role: Literal["luna-specific-replication-not-dataset-sensitivity"] = (
        "luna-specific-replication-not-dataset-sensitivity"
    )
    model: Literal["openai/modelhub/gpt-5.6-luna"] = MODEL
    dataset: Literal["terminal-bench-2-1"] = "terminal-bench-2-1"
    tasks: Literal[61] = 61
    methods: tuple[MainAnalysisMethod, ...] = tuple(MainAnalysisMethod)
    operators: tuple[MainAnalysisOperator, ...] = tuple(
        MainAnalysisOperator(operator=operator, target_invariant=invariant)
        for operator, invariant in _OPERATOR_INVARIANTS
    )
    slot_grid: Literal["task-attempt-major-operator-minor"] = (
        "task-attempt-major-operator-minor"
    )
    task_budget: Literal["4-times-max-1-projected-completion-attempts"] = (
        "4-times-max-1-projected-completion-attempts"
    )
    no_attempt_rule: Literal["four-synthetic-ordinal-target-not-reached-slots"] = (
        "four-synthetic-ordinal-target-not-reached-slots"
    )
    outcomes: tuple[MainAnalysisOutcome, ...] = tuple(MainAnalysisOutcome)
    primary_endpoint: Literal["task-has-any-target-violation"] = (
        "task-has-any-target-violation"
    )
    unit: Literal["tb21-source-identity"] = "tb21-source-identity"
    test: Literal["two-sided-exact-mcnemar"] = "two-sided-exact-mcnemar"
    effect: Literal["paired-absolute-risk-difference"] = (
        "paired-absolute-risk-difference"
    )
    interval: Literal["task-bootstrap-percentile-95"] = (
        "task-bootstrap-percentile-95"
    )
    bootstrap_resamples: Literal[10000] = 10000
    bootstrap_namespace: Literal[
        "thesis-tb21-luna-rq2-replication-v1/bootstrap-rq2-v1"
    ] = BOOTSTRAP_NAMESPACE
    correction: Literal["holm-four-contrasts-alpha-0.05"] = (
        "holm-four-contrasts-alpha-0.05"
    )
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
    holm_family: Literal["tb21-luna-rq2-replication-v1-four-contrasts"] = (
        "tb21-luna-rq2-replication-v1-four-contrasts"
    )
    complete_cohort_required: Literal[True] = True
    choices: LunaDeterministicChoice = LunaDeterministicChoice()

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        if self.methods != tuple(MainAnalysisMethod):
            raise ValueError("Luna RQ2 method order has changed")
        expected_operators = tuple(
            MainAnalysisOperator(operator=operator, target_invariant=invariant)
            for operator, invariant in _OPERATOR_INVARIANTS
        )
        if self.operators != expected_operators:
            raise ValueError("Luna RQ2 operator order has changed")
        if self.outcomes != tuple(MainAnalysisOutcome):
            raise ValueError("Luna RQ2 outcome order has changed")
        if self.choices.namespace == "thesis-tb21-sensitivity-v1":
            raise ValueError("Luna choices cannot reuse the Terra sensitivity domain")
        return self


class LunaClaimBoundary(FrozenModel):
    cohort_use: Literal["tb21-fields-only-analysis-tb20-fields-selection-provenance"] = (
        "tb21-fields-only-analysis-tb20-fields-selection-provenance"
    )
    terra_outcomes: Literal["forbidden-input"] = "forbidden-input"
    tb20_outcomes: Literal["forbidden-input"] = "forbidden-input"
    cross_version_pooling: Literal["forbidden"] = "forbidden"
    cross_version_inference: Literal["none"] = "none"
    cross_model_inference: Literal["none"] = "none"
    dataset_sensitivity_claim: Literal["forbidden"] = "forbidden"
    model_change_causality_claim: Literal["forbidden"] = "forbidden"
    main_result_replacement: Literal["forbidden"] = "forbidden"


class ProviderEnvironmentPolicy(FrozenModel):
    transport: Literal["inherited-environment-only"] = "inherited-environment-only"
    required_keys: tuple[
        Literal["OPENAI_API_KEY", "OPENAI_BASE_URL"],
        Literal["OPENAI_API_KEY", "OPENAI_BASE_URL"],
    ] = ("OPENAI_API_KEY", "OPENAI_BASE_URL")
    endpoint_sha256: Literal[
        "42c964b5536d7dd77320e537c8717a08c7397f93e3398db3e7dc1ef923400dac"
    ] = PROVIDER_ENDPOINT_SHA256
    env_file: Literal["forbidden"] = "forbidden"
    credential_argv: Literal["forbidden"] = "forbidden"
    persisted_values: Literal["forbidden"] = "forbidden"
    build_and_preflight_provider_environment: Literal["stripped"] = "stripped"
    run_seal: Literal["key-names-presence-and-bundle-sha256-only"] = (
        "key-names-presence-and-bundle-sha256-only"
    )


class LunaChronology(FrozenModel):
    freeze_gate: Literal["filesystem-absence-then-all-refs-path-history"] = (
        "filesystem-absence-then-all-refs-path-history"
    )
    protocol_policy: Literal["protocol-first-appearance-precedes-executable"] = (
        "protocol-first-appearance-precedes-executable"
    )
    executable_policy: Literal["executable-first-appearance-precedes-outcomes"] = (
        "executable-first-appearance-precedes-outcomes"
    )
    outcome_policy: Literal["all-outcome-paths-share-one-first-commit"] = (
        "all-outcome-paths-share-one-first-commit"
    )
    claim: Literal["repository-enforced-p-before-e-before-o"] = (
        "repository-enforced-p-before-e-before-o"
    )
    external_non_observation_claim: Literal["not_provable"] = "not_provable"


class LunaRq2Protocol(FrozenModel):
    schema_version: Literal[1] = 1
    protocol_id: Literal["thesis-tb21-luna-rq2-replication-v1"] = PROTOCOL_ID
    canonical_json: MainAnalysisCanonicalJson = MainAnalysisCanonicalJson()
    paths: LunaRq2Paths = LunaRq2Paths()
    chronology: LunaChronology = LunaChronology()
    source_protocol: PrefixBenchFileBinding
    cohort_origin_commit: Literal[
        "69f7fb2da64f3f465f1b72a43469007e2cf1cc2a"
    ] = COHORT_ORIGIN_COMMIT
    cohort: PrefixBenchFileBinding
    tb21: Tb21RepositorySnapshot
    protected_sources: MainAnalysisSourceSet
    imported_protocol_sources: MainAnalysisSourceSet
    protocol_sources: MainAnalysisSourceSet
    analysis: LunaRq2Analysis = LunaRq2Analysis()
    claim_boundary: LunaClaimBoundary = LunaClaimBoundary()
    provider_environment: ProviderEnvironmentPolicy = ProviderEnvironmentPolicy()
    planned_outcomes: tuple[str, ...] = Field(
        min_length=len(LUNA_OUTCOME_PATHS),
        max_length=len(LUNA_OUTCOME_PATHS),
    )
    executable_freeze: Literal[
        "separate-committed-manifest-required-before-provider-execution"
    ] = "separate-committed-manifest-required-before-provider-execution"

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        if self.source_protocol.path != SENSITIVITY_PROTOCOL.as_posix():
            raise ValueError("Luna cohort provenance protocol path has changed")
        if self.cohort.path != SENSITIVITY_MATRIX.as_posix():
            raise ValueError("Luna cohort path has changed")
        if tuple(item.path for item in self.imported_protocol_sources.files) != (
            _IMPORTED_PROTOCOL_SOURCE_PATHS
        ):
            raise ValueError("Luna imported protocol source paths have changed")
        if tuple(item.path for item in self.protocol_sources.files) != (
            _PROTOCOL_SOURCE_PATHS
        ):
            raise ValueError("Luna protocol source paths have changed")
        if self.planned_outcomes != LUNA_OUTCOME_PATHS:
            raise ValueError("Luna planned outcome paths have changed")
        if len(set(self.planned_outcomes)) != len(self.planned_outcomes):
            raise ValueError("Luna planned outcome paths must be unique")
        source_paths = {
            item.path
            for item in (
                *self.protected_sources.files,
                *self.imported_protocol_sources.files,
                *self.protocol_sources.files,
            )
        }
        if source_paths & {LUNA_PROTOCOL.as_posix(), LUNA_EXECUTABLE.as_posix()}:
            raise ValueError("Luna manifests cannot bind themselves as sources")
        if source_paths & set(self.planned_outcomes):
            raise ValueError("Luna outcome paths cannot be protocol sources")
        return self

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.model_dump(mode="json"))


class BoundLunaRq2Protocol(FrozenModel):
    protocol_file: PrefixBenchFileBinding
    spec: LunaRq2Protocol
    cohort_file: PrefixBenchFileBinding
    cohort: Tb21SensitivityMatrix
    preregistration_commit: str = Field(pattern=r"^[0-9a-f]{40}$")


class LunaRq2ProtocolPreflight(FrozenModel):
    schema_version: Literal[1] = 1
    ready_for_executable_freeze: Literal[True] = True
    ready_for_provider_execution: Literal[False] = False
    required_executable: Literal[
        "experiments/prefixbench-v1/tb21-luna-rq2-replication-executable-v1.json"
    ] = LUNA_EXECUTABLE.as_posix()
    protocol: BoundLunaRq2Protocol


def freeze_luna_rq2_protocol(project_root: Path) -> LunaRq2Protocol:
    root = _project_root(project_root)
    _require_unused_paths(root, _PREFREEZE_PATHS)
    base = load_tb21_sensitivity_protocol(root)
    return _current_protocol(root, base)


def load_luna_rq2_protocol(project_root: Path) -> BoundLunaRq2Protocol:
    root = _project_root(project_root)
    protocol_data = _read_regular_file(root / LUNA_PROTOCOL, label="Luna RQ2 protocol")
    try:
        spec = LunaRq2Protocol.model_validate_json(protocol_data)
    except ValidationError as exc:
        raise ValueError("invalid Luna RQ2 preregistration") from exc
    if protocol_data != spec.canonical_bytes():
        raise ValueError("Luna RQ2 protocol is not canonical JSON")

    base = load_tb21_sensitivity_protocol(root)
    if spec != _current_protocol(root, base):
        raise ValueError("Luna RQ2 protocol source binding is stale")
    source_protocol_data = _read_regular_file(
        root / SENSITIVITY_PROTOCOL,
        label="TB2.1 cohort provenance protocol",
    )
    cohort_data = _read_regular_file(root / SENSITIVITY_MATRIX, label="Luna RQ2 cohort")
    _validate_binding(spec.source_protocol, source_protocol_data)
    _validate_binding(spec.cohort, cohort_data)

    _validate_committed_input(root, LUNA_PROTOCOL, protocol_data)
    for binding in (
        spec.source_protocol,
        spec.cohort,
        *spec.protected_sources.files,
        *spec.imported_protocol_sources.files,
        *spec.protocol_sources.files,
    ):
        data = _read_regular_file(root / binding.path, label=f"bound source {binding.path}")
        _validate_binding(binding, data)
        _validate_committed_input(root, Path(binding.path), data)

    protocol_commit = _first_matching_commit(root, LUNA_PROTOCOL, protocol_data)
    matrix_commit = _first_matching_commit(
        root,
        SENSITIVITY_MATRIX,
        cohort_data,
    )
    if matrix_commit != COHORT_ORIGIN_COMMIT:
        raise ValueError("Luna cohort origin commit has changed")
    _require_git_ancestor(root, matrix_commit, protocol_commit)
    _require_git_ancestor(root, protocol_commit, _git_head(root))
    return BoundLunaRq2Protocol(
        protocol_file=_file_binding(LUNA_PROTOCOL, protocol_data),
        spec=spec,
        cohort_file=base.matrix_file,
        cohort=base.matrix_spec,
        preregistration_commit=protocol_commit,
    )


def preflight_luna_rq2_protocol(project_root: Path) -> LunaRq2ProtocolPreflight:
    root = _project_root(project_root)
    _require_unused_paths(root, _PREFREEZE_PATHS)
    return LunaRq2ProtocolPreflight(protocol=load_luna_rq2_protocol(root))


def check_luna_rq2_protocol(project_root: Path) -> tuple[str, ...]:
    try:
        load_luna_rq2_protocol(project_root)
    except (OSError, ValueError) as exc:
        return (f"invalid Luna RQ2 preregistration: {exc}",)
    return ()


def _current_protocol(
    project_root: Path,
    base: object,
) -> LunaRq2Protocol:
    if not hasattr(base, "protocol_file") or not hasattr(base, "matrix_file"):
        raise TypeError("base protocol must be a bound TB2.1 sensitivity protocol")
    return LunaRq2Protocol(
        source_protocol=base.protocol_file,
        cohort=base.matrix_file,
        tb21=base.matrix_spec.tb21,
        protected_sources=base.spec.protected_sources,
        imported_protocol_sources=_source_set(
            project_root,
            _IMPORTED_PROTOCOL_SOURCE_PATHS,
        ),
        protocol_sources=_source_set(project_root, _PROTOCOL_SOURCE_PATHS),
        planned_outcomes=LUNA_OUTCOME_PATHS,
    )


def _git_head(project_root: Path) -> str:
    import subprocess

    completed = subprocess.run(
        ["git", "-C", str(project_root), "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        raise ValueError(f"cannot resolve Git HEAD: {completed.stderr.strip()}")
    return completed.stdout.strip()


def provider_bundle_sha256(api_key: str, base_url: str) -> Sha256:
    if not api_key or not base_url:
        raise ValueError("Provider environment values must be nonempty")
    if "\0" in api_key or "\0" in base_url:
        raise ValueError("Provider environment values cannot contain NUL")
    material = (
        b"tb21-luna-provider-environment-v1\0"
        + b"OPENAI_API_KEY\0"
        + api_key.encode()
        + b"\0OPENAI_BASE_URL\0"
        + base_url.encode()
    )
    return hashlib.sha256(material).hexdigest()


def _canonical_json(value: object) -> bytes:
    import json

    return (
        json.dumps(
            value,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        ).encode()
        + b"\n"
    )
