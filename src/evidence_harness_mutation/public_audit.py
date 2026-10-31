from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, model_validator

from evidence_harness_mutation.candidate_gate import _canonical_json
from evidence_harness_mutation.erosion_campaign import SeedCategory, seed_label
from evidence_harness_mutation.model import FrozenModel, Sha256


class PublicAuditError(ValueError):
    pass


class EditDirection(StrEnum):
    ERODES = "erodes"
    STRENGTHENS = "strengthens"
    NEUTRAL = "neutral"


class Confidence(StrEnum):
    CLEAR = "clear"
    AMBIGUOUS = "ambiguous"


class Candidate(FrozenModel):
    name: str
    iteration: int = Field(ge=0)
    kind: Literal["code", "prompt"]
    parent: str | None
    rate: float | None
    added: tuple[str, ...]
    deleted: tuple[str, ...]


class CandidateLabel(FrozenModel):
    name: str = Field(min_length=1)
    direction: EditDirection
    confidence: Confidence
    category: SeedCategory | None
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_category(self) -> Self:
        eroding = self.direction is EditDirection.ERODES
        if eroding != (self.category is not None):
            raise ValueError("exactly the eroding labels carry a category")
        if self.category is not None and seed_label(self.category) != "erosion":
            raise ValueError("an eroding label needs an erosion category")
        return self


class AuditLabels(FrozenModel):
    schema_version: Literal[1]
    source_url: str
    source_sha256: Sha256
    codebook: dict[EditDirection, str]
    labels: tuple[CandidateLabel, ...]


class AuditRow(FrozenModel):
    name: str
    iteration: int
    kind: Literal["code", "prompt"]
    parent: str | None
    rate: float | None
    parent_rate: float | None
    label: CandidateLabel


class FrontierStep(FrozenModel):
    iteration: int
    best: str
    best_rate: float
    eroding_ancestors: tuple[str, ...]


class DirectionCount(FrozenModel):
    direction: EditDirection
    clear: int
    ambiguous: int
    scored_above_parent: int
    scored: int


class PublicAuditReport(FrozenModel):
    schema_version: Literal[1] = 1
    source_url: str
    source_sha256: Sha256
    labels_sha256: Sha256
    proposals: int
    directions: tuple[DirectionCount, ...]
    frontier: tuple[FrontierStep, ...]
    rows: tuple[AuditRow, ...]

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.model_dump(mode="json"))


def parse_evo_literal(source: str) -> tuple[Candidate, ...]:
    """Read the Meta-Harness `var EVO=[...]` page literal as data, never as code."""
    start = source.find("EVO=")
    end = source.find(",BASELINES=")
    if start < 0 or end < 0:
        raise PublicAuditError("source does not contain the EVO literal")
    try:
        iterations = json.loads(_js_literal_to_json(source[start + 4 : end]))
    except json.JSONDecodeError as exc:
        raise PublicAuditError(f"EVO literal is not parseable: {exc}") from exc
    return tuple(
        Candidate(
            name=agent["name"],
            iteration=step["iter"],
            kind=agent["type"],
            parent=agent["parent"],
            rate=agent["rate"],
            added=tuple(agent.get("add", ())),
            deleted=tuple(agent.get("del", ())),
        )
        for step in iterations
        for agent in step["agents"]
    )


def _js_literal_to_json(literal: str) -> str:
    out: list[str] = []
    index = 0
    while index < len(literal):
        char = literal[index]
        if char in "\"'":
            closing = index + 1
            while literal[closing] != char:
                closing += 2 if literal[closing] == "\\" else 1
            out.append(json.dumps(literal[index + 1 : closing].replace("\\'", "'")))
            index = closing + 1
        elif char.isalpha() or char == "_":
            closing = index
            while literal[closing].isalnum() or literal[closing] == "_":
                closing += 1
            word = literal[index:closing]
            out.append(word if word in ("null", "true", "false") else json.dumps(word))
            index = closing
        elif char == "." and literal[index + 1].isdigit():
            out.append("0.")
            index += 1
        else:
            out.append(char)
            index += 1
    return "".join(out)


def build_public_audit(source: bytes, labels_data: bytes) -> PublicAuditReport:
    labels = AuditLabels.model_validate_json(labels_data)
    if hashlib.sha256(source).hexdigest() != labels.source_sha256:
        raise PublicAuditError("source digest does not match the frozen labels")
    candidates = parse_evo_literal(source.decode())
    by_name = {candidate.name: candidate for candidate in candidates}
    proposals = tuple(candidate for candidate in candidates if candidate.parent is not None)
    label_by_name = {label.name: label for label in labels.labels}
    if set(label_by_name) != {candidate.name for candidate in proposals} or len(
        label_by_name
    ) != len(labels.labels):
        raise PublicAuditError("labels must cover every proposal exactly once")

    rows = tuple(
        AuditRow(
            name=candidate.name,
            iteration=candidate.iteration,
            kind=candidate.kind,
            parent=candidate.parent,
            rate=candidate.rate,
            parent_rate=by_name[candidate.parent].rate if candidate.parent else None,
            label=label_by_name[candidate.name],
        )
        for candidate in proposals
    )
    return PublicAuditReport(
        source_url=labels.source_url,
        source_sha256=labels.source_sha256,
        labels_sha256=hashlib.sha256(labels_data).hexdigest(),
        proposals=len(rows),
        directions=tuple(_direction_count(rows, direction) for direction in EditDirection),
        frontier=_frontier(candidates, by_name, label_by_name),
        rows=rows,
    )


def _direction_count(rows: tuple[AuditRow, ...], direction: EditDirection) -> DirectionCount:
    members = tuple(row for row in rows if row.label.direction is direction)
    scored = tuple(row for row in members if row.rate is not None and row.parent_rate is not None)
    return DirectionCount(
        direction=direction,
        clear=sum(row.label.confidence is Confidence.CLEAR for row in members),
        ambiguous=sum(row.label.confidence is Confidence.AMBIGUOUS for row in members),
        scored_above_parent=sum(
            row.rate is not None and row.parent_rate is not None and row.rate > row.parent_rate
            for row in scored
        ),
        scored=len(scored),
    )


def _frontier(
    candidates: tuple[Candidate, ...],
    by_name: dict[str, Candidate],
    label_by_name: dict[str, CandidateLabel],
) -> tuple[FrontierStep, ...]:
    steps: list[FrontierStep] = []
    best: Candidate | None = None
    for iteration in sorted({candidate.iteration for candidate in candidates}):
        for candidate in candidates:
            if (
                candidate.iteration == iteration
                and candidate.rate is not None
                and (best is None or best.rate is None or candidate.rate > best.rate)
            ):
                best = candidate
        assert best is not None and best.rate is not None
        steps.append(
            FrontierStep(
                iteration=iteration,
                best=best.name,
                best_rate=best.rate,
                eroding_ancestors=tuple(
                    name
                    for name in _lineage(best, by_name)
                    if name in label_by_name
                    and label_by_name[name].direction is EditDirection.ERODES
                    and label_by_name[name].confidence is Confidence.CLEAR
                ),
            )
        )
    return tuple(steps)


def _lineage(candidate: Candidate, by_name: dict[str, Candidate]) -> tuple[str, ...]:
    names: list[str] = []
    current: Candidate | None = candidate
    while current is not None:
        names.append(current.name)
        current = by_name[current.parent] if current.parent else None
    return tuple(reversed(names))
