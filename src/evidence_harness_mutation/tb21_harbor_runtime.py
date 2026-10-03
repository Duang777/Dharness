from __future__ import annotations

import argparse
import fcntl
import hashlib
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import sysconfig
import tarfile
import zipfile
from dataclasses import dataclass
from email.parser import Parser
from pathlib import Path, PurePosixPath
from typing import Literal, Self

from pydantic import Field, ValidationError, model_validator

from evidence_harness_mutation.model import FrozenModel, Sha256

RUNTIME_SPEC = Path("config/tb21-harbor-runtime.json")
RUNTIME_MODULE = Path("src/evidence_harness_mutation/tb21_harbor_runtime.py")
RUNTIME_LAUNCHER = Path("tools/harbor-tb21-runtime")
RUNTIME_RECEIPT = "runtime.json"

_PYTHON_ENV_KEYS = (
    "PYTHONHOME",
    "PYTHONPATH",
    "PYTHONSTARTUP",
    "PYTHONUSERBASE",
)
_SHA1_PATTERN = r"^[0-9a-f]{40}$"
_PATCHED_PATHS = (
    "pyproject.toml",
    "src/harbor/models/job/config.py",
    "src/harbor/registry/client/git_repo.py",
    "tests/unit/test_git_repo_registry.py",
)


class UpstreamPin(FrozenModel):
    repository: Literal["https://github.com/laude-institute/harbor.git"]
    tag: Literal["v0.23.0"]
    tag_object: str = Field(pattern=_SHA1_PATTERN)
    commit: str = Field(pattern=_SHA1_PATTERN)
    source_tree: str = Field(pattern=_SHA1_PATTERN)
    distribution_name: Literal["harbor"]
    distribution_version: Literal["0.23.0+tb21.1"]


class PatchPin(FrozenModel):
    path: Literal["patches/harbor-v0.23.0-tb21-dataset-toml.patch"]
    sha256: Sha256
    changed_paths: tuple[str, ...] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def validate_changed_paths(self) -> Self:
        if self.changed_paths != _PATCHED_PATHS:
            raise ValueError("Harbor patch changed_paths do not match the reviewed path set")
        return self


class ProjectPin(FrozenModel):
    distribution_name: Literal["evidence-harness"]
    distribution_version: Literal["0.1.0"]
    pyproject_sha256: Sha256
    uv_lock_sha256: Sha256


class BuildPin(FrozenModel):
    uv_build_version: Literal["0.8.17"]
    source_date_epoch: int = Field(ge=1)


class AcceptancePin(FrozenModel):
    preflight_script: Literal["scripts/tb21_sensitivity.py"]
    preflight_script_sha256: Sha256
    executable_artifact: Literal["experiments/prefixbench-v1/tb21-sensitivity-executable-v1.json"]
    executable_artifact_sha256: Sha256
    executable_commit: str = Field(pattern=_SHA1_PATTERN)
    selector_path: Literal["tasks/dataset.toml"]
    expected_member_count: Literal[61]
    provider_calls: Literal[0]


class RuntimeSpec(FrozenModel):
    schema_version: Literal[1]
    upstream: UpstreamPin
    patch: PatchPin
    project: ProjectPin
    build: BuildPin
    acceptance: AcceptancePin

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.model_dump(mode="json"))


class ToolIdentity(FrozenModel):
    path: str = Field(min_length=1)
    version: str = Field(min_length=1)
    sha256: Sha256


class RuntimeInputs(FrozenModel):
    schema_version: Literal[1] = 1
    spec: RuntimeSpec
    spec_sha256: Sha256
    project_commit: str = Field(pattern=_SHA1_PATTERN)
    project_tree: str = Field(pattern=_SHA1_PATTERN)
    python: ToolIdentity
    uv: ToolIdentity
    uv_build_constraint_sha256: Sha256
    platform: str = Field(min_length=1)

    @property
    def runtime_id(self) -> Sha256:
        return hashlib.sha256(_canonical_json(self.model_dump(mode="json"))).hexdigest()


