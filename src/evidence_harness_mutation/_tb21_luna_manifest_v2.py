from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, ValidationError, model_validator

from evidence_harness_mutation._tb21_manifest import (
    FrozenRunPlan,
    PlannedTask,
    RegistryMember,
    Tb21TaskKey,
    file_binding,
    first_matching_commit,
    git_text,
    path_has_git_history,
    project_root_path,
    read_regular_file,
    require_git_ancestor,
    source_set,
    validate_binding,
    validate_committed_input,
)
from evidence_harness_mutation.main_analysis_protocol import MainAnalysisSourceSet
from evidence_harness_mutation.model import FrozenModel, Sha256
from evidence_harness_mutation.prefixbench import PrefixBenchFileBinding
from evidence_harness_mutation.tb21_luna_rq2_protocol_v2 import (
    BOOTSTRAP_NAMESPACE,
    CHOICE_NAMESPACE,
    LUNA_EXECUTABLE,
    LUNA_OUTCOME_PATHS,
    MODEL,
    BoundLunaRq2ProtocolV2,
    load_luna_rq2_protocol_v2,
)
from evidence_harness_mutation.tb21_sensitivity_protocol import (
    TB21_COMMIT,
    TB21_REPOSITORY,
    Tb21SensitivityMatrix,
)

IMPLEMENTATION_PATHS = (
    "docs/tb21-luna-rq2-replication-executable-v2.md",
    "scripts/tb21_luna_rq2_v2.py",
    "tools/harbor-tb21-luna-v2",
    "src/evidence_harness_mutation/_tb21_luna_analysis_v2.py",
    "src/evidence_harness_mutation/_tb21_luna_harbor_v2.py",
    "src/evidence_harness_mutation/_tb21_luna_manifest_v2.py",
    "src/evidence_harness_mutation/tb21_luna_rq2_v2.py",
    "src/evidence_harness_mutation/tb21_luna_runtime_v2.py",
    "tests/mutation/test_tb21_luna_analysis_v2.py",
    "tests/mutation/test_tb21_luna_harbor_v2.py",
    "tests/mutation/test_tb21_luna_manifest_v2.py",
    "tests/mutation/test_tb21_luna_runtime_v2.py",
    "tests/test_tb21_luna_rq2_v2_script.py",
)

IMPORTED_KERNEL_PATHS = (
    "src/evidence_harness_mutation/_tb21_harbor.py",
    "src/evidence_harness_mutation/main_analysis_baselines.py",
    "src/evidence_harness_mutation/main_analysis_protocol.py",
    "src/evidence_harness_mutation/main_analysis_report.py",
    "src/evidence_harness_mutation/main_analysis_statistics.py",
    "src/evidence_harness_mutation/tb21_harbor_runtime.py",
)

OPERATIONAL_INPUT_PATHS = (
    "config/tb21-harbor-runtime.json",
    "patches/harbor-v0.23.0-tb21-dataset-toml.patch",
)


class RandomDomains(FrozenModel):
    choice_namespace: Literal["thesis-tb21-luna-rq2-replication-v1"] = CHOICE_NAMESPACE
    bootstrap_namespace: Literal["thesis-tb21-luna-rq2-replication-v1/bootstrap-rq2-v1"] = (
        BOOTSTRAP_NAMESPACE
    )
    seed: Literal[20261003] = 20261003
    choice_algorithm: Literal["sha256-first-8-bytes-mod-n-nul-framed"] = (
        "sha256-first-8-bytes-mod-n-nul-framed"
    )
    bootstrap_algorithm: Literal["sha256-counter-resample-and-draw-ordinals"] = (
        "sha256-counter-resample-and-draw-ordinals"
    )
    bootstrap_resamples: Literal[10000] = 10000

    @model_validator(mode="after")
    def validate_domains(self) -> Self:
        if self.choice_namespace == self.bootstrap_namespace:
            raise ValueError("Luna choice and bootstrap namespaces must differ")
        if "sensitivity" in self.choice_namespace or "sensitivity" in (self.bootstrap_namespace):
            raise ValueError("Luna random domains cannot reuse sensitivity identities")
        return self


