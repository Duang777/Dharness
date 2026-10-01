from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

EventOrdinal = Annotated[int, Field(ge=1)]
Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class TraceStructureError(ValueError):
    pass


class FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class JournalBinding(FrozenModel):
    source_commit: str = Field(pattern=r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
    journal_sha256: Sha256
    journal_schema_version: int = Field(default=2, ge=2, le=2)


class RecordedEvent(FrozenModel):
    line: EventOrdinal
    line_sha256: Sha256
    timestamp: str = Field(min_length=1)
    event_type: str = Field(min_length=1)
    payload: dict[str, JsonValue]


class SourceEventAnchor(FrozenModel):
    line: EventOrdinal
    line_sha256: Sha256

    @classmethod
    def from_event(cls, event: RecordedEvent) -> Self:
        return cls(line=event.line, line_sha256=event.line_sha256)

    def matches(self, event: RecordedEvent) -> bool:
        return self.line == event.line and self.line_sha256 == event.line_sha256


class OfflineTrace(FrozenModel):
    source: JournalBinding
    through_line: EventOrdinal
    instruction: str = Field(min_length=1)
    completion_review_enabled: bool
    events: tuple[RecordedEvent, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_source_order(self) -> Self:
        lines = tuple(event.line for event in self.events)
        if lines != tuple(sorted(set(lines))):
            raise ValueError("trace events must retain unique ascending source lines")
        if lines[-1] > self.through_line:
            raise ValueError("trace events cannot extend beyond through_line")
        if lines[0] != 1 or self.events[0].event_type != "run_started":
            raise ValueError("trace must retain the leading run_started event")
        return self


class StatePrefix(OfflineTrace):
    @model_validator(mode="after")
    def validate_event_range(self) -> Self:
        lines = tuple(event.line for event in self.events)
        if lines != tuple(range(1, self.through_line + 1)):
            raise ValueError("prefix events must contain every journal line through through_line")
        return self


class InvariantId(StrEnum):
    I1 = "I1"
    I2 = "I2"
    I3 = "I3"
    I4 = "I4"


class InvariantStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    NOT_APPLICABLE = "not_applicable"


class InvariantViolation(FrozenModel):
    invariant: InvariantId
    terminal_line: EventOrdinal
    attempt_ordinal: int = Field(ge=1)
    related_lines: tuple[EventOrdinal, ...] = ()
    details: tuple[str, ...] = Field(min_length=1)


class InvariantResult(FrozenModel):
    invariant: InvariantId
    status: InvariantStatus
    violations: tuple[InvariantViolation, ...] = ()

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if self.status is InvariantStatus.FAIL and not self.violations:
            raise ValueError("failed invariant result requires a violation")
        if self.status is not InvariantStatus.FAIL and self.violations:
            raise ValueError("only failed invariant results may contain violations")
        return self


class AuditReport(FrozenModel):
    source: JournalBinding
    through_line: EventOrdinal
    verified_attempts: int = Field(ge=0)
    results: tuple[InvariantResult, ...] = Field(min_length=4, max_length=4)

    @property
    def passed(self) -> bool:
        return self.verified_attempts > 0 and all(
            result.status is InvariantStatus.PASS for result in self.results
        )

    @property
    def violations(self) -> tuple[InvariantViolation, ...]:
        return tuple(violation for result in self.results for violation in result.violations)

    def result(self, invariant: InvariantId) -> InvariantResult:
        return next(result for result in self.results if result.invariant is invariant)
