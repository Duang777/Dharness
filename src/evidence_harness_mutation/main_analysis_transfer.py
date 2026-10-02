from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, ValidationError, model_validator

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class _SourceModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class WorkspaceAttestation(_FrozenModel):
    ordinal: int = Field(ge=1)
    stage: Literal["initial", "after_action", "terminal"]
    action_ordinal: int | None = Field(default=None, ge=1)
    tree_sha256: Sha256

    @model_validator(mode="after")
    def validate_stage(self) -> Self:
        if self.stage == "after_action" and self.action_ordinal is None:
            raise ValueError("after_action attestation requires an action ordinal")
        if self.stage != "after_action" and self.action_ordinal is not None:
            raise ValueError(f"{self.stage} attestation cannot name an action")
        return self


class _TrajectoryInfo(_SourceModel):
    exit_status: str = Field(min_length=1)
    submission: str


class _MiniSweTrajectory(_SourceModel):
    trajectory_format: Literal["mini-swe-agent-1.1"]
    instance_id: str = Field(min_length=1)
    info: _TrajectoryInfo
    messages: tuple[dict[str, JsonValue], ...] = Field(min_length=1)
    workspace_attestations: tuple[WorkspaceAttestation, ...] = Field(min_length=2)


class TransferAction(_FrozenModel):
    ordinal: int = Field(ge=1)
    decision_ordinal: int = Field(ge=1)
    position_in_decision: int = Field(ge=1)
    identity_sha256: Sha256
    command_sha256: Sha256
    source_message_ordinal: int = Field(ge=1)
    prior_tree_sha256: Sha256


class TransferObservation(_FrozenModel):
    action_ordinal: int = Field(ge=1)
    action_identity_sha256: Sha256
    source_message_ordinal: int = Field(ge=1)
    message_sha256: Sha256
    observed_tree_sha256: Sha256
    return_code: int | None
    exception_type: str | None = None
    terminal_observation: bool = False


class TransferCandidateState(_FrozenModel):
    action_ordinal: int = Field(ge=1)
    action_identity_sha256: Sha256
    tree_sha256: Sha256


class TransferStep(_FrozenModel):
    action: TransferAction
    observation: TransferObservation
    candidate: TransferCandidateState

    @model_validator(mode="after")
    def validate_links(self) -> Self:
        if self.observation.action_ordinal != self.action.ordinal:
            raise ValueError("observation action ordinal does not match its action")
        if self.candidate.action_ordinal != self.action.ordinal:
            raise ValueError("candidate action ordinal does not match its action")
        return self


class TransferDecision(_FrozenModel):
    ordinal: int = Field(ge=1)
    source_message_ordinal: int = Field(ge=1)
    message_sha256: Sha256
    steps: tuple[TransferStep, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_steps(self) -> Self:
        if any(step.action.decision_ordinal != self.ordinal for step in self.steps):
            raise ValueError("decision contains an action from another decision")
        expected = tuple(range(1, len(self.steps) + 1))
        if tuple(step.action.position_in_decision for step in self.steps) != expected:
            raise ValueError("decision action positions must be contiguous")
        return self


class TransferTerminal(_FrozenModel):
    source_message_ordinal: int = Field(ge=1)
    exit_status: str = Field(min_length=1)
    submission_sha256: Sha256
    bound_tree_sha256: Sha256


class TransferTrace(_FrozenModel):
    schema_version: Literal[1] = 1
    instance_id: str = Field(min_length=1)
    source_trajectory_sha256: Sha256
    initial_tree_sha256: Sha256
    decisions: tuple[TransferDecision, ...]
    terminal: TransferTerminal

    @property
    def steps(self) -> tuple[TransferStep, ...]:
        return tuple(step for decision in self.decisions for step in decision.steps)

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.model_dump(mode="json"))

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        if tuple(decision.ordinal for decision in self.decisions) != tuple(
            range(1, len(self.decisions) + 1)
        ):
            raise ValueError("decision ordinals must be contiguous")
        steps = self.steps
        if tuple(step.action.ordinal for step in steps) != tuple(range(1, len(steps) + 1)):
            raise ValueError("action ordinals must be contiguous")
        message_ordinals = tuple(decision.source_message_ordinal for decision in self.decisions)
        if message_ordinals != tuple(sorted(set(message_ordinals))):
            raise ValueError("decision source messages must be unique and ordered")
        if message_ordinals and message_ordinals[-1] >= self.terminal.source_message_ordinal:
            raise ValueError("terminal must follow every decision")
        return self


