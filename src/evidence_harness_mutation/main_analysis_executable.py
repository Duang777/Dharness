from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, ValidationError, model_validator

from evidence_harness_mutation.main_analysis_protocol import (
    EXECUTABLE_PROTOCOL,
    MAIN_PROTOCOL,
    MINISWE_COHORT,
    MainAnalysisSourceSet,
    load_main_analysis_protocol,
)
from evidence_harness_mutation.main_analysis_transfer_runtime import (
    MINISWE_COMMIT,
    MINISWE_TREE,
    PROGRAMBENCH_COMMIT,
    PROGRAMBENCH_TREE,
    TRANSFER_MODEL,
    TRANSFER_RUN_ROOT,
    verify_checkout,
)
from evidence_harness_mutation.model import FrozenModel, Sha256
from evidence_harness_mutation.prefixbench import PrefixBenchFileBinding

_PROGRAMBENCH_VERSION: Literal["1.2.4"] = "1.2.4"
_CATALOG_BYTES: Literal[5021] = 5021
_CATALOG_SHA256: Literal["1727a2e958a5ab9fe11c0218c79225e9ea673f0de02618a155c3c600bb9de938"] = (
    "1727a2e958a5ab9fe11c0218c79225e9ea673f0de02618a155c3c600bb9de938"
)
_SELECTION_NAMESPACE: Literal["miniswe-transfer-v1"] = "miniswe-transfer-v1"
_TRANSFER_TASKS: Literal[20] = 20

_IMPLEMENTATION_PATHS = (
    "docs/main-analysis-executable-design.md",
    "scripts/thesis_main_analysis.py",
    "src/evidence_harness_mutation/main_analysis_baselines.py",
    "src/evidence_harness_mutation/main_analysis_executable.py",
    "src/evidence_harness_mutation/main_analysis_reducers.py",
    "src/evidence_harness_mutation/main_analysis_report.py",
    "src/evidence_harness_mutation/main_analysis_statistics.py",
    "src/evidence_harness_mutation/main_analysis_transfer.py",
    "src/evidence_harness_mutation/main_analysis_transfer_runtime.py",
    "tests/mutation/test_main_analysis_baselines.py",
    "tests/mutation/test_main_analysis_executable.py",
    "tests/mutation/test_main_analysis_reducers.py",
    "tests/mutation/test_main_analysis_report.py",
    "tests/mutation/test_main_analysis_statistics.py",
    "tests/test_main_analysis_transfer.py",
    "tests/test_main_analysis_transfer_runtime.py",
    "tests/test_thesis_main_analysis_script.py",
    MINISWE_COHORT.as_posix(),
)

_MINISWE_SOURCE_PATHS = (
    "pyproject.toml",
    "src/minisweagent/__init__.py",
    "src/minisweagent/agents/default.py",
    "src/minisweagent/config/__init__.py",
    "src/minisweagent/config/benchmarks/programbench.yaml",
    "src/minisweagent/environments/__init__.py",
    "src/minisweagent/environments/docker.py",
    "src/minisweagent/models/__init__.py",
    "src/minisweagent/models/utils/actions_text.py",
    "src/minisweagent/models/utils/actions_toolcall.py",
    "src/minisweagent/models/utils/actions_toolcall_response.py",
    "src/minisweagent/run/benchmarks/programbench.py",
    "src/minisweagent/utils/serialize.py",
)
_PROGRAMBENCH_SOURCE_PATHS = (
    "pyproject.toml",
    "src/programbench/constants.py",
    "src/programbench/utils/load_data.py",
)
_CONFIRMATORY_PATHS = (
    "runs/terminal-bench-2/prefixbench-v1-test-20261002",
    TRANSFER_RUN_ROOT.as_posix(),
    "evaluation/prefixbench-v1-test-canonical.json",
    "evaluation/prefixbench-v1-test-readiness.json",
    "evaluation/prefixbench-v1-test-offline-campaign.json",
    "evaluation/prefixbench-v1-test-method-comparison.json",
    "evaluation/prefixbench-v1-test-reducer-comparison.json",
    "evaluation/miniswe-agent-transfer-v1.json",
    "evaluation/thesis-main-analysis-v1.json",
    "evaluation/thesis-tb21-sensitivity-v1.json",
    "evaluation/thesis-auxiliary-v1.json",
)


