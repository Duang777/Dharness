from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Callable
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    TypeAdapter,
    ValidationError,
    model_validator,
)

from evidence_harness.protocol import (
    AgentDecision,
    CheckIsolationEvidence,
    CommandReceipt,
    ReviewDecision,
    VerificationReceipt,
)
from evidence_harness_mutation.attempts import project_completion_attempts
from evidence_harness_mutation.campaign import (
    ApplicableCampaignCase,
    MutationNotApplicableCase,
    OfflineCampaignCase,
    OfflineInvalidCase,
    OfflineViolationCase,
    OracleEquivalentCase,
    OtherOracleChangeCase,
)
from evidence_harness_mutation.invariants import audit_completion_trace
from evidence_harness_mutation.main_analysis_protocol import (
    MainAnalysisMethod,
    MainAnalysisOutcome,
)
from evidence_harness_mutation.model import (
    AuditReport,
    FrozenModel,
    InvariantId,
    InvariantStatus,
    InvariantViolation,
    RecordedEvent,
    Sha256,
    StatePrefix,
    TraceStructureError,
)
from evidence_harness_mutation.operators import MutationId, MutationRequest

_SEED: Literal[20261003] = 20261003
_AUDIT_REPETITIONS: Literal[3] = 3
_WIRE_EVENTS = TypeAdapter(tuple["_WireEvent", ...])
_OPERATOR_INVARIANTS = (
    (MutationId.STALE_EVIDENCE_EPOCH, InvariantId.I1),
    (MutationId.REORDER_CHECK_RECEIPTS, InvariantId.I2),
    (MutationId.REVIEW_TIMEOUT_FALLBACK, InvariantId.I3),
    (MutationId.CROSS_CANDIDATE_EVIDENCE, InvariantId.I4),
)
_TARGET_EVENT_TYPE = {
    MutationId.STALE_EVIDENCE_EPOCH: "verification_receipt",
    MutationId.REORDER_CHECK_RECEIPTS: "verification_receipt",
    MutationId.REVIEW_TIMEOUT_FALLBACK: "completion_review",
    MutationId.CROSS_CANDIDATE_EVIDENCE: "verification_receipt",
}


class _PayloadModel(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _RunStarted(_PayloadModel):
    journal_schema_version: Literal[2]
    instruction: str = Field(min_length=1)


class _IsolationStarted(_PayloadModel):
    attempt_id: int = Field(ge=1)
    work_epoch: int = Field(ge=0)
    check_ids: tuple[str, ...]


class _CandidateCommitted(_PayloadModel):
    attempt_id: int = Field(ge=1)
    candidate_image_id: str = Field(min_length=1)


class _CheckStarted(_PayloadModel):
    attempt_id: int = Field(ge=1)
    check_id: str = Field(min_length=1)
    ordinal: int = Field(ge=1)


class _CheckDisposed(_PayloadModel):
    attempt_id: int = Field(ge=1)
    check_id: str = Field(min_length=1)
    child_id_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class _ReviewError(_PayloadModel):
    error_type: str = Field(min_length=1)
    error: str


class _RunFinished(_PayloadModel):
    stop_reason: str = Field(min_length=1)


_PAYLOAD_MODELS: dict[str, type[BaseModel]] = {
    "run_started": _RunStarted,
    "agent_decision": AgentDecision,
    "completion_isolation_started": _IsolationStarted,
    "completion_candidate_committed": _CandidateCommitted,
    "completion_check_started": _CheckStarted,
    "command_receipt": CommandReceipt,
    "completion_check_isolated": CheckIsolationEvidence,
    "completion_check_disposed": _CheckDisposed,
    "completion_review": ReviewDecision,
    "completion_review_error": _ReviewError,
    "verification_receipt": VerificationReceipt,
    "run_finished": _RunFinished,
}


class _WireEvent(FrozenModel):
    timestamp: str = Field(min_length=1)
    type: str = Field(min_length=1)
    payload: dict[str, JsonValue]

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.model_dump(mode="json"))


