from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import dataclass
from typing import Literal

from pydantic import Field, JsonValue, ValidationError, model_validator

from evidence_harness_mutation.attempts import CompletionAttempt, project_completion_attempts
from evidence_harness_mutation.invariants import audit_completion_trace
from evidence_harness_mutation.model import (
    AuditReport,
    FrozenModel,
    InvariantId,
    InvariantStatus,
    InvariantViolation,
    JournalBinding,
    OfflineTrace,
    RecordedEvent,
    Sha256,
    SourceEventAnchor,
    TraceStructureError,
)
from evidence_harness_mutation.operators import (
    AppliedMutation,
    MutationRequest,
    MutationTarget,
)

_AUDIT_REPETITIONS: Literal[3] = 3
_MINIMALITY: Literal["1-minimal-under-declared-removals"] = "1-minimal-under-declared-removals"
PayloadPath = tuple[str | int, ...]


class ReductionError(ValueError):
    pass


class VerifiedAttemptAnchor(FrozenModel):
    proposal: SourceEventAnchor
    terminal: SourceEventAnchor


class ViolationWitness(FrozenModel):
    invariant: InvariantId
    attempt: VerifiedAttemptAnchor
    target: MutationTarget
    details: tuple[str, ...] = Field(min_length=1)


class CounterexampleSize(FrozenModel):
    event_count: int = Field(ge=1)
    payload_member_count: int = Field(ge=0)
    canonical_json_bytes: int = Field(ge=1)


class ReductionRejections(FrozenModel):
    malformed: int = Field(ge=0)
    not_applicable: int = Field(ge=0)
    no_violation: int = Field(ge=0)
    cause_changed: int = Field(ge=0)
    nondeterministic: int = Field(ge=0)


class ReductionMetrics(FrozenModel):
    before: CounterexampleSize
    after: CounterexampleSize
    candidates_evaluated: int = Field(ge=0)
    audits_executed: int = Field(ge=_AUDIT_REPETITIONS)
    accepted_reductions: int = Field(ge=0)
    rejections: ReductionRejections

    @model_validator(mode="after")
    def validate_non_growth(self) -> ReductionMetrics:
        if self.after.event_count > self.before.event_count:
            raise ValueError("counterexample reduction cannot add events")
        if self.after.payload_member_count > self.before.payload_member_count:
            raise ValueError("counterexample reduction cannot add payload members")
        if self.after.canonical_json_bytes > self.before.canonical_json_bytes:
            raise ValueError("counterexample reduction cannot add canonical JSON bytes")
        return self


class CounterexampleProvenance(FrozenModel):
    schema_version: Literal[1] = 1
    algorithm: Literal["source-anchored-ddmin-v1"] = "source-anchored-ddmin-v1"
    minimality: Literal["1-minimal-under-declared-removals"] = _MINIMALITY
    source: JournalBinding
    through_line: int = Field(ge=1)
    request: MutationRequest
    witness: ViolationWitness
    deterministic_audit_runs: Literal[3] = _AUDIT_REPETITIONS
    input_trace_sha256: Sha256
    reduced_trace_sha256: Sha256
    removed_event_lines: tuple[int, ...]
    target_payload_before_sha256: Sha256
    target_payload_after_sha256: Sha256


class ReducedCounterexample(FrozenModel):
    provenance: CounterexampleProvenance
    trace: OfflineTrace
    audit: AuditReport
    violation: InvariantViolation
    metrics: ReductionMetrics

    @model_validator(mode="after")
    def validate_result(self) -> ReducedCounterexample:
        provenance = self.provenance
        if self.trace.source != provenance.source:
            raise ValueError("reduced trace source does not match provenance")
        if self.trace.through_line != provenance.through_line:
            raise ValueError("reduced trace cutoff does not match provenance")
        if (
            self.audit.source != self.trace.source
            or self.audit.through_line != self.trace.through_line
        ):
            raise ValueError("reduced audit does not match the reduced trace source")
        if self.violation.invariant is not provenance.witness.invariant:
            raise ValueError("reduced violation invariant does not match the witness")
        if self.violation.terminal_line != provenance.witness.attempt.terminal.line:
            raise ValueError("reduced violation terminal does not match the witness")
        if self.violation.details != provenance.witness.details:
            raise ValueError("reduced violation details do not match the witness")
        if _canonical_sha256(self.trace) != provenance.reduced_trace_sha256:
            raise ValueError("reduced trace digest does not match provenance")
        return self