class MiniSweCohortTask(FrozenModel):
    index: int = Field(ge=1, le=20)
    instance_id: str = Field(min_length=1)
    selection_sha256: Sha256
    task_yaml_bytes: int = Field(ge=1)
    task_yaml_sha256: Sha256
    task_yaml_git_blob: str = Field(pattern=r"^[0-9a-f]{40}$")


class MiniSweCohort(FrozenModel):
    schema_version: Literal[1] = 1
    cohort_id: Literal["miniswe-transfer-v1"] = "miniswe-transfer-v1"
    benchmark: Literal["ProgramBench"] = "ProgramBench"
    programbench_version: Literal["1.2.4"] = _PROGRAMBENCH_VERSION
    programbench_commit: Literal["963063c9271cc40fa179977356782ea4582e0b0c"] = PROGRAMBENCH_COMMIT
    programbench_tree: Literal["0662e455d08e8c6e8d326a615d0cd4c6e448cf72"] = PROGRAMBENCH_TREE
    catalog_rule: Literal["task-directories-with-task.yaml-excluding-testorg-prefix"] = (
        "task-directories-with-task.yaml-excluding-testorg-prefix"
    )
    catalog_order: Literal["utf8-byte-order-one-id-per-line-final-lf"] = (
        "utf8-byte-order-one-id-per-line-final-lf"
    )
    catalog_tasks: Literal[200] = 200
    catalog_bytes: Literal[5021] = _CATALOG_BYTES
    catalog_sha256: Literal["1727a2e958a5ab9fe11c0218c79225e9ea673f0de02618a155c3c600bb9de938"] = (
        _CATALOG_SHA256
    )
    selection: Literal["sha256-namespace-nul-instance-id-then-instance-id-first-20"] = (
        "sha256-namespace-nul-instance-id-then-instance-id-first-20"
    )
    tasks: tuple[MiniSweCohortTask, ...] = Field(min_length=20, max_length=20)

    @model_validator(mode="after")
    def validate_tasks(self) -> Self:
        if tuple(task.index for task in self.tasks) != tuple(range(1, 21)):
            raise ValueError("mini-swe cohort indices must be contiguous")
        if len({task.instance_id for task in self.tasks}) != 20:
            raise ValueError("mini-swe cohort task identities must be unique")
        ranked = tuple(
            sorted(
                self.tasks,
                key=lambda task: (
                    bytes.fromhex(task.selection_sha256),
                    task.instance_id.encode(),
                ),
            )
        )
        if self.tasks != ranked:
            raise ValueError("mini-swe cohort tasks are not in selection order")
        for task in self.tasks:
            expected = hashlib.sha256(
                _SELECTION_NAMESPACE.encode() + b"\0" + task.instance_id.encode()
            ).hexdigest()
            if task.selection_sha256 != expected:
                raise ValueError("mini-swe cohort selection digest is stale")
        return self

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.model_dump(mode="json"))


class ExternalSourceBlob(FrozenModel):
    path: str = Field(min_length=1)
    bytes: int = Field(ge=1)
    sha256: Sha256
    git_blob: str = Field(pattern=r"^[0-9a-f]{40}$")


