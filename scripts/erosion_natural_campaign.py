from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from evidence_harness_mutation.candidate_gate import CandidateGateError
from evidence_harness_mutation.erosion_llm import LLMEndpoint, LLMEndpointError
from evidence_harness_mutation.natural_annotation import NaturalLabels
from evidence_harness_mutation.natural_campaign import (
    AUDITOR_PROMPT,
    AuditorDecision,
    build_report,
    parse_auditor,
    run_gates,
)
from evidence_harness_mutation.natural_erosion import (
    Condition,
    NaturalSample,
    SampleStatus,
    SamplingManifest,
    condition_paths,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
GUARD_SUITE = PROJECT_ROOT / "experiments/erosion-v1/guard-suite-v1.json"
REGRESSION_SUITE = PROJECT_ROOT / "experiments/erosion-v1/regression-suite-v1.json"
AUDITOR = "kimi-k3"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the gate, the regression-suite baseline and the LLM auditor on E2 patches."
    )
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--condition", type=Condition, default=Condition.NEUTRAL)
    return parser.parse_args()


def labels_are_committed(labels: Path) -> bool:
    status = subprocess.run(
        ("git", "status", "--porcelain", "--", str(labels)),
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    tracked = subprocess.run(
        ("git", "ls-files", "--error-unmatch", str(labels)),
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=False,
    )
    return not status.strip() and tracked.returncode == 0


def audit(
    endpoint: LLMEndpoint, sample: NaturalSample, base_commit: str, cache_dir: Path
) -> AuditorDecision:
    cache = cache_dir / f"{sample.sample_id}.txt"
    if cache.exists():
        decision = parse_auditor(cache.read_text())
        if decision is not None:
            return decision
    sources = "\n\n".join(
        f"=== {path} ===\n"
        + subprocess.run(
            ("git", "show", f"{base_commit}:{path}"),
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            check=False,
        ).stdout
        for path in sample.touched_paths
    )
    prompt = AUDITOR_PROMPT.format(sources=sources, patch=sample.patch)
    for _ in range(3):
        reply = endpoint.complete(AUDITOR, prompt, max_output_tokens=16000, temperature=None)
        decision = parse_auditor(reply.text)
        if decision is not None:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(reply.text)
            return decision
    raise LLMEndpointError(f"auditor gave no verdict for {sample.sample_id}")


def main() -> int:
    args = parse_args()
    paths = condition_paths(PROJECT_ROOT, args.condition)
    natural_dir = paths.samples_dir
    if not labels_are_committed(paths.labels):
        print(f"freeze and commit {paths.labels.name} before running the gate", file=sys.stderr)
        return 1
    manifest = SamplingManifest.model_validate_json((natural_dir / "manifest.json").read_bytes())
    labels_data = paths.labels.read_bytes()
    labels = NaturalLabels.model_validate_json(labels_data)
    labelled = {item.sample_id for item in labels.labels}
    samples = tuple(
        sample
        for slot in manifest.slots
        if (record := natural_dir / "samples" / f"{slot.sample_id}.json").exists()
        and (sample := NaturalSample.model_validate_json(record.read_bytes())).status
        is SampleStatus.PATCH
        and sample.sample_id in labelled
    )
    try:
        endpoint = LLMEndpoint.from_environment()
        with ThreadPoolExecutor(max_workers=8) as pool:
            decisions = list(
                pool.map(
                    lambda s: audit(endpoint, s, manifest.base_commit, natural_dir / "auditor"),
                    samples,
                )
            )
        guard, regression = run_gates(
            samples,
            repo_root=PROJECT_ROOT,
            base_commit=manifest.base_commit,
            guard_suite=GUARD_SUITE,
            regression_suite=REGRESSION_SUITE,
            jobs=args.jobs,
        )
        report = build_report(
            samples=samples,
            labels=labels,
            labels_sha256=hashlib.sha256(labels_data).hexdigest(),
            guard=guard,
            regression=regression,
            auditor={s.sample_id: d for s, d in zip(samples, decisions, strict=True)},
            auditor_model=AUDITOR,
            guard_suite=GUARD_SUITE,
            regression_suite=REGRESSION_SUITE,
            base_commit=manifest.base_commit,
        )
    except (OSError, ValueError, CandidateGateError, LLMEndpointError) as exc:
        print(f"natural campaign failed: {exc}", file=sys.stderr)
        return 1
    paths.results.write_bytes(report.canonical_bytes())
    paths.timing.write_text(
        json.dumps(
            {
                sample_id: {
                    "guard_sec": round(guard[sample_id].elapsed_sec, 3),
                    "regression_sec": round(regression[sample_id].elapsed_sec, 3),
                }
                for sample_id in sorted(guard)
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    print(
        f"erodes {report.erodes_overall.hits}/{report.erodes_overall.total}"
        f" (clear {report.erodes_clear_overall.hits})"
    )
    for item in report.gates:
        print(
            f"{item.gate:28} erodes {item.erodes_rejected.hits}/{item.erodes_rejected.total}"
            f" clear {item.erodes_clear_rejected.hits}/{item.erodes_clear_rejected.total}"
            f" benign {item.benign_rejected.hits}/{item.benign_rejected.total}"
        )
    for contrast in report.contrasts:
        print(f"{contrast.caught} but not {contrast.missed_by}: {list(contrast.erodes_samples)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
