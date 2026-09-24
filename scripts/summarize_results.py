from __future__ import annotations

import argparse
import json
from pathlib import Path

from evidence_harness.evaluation import load_matrix, render_markdown, summarize_results

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize a Terminal-Bench 2.0 Evidence Harness run."
    )
    parser.add_argument("results_dir", type=Path)
    parser.add_argument(
        "--matrix",
        type=Path,
        default=PROJECT_ROOT / "evaluation" / "matrix.json",
    )
    parser.add_argument("--json-out", type=Path)
    parser.add_argument("--markdown-out", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    matrix = load_matrix(args.matrix)
    summary = summarize_results(matrix, args.results_dir)
    json_text = json.dumps(summary.model_dump(mode="json"), indent=2) + "\n"
    markdown_text = render_markdown(summary)

    if args.json_out is not None:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json_text, encoding="utf-8")
    if args.markdown_out is not None:
        args.markdown_out.parent.mkdir(parents=True, exist_ok=True)
        args.markdown_out.write_text(markdown_text, encoding="utf-8")
    if args.json_out is None and args.markdown_out is None:
        print(markdown_text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