class RQ2SlotKey(FrozenModel):
    task_identity_sha256: Sha256
    attempt_ordinal: int = Field(ge=1)
    operator: MutationId
    target_invariant: InvariantId
    slot_ordinal: int = Field(ge=1)
    synthetic_attempt: bool

    @model_validator(mode="after")
    def validate_target(self) -> Self:
        expected = dict(_OPERATOR_INVARIANTS)[self.operator]
        if self.target_invariant is not expected:
            raise ValueError("RQ2 slot target does not match its operator")
        return self


class RQ2OutcomeBase(FrozenModel):
    slot: RQ2SlotKey
    method: MainAnalysisMethod


class InputRejectedOutcome(RQ2OutcomeBase):
    outcome: Literal[MainAnalysisOutcome.INPUT_REJECTED]
    reason: str = Field(min_length=1)


class TargetNotReachedOutcome(RQ2OutcomeBase):
    outcome: Literal[MainAnalysisOutcome.TARGET_NOT_REACHED]
    reason: str = Field(min_length=1)


class OracleInvalidOutcome(RQ2OutcomeBase):
    outcome: Literal[MainAnalysisOutcome.ORACLE_INVALID]
    reason: str = Field(min_length=1)


class OracleEquivalentOutcome(RQ2OutcomeBase):
    outcome: Literal[MainAnalysisOutcome.ORACLE_EQUIVALENT]


class TargetViolationOutcome(RQ2OutcomeBase):
    outcome: Literal[MainAnalysisOutcome.TARGET_VIOLATION]
    violation: InvariantViolation

    @model_validator(mode="after")
    def validate_violation(self) -> Self:
        if self.violation.invariant is not self.slot.target_invariant:
            raise ValueError("RQ2 target violation does not match its slot")
        return self


class OtherOracleChangeOutcome(RQ2OutcomeBase):
    outcome: Literal[MainAnalysisOutcome.OTHER_ORACLE_CHANGE]


RQ2SlotOutcome = Annotated[
    InputRejectedOutcome
    | TargetNotReachedOutcome
    | OracleInvalidOutcome
    | OracleEquivalentOutcome
    | TargetViolationOutcome
    | OtherOracleChangeOutcome,
    Field(discriminator="outcome"),
]


class RQ2MethodResult(FrozenModel):
    method: MainAnalysisMethod
    outcomes: tuple[RQ2SlotOutcome, ...] = Field(min_length=4)

    @model_validator(mode="after")
    def validate_method(self) -> Self:
        if any(outcome.method is not self.method for outcome in self.outcomes):
            raise ValueError("RQ2 method result contains another method")
        return self


class RQ2TaskResult(FrozenModel):
    task_name: str = Field(min_length=1)
    task_identity_sha256: Sha256
    slots: tuple[RQ2SlotKey, ...] = Field(min_length=4)
    methods: tuple[RQ2MethodResult, ...] = Field(min_length=5, max_length=5)

    @model_validator(mode="after")
    def validate_grid(self) -> Self:
        if tuple(method.method for method in self.methods) != tuple(MainAnalysisMethod):
            raise ValueError("RQ2 task methods must use protocol order")
        if tuple(slot.slot_ordinal for slot in self.slots) != tuple(range(1, len(self.slots) + 1)):
            raise ValueError("RQ2 slot ordinals must be contiguous")
        if any(slot.task_identity_sha256 != self.task_identity_sha256 for slot in self.slots):
            raise ValueError("RQ2 slot task identity does not match its task")
        for method in self.methods:
            if tuple(outcome.slot for outcome in method.outcomes) != self.slots:
                raise ValueError("all RQ2 methods must execute the same slot grid")
        return self


class RandomJsonView(FrozenModel):
    canonical_event_bytes: bytes = Field(min_length=1)


class SchemaValidCandidate(FrozenModel):
    event_ordinal: int = Field(ge=1)
    payload_field: str = Field(min_length=1)
    canonical_event_bytes: bytes = Field(min_length=1)


class SchemaValidRandomView(FrozenModel):
    candidates: tuple[SchemaValidCandidate, ...]


class AgentChaosView(FrozenModel):
    canonical_agent_response_bytes: tuple[bytes, ...]


