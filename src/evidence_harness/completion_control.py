from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from evidence_harness.completion_contract import CompletionContract
from evidence_harness.evidence import EvidenceGate
from evidence_harness.harness_adapter import (
    CompletionState,
    HarnessAdapter,
    IsolatedCheckRun,
)
from evidence_harness.protocol import (
    RunPhase,
    SemanticAssessment,
    VerificationReceipt,
)

GuardName = Literal["evidence", "budget", "phase", "review"]
_PERMIT_AUTHORITY = object()


class GuardResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: GuardName
    accepted: bool
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _AdmissionBinding:
    authority: object
    controller_identity: int
    view_identity: int


@dataclass(frozen=True, slots=True)
class ProposalAdmission:
    evidence: GuardResult
    budget: GuardResult
    phase: GuardResult
    _binding: _AdmissionBinding

    @property
    def accepted(self) -> bool:
        return self.evidence.accepted and self.budget.accepted and self.phase.accepted

    @property
    def reasons(self) -> tuple[str, ...]:
        return _stable_reasons(self.evidence, self.budget, self.phase)

    def journal_payload(self) -> dict[str, object]:
        return {
            "accepted": self.accepted,
            "evidence": self.evidence.model_dump(mode="json"),
            "budget": self.budget.model_dump(mode="json"),
            "phase": self.phase.model_dump(mode="json"),
        }


@dataclass(frozen=True, slots=True)
class _EvaluationBinding:
    authority: object
    controller_identity: int
    state: CompletionState
    attempt_id: int
    work_epoch: int


@dataclass(frozen=True, slots=True)
class AcceptEvaluation:
    evidence: GuardResult
    budget: GuardResult
    phase: GuardResult
    receipt: VerificationReceipt
    _binding: _EvaluationBinding

    @property
    def accepted(self) -> bool:
        return self.evidence.accepted and self.budget.accepted and self.phase.accepted

    @property
    def reasons(self) -> tuple[str, ...]:
        return _stable_reasons(self.evidence, self.budget, self.phase)


@dataclass(frozen=True, slots=True)
class _CompletionPermit(_EvaluationBinding):
    pass


@dataclass(frozen=True, slots=True)
class CompletionEvaluation:
    accept: AcceptEvaluation
    review: GuardResult
    receipt: VerificationReceipt
    permit: _CompletionPermit | None

    @property
    def accepted(self) -> bool:
        return self.accept.accepted and self.review.accepted

    def journal_payload(self) -> dict[str, object]:
        return {
            "accepted": self.accepted,
            "evidence": self.accept.evidence.model_dump(mode="json"),
            "budget": self.accept.budget.model_dump(mode="json"),
            "phase": self.accept.phase.model_dump(mode="json"),
            "review": self.review.model_dump(mode="json"),
        }


class BudgetGuard:
    def evaluate_proposal(
        self,
        contract: CompletionContract,
        state: CompletionState,
        *,
        now: float,
        requested_environment_calls: int,
        review_required: bool,
    ) -> GuardResult:
        reasons = list(self._counter_reasons(contract, state, now=now))
        budget = contract.b_req
        if state.counters.environment_calls + requested_environment_calls > (
            budget.max_environment_calls
        ):
            reasons.append("completion checks exceed the remaining environment-call budget")
        if review_required and state.counters.completion_reviews >= budget.max_completion_reviews:
            reasons.append("completion review budget exhausted")
        return _result("budget", reasons)

    def evaluate_completion(
        self,
        contract: CompletionContract,
        state: CompletionState,
        *,
        now: float,
    ) -> GuardResult:
        return _result("budget", self._counter_reasons(contract, state, now=now))

    def evaluate_work(
        self,
        contract: CompletionContract,
        state: CompletionState,
        *,
        command_count: int,
    ) -> GuardResult:
        budget = contract.b_req
        reasons: list[str] = []
        remaining_calls = budget.max_environment_calls - state.counters.environment_calls
        available_for_work = remaining_calls - budget.verification_environment_reserve
        if command_count > available_for_work:
            reasons.append("work command batch would consume the reserved verification calls")
        if budget.max_turns - state.counters.turns < 1:
            reasons.append("no executor turn remains after this batch")
        return _result("budget", reasons)

    @staticmethod
    def _counter_reasons(
        contract: CompletionContract,
        state: CompletionState,
        *,
        now: float,
    ) -> tuple[str, ...]:
        budget = contract.b_req
        reasons: list[str] = []
        if state.counters.turns > budget.max_turns:
            reasons.append(f"turn budget exceeded: {state.counters.turns} > {budget.max_turns}")
        if state.counters.environment_calls > budget.max_environment_calls:
            reasons.append(
                "environment-call budget exceeded: "
                f"{state.counters.environment_calls} > {budget.max_environment_calls}"
            )
        if state.counters.repairs > budget.max_repairs:
            reasons.append(
                f"repair budget exceeded: {state.counters.repairs} > {budget.max_repairs}"
            )
        if state.counters.recoveries > budget.max_recoveries:
            reasons.append(
                f"recovery budget exceeded: {state.counters.recoveries} > {budget.max_recoveries}"
            )
        if state.counters.completion_reviews > budget.max_completion_reviews:
            reasons.append(
                "completion review budget exceeded: "
                f"{state.counters.completion_reviews} > {budget.max_completion_reviews}"
            )
        if now > state.deadline_monotonic:
            reasons.append(f"wall-clock budget exceeded: {now:g} > {state.deadline_monotonic:g}")
        return tuple(reasons)