class ExternalRevision(FrozenModel):
    repository: str = Field(min_length=1)
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    tree: str = Field(pattern=r"^[0-9a-f]{40}$")
    sources: tuple[ExternalSourceBlob, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_sources(self) -> Self:
        paths = tuple(item.path for item in self.sources)
        if len(paths) != len(set(paths)):
            raise ValueError("external source paths must be unique")
        return self


class MainAnalysisExecutableRules(FrozenModel):
    deterministic_choices: Literal["sha256-first-8-bytes-mod-n-nul-framed-no-redraw"] = (
        "sha256-first-8-bytes-mod-n-nul-framed-no-redraw"
    )
    bootstrap_draws: Literal["sha256-counter-resample-and-draw-ordinals"] = (
        "sha256-counter-resample-and-draw-ordinals"
    )
    bootstrap_resamples: Literal[10000] = 10000
    percentile_interval: Literal["one-based-ranks-250-and-9750"] = "one-based-ranks-250-and-9750"
    transfer_workspace_digest: Literal["recursive-path-mode-type-content-sha256-v1"] = (
        "recursive-path-mode-type-content-sha256-v1"
    )
    transfer_model: Literal["openai/modelhub/gpt-5.6-terra"] = TRANSFER_MODEL


class MainAnalysisExecutable(FrozenModel):
    schema_version: Literal[1] = 1
    executable_id: Literal["thesis-main-analysis-executable-v1"] = (
        "thesis-main-analysis-executable-v1"
    )
    canonical_json: Literal["ensure-ascii-indent-2-sort-keys-trailing-newline"] = (
        "ensure-ascii-indent-2-sort-keys-trailing-newline"
    )
    source_set_algorithm: Literal["sha256-length-framed-path-content-v1"] = (
        "sha256-length-framed-path-content-v1"
    )
    protocol: PrefixBenchFileBinding
    protocol_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    protected_sources: MainAnalysisSourceSet
    implementation_sources: MainAnalysisSourceSet
    mini_swe: ExternalRevision
    programbench: ExternalRevision
    cohort: MiniSweCohort
    rules: MainAnalysisExecutableRules
    confirmatory_outcome_paths: tuple[str, ...] = Field(
        min_length=len(_CONFIRMATORY_PATHS),
        max_length=len(_CONFIRMATORY_PATHS),
    )

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        if self.protocol.path != MAIN_PROTOCOL.as_posix():
            raise ValueError("executable manifest protocol path has changed")
        if tuple(item.path for item in self.implementation_sources.files) != (
            _IMPLEMENTATION_PATHS
        ):
            raise ValueError("executable implementation source paths have changed")
        if self.mini_swe.commit != MINISWE_COMMIT or self.mini_swe.tree != MINISWE_TREE:
            raise ValueError("mini-swe-agent executable identity has changed")
        if (
            self.programbench.commit != PROGRAMBENCH_COMMIT
            or self.programbench.tree != PROGRAMBENCH_TREE
        ):
            raise ValueError("ProgramBench executable identity has changed")
        if self.confirmatory_outcome_paths != _CONFIRMATORY_PATHS:
            raise ValueError("confirmatory outcome paths have changed")
        if EXECUTABLE_PROTOCOL.as_posix() in {
            item.path for item in self.implementation_sources.files
        }:
            raise ValueError("executable manifest cannot bind itself")
        return self

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.model_dump(mode="json"))


class BoundMainAnalysisExecutable(FrozenModel):
    file: PrefixBenchFileBinding
    spec: MainAnalysisExecutable
    executable_commit: str = Field(pattern=r"^[0-9a-f]{40}$")


class MainAnalysisExecutablePreflight(FrozenModel):
    schema_version: Literal[1] = 1
    ready_for_confirmatory_execution: Literal[True] = True
    executable: BoundMainAnalysisExecutable
    cohort: PrefixBenchFileBinding