class TransferFamily(StrEnum):
    I1 = "I1"
    I2 = "I2"
    I3 = "I3"
    I4 = "I4"


class TransferInvariantStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    UNSUPPORTED_BY_DESIGN = "unsupported_by_design"


class TransferViolation(_FrozenModel):
    family: TransferFamily
    action_ordinals: tuple[int, ...]
    details: tuple[str, ...] = Field(min_length=1)


class TransferInvariantResult(_FrozenModel):
    family: TransferFamily
    status: TransferInvariantStatus
    violations: tuple[TransferViolation, ...] = ()

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if self.status is TransferInvariantStatus.FAIL and not self.violations:
            raise ValueError("failed transfer invariant requires a violation")
        if self.status is not TransferInvariantStatus.FAIL and self.violations:
            raise ValueError("only failed transfer invariants may contain violations")
        return self


class TransferAudit(_FrozenModel):
    results: tuple[TransferInvariantResult, ...] = Field(min_length=4, max_length=4)

    def result(self, family: TransferFamily) -> TransferInvariantResult:
        return next(result for result in self.results if result.family is family)

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.model_dump(mode="json"))

    @model_validator(mode="after")
    def validate_families(self) -> Self:
        if tuple(result.family for result in self.results) != tuple(TransferFamily):
            raise ValueError("transfer audit family order has changed")
        return self


class TransferCaseStatus(StrEnum):
    PREEXISTING_VIOLATION = "preexisting_violation"
    NOT_APPLICABLE = "not_applicable"
    TARGET_VIOLATION = "target_violation"
    OTHER_ORACLE_CHANGE = "other_oracle_change"


class TransferCaseResult(_FrozenModel):
    family: TransferFamily
    status: TransferCaseStatus
    source_audit: TransferAudit
    mutated_audit: TransferAudit | None = None
    deterministic_replay: bool = False

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        has_mutant = self.status in {
            TransferCaseStatus.TARGET_VIOLATION,
            TransferCaseStatus.OTHER_ORACLE_CHANGE,
        }
        if has_mutant != (self.mutated_audit is not None):
            raise ValueError("transfer mutation result does not match its status")
        if self.deterministic_replay != has_mutant:
            raise ValueError("executed transfer mutations require deterministic replay")
        return self


def adapt_miniswe_trajectory(data: bytes) -> TransferTrace:
    try:
        source = _MiniSweTrajectory.model_validate_json(data)
    except ValidationError as exc:
        raise ValueError("invalid mini-swe-agent trajectory") from exc

    terminal_ordinal, terminal_message = _terminal_message(source.messages)
    terminal_extra = _object(terminal_message.get("extra"), "terminal extra")
    exit_status = _required_string(terminal_extra, "exit_status")
    submission = _string(terminal_extra.get("submission"), "terminal submission")
    if (exit_status, submission) != (source.info.exit_status, source.info.submission):
        raise ValueError("trajectory info does not match its terminal message")

    actions_by_decision = _source_actions(source.messages, terminal_ordinal)
    action_count = sum(len(actions) for _, _, actions in actions_by_decision)
    attestations = _ordered_attestations(source.workspace_attestations, action_count)
    observations = _source_observations(
        source.messages,
        terminal_ordinal=terminal_ordinal,
        action_count=action_count,
        terminal_message=terminal_message,
    )

    after_by_action = {
        attestation.action_ordinal: attestation.tree_sha256
        for attestation in attestations
        if attestation.stage == "after_action"
    }
    initial = attestations[0].tree_sha256
    decisions: list[TransferDecision] = []
    action_ordinal = 0
    previous_tree = initial
    for decision_ordinal, message_ordinal, source_actions in actions_by_decision:
        message = source.messages[message_ordinal - 1]
        steps: list[TransferStep] = []
        for position, source_action in enumerate(source_actions, start=1):
            action_ordinal += 1
            identity = _action_identity(
                action_ordinal=action_ordinal,
                decision_ordinal=decision_ordinal,
                position_in_decision=position,
                action=source_action,
            )
            observation_ordinal, observation_message, terminal_observation = observations[
                action_ordinal - 1
            ]
            if not terminal_observation:
                _validate_source_association(source_action, observation_message)
            candidate_tree = after_by_action[action_ordinal]
            action = TransferAction(
                ordinal=action_ordinal,
                decision_ordinal=decision_ordinal,
                position_in_decision=position,
                identity_sha256=identity,
                command_sha256=hashlib.sha256(
                    _required_string(source_action, "command").encode()
                ).hexdigest(),
                source_message_ordinal=message_ordinal,
                prior_tree_sha256=previous_tree,
            )
            observation_extra = _optional_object(observation_message.get("extra"))
            steps.append(
                TransferStep(
                    action=action,
                    observation=TransferObservation(
                        action_ordinal=action_ordinal,
                        action_identity_sha256=identity,
                        source_message_ordinal=observation_ordinal,
                        message_sha256=_canonical_sha256(observation_message),
                        observed_tree_sha256=candidate_tree,
                        return_code=_optional_int(observation_extra.get("returncode")),
                        exception_type=_optional_string(observation_extra.get("exception_type")),
                        terminal_observation=terminal_observation,
                    ),
                    candidate=TransferCandidateState(
                        action_ordinal=action_ordinal,
                        action_identity_sha256=identity,
                        tree_sha256=candidate_tree,
                    ),
                )
            )
            previous_tree = candidate_tree
        decisions.append(
            TransferDecision(
                ordinal=decision_ordinal,
                source_message_ordinal=message_ordinal,
                message_sha256=_canonical_sha256(message),
                steps=tuple(steps),
            )
        )

    terminal_attestation = attestations[-1]
    return TransferTrace(
        instance_id=source.instance_id,
        source_trajectory_sha256=hashlib.sha256(data).hexdigest(),
        initial_tree_sha256=initial,
        decisions=tuple(decisions),
        terminal=TransferTerminal(
            source_message_ordinal=terminal_ordinal,
            exit_status=exit_status,
            submission_sha256=hashlib.sha256(submission.encode()).hexdigest(),
            bound_tree_sha256=terminal_attestation.tree_sha256,
        ),
    )