class StatelessEditSite(FrozenModel):
    event_ordinal: int = Field(ge=1)
    event_kind: str = Field(min_length=1)
    canonical_event_bytes: bytes = Field(min_length=1)


class StatelessSemanticView(FrozenModel):
    ordered_event_kinds: tuple[str, ...] = Field(min_length=1)
    allowed_edit_sites: tuple[StatelessEditSite, ...]


class BaselineSelection(FrozenModel):
    candidate_event_bytes: bytes


def build_slot_grid(
    prefix: StatePrefix,
    *,
    task_identity_sha256: str,
) -> tuple[RQ2SlotKey, ...]:
    attempts = project_completion_attempts(prefix.events)
    attempt_ordinals = tuple(attempt.ordinal for attempt in attempts) or (1,)
    synthetic = not attempts
    return tuple(
        RQ2SlotKey(
            task_identity_sha256=task_identity_sha256,
            attempt_ordinal=attempt_ordinal,
            operator=operator,
            target_invariant=invariant,
            slot_ordinal=slot_ordinal,
            synthetic_attempt=synthetic,
        )
        for slot_ordinal, (attempt_ordinal, (operator, invariant)) in enumerate(
            (
                (attempt_ordinal, operator_invariant)
                for attempt_ordinal in attempt_ordinals
                for operator_invariant in _OPERATOR_INVARIANTS
            ),
            start=1,
        )
    )


def select_random_json(
    view: RandomJsonView,
    slot: RQ2SlotKey,
) -> BaselineSelection:
    data = view.canonical_event_bytes
    operation = _draw(MainAnalysisMethod.RANDOM_JSON, slot, 1, 4)
    position = _draw(MainAnalysisMethod.RANDOM_JSON, slot, 2, len(data))
    if operation == 0:
        candidate = data[:position] + data[position + 1 :]
    elif operation == 1:
        candidate = data[:position] + data[position : position + 1] + data[position:]
    elif operation == 2:
        replacement = b"0" if data[position : position + 1] != b"0" else b"1"
        candidate = data[:position] + replacement + data[position + 1 :]
    else:
        candidate = data[:position]
    return BaselineSelection(candidate_event_bytes=candidate)


def select_schema_valid_random(
    view: SchemaValidRandomView,
    slot: RQ2SlotKey,
) -> BaselineSelection | None:
    if not view.candidates:
        return None
    index = _draw(
        MainAnalysisMethod.SCHEMA_VALID_RANDOM,
        slot,
        1,
        len(view.candidates),
    )
    return BaselineSelection(candidate_event_bytes=view.candidates[index].canonical_event_bytes)


