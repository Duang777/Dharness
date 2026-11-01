from __future__ import annotations

import hashlib
import math
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from enum import StrEnum
from itertools import combinations
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator

from evidence_harness_mutation.candidate_gate import (
    GUARD_COMPONENTS,
    GateDecision,
    GateRequest,
    GateVerdict,
    GitObjectId,
    GuardComponent,
    ProbeStatus,
    _canonical_json,
    load_guard_suite,
    run_candidate_gate,
)
from evidence_harness_mutation.model import FrozenModel, Sha256


class ErosionCampaignError(RuntimeError):
    pass


class SeedCategory(StrEnum):
    DELETE_VERIFICATION = "delete_verification"
    CAP_VERIFICATION = "cap_verification"
    TURN_FORCED_COMPLETION = "turn_forced_completion"
    FAST_CHECK_COMMIT = "fast_check_commit"
    TRUST_SELF_REPORT = "trust_self_report"
    REVIEW_FAILURE_PASS = "review_failure_pass"
    STALE_RECEIPT_REUSE = "stale_receipt_reuse"
    OTHER_EROSION = "other_erosion"
    HISTORICAL_REGRESSION = "historical_regression"
    PROMPT_TUNING = "prompt_tuning"
    OBSERVATION = "observation"
    CONTEXT_MANAGEMENT = "context_management"
    REFACTOR = "refactor"
    TELEMETRY = "telemetry"
    TIMING = "timing"
    RECOVERY = "recovery"


SeedLabel = Literal["erosion", "benign"]
_BENIGN_CATEGORIES = frozenset(
    {
        SeedCategory.PROMPT_TUNING,
        SeedCategory.OBSERVATION,
        SeedCategory.CONTEXT_MANAGEMENT,
        SeedCategory.REFACTOR,
        SeedCategory.TELEMETRY,
        SeedCategory.TIMING,
        SeedCategory.RECOVERY,
    }
)


def seed_label(category: SeedCategory) -> SeedLabel:
    return "benign" if category in _BENIGN_CATEGORIES else "erosion"


class SeedProvenance(StrEnum):
    AUTHOR = "author"
    HISTORICAL_PORT = "historical_port"


class SeedPatch(FrozenModel):
    seed_id: str = Field(pattern=r"^[a-z0-9_]+$")
    category: SeedCategory
    provenance: SeedProvenance
    description: str = Field(min_length=1)
    patch: str = Field(pattern=r"^seeded/[a-z_]+/[a-z0-9_]+\.patch$")
    patch_sha256: Sha256

    @model_validator(mode="after")
    def validate_provenance(self) -> Self:
        historical = self.category is SeedCategory.HISTORICAL_REGRESSION
        if historical != (self.provenance is SeedProvenance.HISTORICAL_PORT):
            raise ValueError("historical_regression is exactly the historical_port provenance")
        return self


class SeedCorpus(FrozenModel):
    schema_version: Literal[1]
    corpus_id: str = Field(min_length=1)
    base_commit: GitObjectId
    suite: str = Field(min_length=1)
    seeds: tuple[SeedPatch, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique(self) -> Self:
        if len({seed.seed_id for seed in self.seeds}) != len(self.seeds):
            raise ValueError("seed ids must be unique")
        return self


class SeedOutcome(FrozenModel):
    seed_id: str
    category: SeedCategory
    label: SeedLabel
    provenance: SeedProvenance
    decision: GateDecision
    violated_probes: tuple[str, ...]
    violated_components: tuple[GuardComponent, ...]


class Rate(FrozenModel):
    """Wilson 95% score interval, rendered at four decimals so reports are byte-stable."""

    hits: int = Field(ge=0)
    total: int = Field(ge=0)
    rate: str | None
    low: str | None
    high: str | None


class ComponentAblation(FrozenModel):
    components: tuple[GuardComponent, ...]
    erosion_rejected: Rate
    benign_rejected: Rate


class CategoryDetection(FrozenModel):
    category: SeedCategory
    label: SeedLabel
    rejected: Rate
    misclassified_seeds: tuple[str, ...]


class ProvenanceSummary(FrozenModel):
    provenance: SeedProvenance
    ablations: tuple[ComponentAblation, ...]


class SeededCampaignReport(FrozenModel):
    schema_version: Literal[1] = 1
    corpus_id: str
    corpus_sha256: Sha256
    suite_id: str
    suite_sha256: Sha256
    base_commit: GitObjectId
    outcomes: tuple[SeedOutcome, ...]
    categories: tuple[CategoryDetection, ...]
    provenances: tuple[ProvenanceSummary, ...]

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.model_dump(mode="json"))


def load_seed_corpus(path: Path) -> tuple[SeedCorpus, str]:
    data = path.read_bytes()
    corpus = SeedCorpus.model_validate_json(data)
    for seed in corpus.seeds:
        digest = hashlib.sha256((path.parent / seed.patch).read_bytes()).hexdigest()
        if digest != seed.patch_sha256:
            raise ErosionCampaignError(f"patch digest mismatch for seed {seed.seed_id}")
    return corpus, hashlib.sha256(data).hexdigest()


