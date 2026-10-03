from __future__ import annotations

import argparse
import sys
from pathlib import Path

from evidence_harness_mutation.thesis_reproduction import (
    ReproductionState,
    check_thesis_reproduction,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify the fixed thesis reproduction package.")
    return parser.parse_args()


def main() -> int:
    parse_args()
    result = check_thesis_reproduction(PROJECT_ROOT)
    print(result.canonical_bytes().decode(), end="")
    return 1 if result.state is ReproductionState.PARTIAL_INVALID else 0


if __name__ == "__main__":
    sys.exit(main())