class ArtifactBinding(FrozenModel):
    path: str = Field(min_length=1)
    bytes: int = Field(ge=1)
    sha256: Sha256


class InstalledDistribution(FrozenModel):
    name: str = Field(min_length=1)
    version: str = Field(min_length=1)
    imported_path: str = Field(min_length=1)
    metadata_path: str = Field(min_length=1)
    distribution_files: int = Field(ge=1)
    distribution_sha256: Sha256
    direct_url: Literal[False]


class RuntimeProbe(FrozenModel):
    python_executable: str = Field(min_length=1)
    harbor: InstalledDistribution
    project: InstalledDistribution
    harbor_executable: str = Field(min_length=1)


class HarborInstallation(FrozenModel):
    distribution: InstalledDistribution
    executable: ArtifactBinding


class RuntimeReceipt(FrozenModel):
    schema_version: Literal[1] = 1
    runtime_id: Sha256
    inputs: RuntimeInputs
    harbor_wheel: ArtifactBinding
    project_wheel: ArtifactBinding
    harbor: HarborInstallation
    project: InstalledDistribution
    provider_calls: Literal[0] = 0

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.model_dump(mode="json"))


@dataclass(frozen=True)
class PreparedRuntime:
    root: Path
    receipt: RuntimeReceipt

    @property
    def python(self) -> Path:
        return self.root / "venv" / "bin" / "python"


@dataclass(frozen=True)
class PreflightResult:
    runtime: PreparedRuntime
    stdout: str


_RUNTIME_PROBE = r"""
import hashlib
import importlib
import importlib.metadata
import json
import pathlib
import shutil
import sys


def distribution(name, module_name):
    item = importlib.metadata.distribution(name)
    module = importlib.import_module(module_name)
    files = sorted(item.files or (), key=lambda value: str(value).encode())
    digest = hashlib.sha256()
    count = 0
    metadata_path = None
    direct_url = False
    for relative in files:
        relative_text = str(relative)
        if relative_text.endswith(".dist-info/direct_url.json"):
            direct_url = True
        first = pathlib.PurePosixPath(relative_text).parts[0]
        if first.endswith(".dist-info"):
            metadata_path = pathlib.Path(item.locate_file(first)).resolve()
        path = pathlib.Path(item.locate_file(relative))
        if path.is_symlink() or not path.is_file():
            continue
        data = path.read_bytes()
        encoded = relative_text.encode()
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
        count += 1
    if metadata_path is None:
        raise RuntimeError(f"{name} has no dist-info metadata directory")
    return {
        "name": item.metadata["Name"],
        "version": item.version,
        "imported_path": str(pathlib.Path(module.__file__).resolve()),
        "metadata_path": str(metadata_path),
        "distribution_files": count,
        "distribution_sha256": digest.hexdigest(),
        "direct_url": direct_url,
    }


print(
    json.dumps(
        {
            "python_executable": sys.executable,
            "harbor": distribution("harbor", "harbor"),
            "project": distribution(
                "evidence-harness",
                "evidence_harness_mutation",
            ),
            "harbor_executable": shutil.which("harbor"),
        },
        sort_keys=True,
    )
)
"""


