from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from evidence_harness.completion_contract import CompletionContract, TaskRequirement
from evidence_harness.completion_control import (
    BudgetGuard,
    CompletionController,
    PhaseGuard,
    ReviewGate,
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
    IsolatedCheckRun,
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
    SemanticAssessment,
    SourceAttestation,
    VerificationCheck,
)
from evidence_harness.stub_harness_adapter import StubHarnessAdapter

_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
_CANDIDATE_DIGEST = hashlib.sha256(b"candidate-rootfs").hexdigest()


def _state(
    *,
    phase: RunPhase = RunPhase.FINALIZING,
    next_completion_attempt: int = 1,
    candidate_digest: str | None = None,
    counters: CompletionCounters | None = None,
) -> CompletionState:
    return CompletionState(
        phase=phase,
        work_epoch=1,
        next_completion_attempt=next_completion_attempt,
        candidate_digest=candidate_digest,
        deadline_monotonic=100,
        counters=counters
        or CompletionCounters(
            turns=0,
            environment_calls=0,
            repairs=0,
            recoveries=0,
            completion_reviews=0,
        ),
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


def _checks() -> tuple[VerificationCheck, ...]:
    return (
        _check("artifact-check", CheckKind.ARTIFACT),
        _check("behavior-check", CheckKind.BEHAVIOR),
    )


def _coverage() -> tuple[RequirementCoverage, ...]:
    return (
        RequirementCoverage(requirement="artifact", check_ids=("artifact-check",)),
        RequirementCoverage(requirement="behavior", check_ids=("behavior-check",)),
    )


def _controller(
    *,
    checks: tuple[VerificationCheck, ...] | None = None,
    coverage: tuple[RequirementCoverage, ...] | None = None,
    options: LoopOptions | None = None,
    state: CompletionState | None = None,
) -> CompletionController:
    configured_options = options or LoopOptions()
    configured_checks = checks or _checks()
    view = CompletionTransactionView(
        proposal=CompletionProposal(
            checks=configured_checks,
            coverage=coverage or _coverage(),
            candidate=CurrentCandidate(),
        ),
        contract=_contract(configured_options),
        policy=CompletionPolicy(
            review_required=configured_options.enable_completion_review,
            max_check_timeout_sec=configured_options.max_command_timeout_sec,
        ),
        trace=CompletionTrace(),
        state=state or _state(),
    )
    return CompletionController(
        StubHarnessAdapter(
            view=view,
            candidate_files={"answer.txt": b"ready\n"},
            check_runners={check.id: lambda files: True for check in configured_checks},
        )
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
        attempt_id=1,
        candidate_digest=_CANDIDATE_DIGEST,
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
        candidate_digest=_CANDIDATE_DIGEST,
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


def _run(
    checks: tuple[VerificationCheck, ...],
    receipts: tuple[CommandReceipt, ...],
) -> IsolatedCheckRun:
    isolation = _isolation(checks, receipts)
    return IsolatedCheckRun(
        snapshot=CandidateSnapshot(
            identity=CandidateIdentity(
                algorithm="sha256",
                value=isolation.candidate_image_id,
            ),
            candidate_digest=_CANDIDATE_DIGEST,
            attempt_id=1,
            work_epoch=1,
        ),
        receipts=receipts,
        isolation=isolation,
    )


def _completion_state(*, review_required: bool) -> CompletionState:
    return _state(
        phase=RunPhase.REVIEWING if review_required else RunPhase.VERIFYING,
        next_completion_attempt=2,
        candidate_digest=_CANDIDATE_DIGEST,
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
    checks = (_check("artifact-check", CheckKind.ARTIFACT),)
    coverage = (RequirementCoverage(requirement="artifact", check_ids=("artifact-check",)),)
    controller = _controller(checks=checks, coverage=coverage)

    result = controller.validate_proposal()

    assert result.accepted is False
    assert result.reasons == ("contract requirements are not covered: ['behavior']",)


def test_finish_proposal_is_rejected_in_an_illegal_phase() -> None:
    controller = _controller(state=_state(phase=RunPhase.EXECUTING))

    admission = controller.admit(now=10)

    assert admission.accepted is False
    assert admission.phase.reasons == ("finish proposal is not allowed in phase 'executing'",)


def test_contract_type_rejects_a_mismatched_check_kind_during_acceptance() -> None:
    checks = (
        _check("artifact-check", CheckKind.ARTIFACT),
        _check("behavior-check", CheckKind.ARTIFACT),
    )
    receipts = tuple(_receipt(check, index) for index, check in enumerate(checks, start=1))
    controller = _controller(checks=checks)

    evaluation = controller.evaluate_accept(
        _run(checks, receipts),
        state=_completion_state(review_required=True),
        now=10,
    )

    assert evaluation.accepted is False
    assert evaluation.evidence.reasons == (
        "check 'behavior-check' has kind 'artifact'; requirement 'behavior' allows ['behavior']",
    )


def test_acceptance_rejects_a_receipt_for_different_check_content() -> None:
    checks = _checks()
    receipts = tuple(_receipt(check, index) for index, check in enumerate(checks, start=1))
    receipts = (
        receipts[0].model_copy(update={"script": "different command"}),
        receipts[1],
    )
    controller = _controller(checks=checks)

    evaluation = controller.evaluate_accept(
        _run(checks, receipts),
        state=_completion_state(review_required=True),
        now=10,
    )

    assert evaluation.evidence.reasons == (
        "verification receipt does not match proposed check: artifact-check",
    )


def test_acceptance_rejects_host_attempt_and_epoch_drift() -> None:
    checks = _checks()
    receipts = tuple(_receipt(check, index) for index, check in enumerate(checks, start=1))
    controller = _controller(checks=checks)
    drifted_state = replace(
        _completion_state(review_required=True),
        work_epoch=2,
        next_completion_attempt=3,
    )

    evaluation = controller.evaluate_accept(
        _run(checks, receipts),
        state=drifted_state,
        now=10,
    )

    assert evaluation.phase.reasons == (
        "completion attempt state does not match",
        "completion work epoch changed during verification",
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
    state = _state(
        phase=RunPhase.THINKING,
        counters=CompletionCounters(
            turns=3,
            environment_calls=4,
            repairs=1,
            recoveries=0,
            completion_reviews=2,
        ),
    )

    budget = BudgetGuard().evaluate_completion(contract, state, now=101)
    phase = PhaseGuard().evaluate_completion(
        state,
        review_required=True,
        attempt_id=1,
        work_epoch=1,
    )
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
        "completion attempt state does not match",
    )
    assert review.accepted is False
    assert review.reasons == ("behavior is not proven", "missing output assertion")


def test_controller_issues_a_permit_only_for_the_complete_conjunction() -> None:
    options = LoopOptions(enable_completion_review=False)
    checks = _checks()
    receipts = tuple(_receipt(check, index) for index, check in enumerate(checks, start=1))
    controller = _controller(checks=checks, options=options)
    state = _completion_state(review_required=False)
    acceptance = controller.evaluate_accept(
        _run(checks, receipts),
        state=state,
        now=10,
    )

    completion = controller.evaluate_complete(
        acceptance,
        assessment=None,
        state=state,
    )

    assert completion.accepted is True
    assert completion.receipt.accepted is True
    assert controller.authorizes(completion.permit, state)
    assert _controller(options=options).authorizes(completion.permit, state) is False
    assert (
        controller.authorizes(
            completion.permit,
            replace(state, phase=RunPhase.THINKING),
        )
        is False
    )


def test_controller_rejects_an_acceptance_from_another_controller() -> None:
    options = LoopOptions(enable_completion_review=False)
    checks = _checks()
    receipts = tuple(_receipt(check, index) for index, check in enumerate(checks, start=1))
    controller = _controller(options=options)
    other_controller = _controller(options=options)
    state = _completion_state(review_required=False)
    acceptance = other_controller.evaluate_accept(
        _run(checks, receipts),
        state=state,
        now=10,
    )

    with pytest.raises(RuntimeError, match="current acceptance result"):
        controller.evaluate_complete(
            acceptance,
            assessment=None,
            state=state,
        )
