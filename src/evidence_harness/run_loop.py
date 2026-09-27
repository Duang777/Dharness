from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import asdict

from evidence_harness.evidence import EvidenceGate
from evidence_harness.journal import RunJournal
from evidence_harness.model_client import ModelProtocolError, ModelServiceError
from evidence_harness.policy import (
    PolicyViolation,
    find_repeated_cycle,
    repeated_command_block_reason,
    validate_command,
)
from evidence_harness.prompting import build_executor_prompt, build_review_prompt
from evidence_harness.protocol import (
    ActionKind,
    AgentDecision,
    CommandMode,
    CommandReceipt,
    FailureKind,
    LoopOptions,
    ModelGateway,
    ReviewDecision,
    RunPhase,
    RunReport,
    RunState,
    SemanticAssessment,
    ShellCommand,
    ShellEnvironment,
    StopReason,
)
from evidence_harness.shell import CommandRunner

ProgressCallback = Callable[[RunState], None]
MODEL_CALL_SHUTDOWN_RESERVE_SEC = 5.0
FINALIZATION_TURN_RESERVE = 3

_BOOTSTRAP = ShellCommand(
    id="bootstrap-environment",
    purpose="identify the working directory, platform, files, repository state, and common tools",
    mode=CommandMode.OBSERVE,
    timeout_sec=30,
    script=r"""
set +e
printf '%s\n' '=== working directory ==='
pwd
printf '%s\n' '=== identity and platform ==='
id
uname -a
printf '%s\n' '=== directory ==='
ls -la 2>&1 | sed -n '1,120p'
printf '%s\n' '=== shallow entries ==='
find . -mindepth 1 -maxdepth 2 -not -path './.git/*' -printf '%y %p\n' 2>/dev/null |
  sed -n '1,200p'
printf '%s\n' '=== tools ==='
for tool in bash sh python3 python node go javac gcc make cmake git curl jq rg sed awk tar; do
  if command -v "$tool" >/dev/null 2>&1; then
    printf '%s=%s\n' "$tool" "$(command -v "$tool")"
  fi
done
if command -v git >/dev/null 2>&1 && git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  printf '%s\n' '=== git status ==='
  git status --short --branch 2>&1 | sed -n '1,120p'
fi
exit 0
""".strip(),
)


