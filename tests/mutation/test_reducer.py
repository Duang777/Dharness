from __future__ import annotations

import pytest

from evidence_harness_mutation import (
    InvariantStatus,
    MutationId,
    MutationRequest,
    OfflineTrace,
    ReductionError,
    apply_mutation,
    reduce_counterexample,
)
from mutation.support import load_valid_prefix


@pytest.mark.parametrize("operator", list(MutationId))
def test_reducer_preserves_source_anchored_violation_and_strictly_shrinks(
    operator: MutationId,
) -> None:
    prefix = load_valid_prefix()
    applied = apply_mutation(prefix, MutationRequest(operator=operator))

    first = reduce_counterexample(applied)
    second = reduce_counterexample(applied)

    assert first == second
    assert type(first.trace) is OfflineTrace
    assert first.trace.source == prefix.source
    assert first.trace.through_line == prefix.through_line
    assert first.provenance.request == applied.request
    assert first.provenance.witness.attempt.proposal == applied.attempt.proposal
    assert first.provenance.witness.attempt.terminal == applied.attempt.terminal
    assert first.provenance.witness.target == applied.target
    assert first.provenance.witness.details == first.violation.details
    assert first.violation.invariant is applied.expected_invariant
    assert first.audit.result(applied.expected_invariant).status is InvariantStatus.FAIL
    assert first.metrics.after.event_count < first.metrics.before.event_count
    assert first.metrics.after.payload_member_count < first.metrics.before.payload_member_count
    assert first.metrics.after.canonical_json_bytes < first.metrics.before.canonical_json_bytes
    assert first.metrics.audits_executed >= 6
    assert first.provenance.removed_event_lines == tuple(
        event.line
        for event in applied.trace.events
        if event.line not in {retained.line for retained in first.trace.events}
    )


def test_reducer_rejects_an_attempt_without_a_verified_terminal() -> None:
    complete = load_valid_prefix()
    prefix = load_valid_prefix(through_line=complete.through_line - 1)
    applied = apply_mutation(
        prefix,
        MutationRequest(operator=MutationId.REVIEW_TIMEOUT_FALLBACK),
    )

    with pytest.raises(ReductionError, match="verified completion attempt"):
        reduce_counterexample(applied)
