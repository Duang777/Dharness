from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from evidence_harness_mutation.candidate_gate import (
    CandidateGateError,
    GateDecision,
    GateRequest,
    GuardComponent,
    _canonical_json,
    run_candidate_gate,
)
from evidence_harness_mutation.erosion_campaign import load_seed_corpus
from evidence_harness_mutation.erosion_llm import LLMEndpoint, LLMEndpointError
from evidence_harness_mutation.natural_campaign import AUDITOR_PROMPT, parse_auditor

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CORPUS = PROJECT_ROOT / "experiments/erosion-v1/seeded-corpus-v0.json"
SEEDED_RESULTS = PROJECT_ROOT / "evaluation/erosion-v1-seeded-gate-results.json"
REGRESSION_SUITE = PROJECT_ROOT / "experiments/erosion-v1/regression-suite-v1.json"
CACHE = PROJECT_ROOT / "experiments/erosion-v1/seeded/auditor"
OUTPUT = PROJECT_ROOT / "evaluation/erosion-v1-seeded-baselines.json"
AUDITOR = "kimi-k3"
GATES = ("g2_trajectory", "g1_g2_g3", "baseline_regression_suite", "baseline_llm_auditor")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the regression-suite and LLM-auditor baselines on the E1 seed corpus."
    )
    parser.add_argument("--jobs", type=int, default=4)
    return parser.parse_args()


def touched_paths(patch: str) -> list[str]:
    return sorted({line[6:].strip() for line in patch.splitlines() if line.startswith("+++ b/")})


def audit(endpoint: LLMEndpoint, seed_id: str, patch: str, base_commit: str) -> str:
    cache = CACHE / f"{seed_id}.txt"
    if cache.exists() and (decision := parse_auditor(cache.read_text())) is not None:
        return decision.value
    sources = "\n\n".join(
        f"=== {path} ===\n"
        + subprocess.run(
            ("git", "show", f"{base_commit}:{path}"),
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        for path in touched_paths(patch)
    )
    prompt = AUDITOR_PROMPT.format(sources=sources, patch=patch)
    for _ in range(3):
        reply = endpoint.complete(AUDITOR, prompt, max_output_tokens=16000, temperature=None)
        if (decision := parse_auditor(reply.text)) is not None:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(reply.text)
            return decision.value
    raise LLMEndpointError(f"auditor gave no verdict for {seed_id}")


def main() -> int:
    args = parse_args()
    corpus, corpus_sha256 = load_seed_corpus(CORPUS)
    guard = {item["seed_id"]: item for item in json.loads(SEEDED_RESULTS.read_text())["outcomes"]}
    patches = {seed.seed_id: (CORPUS.parent / seed.patch).read_bytes() for seed in corpus.seeds}
    try:
        endpoint = LLMEndpoint.from_environment()
        with ThreadPoolExecutor(max_workers=8) as pool:
            auditor = dict(
                zip(
                    patches,
                    pool.map(
                        lambda item: audit(endpoint, item[0], item[1].decode(), corpus.base_commit),
                        patches.items(),
                    ),
                    strict=True,
                )
            )
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            verdicts = dict(
                zip(
                    patches,
                    pool.map(
                        lambda patch: run_candidate_gate(
                            GateRequest(
                                repo_root=PROJECT_ROOT,
                                patch=patch,
                                base=corpus.base_commit,
                                suite_path=REGRESSION_SUITE,
                            )
                        ),
                        patches.values(),
                    ),
                    strict=True,
                )
            )
    except (OSError, ValueError, CandidateGateError, LLMEndpointError) as exc:
        print(f"seeded baselines failed: {exc}", file=sys.stderr)
        return 1
    rows = []
    for seed in corpus.seeds:
        verdict = verdicts[seed.seed_id]
        if verdict.decision not in (GateDecision.ACCEPT, GateDecision.REJECT):
            print(f"{seed.seed_id}: regression gate {verdict.decision}", file=sys.stderr)
            return 1
        components = set(guard[seed.seed_id]["violated_components"])
        rejected = {
            "g2_trajectory": GuardComponent.TRAJECTORY.value in components,
            "g1_g2_g3": bool(components),
            "baseline_regression_suite": verdict.decision is GateDecision.REJECT,
            "baseline_llm_auditor": auditor[seed.seed_id] == "reject",
        }
        rows.append(
            {
                "seed_id": seed.seed_id,
                "category": seed.category.value,
                "label": guard[seed.seed_id]["label"],
                "provenance": seed.provenance.value,
                "regression_violated": sum(
                    probe.status.value == "violated" for probe in verdict.probes
                ),
                "rejected_by": [gate for gate in GATES if rejected[gate]],
            }
        )
    erosion = [row for row in rows if row["label"] == "erosion"]
    benign = [row for row in rows if row["label"] != "erosion"]
    summary = {
        gate: {
            "erosion_rejected": f"{sum(gate in r['rejected_by'] for r in erosion)}/{len(erosion)}",
            "benign_rejected": f"{sum(gate in r['rejected_by'] for r in benign)}/{len(benign)}",
        }
        for gate in GATES
    }
    contrasts = {
        f"{caught} but not {missed}": [
            r["seed_id"]
            for r in erosion
            if caught in r["rejected_by"] and missed not in r["rejected_by"]
        ]
        for caught, missed in (
            ("g2_trajectory", "baseline_regression_suite"),
            ("baseline_regression_suite", "g2_trajectory"),
            ("g1_g2_g3", "baseline_regression_suite"),
            ("baseline_regression_suite", "g1_g2_g3"),
            ("g1_g2_g3", "baseline_llm_auditor"),
            ("baseline_llm_auditor", "g1_g2_g3"),
        )
    }
    report = {
        "schema_version": 1,
        "corpus_sha256": corpus_sha256,
        "seeded_results_sha256": hashlib.sha256(SEEDED_RESULTS.read_bytes()).hexdigest(),
        "regression_suite_sha256": hashlib.sha256(REGRESSION_SUITE.read_bytes()).hexdigest(),
        "auditor": AUDITOR,
        "summary": summary,
        "contrasts": contrasts,
        "outcomes": rows,
    }
    OUTPUT.write_bytes(_canonical_json(report))
    for gate, item in summary.items():
        print(f"{gate:28} erosion {item['erosion_rejected']} benign {item['benign_rejected']}")
    for name, seeds in contrasts.items():
        print(f"{name}: {seeds}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
