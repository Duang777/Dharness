from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ActionKind(StrEnum):
    EXECUTE = "execute"
    FINISH = "finish"
    REPLAN = "replan"
    STOP = "stop"


class CommandMode(StrEnum):
    OBSERVE = "observe"
    CHANGE = "change"


class CheckKind(StrEnum):
    ARTIFACT = "artifact"
    BEHAVIOR = "behavior"
    BUILD = "build"
    DATA = "data"
    SERVICE = "service"


class FailureKind(StrEnum):
    NONZERO = "nonzero"
    POLICY = "policy"
    TIMEOUT = "timeout"
    TRANSPORT = "transport"


class StopReason(StrEnum):
    VERIFIED = "verified"
    BUDGET_EXHAUSTED = "budget_exhausted"
    DOOM_LOOP = "doom_loop"
    MODEL_STOPPED = "model_stopped"
    POLICY_BLOCKED = "policy_blocked"
    INFRA_FAILURE = "infrastructure_failure"
    MODEL_FAILURE = "model_failure"


class RunPhase(StrEnum):
    BOOTSTRAPPING = "bootstrapping"
    THINKING = "thinking"
    EXECUTING = "executing"
    REVIEWING = "reviewing"
    VERIFYING = "verifying"
    REPAIRING = "repairing"
    TERMINATED = "terminated"


class ShellCommand(StrictModel):
    id: str = Field(min_length=1, max_length=80)
    script: str = Field(min_length=1, max_length=20_000)
    purpose: str = Field(min_length=1, max_length=500)
    cwd: str | None = Field(default=None, max_length=1_000)
    timeout_sec: int = Field(default=120, ge=1, le=900)
    mode: CommandMode
    repeat_reason: str | None = Field(default=None, max_length=500)


class VerificationCheck(StrictModel):
    id: str = Field(min_length=1, max_length=80)
    kind: CheckKind
    script: str = Field(min_length=1, max_length=20_000)
    proves: str = Field(min_length=1, max_length=1_000)
    cwd: str | None = Field(default=None, max_length=1_000)
    timeout_sec: int = Field(default=120, ge=1, le=900)


class RequirementCoverage(StrictModel):
    requirement: str = Field(min_length=1, max_length=1_000)
    check_ids: tuple[str, ...] = Field(min_length=1, max_length=3)


class AgentDecision(StrictModel):
    action: ActionKind
    rationale: str = Field(min_length=1, max_length=2_000)
    plan: tuple[str, ...] = Field(default_factory=tuple, max_length=8)
    commands: tuple[ShellCommand, ...] = Field(default_factory=tuple, max_length=4)
    checks: tuple[VerificationCheck, ...] = Field(default_factory=tuple, max_length=3)
    coverage: tuple[RequirementCoverage, ...] = Field(default_factory=tuple, max_length=20)
    summary: str | None = Field(default=None, max_length=2_000)
    stop_category: Literal["blocked", "unsafe", "impossible"] | None = None

    @model_validator(mode="after")
    def validate_action_payload(self) -> AgentDecision:
        command_ids = [command.id for command in self.commands]
        check_ids = [check.id for check in self.checks]
        if len(command_ids) != len(set(command_ids)):
            raise ValueError("command ids must be unique")
        if len(check_ids) != len(set(check_ids)):
            raise ValueError("check ids must be unique")

        if self.action is ActionKind.EXECUTE:
            if not self.commands:
                raise ValueError("execute requires at least one command")
            if self.checks or self.coverage or self.stop_category is not None:
                raise ValueError("execute cannot include checks, coverage, or stop_category")
        elif self.action is ActionKind.FINISH:
            if not self.checks:
                raise ValueError("finish requires at least one check")
            if not self.coverage:
                raise ValueError("finish requires a requirement coverage table")
            if self.commands or self.stop_category is not None:
                raise ValueError("finish cannot include commands or stop_category")
            known_checks = set(check_ids)
            unknown = {
                check_id
                for item in self.coverage
                for check_id in item.check_ids
                if check_id not in known_checks
            }
            if unknown:
                raise ValueError(f"coverage references unknown checks: {sorted(unknown)}")
        elif self.action is ActionKind.REPLAN:
            if not self.plan:
                raise ValueError("replan requires a non-empty plan")
            if self.commands or self.checks or self.coverage or self.stop_category is not None:
                raise ValueError(
                    "replan cannot include commands, checks, coverage, or stop_category"
                )
        elif self.action is ActionKind.STOP:
            if self.stop_category is None:
                raise ValueError("stop requires stop_category")
            if self.commands or self.checks or self.coverage:
                raise ValueError("stop cannot include commands, checks, or coverage")
        return self


