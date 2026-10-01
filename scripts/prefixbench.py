from __future__ import annotations

import argparse
import hashlib
import os
import tempfile
from pathlib import Path

from evidence_harness_mutation import (
    PrefixBenchReadiness,
    check_prefixbench_readiness,
    inspect_prefixbench,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CANONICAL = PROJECT_ROOT / "evaluation" / "canonical-89.json"
DEFAULT_MATRIX = PROJECT_ROOT / "evaluation" / "matrix-89.json"
DEFAULT_REPORT = PROJECT_ROOT / "evaluation" / "prefixbench-readiness.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build or verify the frozen PrefixBench readiness report."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="Build the readiness report.")
    build.add_argument("--canonical", type=Path, default=DEFAULT_CANONICAL)
    build.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    build.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    build.add_argument("--out", type=Path, default=DEFAULT_REPORT)

    check = subparsers.add_parser("check", help="Verify the committed readiness report.")
    check.add_argument("--canonical", type=Path, default=DEFAULT_CANONICAL)
    check.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    check.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    check.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    return parser.parse_args()


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    args = parse_args()
    try:
        if args.command == "build":
            report = inspect_prefixbench(
                args.canonical,
                args.matrix,
                args.project_root,
            )
            data = report.canonical_bytes()
            _write_atomic(args.out, data)
            admitted = (
                report.summary.admitted
                if isinstance(report, PrefixBenchReadiness)
                else report.summary.source_admitted
            )
            print(
                f"status={report.status.value} "
                f"tasks={report.summary.tasks} "
                f"admitted={admitted} "
                f"development={report.summary.development} "
                f"test={report.summary.test}"
            )
            print(f"sha256={hashlib.sha256(data).hexdigest()}")
            return 0

        errors = check_prefixbench_readiness(
            readiness_path=args.report,
            canonical_path=args.canonical,
            matrix_path=args.matrix,
            project_root=args.project_root,
        )
        if errors:
            for error in errors:
                print(error)
            return 1
        print("PrefixBench readiness artifact verified")
        return 0
    except (OSError, ValueError) as exc:
        print(f"PrefixBench readiness failed: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
