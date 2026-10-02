from __future__ import annotations

import argparse
import sys
from pathlib import Path

from evidence_harness_mutation.thesis_tables import (
    build_thesis_tables,
    check_thesis_tables,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build or verify the fixed thesis table projection."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "build",
        help="Build the canonical table bundle and Markdown once.",
    )
    subparsers.add_parser(
        "check",
        help="Verify the canonical projection without writing files.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "build":
            result = build_thesis_tables(PROJECT_ROOT)
            print(f"bundle_state={result.bundle_state} markdown_state={result.markdown_state}")
            print(f"bundle_sha256={result.bundle_sha256}")
            print(f"markdown_sha256={result.markdown_sha256}")
            return 0
        errors = check_thesis_tables(PROJECT_ROOT)
        if errors:
            for error in errors:
                print(error)
            return 1
        print("Thesis table projection verified")
        return 0
    except (OSError, TypeError, ValueError) as exc:
        print(f"Thesis table projection failed: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
