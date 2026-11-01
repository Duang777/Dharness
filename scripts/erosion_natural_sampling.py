from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from evidence_harness_mutation.erosion_llm import LLMEndpoint, LLMEndpointError
from evidence_harness_mutation.natural_erosion import (
    Condition,
    condition_paths,
    NaturalSample,
    NaturalSamplingError,
    SampleSlot,
    SamplingManifest,
    build_manifest,
    load_trials,
    manifest_bytes,
    render_prompt,
    sample_from_response,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TRIALS_DIR = PROJECT_ROOT / "evaluation/trials-89"
BASE_COMMIT = "c2f95328b284b9a9addbff8cc6af7e12cbb1c34f"
PROPOSERS = ("claude-sonnet-5-5", "gpt-6-astra", "deepseek-v4-pro")
SELECTION_SEED = 20261101
MAX_OUTPUT_TOKENS = 32000


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sample natural harness edits from LLM proposers for E2 (idempotent)."
    )
    parser.add_argument("--limit", type=int, default=None, help="Run at most this many new slots.")
    parser.add_argument("--jobs", type=int, default=6)
    parser.add_argument("--condition", type=Condition, default=Condition.NEUTRAL)
    parser.add_argument(
        "--reparse",
        action="store_true",
        help="Rebuild every sample record from its saved raw response without calling models.",
    )
    return parser.parse_args()


def load_or_create_manifest(condition: Condition) -> SamplingManifest:
    path = condition_paths(PROJECT_ROOT, condition).samples_dir / "manifest.json"
    if path.exists():
        return SamplingManifest.model_validate_json(path.read_bytes())
    trials = load_trials(TRIALS_DIR)
    first = json.loads(next(TRIALS_DIR.glob("*/result.json")).read_text())
    manifest = build_manifest(
        trials=trials,
        budget=first["config"]["agent"]["options"],
        base_commit=BASE_COMMIT,
        source_run="evaluation/trials-89",
        seed=SELECTION_SEED,
        proposers=PROPOSERS,
        samples_per_trial=2,
        temperature=1.0,
        condition=condition,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(manifest_bytes(manifest))
    return manifest


def samples_dir(manifest: SamplingManifest) -> Path:
    return condition_paths(PROJECT_ROOT, manifest.condition).samples_dir


def base_sources(manifest: SamplingManifest) -> dict[str, str]:
    import subprocess

    listing = subprocess.run(
        ("git", "ls-tree", "--name-only", manifest.base_commit, "src/evidence_harness/"),
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    sources = {}
    for path in listing:
        if path.endswith(".py"):
            sources[Path(path).name] = subprocess.run(
                ("git", "show", f"{manifest.base_commit}:{path}"),
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                check=True,
            ).stdout
    return sources


def run_slot(
    slot: SampleSlot,
    manifest: SamplingManifest,
    sources: dict[str, str],
    endpoint: LLMEndpoint,
) -> NaturalSample:
    trial = next(item for item in manifest.trials if item.task == slot.task)
    prompt = render_prompt(manifest, trial, sources)
    reply = endpoint.complete(
        slot.proposer,
        prompt,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        temperature=manifest.temperature,
    )
    raw_path = samples_dir(manifest) / "raw" / f"{slot.sample_id}.txt"
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(reply.text)
    sample = sample_from_response(
        slot,
        prompt=prompt,
        response_text=reply.text,
        model_reported=reply.model,
        temperature=reply.temperature,
        input_tokens=reply.usage.input_tokens,
        output_tokens=reply.usage.output_tokens,
        repo_root=PROJECT_ROOT,
        base_commit=manifest.base_commit,
    )
    write_sample(manifest, sample)
    return sample


def reparse(manifest: SamplingManifest, sources: dict[str, str]) -> None:
    for slot in manifest.slots:
        record = samples_dir(manifest) / "samples" / f"{slot.sample_id}.json"
        if not record.exists():
            continue
        old = NaturalSample.model_validate_json(record.read_bytes())
        trial = next(item for item in manifest.trials if item.task == slot.task)
        sample = sample_from_response(
            slot,
            prompt=render_prompt(manifest, trial, sources),
            response_text=(samples_dir(manifest) / "raw" / f"{slot.sample_id}.txt").read_text(),
            model_reported=old.model_reported,
            temperature=old.temperature,
            input_tokens=old.input_tokens,
            output_tokens=old.output_tokens,
            repo_root=PROJECT_ROOT,
            base_commit=manifest.base_commit,
        )
        write_sample(manifest, sample)
        print(f"{sample.sample_id}: {old.status} -> {sample.status}")


def write_sample(manifest: SamplingManifest, sample: NaturalSample) -> None:
    record = samples_dir(manifest) / "samples" / f"{sample.sample_id}.json"
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(json.dumps(sample.model_dump(mode="json"), indent=2, sort_keys=True) + "\n")


def main() -> int:
    args = parse_args()
    try:
        manifest = load_or_create_manifest(args.condition)
        endpoint = LLMEndpoint.from_environment()
    except (OSError, NaturalSamplingError, LLMEndpointError) as exc:
        print(f"sampling setup failed: {exc}", file=sys.stderr)
        return 1
    sources = base_sources(manifest)
    if args.reparse:
        reparse(manifest, sources)
        return 0
    pending = [
        slot
        for slot in manifest.slots
        if not (samples_dir(manifest) / "samples" / f"{slot.sample_id}.json").exists()
    ][: args.limit]
    print(f"{len(pending)} pending of {len(manifest.slots)} slots")
    failures = 0

    def guarded(slot: SampleSlot) -> None:
        nonlocal failures
        try:
            sample = run_slot(slot, manifest, sources, endpoint)
        except (LLMEndpointError, NaturalSamplingError) as exc:
            failures += 1
            print(f"FAILED {slot.sample_id}: {exc}", file=sys.stderr, flush=True)
            return
        print(
            f"{sample.sample_id}: {sample.status} in={sample.input_tokens}"
            f" out={sample.output_tokens} touched={list(sample.touched_paths)}",
            flush=True,
        )

    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        list(pool.map(guarded, pending))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