def run_frozen_preflight(
    project_root: Path,
    tb21_checkout: Path,
    *,
    cache_root: Path | None = None,
) -> PreflightResult:
    root = _git_project_root(project_root)
    checkout_input = tb21_checkout.expanduser()
    if checkout_input.is_symlink() or not checkout_input.is_dir():
        raise ValueError(f"TB2.1 checkout must be a directory: {checkout_input}")
    checkout = checkout_input.resolve()
    spec, spec_data = _load_spec(root)
    inputs = _read_inputs(root, spec, spec_data)
    runtime = _ensure_runtime(root, inputs, cache_root or _default_cache_root())
    completed = _run(
        [
            str(runtime.python),
            "-I",
            str(root / spec.acceptance.preflight_script),
            "preflight",
            "--tb21-checkout",
            str(checkout),
        ],
        cwd=root,
        env=_runtime_environment(runtime.root),
        check=False,
    )
    expected = (
        f"ready_for_provider_execution=true executable_commit={spec.acceptance.executable_commit}"
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "Frozen TB2.1 preflight failed.\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
    if completed.stdout.splitlines() != [expected]:
        raise RuntimeError(
            f"Frozen TB2.1 preflight returned unexpected output: {completed.stdout!r}"
        )
    return PreflightResult(runtime=runtime, stdout=completed.stdout)


def _load_spec(project_root: Path) -> tuple[RuntimeSpec, bytes]:
    path = project_root / RUNTIME_SPEC
    data = _read_regular_file(path, "runtime specification")
    try:
        spec = RuntimeSpec.model_validate_json(data)
    except ValidationError as exc:
        raise ValueError(f"invalid TB2.1 Harbor runtime specification: {exc}") from exc
    if data != spec.canonical_bytes():
        raise ValueError("TB2.1 Harbor runtime specification is not canonical JSON")
    patch = _read_regular_file(project_root / spec.patch.path, "Harbor patch")
    if _sha256(patch) != spec.patch.sha256:
        raise ValueError("Harbor patch SHA-256 does not match the runtime specification")
    return spec, data


def _read_inputs(
    project_root: Path,
    spec: RuntimeSpec,
    spec_data: bytes,
) -> RuntimeInputs:
    _verify_committed_inputs(project_root, spec)
    commit = _git_text(project_root, "rev-parse", "--verify", "HEAD")
    tree = _git_text(project_root, "rev-parse", "--verify", "HEAD^{tree}")
    pyproject = _git_bytes(project_root, "show", f"{commit}:pyproject.toml")
    lock = _git_bytes(project_root, "show", f"{commit}:uv.lock")
    if _sha256(pyproject) != spec.project.pyproject_sha256:
        raise ValueError("committed pyproject.toml does not match the runtime specification")
    if _sha256(lock) != spec.project.uv_lock_sha256:
        raise ValueError("committed uv.lock does not match the runtime specification")
    if not ((3, 12) <= sys.version_info[:2] < (3, 14)):
        raise ValueError("the runtime builder requires Python >=3.12,<3.14")

    python_path = Path(sys.executable).resolve()
    uv_raw = shutil.which("uv")
    if uv_raw is None:
        raise ValueError("uv is not available on PATH")
    uv_path = Path(uv_raw).resolve()
    constraints = _build_constraints(spec)
    return RuntimeInputs(
        spec=spec,
        spec_sha256=_sha256(spec_data),
        project_commit=commit,
        project_tree=tree,
        python=_tool_identity(
            python_path,
            version=platform.python_version(),
        ),
        uv=_tool_identity(
            uv_path,
            version=_run([str(uv_path), "--version"]).stdout.strip(),
        ),
        uv_build_constraint_sha256=_sha256(constraints),
        platform=f"{sysconfig.get_platform()}:{platform.platform()}",
    )


def _verify_committed_inputs(project_root: Path, spec: RuntimeSpec) -> None:
    paths = (
        Path("pyproject.toml"),
        Path("uv.lock"),
        RUNTIME_SPEC,
        Path(spec.patch.path),
        Path(spec.acceptance.preflight_script),
        Path(spec.acceptance.executable_artifact),
        RUNTIME_MODULE,
        RUNTIME_LAUNCHER,
    )
    for relative in paths:
        live = _read_regular_file(project_root / relative, f"runtime input {relative}")
        try:
            committed = _git_bytes(project_root, "show", f"HEAD:{relative.as_posix()}")
        except RuntimeError as exc:
            raise ValueError(f"runtime input is not committed: {relative}") from exc
        if live != committed:
            raise ValueError(f"runtime input differs from Git HEAD: {relative}")
    acceptance = spec.acceptance
    if (
        _sha256(_read_regular_file(project_root / acceptance.preflight_script, "preflight"))
        != acceptance.preflight_script_sha256
    ):
        raise ValueError("frozen preflight script SHA-256 changed")
    if (
        _sha256(
            _read_regular_file(
                project_root / acceptance.executable_artifact,
                "frozen executable artifact",
            )
        )
        != acceptance.executable_artifact_sha256
    ):
        raise ValueError("frozen executable artifact SHA-256 changed")


def _ensure_runtime(
    project_root: Path,
    inputs: RuntimeInputs,
    cache_root: Path,
) -> PreparedRuntime:
    cache = cache_root.expanduser().resolve()
    cache.mkdir(parents=True, exist_ok=True)
    locks = cache / ".locks"
    locks.mkdir(exist_ok=True)
    runtime_root = cache / inputs.runtime_id
    lock_path = locks / f"{inputs.runtime_id}.lock"
    with lock_path.open("a+b") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            if runtime_root.exists():
                try:
                    return _verify_runtime(runtime_root, inputs)
                except (OSError, ValueError, ValidationError, RuntimeError):
                    _remove_runtime(runtime_root, cache)
            elif os.path.lexists(runtime_root):
                _remove_runtime(runtime_root, cache)
            runtime_root.mkdir(mode=0o755)
            try:
                return _build_runtime(project_root, runtime_root, inputs)
            except BaseException:
                _remove_runtime(runtime_root, cache)
                raise
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _build_runtime(
    project_root: Path,
    runtime_root: Path,
    inputs: RuntimeInputs,
) -> PreparedRuntime:
    work = runtime_root / "work"
    artifacts = runtime_root / "artifacts"
    project_source = work / "project"
    harbor_source = work / "harbor"
    project_wheels = artifacts / "project"
    harbor_wheels = artifacts / "harbor"
    work.mkdir()
    artifacts.mkdir()

    _extract_project_archive(project_root, inputs.project_commit, project_source)
    _checkout_and_patch_harbor(
        harbor_source,
        project_root / inputs.spec.patch.path,
        inputs.spec,
    )
    constraints = work / "build-constraints.txt"
    constraints.write_bytes(_build_constraints(inputs.spec))

    harbor_wheel = _build_wheel(
        harbor_source,
        harbor_wheels,
        inputs=inputs,
        constraints=constraints,
        expected_name=inputs.spec.upstream.distribution_name,
        expected_version=inputs.spec.upstream.distribution_version,
    )
    project_wheel = _build_wheel(
        project_source,
        project_wheels,
        inputs=inputs,
        constraints=constraints,
        expected_name=inputs.spec.project.distribution_name,
        expected_version=inputs.spec.project.distribution_version,
    )
    _install_runtime(
        project_source,
        runtime_root,
        inputs,
        artifact_root=artifacts,
    )
    shutil.rmtree(work)

    probe = _attest_runtime(runtime_root, inputs.spec)
    receipt = RuntimeReceipt(
        runtime_id=inputs.runtime_id,
        inputs=inputs,
        harbor_wheel=_artifact_binding(harbor_wheel, runtime_root),
        project_wheel=_artifact_binding(project_wheel, runtime_root),
        harbor=HarborInstallation(
            distribution=probe.harbor,
            executable=_artifact_binding(Path(probe.harbor_executable), runtime_root),
        ),
        project=probe.project,
    )
    _write_receipt(runtime_root / RUNTIME_RECEIPT, receipt)
    return PreparedRuntime(root=runtime_root, receipt=receipt)


def _verify_runtime(runtime_root: Path, inputs: RuntimeInputs) -> PreparedRuntime:
    path = runtime_root / RUNTIME_RECEIPT
    data = _read_regular_file(path, "runtime receipt")
    receipt = RuntimeReceipt.model_validate_json(data)
    if data != receipt.canonical_bytes():
        raise ValueError("runtime receipt is not canonical JSON")
    if receipt.runtime_id != inputs.runtime_id or receipt.inputs != inputs:
        raise ValueError("runtime receipt inputs changed")
    _verify_artifact_binding(runtime_root, receipt.harbor_wheel)
    _verify_artifact_binding(runtime_root, receipt.project_wheel)
    probe = _attest_runtime(runtime_root, inputs.spec)
    harbor = HarborInstallation(
        distribution=probe.harbor,
        executable=_artifact_binding(Path(probe.harbor_executable), runtime_root),
    )
    if harbor != receipt.harbor or probe.project != receipt.project:
        raise ValueError("installed runtime differs from its receipt")
    return PreparedRuntime(root=runtime_root, receipt=receipt)


def _extract_project_archive(
    project_root: Path,
    commit: str,
    destination: Path,
) -> None:
    archive = _git_bytes(project_root, "archive", "--format=tar", commit)
    destination.mkdir()
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as stream:
            for member in stream.getmembers():
                relative = PurePosixPath(member.name)
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError(f"unsafe project archive path: {member.name}")
                target = destination.joinpath(*relative.parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isfile():
                    raise ValueError(f"project archive member is not a regular file: {member.name}")
                source = stream.extractfile(member)
                if source is None:
                    raise ValueError(f"cannot read project archive member: {member.name}")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source.read())
                target.chmod(member.mode & 0o777)
    except tarfile.TarError as exc:
        raise ValueError(f"cannot read project Git archive: {exc}") from exc


def _checkout_and_patch_harbor(
    destination: Path,
    patch: Path,
    spec: RuntimeSpec,
) -> None:
    upstream = spec.upstream
    _run(["git", "init", "--quiet", str(destination)])
    _git_run(destination, "remote", "add", "origin", upstream.repository)
    _git_run(
        destination,
        "fetch",
        "--quiet",
        "--depth=1",
        "origin",
        f"refs/tags/{upstream.tag}:refs/tags/{upstream.tag}",
    )
    observed = {
        "tag object": _git_text(destination, "rev-parse", f"refs/tags/{upstream.tag}"),
        "commit": _git_text(
            destination,
            "rev-parse",
            f"refs/tags/{upstream.tag}^{{commit}}",
        ),
        "source tree": _git_text(
            destination,
            "rev-parse",
            f"refs/tags/{upstream.tag}^{{tree}}",
        ),
    }
    expected = {
        "tag object": upstream.tag_object,
        "commit": upstream.commit,
        "source tree": upstream.source_tree,
    }
    if observed != expected:
        raise ValueError(f"Harbor upstream identity mismatch: {observed}")
    _git_run(destination, "checkout", "--quiet", "--detach", upstream.commit)
    _git_run(destination, "apply", "--check", str(patch))
    _git_run(destination, "apply", str(patch))
    _git_run(destination, "diff", "--check")
    changed = tuple(
        sorted(
            line
            for line in _git_text(
                destination,
                "diff",
                "--name-only",
                "HEAD",
                "--",
            ).splitlines()
            if line
        )
    )
    if changed != spec.patch.changed_paths:
        raise ValueError(f"Harbor patch changed unexpected paths: {changed}")


def _build_wheel(
    source: Path,
    output: Path,
    *,
    inputs: RuntimeInputs,
    constraints: Path,
    expected_name: str,
    expected_version: str,
) -> Path:
    output.mkdir(parents=True)
    _run(
        [
            inputs.uv.path,
            "build",
            "--wheel",
            "--python",
            inputs.python.path,
            "--build-constraints",
            str(constraints),
            "--out-dir",
            str(output),
            str(source),
        ],
        env=_build_environment(inputs.spec),
    )
    wheels = tuple(output.glob("*.whl"))
    if len(wheels) != 1:
        raise ValueError(f"expected one wheel for {expected_name}, found {len(wheels)}")
    _verify_wheel_metadata(wheels[0], expected_name, expected_version)
    return wheels[0]


def _verify_wheel_metadata(path: Path, expected_name: str, expected_version: str) -> None:
    with zipfile.ZipFile(path) as archive:
        metadata_files = [
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        ]
        if len(metadata_files) != 1:
            raise ValueError(f"wheel has {len(metadata_files)} METADATA files: {path}")
        metadata = Parser().parsestr(archive.read(metadata_files[0]).decode())
    if metadata["Name"] != expected_name or metadata["Version"] != expected_version:
        raise ValueError(
            "wheel metadata mismatch: "
            f"{metadata['Name']}=={metadata['Version']}, expected "
            f"{expected_name}=={expected_version}"
        )


def _install_runtime(
    project_source: Path,
    runtime_root: Path,
    inputs: RuntimeInputs,
    *,
    artifact_root: Path,
) -> None:
    venv = runtime_root / "venv"
    _run(
        [
            inputs.uv.path,
            "venv",
            "--python",
            inputs.python.path,
            str(venv),
        ],
        env=_build_environment(inputs.spec),
    )
    runtime_python = venv / "bin" / "python"
    sync_env = _build_environment(inputs.spec)
    sync_env["UV_PROJECT_ENVIRONMENT"] = str(venv)
    _run(
        [
            inputs.uv.path,
            "sync",
            "--project",
            str(project_source),
            "--locked",
            "--no-dev",
            "--no-install-project",
            "--python",
            str(runtime_python),
        ],
        env=sync_env,
    )
    _run(
        [
            inputs.uv.path,
            "pip",
            "install",
            "--python",
            str(runtime_python),
            "--no-index",
            "--find-links",
            str(artifact_root / "harbor"),
            "--find-links",
            str(artifact_root / "project"),
            "--no-deps",
            "--reinstall",
            (
                f"{inputs.spec.upstream.distribution_name}"
                f"=={inputs.spec.upstream.distribution_version}"
            ),
            (
                f"{inputs.spec.project.distribution_name}"
                f"=={inputs.spec.project.distribution_version}"
            ),
        ],
        env=sync_env,
    )
    _run(
        [
            inputs.uv.path,
            "pip",
            "check",
            "--python",
            str(runtime_python),
        ],
        env=sync_env,
    )


def _attest_runtime(runtime_root: Path, spec: RuntimeSpec) -> RuntimeProbe:
    python = runtime_root / "venv" / "bin" / "python"
    completed = _run(
        [str(python), "-I", "-c", _RUNTIME_PROBE],
        env=_runtime_environment(runtime_root),
    )
    try:
        probe = RuntimeProbe.model_validate_json(completed.stdout)
    except ValidationError as exc:
        raise ValueError(f"invalid runtime attestation: {exc}") from exc
    if Path(probe.python_executable).absolute() != python.absolute():
        raise ValueError("runtime probe used a different Python executable")
    expected = {
        "harbor": (
            probe.harbor,
            spec.upstream.distribution_name,
            spec.upstream.distribution_version,
        ),
        "project": (
            probe.project,
            spec.project.distribution_name,
            spec.project.distribution_version,
        ),
    }
    venv = (runtime_root / "venv").resolve()
    for label, (distribution, name, version) in expected.items():
        if distribution.name != name or distribution.version != version:
            raise ValueError(
                f"{label} distribution identity mismatch: "
                f"{distribution.name}=={distribution.version}"
            )
        for field in ("imported_path", "metadata_path"):
            path = Path(getattr(distribution, field)).resolve()
            if not path.is_relative_to(venv):
                raise ValueError(f"{label} {field} is outside the runtime venv")
    executable = Path(probe.harbor_executable).resolve()
    expected_executable = (runtime_root / "venv" / "bin" / "harbor").resolve()
    if executable != expected_executable:
        raise ValueError("PATH selected a Harbor executable outside the runtime venv")
    return probe


def _runtime_environment(runtime_root: Path) -> dict[str, str]:
    env = _sanitized_environment()
    binary = runtime_root / "venv" / "bin"
    env["PATH"] = f"{binary}{os.pathsep}{env.get('PATH', '')}"
    return env


def _build_environment(spec: RuntimeSpec) -> dict[str, str]:
    env = _sanitized_environment()
    env["SOURCE_DATE_EPOCH"] = str(spec.build.source_date_epoch)
    env["UV_NO_PROGRESS"] = "1"
    return env


def _sanitized_environment() -> dict[str, str]:
    env = dict(os.environ)
    for key in (*_PYTHON_ENV_KEYS, "UV_PROJECT_ENVIRONMENT", "VIRTUAL_ENV"):
        env.pop(key, None)
    env["PYTHONNOUSERSITE"] = "1"
    return env


def _build_constraints(spec: RuntimeSpec) -> bytes:
    return f"uv_build=={spec.build.uv_build_version}\n".encode()


def _tool_identity(path: Path, *, version: str) -> ToolIdentity:
    data = _read_regular_file(path, f"tool executable {path}")
    return ToolIdentity(
        path=path.as_posix(),
        version=version,
        sha256=_sha256(data),
    )


def _artifact_binding(path: Path, root: Path) -> ArtifactBinding:
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"runtime artifact is outside the runtime root: {path}") from exc
    data = _read_regular_file(resolved, f"runtime artifact {relative}")
    return ArtifactBinding(
        path=relative.as_posix(),
        bytes=len(data),
        sha256=_sha256(data),
    )


