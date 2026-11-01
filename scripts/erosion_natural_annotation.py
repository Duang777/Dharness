from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from evidence_harness_mutation.erosion_llm import LLMEndpoint, LLMEndpointError
from evidence_harness_mutation.natural_annotation import (
    ADJUDICATOR_PROMPT,
    AnnotationError,
    NaturalLabel,
    NaturalLabels,
    cohen_kappa,
    labels_agree,
    labels_bytes,
    parse_label,
    prompt_sha256,
    render_annotation_prompt,
)
from evidence_harness_mutation.natural_erosion import NaturalSample, SampleStatus, SamplingManifest
from evidence_harness_mutation.public_audit import CandidateLabel

PROJECT_ROOT = Path(__file__).resolve().parents[1]
NATURAL_DIR = PROJECT_ROOT / "experiments/erosion-v1/natural"
CODEBOOK = PROJECT_ROOT / "experiments/erosion-v1/public-audit-labels-v1.json"
OUTPUT = PROJECT_ROOT / "experiments/erosion-v1/natural-labels-v1.json"
ANNOTATOR_A = "claude-opus-5-5"
ANNOTATOR_B = "gpt-5.5"
ADJUDICATOR = "gemini-4-argon"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Label every applicable E2 patch with two blind LLM annotators and adjudicate."
    )
    parser.add_argument("--jobs", type=int, default=8)
    return parser.parse_args()


def base_file(base_commit: str, path: str) -> str:
    completed = subprocess.run(
        ("git", "show", f"{base_commit}:{path}"),
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout if completed.returncode == 0 else "(new file)"


def ask(
    endpoint: LLMEndpoint, model: str, sample_id: str, prompt: str, role: str
) -> CandidateLabel:
    cache = NATURAL_DIR / "annotation" / f"{sample_id}.{role}.txt"
    if cache.exists():
        return parse_label(sample_id, cache.read_text())
    last: Exception | None = None
    for _ in range(3):
        reply = endpoint.complete(model, prompt, max_output_tokens=16000, temperature=None)
        try:
            label = parse_label(sample_id, reply.text)
        except AnnotationError as exc:
            last = exc
            continue
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(reply.text)
        return label
    raise AnnotationError(f"{sample_id} {role}: {last}")


def main() -> int:
    args = parse_args()
    manifest_data = (NATURAL_DIR / "manifest.json").read_bytes()
    manifest = SamplingManifest.model_validate_json(manifest_data)
    codebook = json.loads(CODEBOOK.read_text())["codebook"]
    samples = [
        sample
        for slot in manifest.slots
        if (record := NATURAL_DIR / "samples" / f"{slot.sample_id}.json").exists()
        and (sample := NaturalSample.model_validate_json(record.read_bytes())).status
        is SampleStatus.PATCH
    ]
    endpoint = LLMEndpoint.from_environment()

    def label(sample: NaturalSample) -> NaturalLabel:
        assert sample.patch is not None
        prompt = render_annotation_prompt(
            codebook,
            {path: base_file(manifest.base_commit, path) for path in sample.touched_paths},
            sample.patch,
        )
        first = ask(endpoint, ANNOTATOR_A, sample.sample_id, prompt, "a")
        second = ask(endpoint, ANNOTATOR_B, sample.sample_id, prompt, "b")
        adjudicated = None
        if not labels_agree(first, second):
            adjudicated = ask(
                endpoint,
                ADJUDICATOR,
                sample.sample_id,
                ADJUDICATOR_PROMPT.format(
                    annotation_prompt=prompt,
                    label_a=first.model_dump_json(exclude={"name"}),
                    label_b=second.model_dump_json(exclude={"name"}),
                ),
                "adjudicator",
            )
        return NaturalLabel(
            sample_id=sample.sample_id,
            annotator_a=first,
            annotator_b=second,
            adjudicated=adjudicated,
            final=adjudicated or first,
        )

    try:
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            labels = tuple(pool.map(label, samples))
    except (LLMEndpointError, AnnotationError) as exc:
        print(f"annotation failed: {exc}", file=sys.stderr)
        return 1
    pairs = [
        (item.annotator_a.direction.value, item.annotator_b.direction.value) for item in labels
    ]
    report = NaturalLabels(
        manifest_sha256=hashlib.sha256(manifest_data).hexdigest(),
        annotator_a=ANNOTATOR_A,
        annotator_b=ANNOTATOR_B,
        adjudicator=ADJUDICATOR,
        annotator_prompt_sha256=prompt_sha256(),
        direction_kappa=cohen_kappa(pairs),
        direction_agreement=f"{sum(a == b for a, b in pairs)}/{len(pairs)}",
        labels=labels,
    )
    OUTPUT.write_bytes(labels_bytes(report))
    counts: dict[str, int] = {}
    for item in labels:
        key = f"{item.final.direction.value}/{item.final.confidence.value}"
        counts[key] = counts.get(key, 0) + 1
    print(
        f"{len(labels)} labelled, kappa={report.direction_kappa},"
        f" agreement={report.direction_agreement}"
    )
    print(json.dumps(counts, sort_keys=True))
    print(f"sha256 {hashlib.sha256(OUTPUT.read_bytes()).hexdigest()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
