from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, ValidationError, model_validator

from evidence_harness_mutation.main_analysis_protocol import MainAnalysisSourceSet
from evidence_harness_mutation.model import FrozenModel, Sha256
from evidence_harness_mutation.prefixbench import PrefixBenchFileBinding
from evidence_harness_mutation.tb21_sensitivity_protocol import (
    SENSITIVITY_EXECUTABLE,
    SENSITIVITY_MATRIX,
    TB21_COMMIT,
    TB21_REPOSITORY,
    BoundTb21SensitivityProtocol,
    Tb21SensitivityMatrix,
    load_tb21_sensitivity_protocol,
)

MODEL: Literal["openai/modelhub/gpt-5.6-terra"] = "openai/modelhub/gpt-5.6-terra"
CHOICE_NAMESPACE: Literal["thesis-tb21-sensitivity-v1"] = "thesis-tb21-sensitivity-v1"
BOOTSTRAP_NAMESPACE: Literal["thesis-tb21-sensitivity-v1/bootstrap-rq2-v1"] = (
    "thesis-tb21-sensitivity-v1/bootstrap-rq2-v1"
)

IMPLEMENTATION_PATHS = (
    "docs/tb21-sensitivity-executable-design.md",
    "scripts/tb21_sensitivity.py",
    "src/evidence_harness_mutation/_tb21_analysis.py",
    "src/evidence_harness_mutation/_tb21_harbor.py",
    "src/evidence_harness_mutation/_tb21_manifest.py",
    "src/evidence_harness_mutation/tb21_sensitivity.py",
    "tests/mutation/test_tb21_sensitivity_analysis.py",
    "tests/mutation/test_tb21_sensitivity_harbor.py",
    "tests/mutation/test_tb21_sensitivity_manifest.py",
    "tests/test_tb21_sensitivity_script.py",
)

IMPORTED_KERNEL_PATHS = (
    "src/evidence_harness_mutation/main_analysis_baselines.py",
    "src/evidence_harness_mutation/main_analysis_protocol.py",
    "src/evidence_harness_mutation/main_analysis_report.py",
    "src/evidence_harness_mutation/main_analysis_statistics.py",
)

OUTCOME_PATHS = (
    "runs/terminal-bench-2-1/prefixbench-v1-sensitivity-20261003",
    "evaluation/prefixbench-v1-tb21-sensitivity-canonical.json",
    "evaluation/prefixbench-v1-tb21-sensitivity-readiness.json",
    "evaluation/prefixbench-v1-tb21-sensitivity-offline-campaign.json",
    "evaluation/prefixbench-v1-tb21-sensitivity-method-comparison.json",
    "evaluation/thesis-tb21-sensitivity-v1.json",
)


class Tb21TaskKey(FrozenModel):
    version: Literal["terminal-bench-2-1"] = "terminal-bench-2-1"
    source_identity_sha256: Sha256


class RegistryMember(FrozenModel):
    registry_name: str = Field(pattern=r"^terminal-bench/[^/\x00]+$")
    package_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class PlannedTask(FrozenModel):
    ordinal: int = Field(ge=1, le=61)
    key: Tb21TaskKey
    member: RegistryMember
    task_tree: str = Field(pattern=r"^[0-9a-f]{40}$")
    invocation_sha256: Sha256


class FrozenRunPlan(FrozenModel):
    tasks: tuple[PlannedTask, ...] = Field(min_length=61, max_length=61)

    @model_validator(mode="after")
    def validate_tasks(self) -> Self:
        if tuple(task.ordinal for task in self.tasks) != tuple(range(1, 62)):
            raise ValueError("TB2.1 run-plan ordinals must be contiguous")
        for field in ("key", "member", "task_tree", "invocation_sha256"):
            values = tuple(_identity_value(task, field) for task in self.tasks)
            if len(values) != len(set(values)):
                raise ValueError(f"TB2.1 run-plan {field} values must be unique")
        return self

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.model_dump(mode="json"))


