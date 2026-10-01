from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import BaseModel, ConfigDict, ValidationError

from evidence_harness.protocol import ActionKind, AgentDecision, StopReason
from evidence_harness_mutation.model import RecordedEvent, TraceStructureError


class _EventPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _RunFinished(_EventPayload):
    stop_reason: StopReason


@dataclass(slots=True)
class CompletionAttempt:
    ordinal: int
    proposal_event: RecordedEvent
    decision: AgentDecision
    events: list[RecordedEvent] = field(default_factory=list)
    terminal_line: int | None = None

    @property
    def verified(self) -> bool:
        return self.terminal_line is not None

    def of_type(self, event_type: str) -> tuple[RecordedEvent, ...]:
        return tuple(event for event in self.events if event.event_type == event_type)


def project_completion_attempts(
    events: tuple[RecordedEvent, ...],
) -> tuple[CompletionAttempt, ...]:
    attempts: list[CompletionAttempt] = []
    active: CompletionAttempt | None = None
    attempt_ordinal = 0
    for event in events:
        if event.event_type == "agent_decision":
            decision = parse_event(event, AgentDecision)
            active = None
            if decision.action is ActionKind.FINISH:
                attempt_ordinal += 1
                active = CompletionAttempt(
                    ordinal=attempt_ordinal,
                    proposal_event=event,
                    decision=decision,
                )
                attempts.append(active)
            continue
        if active is None:
            continue
        active.events.append(event)
        if event.event_type == "run_finished":
            finished = parse_event(event, _RunFinished)
            if finished.stop_reason is StopReason.VERIFIED:
                active.terminal_line = event.line
            active = None
    return tuple(attempts)


def parse_event[Payload: BaseModel](
    event: RecordedEvent,
    model: type[Payload],
) -> Payload:
    try:
        return model.model_validate(event.payload)
    except ValidationError as exc:
        raise TraceStructureError(
            f"invalid {event.event_type} payload on journal line {event.line}"
        ) from exc
