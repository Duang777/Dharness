from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from evidence_harness_mutation.candidate_gate import (
    GateDecision,
    GateRequest,
    GateVerdict,
    GuardComponent,
    GuardProperty,
    ProbeResult,
    ProbeStatus,
)
from evidence_harness_mutation.erosion_campaign import (
    ErosionCampaignError,
    SeedCategory,
    load_seed_corpus,
    run_seeded_campaign,
    wilson_rate,
)

_PROBES = [
    {
        "probe_id": "freshness.unit",
        "component": "protocol",
        "property": "freshness",
        "pytest_node": "tests/test_a.py::test_unit",
    },
    {
        "probe_id": "review.episode",
        "component": "trajectory",
        "property": "review",
        "pytest_node": "tests/test_b.py::test_episode",
    },
]
_SUITE = {"schema_version": 1, "suite_id": "suite-under-test", "probes": _PROBES}
_VIOLATIONS = {
    "caught_by_unit": ("freshness.unit",),
    "caught_by_episode": ("review.episode",),
    "missed": (),
    "benign_ok": (),
    "benign_flagged": ("review.episode",),
}
_CATEGORIES = {
    "caught_by_unit": SeedCategory.STALE_RECEIPT_REUSE,
    "caught_by_episode": SeedCategory.REVIEW_FAILURE_PASS,
    "missed": SeedCategory.REVIEW_FAILURE_PASS,
    "benign_ok": SeedCategory.REFACTOR,
    "benign_flagged": SeedCategory.REFACTOR,
}


def _write_corpus(root: Path, *, tamper: str | None = None) -> Path:
    (root / "seeded/author").mkdir(parents=True)
    (root / "suite.json").write_text(json.dumps(_SUITE))
    seeds = []
    for seed_id, category in _CATEGORIES.items():
        patch = root / f"seeded/author/{seed_id}.patch"
        patch.write_bytes(seed_id.encode())
        seeds.append(
            {
                "seed_id": seed_id,
                "category": category,
                "provenance": "author",
                "description": seed_id,
                "patch": f"seeded/author/{seed_id}.patch",
                "patch_sha256": hashlib.sha256(seed_id.encode()).hexdigest(),
            }
        )
    if tamper is not None:
        (root / f"seeded/author/{tamper}.patch").write_bytes(b"changed after freezing")
    corpus = root / "corpus.json"
    corpus.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "corpus_id": "corpus-under-test",
                "base_commit": "a" * 40,
                "suite": "suite.json",
                "seeds": seeds,
            }
        )
    )
    return corpus


def _fake_gate(request: GateRequest) -> GateVerdict:
    violated = set(_VIOLATIONS[request.patch.decode()])
    probes = tuple(
        ProbeResult(
            probe_id=probe["probe_id"],
            component=GuardComponent(probe["component"]),
            property=GuardProperty(probe["property"]),
            status=ProbeStatus.VIOLATED if probe["probe_id"] in violated else ProbeStatus.HELD,
        )
        for probe in _PROBES
    )
    return GateVerdict(
        suite_id="suite-under-test",
        suite_sha256="0" * 64,
        base_commit=request.base,
        patch_sha256=hashlib.sha256(request.patch).hexdigest(),
        touched_paths=("src/evidence_harness/run_loop.py",),
        out_of_scope_paths=(),
        candidate_source_sha256="1" * 64,
        decision=GateDecision.REJECT if violated else GateDecision.ACCEPT,
        detail=None,
        violated_properties=tuple(
            sorted({probe.property for probe in probes if probe.status is ProbeStatus.VIOLATED})
        ),
        probes=probes,
        elapsed_sec=1.0,
    )


def test_campaign_reports_misclassified_seeds_and_component_ablation(tmp_path: Path) -> None:
    report, timing = run_seeded_campaign(
        _write_corpus(tmp_path), repo_root=tmp_path, jobs=2, gate=_fake_gate
    )

    by_category = {item.category: item for item in report.categories}
    assert by_category[SeedCategory.REVIEW_FAILURE_PASS].misclassified_seeds == ("missed",)
    assert by_category[SeedCategory.REFACTOR].misclassified_seeds == ("benign_flagged",)
    assert by_category[SeedCategory.STALE_RECEIPT_REUSE].misclassified_seeds == ()
    (author,) = report.provenances
    ablation = {item.components: item for item in author.ablations}
    unit_only = ablation[(GuardComponent.PROTOCOL,)]
    assert (unit_only.erosion_rejected.hits, unit_only.erosion_rejected.total) == (1, 3)
    assert unit_only.benign_rejected.hits == 0
    both = ablation[(GuardComponent.PROTOCOL, GuardComponent.TRAJECTORY)]
    assert (both.erosion_rejected.hits, both.benign_rejected.hits) == (2, 1)
    assert set(timing) == set(_CATEGORIES)


def test_campaign_report_is_byte_stable_across_job_counts(tmp_path: Path) -> None:
    corpus = _write_corpus(tmp_path)
    serial, _ = run_seeded_campaign(corpus, repo_root=tmp_path, jobs=1, gate=_fake_gate)
    parallel, _ = run_seeded_campaign(corpus, repo_root=tmp_path, jobs=4, gate=_fake_gate)

    assert serial.canonical_bytes() == parallel.canonical_bytes()


def test_corpus_rejects_a_patch_edited_after_freezing(tmp_path: Path) -> None:
    with pytest.raises(ErosionCampaignError, match="missed"):
        load_seed_corpus(_write_corpus(tmp_path, tamper="missed"))


def test_wilson_interval_matches_reference_values() -> None:
    assert wilson_rate(9, 14).model_dump() == {
        "hits": 9,
        "total": 14,
        "rate": "0.6429",
        "low": "0.3876",
        "high": "0.8366",
    }
    assert wilson_rate(0, 14).low == "0.0000"
    assert wilson_rate(0, 0).rate is None