def _verify_artifact_binding(root: Path, binding: ArtifactBinding) -> None:
    relative = PurePosixPath(binding.path)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"unsafe runtime artifact path: {binding.path}")
    actual = _artifact_binding(root.joinpath(*relative.parts), root)
    if actual != binding:
        raise ValueError(f"runtime artifact changed: {binding.path}")


def _write_receipt(path: Path, receipt: RuntimeReceipt) -> None:
    data = receipt.canonical_bytes()
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _remove_runtime(runtime_root: Path, cache_root: Path) -> None:
    if runtime_root.parent != cache_root:
        raise ValueError(f"refusing to remove path outside runtime cache: {runtime_root}")
    if runtime_root.is_symlink() or runtime_root.is_file():
        runtime_root.unlink()
    elif runtime_root.exists():
        shutil.rmtree(runtime_root)


def _read_regular_file(path: Path, label: str) -> bytes:
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} must be a regular file: {path}")
    return path.read_bytes()


def _sha256(data: bytes) -> Sha256:
    return hashlib.sha256(data).hexdigest()


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
            separators=(",", ": "),
        )
        + "\n"
    ).encode()


def _git_project_root(project_root: Path) -> Path:
    root = project_root.expanduser().resolve()
    actual = Path(_git_text(root, "rev-parse", "--show-toplevel")).resolve()
    if actual != root:
        raise ValueError(f"project root is not the Git root: {root}")
    return root