class HarborCapabilityContract(FrozenModel):
    command_prefix: tuple[str, ...]
    manifest_kind: Literal["dataset-toml-package-members"] = "dataset-toml-package-members"
    selector_field: Literal["registry_name"] = "registry_name"
    expected_member_count: Literal[61] = 61
    required_identity: Literal["registry-name-plus-package-digest"] = (
        "registry-name-plus-package-digest"
    )
    provider_calls_during_preflight: Literal[0] = 0
    version_policy: Literal["behavior-probed-not-version-allowlisted"] = (
        "behavior-probed-not-version-allowlisted"
    )

    @model_validator(mode="after")
    def validate_prefix(self) -> Self:
        if self.command_prefix != harbor_command_prefix():
            raise ValueError("Luna Harbor command prefix has changed")
        return self


class CollectorContract(FrozenModel):
    model: Literal["openai/modelhub/gpt-5.6-luna"] = MODEL
    collection_profile: Literal["prefixbench-v1"] = "prefixbench-v1"
    order: Literal["cohort-order"] = "cohort-order"
    concurrency: Literal[1] = 1
    attempts_per_launch: Literal[1] = 1
    harbor_max_retries: Literal[0] = 0
    terminal_results_per_task: Literal[1] = 1
    resume: Literal["interrupted-only-same-command-digest"] = "interrupted-only-same-command-digest"
    retry_by_reward_or_exception: Literal[False] = False
    task_selector: Literal["one-exact-registry-name-per-command"] = (
        "one-exact-registry-name-per-command"
    )
    credential_transport: Literal["inherited-environment-only"] = "inherited-environment-only"
    provider_environment: tuple[str, str] = ("OPENAI_API_KEY", "OPENAI_BASE_URL")
    env_file: Literal["forbidden"] = "forbidden"
    model_evidence: Literal["saved-config-plus-result-config-plus-model-info"] = (
        "saved-config-plus-result-config-plus-model-info"
    )
    debian_source_mounts: Literal["inherit-frozen-tb20-task-map"] = "inherit-frozen-tb20-task-map"
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

    @model_validator(mode="after")
    def validate_provider_environment(self) -> Self:
        if self.provider_environment != ("OPENAI_API_KEY", "OPENAI_BASE_URL"):
            raise ValueError("Luna Provider environment allowlist has changed")
        return self


class ArtifactGraph(FrozenModel):
    paths: tuple[str, ...] = Field(
        min_length=len(LUNA_OUTCOME_PATHS),
        max_length=len(LUNA_OUTCOME_PATHS),
    )
    dependencies: tuple[str, ...] = (
        "raw->canonical",
        "canonical->readiness",
        "readiness->campaign",
        "campaign->luna-rq2-report",
    )

    @model_validator(mode="after")
    def validate_paths(self) -> Self:
        if self.paths != LUNA_OUTCOME_PATHS:
            raise ValueError("Luna executable outcome paths have changed")
        return self


class ExecutableManifest(FrozenModel):
    schema_version: Literal[1] = 1
    executable_id: Literal["thesis-tb21-luna-rq2-replication-executable-v2"] = (
        "thesis-tb21-luna-rq2-replication-executable-v2"
    )
    canonical_json: Literal["ensure-ascii-indent-2-sort-keys-trailing-newline"] = (
        "ensure-ascii-indent-2-sort-keys-trailing-newline"
    )
    source_set_algorithm: Literal["sha256-length-framed-path-content-v1"] = (
        "sha256-length-framed-path-content-v1"
    )
    protocol: PrefixBenchFileBinding
    protocol_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    cohort: PrefixBenchFileBinding
    protected_sources: MainAnalysisSourceSet
    implementation_sources: MainAnalysisSourceSet
    imported_kernel_sources: MainAnalysisSourceSet
    operational_inputs: MainAnalysisSourceSet
    run_plan_sha256: Sha256
    randomness: RandomDomains = RandomDomains()
    harbor: HarborCapabilityContract
    collector: CollectorContract = CollectorContract()
    artifacts: ArtifactGraph = ArtifactGraph(paths=LUNA_OUTCOME_PATHS)

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        if self.protocol.path != (
            "experiments/prefixbench-v1/tb21-luna-rq2-replication-protocol-v2.json"
        ):
            raise ValueError("Luna executable protocol path has changed")
        if self.cohort.path != "evaluation/matrix-prefixbench-tb21-sensitivity.json":
            raise ValueError("Luna executable cohort path has changed")
        if tuple(item.path for item in self.implementation_sources.files) != (IMPLEMENTATION_PATHS):
            raise ValueError("Luna executable implementation paths have changed")
        if tuple(item.path for item in self.imported_kernel_sources.files) != (
            IMPORTED_KERNEL_PATHS
        ):
            raise ValueError("Luna imported kernel paths have changed")
        if tuple(item.path for item in self.operational_inputs.files) != (OPERATIONAL_INPUT_PATHS):
            raise ValueError("Luna operational input paths have changed")
        all_sources = {
            item.path
            for item in (
                *self.protected_sources.files,
                *self.implementation_sources.files,
                *self.imported_kernel_sources.files,
                *self.operational_inputs.files,
            )
        }
        if LUNA_EXECUTABLE.as_posix() in all_sources:
            raise ValueError("Luna executable manifest cannot bind itself")
        if all_sources & set(self.artifacts.paths):
            raise ValueError("Luna outcomes cannot be executable sources")
        return self

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.model_dump(mode="json"))