class EvidenceLoop:
    def __init__(
        self,
        *,
        model: ModelGateway,
        journal: RunJournal,
        options: LoopOptions,
        clock: Callable[[], float] = time.monotonic,
        on_progress: ProgressCallback | None = None,
    ) -> None:
        self._model = model
        self._journal = journal
        self._options = options
        self._clock = clock
        self._on_progress = on_progress
        self._gate = EvidenceGate(options)

    async def run(self, instruction: str, environment: ShellEnvironment) -> RunReport:
        started = self._clock()
        state = RunState(
            instruction=instruction,
            phase=RunPhase.BOOTSTRAPPING,
            started_monotonic=started,
            deadline_monotonic=started + self._options.max_wall_time_sec,
        )
        runner = CommandRunner(environment, self._journal, self._options, self._clock)
        self._journal.append(
            "run_started",
            {"instruction": instruction, "options": asdict(self._options)},
        )

        bootstrap = await self._run_command(state, runner, _BOOTSTRAP)
        if bootstrap.failure in {FailureKind.TIMEOUT, FailureKind.TRANSPORT}:
            return self._finish(
                state,
                StopReason.INFRA_FAILURE,
                failure_category="environment",
            )

        while state.stop_reason is None:
            budget_reason = self._budget_exhaustion(state)
            if budget_reason is not None:
                state.unresolved_errors.append(budget_reason)
                return self._finish(
                    state,
                    StopReason.BUDGET_EXHAUSTED,
                    failure_category="harness_control",
                )

            self._enter_finalization_if_needed(state)
            state.phase = RunPhase.FINALIZING if state.finalization_started else RunPhase.THINKING
            self._notify(state)
            allowed_actions = self._allowed_actions(state)
            prompt = build_executor_prompt(
                state,
                self._options,
                self._clock(),
                allowed_actions=allowed_actions,
            )
            model_timeout_sec = self._model_call_timeout_sec(state)
            try:
                async with asyncio.timeout(model_timeout_sec):
                    decision = await self._model.decide(prompt)
            except ModelProtocolError as exc:
                state.turn_count += 1
                state.unresolved_errors.append(str(exc))
                self._journal.append("model_protocol_error", {"error": str(exc)})
                if state.turn_count >= self._options.max_turns:
                    return self._finish(
                        state,
                        StopReason.MODEL_FAILURE,
                        failure_category="model_protocol",
                    )
                continue
            except ModelServiceError as exc:
                state.unresolved_errors.append(str(exc))
                self._journal.append("model_service_error", {"error": str(exc)})
                return self._finish(
                    state,
                    StopReason.MODEL_FAILURE,
                    failure_category="model_service",
                )
            except TimeoutError:
                error = f"model decision exceeded {model_timeout_sec:g}s timeout"
                state.unresolved_errors.append(error)
                self._journal.append("model_service_error", {"error": error})
                return self._finish(
                    state,
                    StopReason.MODEL_FAILURE,
                    failure_category="model_service",
                )
            except Exception as exc:
                state.unresolved_errors.append(f"{type(exc).__name__}: {exc}")
                self._journal.append(
                    "model_error",
                    {"error_type": type(exc).__name__, "error": str(exc)},
                )
                return self._finish(
                    state,
                    StopReason.MODEL_FAILURE,
                    failure_category="model_service",
                )

            state.turn_count += 1
            self._journal.append("agent_decision", decision)
            if decision.action not in allowed_actions:
                feedback = (
                    f"Action '{decision.action}' is not allowed during finalization. "
                    "Submit completion checks or stop."
                )
                state.unresolved_errors.append(feedback)
                self._journal.append(
                    "finalization_action_rejected",
                    {
                        "action": decision.action,
                        "allowed_actions": allowed_actions,
                        "turns_remaining": (self._options.max_turns - state.turn_count),
                    },
                )
                if state.turn_count >= self._options.max_turns:
                    return self._finish(
                        state,
                        StopReason.BUDGET_EXHAUSTED,
                        failure_category="harness_control",
                    )
                continue
            if (
                not state.finalization_started
                and state.must_replan
                and decision.action not in {ActionKind.REPLAN, ActionKind.STOP}
            ):
                self._record_control_feedback(
                    state,
                    "A repeated cycle requires a replan or stop before more shell commands.",
                )
                continue

            if decision.action is ActionKind.EXECUTE:
                if state.finalization_started:
                    state.finalization_repair_used = True
                await self._handle_execute(state, runner, decision)
            elif decision.action is ActionKind.FINISH:
                await self._handle_finish(state, runner, decision)
            elif decision.action is ActionKind.REPLAN:
                self._handle_replan(state, decision)
            else:
                state.final_summary = decision.summary or decision.rationale
                return self._finish(
                    state,
                    StopReason.MODEL_STOPPED,
                    failure_category=f"model_{decision.stop_category}",
                )

            self._notify(state)

        return self._report(state)

    async def _handle_execute(
        self,
        state: RunState,
        runner: CommandRunner,
        decision: AgentDecision,
    ) -> None:
        remaining_calls = self._options.max_environment_calls - state.environment_call_count
        available_for_work = remaining_calls - self._options.verification_environment_reserve
        if len(decision.commands) > available_for_work:
            self._record_control_feedback(
                state,
                "Work command batch would consume the reserved verification calls. "
                "Finish with focused checks or replan to a smaller batch.",
            )
            return
        if self._options.max_turns - state.turn_count < 1:
            self._record_control_feedback(
                state,
                "No executor turn remains after this batch. Submit completion checks now.",
            )
            return

        state.phase = RunPhase.EXECUTING
        state.current_plan = decision.plan
        state.current_goal = decision.commands[0].purpose
        state.work_epoch += 1
        state.latest_evidence = None
        receipts_before = {item.observation_fingerprint for item in state.observations}
        batch_progressed = False

        for command in decision.commands:
            try:
                validate_command(command, self._options.max_command_timeout_sec)
            except PolicyViolation as exc:
                self._record_policy_rejection(state, command.id, str(exc))
                break

            repeat_reason = repeated_command_block_reason(command, state.observations)
            if repeat_reason is not None:
                self._record_policy_rejection(state, command.id, repeat_reason)
                break

            receipt = await self._run_command(state, runner, command)
            if receipt.succeeded and (
                receipt.mode is CommandMode.CHANGE
                or receipt.observation_fingerprint not in receipts_before
            ):
                batch_progressed = True
            if not receipt.succeeded:
                state.unresolved_errors.append(_failure_summary(receipt))
                break

        state.last_batch_progressed = batch_progressed
        state.stagnant_batches = 0 if batch_progressed else state.stagnant_batches + 1
        if not state.finalization_started:
            self._apply_recovery_policy(state)
        if state.phase is not RunPhase.TERMINATED:
            state.phase = RunPhase.FINALIZING if state.finalization_started else RunPhase.THINKING

    async def _handle_finish(
        self,
        state: RunState,
        runner: CommandRunner,
        decision: AgentDecision,
    ) -> None:
        rejection_reasons = self._gate.validate_proposal(decision.checks, decision.coverage)
        if rejection_reasons:
            self._reject_completion(state, rejection_reasons)
            return

        if (
            self._options.enable_completion_review
            and state.completion_review_count >= self._options.max_completion_reviews
        ):
            state.unresolved_errors.append("completion review budget exhausted")
            self._finish(
                state,
                StopReason.BUDGET_EXHAUSTED,
                failure_category="completion_review_budget",
            )
            return

        remaining_calls = self._options.max_environment_calls - state.environment_call_count
        if len(decision.checks) > remaining_calls:
            state.unresolved_errors.append("not enough environment calls remain for verification")
            self._finish(
                state,
                StopReason.BUDGET_EXHAUSTED,
                failure_category="harness_control",
            )
            return

        supporting_observations = list(state.observations)
        state.phase = RunPhase.VERIFYING
        receipts: list[CommandReceipt] = []
        for check in decision.checks:
            command = ShellCommand(
                id=check.id,
                script=check.script,
                purpose=check.proves,
                cwd=check.cwd,
                timeout_sec=check.timeout_sec,
                mode=CommandMode.OBSERVE,
                repeat_reason="fresh completion verification",
            )
            receipt = await self._run_command(state, runner, command)
            receipts.append(receipt)
            if not receipt.succeeded:
                break

        semantic_assessment: SemanticAssessment | None = None
        mechanical_evidence = self._gate.decide(
            work_epoch=state.work_epoch,
            checks=tuple(receipts),
            coverage=decision.coverage,
        )
        if not mechanical_evidence.accepted:
            state.latest_evidence = mechanical_evidence
            self._journal.append("verification_receipt", mechanical_evidence)
            self._reject_completion(state, mechanical_evidence.rejection_reasons)
            return

        if self._options.enable_completion_review:
            state.phase = RunPhase.REVIEWING
            review_prompt = build_review_prompt(
                instruction=state.instruction,
                checks=decision.checks,
                coverage=decision.coverage,
                verification_receipts=tuple(receipts),
                supporting_observations=supporting_observations,
                prior_findings=state.completion_findings,
            )
            state.completion_review_count += 1
            model_timeout_sec = self._model_call_timeout_sec(state)
            try:
                async with asyncio.timeout(model_timeout_sec):
                    review = await self._model.review(review_prompt)
                self._journal.append("completion_review", review)
                semantic_assessment = _semantic_assessment(review)
            except (ModelProtocolError, ModelServiceError, TimeoutError) as exc:
                self._journal.append(
                    "completion_review_error",
                    {"error_type": type(exc).__name__, "error": str(exc)},
                )
                evidence = self._gate.decide(
                    work_epoch=state.work_epoch,
                    checks=tuple(receipts),
                    coverage=decision.coverage,
                    require_semantic_review=True,
                )
                state.latest_evidence = evidence
                self._journal.append("verification_receipt", evidence)
                failure_category = (
                    "completion_review_protocol"
                    if isinstance(exc, ModelProtocolError)
                    else "completion_review_service"
                )
                self._finish(
                    state,
                    StopReason.MODEL_FAILURE,
                    failure_category=failure_category,
                )
                return

        evidence = self._gate.decide(
            work_epoch=state.work_epoch,
            checks=tuple(receipts),
            coverage=decision.coverage,
            semantic_assessment=semantic_assessment,
            require_semantic_review=self._options.enable_completion_review,
        )
        state.latest_evidence = evidence
        self._journal.append("verification_receipt", evidence)
        if evidence.accepted:
            state.completion_findings = ()
            state.final_summary = decision.summary or decision.rationale
            self._finish(state, StopReason.VERIFIED)
            return

        self._reject_completion(state, evidence.rejection_reasons)

    def _handle_replan(self, state: RunState, decision: AgentDecision) -> None:
        if state.must_replan:
            state.recovery_count += 1
        state.current_plan = decision.plan
        state.current_goal = decision.rationale
        state.must_replan = False
        state.recovery_directive = None
        state.stagnant_batches = 0
        self._journal.append(
            "replanned",
            {
                "plan": decision.plan,
                "recovery_count": state.recovery_count,
            },
        )
        if state.recovery_count > self._options.max_recoveries:
            self._finish(
                state,
                StopReason.DOOM_LOOP,
                failure_category="model_reasoning",
            )

    async def _run_command(
        self,
        state: RunState,
        runner: CommandRunner,
        command: ShellCommand,
    ) -> CommandReceipt:
        wall_timeout_sec = max(0.0, state.deadline_monotonic - self._clock())
        receipt = await runner.execute(
            command,
            sequence=state.next_sequence,
            work_epoch=state.work_epoch,
            wall_timeout_sec=wall_timeout_sec,
        )
        state.next_sequence += 1
        state.environment_call_count += 1
        state.observations.append(receipt)
        self._notify(state)
        return receipt

    def _apply_recovery_policy(self, state: RunState) -> None:
        repeated_cycle = find_repeated_cycle(state.observations)
        if repeated_cycle is None and state.stagnant_batches < 3:
            return
        if state.recovery_count >= self._options.max_recoveries:
            self._finish(
                state,
                StopReason.DOOM_LOOP,
                failure_category="model_reasoning",
            )
            return
        state.must_replan = True
        state.recovery_directive = (
            "REPLAN REQUIRED: recent commands repeated without useful state change. "
            "State a new hypothesis and choose a different diagnostic or implementation path."
        )
        self._journal.append(
            "recovery_required",
            {
                "cycle": repeated_cycle,
                "stagnant_batches": state.stagnant_batches,
            },
        )

    def _model_call_timeout_sec(self, state: RunState) -> float:
        remaining = state.deadline_monotonic - self._clock()
        return max(
            0.001,
            min(
                float(self._options.max_model_call_timeout_sec),
                remaining - MODEL_CALL_SHUTDOWN_RESERVE_SEC,
            ),
        )

    def _reject_completion(self, state: RunState, reasons: tuple[str, ...]) -> None:
        state.completion_findings = _stable_unique(reasons)
        review_exhausted = (
            self._options.enable_completion_review
            and state.completion_review_count >= self._options.max_completion_reviews
        )
        repair_granted = not review_exhausted and state.repair_count < self._options.max_repairs
        if repair_granted:
            state.repair_count += 1
            state.phase = RunPhase.REPAIRING
        self._journal.append(
            "completion_rejected",
            {
                "repair_count": state.repair_count,
                "repair_granted": repair_granted,
                "reasons": state.completion_findings,
            },
        )
        if review_exhausted:
            self._finish(
                state,
                StopReason.BUDGET_EXHAUSTED,
                failure_category="completion_review_budget",
            )
        elif not repair_granted:
            self._finish(
                state,
                StopReason.BUDGET_EXHAUSTED,
                failure_category="completion_repair_budget",
            )

    def _enter_finalization_if_needed(self, state: RunState) -> None:
        if state.finalization_started:
            return
        reserve = min(
            FINALIZATION_TURN_RESERVE,
            max(1, self._options.max_turns - 1),
        )
        turns_remaining = self._options.max_turns - state.turn_count
        if turns_remaining > reserve:
            return
        state.finalization_started = True
        state.must_replan = False
        state.recovery_directive = None
        self._journal.append(
            "finalization_started",
            {
                "turns_remaining": turns_remaining,
                "turn_reserve": reserve,
                "completion_findings": state.completion_findings,
            },
        )

    def _allowed_actions(self, state: RunState) -> tuple[ActionKind, ...]:
        if not state.finalization_started:
            return tuple(ActionKind)
        turns_remaining = self._options.max_turns - state.turn_count
        may_repair = (
            bool(state.completion_findings)
            and not state.finalization_repair_used
            and turns_remaining >= 2
        )
        if may_repair:
            return (ActionKind.EXECUTE, ActionKind.FINISH, ActionKind.STOP)
        return (ActionKind.FINISH, ActionKind.STOP)

    def _record_policy_rejection(
        self,
        state: RunState,
        command_id: str,
        reason: str,
    ) -> None:
        self._journal.append(
            "policy_rejection",
            {"command_id": command_id, "reason": reason},
        )
        state.unresolved_errors.append(f"command '{command_id}' rejected: {reason}")

    def _record_control_feedback(self, state: RunState, feedback: str) -> None:
        state.unresolved_errors.append(feedback)
        state.last_batch_progressed = False
        state.stagnant_batches += 1
        if not state.finalization_started:
            self._apply_recovery_policy(state)

    def _budget_exhaustion(self, state: RunState) -> str | None:
        if self._clock() >= state.deadline_monotonic - MODEL_CALL_SHUTDOWN_RESERVE_SEC:
            return "wall-clock budget exhausted"
        if state.turn_count >= self._options.max_turns:
            return "model turn budget exhausted"
        if state.environment_call_count >= self._options.max_environment_calls:
            return "environment call budget exhausted"
        return None

    def _finish(
        self,
        state: RunState,
        reason: StopReason,
        *,
        failure_category: str | None = None,
    ) -> RunReport:
        if state.stop_reason is None:
            state.stop_reason = reason
            state.failure_category = failure_category
            state.phase = RunPhase.TERMINATED
            self._journal.append(
                "run_finished",
                {
                    "stop_reason": reason,
                    "failure_category": failure_category,
                    "turns_used": state.turn_count,
                    "environment_calls_used": state.environment_call_count,
                    "repairs_used": state.repair_count,
                    "recoveries_used": state.recovery_count,
                },
            )
            self._notify(state)
        return self._report(state, failure_category=failure_category)

    def _report(
        self,
        state: RunState,
        *,
        failure_category: str | None = None,
    ) -> RunReport:
        return RunReport(
            stop_reason=state.stop_reason or StopReason.INFRA_FAILURE,
            final_summary=state.final_summary,
            latest_evidence=state.latest_evidence,
            turns_used=state.turn_count,
            environment_calls_used=state.environment_call_count,
            repairs_used=state.repair_count,
            recoveries_used=state.recovery_count,
            failure_category=failure_category or state.failure_category,
            usage=self._model.usage,
        )

    def _notify(self, state: RunState) -> None:
        if self._on_progress is None:
            return
        try:
            self._on_progress(state)
        except Exception as exc:
            self._journal.append(
                "progress_callback_error",
                {"error_type": type(exc).__name__, "error": str(exc)},
            )


def _failure_summary(receipt: CommandReceipt) -> str:
    detail = (
        receipt.stderr.tail or receipt.stderr.head or receipt.stdout.tail or receipt.stdout.head
    )
    return (
        f"command '{receipt.command_id}' failed with {receipt.failure or receipt.return_code}: "
        f"{detail[-1_000:]}"
    )


def _semantic_assessment(review: ReviewDecision) -> SemanticAssessment:
    if review.verdict == "accept":
        return SemanticAssessment(accepted=True, rationale=review.rationale)
    findings = _stable_unique((*review.missing_requirements, *review.suggested_checks))
    if not findings:
        findings = (review.rationale,)
    return SemanticAssessment(
        accepted=False,
        rationale=review.rationale,
        findings=findings,
    )


def _stable_unique(values: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value for value in values if value))