def build_miniswe_cohort(programbench_checkout: Path) -> MiniSweCohort:
    root = programbench_checkout.resolve()
    verify_checkout(
        root,
        expected_commit=PROGRAMBENCH_COMMIT,
        expected_tree=PROGRAMBENCH_TREE,
    )
    task_root = root / "src" / "programbench" / "data" / "tasks"
    task_directories = tuple(
        path
        for path in task_root.iterdir()
        if path.is_dir()
        and not path.name.startswith("testorg__")
        and (path / "task.yaml").is_file()
        and not (path / "task.yaml").is_symlink()
    )
    instance_ids = tuple(
        sorted((path.name for path in task_directories), key=lambda value: value.encode())
    )
    catalog = ("\n".join(instance_ids) + "\n").encode()
    if (
        len(instance_ids),
        len(catalog),
        hashlib.sha256(catalog).hexdigest(),
    ) != (200, _CATALOG_BYTES, _CATALOG_SHA256):
        raise ValueError("ProgramBench catalog differs from the frozen identity")
    ranked = sorted(
        instance_ids,
        key=lambda instance_id: (
            hashlib.sha256(_SELECTION_NAMESPACE.encode() + b"\0" + instance_id.encode()).digest(),
            instance_id.encode(),
        ),
    )[:_TRANSFER_TASKS]
    tasks: list[MiniSweCohortTask] = []
    for index, instance_id in enumerate(ranked, start=1):
        relative = Path("src/programbench/data/tasks") / instance_id / "task.yaml"
        data = _git_bytes(root, "show", f"{PROGRAMBENCH_COMMIT}:{relative.as_posix()}")
        tasks.append(
            MiniSweCohortTask(
                index=index,
                instance_id=instance_id,
                selection_sha256=hashlib.sha256(
                    _SELECTION_NAMESPACE.encode() + b"\0" + instance_id.encode()
                ).hexdigest(),
                task_yaml_bytes=len(data),
                task_yaml_sha256=hashlib.sha256(data).hexdigest(),
                task_yaml_git_blob=_git_text(
                    root,
                    "rev-parse",
                    f"{PROGRAMBENCH_COMMIT}:{relative.as_posix()}",
                ),
            )
        )
    return MiniSweCohort(tasks=tuple(tasks))


def freeze_main_analysis_executable(
    project_root: Path,
    *,
    mini_swe_checkout: Path,
    programbench_checkout: Path,
) -> tuple[MiniSweCohort, MainAnalysisExecutable]:
    root = _project_root(project_root)
    _require_preoutcome_state(root)
    protocol = load_main_analysis_protocol(root)
    verify_checkout(
        mini_swe_checkout,
        expected_commit=MINISWE_COMMIT,
        expected_tree=MINISWE_TREE,
    )
    cohort = build_miniswe_cohort(programbench_checkout)
    cohort_bytes = cohort.canonical_bytes()
    overrides = {MINISWE_COHORT.as_posix(): cohort_bytes}
    implementation = _source_set(root, _IMPLEMENTATION_PATHS, overrides=overrides)
    mini_swe = _external_revision(
        mini_swe_checkout,
        repository="https://github.com/SWE-agent/mini-swe-agent",
        commit=MINISWE_COMMIT,
        tree=MINISWE_TREE,
        paths=_MINISWE_SOURCE_PATHS,
    )
    programbench_paths = (
        *_PROGRAMBENCH_SOURCE_PATHS,
        *(f"src/programbench/data/tasks/{task.instance_id}/task.yaml" for task in cohort.tasks),
    )
    programbench = _external_revision(
        programbench_checkout,
        repository="https://github.com/facebookresearch/ProgramBench",
        commit=PROGRAMBENCH_COMMIT,
        tree=PROGRAMBENCH_TREE,
        paths=programbench_paths,
    )
    return cohort, MainAnalysisExecutable(
        protocol=protocol.file,
        protocol_commit=protocol.preregistration_commit,
        protected_sources=protocol.spec.protected_sources,
        implementation_sources=implementation,
        mini_swe=mini_swe,
        programbench=programbench,
        cohort=cohort,
        rules=MainAnalysisExecutableRules(),
        confirmatory_outcome_paths=_CONFIRMATORY_PATHS,
    )


