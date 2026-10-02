from __future__ import annotations

import hashlib
import math
from collections import Counter
from decimal import Decimal, localcontext
from fractions import Fraction
from typing import Literal, Self

from pydantic import Field, model_validator

from evidence_harness_mutation.model import FrozenModel

_BOOTSTRAP_RESAMPLES: Literal[10000] = 10_000
_LOWER_PERCENTILE_RANK: Literal[250] = 250
_UPPER_PERCENTILE_RANK: Literal[9750] = 9_750
_DECIMAL_PLACES = 12


class ExactValue(FrozenModel):
    numerator: int
    denominator: int = Field(ge=1)
    decimal: str = Field(pattern=r"^-?[0-9]+\.[0-9]{12}$")

    @classmethod
    def from_fraction(cls, value: Fraction) -> Self:
        reduced = Fraction(value.numerator, value.denominator)
        return cls(
            numerator=reduced.numerator,
            denominator=reduced.denominator,
            decimal=_decimal_text(reduced),
        )

    def as_fraction(self) -> Fraction:
        return Fraction(self.numerator, self.denominator)

    @model_validator(mode="after")
    def validate_exact_value(self) -> Self:
        reduced = Fraction(self.numerator, self.denominator)
        if (self.numerator, self.denominator) != (
            reduced.numerator,
            reduced.denominator,
        ):
            raise ValueError("exact value must be reduced")
        if self.decimal != _decimal_text(reduced):
            raise ValueError("decimal text does not match exact value")
        return self


class ExactProbability(ExactValue):
    @model_validator(mode="after")
    def validate_probability(self) -> Self:
        value = self.as_fraction()
        if value < 0 or value > 1:
            raise ValueError("probability must be between zero and one")
        return self

    @classmethod
    def from_fraction(cls, value: Fraction) -> Self:
        return cls(
            numerator=value.numerator,
            denominator=value.denominator,
            decimal=_decimal_text(value),
        )


class PairedBinaryTask(FrozenModel):
    task_identity: str = Field(min_length=1)
    treatment_detected: bool
    comparator_detected: bool


class RetainedBytesTask(FrozenModel):
    task_identity: str = Field(min_length=1)
    treatment_after_bytes: int = Field(ge=0)
    treatment_before_bytes: int = Field(ge=1)
    comparator_after_bytes: int = Field(ge=0)
    comparator_before_bytes: int = Field(ge=1)

    @model_validator(mode="after")
    def validate_non_growth(self) -> Self:
        if self.treatment_after_bytes > self.treatment_before_bytes:
            raise ValueError("treatment retained bytes cannot exceed input bytes")
        if self.comparator_after_bytes > self.comparator_before_bytes:
            raise ValueError("comparator retained bytes cannot exceed input bytes")
        return self

    @property
    def treatment_fraction(self) -> Fraction:
        return Fraction(self.treatment_after_bytes, self.treatment_before_bytes)

    @property
    def comparator_fraction(self) -> Fraction:
        return Fraction(self.comparator_after_bytes, self.comparator_before_bytes)


class ExactInterval(FrozenModel):
    lower: ExactValue
    upper: ExactValue
    resamples: Literal[10000] = _BOOTSTRAP_RESAMPLES
    lower_one_based_rank: Literal[250] = _LOWER_PERCENTILE_RANK
    upper_one_based_rank: Literal[9750] = _UPPER_PERCENTILE_RANK

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        if self.lower.as_fraction() > self.upper.as_fraction():
            raise ValueError("interval lower bound cannot exceed upper bound")
        return self


class McNemarResult(FrozenModel):
    tasks: int = Field(ge=1)
    both_detected: int = Field(ge=0)
    treatment_only: int = Field(ge=0)
    comparator_only: int = Field(ge=0)
    neither_detected: int = Field(ge=0)
    p_value: ExactProbability
    risk_difference: ExactValue

    @model_validator(mode="after")
    def validate_partition(self) -> Self:
        if (
            self.both_detected + self.treatment_only + self.comparator_only + self.neither_detected
            != self.tasks
        ):
            raise ValueError("McNemar cells must partition tasks")
        expected_difference = Fraction(
            self.treatment_only - self.comparator_only,
            self.tasks,
        )
        if self.risk_difference.as_fraction() != expected_difference:
            raise ValueError("risk difference does not match McNemar cells")
        return self