@dataclass(slots=True)
class _Counters:
    candidates_evaluated: int = 0
    audits_executed: int = 0
    accepted_reductions: int = 0
    malformed: int = 0
    not_applicable: int = 0
    no_violation: int = 0
    cause_changed: int = 0
    nondeterministic: int = 0


@dataclass(slots=True)
class _ReductionSession:
    witness: ViolationWitness
    counters: _Counters

    def accepts(self, candidate: OfflineTrace) -> bool:
        self.counters.candidates_evaluated += 1
        try:
            validated = _validated_trace(candidate, events=candidate.events)
            attempt = _anchored_attempt(validated, self.witness)
        except (TraceStructureError, ValidationError):
            self.counters.malformed += 1
            return False
        except ReductionError:
            self.counters.not_applicable += 1
            return False

        reports: list[AuditReport] = []
        try:
            for _ in range(_AUDIT_REPETITIONS):
                reports.append(audit_completion_trace(validated))
                self.counters.audits_executed += 1
        except (TraceStructureError, ValidationError):
            self.counters.malformed += 1
            return False
        if any(report != reports[0] for report in reports[1:]):
            self.counters.nondeterministic += 1
            return False

        violation = _violation_for_attempt(
            reports[0],
            invariant=self.witness.invariant,
            terminal_line=attempt.terminal_line,
        )
        if violation is None:
            self.counters.no_violation += 1
            return False
        if violation.details != self.witness.details:
            self.counters.cause_changed += 1
            return False
        self.counters.accepted_reductions += 1
        return True


def reduce_counterexample(mutation: AppliedMutation) -> ReducedCounterexample:
    if mutation.attempt.terminal is None:
        raise ReductionError("counterexample reduction requires a verified completion attempt")

    original = OfflineTrace.model_validate(mutation.trace.model_dump(mode="python"))
    counters = _Counters()
    initial_audit = _stable_audit(original, counters)
    witness = _build_witness(mutation, original, initial_audit)
    session = _ReductionSession(witness=witness, counters=counters)
    protected_lines = {
        original.events[0].line,
        witness.attempt.proposal.line,
        witness.attempt.terminal.line,
        witness.target.source.line,
    }

    current = _reduce_event_batches(original, protected_lines, session)
    current = _reduce_events_to_fixed_point(current, protected_lines, session)
    current = _reduce_payload_to_fixed_point(current, witness.target.source, session)
    while True:
        reduced_events = _reduce_events_to_fixed_point(current, protected_lines, session)
        reduced_payload = _reduce_payload_to_fixed_point(
            reduced_events,
            witness.target.source,
            session,
        )
        if reduced_payload == current:
            break
        current = reduced_payload

    final_audit = _stable_audit(current, counters)
    final_attempt = _anchored_attempt(current, witness)
    violation = _violation_for_attempt(
        final_audit,
        invariant=witness.invariant,
        terminal_line=final_attempt.terminal_line,
    )
    if violation is None or violation.details != witness.details:
        raise AssertionError("reducer returned a trace that does not preserve its witness")

    original_target = _event_by_anchor(original.events, witness.target.source)
    reduced_target = _event_by_anchor(current.events, witness.target.source)
    removed_lines = tuple(
        event.line
        for event in original.events
        if not any(retained.line == event.line for retained in current.events)
    )
    return ReducedCounterexample(
        provenance=CounterexampleProvenance(
            source=original.source,
            through_line=original.through_line,
            request=mutation.request,
            witness=witness,
            input_trace_sha256=_canonical_sha256(original),
            reduced_trace_sha256=_canonical_sha256(current),
            removed_event_lines=removed_lines,
            target_payload_before_sha256=_canonical_sha256(original_target.payload),
            target_payload_after_sha256=_canonical_sha256(reduced_target.payload),
        ),
        trace=current,
        audit=final_audit,
        violation=violation,
        metrics=ReductionMetrics(
            before=_trace_size(original),
            after=_trace_size(current),
            candidates_evaluated=counters.candidates_evaluated,
            audits_executed=counters.audits_executed,
            accepted_reductions=counters.accepted_reductions,
            rejections=ReductionRejections(
                malformed=counters.malformed,
                not_applicable=counters.not_applicable,
                no_violation=counters.no_violation,
                cause_changed=counters.cause_changed,
                nondeterministic=counters.nondeterministic,
            ),
        ),
    )