def load_main_analysis_executable(
    project_root: Path,
    *,
    mini_swe_checkout: Path | None = None,
    programbench_checkout: Path | None = None,
) -> BoundMainAnalysisExecutable:
    root = _project_root(project_root)
    data = _read_regular_file(root / EXECUTABLE_PROTOCOL, "executable manifest")
    try:
        spec = MainAnalysisExecutable.model_validate_json(data)
    except ValidationError as exc:
        raise ValueError("invalid main-analysis executable manifest") from exc
    if data != spec.canonical_bytes():
        raise ValueError("main-analysis executable manifest is not canonical JSON")
    cohort_data = _read_regular_file(root / MINISWE_COHORT, "mini-swe cohort")
    try:
        cohort = MiniSweCohort.model_validate_json(cohort_data)
    except ValidationError as exc:
        raise ValueError("invalid mini-swe cohort") from exc
    if cohort_data != cohort.canonical_bytes() or cohort != spec.cohort:
        raise ValueError("mini-swe cohort does not match executable manifest")
    implementation = _source_set(root, _IMPLEMENTATION_PATHS)
    if implementation != spec.implementation_sources:
        raise ValueError("main-analysis implementation source binding is stale")
    protocol = load_main_analysis_protocol(root)
    if protocol.file != spec.protocol or protocol.preregistration_commit != spec.protocol_commit:
        raise ValueError("main-analysis executable protocol binding is stale")
    if protocol.spec.protected_sources != spec.protected_sources:
        raise ValueError("main-analysis protected source binding is stale")
    if mini_swe_checkout is not None:
        actual = _external_revision(
            mini_swe_checkout,
            repository=spec.mini_swe.repository,
            commit=MINISWE_COMMIT,
            tree=MINISWE_TREE,
            paths=_MINISWE_SOURCE_PATHS,
        )
        if actual != spec.mini_swe:
            raise ValueError("mini-swe-agent source binding is stale")
    if programbench_checkout is not None:
        paths = tuple(item.path for item in spec.programbench.sources)
        actual = _external_revision(
            programbench_checkout,
            repository=spec.programbench.repository,
            commit=PROGRAMBENCH_COMMIT,
            tree=PROGRAMBENCH_TREE,
            paths=paths,
        )
        if actual != spec.programbench:
            raise ValueError("ProgramBench source binding is stale")
    _validate_committed_input(root, EXECUTABLE_PROTOCOL, data)
    _validate_committed_input(root, MINISWE_COHORT, cohort_data)
    for binding in implementation.files:
        _validate_committed_input(
            root,
            Path(binding.path),
            _read_regular_file(root / binding.path, f"implementation source {binding.path}"),
        )
    executable_commit = _first_matching_commit(root, EXECUTABLE_PROTOCOL, data)
    head = _git_text(root, "rev-parse", "HEAD")
    _require_ancestor(root, spec.protocol_commit, executable_commit)
    _require_ancestor(root, executable_commit, head)
    return BoundMainAnalysisExecutable(
        file=_file_binding(EXECUTABLE_PROTOCOL.as_posix(), data),
        spec=spec,
        executable_commit=executable_commit,
    )


def preflight_main_analysis_executable(
    project_root: Path,
    *,
    mini_swe_checkout: Path,
    programbench_checkout: Path,
) -> MainAnalysisExecutablePreflight:
    root = _project_root(project_root)
    _require_preoutcome_state(root)
    executable = load_main_analysis_executable(
        root,
        mini_swe_checkout=mini_swe_checkout,
        programbench_checkout=programbench_checkout,
    )
    cohort_data = _read_regular_file(root / MINISWE_COHORT, "mini-swe cohort")
    return MainAnalysisExecutablePreflight(
        executable=executable,
        cohort=_file_binding(MINISWE_COHORT.as_posix(), cohort_data),
    )


def check_main_analysis_executable(
    project_root: Path,
    *,
    mini_swe_checkout: Path | None = None,
    programbench_checkout: Path | None = None,
) -> tuple[str, ...]:
    try:
        load_main_analysis_executable(
            project_root,
            mini_swe_checkout=mini_swe_checkout,
            programbench_checkout=programbench_checkout,
        )
    except (OSError, ValueError) as exc:
        return (f"invalid main-analysis executable manifest: {exc}",)
    return ()


def _require_preoutcome_state(project_root: Path) -> None:
    existing = tuple(path for path in _CONFIRMATORY_PATHS if (project_root / path).exists())
    if existing:
        raise ValueError(
            "main-analysis executable must precede outcome artifacts: " + ", ".join(existing)
        )
    historical = tuple(
        path for path in _CONFIRMATORY_PATHS if _path_has_git_history(project_root, path)
    )
    if historical:
        raise ValueError(
            "main-analysis outcome path already exists in Git history: " + ", ".join(historical)
        )