def audit_transfer_trace(trace: TransferTrace) -> TransferAudit:
    return TransferAudit(
        results=(
            _audit_freshness(trace),
            _audit_association(trace),
            TransferInvariantResult(
                family=TransferFamily.I3,
                status=TransferInvariantStatus.UNSUPPORTED_BY_DESIGN,
            ),
            _audit_terminal_binding(trace),
        )
    )


def transfer_family_applicable(trace: TransferTrace, family: TransferFamily) -> bool:
    if family is TransferFamily.I1:
        return any(
            step.action.prior_tree_sha256 != step.candidate.tree_sha256 for step in trace.steps
        )
    if family is TransferFamily.I2:
        return len(trace.steps) >= 2
    if family is TransferFamily.I3:
        return False
    if trace.terminal.exit_status != "Submitted":
        return False
    current = trace.steps[-1].candidate.tree_sha256 if trace.steps else trace.initial_tree_sha256
    prior = (trace.initial_tree_sha256, *(step.candidate.tree_sha256 for step in trace.steps[:-1]))
    return any(tree != current for tree in prior)


def mutate_transfer_family(trace: TransferTrace, family: TransferFamily) -> TransferTrace:
    if not transfer_family_applicable(trace, family):
        raise ValueError(f"transfer family is not applicable: {family}")
    if family is TransferFamily.I1:
        return _mutate_stale_observation(trace)
    if family is TransferFamily.I2:
        return _mutate_swapped_observations(trace)
    if family is TransferFamily.I4:
        return _mutate_terminal_binding(trace)
    raise ValueError("unsupported transfer family cannot be mutated")


def evaluate_transfer_family(
    trace: TransferTrace,
    family: TransferFamily,
) -> TransferCaseResult:
    source_audit = audit_transfer_trace(trace)
    source_result = source_audit.result(family)
    if source_result.status is TransferInvariantStatus.FAIL:
        return TransferCaseResult(
            family=family,
            status=TransferCaseStatus.PREEXISTING_VIOLATION,
            source_audit=source_audit,
        )
    if not transfer_family_applicable(trace, family):
        return TransferCaseResult(
            family=family,
            status=TransferCaseStatus.NOT_APPLICABLE,
            source_audit=source_audit,
        )

    mutated = mutate_transfer_family(trace, family)
    audits = tuple(audit_transfer_trace(mutated) for _ in range(3))
    audit_bytes = tuple(audit.canonical_bytes() for audit in audits)
    if len(set(audit_bytes)) != 1:
        raise AssertionError("transfer audit is not deterministic")
    mutated_audit = audits[0]
    target_failed = mutated_audit.result(family).status is TransferInvariantStatus.FAIL
    return TransferCaseResult(
        family=family,
        status=(
            TransferCaseStatus.TARGET_VIOLATION
            if target_failed
            else TransferCaseStatus.OTHER_ORACLE_CHANGE
        ),
        source_audit=source_audit,
        mutated_audit=mutated_audit,
        deterministic_replay=True,
    )


