from __future__ import annotations

import argparse
import sys
from pathlib import Path

from evidence_harness_mutation.historical import (
    HistoricalInfrastructureError,
    HistoricalRunRequest,
    run_historical7,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = Path("evaluation/historical-7.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the frozen Historical-7 defect suite.")
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Report path, relative to the repository root by default.",
    )
    parser.add_argument(
        "--timeout-sec",
        type=float,
        default=30.0,
        help="Maximum runtime for each isolated historical probe.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = run_historical7(
            HistoricalRunRequest(
                repo_root=PROJECT_ROOT,
                output_path=args.output,
                subprocess_timeout_sec=args.timeout_sec,
            )
        )
    except HistoricalInfrastructureError as exc:
        print(f"Historical-7 infrastructure error: {exc}", file=sys.stderr)
        return 2

    output_path = args.output if args.output.is_absolute() else PROJECT_ROOT / args.output
    print(f"Historical-7 {report.verdict}: {output_path}")
    if report.verdict == "passed":
        return 0
    if report.verdict == "failed":
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
