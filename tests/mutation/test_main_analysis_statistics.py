from __future__ import annotations

from fractions import Fraction

import pytest
from pydantic import ValidationError

from evidence_harness_mutation.main_analysis_statistics import (
    ExactProbability,
    ExactValue,
    HolmInput,
    PairedBinaryTask,
    RetainedBytesTask,
    bootstrap_binary_risk_difference,
    bootstrap_clustered_retained_difference,
    exact_mcnemar,
    exact_wilcoxon,
    holm_adjust,
)


def _binary_rows(
    values: tuple[tuple[bool, bool], ...],
) -> tuple[PairedBinaryTask, ...]:
    return tuple(
        PairedBinaryTask(
            task_identity=f"task-{ordinal}",
            treatment_detected=treatment,
            comparator_detected=comparator,
        )
        for ordinal, (treatment, comparator) in enumerate(values, start=1)
    )


def _retained_rows(
    values: tuple[tuple[int, int], ...],
) -> tuple[RetainedBytesTask, ...]:
    return tuple(
        RetainedBytesTask(
            task_identity=f"task-{ordinal}",
            treatment_after_bytes=treatment,
            treatment_before_bytes=10,
            comparator_after_bytes=comparator,
            comparator_before_bytes=10,
        )
        for ordinal, (treatment, comparator) in enumerate(values, start=1)
    )


def test_exact_value_reduces_and_serializes_stable_decimal_text() -> None:
    value = ExactValue.from_fraction(Fraction(-2, 6))

    assert value.model_dump() == {
        "numerator": -1,
        "denominator": 3,
        "decimal": "-0.333333333333",
    }

    with pytest.raises(ValidationError, match="must be reduced"):
        ExactValue(numerator=2, denominator=4, decimal="0.500000000000")


def test_exact_mcnemar_reports_cells_p_value_and_paired_risk_difference() -> None:
    result = exact_mcnemar(
        _binary_rows(
            (
                (True, True),
                (True, False),
                (True, False),
                (True, False),
                (False, True),
                (False, False),
            )
        )
    )

    assert result.model_dump() == {
        "tasks": 6,
        "both_detected": 1,
        "treatment_only": 3,
        "comparator_only": 1,
        "neither_detected": 1,
        "p_value": {
            "numerator": 5,
            "denominator": 8,
            "decimal": "0.625000000000",
        },
        "risk_difference": {
            "numerator": 1,
            "denominator": 3,
            "decimal": "0.333333333333",
        },
    }


def test_exact_mcnemar_returns_one_without_discordant_pairs() -> None:
    result = exact_mcnemar(_binary_rows(((True, True), (False, False))))

    assert result.p_value.as_fraction() == 1
    assert result.risk_difference.as_fraction() == 0


def test_exact_wilcoxon_uses_average_ranks_for_ties() -> None:
    result = exact_wilcoxon(_retained_rows(((4, 5), (6, 5), (3, 5))))

    assert result.model_dump() == {
        "pairs": 3,
        "nonzero_pairs": 3,
        "positive_rank_sum": {
            "numerator": 3,
            "denominator": 2,
            "decimal": "1.500000000000",
        },
        "negative_rank_sum": {
            "numerator": 9,
            "denominator": 2,
            "decimal": "4.500000000000",
        },
        "p_value": {
            "numerator": 3,
            "denominator": 4,
            "decimal": "0.750000000000",
        },
        "rank_biserial": {
            "numerator": -1,
            "denominator": 2,
            "decimal": "-0.500000000000",
        },
    }


def test_exact_wilcoxon_omits_zero_differences_from_sign_enumeration() -> None:
    result = exact_wilcoxon(_retained_rows(((5, 5), (5, 5))))

    assert result.nonzero_pairs == 0
    assert result.p_value.as_fraction() == 1
    assert result.rank_biserial.as_fraction() == 0


def test_holm_uses_protocol_order_for_ties_and_monotonic_adjustment() -> None:
    adjusted = holm_adjust(
        (
            HolmInput(
                comparison="first",
                p_value=ExactProbability.from_fraction(Fraction(1, 100)),
            ),
            HolmInput(
                comparison="second",
                p_value=ExactProbability.from_fraction(Fraction(1, 100)),
            ),
            HolmInput(
                comparison="third",
                p_value=ExactProbability.from_fraction(Fraction(1, 25)),
            ),
        )
    )

    assert [
        (
            item.comparison,
            item.sorted_rank,
            item.adjusted_p_value.as_fraction(),
        )
        for item in adjusted
    ] == [
        ("first", 1, Fraction(3, 100)),
        ("second", 2, Fraction(3, 100)),
        ("third", 3, Fraction(1, 25)),
    ]


def test_binary_bootstrap_is_deterministic_and_uses_fixed_percentile_ranks() -> None:
    rows = _binary_rows(((True, False), (True, False), (True, False)))

    first = bootstrap_binary_risk_difference(rows, seed=b"rq2-test")
    second = bootstrap_binary_risk_difference(rows, seed=b"rq2-test")

    assert first == second
    assert first.lower.as_fraction() == 1
    assert first.upper.as_fraction() == 1
    assert first.resamples == 10_000
    assert first.lower_one_based_rank == 250
    assert first.upper_one_based_rank == 9_750


def test_cluster_bootstrap_samples_whole_task_totals() -> None:
    rows = (
        RetainedBytesTask(
            task_identity="task-a",
            treatment_after_bytes=2,
            treatment_before_bytes=10,
            comparator_after_bytes=8,
            comparator_before_bytes=10,
        ),
        RetainedBytesTask(
            task_identity="task-b",
            treatment_after_bytes=1,
            treatment_before_bytes=20,
            comparator_after_bytes=11,
            comparator_before_bytes=20,
        ),
    )

    interval = bootstrap_clustered_retained_difference(rows, seed=b"rq3-test")

    assert interval.lower.as_fraction() == Fraction(-3, 5)
    assert interval.upper.as_fraction() == Fraction(-1, 2)


@pytest.mark.parametrize(
    "operation",
    [
        lambda rows: exact_mcnemar(rows),
        lambda rows: bootstrap_binary_risk_difference(rows, seed=b"rq2"),
    ],
)
def test_statistics_reject_duplicate_task_identities(operation: object) -> None:
    rows = (
        PairedBinaryTask(
            task_identity="same",
            treatment_detected=True,
            comparator_detected=False,
        ),
        PairedBinaryTask(
            task_identity="same",
            treatment_detected=False,
            comparator_detected=True,
        ),
    )

    with pytest.raises(ValueError, match="identities must be unique"):
        operation(rows)  # type: ignore[operator]
