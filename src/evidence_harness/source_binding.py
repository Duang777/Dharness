from __future__ import annotations

import hashlib
import io
import subprocess
import tarfile
from collections.abc import Iterable
from pathlib import Path
from typing import TypedDict

from evidence_harness.protocol import ProducerAttestation


class RuntimeSourceFileBinding(TypedDict):
    path: str
    bytes: int
    sha256: str


class RuntimeSourceBinding(TypedDict):
    path: str
    bytes: int
    sha256: str
    files: list[RuntimeSourceFileBinding]


_RUNTIME_SOURCE_DESCRIPTION = "pyproject.toml, uv.lock, and src/evidence_harness/**/*.py"


def runtime_project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def runtime_source_binding(project_root: Path) -> RuntimeSourceBinding:
    root = project_root.resolve()
    entries: list[tuple[str, bytes]] = []
    for path in (
        project_root / "pyproject.toml",
        project_root / "uv.lock",
        *sorted((project_root / "src" / "evidence_harness").rglob("*.py")),
    ):
        if not path.exists():
            continue
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"runtime source path must be a regular file: {path}")
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(root).as_posix()
        except ValueError as exc:
            raise ValueError(f"runtime source path is outside the project: {path}") from exc
        entries.append((relative, resolved.read_bytes()))
    return _binding_from_entries(entries)


def archived_runtime_source_binding(
    project_root: Path,
    revision: str = "HEAD",
) -> RuntimeSourceBinding:
    root = _git_project_root(project_root)
    archive = _run_git_bytes(
        root,
        "archive",
        "--format=tar",
        revision,
        "--",
        "pyproject.toml",
        "uv.lock",
        "src/evidence_harness",
    )
    entries: list[tuple[str, bytes]] = []
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as stream:
            for member in stream.getmembers():
                path = member.name.removeprefix("./")
                if not _is_runtime_source_path(path):
                    continue
                if not member.isfile():
                    raise ValueError(f"archived runtime source is not a regular file: {path}")
                source = stream.extractfile(member)
                if source is None:
                    raise ValueError(f"cannot read archived runtime source: {path}")
                entries.append((path, source.read()))
    except tarfile.TarError as exc:
        raise ValueError(f"cannot read Git source archive: {exc}") from exc
    return _binding_from_entries(entries)


def attest_git_runtime_source(project_root: Path) -> ProducerAttestation:
    root = _git_project_root(project_root)
    workspace = runtime_source_binding(root)
    archived = archived_runtime_source_binding(root)
    if workspace != archived:
        raise ValueError("runtime source differs from git archive HEAD")
    return ProducerAttestation(
        commit=_run_git_text(root, "rev-parse", "--verify", "HEAD"),
        tree=_run_git_text(root, "rev-parse", "--verify", "HEAD^{tree}"),
        source_sha256=workspace["sha256"],
    )


def verify_git_runtime_source(
    project_root: Path,
    expected: ProducerAttestation,
) -> ProducerAttestation:
    actual = attest_git_runtime_source(project_root)
    if actual != expected:
        changed = [
            field
            for field in ProducerAttestation.model_fields
            if getattr(actual, field) != getattr(expected, field)
        ]
        raise ValueError("runtime producer attestation changed: " + ", ".join(changed))
    return actual


def _binding_from_entries(
    entries: Iterable[tuple[str, bytes]],
) -> RuntimeSourceBinding:
    ordered = sorted(entries, key=lambda item: _runtime_source_order(item[0]))
    if not ordered:
        raise ValueError("runtime source set is empty")
    paths = [path for path, _data in ordered]
    if len(paths) != len(set(paths)):
        raise ValueError("runtime source set contains duplicate paths")
    digest = hashlib.sha256()
    files: list[RuntimeSourceFileBinding] = []
    for relative, data in ordered:
        encoded_path = relative.encode()
        digest.update(len(encoded_path).to_bytes(8, "big"))
        digest.update(encoded_path)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
        files.append(
            {
                "path": relative,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    return {
        "path": _RUNTIME_SOURCE_DESCRIPTION,
        "bytes": sum(int(item["bytes"]) for item in files),
        "sha256": digest.hexdigest(),
        "files": files,
    }


def _git_project_root(project_root: Path) -> Path:
    root = project_root.resolve()
    actual = Path(_run_git_text(root, "rev-parse", "--show-toplevel")).resolve()
    if actual != root:
        raise ValueError(f"runtime project root is not the Git top level: {root}")
    return root


def _run_git_text(project_root: Path, *args: str) -> str:
    return _run_git(project_root, *args).stdout.decode().strip()


def _run_git_bytes(project_root: Path, *args: str) -> bytes:
    return _run_git(project_root, *args).stdout


def _run_git(project_root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(project_root), *args],
            check=False,
            capture_output=True,
        )
    except OSError as exc:
        raise ValueError(f"cannot execute Git: {exc}") from exc
    if completed.returncode:
        detail = completed.stderr.decode(errors="replace").strip()
        raise ValueError(f"Git {' '.join(args)} failed: {detail}")
    return completed


def _is_runtime_source_path(path: str) -> bool:
    return path in {"pyproject.toml", "uv.lock"} or (
        path.startswith("src/evidence_harness/") and path.endswith(".py")
    )


def _runtime_source_order(path: str) -> tuple[int, str]:
    if path == "pyproject.toml":
        return 0, path
    if path == "uv.lock":
        return 1, path
    return 2, path
