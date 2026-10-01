from __future__ import annotations

import json

from pydantic import JsonValue

from evidence_harness_mutation import (
    InvariantId,
    MutationId,
    MutationRequest,
    OfflineCampaignOutcome,
    OfflineCampaignReport,
    OfflineInvalidCase,
    run_offline_campaign,
)
from evidence_harness_mutation import campaign as campaign_module
from mutation.support import load_valid_prefix, mutate_event


def test_default_campaign_runs_every_operator_in_declaration_order() -> None:
    report = run_offline_campaign(load_valid_prefix())

    assert report.baseline is not None
    assert report.baseline_error is None
    assert report.summary.scheduled == len(MutationId)
    assert report.summary.offline_violation == len(MutationId)
    assert report.summary.mutation_not_applicable == 0
    assert [case.request.operator for case in report.cases] == list(MutationId)
    assert all(case.outcome is OfflineCampaignOutcome.OFFLINE_VIOLATION for case in report.cases)
    for case in report.cases:
        assert case.outcome is OfflineCampaignOutcome.OFFLINE_VIOLATION
        assert case.counterexample.violation.invariant is case.violation.invariant
        assert case.counterexample.violation.terminal_line == case.violation.terminal_line
        assert case.counterexample.violation.details == case.violation.details
        assert case.violation.invariant is case.expected_invariant


def test_explicit_schedule_sorts_requests_and_retains_duplicates() -> None:
    requests = (
        MutationRequest(
            operator=MutationId.CROSS_CANDIDATE_EVIDENCE,
            attempt_ordinal=99,
        ),
        MutationRequest(operator=MutationId.REVIEW_TIMEOUT_FALLBACK),
        MutationRequest(
            operator=MutationId.STALE_EVIDENCE_EPOCH,
            attempt_ordinal=99,
        ),
        MutationRequest(
            operator=MutationId.STALE_EVIDENCE_EPOCH,
            attempt_ordinal=99,
        ),
    )

    report = run_offline_campaign(load_valid_prefix(), requests=requests)

    assert [case.request for case in report.cases] == [
        requests[1],
        requests[2],
        requests[3],
        requests[0],
    ]
    assert report.summary.scheduled == 4
    assert report.summary.offline_violation == 1
    assert report.summary.mutation_not_applicable == 3


def test_default_schedule_keeps_attempt_one_visible_when_no_attempt_exists() -> None:
    report = run_offline_campaign(load_valid_prefix(through_line=1))

    assert [case.request.attempt_ordinal for case in report.cases] == [1, 1, 1, 1]
    assert report.summary.scheduled == len(MutationId)
    assert report.summary.mutation_not_applicable == len(MutationId)


def test_preexisting_failure_is_not_credited_to_the_mutation() -> None:
    prefix = mutate_event(
        load_valid_prefix(),
        "completion_isolation_started",
        lambda payload: {**payload, "work_epoch": 3},
    )

    report = run_offline_campaign(
        prefix,
        requests=(MutationRequest(operator=MutationId.STALE_EVIDENCE_EPOCH),),
    )

    assert report.baseline is not None
    assert report.baseline.result(InvariantId.I1).status == "fail"
    assert report.cases[0].outcome is OfflineCampaignOutcome.OTHER_ORACLE_CHANGE
    assert report.summary.other_oracle_change == 1


def test_unchanged_audit_is_oracle_equivalent() -> None:
    def cross_candidate(
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        isolation = payload["isolation"]
        assert isinstance(isolation, dict)
        candidate = "sha256:" + "9" * 64
        isolation["candidate_image_id"] = candidate
        checks = isolation["checks"]
        assert isinstance(checks, list)
        for check in checks:
            assert isinstance(check, dict)
            check["started_from_image_id"] = candidate
        return payload

    prefix = mutate_event(
        load_valid_prefix(),
        "verification_receipt",
        cross_candidate,
    )

    report = run_offline_campaign(
        prefix,
        requests=(MutationRequest(operator=MutationId.CROSS_CANDIDATE_EVIDENCE),),
    )

    assert report.cases[0].outcome is OfflineCampaignOutcome.ORACLE_EQUIVALENT
    assert report.summary.oracle_equivalent == 1


def test_nondeterministic_baseline_preserves_schedule_as_invalid(
    monkeypatch,
) -> None:
    original = campaign_module.audit_completion_trace
    calls = 0

    def alternating_audit(trace):
        nonlocal calls
        calls += 1
        report = original(trace)
        return report.model_copy(update={"verified_attempts": report.verified_attempts + calls % 2})

    monkeypatch.setattr(campaign_module, "audit_completion_trace", alternating_audit)

    report = run_offline_campaign(load_valid_prefix())

    assert report.baseline is None
    assert report.baseline_error is not None
    assert report.summary.offline_invalid == len(MutationId)
    assert all(case.outcome is OfflineCampaignOutcome.OFFLINE_INVALID for case in report.cases)


def test_malformed_attempt_payload_preserves_default_schedule_as_invalid() -> None:
    prefix = mutate_event(
        load_valid_prefix(),
        "agent_decision",
        lambda payload: {},
    )

    report = run_offline_campaign(prefix)

    assert report.baseline is None
    assert report.summary.scheduled == len(MutationId)
    assert report.summary.offline_invalid == len(MutationId)
    assert all(
        isinstance(case, OfflineInvalidCase) and case.stage == "baseline_audit"
        for case in report.cases
    )


def test_canonical_campaign_bytes_are_repeatable_and_self_describing() -> None:
    requests = (MutationRequest(operator=MutationId.REVIEW_TIMEOUT_FALLBACK),)

    first = run_offline_campaign(load_valid_prefix(), requests=requests)
    second = run_offline_campaign(load_valid_prefix(), requests=requests)
    payload = json.loads(first.canonical_bytes())

    assert first == second
    assert first.canonical_bytes() == second.canonical_bytes()
    assert first.canonical_bytes().endswith(b"\n")
    assert OfflineCampaignReport.model_validate_json(first.canonical_bytes()) == first
    assert payload["schema_version"] == 1
    assert payload["summary"]["offline_violation"] == 1
