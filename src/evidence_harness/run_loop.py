from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import asdict

from evidence_harness.budget import (
    finalization_turn_reserve,
    finalization_wall_time_reserve_sec,
)
from evidence_harness.completion_contract import CompletionContract
from evidence_harness.completion_control import CompletionController
from evidence_harness.completion_isolation import (
    CompletionIsolation,
    CompletionIsolationError,
    CompletionIsolationRequest,
)
from evidence_harness.journal import RunJournal
from evidence_harness.model_client import ModelProtocolError, ModelServiceError
from evidence_harness.policy import (
    PolicyViolation,
    find_repeated_cycle,
    repeated_command_block_reason,
    validate_command,
)
from evidence_harness.process_containment import contain_harbor_environment
from evidence_harness.prompting import build_executor_prompt, build_review_prompt
from evidence_harness.protocol import (
    ActionKind,
    AgentDecision,
    CollectionAttestation,
    CommandMode,
    CommandReceipt,
    CompletionReviewStarted,
    ExecutorTurnStarted,
    FailureKind,
    FinalizationStarted,
    FinalizationTrigger,
    LoopOptions,
    ModelGateway,
    RecoveryRequired,
    ReviewDecision,
    RunPhase,
    RunReport,
    RunState,
    SemanticAssessment,
    ShellCommand,
    ShellEnvironment,
    StopReason,
    VerificationCheck,
    WorkBatchStarted,
)
from evidence_harness.shell import CommandRunner

