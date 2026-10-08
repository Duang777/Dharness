from __future__ import annotations

import hashlib

import pytest

from evidence_harness.completion_contract import (
    CompletionContract,
    TaskRequirement,
)
from evidence_harness.completion_control import (
    BudgetGuard,
    CompletionController,
    PhaseGuard,
    ReviewGate,
)
from evidence_harness.protocol import (
    CheckIsolationEvidence,
    CheckKind,
    CommandMode,
    CommandReceipt,
    CompletionIsolationEvidence,
    FilesystemDelta,
    IsolationCost,
    LoopOptions,
    OutputExcerpt,
    RequirementCoverage,
    RunPhase,
    RunState,
    SemanticAssessment,
    SourceAttestation,
    VerificationCheck,
)

_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def _state(*, phase: RunPhase = RunPhase.VERIFYING) -> RunState:
    return RunState(
        instruction="Create answer.txt and make the command print ready",
        phase=phase,
        started_monotonic=0,
        deadline_monotonic=100,
        work_epoch=1,
    )


def _contract(options: LoopOptions | None = None) -> CompletionContract:
    return CompletionContract.create(
        requirements=(
            TaskRequirement(
                id="artifact",
                statement="answer.txt exists",
                evidence_kinds=(CheckKind.ARTIFACT,),
            ),
            TaskRequirement(
                id="behavior",
                statement="the command prints ready",
                evidence_kinds=(CheckKind.BEHAVIOR,),
            ),
        ),
        options=options or LoopOptions(),
    )


def _check(
    check_id: str,
    kind: CheckKind,
    *,
    script: str | None = None,
) -> VerificationCheck:
    return VerificationCheck(
        id=check_id,
        kind=kind,
        script=script or f"test {check_id}",
        proves=check_id,
    )


def _receipt(check: VerificationCheck, sequence: int) -> CommandReceipt:
    output = OutputExcerpt(
        head="",
        tail="",
        total_bytes=0,
        omitted_bytes=0,
        sha256=_EMPTY_SHA256,
    )
    return CommandReceipt(
        sequence=sequence,
        command_id=check.id,
        script=check.script,
        purpose=check.proves,
        cwd=check.cwd,
        mode=CommandMode.OBSERVE,
        work_epoch=1,
        return_code=0,
        duration_sec=0.1,
        stdout=output,
        stderr=output,
        command_fingerprint=f"{sequence}" * 64,
        observation_fingerprint=f"{sequence + 2}" * 64,
    )


def _isolation(
    checks: tuple[VerificationCheck, ...],
    receipts: tuple[CommandReceipt, ...],
) -> CompletionIsolationEvidence:
    candidate = "sha256:candidate"
    return CompletionIsolationEvidence(
        backend="test-isolation-v1",
        attempt_id=1,
        work_epoch=1,
        candidate_image_id=candidate,
        environment_identity_sha256="9" * 64,
        checks=tuple(
            CheckIsolationEvidence(
                check_id=check.id,
                receipt_sequence=receipt.sequence,
                receipt_observation_sha256=receipt.observation_fingerprint,
                child_id_sha256=f"{index}" * 64,
                started_from_image_id=candidate,
                delta=FilesystemDelta(sha256=_EMPTY_SHA256),
                disposed=True,
            )
            for index, (check, receipt) in enumerate(
                zip(checks, receipts, strict=True),
                start=4,
            )
        ),
        source=SourceAttestation(
            container_id_sha256="8" * 64,
            diff_sha256_before=_EMPTY_SHA256,
            diff_sha256_after=_EMPTY_SHA256,
            remained_paused=True,
            resumed=True,
        ),
        snapshot_image_disposed=True,
        cost=IsolationCost(
            host_operations=len(checks),
            child_count=len(checks),
            duration_sec=0.2,
        ),
    )


