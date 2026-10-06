from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import sysconfig
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from evidence_harness_mutation import tb21_harbor_runtime as base
from evidence_harness_mutation._tb21_luna_harbor import provider_environment
from evidence_harness_mutation._tb21_luna_manifest import BoundExecutable, load_manifest

RUNTIME_MODULE = Path("src/evidence_harness_mutation/tb21_luna_runtime.py")
RUNTIME_LAUNCHER = Path("tools/harbor-tb21-luna")
_PROVIDER_KEYS = ("OPENAI_API_KEY", "OPENAI_BASE_URL")
_ENVIRONMENT_LOCK = threading.Lock()


@dataclass(frozen=True)
class PreflightResult:
    runtime: base.PreparedRuntime
    stdout: str


@dataclass(frozen=True)
class CollectionResult:
    runtime: base.PreparedRuntime
    returncode: int


def prepare_luna_runtime(
    project_root: Path,
    *,
    cache_root: Path | None = None,
) -> base.PreparedRuntime:
    root = base._git_project_root(project_root)
    executable = load_manifest(root)
    spec, spec_data = base._load_spec(root)
    inputs = _read_inputs(root, executable, spec, spec_data)
    with _without_provider_environment():
        return base._ensure_runtime(
            root,
            inputs,
            cache_root or _default_cache_root(),
        )