ProgressCallback = Callable[[RunState], None]
MODEL_CALL_SHUTDOWN_RESERVE_SEC = 5.0

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
        completion_isolation: CompletionIsolation,
        completion_contract: CompletionContract | None = None,
        collection: CollectionAttestation | None = None,
        clock: Callable[[], float] = time.monotonic,
        on_progress: ProgressCallback | None = None,
    ) -> None:
        self._model = model
        self._journal = journal
        self._options = options
        self._completion_isolation = completion_isolation
        self._configured_completion_contract = completion_contract
        self._collection = collection
        self._clock = clock
        self._on_progress = on_progress

    async def run(self, instruction: str, environment: ShellEnvironment) -> RunReport:
        contract = self._configured_completion_contract or CompletionContract.from_instruction(
            instruction,
            self._options,
        )
        controller = CompletionController(contract, options=self._options)
        started = self._clock()
        state = RunState(
            instruction=instruction,
            phase=RunPhase.BOOTSTRAPPING,
            started_monotonic=started,
            deadline_monotonic=started + self._options.max_wall_time_sec,
        )
        runner = CommandRunner(environment, self._journal, self._options, self._clock)
        started_payload = {
            "journal_schema_version": 2,
            "control_audit_version": 1,
            "instruction": instruction,
            "options": (
                self._collection.options if self._collection is not None else asdict(self._options)
            ),
            "completion_contract": contract.model_dump(mode="json"),
        }
        if self._collection is not None:
            started_payload.update(
                {
                    "prefixbench_profile": self._collection.prefixbench_profile,
                    "producer": self._collection.producer.model_dump(mode="json"),
                }
            )
        self._journal.append("run_started", started_payload)

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
            if state.phase is RunPhase.THINKING:
                attempt_id = state.next_executor_attempt
                state.next_executor_attempt += 1
                self._journal.append(
                    "executor_turn_started",
                    ExecutorTurnStarted(
                        attempt_id=attempt_id,
                        turns_completed=state.turn_count,
                        work_epoch=state.work_epoch,
                        recovery_required=state.must_replan,
                    ),
                )
            self._notify(state)
            allowed_actions = self._allowed_actions(state)
            prompt = build_executor_prompt(
                state,
                self._options,
                self._clock(),
                contract=contract,
                allowed_actions=allowed_actions,
            )
            finalization_started_before_decision = state.finalization_started
            model_timeout_sec = self._model_call_timeout_sec(
                state,
                preserve_finalization_reserve=True,
            )
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
                self._enter_finalization_if_needed(state, include_turn_trigger=False)
                if state.finalization_started:
                    self._journal.append(
                        "model_decision_interrupted_for_finalization",
                        {
                            "timeout_sec": model_timeout_sec,
                            "triggers": state.finalization_triggers,
                        },
                    )
                    continue
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
            self._enter_finalization_if_needed(state, include_turn_trigger=False)
            if state.finalization_started and not finalization_started_before_decision:
                allowed_actions = self._allowed_actions(state)
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
                        "wall_time_remaining_sec": max(
                            0.0,
                            state.deadline_monotonic - self._clock(),
                        ),
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
                await self._handle_execute(state, runner, decision, controller)
            elif decision.action is ActionKind.FINISH:
                await self._handle_finish(state, decision, controller)
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
        controller: CompletionController,
    ) -> None:
        budget = controller.contract.b_req
        remaining_calls = budget.max_environment_calls - state.environment_call_count
        available_for_work = remaining_calls - budget.verification_environment_reserve
        admission = controller.budget_guard.evaluate_work(
            controller.contract,
            state,
            command_count=len(decision.commands),
        )
        self._journal.append(
            "work_batch_admission",
            {
                "accepted": admission.accepted,
                "reasons": admission.reasons,
                "command_ids": tuple(command.id for command in decision.commands),
                "requested_environment_calls": len(decision.commands),
                "environment_calls_used": state.environment_call_count,
                "environment_calls_remaining": remaining_calls,
                "verification_environment_reserve": budget.verification_environment_reserve,
                "available_for_work": available_for_work,
                "turns_used": state.turn_count,
                "turns_remaining": budget.max_turns - state.turn_count,
                "started_in_finalization": state.finalization_started,
            },
        )
        if not admission.accepted:
            feedback = ". ".join(reason.capitalize() for reason in admission.reasons)
            self._record_control_feedback(state, f"{feedback}.")
            return

        state.phase = RunPhase.EXECUTING
        state.current_plan = decision.plan
        state.current_goal = decision.commands[0].purpose
        state.work_epoch += 1
        state.latest_evidence = None
        self._journal.append(
            "work_batch_started",
            WorkBatchStarted(
                work_epoch=state.work_epoch,
                command_ids=tuple(command.id for command in decision.commands),
                started_in_finalization=state.finalization_started,
            ),
        )
        receipts_before = {item.observation_fingerprint for item in state.observations}
        batch_receipts: list[CommandReceipt] = []
        batch_progressed = False
        started_in_finalization = state.finalization_started
        command_deadline_monotonic = (
            state.deadline_monotonic
            if started_in_finalization
            else state.deadline_monotonic
            - finalization_wall_time_reserve_sec(self._options.max_wall_time_sec)
        )

        for command_index, command in enumerate(decision.commands):
            if not started_in_finalization:
                self._enter_finalization_if_needed(state, include_turn_trigger=False)
                if state.finalization_started:
                    self._journal.append(
                        "work_batch_interrupted_for_finalization",
                        {
                            "commands_completed": command_index,
                            "commands_remaining": len(decision.commands) - command_index,
                            "triggers": state.finalization_triggers,
                        },
                    )
                    break
            try:
                validate_command(command, self._options.max_command_timeout_sec)
            except PolicyViolation as exc:
                self._record_policy_rejection(state, command.id, str(exc))
                break

            repeat_reason = repeated_command_block_reason(command, state.observations)
            if repeat_reason is not None:
                self._record_policy_rejection(state, command.id, repeat_reason)
                break

            receipt = await self._run_command(
                state,
                runner,
                command,
                wall_deadline_monotonic=command_deadline_monotonic,
            )
            batch_receipts.append(receipt)
            if receipt.succeeded and (
                receipt.mode is CommandMode.CHANGE
                or receipt.observation_fingerprint not in receipts_before
            ):
                batch_progressed = True
            if not receipt.succeeded:
                state.unresolved_errors.append(_failure_summary(receipt))
                break

        stagnant_before = state.stagnant_batches
        state.last_batch_progressed = batch_progressed
        state.stagnant_batches = 0 if batch_progressed else state.stagnant_batches + 1
        self._journal.append(
            "work_batch_finished",
            {
                "work_epoch": state.work_epoch,
                "command_ids": tuple(command.id for command in decision.commands),
                "completed_command_ids": tuple(receipt.command_id for receipt in batch_receipts),
                "failed_command_ids": tuple(
                    receipt.command_id for receipt in batch_receipts if not receipt.succeeded
                ),
                "successful_change_ids": tuple(
                    receipt.command_id
                    for receipt in batch_receipts
                    if receipt.succeeded and receipt.mode is CommandMode.CHANGE
                ),
                "novel_observation_ids": tuple(
                    receipt.command_id
                    for receipt in batch_receipts
                    if receipt.succeeded
                    and receipt.mode is CommandMode.OBSERVE
                    and receipt.observation_fingerprint not in receipts_before
                ),
                "progressed": batch_progressed,
                "stagnant_batches_before": stagnant_before,
                "stagnant_batches_after": state.stagnant_batches,
            },
        )
        if not state.finalization_started:
            self._apply_recovery_policy(state)
        if state.phase is not RunPhase.TERMINATED:
            state.phase = RunPhase.FINALIZING if state.finalization_started else RunPhase.THINKING

    async def _handle_finish(
        self,
        state: RunState,
        decision: AgentDecision,
        controller: CompletionController,
    ) -> None:
        admission = controller.admit_proposal(
            state=state,
            checks=decision.checks,
            coverage=decision.coverage,
            now=self._clock(),
        )
        self._journal.append(
            "completion_proposal_admission",
            {
                **admission.journal_payload(),
                "work_epoch": state.work_epoch,
                "check_ids": tuple(check.id for check in decision.checks),
            },
        )
        if not admission.accepted:
            if not admission.budget.accepted:
                state.unresolved_errors.extend(admission.budget.reasons)
                category = (
                    "completion_review_budget"
                    if "completion review budget exhausted" in admission.budget.reasons
                    else "harness_control"
                )
                self._finish(
                    state,
                    StopReason.BUDGET_EXHAUSTED,
                    failure_category=category,
                )
            else:
                self._reject_completion(state, admission.reasons, controller)
            return

        supporting_observations = list(state.observations)
        state.phase = RunPhase.VERIFYING
        attempt_id = state.next_completion_attempt
        state.next_completion_attempt += 1

        async def execute_check(
            check: VerificationCheck,
            environment: ShellEnvironment,
            command_deadline_monotonic: float,
        ) -> CommandReceipt:
            return await self._run_completion_check(
                state,
                check,
                environment,
                command_deadline_monotonic,
                completion_attempt_id=attempt_id,
            )

        try:
            isolated = await self._completion_isolation.verify(
                CompletionIsolationRequest(
                    attempt_id=attempt_id,
                    work_epoch=state.work_epoch,
                    checks=decision.checks,
                    deadline_monotonic=state.deadline_monotonic,
                ),
                execute_check,
            )
        except CompletionIsolationError as exc:
            state.unresolved_errors.append(str(exc))
            self._journal.append(
                "completion_isolation_failed",
                {
                    "attempt_id": exc.attempt_id,
                    "failure_kind": exc.kind,
                    "detail": exc.detail,
                },
            )
            self._finish(
                state,
                StopReason.INFRA_FAILURE,
                failure_category=exc.kind.value,
            )
            return

        receipts = isolated.receipts

        mechanical_evidence = controller.evaluate_evidence(
            state=state,
            checks=receipts,
            proposed_checks=decision.checks,
            coverage=decision.coverage,
            expected_check_ids=tuple(check.id for check in decision.checks),
            attempt_id=attempt_id,
            isolation=isolated.evidence,
        )
        if not mechanical_evidence.accepted:
            state.latest_evidence = mechanical_evidence
            self._journal.append("verification_receipt", mechanical_evidence)
            self._reject_completion(
                state,
                mechanical_evidence.rejection_reasons,
                controller,
            )
            return

        semantic_assessment: SemanticAssessment | None = None
        state.phase = (
            RunPhase.REVIEWING if self._options.enable_completion_review else RunPhase.VERIFYING
        )
        pre_review_acceptance = controller.evaluate_accept(
            state=state,
            checks=receipts,
            proposed_checks=decision.checks,
            coverage=decision.coverage,
            expected_check_ids=tuple(check.id for check in decision.checks),
            attempt_id=attempt_id,
            isolation=isolated.evidence,
            now=self._clock(),
        )
        if not pre_review_acceptance.budget.accepted:
            completion = controller.evaluate_complete(
                pre_review_acceptance,
                assessment=None,
                state=state,
                attempt_id=attempt_id,
            )
            state.latest_evidence = completion.receipt
            state.unresolved_errors.extend(pre_review_acceptance.budget.reasons)
            self._journal.append(
                "completion_guard_result",
                {
                    **completion.journal_payload(),
                    "attempt_id": attempt_id,
                    "work_epoch": state.work_epoch,
                },
            )
            self._journal.append("verification_receipt", completion.receipt)
            self._finish(
                state,
                StopReason.BUDGET_EXHAUSTED,
                failure_category="harness_control",
            )
            return

        if self._options.enable_completion_review:
            state.completion_review_count += 1
            self._journal.append(
                "completion_review_started",
                CompletionReviewStarted(
                    attempt_id=attempt_id,
                    review_ordinal=state.completion_review_count,
                    work_epoch=state.work_epoch,
                ),
            )
            review_prompt = build_review_prompt(
                instruction=state.instruction,
                contract=controller.contract,
                checks=decision.checks,
                coverage=decision.coverage,
                verification_receipts=receipts,
                isolation=isolated.evidence,
                supporting_observations=supporting_observations,
                prior_findings=state.completion_findings,
            )
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
                acceptance = controller.evaluate_accept(
                    state=state,
                    checks=receipts,
                    proposed_checks=decision.checks,
                    coverage=decision.coverage,
                    expected_check_ids=tuple(check.id for check in decision.checks),
                    attempt_id=attempt_id,
                    isolation=isolated.evidence,
                    now=self._clock(),
                )
                completion = controller.evaluate_complete(
                    acceptance,
                    assessment=None,
                    state=state,
                    attempt_id=attempt_id,
                )
                evidence = completion.receipt
                state.latest_evidence = evidence
                self._journal.append(
                    "completion_guard_result",
                    {
                        **completion.journal_payload(),
                        "attempt_id": attempt_id,
                        "work_epoch": state.work_epoch,
                    },
                )
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

        acceptance = controller.evaluate_accept(
            state=state,
            checks=receipts,
            proposed_checks=decision.checks,
            coverage=decision.coverage,
            expected_check_ids=tuple(check.id for check in decision.checks),
            attempt_id=attempt_id,
            isolation=isolated.evidence,
            now=self._clock(),
        )
        completion = controller.evaluate_complete(
            acceptance,
            assessment=semantic_assessment,
            state=state,
            attempt_id=attempt_id,
        )
        evidence = completion.receipt
        state.latest_evidence = evidence
        self._journal.append(
            "completion_guard_result",
            {
                **completion.journal_payload(),
                "attempt_id": attempt_id,
                "work_epoch": state.work_epoch,
            },
        )
        self._journal.append("verification_receipt", evidence)
        if completion.accepted:
            state.completion_findings = ()
            state.final_summary = decision.summary or decision.rationale
            self._finish(
                state,
                StopReason.VERIFIED,
                completion_controller=controller,
                completion_permit=completion.permit,
            )
            return

        if not completion.accept.budget.accepted:
            state.unresolved_errors.extend(completion.accept.budget.reasons)
            self._finish(
                state,
                StopReason.BUDGET_EXHAUSTED,
                failure_category="harness_control",
            )
            return
        if not completion.accept.phase.accepted:
            raise RuntimeError(
                f"completion evaluation reached an illegal phase: {completion.accept.phase.reasons}"
            )
        self._reject_completion(state, evidence.rejection_reasons, controller)

    async def _run_completion_check(
        self,
        state: RunState,
        check: VerificationCheck,
        environment: ShellEnvironment,
        command_deadline_monotonic: float,
        *,
        completion_attempt_id: int,
    ) -> CommandReceipt:
        command = ShellCommand(
            id=check.id,
            script=check.script,
            purpose=check.proves,
            cwd=check.cwd,
            timeout_sec=check.timeout_sec,
            mode=CommandMode.OBSERVE,
            repeat_reason="fresh isolated completion verification",
        )
        runner = CommandRunner(
            contain_harbor_environment(environment),
            self._journal,
            self._options,
            self._clock,
            completion_attempt_id=completion_attempt_id,
        )
        return await self._run_command(
            state,
            runner,
            command,
            wall_deadline_monotonic=command_deadline_monotonic,
        )

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
        *,
        wall_deadline_monotonic: float | None = None,
    ) -> CommandReceipt:
        effective_deadline = (
            state.deadline_monotonic if wall_deadline_monotonic is None else wall_deadline_monotonic
        )
        wall_timeout_sec = max(0.0, effective_deadline - self._clock())
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
        if state.must_replan:
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
            RecoveryRequired(
                recovery_ordinal=state.recovery_count + 1,
                cycle=repeated_cycle,
                stagnant_batches=state.stagnant_batches,
                work_epoch=state.work_epoch,
            ),
        )

    def _model_call_timeout_sec(
        self,
        state: RunState,
        *,
        preserve_finalization_reserve: bool = False,
    ) -> float:
        effective_deadline = state.deadline_monotonic - MODEL_CALL_SHUTDOWN_RESERVE_SEC
        if preserve_finalization_reserve and not state.finalization_started:
            effective_deadline = min(
                effective_deadline,
                state.deadline_monotonic
                - finalization_wall_time_reserve_sec(self._options.max_wall_time_sec),
            )
        remaining = effective_deadline - self._clock()
        return max(
            0.001,
            min(
                float(self._options.max_model_call_timeout_sec),
                remaining,
            ),
        )

    def _reject_completion(
        self,
        state: RunState,
        reasons: tuple[str, ...],
        controller: CompletionController,
    ) -> None:
        budget = controller.contract.b_req
        state.completion_findings = _stable_unique(reasons)
        review_exhausted = (
            self._options.enable_completion_review
            and state.completion_review_count >= budget.max_completion_reviews
        )
        repair_count_before = state.repair_count
        repair_granted = not review_exhausted and repair_count_before < budget.max_repairs
        if repair_granted:
            state.repair_count += 1
            state.phase = RunPhase.REPAIRING
        self._journal.append(
            "repair_admission",
            {
                "accepted": repair_granted,
                "reasons": (
                    ("completion review budget exhausted",)
                    if review_exhausted
                    else (() if repair_granted else ("completion repair budget exhausted",))
                ),
                "repair_count_before": repair_count_before,
                "repair_count_after": state.repair_count,
                "max_repairs": budget.max_repairs,
                "completion_reviews_used": state.completion_review_count,
                "max_completion_reviews": budget.max_completion_reviews,
            },
        )
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

    def _enter_finalization_if_needed(
        self,
        state: RunState,
        *,
        include_turn_trigger: bool = True,
    ) -> None:
        if state.finalization_started:
            return
        turn_reserve = finalization_turn_reserve(self._options.max_turns)
        wall_time_reserve_sec = finalization_wall_time_reserve_sec(self._options.max_wall_time_sec)
        turns_remaining = self._options.max_turns - state.turn_count
        wall_time_remaining_sec = max(0.0, state.deadline_monotonic - self._clock())
        triggers: list[FinalizationTrigger] = []
        if include_turn_trigger and turns_remaining <= turn_reserve:
            triggers.append("turn_budget")
        if wall_time_remaining_sec <= wall_time_reserve_sec:
            triggers.append("wall_clock")
        if not triggers:
            return
        state.finalization_started = True
        state.finalization_triggers = tuple(triggers)
        state.must_replan = False
        state.recovery_directive = None
        self._journal.append(
            "finalization_started",
            FinalizationStarted(
                triggers=state.finalization_triggers,
                turns_remaining=turns_remaining,
                turn_reserve=turn_reserve,
                wall_time_remaining_sec=wall_time_remaining_sec,
                wall_time_reserve_sec=wall_time_reserve_sec,
                completion_findings=state.completion_findings,
            ),
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
        completion_controller: CompletionController | None = None,
        completion_permit: object | None = None,
    ) -> RunReport:
        if state.stop_reason is None:
            if reason is StopReason.VERIFIED:
                if completion_controller is None:
                    raise RuntimeError("verified completion requires a CompletionController")
                if not completion_controller.phase_ok(state):
                    raise RuntimeError(
                        f"verified completion is not allowed in phase '{state.phase}'"
                    )
                if not completion_controller.authorizes(
                    completion_permit,
                    state,
                ):
                    raise RuntimeError(
                        "verified completion requires a current CompletionController permit"
                    )
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