class RandomDomains(FrozenModel):
    choice_namespace: Literal["thesis-tb21-sensitivity-v1"] = CHOICE_NAMESPACE
    bootstrap_namespace: Literal["thesis-tb21-sensitivity-v1/bootstrap-rq2-v1"] = (
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
            raise ValueError("choice and bootstrap namespaces must differ")
        if self.choice_namespace == "thesis-main-analysis-v1":
            raise ValueError("TB2.1 choices cannot use the main-analysis namespace")
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
        if self.command_prefix != expected:
            raise ValueError("TB2.1 Harbor command prefix has changed")
        return self


class CollectorContract(FrozenModel):
    model: Literal["openai/modelhub/gpt-5.6-terra"] = MODEL
    collection_profile: Literal["prefixbench-v1"] = "prefixbench-v1"
    order: Literal["matrix-order"] = "matrix-order"
    concurrency: Literal[1] = 1
    attempts_per_launch: Literal[1] = 1
    harbor_max_retries: Literal[0] = 0
    terminal_results_per_task: Literal[1] = 1
    resume: Literal["interrupted-only-same-command-digest"] = "interrupted-only-same-command-digest"
    retry_by_reward_or_exception: Literal[False] = False
    task_selector: Literal["one-exact-registry-name-per-command"] = (
        "one-exact-registry-name-per-command"
    )
    debian_source_mounts: Literal["inherit-frozen-tb20-task-map"] = "inherit-frozen-tb20-task-map"


class ArtifactGraph(FrozenModel):
    paths: tuple[str, ...] = Field(min_length=6, max_length=6)
    dependencies: tuple[str, ...] = (
        "raw->canonical",
        "canonical->readiness",
        "readiness->campaign",
        "campaign->method",
        "main-rq2+method->final",
    )

    @model_validator(mode="after")
    def validate_paths(self) -> Self:
        if self.paths != OUTCOME_PATHS:
            raise ValueError("TB2.1 executable outcome paths have changed")
        return self


class ExecutableManifest(FrozenModel):
    schema_version: Literal[1] = 1
    executable_id: Literal["thesis-tb21-sensitivity-executable-v1"] = (
        "thesis-tb21-sensitivity-executable-v1"
    )
    canonical_json: Literal["ensure-ascii-indent-2-sort-keys-trailing-newline"] = (
        "ensure-ascii-indent-2-sort-keys-trailing-newline"
    )
    source_set_algorithm: Literal["sha256-length-framed-path-content-v1"] = (
        "sha256-length-framed-path-content-v1"
    )
    protocol: PrefixBenchFileBinding
    protocol_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    matrix: PrefixBenchFileBinding
    protected_sources: MainAnalysisSourceSet
    implementation_sources: MainAnalysisSourceSet
    imported_kernel_sources: MainAnalysisSourceSet
    run_plan_sha256: Sha256
    randomness: RandomDomains = RandomDomains()
    harbor: HarborCapabilityContract
    collector: CollectorContract = CollectorContract()
    artifacts: ArtifactGraph = ArtifactGraph(paths=OUTCOME_PATHS)

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        if self.protocol.path != ("experiments/prefixbench-v1/tb21-sensitivity-protocol-v1.json"):
            raise ValueError("TB2.1 executable protocol path has changed")
        if self.matrix.path != SENSITIVITY_MATRIX.as_posix():
            raise ValueError("TB2.1 executable matrix path has changed")
        if tuple(item.path for item in self.implementation_sources.files) != (IMPLEMENTATION_PATHS):
            raise ValueError("TB2.1 executable implementation paths have changed")
        if tuple(item.path for item in self.imported_kernel_sources.files) != (
            IMPORTED_KERNEL_PATHS
        ):
            raise ValueError("TB2.1 executable imported kernel paths have changed")
        all_sources = {
            item.path
            for item in (
                *self.protected_sources.files,
                *self.implementation_sources.files,
                *self.imported_kernel_sources.files,
            )
        }
        if SENSITIVITY_EXECUTABLE.as_posix() in all_sources:
            raise ValueError("TB2.1 executable manifest cannot bind itself")
        if all_sources & set(self.artifacts.paths):
            raise ValueError("TB2.1 outcome paths cannot be executable sources")
        return self

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.model_dump(mode="json"))


class BoundExecutable(FrozenModel):
    file: PrefixBenchFileBinding
    spec: ExecutableManifest
    protocol: BoundTb21SensitivityProtocol
    plan: FrozenRunPlan
    executable_commit: str = Field(pattern=r"^[0-9a-f]{40}$")


def freeze_manifest(project_root: Path) -> ExecutableManifest:
    root = project_root_path(project_root)
    require_preoutcome_state(root, include_executable=True)
    protocol = load_tb21_sensitivity_protocol(root)
    plan = build_run_plan(protocol.matrix_spec, protocol.spec.harbor.command)
    return ExecutableManifest(
        protocol=protocol.protocol_file,
        protocol_commit=protocol.preregistration_commit,
        matrix=protocol.matrix_file,
        protected_sources=protocol.spec.protected_sources,
        implementation_sources=source_set(root, IMPLEMENTATION_PATHS),
        imported_kernel_sources=source_set(root, IMPORTED_KERNEL_PATHS),
        run_plan_sha256=hashlib.sha256(plan.canonical_bytes()).hexdigest(),
        harbor=HarborCapabilityContract(command_prefix=protocol.spec.harbor.command),
    )