def _source_actions(
    messages: tuple[dict[str, JsonValue], ...],
    terminal_ordinal: int,
) -> tuple[tuple[int, int, tuple[dict[str, JsonValue], ...]], ...]:
    decisions: list[tuple[int, int, tuple[dict[str, JsonValue], ...]]] = []
    for message_ordinal, message in enumerate(messages[: terminal_ordinal - 1], start=1):
        extra = _optional_object(message.get("extra"))
        raw_actions = extra.get("actions")
        if raw_actions is None:
            continue
        if not isinstance(raw_actions, list) or not raw_actions:
            raise ValueError(f"decision message has invalid actions: {message_ordinal}")
        actions = tuple(_object(action, "trajectory action") for action in raw_actions)
        for action in actions:
            _required_string(action, "command")
            call_id = action.get("tool_call_id")
            if call_id is not None:
                _string(call_id, "action tool_call_id")
        decisions.append((len(decisions) + 1, message_ordinal, actions))
    return tuple(decisions)


def _source_observations(
    messages: tuple[dict[str, JsonValue], ...],
    *,
    terminal_ordinal: int,
    action_count: int,
    terminal_message: dict[str, JsonValue],
) -> tuple[tuple[int, dict[str, JsonValue], bool], ...]:
    observations = tuple(
        (ordinal, message, False)
        for ordinal, message in enumerate(messages[: terminal_ordinal - 1], start=1)
        if _is_observation(message)
    )
    if len(observations) == action_count:
        return observations
    if len(observations) + 1 == action_count:
        return (*observations, (terminal_ordinal, terminal_message, True))
    raise ValueError("trajectory actions do not have one observation each")


def _terminal_message(
    messages: tuple[dict[str, JsonValue], ...],
) -> tuple[int, dict[str, JsonValue]]:
    terminals = tuple(
        (ordinal, message)
        for ordinal, message in enumerate(messages, start=1)
        if _message_role(message) == "exit"
    )
    if len(terminals) != 1:
        raise ValueError("trajectory must contain exactly one terminal message")
    ordinal, message = terminals[0]
    if ordinal != len(messages):
        raise ValueError("terminal message must be last")
    return ordinal, message


def _ordered_attestations(
    attestations: tuple[WorkspaceAttestation, ...],
    action_count: int,
) -> tuple[WorkspaceAttestation, ...]:
    expected_count = action_count + 2
    if len(attestations) != expected_count:
        raise ValueError("workspace attestation count does not match trajectory actions")
    if tuple(item.ordinal for item in attestations) != tuple(range(1, expected_count + 1)):
        raise ValueError("workspace attestations must be unique and ordered")
    expected_stages = ("initial", *("after_action" for _ in range(action_count)), "terminal")
    if tuple(item.stage for item in attestations) != expected_stages:
        raise ValueError("workspace attestations have an invalid stage order")
    action_ordinals = tuple(
        item.action_ordinal for item in attestations if item.stage == "after_action"
    )
    if action_ordinals != tuple(range(1, action_count + 1)):
        raise ValueError("workspace attestations do not match action order")
    return attestations


def _validate_source_association(
    action: dict[str, JsonValue],
    observation: dict[str, JsonValue],
) -> None:
    expected = action.get("tool_call_id")
    actual = observation.get("tool_call_id", observation.get("call_id"))
    if expected is not None and expected != actual:
        raise ValueError("tool-call observation does not match its action")
    if expected is None and actual is not None:
        raise ValueError("observation names a tool call but its action does not")


def _audit_freshness(trace: TransferTrace) -> TransferInvariantResult:
    violations = tuple(
        TransferViolation(
            family=TransferFamily.I1,
            action_ordinals=(step.action.ordinal,),
            details=(
                "workspace_changed",
                f"expected_tree={step.candidate.tree_sha256}",
                f"observed_tree={step.observation.observed_tree_sha256}",
            ),
        )
        for step in trace.steps
        if step.action.prior_tree_sha256 != step.candidate.tree_sha256
        and step.observation.observed_tree_sha256 != step.candidate.tree_sha256
    )
    return _result(TransferFamily.I1, violations)


def _audit_association(trace: TransferTrace) -> TransferInvariantResult:
    violations: list[TransferViolation] = []
    for step in trace.steps:
        mismatches: list[str] = []
        if step.observation.action_identity_sha256 != step.action.identity_sha256:
            mismatches.append("observation_action_identity_mismatch")
        if step.candidate.action_identity_sha256 != step.action.identity_sha256:
            mismatches.append("candidate_action_identity_mismatch")
        if mismatches:
            violations.append(
                TransferViolation(
                    family=TransferFamily.I2,
                    action_ordinals=(step.action.ordinal,),
                    details=tuple(mismatches),
                )
            )
    return _result(TransferFamily.I2, tuple(violations))