class PhaseGuard:
    def evaluate_proposal(self, state: CompletionState) -> GuardResult:
        allowed = {
            RunPhase.THINKING,
            RunPhase.FINALIZING,
            RunPhase.REPAIRING,
        }
        reasons = (
            ()
            if state.phase in allowed
            else (f"finish proposal is not allowed in phase '{state.phase}'",)
        )
        return _result("phase", reasons)

    def evaluate_completion(
        self,
        state: CompletionState,
        *,
        review_required: bool,
        attempt_id: int,
        work_epoch: int,
    ) -> GuardResult:
        expected = RunPhase.REVIEWING if review_required else RunPhase.VERIFYING
        reasons: list[str] = []
        if not phase_ok(state, review_required=review_required):
            reasons.append(
                f"completion requires phase '{expected}' when review is "
                f"{'enabled' if review_required else 'disabled'}; got '{state.phase}'"
            )
        if state.next_completion_attempt != attempt_id + 1:
            reasons.append("completion attempt state does not match")
        if state.work_epoch != work_epoch:
            reasons.append("completion work epoch changed during verification")
        return _result("phase", reasons)


class ReviewGate:
    def evaluate(
        self,
        *,
        required: bool,
        assessment: SemanticAssessment | None,
    ) -> GuardResult:
        reasons: tuple[str, ...] = ()
        if required and assessment is None:
            reasons = ("completion semantic review was not accepted",)
        elif assessment is not None and not assessment.accepted:
            reasons = tuple(dict.fromkeys((assessment.rationale, *assessment.findings)))
        return _result("review", reasons)


def phase_ok(state: CompletionState, *, review_required: bool) -> bool:
    expected = RunPhase.REVIEWING if review_required else RunPhase.VERIFYING
    return state.phase is expected


