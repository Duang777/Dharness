from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from evidence_harness.completion_contract import CompletionBudget, CompletionContract
from evidence_harness.evidence import EvidenceGate
from evidence_harness.protocol import (
    CommandReceipt,
    CompletionIsolationEvidence,
    LoopOptions,
    RequirementCoverage,
    RunPhase,
    RunState,
    SemanticAssessment,
    VerificationCheck,
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
class ProposalAdmission:
    evidence: GuardResult
    budget: GuardResult
    phase: GuardResult

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
    state_identity: int
    attempt_id: int
    work_epoch: int
    candidate_digest: str | None
    phase: RunPhase
    counters: tuple[int, int, int, int, int]


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
        state: RunState,
        *,
        now: float,
        requested_environment_calls: int,
        review_required: bool,
    ) -> GuardResult:
        reasons = list(self._counter_reasons(contract, state, now=now))
        budget = contract.b_req
        if state.environment_call_count + requested_environment_calls > (
            budget.max_environment_calls
        ):
            reasons.append("completion checks exceed the remaining environment-call budget")
        if review_required and state.completion_review_count >= budget.max_completion_reviews:
            reasons.append("completion review budget exhausted")
        return _result("budget", reasons)

    def evaluate_completion(
        self,
        contract: CompletionContract,
        state: RunState,
        *,
        now: float,
    ) -> GuardResult:
        return _result("budget", self._counter_reasons(contract, state, now=now))

    def evaluate_work(
        self,
        contract: CompletionContract,
        state: RunState,
        *,
        command_count: int,
    ) -> GuardResult:
        budget = contract.b_req
        reasons: list[str] = []
        remaining_calls = budget.max_environment_calls - state.environment_call_count
        available_for_work = remaining_calls - budget.verification_environment_reserve
        if command_count > available_for_work:
            reasons.append("work command batch would consume the reserved verification calls")
        if budget.max_turns - state.turn_count < 1:
            reasons.append("no executor turn remains after this batch")
        return _result("budget", reasons)

    @staticmethod
    def _counter_reasons(
        contract: CompletionContract,
        state: RunState,
        *,
        now: float,
    ) -> tuple[str, ...]:
        budget = contract.b_req
        reasons: list[str] = []
        if state.turn_count > budget.max_turns:
            reasons.append(f"turn budget exceeded: {state.turn_count} > {budget.max_turns}")
        if state.environment_call_count > budget.max_environment_calls:
            reasons.append(
                "environment-call budget exceeded: "
                f"{state.environment_call_count} > {budget.max_environment_calls}"
            )
        if state.repair_count > budget.max_repairs:
            reasons.append(f"repair budget exceeded: {state.repair_count} > {budget.max_repairs}")
        if state.recovery_count > budget.max_recoveries:
            reasons.append(
                f"recovery budget exceeded: {state.recovery_count} > {budget.max_recoveries}"
            )
        if state.completion_review_count > budget.max_completion_reviews:
            reasons.append(
                "completion review budget exceeded: "
                f"{state.completion_review_count} > {budget.max_completion_reviews}"
            )
        if now > state.deadline_monotonic:
            reasons.append(f"wall-clock budget exceeded: {now:g} > {state.deadline_monotonic:g}")
        return tuple(reasons)


class PhaseGuard:
    def evaluate_proposal(self, state: RunState) -> GuardResult:
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
        state: RunState,
        *,
        review_required: bool,
    ) -> GuardResult:
        expected = RunPhase.REVIEWING if review_required else RunPhase.VERIFYING
        reasons = (
            ()
            if phase_ok(state, review_required=review_required)
            else (
                f"completion requires phase '{expected}' when review is "
                f"{'enabled' if review_required else 'disabled'}; got '{state.phase}'",
            )
        )
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


def phase_ok(state: RunState, *, review_required: bool) -> bool:
    expected = RunPhase.REVIEWING if review_required else RunPhase.VERIFYING
    return state.phase is expected


