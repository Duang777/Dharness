from __future__ import annotations

import argparse
import hashlib
import os
import tempfile
from pathlib import Path

from evidence_harness_mutation import (
    build_prefixbench_development_campaign,
    check_prefixbench_development_campaign,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = PROJECT_ROOT / "evaluation" / "prefixbench-v1-development-offline-campaign.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build or verify the frozen PrefixBench development offline campaign."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "build",
        help="Build the fixed 28-task development campaign.",
    )
    subparsers.add_parser(
        "check",
        help="Verify the committed development campaign.",
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
            report = build_prefixbench_development_campaign(PROJECT_ROOT)
            data = report.canonical_bytes()
            _write_atomic(REPORT_PATH, data)
            outcomes = report.summary.outcomes
            print(
                f"tasks={report.summary.tasks} "
                f"projected_attempts={report.summary.projected_attempts} "
                f"verified_attempts={report.summary.verified_attempts} "
                f"scheduled={outcomes.scheduled}"
            )
            print(
                f"mutation_not_applicable={outcomes.mutation_not_applicable} "
                f"offline_invalid={outcomes.offline_invalid} "
                f"oracle_equivalent={outcomes.oracle_equivalent} "
                f"offline_violation={outcomes.offline_violation} "
                f"other_oracle_change={outcomes.other_oracle_change}"
            )
            print(f"sha256={hashlib.sha256(data).hexdigest()}")
            return 0

        errors = check_prefixbench_development_campaign(
            PROJECT_ROOT,
            report_path=REPORT_PATH,
        )
        if errors:
            for error in errors:
                print(error)
            return 1
        print("PrefixBench development offline campaign verified")
        return 0
    except (OSError, ValueError) as exc:
        print(f"PrefixBench development offline campaign failed: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