class BoundExecutable(FrozenModel):
    file: PrefixBenchFileBinding
    spec: ExecutableManifest
    protocol: BoundLunaRq2ProtocolV2
    plan: FrozenRunPlan
    executable_commit: str = Field(pattern=r"^[0-9a-f]{40}$")


def freeze_manifest(project_root: Path) -> ExecutableManifest:
    root = project_root_path(project_root)
    require_preoutcome_state(root, include_executable=True)
    protocol = load_luna_rq2_protocol_v2(root)
    plan = build_run_plan(protocol.cohort)
    return ExecutableManifest(
        protocol=protocol.protocol_file,
        protocol_commit=protocol.preregistration_commit,
        cohort=protocol.cohort_file,
        protected_sources=protocol.spec.protected_sources,
        implementation_sources=source_set(root, IMPLEMENTATION_PATHS),
        imported_kernel_sources=source_set(root, IMPORTED_KERNEL_PATHS),
        operational_inputs=source_set(root, OPERATIONAL_INPUT_PATHS),
        run_plan_sha256=hashlib.sha256(plan.canonical_bytes()).hexdigest(),
        harbor=HarborCapabilityContract(command_prefix=harbor_command_prefix()),
    )


def load_manifest(project_root: Path) -> BoundExecutable:
    root = project_root_path(project_root)
    data = read_regular_file(root / LUNA_EXECUTABLE, "Luna executable manifest")
    try:
        spec = ExecutableManifest.model_validate_json(data)
    except ValidationError as exc:
        raise ValueError("invalid Luna executable manifest") from exc
    if data != spec.canonical_bytes():
        raise ValueError("Luna executable manifest is not canonical JSON")

    protocol = load_luna_rq2_protocol_v2(root)
    if spec.protocol != protocol.protocol_file:
        raise ValueError("Luna executable protocol binding is stale")
    if spec.protocol_commit != protocol.preregistration_commit:
        raise ValueError("Luna executable protocol commit is stale")
    if spec.cohort != protocol.cohort_file:
        raise ValueError("Luna executable cohort binding is stale")
    if spec.protected_sources != protocol.spec.protected_sources:
        raise ValueError("Luna protected source binding is stale")

    current_sets = (
        (spec.implementation_sources, source_set(root, IMPLEMENTATION_PATHS)),
        (spec.imported_kernel_sources, source_set(root, IMPORTED_KERNEL_PATHS)),
        (spec.operational_inputs, source_set(root, OPERATIONAL_INPUT_PATHS)),
    )
    if any(expected != actual for expected, actual in current_sets):
        raise ValueError("Luna executable source binding is stale")
    plan = build_run_plan(protocol.cohort)
    if hashlib.sha256(plan.canonical_bytes()).hexdigest() != spec.run_plan_sha256:
        raise ValueError("Luna executable run-plan binding is stale")

    validate_committed_input(root, LUNA_EXECUTABLE, data)
    for source_set_value in (
        spec.implementation_sources,
        spec.imported_kernel_sources,
        spec.operational_inputs,
    ):
        for binding in source_set_value.files:
            source_data = read_regular_file(root / binding.path, f"bound source {binding.path}")
            validate_binding(binding, source_data)
            validate_committed_input(root, Path(binding.path), source_data)

    executable_commit = first_matching_commit(root, LUNA_EXECUTABLE, data)
    head = git_text(root, "rev-parse", "HEAD")
    if executable_commit == spec.protocol_commit:
        raise ValueError("Luna protocol and executable require distinct commits")
    require_git_ancestor(root, spec.protocol_commit, executable_commit)
    require_git_ancestor(root, executable_commit, head)
    return BoundExecutable(
        file=file_binding(LUNA_EXECUTABLE, data),
        spec=spec,
        protocol=protocol,
        plan=plan,
        executable_commit=executable_commit,
    )