class CompletionController:
    def __init__(
        self,
        contract: CompletionContract,
        *,
        options: LoopOptions | None = None,
    ) -> None:
        self.contract = contract
        self.options = options or LoopOptions()
        if contract.b_req != CompletionBudget.from_options(self.options):
            raise ValueError("completion contract budget does not match controller options")
        self.evidence_gate = EvidenceGate(self.options)
        self.budget_guard = BudgetGuard()
        self.phase_guard = PhaseGuard()
        self.review_gate = ReviewGate()

    def validate_proposal(
        self,
        checks: tuple[VerificationCheck, ...],
        coverage: tuple[RequirementCoverage, ...],
    ) -> GuardResult:
        reasons = self.evidence_gate.validate_proposal(
            checks,
            coverage,
            contract=self.contract,
            isolated=True,
        )
        return _result("evidence", reasons)

    def admit_proposal(
        self,
        *,
        state: RunState,
        checks: tuple[VerificationCheck, ...],
        coverage: tuple[RequirementCoverage, ...],
        now: float,
    ) -> ProposalAdmission:
        return ProposalAdmission(
            evidence=self.validate_proposal(checks, coverage),
            budget=self.budget_guard.evaluate_proposal(
                self.contract,
                state,
                now=now,
                requested_environment_calls=len(checks),
                review_required=self.options.enable_completion_review,
            ),
            phase=self.phase_guard.evaluate_proposal(state),
        )

    def evaluate_evidence(
        self,
        *,
        state: RunState,
        checks: tuple[CommandReceipt, ...],
        proposed_checks: tuple[VerificationCheck, ...],
        coverage: tuple[RequirementCoverage, ...],
        expected_check_ids: tuple[str, ...],
        attempt_id: int,
        isolation: CompletionIsolationEvidence | None,
    ) -> VerificationReceipt:
        return self.evidence_gate.decide(
            contract=self.contract,
            work_epoch=state.work_epoch,
            checks=checks,
            proposed_checks=proposed_checks,
            coverage=coverage,
            expected_check_ids=expected_check_ids,
            attempt_id=attempt_id,
            candidate_digest=state.candidate_digest,
            isolation=isolation,
            require_isolation=self.contract.v_req.require_isolation,
        )

    def evaluate_accept(
        self,
        *,
        state: RunState,
        checks: tuple[CommandReceipt, ...],
        proposed_checks: tuple[VerificationCheck, ...],
        coverage: tuple[RequirementCoverage, ...],
        expected_check_ids: tuple[str, ...],
        attempt_id: int,
        isolation: CompletionIsolationEvidence | None,
        now: float,
    ) -> AcceptEvaluation:
        receipt = self.evaluate_evidence(
            state=state,
            checks=checks,
            proposed_checks=proposed_checks,
            coverage=coverage,
            expected_check_ids=expected_check_ids,
            attempt_id=attempt_id,
            isolation=isolation,
        )
        return AcceptEvaluation(
            evidence=_result("evidence", receipt.rejection_reasons),
            budget=self.budget_guard.evaluate_completion(
                self.contract,
                state,
                now=now,
            ),
            phase=self.phase_guard.evaluate_completion(
                state,
                review_required=self.options.enable_completion_review,
            ),
            receipt=receipt,
            _binding=_EvaluationBinding(
                authority=_PERMIT_AUTHORITY,
                controller_identity=id(self),
                state_identity=id(state),
                attempt_id=attempt_id,
                work_epoch=state.work_epoch,
                candidate_digest=state.candidate_digest,
                phase=state.phase,
                counters=_state_counters(state),
            ),
        )

    def evaluate_complete(
        self,
        acceptance: AcceptEvaluation,
        *,
        assessment: SemanticAssessment | None,
        state: RunState,
        attempt_id: int,
    ) -> CompletionEvaluation:
        if not self._authorizes_acceptance(acceptance, state, attempt_id):
            raise RuntimeError("completion evaluation requires a current acceptance result")
        review_required = self.options.enable_completion_review
        review = self.review_gate.evaluate(
            required=review_required,
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
                state_identity=id(state),
                attempt_id=attempt_id,
                work_epoch=state.work_epoch,
                candidate_digest=state.candidate_digest,
                phase=state.phase,
                counters=_state_counters(state),
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
        state: RunState,
    ) -> bool:
        return (
            isinstance(permit, _CompletionPermit)
            and permit.authority is _PERMIT_AUTHORITY
            and permit.controller_identity == id(self)
            and permit.state_identity == id(state)
            and permit.attempt_id == state.next_completion_attempt - 1
            and permit.work_epoch == state.work_epoch
            and permit.candidate_digest == state.candidate_digest
            and permit.phase is state.phase
            and permit.counters == _state_counters(state)
            and self.phase_guard.evaluate_completion(
                state,
                review_required=self.options.enable_completion_review,
            ).accepted
        )

    def _authorizes_acceptance(
        self,
        acceptance: AcceptEvaluation,
        state: RunState,
        attempt_id: int,
    ) -> bool:
        binding = acceptance._binding
        return (
            binding.authority is _PERMIT_AUTHORITY
            and binding.controller_identity == id(self)
            and binding.state_identity == id(state)
            and binding.attempt_id == attempt_id
            and binding.work_epoch == state.work_epoch
            and binding.candidate_digest == state.candidate_digest
            and binding.phase is state.phase
            and binding.counters == _state_counters(state)
        )

    def phase_ok(self, state: RunState) -> bool:
        return phase_ok(
            state,
            review_required=self.options.enable_completion_review,
        )


def _result(name: GuardName, reasons: tuple[str, ...] | list[str]) -> GuardResult:
    unique = tuple(dict.fromkeys(reasons))
    return GuardResult(name=name, accepted=not unique, reasons=unique)


def _stable_reasons(*results: GuardResult) -> tuple[str, ...]:
    return tuple(dict.fromkeys(reason for result in results for reason in result.reasons))


def _state_counters(state: RunState) -> tuple[int, int, int, int, int]:
    return (
        state.turn_count,
        state.environment_call_count,
        state.repair_count,
        state.recovery_count,
        state.completion_review_count,
    )
