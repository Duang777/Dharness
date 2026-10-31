from __future__ import annotations

import argparse
import sys
from pathlib import Path

from evidence_harness_mutation.public_audit import PublicAuditError, build_public_audit

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LABELS = PROJECT_ROOT / "experiments/erosion-v1/public-audit-labels-v1.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "evaluation/erosion-v1-public-audit.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Join frozen erosion labels with the Meta-Harness TB2 search history."
    )
    parser.add_argument(
        "--source",
        type=Path,
        required=True,
        help="Downloaded evo.js; its sha256 must match the labels file.",
    )
    parser.add_argument("--labels", type=Path, default=DEFAULT_LABELS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report = build_public_audit(args.source.read_bytes(), args.labels.read_bytes())
    except (OSError, ValueError, PublicAuditError) as exc:
        print(f"public audit failed: {exc}", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(report.canonical_bytes())
    for item in report.directions:
        print(
            f"{item.direction:11} clear={item.clear} ambiguous={item.ambiguous} "
            f"above_parent={item.scored_above_parent}/{item.scored}"
        )
    for step in report.frontier:
        print(
            f"iter {step.iteration:2} best={step.best} {step.best_rate} "
            f"eroding={step.eroding_ancestors}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
