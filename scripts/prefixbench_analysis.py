from __future__ import annotations

import argparse
import hashlib
import os
import tempfile
from pathlib import Path

from evidence_harness_mutation.prefixbench_analysis import (
    DEVELOPMENT_ANALYSIS,
    build_prefixbench_development_analysis,
    check_prefixbench_development_analysis,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = PROJECT_ROOT / DEVELOPMENT_ANALYSIS


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build or verify the fixed PrefixBench development analysis."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "build",
        help="Build the artifact-only development analysis.",
    )
    subparsers.add_parser(
        "check",
        help="Verify the committed development analysis.",
    )
    return parser.parse_args()


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main() -> int:
    args = parse_args()
    try:
        if args.command == "build":
            report = build_prefixbench_development_analysis(PROJECT_ROOT)
            data = report.canonical_bytes()
            _write_atomic(REPORT_PATH, data)
            print(
                f"tasks={report.tasks.tasks} "
                f"projected_attempts={report.tasks.projected_attempts} "
                f"verified_attempts={report.tasks.verified_attempts_of_projected.count} "
                f"scheduled={report.cases.scheduled_cases}"
            )
            print(
                f"applicable={report.cases.applicable_cases.count} "
                f"mutation_not_applicable="
                f"{report.cases.mutation_not_applicable_cases.count} "
                f"offline_invalid={report.cases.offline_invalid_cases.count} "
                f"oracle_equivalent={report.cases.oracle_equivalent_cases.count} "
                f"offline_violation={report.cases.offline_violations.count} "
                f"other_oracle_change={report.cases.other_oracle_changes.count}"
            )
            print(
                f"reduced_events={report.reduction.events.before}"
                f"->{report.reduction.events.after} "
                f"reduced_payload_members="
                f"{report.reduction.recursive_payload_members.before}"
                f"->{report.reduction.recursive_payload_members.after} "
                f"reduced_canonical_json_bytes="
                f"{report.reduction.compact_canonical_json_bytes.before}"
                f"->{report.reduction.compact_canonical_json_bytes.after}"
            )
            print(f"sha256={hashlib.sha256(data).hexdigest()}")
            return 0

        errors = check_prefixbench_development_analysis(
            PROJECT_ROOT,
            report_path=REPORT_PATH,
        )
        if errors:
            for error in errors:
                print(error)
            return 1
        print("PrefixBench development analysis verified")
        return 0
    except (OSError, ValueError) as exc:
        print(f"PrefixBench development analysis failed: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
