from __future__ import annotations

import argparse
import sys
from pathlib import Path

from evidence_harness_mutation.candidate_gate import (
    DEFAULT_SUITE_RELATIVE_PATH,
    DEFAULT_TIMEOUT_SEC,
    CandidateGateError,
    GateDecision,
    GateRequest,
    run_candidate_gate,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXIT_CODES = {
    GateDecision.ACCEPT: 0,
    GateDecision.REJECT: 1,
    GateDecision.OUT_OF_SCOPE: 2,
    GateDecision.NOT_APPLICABLE: 2,
    GateDecision.INFRA_ERROR: 3,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Decide whether base + patch still satisfies the frozen guard suite."
    )
    parser.add_argument("--patch", type=Path, required=True, help="Unified-diff patch file.")
    parser.add_argument("--base", default="HEAD", help="Base revision the patch applies to.")
    parser.add_argument(
        "--suite",
        type=Path,
        default=PROJECT_ROOT / DEFAULT_SUITE_RELATIVE_PATH,
        help="Guard suite registry.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SEC,
        help="Maximum seconds for each candidate subprocess.",
    )
    parser.add_argument("--output", type=Path, help="Write the verdict here instead of stdout.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        patch = args.patch.read_bytes()
        verdict = run_candidate_gate(
            GateRequest(
                repo_root=PROJECT_ROOT,
                patch=patch,
                base=args.base,
                suite_path=args.suite,
                timeout_sec=args.timeout,
            )
        )
    except (OSError, CandidateGateError) as exc:
        print(f"harness gate infrastructure error: {exc}", file=sys.stderr)
        return EXIT_CODES[GateDecision.INFRA_ERROR]

    data = verdict.report_bytes()
    if args.output is None:
        print(data.decode(), end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(data)
    return EXIT_CODES[verdict.decision]


if __name__ == "__main__":
    raise SystemExit(main())
