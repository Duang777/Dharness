from __future__ import annotations

import subprocess
import sys

import pytest
from pydantic import JsonValue

from evidence_harness_mutation import (
    InvariantId,
    InvariantStatus,
    MutationId,
    MutationNotApplicable,
    MutationRequest,
    apply_mutation,
    audit_completion_trace,
)
from mutation.support import load_valid_prefix, mutate_event


def test_valid_schema2_trace_satisfies_i1_through_i4() -> None:
    report = audit_completion_trace(load_valid_prefix())

    assert report.passed is True
    assert report.verified_attempts == 1
    assert [result.invariant for result in report.results] == list(InvariantId)
    assert all(result.status is InvariantStatus.PASS for result in report.results)
    assert report.violations == ()


def test_incomplete_prefix_has_no_applicable_completion_invariants() -> None:
    complete = load_valid_prefix()
    report = audit_completion_trace(load_valid_prefix(through_line=complete.through_line - 1))

    assert report.passed is False
    assert report.verified_attempts == 0
    assert all(result.status is InvariantStatus.NOT_APPLICABLE for result in report.results)


def test_i1_detects_stale_evidence_after_the_current_epoch_changes() -> None:
    mutated = mutate_event(
        load_valid_prefix(),
        "completion_isolation_started",
        lambda payload: {**payload, "work_epoch": 2},
    )

    report = audit_completion_trace(mutated)

    assert report.result(InvariantId.I1).status is InvariantStatus.FAIL
    assert "current work epoch 2" in report.result(InvariantId.I1).violations[0].details[0]


def test_i2_detects_reordered_embedded_receipts() -> None:
    def reverse_checks(payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
        checks = payload["checks"]
        assert isinstance(checks, list)
        return {**payload, "checks": list(reversed(checks))}

    report = audit_completion_trace(
        mutate_event(load_valid_prefix(), "verification_receipt", reverse_checks)
    )

    assert report.result(InvariantId.I2).status is InvariantStatus.FAIL
    assert any(
        "do not match proposed order" in detail
        for detail in report.result(InvariantId.I2).violations[0].details
    )


def test_i3_detects_5f64d66_review_error_fallback_to_verified() -> None:
    mutated = mutate_event(
        load_valid_prefix(),
        "completion_review",
        lambda payload: {"error_type": "TimeoutError", "error": "review timed out"},
        replacement_type="completion_review_error",
    )

    report = audit_completion_trace(mutated)

    assert report.result(InvariantId.I3).status is InvariantStatus.FAIL
    assert any(
        "review error" in detail for detail in report.result(InvariantId.I3).violations[0].details
    )


def test_i4_detects_289bea4_cross_candidate_evidence() -> None:
    def replace_candidate(payload: dict[str, JsonValue]) -> dict[str, JsonValue]:
        isolation = payload["isolation"]
        assert isinstance(isolation, dict)
        isolation["candidate_image_id"] = "sha256:" + "9" * 64
        return payload

    report = audit_completion_trace(
        mutate_event(load_valid_prefix(), "verification_receipt", replace_candidate)
    )

    assert report.result(InvariantId.I4).status is InvariantStatus.FAIL
    assert any(
        "crosses candidate images" in detail
        for detail in report.result(InvariantId.I4).violations[0].details
    )


@pytest.mark.parametrize(
    ("operator", "expected_invariant"),
    [
        (MutationId.STALE_EVIDENCE_EPOCH, InvariantId.I1),
        (MutationId.REORDER_CHECK_RECEIPTS, InvariantId.I2),
        (MutationId.REVIEW_TIMEOUT_FALLBACK, InvariantId.I3),
        (MutationId.CROSS_CANDIDATE_EVIDENCE, InvariantId.I4),
    ],
)
def test_stateful_operators_produce_deterministic_detectable_mutations(
    operator: MutationId,
    expected_invariant: InvariantId,
) -> None:
    prefix = load_valid_prefix()
    request = MutationRequest(operator=operator)

    first = apply_mutation(prefix, request)
    second = apply_mutation(prefix, request)
    report = audit_completion_trace(first.trace)

    assert first == second
    assert first.trace.source == prefix.source
    assert first.expected_invariant is expected_invariant
    assert first.source_line_sha256 == prefix.events[first.target_line - 1].line_sha256
    assert first.attempt.proposal.matches(prefix.events[1])
    assert first.attempt.terminal is not None
    assert first.attempt.terminal.matches(prefix.events[-1])
    assert first.target.original_event_type == prefix.events[first.target_line - 1].event_type
    assert report.result(expected_invariant).status is InvariantStatus.FAIL


def test_operator_rejects_a_missing_attempt() -> None:
    with pytest.raises(MutationNotApplicable, match="attempt 2 is not present"):
        apply_mutation(
            load_valid_prefix(),
            MutationRequest(
                operator=MutationId.STALE_EVIDENCE_EPOCH,
                attempt_ordinal=2,
            ),
        )


def test_oracle_import_does_not_load_the_production_evidence_gate() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import evidence_harness_mutation.invariants; "
                "assert 'evidence_harness.evidence' not in sys.modules"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