def _build_witness(
    mutation: AppliedMutation,
    trace: OfflineTrace,
    report: AuditReport,
) -> ViolationWitness:
    terminal = mutation.attempt.terminal
    if terminal is None:
        raise ReductionError("counterexample reduction requires a verified completion attempt")
    attempt_anchor = VerifiedAttemptAnchor(
        proposal=mutation.attempt.proposal,
        terminal=terminal,
    )
    provisional = ViolationWitness(
        invariant=mutation.expected_invariant,
        attempt=attempt_anchor,
        target=mutation.target,
        details=("pending",),
    )
    attempt = _anchored_attempt(trace, provisional)
    violation = _violation_for_attempt(
        report,
        invariant=mutation.expected_invariant,
        terminal_line=attempt.terminal_line,
    )
    if violation is None:
        raise ReductionError(
            f"applied mutation does not fail expected invariant {mutation.expected_invariant}"
        )
    return provisional.model_copy(update={"details": violation.details})


def _reduce_event_batches(
    trace: OfflineTrace,
    protected_lines: set[int],
    session: _ReductionSession,
) -> OfflineTrace:
    current = trace
    granularity = 2
    while True:
        removable = tuple(
            event.line for event in current.events if event.line not in protected_lines
        )
        if len(removable) < 2:
            return current
        chunks = _partition(removable, granularity)
        accepted = False
        for chunk in chunks:
            candidate = _without_lines(current, set(chunk))
            if candidate is not None and session.accepts(candidate):
                current = candidate
                granularity = max(2, granularity - 1)
                accepted = True
                break
        if accepted:
            continue
        if granularity >= len(removable):
            return current
        granularity = min(len(removable), granularity * 2)


def _reduce_events_to_fixed_point(
    trace: OfflineTrace,
    protected_lines: set[int],
    session: _ReductionSession,
) -> OfflineTrace:
    current = trace
    while True:
        accepted = False
        for event in current.events:
            if event.line in protected_lines:
                continue
            candidate = _without_lines(current, {event.line})
            if candidate is not None and session.accepts(candidate):
                current = candidate
                accepted = True
                break
        if not accepted:
            return current


def _reduce_payload_to_fixed_point(
    trace: OfflineTrace,
    target: SourceEventAnchor,
    session: _ReductionSession,
) -> OfflineTrace:
    current = trace
    while True:
        event = _event_by_anchor(current.events, target)
        accepted = False
        for path in _payload_removals(event.payload):
            payload = _remove_payload_member(event.payload, path)
            replacement = event.model_copy(update={"payload": payload})
            candidate = _replace_event(current, replacement)
            if session.accepts(candidate):
                current = candidate
                accepted = True
                break
        if not accepted:
            return current


def _partition(items: tuple[int, ...], parts: int) -> tuple[tuple[int, ...], ...]:
    chunk_size = math.ceil(len(items) / parts)
    return tuple(items[index : index + chunk_size] for index in range(0, len(items), chunk_size))


def _without_lines(trace: OfflineTrace, removed: set[int]) -> OfflineTrace | None:
    events = tuple(event for event in trace.events if event.line not in removed)
    try:
        return _validated_trace(trace, events=events)
    except ValidationError:
        return None


def _replace_event(trace: OfflineTrace, replacement: RecordedEvent) -> OfflineTrace:
    events = tuple(
        replacement if event.line == replacement.line else event for event in trace.events
    )
    return _validated_trace(trace, events=events)


def _validated_trace(
    trace: OfflineTrace,
    *,
    events: tuple[RecordedEvent, ...],
) -> OfflineTrace:
    return OfflineTrace.model_validate(
        {
            **trace.model_dump(mode="python", exclude={"events"}),
            "events": tuple(event.model_dump(mode="python") for event in events),
        }
    )


def _payload_removals(
    value: JsonValue,
    path: PayloadPath = (),
) -> tuple[PayloadPath, ...]:
    paths: list[PayloadPath] = []
    if isinstance(value, dict):
        for key in sorted(value):
            paths.append((*path, key))
        for key in sorted(value):
            paths.extend(_payload_removals(value[key], (*path, key)))
    elif isinstance(value, list):
        for index in range(len(value)):
            paths.append((*path, index))
        for index, item in enumerate(value):
            paths.extend(_payload_removals(item, (*path, index)))
    return tuple(paths)


