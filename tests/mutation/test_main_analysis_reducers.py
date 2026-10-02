from __future__ import annotations

import pytest

from evidence_harness_mutation import (
    MutationId,
    MutationRequest,
    apply_mutation,
    reduce_counterexample,
)
from evidence_harness_mutation.main_analysis_protocol import MainAnalysisReducer
from evidence_harness_mutation.main_analysis_reducers import (
    MainAnalysisReductionComparison,
    MainAnalysisReductionStatus,
    ReductionInput,
    compare_reducers,
)
from mutation.support import load_valid_prefix

_TASK_IDENTITY = "b" * 64


@pytest.mark.parametrize("operator", list(MutationId))
def test_reducers_share_input_witness_and_size(operator: MutationId) -> None:
    prefix = load_valid_prefix()
    mutation = apply_mutation(prefix, MutationRequest(operator=operator))

    comparison = compare_reducers(
        ReductionInput(
            task_identity_sha256=_TASK_IDENTITY,
            case_ordinal=1,
            mutation=mutation,
        )
    )

    assert tuple(result.reducer for result in comparison.results) == tuple(MainAnalysisReducer)
    assert all(
        result.status is not MainAnalysisReductionStatus.FAILED_RETAINED
        for result in comparison.results
    )
    assert all(result.witness == comparison.results[0].witness for result in comparison.results)
    assert all(result.metrics.audits_executed >= 3 for result in comparison.results)

    no_reduction = comparison.results[0]
    assert no_reduction.reducer is MainAnalysisReducer.NO_REDUCTION
    assert no_reduction.status is MainAnalysisReductionStatus.UNCHANGED
    assert no_reduction.input_trace_sha256 == no_reduction.output_trace_sha256
    assert no_reduction.metrics.before == no_reduction.metrics.after
    assert no_reduction.metrics.audits_executed == 3


@pytest.mark.parametrize("operator", list(MutationId))
def test_source_anchored_matches_frozen_reducer_output(operator: MutationId) -> None:
    prefix = load_valid_prefix()
    mutation = apply_mutation(prefix, MutationRequest(operator=operator))

    expected = reduce_counterexample(mutation)
    comparison = compare_reducers(
        ReductionInput(
            task_identity_sha256=_TASK_IDENTITY,
            case_ordinal=1,
            mutation=mutation,
        )
    )
    actual = comparison.results[-1]

    assert actual.reducer is MainAnalysisReducer.SOURCE_ANCHORED
    assert actual.trace == expected.trace
    assert actual.audit == expected.audit
    assert actual.violation == expected.violation
    assert actual.metrics.model_dump() == expected.metrics.model_dump()


def test_reducer_failure_retains_full_input_in_every_denominator() -> None:
    complete = load_valid_prefix()
    prefix = load_valid_prefix(through_line=complete.through_line - 1)
    mutation = apply_mutation(
        prefix,
        MutationRequest(operator=MutationId.REVIEW_TIMEOUT_FALLBACK),
    )

    comparison = compare_reducers(
        ReductionInput(
            task_identity_sha256=_TASK_IDENTITY,
            case_ordinal=1,
            mutation=mutation,
        )
    )

    assert all(
        result.status is MainAnalysisReductionStatus.FAILED_RETAINED
        for result in comparison.results
    )
    assert all(result.metrics.after == result.metrics.before for result in comparison.results)
    assert all(
        result.output_trace_sha256 == result.input_trace_sha256 for result in comparison.results
    )
    assert all(result.failure_reason for result in comparison.results)


def test_comparison_rejects_mixed_input_digests() -> None:
    prefix = load_valid_prefix()
    mutation = apply_mutation(
        prefix,
        MutationRequest(operator=MutationId.STALE_EVIDENCE_EPOCH),
    )
    comparison = compare_reducers(
        ReductionInput(
            task_identity_sha256=_TASK_IDENTITY,
            case_ordinal=1,
            mutation=mutation,
        )
    )
    changed = comparison.results[0].model_copy(update={"input_trace_sha256": "c" * 64})

    with pytest.raises(ValueError, match="same input trace"):
        MainAnalysisReductionComparison(
            task_identity_sha256=comparison.task_identity_sha256,
            case_ordinal=comparison.case_ordinal,
            input_trace_sha256=comparison.input_trace_sha256,
            results=(changed, *comparison.results[1:]),
        )