def test_default_contract_freezes_the_full_instruction_and_runtime_budgets() -> None:
    options = LoopOptions(max_turns=7, max_environment_calls=11, max_repairs=2)

    contract = CompletionContract.from_instruction("Create answer.txt", options)

    assert tuple(item.id for item in contract.e_req) == ("REQ-1",)
    assert contract.e_req[0].statement == "Create answer.txt"
    assert set(contract.e_req[0].evidence_kinds) == set(CheckKind)
    assert contract.b_req.max_turns == 7
    assert contract.b_req.max_environment_calls == 11
    assert contract.b_req.max_repairs == 2


def test_default_contract_preserves_a_long_instruction() -> None:
    instruction = "x" * 10_000

    contract = CompletionContract.from_instruction(instruction, LoopOptions())

    assert contract.e_req[0].statement == instruction


def test_contract_coverage_rejects_an_omitted_requirement() -> None:
    controller = CompletionController(_contract())
    checks = (_check("artifact-check", CheckKind.ARTIFACT),)
    coverage = (RequirementCoverage(requirement="artifact", check_ids=("artifact-check",)),)

    result = controller.validate_proposal(checks, coverage)

    assert result.accepted is False
    assert result.reasons == ("contract requirements are not covered: ['behavior']",)


def test_finish_proposal_is_rejected_in_an_illegal_phase() -> None:
    controller = CompletionController(_contract())
    state = _state(phase=RunPhase.EXECUTING)
    checks = (
        _check("artifact-check", CheckKind.ARTIFACT),
        _check("behavior-check", CheckKind.BEHAVIOR),
    )
    coverage = (
        RequirementCoverage(requirement="artifact", check_ids=("artifact-check",)),
        RequirementCoverage(requirement="behavior", check_ids=("behavior-check",)),
    )

    admission = controller.admit_proposal(
        state=state,
        checks=checks,
        coverage=coverage,
        now=10,
    )

    assert admission.accepted is False
    assert admission.phase.reasons == ("finish proposal is not allowed in phase 'executing'",)


def test_contract_type_rejects_a_mismatched_check_kind_during_acceptance() -> None:
    controller = CompletionController(_contract())
    checks = (
        _check("artifact-check", CheckKind.ARTIFACT),
        _check("behavior-check", CheckKind.ARTIFACT),
    )
    coverage = (
        RequirementCoverage(requirement="artifact", check_ids=("artifact-check",)),
        RequirementCoverage(requirement="behavior", check_ids=("behavior-check",)),
    )
    receipts = tuple(_receipt(check, index) for index, check in enumerate(checks, start=1))

    evaluation = controller.evaluate_accept(
        state=_state(phase=RunPhase.REVIEWING),
        checks=receipts,
        proposed_checks=checks,
        coverage=coverage,
        expected_check_ids=tuple(check.id for check in checks),
        attempt_id=1,
        isolation=_isolation(checks, receipts),
        now=10,
    )

    assert evaluation.accepted is False
    assert evaluation.evidence.accepted is False
    assert evaluation.evidence.reasons == (
        "check 'behavior-check' has kind 'artifact'; requirement 'behavior' allows ['behavior']",
    )


def test_acceptance_rejects_a_receipt_for_different_check_content() -> None:
    controller = CompletionController(_contract())
    checks = (
        _check("artifact-check", CheckKind.ARTIFACT),
        _check("behavior-check", CheckKind.BEHAVIOR),
    )
    coverage = (
        RequirementCoverage(requirement="artifact", check_ids=("artifact-check",)),
        RequirementCoverage(requirement="behavior", check_ids=("behavior-check",)),
    )
    receipts = tuple(_receipt(check, index) for index, check in enumerate(checks, start=1))
    receipts = (
        receipts[0].model_copy(update={"script": "different command"}),
        receipts[1],
    )

    evaluation = controller.evaluate_accept(
        state=_state(phase=RunPhase.REVIEWING),
        checks=receipts,
        proposed_checks=checks,
        coverage=coverage,
        expected_check_ids=tuple(check.id for check in checks),
        attempt_id=1,
        isolation=_isolation(checks, receipts),
        now=10,
    )

    assert evaluation.evidence.reasons == (
        "verification receipt does not match proposed check: artifact-check",
    )