class CompletionController:
    def __init__(self, adapter: HarnessAdapter) -> None:
        self._adapter = adapter
        self._view = adapter.view
        self.contract = self._view.contract
        self.policy = self._view.policy
        self.evidence_gate = EvidenceGate(max_command_timeout_sec=self.policy.max_check_timeout_sec)
        self.budget_guard = BudgetGuard()
        self.phase_guard = PhaseGuard()
        self.review_gate = ReviewGate()
        self._verification_started = False

    def validate_proposal(self) -> GuardResult:
        proposal = self._view.proposal
        reasons = self.evidence_gate.validate_proposal(
            proposal.checks,
            proposal.coverage,
            contract=self.contract,
            isolated=True,
        )
        return _result("evidence", reasons)

    def admit(self, *, now: float) -> ProposalAdmission:
        state = self._view.state
        return ProposalAdmission(
            evidence=self.validate_proposal(),
            budget=self.budget_guard.evaluate_proposal(
                self.contract,
                state,
                now=now,
                requested_environment_calls=len(self._view.proposal.checks),
                review_required=self.policy.review_required,
            ),
            phase=self.phase_guard.evaluate_proposal(state),
            _binding=_AdmissionBinding(
                authority=_PERMIT_AUTHORITY,
                controller_identity=id(self),
                view_identity=id(self._view),
            ),
        )

    async def verify(self, admission: ProposalAdmission) -> IsolatedCheckRun:
        if not admission.accepted:
            raise RuntimeError("isolated checks require an accepted proposal")
        binding = admission._binding
        if (
            binding.authority is not _PERMIT_AUTHORITY
            or binding.controller_identity != id(self)
            or binding.view_identity != id(self._view)
        ):
            raise RuntimeError("proposal admission belongs to another completion controller")
        if self._verification_started:
            raise RuntimeError("a completion controller can verify only once")
        self._verification_started = True
        return deepcopy(await self._adapter.run_checks_isolated())

    def evaluate_evidence(self, run: IsolatedCheckRun) -> VerificationReceipt:
        verified_run = deepcopy(run)
        proposal = self._view.proposal
        state = self._view.state
        return self.evidence_gate.decide(
            contract=self.contract,
            work_epoch=state.work_epoch,
            checks=verified_run.receipts,
            proposed_checks=proposal.checks,
            coverage=proposal.coverage,
            expected_check_ids=tuple(check.id for check in proposal.checks),
            attempt_id=state.next_completion_attempt,
            isolation=verified_run.isolation,
            require_isolation=self.contract.v_req.require_isolation,
            prior_rejections=self._run_binding_rejections(verified_run),
        )

    def evaluate_accept(
        self,
        run: IsolatedCheckRun,
        *,
        state: CompletionState,
        now: float,
    ) -> AcceptEvaluation:
        expected_state = self._view.state
        receipt = self.evaluate_evidence(run)
        return AcceptEvaluation(
            evidence=_result("evidence", receipt.rejection_reasons),
            budget=self.budget_guard.evaluate_completion(
                self.contract,
                state,
                now=now,
            ),
            phase=self.phase_guard.evaluate_completion(
                state,
                review_required=self.policy.review_required,
                attempt_id=expected_state.next_completion_attempt,
                work_epoch=expected_state.work_epoch,
            ),
            receipt=receipt,
            _binding=_EvaluationBinding(
                authority=_PERMIT_AUTHORITY,
                controller_identity=id(self),
                state=state,
                attempt_id=expected_state.next_completion_attempt,
                work_epoch=expected_state.work_epoch,
            ),
        )

    def evaluate_complete(
        self,
        acceptance: AcceptEvaluation,
        *,
        assessment: SemanticAssessment | None,
        state: CompletionState,
    ) -> CompletionEvaluation:
        if not self._authorizes_acceptance(acceptance, state):
            raise RuntimeError("completion evaluation requires a current acceptance result")
        review = self.review_gate.evaluate(
            required=self.policy.review_required,
            assessment=assessment,
        )
        reasons = _stable_reasons(
            acceptance.evidence,
            acceptance.budget,
            acceptance.phase,
            review,
        )
        accepted = acceptance.accepted and review.accepted
        receipt = acceptance.receipt.model_copy(
            update={
                "semantic_assessment": assessment,
                "accepted": accepted,
                "rejection_reasons": reasons,
            }
        )
        permit = (
            _CompletionPermit(
                authority=_PERMIT_AUTHORITY,
                controller_identity=id(self),
                state=state,
                attempt_id=self._view.state.next_completion_attempt,
                work_epoch=self._view.state.work_epoch,
            )
            if accepted
            else None
        )
        return CompletionEvaluation(
            accept=acceptance,
            review=review,
            receipt=receipt,
            permit=permit,
        )

    def authorizes(
        self,
        permit: object | None,
        state: CompletionState,
    ) -> bool:
        return (
            isinstance(permit, _CompletionPermit)
            and permit.authority is _PERMIT_AUTHORITY
            and permit.controller_identity == id(self)
            and permit.state == state
            and permit.attempt_id == self._view.state.next_completion_attempt
            and permit.work_epoch == self._view.state.work_epoch
            and self.phase_guard.evaluate_completion(
                state,
                review_required=self.policy.review_required,
                attempt_id=permit.attempt_id,
                work_epoch=permit.work_epoch,
            ).accepted
        )

    def _authorizes_acceptance(
        self,
        acceptance: AcceptEvaluation,
        state: CompletionState,
    ) -> bool:
        binding = acceptance._binding
        return (
            binding.authority is _PERMIT_AUTHORITY
            and binding.controller_identity == id(self)
            and binding.state == state
            and binding.attempt_id == self._view.state.next_completion_attempt
            and binding.work_epoch == self._view.state.work_epoch
        )

    def phase_ok(self, state: CompletionState) -> bool:
        return phase_ok(
            state,
            review_required=self.policy.review_required,
        )

    def _run_binding_rejections(self, run: IsolatedCheckRun) -> tuple[str, ...]:
        expected_state = self._view.state
        reasons: list[str] = []
        if run.snapshot.attempt_id != expected_state.next_completion_attempt:
            reasons.append("candidate snapshot attempt does not match")
        if run.snapshot.work_epoch != expected_state.work_epoch:
            reasons.append("candidate snapshot work epoch does not match")
        if run.snapshot.identity.value != run.isolation.candidate_image_id:
            reasons.append("candidate snapshot identity does not match isolation evidence")
        return tuple(reasons)


def _result(name: GuardName, reasons: tuple[str, ...] | list[str]) -> GuardResult:
    unique = tuple(dict.fromkeys(reasons))
    return GuardResult(name=name, accepted=not unique, reasons=unique)


def _stable_reasons(*results: GuardResult) -> tuple[str, ...]:
    return tuple(dict.fromkeys(reason for result in results for reason in result.reasons))
