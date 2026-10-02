from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, JsonValue, ValidationError, model_validator

from evidence_harness_mutation.attempts import CompletionAttempt, project_completion_attempts
from evidence_harness_mutation.invariants import audit_completion_trace
from evidence_harness_mutation.main_analysis_protocol import MainAnalysisReducer
from evidence_harness_mutation.model import (
    AuditReport,
    FrozenModel,
    InvariantId,
    InvariantStatus,
    InvariantViolation,
    OfflineTrace,
    RecordedEvent,
    Sha256,
    SourceEventAnchor,
    TraceStructureError,
)
from evidence_harness_mutation.operators import AppliedMutation
from evidence_harness_mutation.reducer import (
    CounterexampleSize,
    ReductionError,
    ReductionRejections,
    VerifiedAttemptAnchor,
    ViolationWitness,
)

_AUDIT_REPETITIONS: Literal[3] = 3
PayloadPath = tuple[str | int, ...]


class MainAnalysisReductionStatus(StrEnum):
    REDUCED = "reduced"
    UNCHANGED = "unchanged"
    FAILED_RETAINED = "failed_retained"


class ReductionInput(FrozenModel):
    task_identity_sha256: Sha256
    case_ordinal: int = Field(ge=1)
    mutation: AppliedMutation


class MainAnalysisReductionMetrics(FrozenModel):
    before: CounterexampleSize
    after: CounterexampleSize
    candidates_evaluated: int = Field(ge=0)
    audits_executed: int = Field(ge=0)
    accepted_reductions: int = Field(ge=0)
    rejections: ReductionRejections

    @model_validator(mode="after")
    def validate_metrics(self) -> Self:
        rejected = sum(
            getattr(self.rejections, field) for field in ReductionRejections.model_fields
        )
        if self.accepted_reductions + rejected != self.candidates_evaluated:
            raise ValueError("reducer candidate outcomes do not match candidate count")
        if self.after.event_count > self.before.event_count:
            raise ValueError("reducer cannot add events")
        if self.after.payload_member_count > self.before.payload_member_count:
            raise ValueError("reducer cannot add payload members")
        if self.after.canonical_json_bytes > self.before.canonical_json_bytes:
            raise ValueError("reducer cannot add canonical JSON bytes")
        return self


class MainAnalysisReductionResult(FrozenModel):
    reducer: MainAnalysisReducer
    status: MainAnalysisReductionStatus
    input_trace_sha256: Sha256
    output_trace_sha256: Sha256
    trace: OfflineTrace
    audit: AuditReport | None
    violation: InvariantViolation | None
    witness: ViolationWitness | None
    metrics: MainAnalysisReductionMetrics
    failure_reason: str | None

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        failed = self.status is MainAnalysisReductionStatus.FAILED_RETAINED
        if failed:
            if not self.failure_reason:
                raise ValueError("failed reducer must record a failure reason")
            if self.input_trace_sha256 != self.output_trace_sha256:
                raise ValueError("failed reducer must retain the full input trace")
            if self.metrics.after != self.metrics.before:
                raise ValueError("failed reducer must retain the full input size")
        else:
            if self.failure_reason is not None:
                raise ValueError("successful reducer cannot record a failure reason")
            if self.audit is None or self.violation is None or self.witness is None:
                raise ValueError("successful reducer requires witness evidence")
        return self


class MainAnalysisReductionComparison(FrozenModel):
    task_identity_sha256: Sha256
    case_ordinal: int = Field(ge=1)
    input_trace_sha256: Sha256
    results: tuple[MainAnalysisReductionResult, ...] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def validate_results(self) -> Self:
        if tuple(result.reducer for result in self.results) != tuple(MainAnalysisReducer):
            raise ValueError("reducer comparison must use protocol order")
        if any(result.input_trace_sha256 != self.input_trace_sha256 for result in self.results):
            raise ValueError("all reducers must use the same input trace")
        before = self.results[0].metrics.before
        if any(result.metrics.before != before for result in self.results):
            raise ValueError("all reducers must use the same input size")
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

    def rejections(self) -> ReductionRejections:
        return ReductionRejections(
            malformed=self.malformed,
            not_applicable=self.not_applicable,
            no_violation=self.no_violation,
            cause_changed=self.cause_changed,
            nondeterministic=self.nondeterministic,
        )