def _remove_payload_member(
    payload: dict[str, JsonValue],
    path: PayloadPath,
) -> dict[str, JsonValue]:
    if not path:
        raise ValueError("payload removal path cannot be empty")
    result = copy.deepcopy(payload)
    parent: JsonValue = result
    for component in path[:-1]:
        if isinstance(parent, dict):
            if not isinstance(component, str):
                raise ValueError(f"payload path does not exist: {path!r}")
            parent = parent[component]
        elif isinstance(parent, list):
            if not isinstance(component, int):
                raise ValueError(f"payload path does not exist: {path!r}")
            parent = parent[component]
        else:
            raise ValueError(f"payload path does not exist: {path!r}")
    member = path[-1]
    if isinstance(parent, dict):
        if not isinstance(member, str):
            raise ValueError(f"payload path does not exist: {path!r}")
        del parent[member]
    elif isinstance(parent, list):
        if not isinstance(member, int):
            raise ValueError(f"payload path does not exist: {path!r}")
        del parent[member]
    else:
        raise ValueError(f"payload path does not exist: {path!r}")
    return result


def _stable_audit(trace: OfflineTrace, counters: _Counters) -> AuditReport:
    reports = []
    for _ in range(_AUDIT_REPETITIONS):
        reports.append(audit_completion_trace(trace))
        counters.audits_executed += 1
    if any(report != reports[0] for report in reports[1:]):
        raise ReductionError("offline audit is not deterministic across three runs")
    return reports[0]


def _anchored_attempt(
    trace: OfflineTrace,
    witness: ViolationWitness,
) -> CompletionAttempt:
    proposal = _event_by_anchor(trace.events, witness.attempt.proposal)
    terminal = _event_by_anchor(trace.events, witness.attempt.terminal)
    target = _event_by_anchor(trace.events, witness.target.source)
    if target.event_type != witness.target.mutated_event_type:
        raise ReductionError("mutation target no longer has the mutated event type")

    attempts = project_completion_attempts(trace.events)
    attempt = next(
        (
            attempt
            for attempt in attempts
            if witness.attempt.proposal.matches(attempt.proposal_event)
        ),
        None,
    )
    if attempt is None or not attempt.verified:
        raise ReductionError("source-anchored completion attempt is no longer verified")
    if attempt.terminal_line != terminal.line:
        raise ReductionError("source-anchored completion terminal changed")
    if not any(event.line == target.line for event in attempt.events):
        raise ReductionError("mutation target left the source-anchored completion attempt")
    if proposal.event_type != "agent_decision" or terminal.event_type != "run_finished":
        raise ReductionError("source attempt anchors changed event type")
    return attempt


def _event_by_anchor(
    events: tuple[RecordedEvent, ...],
    anchor: SourceEventAnchor,
) -> RecordedEvent:
    matches = tuple(event for event in events if anchor.matches(event))
    if len(matches) != 1:
        raise ReductionError(
            f"source event anchor {anchor.line}:{anchor.line_sha256} is not present exactly once"
        )
    return matches[0]


def _violation_for_attempt(
    report: AuditReport,
    *,
    invariant: InvariantId,
    terminal_line: int | None,
) -> InvariantViolation | None:
    if terminal_line is None:
        return None
    result = report.result(invariant)
    if result.status is not InvariantStatus.FAIL:
        return None
    matches = tuple(
        violation for violation in result.violations if violation.terminal_line == terminal_line
    )
    if len(matches) > 1:
        raise ReductionError("offline audit returned duplicate violations for one terminal")
    return matches[0] if matches else None


def _trace_size(trace: OfflineTrace) -> CounterexampleSize:
    return CounterexampleSize(
        event_count=len(trace.events),
        payload_member_count=sum(_payload_member_count(event.payload) for event in trace.events),
        canonical_json_bytes=len(_canonical_bytes(trace)),
    )


def _payload_member_count(value: JsonValue) -> int:
    if isinstance(value, dict):
        return len(value) + sum(_payload_member_count(item) for item in value.values())
    if isinstance(value, list):
        return len(value) + sum(_payload_member_count(item) for item in value)
    return 0


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _canonical_bytes(value: object) -> bytes:
    if isinstance(value, FrozenModel):
        value = value.model_dump(mode="json")
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