def select_agentchaos_style(
    view: AgentChaosView,
    slot: RQ2SlotKey,
) -> BaselineSelection | None:
    if not view.canonical_agent_response_bytes:
        return None
    response_index = _draw(
        MainAnalysisMethod.AGENTCHAOS_STYLE,
        slot,
        1,
        len(view.canonical_agent_response_bytes),
    )
    response = view.canonical_agent_response_bytes[response_index]
    operation = (slot.slot_ordinal - 1) % 4
    if operation == 0:
        candidate = b""
    elif operation == 1:
        candidate = response[: len(response) // 2]
    else:
        try:
            value = json.loads(response)
        except json.JSONDecodeError:
            value = None
        if not isinstance(value, dict):
            candidate = b"null"
        elif operation == 2:
            value.pop("action", None)
            candidate = _canonical_json(value)
        else:
            value["action"] = 7
            candidate = _canonical_json(value)
    return BaselineSelection(candidate_event_bytes=candidate)


def select_stateless_semantic(
    view: StatelessSemanticView,
    slot: RQ2SlotKey,
) -> BaselineSelection | None:
    matching = tuple(
        site
        for site in view.allowed_edit_sites
        if site.event_kind == _TARGET_EVENT_TYPE[slot.operator]
    )
    if not matching:
        return None
    site = matching[_draw(MainAnalysisMethod.STATELESS_SEMANTIC, slot, 1, len(matching))]
    try:
        wire = _WireEvent.model_validate_json(site.canonical_event_bytes)
        payload = copy.deepcopy(wire.payload)
        event_type = wire.type
        if slot.operator is MutationId.STALE_EVIDENCE_EPOCH:
            epoch = payload.get("work_epoch")
            if not isinstance(epoch, int) or isinstance(epoch, bool):
                return None
            payload["work_epoch"] = max(0, epoch - 1)
            checks = payload.get("checks")
            if isinstance(checks, list):
                for check in checks:
                    if isinstance(check, dict):
                        check["work_epoch"] = max(0, epoch - 1)
            isolation = payload.get("isolation")
            if isinstance(isolation, dict):
                isolation["work_epoch"] = max(0, epoch - 1)
        elif slot.operator is MutationId.REORDER_CHECK_RECEIPTS:
            checks = payload.get("checks")
            if not isinstance(checks, list) or len(checks) < 2:
                return None
            payload["checks"] = list(reversed(checks))
        elif slot.operator is MutationId.REVIEW_TIMEOUT_FALLBACK:
            event_type = "completion_review_error"
            payload = {
                "error": "injected completion review timeout",
                "error_type": "TimeoutError",
            }
        else:
            isolation = payload.get("isolation")
            if not isinstance(isolation, dict):
                return None
            candidate_id = isolation.get("candidate_image_id")
            if not isinstance(candidate_id, str):
                return None
            replacement = (
                "sha256:"
                + hashlib.sha256(f"{candidate_id}:{slot.operator.value}".encode()).hexdigest()
            )
            isolation["candidate_image_id"] = replacement
            checks = isolation.get("checks")
            if isinstance(checks, list):
                for check in checks:
                    if isinstance(check, dict):
                        check["started_from_image_id"] = replacement
        candidate_bytes = wire.model_copy(
            update={"type": event_type, "payload": payload}
        ).canonical_bytes()
    except (ValidationError, ValueError, TypeError):
        return None
    return BaselineSelection(candidate_event_bytes=candidate_bytes)


def build_rq2_task(
    *,
    task_name: str,
    task_identity_sha256: str,
    prefix: StatePrefix,
    load_state_aware_cases: Callable[[], tuple[OfflineCampaignCase, ...]],
) -> RQ2TaskResult:
    slots = build_slot_grid(prefix, task_identity_sha256=task_identity_sha256)
    baseline_results = _run_all_baselines(prefix, slots)
    state_aware_cases = load_state_aware_cases()
    state_aware = _state_aware_result(slots, state_aware_cases)
    return RQ2TaskResult(
        task_name=task_name,
        task_identity_sha256=task_identity_sha256,
        slots=slots,
        methods=(state_aware, *baseline_results),
    )


def _run_all_baselines(
    prefix: StatePrefix,
    slots: tuple[RQ2SlotKey, ...],
) -> tuple[RQ2MethodResult, ...]:
    baseline: AuditReport | None
    baseline_error: str | None
    try:
        baseline = _stable_audit(prefix)
        baseline_error = None
    except (TraceStructureError, ValidationError, ValueError) as exc:
        baseline = None
        baseline_error = _stable_error(exc)

    document = _encode_events(prefix.events)
    random_view = RandomJsonView(canonical_event_bytes=document)
    schema_view = SchemaValidRandomView(candidates=_schema_valid_candidates(prefix))
    agent_view = AgentChaosView(
        canonical_agent_response_bytes=tuple(
            _canonical_json(event.payload)
            for event in prefix.events
            if event.event_type == "agent_decision"
        )
    )
    stateless_view = StatelessSemanticView(
        ordered_event_kinds=tuple(event.event_type for event in prefix.events),
        allowed_edit_sites=tuple(
            StatelessEditSite(
                event_ordinal=ordinal,
                event_kind=event.event_type,
                canonical_event_bytes=_wire_event(event).canonical_bytes(),
            )
            for ordinal, event in enumerate(prefix.events, start=1)
            if event.event_type in set(_TARGET_EVENT_TYPE.values())
        ),
    )

    random_selections = tuple(select_random_json(random_view, slot) for slot in slots)
    schema_selections = tuple(select_schema_valid_random(schema_view, slot) for slot in slots)
    agent_selections = tuple(select_agentchaos_style(agent_view, slot) for slot in slots)
    stateless_selections = tuple(select_stateless_semantic(stateless_view, slot) for slot in slots)

    return (
        _classify_method(
            prefix,
            slots,
            MainAnalysisMethod.RANDOM_JSON,
            random_selections,
            baseline,
            baseline_error,
        ),
        _classify_method(
            prefix,
            slots,
            MainAnalysisMethod.SCHEMA_VALID_RANDOM,
            schema_selections,
            baseline,
            baseline_error,
        ),
        _classify_agentchaos(
            prefix,
            slots,
            agent_selections,
            baseline,
            baseline_error,
        ),
        _classify_stateless(
            prefix,
            slots,
            stateless_view,
            stateless_selections,
            baseline,
            baseline_error,
        ),
    )


def _classify_method(
    prefix: StatePrefix,
    slots: tuple[RQ2SlotKey, ...],
    method: MainAnalysisMethod,
    selections: tuple[BaselineSelection | None, ...],
    baseline: AuditReport | None,
    baseline_error: str | None,
) -> RQ2MethodResult:
    outcomes = tuple(
        _classify_document(
            prefix,
            slot,
            method,
            selection.candidate_event_bytes if selection is not None else None,
            baseline,
            baseline_error,
        )
        for slot, selection in zip(slots, selections, strict=True)
    )
    return RQ2MethodResult(method=method, outcomes=outcomes)


def _classify_agentchaos(
    prefix: StatePrefix,
    slots: tuple[RQ2SlotKey, ...],
    selections: tuple[BaselineSelection | None, ...],
    baseline: AuditReport | None,
    baseline_error: str | None,
) -> RQ2MethodResult:
    decision_indices = tuple(
        index for index, event in enumerate(prefix.events) if event.event_type == "agent_decision"
    )
    outcomes: list[RQ2SlotOutcome] = []
    for slot, selection in zip(slots, selections, strict=True):
        if selection is None or not decision_indices:
            outcomes.append(
                TargetNotReachedOutcome(
                    slot=slot,
                    method=MainAnalysisMethod.AGENTCHAOS_STYLE,
                    outcome=MainAnalysisOutcome.TARGET_NOT_REACHED,
                    reason="no agent response is available",
                )
            )
            continue
        response_index = _draw(
            MainAnalysisMethod.AGENTCHAOS_STYLE,
            slot,
            1,
            len(decision_indices),
        )
        try:
            payload = AgentDecision.model_validate_json(selection.candidate_event_bytes).model_dump(
                mode="json"
            )
        except ValidationError as exc:
            outcomes.append(
                InputRejectedOutcome(
                    slot=slot,
                    method=MainAnalysisMethod.AGENTCHAOS_STYLE,
                    outcome=MainAnalysisOutcome.INPUT_REJECTED,
                    reason=_stable_error(exc),
                )
            )
            continue
        events = list(prefix.events)
        event = events[decision_indices[response_index]]
        events[decision_indices[response_index]] = event.model_copy(update={"payload": payload})
        outcomes.append(
            _classify_trace(
                prefix,
                _replace_events(prefix, tuple(events)),
                slot,
                MainAnalysisMethod.AGENTCHAOS_STYLE,
                baseline,
                baseline_error,
            )
        )
    return RQ2MethodResult(
        method=MainAnalysisMethod.AGENTCHAOS_STYLE,
        outcomes=tuple(outcomes),
    )


def _classify_stateless(
    prefix: StatePrefix,
    slots: tuple[RQ2SlotKey, ...],
    view: StatelessSemanticView,
    selections: tuple[BaselineSelection | None, ...],
    baseline: AuditReport | None,
    baseline_error: str | None,
) -> RQ2MethodResult:
    outcomes: list[RQ2SlotOutcome] = []
    for slot, selection in zip(slots, selections, strict=True):
        matching = tuple(
            site
            for site in view.allowed_edit_sites
            if site.event_kind == _TARGET_EVENT_TYPE[slot.operator]
        )
        if selection is None or not matching:
            outcomes.append(
                TargetNotReachedOutcome(
                    slot=slot,
                    method=MainAnalysisMethod.STATELESS_SEMANTIC,
                    outcome=MainAnalysisOutcome.TARGET_NOT_REACHED,
                    reason="no stateless semantic edit site is available",
                )
            )
            continue
        site = matching[
            _draw(
                MainAnalysisMethod.STATELESS_SEMANTIC,
                slot,
                1,
                len(matching),
            )
        ]
        try:
            wire = _WireEvent.model_validate_json(selection.candidate_event_bytes)
            events = list(prefix.events)
            original = events[site.event_ordinal - 1]
            events[site.event_ordinal - 1] = original.model_copy(
                update={"event_type": wire.type, "payload": wire.payload}
            )
            candidate = _replace_events(prefix, tuple(events))
        except (ValidationError, ValueError, IndexError) as exc:
            outcomes.append(
                InputRejectedOutcome(
                    slot=slot,
                    method=MainAnalysisMethod.STATELESS_SEMANTIC,
                    outcome=MainAnalysisOutcome.INPUT_REJECTED,
                    reason=_stable_error(exc),
                )
            )
            continue
        outcomes.append(
            _classify_trace(
                prefix,
                candidate,
                slot,
                MainAnalysisMethod.STATELESS_SEMANTIC,
                baseline,
                baseline_error,
            )
        )
    return RQ2MethodResult(
        method=MainAnalysisMethod.STATELESS_SEMANTIC,
        outcomes=tuple(outcomes),
    )


def _classify_document(
    prefix: StatePrefix,
    slot: RQ2SlotKey,
    method: MainAnalysisMethod,
    candidate_bytes: bytes | None,
    baseline: AuditReport | None,
    baseline_error: str | None,
) -> RQ2SlotOutcome:
    if candidate_bytes is None:
        return TargetNotReachedOutcome(
            slot=slot,
            method=method,
            outcome=MainAnalysisOutcome.TARGET_NOT_REACHED,
            reason="selector has no valid candidate",
        )
    try:
        candidate = _decode_events(prefix, candidate_bytes)
    except (json.JSONDecodeError, ValidationError, ValueError, TypeError) as exc:
        return InputRejectedOutcome(
            slot=slot,
            method=method,
            outcome=MainAnalysisOutcome.INPUT_REJECTED,
            reason=_stable_error(exc),
        )
    return _classify_trace(prefix, candidate, slot, method, baseline, baseline_error)


def _classify_trace(
    original: StatePrefix,
    candidate: StatePrefix,
    slot: RQ2SlotKey,
    method: MainAnalysisMethod,
    baseline: AuditReport | None,
    baseline_error: str | None,
) -> RQ2SlotOutcome:
    if not _target_reached(original, candidate, slot):
        return TargetNotReachedOutcome(
            slot=slot,
            method=method,
            outcome=MainAnalysisOutcome.TARGET_NOT_REACHED,
            reason="mutation did not alter the slot target event",
        )
    if baseline is None:
        return OracleInvalidOutcome(
            slot=slot,
            method=method,
            outcome=MainAnalysisOutcome.ORACLE_INVALID,
            reason=baseline_error or "baseline audit failed",
        )
    try:
        mutated = _stable_audit(candidate)
    except (TraceStructureError, ValidationError, ValueError) as exc:
        return OracleInvalidOutcome(
            slot=slot,
            method=method,
            outcome=MainAnalysisOutcome.ORACLE_INVALID,
            reason=_stable_error(exc),
        )
    if mutated == baseline:
        return OracleEquivalentOutcome(
            slot=slot,
            method=method,
            outcome=MainAnalysisOutcome.ORACLE_EQUIVALENT,
        )
    violation = _new_target_violation(baseline, mutated, slot)
    if violation is not None:
        return TargetViolationOutcome(
            slot=slot,
            method=method,
            outcome=MainAnalysisOutcome.TARGET_VIOLATION,
            violation=violation,
        )
    return OtherOracleChangeOutcome(
        slot=slot,
        method=method,
        outcome=MainAnalysisOutcome.OTHER_ORACLE_CHANGE,
    )


def _state_aware_result(
    slots: tuple[RQ2SlotKey, ...],
    cases: tuple[OfflineCampaignCase, ...],
) -> RQ2MethodResult:
    if len(cases) != len(slots):
        raise ValueError("state-aware campaign does not match the RQ2 slot count")
    outcomes: list[RQ2SlotOutcome] = []
    for slot, case in zip(slots, cases, strict=True):
        expected = MutationRequest(
            operator=slot.operator,
            attempt_ordinal=slot.attempt_ordinal,
        )
        if case.request != expected:
            raise ValueError("state-aware campaign request does not match its RQ2 slot")
        if isinstance(case, MutationNotApplicableCase):
            outcomes.append(
                TargetNotReachedOutcome(
                    slot=slot,
                    method=MainAnalysisMethod.STATE_AWARE,
                    outcome=MainAnalysisOutcome.TARGET_NOT_REACHED,
                    reason=case.reason,
                )
            )
        elif isinstance(case, OfflineInvalidCase):
            outcomes.append(
                OracleInvalidOutcome(
                    slot=slot,
                    method=MainAnalysisMethod.STATE_AWARE,
                    outcome=MainAnalysisOutcome.ORACLE_INVALID,
                    reason=case.error,
                )
            )
        elif isinstance(case, OracleEquivalentCase):
            outcomes.append(
                OracleEquivalentOutcome(
                    slot=slot,
                    method=MainAnalysisMethod.STATE_AWARE,
                    outcome=MainAnalysisOutcome.ORACLE_EQUIVALENT,
                )
            )
        elif isinstance(case, OfflineViolationCase):
            outcomes.append(
                TargetViolationOutcome(
                    slot=slot,
                    method=MainAnalysisMethod.STATE_AWARE,
                    outcome=MainAnalysisOutcome.TARGET_VIOLATION,
                    violation=case.violation,
                )
            )
        elif isinstance(case, OtherOracleChangeCase):
            outcomes.append(
                OtherOracleChangeOutcome(
                    slot=slot,
                    method=MainAnalysisMethod.STATE_AWARE,
                    outcome=MainAnalysisOutcome.OTHER_ORACLE_CHANGE,
                )
            )
        elif isinstance(case, ApplicableCampaignCase):
            raise AssertionError(f"unhandled applicable campaign case: {type(case).__name__}")
        else:
            raise AssertionError(f"unhandled campaign case: {type(case).__name__}")
    return RQ2MethodResult(
        method=MainAnalysisMethod.STATE_AWARE,
        outcomes=tuple(outcomes),
    )


def _schema_valid_candidates(prefix: StatePrefix) -> tuple[SchemaValidCandidate, ...]:
    candidates: list[SchemaValidCandidate] = []
    for event_ordinal, event in enumerate(prefix.events, start=1):
        payload_model = _PAYLOAD_MODELS.get(event.event_type)
        if payload_model is None:
            continue
        for field in sorted(event.payload):
            replacement = _single_field_variant(event.payload[field])
            if isinstance(replacement, _NoVariant):
                continue
            payload = copy.deepcopy(event.payload)
            payload[field] = replacement
            try:
                validated = payload_model.model_validate(payload)
            except ValidationError:
                continue
            replacement_event = event.model_copy(
                update={"payload": validated.model_dump(mode="json")}
            )
            events = tuple(
                replacement_event if index == event_ordinal else current
                for index, current in enumerate(prefix.events, start=1)
            )
            candidates.append(
                SchemaValidCandidate(
                    event_ordinal=event_ordinal,
                    payload_field=field,
                    canonical_event_bytes=_encode_events(events),
                )
            )
    return tuple(candidates)


class _NoVariant:
    pass


_NO_VARIANT = _NoVariant()


def _single_field_variant(value: JsonValue) -> JsonValue | _NoVariant:
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + 1
    if isinstance(value, float):
        return value + 1
    if isinstance(value, str):
        return value + "-mutation"
    if isinstance(value, list) and value:
        return list(reversed(value))
    if isinstance(value, dict) and value:
        result = copy.deepcopy(value)
        del result[sorted(result)[0]]
        return result
    return _NO_VARIANT


def _target_reached(
    original: StatePrefix,
    candidate: StatePrefix,
    slot: RQ2SlotKey,
) -> bool:
    attempts = project_completion_attempts(original.events)
    attempt = next(
        (item for item in attempts if item.ordinal == slot.attempt_ordinal),
        None,
    )
    if attempt is None:
        return False
    expected_type = _TARGET_EVENT_TYPE[slot.operator]
    targets = tuple(event for event in attempt.events if event.event_type == expected_type)
    if len(targets) != 1:
        return False
    target = targets[0]
    changed = next((event for event in candidate.events if event.line == target.line), None)
    return changed is None or (
        changed.event_type != target.event_type or changed.payload != target.payload
    )


def _new_target_violation(
    baseline: AuditReport,
    mutated: AuditReport,
    slot: RQ2SlotKey,
) -> InvariantViolation | None:
    before = baseline.result(slot.target_invariant)
    after = mutated.result(slot.target_invariant)
    if before.status is InvariantStatus.FAIL or after.status is not InvariantStatus.FAIL:
        return None
    matches = tuple(
        violation
        for violation in after.violations
        if violation.attempt_ordinal == slot.attempt_ordinal
    )
    if len(matches) > 1:
        raise ValueError("oracle returned duplicate target violations")
    return matches[0] if matches else None


def _stable_audit(trace: StatePrefix) -> AuditReport:
    reports = tuple(audit_completion_trace(trace) for _ in range(_AUDIT_REPETITIONS))
    encoded = tuple(_canonical_json(report.model_dump(mode="json")) for report in reports)
    if any(item != encoded[0] for item in encoded[1:]):
        raise ValueError("offline audit is not deterministic across three runs")
    return reports[0]


def _encode_events(events: tuple[RecordedEvent, ...]) -> bytes:
    return _canonical_json([_wire_event(event).model_dump(mode="json") for event in events])


def _decode_events(prefix: StatePrefix, data: bytes) -> StatePrefix:
    raw = json.loads(data)
    wires = _WIRE_EVENTS.validate_python(raw)
    events = tuple(
        RecordedEvent(
            line=ordinal,
            line_sha256=(
                prefix.events[ordinal - 1].line_sha256
                if len(wires) == len(prefix.events)
                else hashlib.sha256(wire.canonical_bytes()).hexdigest()
            ),
            timestamp=wire.timestamp,
            event_type=wire.type,
            payload=wire.payload,
        )
        for ordinal, wire in enumerate(wires, start=1)
    )
    return _replace_events(prefix, events)


def _replace_events(
    prefix: StatePrefix,
    events: tuple[RecordedEvent, ...],
) -> StatePrefix:
    return StatePrefix.model_validate(
        {
            **prefix.model_dump(mode="python", exclude={"events", "through_line"}),
            "through_line": len(events),
            "events": tuple(event.model_dump(mode="python") for event in events),
        }
    )


def _wire_event(event: RecordedEvent) -> _WireEvent:
    return _WireEvent(
        timestamp=event.timestamp,
        type=event.event_type,
        payload=event.payload,
    )


def _draw(
    method: MainAnalysisMethod,
    slot: RQ2SlotKey,
    draw_ordinal: int,
    choices: int,
) -> int:
    if choices < 1:
        raise ValueError("deterministic choice requires a nonempty candidate set")
    material = b"\0".join(
        (
            b"thesis-main-analysis-v1",
            str(_SEED).encode("ascii"),
            b"rq2",
            method.value.encode("ascii"),
            slot.task_identity_sha256.encode("ascii"),
            str(slot.attempt_ordinal).encode("ascii"),
            slot.operator.value.encode("ascii"),
            str(slot.slot_ordinal).encode("ascii"),
            str(draw_ordinal).encode("ascii"),
        )
    )
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big") % choices


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


def _stable_error(error: Exception) -> str:
    return f"{type(error).__name__}: {error}"