def _external_revision(
    checkout: Path,
    *,
    repository: str,
    commit: str,
    tree: str,
    paths: tuple[str, ...],
) -> ExternalRevision:
    root = checkout.resolve()
    verify_checkout(root, expected_commit=commit, expected_tree=tree)
    sources = tuple(_external_blob(root, commit, path) for path in paths)
    return ExternalRevision(
        repository=repository,
        commit=commit,
        tree=tree,
        sources=sources,
    )


def _external_blob(checkout: Path, commit: str, path: str) -> ExternalSourceBlob:
    data = _git_bytes(checkout, "show", f"{commit}:{path}")
    return ExternalSourceBlob(
        path=path,
        bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        git_blob=_git_text(checkout, "rev-parse", f"{commit}:{path}"),
    )


def _source_set(
    project_root: Path,
    paths: tuple[str, ...],
    *,
    overrides: dict[str, bytes] | None = None,
) -> MainAnalysisSourceSet:
    provided = overrides or {}
    digest = hashlib.sha256()
    bindings: list[PrefixBenchFileBinding] = []
    total = 0
    for relative in paths:
        data = provided.get(relative)
        if data is None:
            data = _read_regular_file(
                project_root / relative,
                f"main-analysis executable source {relative}",
            )
        encoded = relative.encode()
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
        total += len(data)
        bindings.append(_file_binding(relative, data))
    return MainAnalysisSourceSet(
        bytes=total,
        sha256=digest.hexdigest(),
        files=tuple(bindings),
    )


def _first_matching_commit(project_root: Path, path: Path, data: bytes) -> str:
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
    matching = tuple(
        commit for commit in commits if _git_path_or_none(project_root, commit, path) == data
    )
    if not matching:
        raise ValueError(f"Git history does not contain current bytes: {path}")
    return matching[-1]


def _path_has_git_history(project_root: Path, path: str) -> bool:
    return bool(_git_text(project_root, "log", "--all", "--format=%H", "--", path))


def _validate_committed_input(project_root: Path, relative: Path, data: bytes) -> None:
    if _git_bytes(project_root, "show", f"HEAD:{relative.as_posix()}") != data:
        raise ValueError(f"main-analysis executable input differs from Git HEAD: {relative}")


def _require_ancestor(project_root: Path, ancestor: str, descendant: str) -> None:
    completed = subprocess.run(
        ("git", "-C", str(project_root), "merge-base", "--is-ancestor", ancestor, descendant),
        check=False,
        capture_output=True,
    )
    if completed.returncode == 1:
        raise ValueError(f"required ancestor relation is absent: {ancestor} -> {descendant}")
    if completed.returncode:
        detail = completed.stderr.decode(errors="replace").strip()
        raise ValueError(f"cannot verify executable ancestry: {detail}")


def _git_path_or_none(project_root: Path, commit: str, path: Path) -> bytes | None:
    completed = subprocess.run(
        ("git", "-C", str(project_root), "show", f"{commit}:{path.as_posix()}"),
        check=False,
        capture_output=True,
    )
    return completed.stdout if completed.returncode == 0 else None


def _project_root(project_root: Path) -> Path:
    root = project_root.resolve()
    if not root.is_dir():
        raise ValueError(f"project root is not a directory: {root}")
    actual = Path(_git_text(root, "rev-parse", "--show-toplevel")).resolve()
    if actual != root:
        raise ValueError("project root is not the Git top level")
    return root


def _read_regular_file(path: Path, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} must be a regular file: {path}")
    return path.read_bytes()


def _file_binding(path: str, data: bytes) -> PrefixBenchFileBinding:
    return PrefixBenchFileBinding(
        path=path,
        bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def _git_text(checkout: Path, *args: str) -> str:
    return _git_bytes(checkout, *args).decode(errors="strict").strip()


def _git_bytes(checkout: Path, *args: str) -> bytes:
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