class WilcoxonResult(FrozenModel):
    pairs: int = Field(ge=1)
    nonzero_pairs: int = Field(ge=0)
    positive_rank_sum: ExactValue
    negative_rank_sum: ExactValue
    p_value: ExactProbability
    rank_biserial: ExactValue

    @model_validator(mode="after")
    def validate_rank_sums(self) -> Self:
        positive = self.positive_rank_sum.as_fraction()
        negative = self.negative_rank_sum.as_fraction()
        total = positive + negative
        if positive < 0 or negative < 0:
            raise ValueError("Wilcoxon rank sums must be nonnegative")
        expected_effect = Fraction(0) if total == 0 else (positive - negative) / total
        if self.rank_biserial.as_fraction() != expected_effect:
            raise ValueError("rank-biserial effect does not match rank sums")
        if self.nonzero_pairs > self.pairs:
            raise ValueError("nonzero Wilcoxon pairs cannot exceed all pairs")
        return self


class HolmInput(FrozenModel):
    comparison: str = Field(min_length=1)
    p_value: ExactProbability


class HolmResult(FrozenModel):
    comparison: str = Field(min_length=1)
    protocol_ordinal: int = Field(ge=1)
    sorted_rank: int = Field(ge=1)
    raw_p_value: ExactProbability
    adjusted_p_value: ExactProbability


def exact_mcnemar(rows: tuple[PairedBinaryTask, ...]) -> McNemarResult:
    _require_unique_tasks(rows)
    if not rows:
        raise ValueError("McNemar test requires at least one task")

    both = sum(row.treatment_detected and row.comparator_detected for row in rows)
    treatment_only = sum(row.treatment_detected and not row.comparator_detected for row in rows)
    comparator_only = sum(not row.treatment_detected and row.comparator_detected for row in rows)
    neither = len(rows) - both - treatment_only - comparator_only
    discordant = treatment_only + comparator_only
    if discordant == 0:
        p_value = Fraction(1)
    else:
        tail = sum(
            math.comb(discordant, count)
            for count in range(min(treatment_only, comparator_only) + 1)
        )
        p_value = min(Fraction(1), Fraction(2 * tail, 2**discordant))

    return McNemarResult(
        tasks=len(rows),
        both_detected=both,
        treatment_only=treatment_only,
        comparator_only=comparator_only,
        neither_detected=neither,
        p_value=ExactProbability.from_fraction(p_value),
        risk_difference=ExactValue.from_fraction(
            Fraction(treatment_only - comparator_only, len(rows))
        ),
    )


def exact_wilcoxon(
    rows: tuple[RetainedBytesTask, ...],
) -> WilcoxonResult:
    _require_unique_tasks(rows)
    if not rows:
        raise ValueError("Wilcoxon test requires at least one task")

    differences = tuple(row.treatment_fraction - row.comparator_fraction for row in rows)
    nonzero = tuple(difference for difference in differences if difference)
    if not nonzero:
        zero = ExactValue.from_fraction(Fraction(0))
        return WilcoxonResult(
            pairs=len(rows),
            nonzero_pairs=0,
            positive_rank_sum=zero,
            negative_rank_sum=zero,
            p_value=ExactProbability.from_fraction(Fraction(1)),
            rank_biserial=zero,
        )

    ranks_twice = _average_ranks_twice(tuple(abs(value) for value in nonzero))
    positive_twice = sum(
        rank for difference, rank in zip(nonzero, ranks_twice, strict=True) if difference > 0
    )
    total_twice = sum(ranks_twice)
    negative_twice = total_twice - positive_twice
    threshold = min(positive_twice, negative_twice)
    distribution = _subset_sum_distribution(ranks_twice)
    extreme = sum(
        count
        for rank_sum, count in distribution.items()
        if rank_sum <= threshold or rank_sum >= total_twice - threshold
    )

    return WilcoxonResult(
        pairs=len(rows),
        nonzero_pairs=len(nonzero),
        positive_rank_sum=ExactValue.from_fraction(Fraction(positive_twice, 2)),
        negative_rank_sum=ExactValue.from_fraction(Fraction(negative_twice, 2)),
        p_value=ExactProbability.from_fraction(
            min(Fraction(1), Fraction(extreme, 2 ** len(nonzero)))
        ),
        rank_biserial=ExactValue.from_fraction(
            Fraction(positive_twice - negative_twice, total_twice)
        ),
    )


def holm_adjust(inputs: tuple[HolmInput, ...]) -> tuple[HolmResult, ...]:
    if not inputs:
        raise ValueError("Holm correction requires at least one comparison")
    comparisons = tuple(item.comparison for item in inputs)
    if len(comparisons) != len(set(comparisons)):
        raise ValueError("Holm comparison names must be unique")

    ordered = sorted(
        enumerate(inputs, start=1),
        key=lambda item: (item[1].p_value.as_fraction(), item[0]),
    )
    adjusted_by_ordinal: dict[int, HolmResult] = {}
    previous = Fraction(0)
    family_size = len(inputs)
    for sorted_rank, (protocol_ordinal, item) in enumerate(ordered, start=1):
        candidate = min(
            Fraction(1),
            item.p_value.as_fraction() * (family_size - sorted_rank + 1),
        )
        adjusted = max(previous, candidate)
        previous = adjusted
        adjusted_by_ordinal[protocol_ordinal] = HolmResult(
            comparison=item.comparison,
            protocol_ordinal=protocol_ordinal,
            sorted_rank=sorted_rank,
            raw_p_value=item.p_value,
            adjusted_p_value=ExactProbability.from_fraction(adjusted),
        )
    return tuple(adjusted_by_ordinal[ordinal] for ordinal in range(1, family_size + 1))