def _audit_terminal_binding(trace: TransferTrace) -> TransferInvariantResult:
    current = trace.steps[-1].candidate.tree_sha256 if trace.steps else trace.initial_tree_sha256
    violations: tuple[TransferViolation, ...] = ()
    if trace.terminal.bound_tree_sha256 != current:
        violations = (
            TransferViolation(
                family=TransferFamily.I4,
                action_ordinals=((trace.steps[-1].action.ordinal,) if trace.steps else ()),
                details=(
                    f"current_tree={current}",
                    f"terminal_tree={trace.terminal.bound_tree_sha256}",
                ),
            ),
        )
    return _result(TransferFamily.I4, violations)


def _result(
    family: TransferFamily,
    violations: tuple[TransferViolation, ...],
) -> TransferInvariantResult:
    return TransferInvariantResult(
        family=family,
        status=(TransferInvariantStatus.FAIL if violations else TransferInvariantStatus.PASS),
        violations=violations,
    )


def _mutate_stale_observation(trace: TransferTrace) -> TransferTrace:
    target = next(
        step for step in trace.steps if step.action.prior_tree_sha256 != step.candidate.tree_sha256
    )
    changed = target.model_copy(
        update={
            "observation": target.observation.model_copy(
                update={"observed_tree_sha256": target.action.prior_tree_sha256}
            )
        }
    )
    return _replace_steps(trace, {target.action.ordinal: changed})


def _mutate_swapped_observations(trace: TransferTrace) -> TransferTrace:
    first, second = trace.steps[:2]
    first_changed = first.model_copy(
        update={
            "observation": second.observation.model_copy(
                update={"action_ordinal": first.action.ordinal}
            )
        }
    )
    second_changed = second.model_copy(
        update={
            "observation": first.observation.model_copy(
                update={"action_ordinal": second.action.ordinal}
            )
        }
    )
    return _replace_steps(
        trace,
        {
            first.action.ordinal: first_changed,
            second.action.ordinal: second_changed,
        },
    )


def _mutate_terminal_binding(trace: TransferTrace) -> TransferTrace:
    current = trace.steps[-1].candidate.tree_sha256 if trace.steps else trace.initial_tree_sha256
    prior = (trace.initial_tree_sha256, *(step.candidate.tree_sha256 for step in trace.steps[:-1]))
    replacement = next(tree for tree in prior if tree != current)
    return trace.model_copy(
        update={"terminal": trace.terminal.model_copy(update={"bound_tree_sha256": replacement})}
    )


def _replace_steps(
    trace: TransferTrace,
    replacements: dict[int, TransferStep],
) -> TransferTrace:
    decisions = tuple(
        decision.model_copy(
            update={
                "steps": tuple(
                    replacements.get(step.action.ordinal, step) for step in decision.steps
                )
            }
        )
        for decision in trace.decisions
    )
    return trace.model_copy(update={"decisions": decisions})


def _action_identity(
    *,
    action_ordinal: int,
    decision_ordinal: int,
    position_in_decision: int,
    action: dict[str, JsonValue],
) -> str:
    return _canonical_sha256(
        {
            "action_ordinal": action_ordinal,
            "decision_ordinal": decision_ordinal,
            "position_in_decision": position_in_decision,
            "action": action,
        }
    )


def _is_observation(message: dict[str, JsonValue]) -> bool:
    if _message_role(message) == "exit":
        return False
    extra = _optional_object(message.get("extra"))
    return (
        "returncode" in extra
        or message.get("type") == "function_call_output"
        or message.get("role") == "tool"
    )


def _message_role(message: dict[str, JsonValue]) -> str | None:
    role = message.get("role")
    return role if isinstance(role, str) else None


def _object(value: JsonValue | None, label: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _optional_object(value: JsonValue | None) -> dict[str, JsonValue]:
    return value if isinstance(value, dict) else {}


def _required_string(value: dict[str, JsonValue], field: str) -> str:
    result = value.get(field)
    if not isinstance(result, str) or not result:
        raise ValueError(f"{field} must be a non-empty string")
    return result


def _string(value: JsonValue | None, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    return value


def _optional_string(value: JsonValue | None) -> str | None:
    if value is None:
        return None
    return _string(value, "optional string")


def _optional_int(value: JsonValue | None) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError("optional integer has an invalid value")
    return value


def _canonical_sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
