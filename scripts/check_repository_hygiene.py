from __future__ import annotations

import argparse
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

_MACOS_HOME = b"/" + b"Users/"
_PLACEHOLDER_ROOT = b"/" + b"absolute/path/"
_TEMP_ROOT = b"/" + b"tmp/"
_FORBIDDEN_PATTERNS = (
    (
        "internal service URL",
        re.compile(rb"https?://[^\s\"']+\.bytedance\.(?:net|com)(?:[^\s\"']*)", re.IGNORECASE),
    ),
    (
        "local macOS home path",
        re.compile(rb"(?:file://)?" + re.escape(_MACOS_HOME) + rb"[^/\s\"']+/"),
    ),
    (
        "placeholder absolute path",
        re.compile(re.escape(_PLACEHOLDER_ROOT)),
    ),
    (
        "temporary credential file",
        re.compile(re.escape(_TEMP_ROOT) + rb"[A-Za-z0-9._-]*\.env\b"),
    ),
)


@dataclass(frozen=True, slots=True)
class HygieneViolation:
    path: Path
    line: int
    kind: str


def tracked_files(root: Path = PROJECT_ROOT) -> tuple[Path, ...]:
    completed = subprocess.run(
        ("git", "-C", str(root), "ls-files", "-z"),
        check=True,
        capture_output=True,
    )
    return tuple(root / value.decode() for value in completed.stdout.split(b"\0") if value)


def find_violations(
    paths: tuple[Path, ...],
    root: Path = PROJECT_ROOT,
) -> tuple[HygieneViolation, ...]:
    violations: list[HygieneViolation] = []
    for path in paths:
        if not path.is_file() or path.is_symlink():
            continue
        data = path.read_bytes()
        if b"\0" in data:
            continue
        for line_number, line in enumerate(data.splitlines(), start=1):
            for kind, pattern in _FORBIDDEN_PATTERNS:
                if kind == "temporary credential file" and path.suffix != ".md":
                    continue
                if pattern.search(line):
                    violations.append(
                        HygieneViolation(
                            path=path.resolve().relative_to(root.resolve()),
                            line=line_number,
                            kind=kind,
                        )
                    )
    return tuple(violations)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reject machine-specific paths and internal service URLs in tracked files."
    )
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    violations = find_violations(tracked_files(root), root)
    for violation in violations:
        print(f"{violation.path}:{violation.line}: {violation.kind}")
    if violations:
        return 1
    print("Repository hygiene verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
