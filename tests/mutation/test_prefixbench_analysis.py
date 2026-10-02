from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from evidence_harness_mutation import prefixbench_analysis as analysis_module
from evidence_harness_mutation.prefixbench_analysis import (
    DEVELOPMENT_ANALYSIS,
    DEVELOPMENT_CAMPAIGN,
    ExactFraction,
    PrefixBenchDevelopmentAnalysisReport,
    build_prefixbench_development_analysis,
    check_prefixbench_development_analysis,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CAMPAIGN_PATH = PROJECT_ROOT / DEVELOPMENT_CAMPAIGN


def test_real_development_analysis_is_deterministic_and_exact() -> None:
    first = build_prefixbench_development_analysis(PROJECT_ROOT)
    second = build_prefixbench_development_analysis(PROJECT_ROOT)

    assert first == second
    assert first.canonical_bytes() == second.canonical_bytes()
    assert (
        PrefixBenchDevelopmentAnalysisReport.model_validate_json(first.canonical_bytes()) == first
    )
    assert first.source_campaign.model_dump() == {
        "path": "evaluation/prefixbench-v1-development-offline-campaign.json",
        "bytes": 1_510_691,
        "sha256": "659dba67f203183be64a200e85198596d7fbb929849a308df7b681cb7f42794d",
    }
    assert first.tasks.model_dump(mode="json") == {
        "tasks": 28,
        "projected_attempts": 39,
        "tasks_with_projected_attempts": {
            "count": 25,
            "population": 28,
            "fraction": {"numerator": 25, "denominator": 28},
        },
        "tasks_with_verified_attempts": {
            "count": 7,
            "population": 28,
            "fraction": {"numerator": 1, "denominator": 4},
        },
        "verified_attempts_of_projected": {
            "count": 7,
            "population": 39,
            "fraction": {"numerator": 7, "denominator": 39},
        },
        "tasks_with_applicable_case": {
            "count": 12,
            "population": 28,
            "fraction": {"numerator": 3, "denominator": 7},
        },
        "tasks_with_offline_violation": {
            "count": 7,
            "population": 28,
            "fraction": {"numerator": 1, "denominator": 4},
        },
    }
    assert first.cases.model_dump(mode="json") == {
        "scheduled_cases": 168,
        "applicable_cases": {
            "count": 91,
            "population": 168,
            "fraction": {"numerator": 13, "denominator": 24},
        },
        "mutation_not_applicable_cases": {
            "count": 77,
            "population": 168,
            "fraction": {"numerator": 11, "denominator": 24},
        },
        "offline_invalid_cases": {
            "count": 0,
            "population": 168,
            "fraction": {"numerator": 0, "denominator": 1},
        },
        "oracle_equivalent_cases": {
            "count": 63,
            "population": 91,
            "fraction": {"numerator": 9, "denominator": 13},
        },
        "offline_violations": {
            "count": 28,
            "population": 91,
            "fraction": {"numerator": 4, "denominator": 13},
        },
        "other_oracle_changes": {
            "count": 0,
            "population": 91,
            "fraction": {"numerator": 0, "denominator": 1},
        },
    }
    assert [
        (
            row.operator.value,
            row.expected_invariant.value,
            row.applicable_cases_of_scheduled.count,
            row.offline_violations_of_applicable.count,
        )
        for row in first.operators
    ] == [
        ("stale_evidence_epoch", "I1", 26, 7),
        ("reorder_check_receipts", "I2", 23, 7),
        ("review_timeout_fallback", "I3", 16, 7),
        ("cross_candidate_evidence", "I4", 26, 7),
    ]
    assert first.reduction.model_dump(mode="json") == {
        "counterexamples": 28,
        "events": {
            "before": 1_996,
            "after": 230,
            "retained_fraction": {"numerator": 115, "denominator": 998},
        },
        "recursive_payload_members": {
            "before": 35_582,
            "after": 6_465,
            "retained_fraction": {"numerator": 6_465, "denominator": 35_582},
        },
        "compact_canonical_json_bytes": {
            "before": 4_044_814,
            "after": 716_196,
            "retained_fraction": {"numerator": 358_098, "denominator": 2_022_407},
        },
        "candidates_evaluated": 15_587,
        "audits_executed": 7_938,
        "accepted_reductions": 568,
        "rejections": {
            "malformed": 12_997,
            "not_applicable": 0,
            "no_violation": 0,
            "cause_changed": 2_022,
            "nondeterministic": 0,
        },
    }


def test_analysis_build_needs_only_the_frozen_campaign(
    tmp_path: Path,
) -> None:
    campaign_path = tmp_path / DEVELOPMENT_CAMPAIGN
    campaign_path.parent.mkdir(parents=True)
    campaign_path.write_bytes(CAMPAIGN_PATH.read_bytes())

    report = build_prefixbench_development_analysis(tmp_path)
    analysis_path = tmp_path / DEVELOPMENT_ANALYSIS
    analysis_path.write_bytes(report.canonical_bytes())

    assert check_prefixbench_development_analysis(tmp_path) == ()
    assert tuple(tmp_path.rglob("*")) == (
        tmp_path / "evaluation",
        campaign_path,
        analysis_path,
    )


def test_analysis_rejects_changed_campaign_bytes(tmp_path: Path) -> None:
    campaign_path = tmp_path / DEVELOPMENT_CAMPAIGN
    campaign_path.parent.mkdir(parents=True)
    campaign_path.write_bytes(CAMPAIGN_PATH.read_bytes() + b"\n")

    with pytest.raises(ValueError, match="does not match the frozen analysis input"):
        build_prefixbench_development_analysis(tmp_path)


def test_analysis_rejects_campaign_symlink(tmp_path: Path) -> None:
    campaign_path = tmp_path / DEVELOPMENT_CAMPAIGN
    campaign_path.parent.mkdir(parents=True)
    campaign_path.symlink_to(CAMPAIGN_PATH)

    with pytest.raises(ValueError, match="must be a regular file"):
        build_prefixbench_development_analysis(tmp_path)


def test_analysis_rejects_unreduced_fraction() -> None:
    with pytest.raises(ValidationError, match="fraction must be reduced"):
        ExactFraction(numerator=2, denominator=4)


def test_checker_rejects_stale_but_valid_analysis(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = build_prefixbench_development_analysis(PROJECT_ROOT)
    payload = report.model_dump(mode="json")
    payload["reduction"]["audits_executed"] += 1
    stale = PrefixBenchDevelopmentAnalysisReport.model_validate(payload)
    path = tmp_path / "analysis.json"
    path.write_bytes(stale.canonical_bytes())
    monkeypatch.setattr(
        analysis_module,
        "_require_project_path",
        lambda *_args, **_kwargs: None,
    )

    assert check_prefixbench_development_analysis(
        PROJECT_ROOT,
        report_path=path,
    ) == ("PrefixBench analysis artifact is stale",)


def test_report_rejects_operator_totals_that_disagree_with_cases() -> None:
    payload = json.loads(build_prefixbench_development_analysis(PROJECT_ROOT).canonical_bytes())
    payload["operators"][0]["outcomes"]["oracle_equivalent"] += 1
    payload["operators"][0]["outcomes"]["offline_violation"] -= 1
    operator = payload["operators"][0]
    operator["offline_violations_of_applicable"] = {
        "count": 6,
        "population": 26,
        "fraction": {"numerator": 3, "denominator": 13},
    }

    with pytest.raises(ValidationError, match="operator oracle_equivalent total"):
        PrefixBenchDevelopmentAnalysisReport.model_validate(payload)