def load_manifest(project_root: Path) -> BoundExecutable:
    root = project_root_path(project_root)
    data = read_regular_file(root / SENSITIVITY_EXECUTABLE, "TB2.1 executable manifest")
    try:
        spec = ExecutableManifest.model_validate_json(data)
    except ValidationError as exc:
        raise ValueError("invalid TB2.1 executable manifest") from exc
    if data != spec.canonical_bytes():
        raise ValueError("TB2.1 executable manifest is not canonical JSON")

    protocol = load_tb21_sensitivity_protocol(root)
    if spec.protocol != protocol.protocol_file:
        raise ValueError("TB2.1 executable protocol binding is stale")
    if spec.protocol_commit != protocol.preregistration_commit:
        raise ValueError("TB2.1 executable protocol commit is stale")
    if spec.matrix != protocol.matrix_file:
        raise ValueError("TB2.1 executable matrix binding is stale")
    if spec.protected_sources != protocol.spec.protected_sources:
        raise ValueError("TB2.1 executable protected source binding is stale")

    implementation = source_set(root, IMPLEMENTATION_PATHS)
    if implementation != spec.implementation_sources:
        raise ValueError("TB2.1 executable implementation source binding is stale")
    kernels = source_set(root, IMPORTED_KERNEL_PATHS)
    if kernels != spec.imported_kernel_sources:
        raise ValueError("TB2.1 executable imported kernel binding is stale")
    plan = build_run_plan(protocol.matrix_spec, protocol.spec.harbor.command)
    if hashlib.sha256(plan.canonical_bytes()).hexdigest() != spec.run_plan_sha256:
        raise ValueError("TB2.1 executable run-plan binding is stale")

    validate_committed_input(root, SENSITIVITY_EXECUTABLE, data)
    for binding in (
        *spec.implementation_sources.files,
        *spec.imported_kernel_sources.files,
    ):
        source_data = read_regular_file(root / binding.path, f"bound source {binding.path}")
        validate_binding(binding, source_data)
        validate_committed_input(root, Path(binding.path), source_data)

    executable_commit = first_matching_commit(root, SENSITIVITY_EXECUTABLE, data)
    head = git_text(root, "rev-parse", "HEAD")
    require_git_ancestor(root, spec.protocol_commit, executable_commit)
    require_git_ancestor(root, executable_commit, head)
    return BoundExecutable(
        file=file_binding(SENSITIVITY_EXECUTABLE, data),
        spec=spec,
        protocol=protocol,
        plan=plan,
        executable_commit=executable_commit,
    )


def build_run_plan(
    matrix: Tb21SensitivityMatrix,
    command_prefix: tuple[str, ...],
) -> FrozenRunPlan:
    tasks = tuple(
        PlannedTask(
            ordinal=task.ordinal,
            key=Tb21TaskKey(source_identity_sha256=task.tb21.source_identity_sha256),
            member=RegistryMember(
                registry_name=task.tb21.registry_name,
                package_digest=task.tb21.package_digest,
            ),
            task_tree=task.tb21.task_tree,
            invocation_sha256=_invocation_sha256(
                command_prefix,
                task.tb21.registry_name,
                task.tb21.package_digest,
                task.tb21.source_identity_sha256,
            ),
        )
        for task in matrix.tasks
    )
    return FrozenRunPlan(tasks=tasks)


