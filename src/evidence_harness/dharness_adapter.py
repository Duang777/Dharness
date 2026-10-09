from __future__ import annotations

from copy import deepcopy

from evidence_harness.completion_contract import CompletionContract
from evidence_harness.completion_isolation import (
    CheckExecutor,
    CompletionIsolation,
    CompletionIsolationError,
    CompletionIsolationRequest,
)
from evidence_harness.harness_adapter import (
    CandidateIdentity,
    CandidateSnapshot,
    CompletionCounters,
    CompletionPolicy,
    CompletionProposal,
    CompletionState,
    CompletionTrace,
    CompletionTransactionView,
    CurrentCandidate,
    HarnessAdapter,
    HarnessAdapterError,
    IsolatedCheckRun,
)
from evidence_harness.protocol import ActionKind, AgentDecision, LoopOptions, RunState


class DharnessAdapter(HarnessAdapter):
    def __init__(
        self,
        *,
        view: CompletionTransactionView,
        isolation: CompletionIsolation,
        execute_check: CheckExecutor,
    ) -> None:
        self._view = deepcopy(view)
        self._isolation = isolation
        self._execute_check = execute_check
        self._has_run = False

    @classmethod
    def capture(
        cls,
        *,
        contract: CompletionContract,
        options: LoopOptions,
        state: RunState,
        decision: AgentDecision,
        isolation: CompletionIsolation,
        execute_check: CheckExecutor,
    ) -> DharnessAdapter:
        if decision.action is not ActionKind.FINISH:
            raise ValueError("DharnessAdapter requires a finish decision")
        return cls(
            view=CompletionTransactionView(
                proposal=CompletionProposal(
                    checks=tuple(decision.checks),
                    coverage=tuple(decision.coverage),
                    candidate=CurrentCandidate(),
                    summary=decision.summary,
                ),
                contract=contract,
                policy=CompletionPolicy(
                    review_required=options.enable_completion_review,
                    max_check_timeout_sec=options.max_command_timeout_sec,
                ),
                trace=CompletionTrace(receipts=tuple(state.observations)),
                state=cls.project_state(state),
            ),
            isolation=isolation,
            execute_check=execute_check,
        )

    @property
    def view(self) -> CompletionTransactionView:
        return deepcopy(self._view)

    @staticmethod
    def project_state(state: RunState) -> CompletionState:
        return CompletionState(
            phase=state.phase,
            work_epoch=state.work_epoch,
            next_completion_attempt=state.next_completion_attempt,
            deadline_monotonic=state.deadline_monotonic,
            counters=CompletionCounters(
                turns=state.turn_count,
                environment_calls=state.environment_call_count,
                repairs=state.repair_count,
                recoveries=state.recovery_count,
                completion_reviews=state.completion_review_count,
            ),
        )

    async def run_checks_isolated(self) -> IsolatedCheckRun:
        if self._has_run:
            raise RuntimeError("a completion adapter can run isolated checks only once")
        self._has_run = True
        state = self._view.state
        try:
            result = await self._isolation.verify(
                CompletionIsolationRequest(
                    attempt_id=state.next_completion_attempt,
                    work_epoch=state.work_epoch,
                    checks=self._view.proposal.checks,
                    deadline_monotonic=state.deadline_monotonic,
                ),
                self._execute_check,
            )
        except CompletionIsolationError as exc:
            raise HarnessAdapterError(
                exc.kind.value,
                exc.detail,
                attempt_id=exc.attempt_id,
            ) from exc

        identity = CandidateIdentity(
            algorithm="docker-image-id",
            value=result.evidence.candidate_image_id,
        )
        return IsolatedCheckRun(
            snapshot=CandidateSnapshot(
                identity=identity,
                attempt_id=result.evidence.attempt_id,
                work_epoch=result.evidence.work_epoch,
            ),
            receipts=result.receipts,
            isolation=result.evidence,
        )