def bootstrap_binary_risk_difference(
    rows: tuple[PairedBinaryTask, ...],
    *,
    seed: bytes,
) -> ExactInterval:
    _require_unique_tasks(rows)
    if not rows:
        raise ValueError("binary bootstrap requires at least one task")
    values = tuple(int(row.treatment_detected) - int(row.comparator_detected) for row in rows)
    estimates = tuple(
        Fraction(
            sum(
                values[_counter_draw(seed, resample, draw, len(values))]
                for draw in range(1, len(values) + 1)
            ),
            len(values),
        )
        for resample in range(1, _BOOTSTRAP_RESAMPLES + 1)
    )
    return _percentile_interval(estimates)


def bootstrap_clustered_retained_difference(
    rows: tuple[RetainedBytesTask, ...],
    *,
    seed: bytes,
) -> ExactInterval:
    _require_unique_tasks(rows)
    if not rows:
        raise ValueError("cluster bootstrap requires at least one task")

    estimates: list[Fraction] = []
    for resample in range(1, _BOOTSTRAP_RESAMPLES + 1):
        sampled = tuple(
            rows[_counter_draw(seed, resample, draw, len(rows))] for draw in range(1, len(rows) + 1)
        )
        treatment = Fraction(
            sum(row.treatment_after_bytes for row in sampled),
            sum(row.treatment_before_bytes for row in sampled),
        )
        comparator = Fraction(
            sum(row.comparator_after_bytes for row in sampled),
            sum(row.comparator_before_bytes for row in sampled),
        )
        estimates.append(treatment - comparator)
    return _percentile_interval(tuple(estimates))


def _require_unique_tasks(
    rows: tuple[PairedBinaryTask, ...] | tuple[RetainedBytesTask, ...],
) -> None:
    identities = tuple(row.task_identity for row in rows)
    if len(identities) != len(set(identities)):
        raise ValueError("statistical task identities must be unique")


def _average_ranks_twice(values: tuple[Fraction, ...]) -> tuple[int, ...]:
    indexed = sorted(enumerate(values), key=lambda item: (item[1], item[0]))
    ranks = [0] * len(values)
    start = 0
    while start < len(indexed):
        end = start + 1
        while end < len(indexed) and indexed[end][1] == indexed[start][1]:
            end += 1
        rank_twice = (start + 1) + end
        for original_index, _value in indexed[start:end]:
            ranks[original_index] = rank_twice
        start = end
    return tuple(ranks)


def _subset_sum_distribution(weights: tuple[int, ...]) -> Counter[int]:
    distribution: Counter[int] = Counter({0: 1})
    for weight in weights:
        shifted = Counter({rank_sum + weight: count for rank_sum, count in distribution.items()})
        distribution.update(shifted)
    return distribution


def _counter_draw(
    seed: bytes,
    resample_ordinal: int,
    draw_ordinal: int,
    population: int,
) -> int:
    if not seed:
        raise ValueError("bootstrap seed cannot be empty")
    if resample_ordinal < 1 or draw_ordinal < 1:
        raise ValueError("bootstrap ordinals must be positive")
    if population < 1:
        raise ValueError("bootstrap population must be positive")
    material = b"\0".join(
        (
            seed,
            str(resample_ordinal).encode("ascii"),
            str(draw_ordinal).encode("ascii"),
        )
    )
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big") % population


def _percentile_interval(estimates: tuple[Fraction, ...]) -> ExactInterval:
    if len(estimates) != _BOOTSTRAP_RESAMPLES:
        raise ValueError("percentile interval requires exactly 10,000 estimates")
    ordered = sorted(estimates)
    return ExactInterval(
        lower=ExactValue.from_fraction(ordered[_LOWER_PERCENTILE_RANK - 1]),
        upper=ExactValue.from_fraction(ordered[_UPPER_PERCENTILE_RANK - 1]),
    )


def _decimal_text(value: Fraction) -> str:
    with localcontext() as context:
        context.prec = 64
        decimal = Decimal(value.numerator) / Decimal(value.denominator)
        quantum = Decimal(1).scaleb(-_DECIMAL_PLACES)
        return format(decimal.quantize(quantum), f".{_DECIMAL_PLACES}f")