@dataclass(slots=True)
class _ReductionKernel:
    witness: ViolationWitness
    counters: _Counters

    def stable_audit(self, trace: OfflineTrace) -> AuditReport:
        reports: list[AuditReport] = []
        for _ in range(_AUDIT_REPETITIONS):
            reports.append(audit_completion_trace(trace))
            self.counters.audits_executed += 1
        encoded = tuple(_canonical_bytes(report) for report in reports)
        if any(item != encoded[0] for item in encoded[1:]):
            raise ReductionError("offline audit is not deterministic across three runs")
        return reports[0]

    def accepts(self, candidate: OfflineTrace) -> bool:
        self.counters.candidates_evaluated += 1
        try:
            validated = _validated_trace(candidate, candidate.events)
            attempt = _anchored_attempt(validated, self.witness)
        except (TraceStructureError, ValidationError):
            self.counters.malformed += 1
            return False
        except ReductionError:
            self.counters.not_applicable += 1
            return False

        try:
            report = self.stable_audit(validated)
        except (TraceStructureError, ValidationError):
            self.counters.malformed += 1
            return False
        except ReductionError:
            self.counters.nondeterministic += 1
            return False

        violation = _violation_for_attempt(
            report,
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


class _AtomKind(StrEnum):
    EVENT = "event"
    PAYLOAD = "payload"


@dataclass(frozen=True, slots=True)
class _Atom:
    kind: _AtomKind
    source_line: int
    payload_path: PayloadPath = ()


def compare_reducers(
    reduction_input: ReductionInput,
) -> MainAnalysisReductionComparison:
    input_sha256 = _canonical_sha256(reduction_input.mutation.trace)
    results = tuple(run_reducer(reduction_input, reducer) for reducer in MainAnalysisReducer)
    return MainAnalysisReductionComparison(
        task_identity_sha256=reduction_input.task_identity_sha256,
        case_ordinal=reduction_input.case_ordinal,
        input_trace_sha256=input_sha256,
        results=results,
    )


def run_reducer(
    reduction_input: ReductionInput,
    reducer: MainAnalysisReducer,
) -> MainAnalysisReductionResult:
    original = OfflineTrace.model_validate(reduction_input.mutation.trace.model_dump(mode="python"))
    before = trace_size(original)
    input_sha256 = _canonical_sha256(original)
    counters = _Counters()
    audit: AuditReport | None = None
    witness: ViolationWitness | None = None
    try:
        provisional = _provisional_witness(reduction_input.mutation)
        kernel = _ReductionKernel(witness=provisional, counters=counters)
        audit = kernel.stable_audit(original)
        witness = _complete_witness(provisional, original, audit)
        kernel.witness = witness
        current = _apply_reducer(reducer, original, kernel)
        final_audit = (
            audit if reducer is MainAnalysisReducer.NO_REDUCTION else kernel.stable_audit(current)
        )
        attempt = _anchored_attempt(current, witness)
        violation = _violation_for_attempt(
            final_audit,
            invariant=witness.invariant,
            terminal_line=attempt.terminal_line,
        )
        if violation is None or violation.details != witness.details:
            raise ReductionError("reducer did not preserve the declared witness")
    except Exception as exc:
        return MainAnalysisReductionResult(
            reducer=reducer,
            status=MainAnalysisReductionStatus.FAILED_RETAINED,
            input_trace_sha256=input_sha256,
            output_trace_sha256=input_sha256,
            trace=original,
            audit=audit,
            violation=None,
            witness=witness,
            metrics=_metrics(before, before, counters),
            failure_reason=f"{type(exc).__name__}: {exc}",
        )

    after = trace_size(current)
    status = (
        MainAnalysisReductionStatus.REDUCED
        if after != before
        else MainAnalysisReductionStatus.UNCHANGED
    )
    return MainAnalysisReductionResult(
        reducer=reducer,
        status=status,
        input_trace_sha256=input_sha256,
        output_trace_sha256=_canonical_sha256(current),
        trace=current,
        audit=final_audit,
        violation=violation,
        witness=witness,
        metrics=_metrics(before, after, counters),
        failure_reason=None,
    )


def trace_size(trace: OfflineTrace) -> CounterexampleSize:
    return CounterexampleSize(
        event_count=len(trace.events),
        payload_member_count=sum(_payload_member_count(event.payload) for event in trace.events),
        canonical_json_bytes=len(_canonical_bytes(trace)),
    )


def _apply_reducer(
    reducer: MainAnalysisReducer,
    original: OfflineTrace,
    kernel: _ReductionKernel,
) -> OfflineTrace:
    if reducer is MainAnalysisReducer.NO_REDUCTION:
        return original
    if reducer is MainAnalysisReducer.FLAT_DDMIN:
        return _flat_ddmin(original, kernel)
    if reducer is MainAnalysisReducer.HDD:
        return _hdd(original, kernel)
    if reducer is MainAnalysisReducer.SOURCE_ANCHORED:
        return _source_anchored(original, kernel)
    raise AssertionError(f"unsupported reducer: {reducer}")


def _flat_ddmin(
    trace: OfflineTrace,
    kernel: _ReductionKernel,
) -> OfflineTrace:
    protected = _protected_lines(trace, kernel.witness)
    target = _event_by_anchor(trace.events, kernel.witness.target.source)
    atoms = tuple(
        [
            *(
                _Atom(kind=_AtomKind.EVENT, source_line=event.line)
                for event in trace.events
                if event.line not in protected
            ),
            *(
                _Atom(
                    kind=_AtomKind.PAYLOAD,
                    source_line=target.line,
                    payload_path=path,
                )
                for path in _payload_removals(target.payload)
            ),
        ]
    )
    removed = _ddmin_removals(trace, atoms, kernel)
    return _apply_atom_removals(trace, removed)


def _hdd(
    trace: OfflineTrace,
    kernel: _ReductionKernel,
) -> OfflineTrace:
    protected = _protected_lines(trace, kernel.witness)
    event_atoms = tuple(
        _Atom(kind=_AtomKind.EVENT, source_line=event.line)
        for event in trace.events
        if event.line not in protected
    )
    removed_events = _ddmin_removals(trace, event_atoms, kernel)
    current = _apply_atom_removals(trace, removed_events)

    target = _event_by_anchor(current.events, kernel.witness.target.source)
    paths = _payload_removals(target.payload)
    for depth in sorted({len(path) for path in paths}):
        atoms = tuple(
            _Atom(
                kind=_AtomKind.PAYLOAD,
                source_line=target.line,
                payload_path=path,
            )
            for path in paths
            if len(path) == depth
        )
        removed = _ddmin_removals(current, atoms, kernel)
        current = _apply_atom_removals(current, removed)
        target = _event_by_anchor(current.events, kernel.witness.target.source)
        paths = _payload_removals(target.payload)
    return current


def _source_anchored(
    trace: OfflineTrace,
    kernel: _ReductionKernel,
) -> OfflineTrace:
    protected = _protected_lines(trace, kernel.witness)
    current = _reduce_event_batches(trace, protected, kernel)
    current = _reduce_events_to_fixed_point(current, protected, kernel)
    current = _reduce_payload_to_fixed_point(
        current,
        kernel.witness.target.source,
        kernel,
    )
    while True:
        reduced_events = _reduce_events_to_fixed_point(current, protected, kernel)
        reduced_payload = _reduce_payload_to_fixed_point(
            reduced_events,
            kernel.witness.target.source,
            kernel,
        )
        if reduced_payload == current:
            return current
        current = reduced_payload


def _ddmin_removals(
    trace: OfflineTrace,
    atoms: tuple[_Atom, ...],
    kernel: _ReductionKernel,
) -> frozenset[_Atom]:
    remaining = atoms
    removed: frozenset[_Atom] = frozenset()
    granularity = 2
    while remaining:
        chunks = _partition(remaining, granularity)
        accepted = False
        for chunk in chunks:
            candidate_removed = removed | frozenset(chunk)
            if _accept_atom_removals(trace, candidate_removed, kernel):
                removed = candidate_removed
                remaining = tuple(atom for atom in remaining if atom not in chunk)
                granularity = max(2, granularity - 1)
                accepted = True
                break
        if accepted:
            continue
        if granularity >= len(remaining):
            break
        granularity = min(len(remaining), granularity * 2)
    for atom in remaining:
        candidate_removed = removed | frozenset({atom})
        if _accept_atom_removals(trace, candidate_removed, kernel):
            removed = candidate_removed
    return removed


def _accept_atom_removals(
    trace: OfflineTrace,
    removed: frozenset[_Atom],
    kernel: _ReductionKernel,
) -> bool:
    try:
        candidate = _apply_atom_removals(trace, removed)
    except (ValidationError, ValueError):
        kernel.counters.candidates_evaluated += 1
        kernel.counters.malformed += 1
        return False
    return kernel.accepts(candidate)


def _apply_atom_removals(
    trace: OfflineTrace,
    removed: frozenset[_Atom],
) -> OfflineTrace:
    removed_lines = {atom.source_line for atom in removed if atom.kind is _AtomKind.EVENT}
    events = tuple(event for event in trace.events if event.line not in removed_lines)
    payload_by_line: dict[int, set[PayloadPath]] = {}
    for atom in removed:
        if atom.kind is _AtomKind.PAYLOAD:
            payload_by_line.setdefault(atom.source_line, set()).add(atom.payload_path)
    replaced = tuple(
        event.model_copy(update={"payload": _remove_payload_paths(event.payload, paths)})
        if (paths := payload_by_line.get(event.line))
        else event
        for event in events
    )
    return _validated_trace(trace, replaced)


class _Removed:
    pass


_REMOVED = _Removed()


def _remove_payload_paths(
    payload: dict[str, JsonValue],
    removed: set[PayloadPath],
) -> dict[str, JsonValue]:
    value = _prune_value(payload, (), removed)
    if not isinstance(value, dict):
        raise ValueError("payload root cannot be removed")
    return value


def _prune_value(
    value: JsonValue,
    path: PayloadPath,
    removed: set[PayloadPath],
) -> JsonValue | _Removed:
    if path in removed:
        return _REMOVED
    if isinstance(value, dict):
        result: dict[str, JsonValue] = {}
        for key in sorted(value):
            child = _prune_value(value[key], (*path, key), removed)
            if not isinstance(child, _Removed):
                result[key] = child
        return result
    if isinstance(value, list):
        result_list: list[JsonValue] = []
        for index, item in enumerate(value):
            child = _prune_value(item, (*path, index), removed)
            if not isinstance(child, _Removed):
                result_list.append(child)
        return result_list
    return copy.deepcopy(value)


def _reduce_event_batches(
    trace: OfflineTrace,
    protected_lines: set[int],
    kernel: _ReductionKernel,
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
            if candidate is not None and kernel.accepts(candidate):
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
    kernel: _ReductionKernel,
) -> OfflineTrace:
    current = trace
    while True:
        accepted = False
        for event in current.events:
            if event.line in protected_lines:
                continue
            candidate = _without_lines(current, {event.line})
            if candidate is not None and kernel.accepts(candidate):
                current = candidate
                accepted = True
                break
        if not accepted:
            return current


def _reduce_payload_to_fixed_point(
    trace: OfflineTrace,
    target: SourceEventAnchor,
    kernel: _ReductionKernel,
) -> OfflineTrace:
    current = trace
    while True:
        event = _event_by_anchor(current.events, target)
        accepted = False
        for path in _payload_removals(event.payload):
            payload = _remove_payload_member(event.payload, path)
            replacement = event.model_copy(update={"payload": payload})
            candidate = _replace_event(current, replacement)
            if kernel.accepts(candidate):
                current = candidate
                accepted = True
                break
        if not accepted:
            return current


def _provisional_witness(mutation: AppliedMutation) -> ViolationWitness:
    terminal = mutation.attempt.terminal
    if terminal is None:
        raise ReductionError("counterexample reduction requires a verified completion attempt")
    return ViolationWitness(
        invariant=mutation.expected_invariant,
        attempt=VerifiedAttemptAnchor(
            proposal=mutation.attempt.proposal,
            terminal=terminal,
        ),
        target=mutation.target,
        details=("pending",),
    )


def _complete_witness(
    provisional: ViolationWitness,
    trace: OfflineTrace,
    report: AuditReport,
) -> ViolationWitness:
    attempt = _anchored_attempt(trace, provisional)
    violation = _violation_for_attempt(
        report,
        invariant=provisional.invariant,
        terminal_line=attempt.terminal_line,
    )
    if violation is None:
        raise ReductionError(
            f"applied mutation does not fail expected invariant {provisional.invariant}"
        )
    return provisional.model_copy(update={"details": violation.details})


def _protected_lines(
    trace: OfflineTrace,
    witness: ViolationWitness,
) -> set[int]:
    return {
        trace.events[0].line,
        witness.attempt.proposal.line,
        witness.attempt.terminal.line,
        witness.target.source.line,
    }


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
        parent = _payload_child(parent, component, path)
    _delete_payload_member(parent, path[-1], path)
    return result


def _payload_child(
    parent: JsonValue,
    component: str | int,
    path: PayloadPath,
) -> JsonValue:
    if isinstance(parent, dict):
        if not isinstance(component, str):
            raise ValueError(f"payload path does not exist: {path!r}")
        return parent[component]
    if isinstance(parent, list):
        if not isinstance(component, int):
            raise ValueError(f"payload path does not exist: {path!r}")
        return parent[component]
    raise ValueError(f"payload path does not exist: {path!r}")


def _delete_payload_member(
    parent: JsonValue,
    member: str | int,
    path: PayloadPath,
) -> None:
    if isinstance(parent, dict):
        if not isinstance(member, str):
            raise ValueError(f"payload path does not exist: {path!r}")
        del parent[member]
        return
    if isinstance(parent, list):
        if not isinstance(member, int):
            raise ValueError(f"payload path does not exist: {path!r}")
        del parent[member]
        return
    raise ValueError(f"payload path does not exist: {path!r}")


def _partition[T](items: tuple[T, ...], parts: int) -> tuple[tuple[T, ...], ...]:
    chunk_size = math.ceil(len(items) / parts)
    return tuple(items[index : index + chunk_size] for index in range(0, len(items), chunk_size))


def _without_lines(
    trace: OfflineTrace,
    removed: set[int],
) -> OfflineTrace | None:
    events = tuple(event for event in trace.events if event.line not in removed)
    try:
        return _validated_trace(trace, events)
    except ValidationError:
        return None


def _replace_event(
    trace: OfflineTrace,
    replacement: RecordedEvent,
) -> OfflineTrace:
    events = tuple(
        replacement if event.line == replacement.line else event for event in trace.events
    )
    return _validated_trace(trace, events)


def _validated_trace(
    trace: OfflineTrace,
    events: tuple[RecordedEvent, ...],
) -> OfflineTrace:
    return OfflineTrace.model_validate(
        {
            **trace.model_dump(mode="python", exclude={"events"}),
            "events": tuple(event.model_dump(mode="python") for event in events),
        }
    )


def _metrics(
    before: CounterexampleSize,
    after: CounterexampleSize,
    counters: _Counters,
) -> MainAnalysisReductionMetrics:
    return MainAnalysisReductionMetrics(
        before=before,
        after=after,
        candidates_evaluated=counters.candidates_evaluated,
        audits_executed=counters.audits_executed,
        accepted_reductions=counters.accepted_reductions,
        rejections=counters.rejections(),
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