def run_seeded_campaign(
    corpus_path: Path,
    *,
    repo_root: Path,
    jobs: int = 1,
    gate: Callable[[GateRequest], GateVerdict] = run_candidate_gate,
) -> tuple[SeededCampaignReport, dict[str, float]]:
    corpus, corpus_sha256 = load_seed_corpus(corpus_path)
    suite_path = corpus_path.parent / corpus.suite
    suite, suite_sha256 = load_guard_suite(suite_path)
    probe_components = {probe.probe_id: probe.component for probe in suite.probes}

    def evaluate(seed: SeedPatch) -> GateVerdict:
        return gate(
            GateRequest(
                repo_root=repo_root,
                patch=(corpus_path.parent / seed.patch).read_bytes(),
                base=corpus.base_commit,
                suite_path=suite_path,
            )
        )

    with ThreadPoolExecutor(max_workers=jobs) as pool:
        verdicts = tuple(pool.map(evaluate, corpus.seeds))
    outcomes = tuple(
        _outcome(seed, verdict, probe_components)
        for seed, verdict in zip(corpus.seeds, verdicts, strict=True)
    )
    report = SeededCampaignReport(
        corpus_id=corpus.corpus_id,
        corpus_sha256=corpus_sha256,
        suite_id=suite.suite_id,
        suite_sha256=suite_sha256,
        base_commit=corpus.base_commit,
        outcomes=outcomes,
        categories=summarize_categories(outcomes),
        provenances=tuple(
            ProvenanceSummary(
                provenance=provenance,
                ablations=ablate_components(
                    tuple(item for item in outcomes if item.provenance is provenance)
                ),
            )
            for provenance in SeedProvenance
            if any(item.provenance is provenance for item in outcomes)
        ),
    )
    timing = {
        seed.seed_id: round(verdict.elapsed_sec, 3)
        for seed, verdict in zip(corpus.seeds, verdicts, strict=True)
    }
    return report, timing


def _outcome(
    seed: SeedPatch,
    verdict: GateVerdict,
    probe_components: dict[str, GuardComponent],
) -> SeedOutcome:
    if verdict.decision not in (GateDecision.ACCEPT, GateDecision.REJECT):
        raise ErosionCampaignError(
            f"seed {seed.seed_id} did not reach the probes: {verdict.decision} {verdict.detail}"
        )
    violated = tuple(
        sorted(probe.probe_id for probe in verdict.probes if probe.status is ProbeStatus.VIOLATED)
    )
    return SeedOutcome(
        seed_id=seed.seed_id,
        category=seed.category,
        label=seed_label(seed.category),
        provenance=seed.provenance,
        decision=verdict.decision,
        violated_probes=violated,
        violated_components=_ordered_components(probe_components[item] for item in violated),
    )


def summarize_categories(outcomes: tuple[SeedOutcome, ...]) -> tuple[CategoryDetection, ...]:
    return tuple(
        CategoryDetection(
            category=category,
            label=seed_label(category),
            rejected=wilson_rate(
                sum(item.decision is GateDecision.REJECT for item in members), len(members)
            ),
            misclassified_seeds=tuple(
                item.seed_id
                for item in members
                if (item.decision is GateDecision.REJECT) != (item.label == "erosion")
            ),
        )
        for category in SeedCategory
        if (members := tuple(item for item in outcomes if item.category is category))
    )


def ablate_components(outcomes: tuple[SeedOutcome, ...]) -> tuple[ComponentAblation, ...]:
    erosion = tuple(item for item in outcomes if item.label == "erosion")
    benign = tuple(item for item in outcomes if item.label == "benign")
    subsets = (
        subset
        for size in range(1, len(GUARD_COMPONENTS) + 1)
        for subset in combinations(GUARD_COMPONENTS, size)
    )
    return tuple(
        ComponentAblation(
            components=subset,
            erosion_rejected=_rejected_by(erosion, subset),
            benign_rejected=_rejected_by(benign, subset),
        )
        for subset in subsets
    )


def _rejected_by(outcomes: tuple[SeedOutcome, ...], subset: tuple[GuardComponent, ...]) -> Rate:
    return wilson_rate(
        sum(bool(set(item.violated_components) & set(subset)) for item in outcomes),
        len(outcomes),
    )


def wilson_rate(hits: int, total: int, *, z: float = 1.959963984540054) -> Rate:
    if total == 0:
        return Rate(hits=0, total=0, rate=None, low=None, high=None)
    proportion = hits / total
    denominator = 1 + z * z / total
    centre = (proportion + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total))
    margin /= denominator
    return Rate(
        hits=hits,
        total=total,
        rate=f"{proportion:.4f}",
        low=f"{max(0.0, centre - margin):.4f}",
        high=f"{min(1.0, centre + margin):.4f}",
    )


def _ordered_components(components: Iterable[GuardComponent]) -> tuple[GuardComponent, ...]:
    present = set(components)
    return tuple(component for component in GuardComponent if component in present)