class ReviewDecision(StrictModel):
    verdict: Literal["accept", "repair"]
    rationale: str = Field(min_length=1, max_length=2_000)
    missing_requirements: tuple[str, ...] = Field(default_factory=tuple, max_length=20)
    suggested_checks: tuple[str, ...] = Field(default_factory=tuple, max_length=5)

    @model_validator(mode="after")
    def validate_verdict(self) -> ReviewDecision:
        if self.verdict == "accept" and self.missing_requirements:
            raise ValueError("accepted coverage cannot have missing requirements")
        return self


class OutputExcerpt(StrictModel):
    head: str
    tail: str
    total_bytes: int = Field(ge=0)
    omitted_bytes: int = Field(ge=0)
    sha256: str
    archive_path: str | None = None


class CommandReceipt(StrictModel):
    sequence: int = Field(ge=1)
    command_id: str
    script: str
    purpose: str
    cwd: str | None
    mode: CommandMode
    work_epoch: int = Field(ge=0)
    return_code: int | None
    failure: FailureKind | None = None
    duration_sec: float = Field(ge=0)
    stdout: OutputExcerpt
    stderr: OutputExcerpt
    command_fingerprint: str
    observation_fingerprint: str

    @property
    def succeeded(self) -> bool:
        return self.failure is None and self.return_code == 0


class VerificationReceipt(StrictModel):
    work_epoch: int = Field(ge=0)
    checks: tuple[CommandReceipt, ...]
    coverage: tuple[RequirementCoverage, ...]
    accepted: bool
    rejection_reasons: tuple[str, ...] = ()


class UsageTotals(StrictModel):
    input_tokens: int = Field(default=0, ge=0)
    cache_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0, ge=0)
    model_calls: int = Field(default=0, ge=0)


class RunReport(StrictModel):
    stop_reason: StopReason
    final_summary: str | None = None
    latest_evidence: VerificationReceipt | None = None
    turns_used: int = Field(ge=0)
    environment_calls_used: int = Field(ge=0)
    repairs_used: int = Field(ge=0)
    recoveries_used: int = Field(ge=0)
    failure_category: str | None = None
    usage: UsageTotals = Field(default_factory=UsageTotals)


@dataclass(frozen=True, slots=True)
class LoopOptions:
    max_turns: int = 40
    max_environment_calls: int = 80
    max_repairs: int = 4
    max_recoveries: int = 2
    max_completion_reviews: int = 4
    max_wall_time_sec: int = 1_800
    max_model_call_timeout_sec: int = 360
    max_command_timeout_sec: int = 300
    verification_environment_reserve: int = 3
    recent_observation_count: int = 8
    output_inline_bytes: int = 12_000
    context_max_chars: int = 80_000
    enable_completion_review: bool = True


@dataclass(slots=True)
class RunState:
    instruction: str
    phase: RunPhase
    started_monotonic: float
    deadline_monotonic: float
    current_plan: tuple[str, ...] = ()
    current_goal: str = ""
    turn_count: int = 0
    environment_call_count: int = 0
    repair_count: int = 0
    recovery_count: int = 0
    completion_review_count: int = 0
    work_epoch: int = 0
    next_sequence: int = 1
    observations: list[CommandReceipt] = field(default_factory=list)
    unresolved_errors: list[str] = field(default_factory=list)
    recovery_directive: str | None = None
    must_replan: bool = False
    latest_evidence: VerificationReceipt | None = None
    final_summary: str | None = None
    stop_reason: StopReason | None = None
    failure_category: str | None = None
    last_batch_progressed: bool = True
    stagnant_batches: int = 0


class EnvironmentResult(Protocol):
    stdout: str | None
    stderr: str | None
    return_code: int


class ShellEnvironment(Protocol):
    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        timeout_sec: int | None = None,
    ) -> EnvironmentResult: ...


class ModelGateway(Protocol):
    @property
    def usage(self) -> UsageTotals: ...

    async def decide(self, prompt: str) -> AgentDecision: ...

    async def review(self, prompt: str) -> ReviewDecision: ...
