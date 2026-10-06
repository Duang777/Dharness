from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, ValidationError, model_validator

from evidence_harness_mutation.main_analysis_protocol import (
    MainAnalysisCanonicalJson,
    MainAnalysisSourceSet,
)
from evidence_harness_mutation.model import FrozenModel, Sha256
from evidence_harness_mutation.prefixbench import PrefixBenchFileBinding
from evidence_harness_mutation.tb21_luna_rq2_protocol import (
    LUNA_EXECUTABLE as V1_EXECUTABLE,
)
from evidence_harness_mutation.tb21_luna_rq2_protocol import (
    LUNA_PROTOCOL as V1_PROTOCOL,
)
from evidence_harness_mutation.tb21_luna_rq2_protocol import (
    BoundLunaRq2Protocol,
    LunaClaimBoundary,
    LunaRq2Analysis,
    ProviderEnvironmentPolicy,
    load_luna_rq2_protocol,
)
from evidence_harness_mutation.tb21_sensitivity_protocol import (
    SENSITIVITY_MATRIX,
    SENSITIVITY_PROTOCOL,
    BoundTb21SensitivityProtocol,
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

PROTOCOL_ID: Literal["thesis-tb21-luna-rq2-replication-v2"] = "thesis-tb21-luna-rq2-replication-v2"
MODEL: Literal["openai/modelhub/gpt-5.6-luna"] = "openai/modelhub/gpt-5.6-luna"
CHOICE_NAMESPACE: Literal["thesis-tb21-luna-rq2-replication-v1"] = (
    "thesis-tb21-luna-rq2-replication-v1"
)
BOOTSTRAP_NAMESPACE: Literal["thesis-tb21-luna-rq2-replication-v1/bootstrap-rq2-v1"] = (
    "thesis-tb21-luna-rq2-replication-v1/bootstrap-rq2-v1"
)
PROVIDER_ENDPOINT_SHA256: Literal[
    "42c964b5536d7dd77320e537c8717a08c7397f93e3398db3e7dc1ef923400dac"
] = "42c964b5536d7dd77320e537c8717a08c7397f93e3398db3e7dc1ef923400dac"
COHORT_ORIGIN_COMMIT: Literal["69f7fb2da64f3f465f1b72a43469007e2cf1cc2a"] = (
    "69f7fb2da64f3f465f1b72a43469007e2cf1cc2a"
)
V1_EXECUTABLE_COMMIT: Literal["27a90d5969fa9e5e9e6684093a3f094db157b7c6"] = (
    "27a90d5969fa9e5e9e6684093a3f094db157b7c6"
)

LUNA_PROTOCOL = Path("experiments/prefixbench-v1/tb21-luna-rq2-replication-protocol-v2.json")
LUNA_EXECUTABLE = Path("experiments/prefixbench-v1/tb21-luna-rq2-replication-executable-v2.json")
LUNA_RUN_ROOT = Path("runs/terminal-bench-2-1/prefixbench-v1-luna-rq2-replication-v2")
LUNA_CANONICAL = Path("evaluation/prefixbench-v1-tb21-luna-rq2-canonical-v2.json")
LUNA_READINESS = Path("evaluation/prefixbench-v1-tb21-luna-rq2-readiness-v2.json")
LUNA_CAMPAIGN = Path("evaluation/prefixbench-v1-tb21-luna-rq2-offline-campaign-v2.json")
LUNA_REPORT = Path("evaluation/thesis-tb21-luna-rq2-replication-v2.json")

LUNA_OUTCOME_PATHS = (
    LUNA_RUN_ROOT.as_posix(),
    LUNA_CANONICAL.as_posix(),
    LUNA_READINESS.as_posix(),
    LUNA_CAMPAIGN.as_posix(),
    LUNA_REPORT.as_posix(),
)
_PREFREEZE_PATHS = (LUNA_EXECUTABLE.as_posix(), *LUNA_OUTCOME_PATHS)
_PROTOCOL_SOURCE_PATHS = (
    "docs/tb21-luna-rq2-replication-protocol-v2.md",
    "scripts/tb21_luna_rq2_protocol_v2.py",
    "src/evidence_harness_mutation/tb21_luna_rq2_protocol_v2.py",
    "tests/mutation/test_tb21_luna_rq2_protocol_v2.py",
    "tests/test_tb21_luna_rq2_protocol_v2_script.py",
)
_IMPORTED_PROTOCOL_SOURCE_PATHS = (
    "src/evidence_harness_mutation/tb21_luna_rq2_protocol.py",
    "src/evidence_harness_mutation/tb21_sensitivity_protocol.py",
)


class LunaRq2Paths(FrozenModel):
    cohort: Literal["evaluation/matrix-prefixbench-tb21-sensitivity.json"] = (
        "evaluation/matrix-prefixbench-tb21-sensitivity.json"
    )
    protocol: Literal["experiments/prefixbench-v1/tb21-luna-rq2-replication-protocol-v2.json"] = (
        "experiments/prefixbench-v1/tb21-luna-rq2-replication-protocol-v2.json"
    )
    executable: Literal[
        "experiments/prefixbench-v1/tb21-luna-rq2-replication-executable-v2.json"
    ] = "experiments/prefixbench-v1/tb21-luna-rq2-replication-executable-v2.json"
    raw: Literal["runs/terminal-bench-2-1/prefixbench-v1-luna-rq2-replication-v2"] = (
        "runs/terminal-bench-2-1/prefixbench-v1-luna-rq2-replication-v2"
    )
    canonical: Literal["evaluation/prefixbench-v1-tb21-luna-rq2-canonical-v2.json"] = (
        "evaluation/prefixbench-v1-tb21-luna-rq2-canonical-v2.json"
    )
    readiness: Literal["evaluation/prefixbench-v1-tb21-luna-rq2-readiness-v2.json"] = (
        "evaluation/prefixbench-v1-tb21-luna-rq2-readiness-v2.json"
    )
    campaign: Literal["evaluation/prefixbench-v1-tb21-luna-rq2-offline-campaign-v2.json"] = (
        "evaluation/prefixbench-v1-tb21-luna-rq2-offline-campaign-v2.json"
    )
    final_report: Literal["evaluation/thesis-tb21-luna-rq2-replication-v2.json"] = (
        "evaluation/thesis-tb21-luna-rq2-replication-v2.json"
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


class LunaOperationalCorrection(FrozenModel):
    superseded_protocol_id: Literal["thesis-tb21-luna-rq2-replication-v1"] = (
        "thesis-tb21-luna-rq2-replication-v1"
    )
    superseded_executable_id: Literal["thesis-tb21-luna-rq2-replication-executable-v1"] = (
        "thesis-tb21-luna-rq2-replication-executable-v1"
    )
    superseded_executable_commit: Literal["27a90d5969fa9e5e9e6684093a3f094db157b7c6"] = (
        V1_EXECUTABLE_COMMIT
    )
    observed_failure_stage: Literal["agent-runtime-source-attestation-before-model-call"] = (
        "agent-runtime-source-attestation-before-model-call"
    )
    observed_model_calls: Literal[0] = 0
    prior_run_handling: Literal["preserve-locally-never-retry-or-input"] = (
        "preserve-locally-never-retry-or-input"
    )
    correction_scope: Literal["operational-only-scientific-contract-unchanged"] = (
        "operational-only-scientific-contract-unchanged"
    )
    control_plane_source: Literal["installed-executable-wheel"] = "installed-executable-wheel"
    harbor_agent_source: Literal["executable-project-root-src-prepended-to-child-pythonpath"] = (
        "executable-project-root-src-prepended-to-child-pythonpath"
    )
    result_selection: Literal["exactly-one-trial-result-excluding-job-aggregate"] = (
        "exactly-one-trial-result-excluding-job-aggregate"
    )
    recovery_parser: Literal["same-trial-result-parser-as-live-launch"] = (
        "same-trial-result-parser-as-live-launch"
    )


class LunaRq2ProtocolV2(FrozenModel):
    schema_version: Literal[1] = 1
    protocol_id: Literal["thesis-tb21-luna-rq2-replication-v2"] = PROTOCOL_ID
    canonical_json: MainAnalysisCanonicalJson = MainAnalysisCanonicalJson()
    paths: LunaRq2Paths = LunaRq2Paths()
    chronology: LunaChronology = LunaChronology()
    source_protocol: PrefixBenchFileBinding
    superseded_protocol: PrefixBenchFileBinding
    superseded_executable: PrefixBenchFileBinding
    cohort_origin_commit: Literal["69f7fb2da64f3f465f1b72a43469007e2cf1cc2a"] = COHORT_ORIGIN_COMMIT
    cohort: PrefixBenchFileBinding
    tb21: Tb21RepositorySnapshot
    protected_sources: MainAnalysisSourceSet
    imported_protocol_sources: MainAnalysisSourceSet
    protocol_sources: MainAnalysisSourceSet
    analysis: LunaRq2Analysis = LunaRq2Analysis()
    claim_boundary: LunaClaimBoundary = LunaClaimBoundary()
    provider_environment: ProviderEnvironmentPolicy = ProviderEnvironmentPolicy()
    operational_correction: LunaOperationalCorrection = LunaOperationalCorrection()
    planned_outcomes: tuple[str, ...] = Field(
        min_length=len(LUNA_OUTCOME_PATHS),
        max_length=len(LUNA_OUTCOME_PATHS),
    )
    executable_freeze: Literal["separate-committed-manifest-required-before-provider-execution"] = (
        "separate-committed-manifest-required-before-provider-execution"
    )

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        if self.source_protocol.path != SENSITIVITY_PROTOCOL.as_posix():
            raise ValueError("Luna cohort provenance protocol path has changed")
        if self.superseded_protocol.path != V1_PROTOCOL.as_posix():
            raise ValueError("superseded Luna protocol path has changed")
        if self.superseded_executable.path != V1_EXECUTABLE.as_posix():
            raise ValueError("superseded Luna executable path has changed")
        if self.cohort.path != SENSITIVITY_MATRIX.as_posix():
            raise ValueError("Luna cohort path has changed")
        if tuple(item.path for item in self.imported_protocol_sources.files) != (
            _IMPORTED_PROTOCOL_SOURCE_PATHS
        ):
            raise ValueError("Luna imported protocol source paths have changed")
        if tuple(item.path for item in self.protocol_sources.files) != (_PROTOCOL_SOURCE_PATHS):
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


class BoundLunaRq2ProtocolV2(FrozenModel):
    protocol_file: PrefixBenchFileBinding
    spec: LunaRq2ProtocolV2
    cohort_file: PrefixBenchFileBinding
    cohort: Tb21SensitivityMatrix
    preregistration_commit: str = Field(pattern=r"^[0-9a-f]{40}$")


class LunaRq2ProtocolV2Preflight(FrozenModel):
    schema_version: Literal[1] = 1
    ready_for_executable_freeze: Literal[True] = True
    ready_for_provider_execution: Literal[False] = False
    required_executable: Literal[
        "experiments/prefixbench-v1/tb21-luna-rq2-replication-executable-v2.json"
    ] = "experiments/prefixbench-v1/tb21-luna-rq2-replication-executable-v2.json"
    protocol: BoundLunaRq2ProtocolV2


def freeze_luna_rq2_protocol_v2(project_root: Path) -> LunaRq2ProtocolV2:
    root = _project_root(project_root)
    _require_unused_paths(root, _PREFREEZE_PATHS)
    base = load_tb21_sensitivity_protocol(root)
    prior = load_luna_rq2_protocol(root)
    return _current_protocol(root, base, prior)


def load_luna_rq2_protocol_v2(project_root: Path) -> BoundLunaRq2ProtocolV2:
    root = _project_root(project_root)
    protocol_data = _read_regular_file(root / LUNA_PROTOCOL, label="Luna RQ2 protocol")
    try:
        spec = LunaRq2ProtocolV2.model_validate_json(protocol_data)
    except ValidationError as exc:
        raise ValueError("invalid Luna RQ2 preregistration") from exc
    if protocol_data != spec.canonical_bytes():
        raise ValueError("Luna RQ2 protocol is not canonical JSON")

    base = load_tb21_sensitivity_protocol(root)
    prior = load_luna_rq2_protocol(root)
    if spec != _current_protocol(root, base, prior):
        raise ValueError("Luna RQ2 protocol source binding is stale")
    source_protocol_data = _read_regular_file(
        root / SENSITIVITY_PROTOCOL,
        label="TB2.1 cohort provenance protocol",
    )
    cohort_data = _read_regular_file(root / SENSITIVITY_MATRIX, label="Luna RQ2 cohort")
    superseded_protocol_data = _read_regular_file(
        root / V1_PROTOCOL,
        label="superseded Luna protocol",
    )
    superseded_executable_data = _read_regular_file(
        root / V1_EXECUTABLE,
        label="superseded Luna executable",
    )
    _validate_binding(spec.source_protocol, source_protocol_data)
    _validate_binding(spec.cohort, cohort_data)
    _validate_binding(spec.superseded_protocol, superseded_protocol_data)
    _validate_binding(spec.superseded_executable, superseded_executable_data)

    _validate_committed_input(root, LUNA_PROTOCOL, protocol_data)
    _validate_committed_input(root, V1_PROTOCOL, superseded_protocol_data)
    _validate_committed_input(root, V1_EXECUTABLE, superseded_executable_data)
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
    executable_commit = _first_matching_commit(
        root,
        V1_EXECUTABLE,
        superseded_executable_data,
    )
    if executable_commit != V1_EXECUTABLE_COMMIT:
        raise ValueError("superseded Luna executable commit has changed")
    _require_git_ancestor(root, matrix_commit, protocol_commit)
    _require_git_ancestor(root, executable_commit, protocol_commit)
    _require_git_ancestor(root, protocol_commit, _git_head(root))
    return BoundLunaRq2ProtocolV2(
        protocol_file=_file_binding(LUNA_PROTOCOL, protocol_data),
        spec=spec,
        cohort_file=base.matrix_file,
        cohort=base.matrix_spec,
        preregistration_commit=protocol_commit,
    )


def preflight_luna_rq2_protocol_v2(project_root: Path) -> LunaRq2ProtocolV2Preflight:
    root = _project_root(project_root)
    _require_unused_paths(root, _PREFREEZE_PATHS)
    return LunaRq2ProtocolV2Preflight(protocol=load_luna_rq2_protocol_v2(root))


def check_luna_rq2_protocol_v2(project_root: Path) -> tuple[str, ...]:
    try:
        load_luna_rq2_protocol_v2(project_root)
    except (OSError, ValueError) as exc:
        return (f"invalid Luna RQ2 preregistration: {exc}",)
    return ()


def _current_protocol(
    project_root: Path,
    base: BoundTb21SensitivityProtocol,
    prior: BoundLunaRq2Protocol,
) -> LunaRq2ProtocolV2:
    superseded_executable_data = _read_regular_file(
        project_root / V1_EXECUTABLE,
        label="superseded Luna executable",
    )
    return LunaRq2ProtocolV2(
        source_protocol=base.protocol_file,
        superseded_protocol=prior.protocol_file,
        superseded_executable=_file_binding(
            V1_EXECUTABLE,
            superseded_executable_data,
        ),
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
        b"tb21-luna-provider-environment-v2\0"
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