def test_each_completion_guard_reports_its_own_reason() -> None:
    options = LoopOptions(
        max_turns=2,
        max_environment_calls=3,
        max_repairs=0,
        max_completion_reviews=1,
        max_wall_time_sec=100,
    )
    contract = _contract(options)
    state = _state(phase=RunPhase.THINKING)
    state.turn_count = 3
    state.environment_call_count = 4
    state.repair_count = 1
    state.completion_review_count = 2

    budget = BudgetGuard().evaluate_completion(contract, state, now=101)
    phase = PhaseGuard().evaluate_completion(state, review_required=True)
    review = ReviewGate().evaluate(
        required=True,
        assessment=SemanticAssessment(
            accepted=False,
            rationale="behavior is not proven",
            findings=("missing output assertion",),
        ),
    )

    assert budget.accepted is False
    assert budget.name == "budget"
    assert len(budget.reasons) == 5
    assert phase.accepted is False
    assert phase.reasons == (
        "completion requires phase 'reviewing' when review is enabled; got 'thinking'",
    )
    assert review.accepted is False
    assert review.reasons == ("behavior is not proven", "missing output assertion")


def test_controller_issues_a_permit_only_for_the_complete_conjunction() -> None:
    options = LoopOptions(enable_completion_review=False)
    contract = _contract(options)
    controller = CompletionController(contract, options=options)
    state = _state()
    state.next_completion_attempt = 2
    checks = (
        _check("artifact-check", CheckKind.ARTIFACT),
        _check("behavior-check", CheckKind.BEHAVIOR),
    )
    coverage = (
        RequirementCoverage(requirement="artifact", check_ids=("artifact-check",)),
        RequirementCoverage(requirement="behavior", check_ids=("behavior-check",)),
    )
    receipts = tuple(_receipt(check, index) for index, check in enumerate(checks, start=1))
    accept = controller.evaluate_accept(
        state=state,
        checks=receipts,
        proposed_checks=checks,
        coverage=coverage,
        expected_check_ids=tuple(check.id for check in checks),
        attempt_id=1,
        isolation=_isolation(checks, receipts),
        now=10,
    )

    completion = controller.evaluate_complete(
        accept,
        assessment=None,
        state=state,
        attempt_id=1,
    )

    assert completion.accepted is True
    assert completion.receipt.accepted is True
    assert controller.authorizes(completion.permit, state)
    assert (
        CompletionController(contract, options=options).authorizes(
            completion.permit,
            state,
        )
        is False
    )

    state.phase = RunPhase.THINKING
    assert controller.authorizes(completion.permit, state) is False


def test_controller_rejects_an_acceptance_from_another_controller() -> None:
    options = LoopOptions(enable_completion_review=False)
    contract = _contract(options)
    controller = CompletionController(contract, options=options)
    other_controller = CompletionController(contract, options=options)
    state = _state()
    checks = (
        _check("artifact-check", CheckKind.ARTIFACT),
        _check("behavior-check", CheckKind.BEHAVIOR),
    )
    coverage = (
        RequirementCoverage(requirement="artifact", check_ids=("artifact-check",)),
        RequirementCoverage(requirement="behavior", check_ids=("behavior-check",)),
    )
    receipts = tuple(_receipt(check, index) for index, check in enumerate(checks, start=1))
    acceptance = other_controller.evaluate_accept(
        state=state,
        checks=receipts,
        proposed_checks=checks,
        coverage=coverage,
        expected_check_ids=tuple(check.id for check in checks),
        attempt_id=1,
        isolation=_isolation(checks, receipts),
        now=10,
    )

    with pytest.raises(RuntimeError, match="current acceptance result"):
        controller.evaluate_complete(
            acceptance,
            assessment=None,
            state=state,
            attempt_id=1,
        )
