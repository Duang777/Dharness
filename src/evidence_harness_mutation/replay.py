from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import ValidationError

from evidence_harness.protocol import AgentDecision, ReviewDecision, UsageTotals
from evidence_harness_mutation.model import StatePrefix


class ReplayCallKind(StrEnum):
    DECIDE = "decide"
    REVIEW = "review"


class ReplayContractError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class _ReplayCall:
    kind: ReplayCallKind
    line: int
    value: AgentDecision | ReviewDecision


class ReplayGateway:
    def __init__(self, prefix: StatePrefix) -> None:
        calls: list[_ReplayCall] = []
        for event in prefix.events:
            try:
                if event.event_type == "agent_decision":
                    calls.append(
                        _ReplayCall(
                            kind=ReplayCallKind.DECIDE,
                            line=event.line,
                            value=AgentDecision.model_validate(event.payload),
                        )
                    )
                elif event.event_type == "completion_review":
                    calls.append(
                        _ReplayCall(
                            kind=ReplayCallKind.REVIEW,
                            line=event.line,
                            value=ReviewDecision.model_validate(event.payload),
                        )
                    )
            except ValidationError as exc:
                raise ReplayContractError(
                    f"invalid {event.event_type} payload on journal line {event.line}"
                ) from exc
        self._calls = tuple(calls)
        self._position = 0

    @classmethod
    def from_prefix(cls, prefix: StatePrefix) -> ReplayGateway:
        return cls(prefix)

    @property
    def usage(self) -> UsageTotals:
        return UsageTotals(model_calls=self._position)

    @property
    def consumed_lines(self) -> tuple[int, ...]:
        return tuple(call.line for call in self._calls[: self._position])

    async def decide(self, prompt: str) -> AgentDecision:
        del prompt
        call = self._consume(ReplayCallKind.DECIDE)
        if not isinstance(call.value, AgentDecision):
            raise AssertionError("replay call kind and payload type diverged")
        return call.value.model_copy(deep=True)

    async def review(self, prompt: str) -> ReviewDecision:
        del prompt
        call = self._consume(ReplayCallKind.REVIEW)
        if not isinstance(call.value, ReviewDecision):
            raise AssertionError("replay call kind and payload type diverged")
        return call.value.model_copy(deep=True)

    def assert_exhausted(self) -> None:
        if self._position == len(self._calls):
            return
        call = self._calls[self._position]
        raise ReplayContractError(
            f"unconsumed {call.kind.value} call from journal line {call.line}"
        )

    def _consume(self, expected: ReplayCallKind) -> _ReplayCall:
        if self._position >= len(self._calls):
            raise ReplayContractError(f"no recorded {expected.value} call remains")
        call = self._calls[self._position]
        if call.kind is not expected:
            raise ReplayContractError(
                f"expected replay {call.kind.value} from journal line {call.line}, "
                f"got {expected.value}"
            )
        self._position += 1
        return call
