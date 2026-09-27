from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any


def runtime_source_binding(project_root: Path) -> dict[str, Any]:
    paths = [
        path
        for path in (
            project_root / "pyproject.toml",
            project_root / "uv.lock",
        )
        if path.is_file()
    ]
    paths.extend(sorted((project_root / "src" / "evidence_harness").rglob("*.py")))
    if not paths:
        raise ValueError("runtime source set is empty")

    digest = hashlib.sha256()
    files: list[dict[str, str | int]] = []
    for path in paths:
        resolved = path.resolve()
        try:
            relative = resolved.relative_to(project_root.resolve()).as_posix()
        except ValueError as exc:
            raise ValueError(f"runtime source path is outside the project: {path}") from exc
        data = resolved.read_bytes()
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
        "path": "pyproject.toml, uv.lock, and src/evidence_harness/**/*.py",
        "bytes": sum(int(item["bytes"]) for item in files),
        "sha256": digest.hexdigest(),
        "files": files,
    }