def harbor_command_prefix() -> tuple[str, ...]:
    return (
        "harbor",
        "run",
        "--repo",
        f"{TB21_REPOSITORY}@{TB21_COMMIT}",
        "--registry-path",
        "tasks/dataset.toml",
        "--dataset",
        "terminal-bench-2-1",
    )


def build_run_plan(matrix: Tb21SensitivityMatrix) -> FrozenRunPlan:
    return FrozenRunPlan(
        tasks=tuple(
            PlannedTask(
                ordinal=task.ordinal,
                key=Tb21TaskKey(source_identity_sha256=task.tb21.source_identity_sha256),
                member=RegistryMember(
                    registry_name=task.tb21.registry_name,
                    package_digest=task.tb21.package_digest,
                ),
                task_tree=task.tb21.task_tree,
                invocation_sha256=_invocation_sha256(
                    task.tb21.registry_name,
                    task.tb21.package_digest,
                    task.tb21.source_identity_sha256,
                ),
            )
            for task in matrix.tasks
        )
    )


def invocation_command(task: PlannedTask) -> tuple[str, ...]:
    return (
        *harbor_command_prefix(),
        "--agent",
        "evidence_harness.harbor_agent:EvidenceHarnessAgent",
        "--model",
        MODEL,
        "--n-concurrent",
        "1",
        "--n-attempts",
        "1",
        "--max-retries",
        "0",
        "--agent-timeout-multiplier",
        "2.0",
        "--include-task-name",
        task.member.registry_name,
    )


def require_preoutcome_state(project_root: Path, *, include_executable: bool) -> None:
    existing = tuple(path for path in LUNA_OUTCOME_PATHS if os.path.lexists(project_root / path))
    if existing:
        raise ValueError("Luna executable must precede outcomes: " + ", ".join(existing))
    historical = tuple(
        path for path in LUNA_OUTCOME_PATHS if path_has_git_history(project_root, path)
    )
    if historical:
        raise ValueError(
            "Luna outcome path already exists in Git history: " + ", ".join(historical)
        )
    if not include_executable:
        return
    if path_has_git_history(project_root, LUNA_EXECUTABLE.as_posix()):
        raise ValueError("Luna executable path already exists in Git history")
    target = project_root / LUNA_EXECUTABLE
    if os.path.lexists(target) and (target.is_symlink() or not target.is_file()):
        raise ValueError("existing Luna executable output must be a regular file")


def _invocation_sha256(
    registry_name: str,
    package_digest: str,
    source_identity_sha256: str,
) -> str:
    material = _nul_frame(
        (
            "tb21-luna-rq2-invocation-v2",
            *harbor_command_prefix(),
            MODEL,
            "prefixbench-v1",
            registry_name,
            package_digest,
            source_identity_sha256,
            "n-attempts=1",
            "max-retries=0",
            "n-concurrent=1",
        )
    )
    return hashlib.sha256(material).hexdigest()


def _nul_frame(values: tuple[str, ...]) -> bytes:
    if any("\0" in value for value in values):
        raise ValueError("NUL is not allowed in framed identity fields")
    return b"\0".join(value.encode() for value in values)


def _canonical_json(value: object) -> bytes:
    import json

    return json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True).encode() + b"\n"