def run_frozen_preflight(
    project_root: Path,
    tb21_checkout: Path,
    *,
    cache_root: Path | None = None,
) -> PreflightResult:
    root, checkout = _validated_paths(project_root, tb21_checkout)
    runtime = prepare_luna_runtime(root, cache_root=cache_root)
    completed = base._run(
        _installed_command(runtime, "preflight", checkout),
        cwd=root,
        env=_preflight_environment(runtime.root),
        check=False,
    )
    expected = (
        "ready_for_provider_execution=true "
        f"executable_commit={runtime.receipt.inputs.project_commit}"
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "Frozen Luna preflight failed.\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )
    if completed.stdout.splitlines() != [expected]:
        raise RuntimeError(
            f"Frozen Luna preflight returned unexpected output: {completed.stdout!r}"
        )
    return PreflightResult(runtime=runtime, stdout=completed.stdout)


def run_frozen_collection(
    project_root: Path,
    tb21_checkout: Path,
    *,
    cache_root: Path | None = None,
) -> CollectionResult:
    root, checkout = _validated_paths(project_root, tb21_checkout)
    preflight = run_frozen_preflight(root, checkout, cache_root=cache_root)
    completed = subprocess.run(
        _installed_command(preflight.runtime, "collect", checkout),
        cwd=root,
        env=_collection_environment(preflight.runtime.root),
        check=False,
    )
    return CollectionResult(runtime=preflight.runtime, returncode=completed.returncode)


def _read_inputs(
    project_root: Path,
    executable: BoundExecutable,
    spec: base.RuntimeSpec,
    spec_data: bytes,
) -> base.RuntimeInputs:
    commit = executable.executable_commit
    _verify_committed_inputs(project_root, executable, spec, commit)
    pyproject = base._git_bytes(project_root, "show", f"{commit}:pyproject.toml")
    lock = base._git_bytes(project_root, "show", f"{commit}:uv.lock")
    if base._sha256(pyproject) != spec.project.pyproject_sha256:
        raise ValueError("committed pyproject.toml does not match the runtime specification")
    if base._sha256(lock) != spec.project.uv_lock_sha256:
        raise ValueError("committed uv.lock does not match the runtime specification")
    if not ((3, 12) <= sys.version_info[:2] < (3, 14)):
        raise ValueError("the runtime builder requires Python >=3.12,<3.14")

    python_path = Path(sys.executable).resolve()
    uv_raw = shutil.which("uv")
    if uv_raw is None:
        raise ValueError("uv is not available on PATH")
    uv_path = Path(uv_raw).resolve()
    return base.RuntimeInputs(
        spec=spec,
        spec_sha256=base._sha256(spec_data),
        project_commit=commit,
        project_tree=base._git_text(
            project_root,
            "rev-parse",
            "--verify",
            f"{commit}^{{tree}}",
        ),
        python=base._tool_identity(
            python_path,
            version=platform.python_version(),
        ),
        uv=base._tool_identity(
            uv_path,
            version=base._run([str(uv_path), "--version"]).stdout.strip(),
        ),
        uv_build_constraint_sha256=base._sha256(base._build_constraints(spec)),
        platform=f"{sysconfig.get_platform()}:{platform.platform()}",
    )


def _verify_committed_inputs(
    project_root: Path,
    executable: BoundExecutable,
    spec: base.RuntimeSpec,
    commit: str,
) -> None:
    paths = (
        Path("pyproject.toml"),
        Path("uv.lock"),
        base.RUNTIME_SPEC,
        Path(spec.patch.path),
        Path(executable.file.path),
        RUNTIME_MODULE,
        RUNTIME_LAUNCHER,
    )
    for relative in paths:
        live = base._read_regular_file(project_root / relative, f"Luna runtime input {relative}")
        try:
            committed = base._git_bytes(
                project_root,
                "show",
                f"{commit}:{relative.as_posix()}",
            )
        except RuntimeError as exc:
            raise ValueError(f"Luna runtime input is not committed at E: {relative}") from exc
        if live != committed:
            raise ValueError(f"Luna runtime input differs from executable commit: {relative}")


def _validated_paths(project_root: Path, tb21_checkout: Path) -> tuple[Path, Path]:
    root = base._git_project_root(project_root)
    checkout_input = tb21_checkout.expanduser()
    if checkout_input.is_symlink() or not checkout_input.is_dir():
        raise ValueError(f"TB2.1 checkout must be a directory: {checkout_input}")
    return root, checkout_input.resolve()


def _installed_command(
    runtime: base.PreparedRuntime,
    command: str,
    checkout: Path,
) -> list[str]:
    return [
        str(runtime.python),
        "-I",
        "-m",
        "evidence_harness_mutation.tb21_luna_rq2",
        command,
        "--tb21-checkout",
        str(checkout),
    ]


def _is_provider_variable(key: str) -> bool:
    upper = key.upper()
    return upper.startswith(("OPENAI_", "AZURE_OPENAI_", "ANTHROPIC_")) or upper.endswith(
        ("_API_KEY", "_ACCESS_TOKEN", "_SECRET")
    )


@contextmanager
def _without_provider_environment():
    with _ENVIRONMENT_LOCK:
        removed = {
            key: os.environ.pop(key) for key in tuple(os.environ) if _is_provider_variable(key)
        }
        try:
            yield
        finally:
            os.environ.update(removed)


def _preflight_environment(runtime_root: Path) -> dict[str, str]:
    environment = base._runtime_environment(runtime_root)
    for key in tuple(environment):
        if _is_provider_variable(key):
            environment.pop(key)
    return environment


def _collection_environment(runtime_root: Path) -> dict[str, str]:
    environment, _attestation = provider_environment(os.environ)
    for key in (*base._PYTHON_ENV_KEYS, "UV_PROJECT_ENVIRONMENT", "VIRTUAL_ENV"):
        environment.pop(key, None)
    binary = runtime_root / "venv" / "bin"
    environment["PATH"] = f"{binary}{os.pathsep}{environment.get('PATH', '')}"
    environment["PYTHONNOUSERSITE"] = "1"
    return environment


def _default_cache_root() -> Path:
    base_path = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base_path / "evidence-harness" / "tb21-luna"


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build the pinned runtime and execute the frozen Luna replication."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("preflight", "collect"):
        action = subparsers.add_parser(command)
        action.add_argument("--tb21-checkout", required=True, type=Path)
        action.add_argument("--cache-root", type=Path, default=_default_cache_root())
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    project_root = Path(__file__).resolve().parents[2]
    try:
        if args.command == "preflight":
            result = run_frozen_preflight(
                project_root,
                args.tb21_checkout,
                cache_root=args.cache_root,
            )
            print(result.stdout, end="")
            print(f"runtime_id={result.runtime.receipt.runtime_id}")
            print(f"runtime_root={result.runtime.root}")
            print(f"provider_calls={result.runtime.receipt.provider_calls}")
            return 0

        collection_result = run_frozen_collection(
            project_root,
            args.tb21_checkout,
            cache_root=args.cache_root,
        )
        return collection_result.returncode
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"TB2.1 Luna runtime failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
