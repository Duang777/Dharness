from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from evidence_harness_mutation import (
    MutationId,
    OfflineCampaignOutcome,
    PrefixBenchDevelopmentCampaignReport,
    PrefixBenchFileBinding,
    build_prefixbench_development_campaign,
    check_prefixbench_development_campaign,
)
from evidence_harness_mutation import prefixbench_campaign as campaign_module

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = PROJECT_ROOT / "evaluation" / "prefixbench-v1-development-offline-campaign.json"
READINESS_PATH = PROJECT_ROOT / "evaluation" / "prefixbench-v1-development-readiness.json"


def _bound_journals_exist() -> bool:
    readiness = json.loads(READINESS_PATH.read_bytes())
    return all(
        (PROJECT_ROOT / task["sources"]["journal"]["path"]).is_file() for task in readiness["tasks"]
    )


@pytest.mark.skipif(not _bound_journals_exist(), reason="raw development journals are unavailable")
def test_real_development_campaign_is_deterministic_and_complete() -> None:
    first = build_prefixbench_development_campaign(PROJECT_ROOT)
    second = build_prefixbench_development_campaign(PROJECT_ROOT)

    assert first == second
    assert first.canonical_bytes() == second.canonical_bytes()
    assert (
        PrefixBenchDevelopmentCampaignReport.model_validate_json(first.canonical_bytes()) == first
    )
    assert first.summary.model_dump(mode="json") == {
        "tasks": 28,
        "tasks_with_attempts": 25,
        "tasks_with_verified_attempts": 7,
        "projected_attempts": 39,
        "verified_attempts": 7,
        "outcomes": {
            "scheduled": 168,
            "mutation_not_applicable": 77,
            "offline_invalid": 0,
            "oracle_equivalent": 63,
            "offline_violation": 28,
            "other_oracle_change": 0,
        },
        "operators": [
            {
                "operator": "stale_evidence_epoch",
                "expected_invariant": "I1",
                "outcomes": {
                    "scheduled": 42,
                    "mutation_not_applicable": 16,
                    "offline_invalid": 0,
                    "oracle_equivalent": 19,
                    "offline_violation": 7,
                    "other_oracle_change": 0,
                },
            },
            {
                "operator": "reorder_check_receipts",
                "expected_invariant": "I2",
                "outcomes": {
                    "scheduled": 42,
                    "mutation_not_applicable": 19,
                    "offline_invalid": 0,
                    "oracle_equivalent": 16,
                    "offline_violation": 7,
                    "other_oracle_change": 0,
                },
            },
            {
                "operator": "review_timeout_fallback",
                "expected_invariant": "I3",
                "outcomes": {
                    "scheduled": 42,
                    "mutation_not_applicable": 26,
                    "offline_invalid": 0,
                    "oracle_equivalent": 9,
                    "offline_violation": 7,
                    "other_oracle_change": 0,
                },
            },
            {
                "operator": "cross_candidate_evidence",
                "expected_invariant": "I4",
                "outcomes": {
                    "scheduled": 42,
                    "mutation_not_applicable": 16,
                    "offline_invalid": 0,
                    "oracle_equivalent": 19,
                    "offline_violation": 7,
                    "other_oracle_change": 0,
                },
            },
        ],
    }
    cases = tuple(case for task in first.tasks for case in task.campaign.cases)
    violations = tuple(
        case for case in cases if case.outcome is OfflineCampaignOutcome.OFFLINE_VIOLATION
    )
    assert len(cases) == 168
    assert len(violations) == 28
    assert all(case.counterexample.trace.events for case in violations)
    assert tuple(summary.operator for summary in first.summary.operators) == tuple(MutationId)


def test_committed_report_rejects_request_sequence_drift() -> None:
    payload = json.loads(REPORT_PATH.read_bytes())
    cases = payload["tasks"][0]["campaign"]["cases"]
    cases[0]["request"], cases[3]["request"] = cases[3]["request"], cases[0]["request"]

    with pytest.raises(ValidationError, match="exact default request schedule"):
        PrefixBenchDevelopmentCampaignReport.model_validate(payload)


def test_checker_accepts_artifact_when_all_journals_are_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        campaign_module,
        "_resolve_journal_path",
        lambda binding, _root: tmp_path / binding.sha256,
    )

    assert (
        check_prefixbench_development_campaign(
            PROJECT_ROOT,
            report_path=REPORT_PATH,
        )
        == ()
    )


def test_checker_rejects_partial_journal_availability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = PrefixBenchDevelopmentCampaignReport.model_validate_json(REPORT_PATH.read_bytes())
    present = tmp_path / "present.jsonl"
    present.write_text("{}\n", encoding="utf-8")

    def resolve(binding: PrefixBenchFileBinding, _root: Path) -> Path:
        return present if binding == report.tasks[0].journal else tmp_path / binding.sha256

    monkeypatch.setattr(campaign_module, "_resolve_journal_path", resolve)

    assert check_prefixbench_development_campaign(
        PROJECT_ROOT,
        report_path=REPORT_PATH,
    ) == ("PrefixBench campaign journals are partially available: 1/28 bound files exist",)


def test_protocol_loader_rejects_implementation_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = campaign_module._current_protocol_manifest(PROJECT_ROOT)
    changed = expected.model_copy(
        update={"source_set": expected.source_set.model_copy(update={"sha256": "0" * 64})}
    )
    monkeypatch.setattr(
        campaign_module,
        "_current_protocol_manifest",
        lambda _root: changed,
    )

    with pytest.raises(ValueError, match="source binding is stale"):
        campaign_module._load_bound_protocol(PROJECT_ROOT)


def test_development_inputs_must_match_git_head() -> None:
    with pytest.raises(ValueError, match="differs from Git HEAD"):
        campaign_module._validate_committed_input(
            PROJECT_ROOT,
            campaign_module.DEVELOPMENT_MATRIX,
            b"{}\n",
        )