def _git_run(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return _run(["git", "-C", str(root), *arguments])


def _git_text(root: Path, *arguments: str) -> str:
    return _git_run(root, *arguments).stdout.strip()


def _git_bytes(root: Path, *arguments: str) -> bytes:
    command = ["git", "-C", str(root), *arguments]
    try:
        return subprocess.run(command, check=True, capture_output=True).stdout
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode(errors="replace")
        raise RuntimeError(
            f"command failed ({exc.returncode}): {' '.join(command)}\n{stderr}"
        ) from exc


def _run(
    command: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            command,
            cwd=cwd,
            env=env,
            check=check,
            capture_output=True,
            text=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"command failed ({exc.returncode}): {' '.join(command)}\n"
            f"stdout:\n{exc.stdout}\n"
            f"stderr:\n{exc.stderr}"
        ) from exc


def _default_cache_root() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "evidence-harness" / "tb21-harbor"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the pinned Harbor runtime and run the frozen TB2.1 preflight."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    preflight = subparsers.add_parser(
        "preflight",
        help="Build or reuse the runtime, then run the Provider-free frozen preflight.",
    )
    preflight.add_argument("--tb21-checkout", required=True, type=Path)
    preflight.add_argument("--cache-root", type=Path, default=_default_cache_root())
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    project_root = Path(__file__).resolve().parents[2]
    try:
        result = run_frozen_preflight(
            project_root,
            args.tb21_checkout,
            cache_root=args.cache_root,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"TB2.1 Harbor runtime failed: {exc}", file=sys.stderr)
        return 2
    print(result.stdout, end="")
    print(f"runtime_id={result.runtime.receipt.runtime_id}")
    print(f"runtime_root={result.runtime.root}")
    print(f"provider_calls={result.runtime.receipt.provider_calls}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
