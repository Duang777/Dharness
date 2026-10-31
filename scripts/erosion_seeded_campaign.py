from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from evidence_harness_mutation.candidate_gate import CandidateGateError
from evidence_harness_mutation.erosion_campaign import ErosionCampaignError, run_seeded_campaign

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS = PROJECT_ROOT / "experiments/erosion-v1/seeded-corpus-v0.json"
DEFAULT_OUTPUT = PROJECT_ROOT / "evaluation/erosion-v1-seeded-gate-results.json"
DEFAULT_TIMING = PROJECT_ROOT / "evaluation/erosion-v1-seeded-gate-timing.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the candidate gate on every seed patch and write the canonical report."
    )
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--timing",
        type=Path,
        default=DEFAULT_TIMING,
        help="Wall-clock seconds per seed; kept out of the canonical report.",
    )
    parser.add_argument("--jobs", type=int, default=4)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        report, timing = run_seeded_campaign(args.corpus, repo_root=PROJECT_ROOT, jobs=args.jobs)
    except (OSError, ValueError, CandidateGateError, ErosionCampaignError) as exc:
        print(f"seeded campaign failed: {exc}", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(report.canonical_bytes())
    args.timing.write_text(json.dumps(timing, indent=2, sort_keys=True) + "\n")
    for item in report.categories:
        print(
            f"{item.label:7} {item.category:24} rejected {item.rejected.hits}/{item.rejected.total}"
            f" misclassified={list(item.misclassified_seeds)}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