def invocation_command(command_prefix: tuple[str, ...], task: PlannedTask) -> tuple[str, ...]:
    return (
        *command_prefix,
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
    outcome_existing = tuple(path for path in OUTCOME_PATHS if os.path.lexists(project_root / path))
    if outcome_existing:
        raise ValueError(
            "TB2.1 executable must precede outcome artifacts: " + ", ".join(outcome_existing)
        )
    outcome_historical = tuple(
        path for path in OUTCOME_PATHS if path_has_git_history(project_root, path)
    )
    if outcome_historical:
        raise ValueError(
            "TB2.1 outcome path already exists in Git history: " + ", ".join(outcome_historical)
        )
    if not include_executable:
        return
    executable_path = project_root / SENSITIVITY_EXECUTABLE
    if path_has_git_history(project_root, SENSITIVITY_EXECUTABLE.as_posix()):
        raise ValueError("TB2.1 executable path already exists in Git history")
    if os.path.lexists(executable_path) and (
        executable_path.is_symlink() or not executable_path.is_file()
    ):
        raise ValueError("existing TB2.1 executable output must be a regular file")


def source_set(project_root: Path, paths: tuple[str, ...]) -> MainAnalysisSourceSet:
    digest = hashlib.sha256()
    bindings: list[PrefixBenchFileBinding] = []
    total = 0
    for relative in paths:
        data = read_regular_file(
            project_root / relative,
            f"TB2.1 executable source {relative}",
        )
        encoded = relative.encode()
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
        total += len(data)
        bindings.append(file_binding(Path(relative), data))
    return MainAnalysisSourceSet(
        bytes=total,
        sha256=digest.hexdigest(),
        files=tuple(bindings),
    )


def first_matching_commit(project_root: Path, path: Path, data: bytes) -> str:
    commits = tuple(
        line
        for line in git_text(
            project_root,
            "log",
            "--all",
            "--format=%H",
            "--",
            path.as_posix(),
        ).splitlines()
        if line
    )
    matching = tuple(
        commit for commit in commits if git_path_or_none(project_root, commit, path) == data
    )
    if not matching:
        raise ValueError(f"Git history does not contain current bytes: {path}")
    return matching[-1]


def path_has_git_history(project_root: Path, path: str) -> bool:
    return bool(git_text(project_root, "log", "--all", "--format=%H", "--", path))


def validate_committed_input(project_root: Path, relative: Path, data: bytes) -> None:
    if git_bytes(project_root, "show", f"HEAD:{relative.as_posix()}") != data:
        raise ValueError(f"TB2.1 executable input differs from Git HEAD: {relative}")


def require_git_ancestor(project_root: Path, ancestor: str, descendant: str) -> None:
    completed = subprocess.run(
        ("git", "-C", str(project_root), "merge-base", "--is-ancestor", ancestor, descendant),
        check=False,
        capture_output=True,
    )
    if completed.returncode == 1:
        raise ValueError(f"required ancestor relation is absent: {ancestor} -> {descendant}")
    if completed.returncode:
        detail = completed.stderr.decode(errors="replace").strip()
        raise ValueError(f"cannot verify TB2.1 executable ancestry: {detail}")


def git_path_or_none(project_root: Path, commit: str, path: Path) -> bytes | None:
    completed = subprocess.run(
        ("git", "-C", str(project_root), "show", f"{commit}:{path.as_posix()}"),
        check=False,
        capture_output=True,
    )
    return completed.stdout if completed.returncode == 0 else None


def validate_binding(binding: PrefixBenchFileBinding, data: bytes) -> None:
    if binding != file_binding(Path(binding.path), data):
        raise ValueError(f"file binding is stale: {binding.path}")


def file_binding(path: Path, data: bytes) -> PrefixBenchFileBinding:
    return PrefixBenchFileBinding(
        path=path.as_posix(),
        bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def read_regular_file(path: Path, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} must be a regular file: {path}")
    return path.read_bytes()


def project_root_path(path: Path) -> Path:
    root = path.resolve()
    if not root.is_dir():
        raise ValueError(f"project root is not a directory: {root}")
    actual = Path(git_text(root, "rev-parse", "--show-toplevel")).resolve()
    if actual != root:
        raise ValueError("project root is not the Git top level")
    return root


def git_text(checkout: Path, *args: str) -> str:
    return git_bytes(checkout, *args).decode(errors="strict").strip()


def git_bytes(checkout: Path, *args: str) -> bytes:
    try:
        completed = subprocess.run(
            ("git", "-C", str(checkout), *args),
            check=False,
            capture_output=True,
        )
    except OSError as exc:
        raise ValueError(f"cannot execute Git: {exc}") from exc
    if completed.returncode:
        detail = completed.stderr.decode(errors="replace").strip()
        raise ValueError(f"Git {' '.join(args)} failed: {detail}")
    return completed.stdout


def _identity_value(task: PlannedTask, field: str) -> object:
    if field == "key":
        return task.key.source_identity_sha256
    if field == "member":
        return (task.member.registry_name, task.member.package_digest)
    return getattr(task, field)


def _invocation_sha256(
    command_prefix: tuple[str, ...],
    registry_name: str,
    package_digest: str,
    source_identity_sha256: str,
) -> str:
    material = _nul_frame(
        (
            "tb21-sensitivity-invocation-v1",
            *command_prefix,
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
    return b"\0".join(value.encode("utf-8") for value in values)


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
