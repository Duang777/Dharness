from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest
from conftest import FakeCompletionIsolation, FakeEnvironment, FakeExecResult, ScriptedModel

from evidence_harness.completion_contract import CompletionContract, TaskRequirement
from evidence_harness.completion_isolation import (
    CheckExecutor,
    CompletionIsolationRequest,
    CompletionIsolationResult,
)
from evidence_harness.journal import RunJournal
from evidence_harness.model_client import ModelProtocolError, ModelServiceError
from evidence_harness.protocol import (
    ActionKind,
    AgentDecision,
    CheckKind,
    CommandMode,
    CommandReceipt,
    LoopOptions,
    RequirementCoverage,
    ReviewDecision,
    RunReport,
    ShellCommand,
    ShellEnvironment,
    StopReason,
    VerificationCheck,
)
from evidence_harness.run_loop import EvidenceLoop
from evidence_harness_mutation import load_state_prefix
from evidence_harness_mutation.control_invariants import ControlAuditReport, audit_control_trace

_INSTRUCTION = "Write good to answer.txt"
_SOURCE_COMMIT = "0" * 40
_OPTIONS = LoopOptions(max_turns=12, max_environment_calls=20)
_TWO_REPAIRS = replace(_OPTIONS, max_repairs=2, max_completion_reviews=3)
_ACCEPT = ReviewDecision(verdict="accept", rationale="the receipts prove the requirement")
_REPAIR = ReviewDecision(
    verdict="repair",
    rationale="the receipts do not prove the requirement",
    missing_requirements=("answer.txt must contain good",),
)


class _JournaledIsolation(FakeCompletionIsolation):
    """Journals the lifecycle events the Docker backend writes, which offline I4 audits."""

    def __init__(self, environment: ShellEnvironment, journal: RunJournal) -> None:
        super().__init__(environment)
        self._journal = journal

    async def verify(
        self,
        request: CompletionIsolationRequest,
        execute: CheckExecutor,
    ) -> CompletionIsolationResult:
        self._journal.append(
            "completion_isolation_started",
            {
                "attempt_id": request.attempt_id,
                "work_epoch": request.work_epoch,
                "backend": "test-isolation-v1",
                "check_ids": [check.id for check in request.checks],
            },
        )
        ordinals = iter(range(1, len(request.checks) + 1))

        async def journaled(
            check: VerificationCheck,
            environment: ShellEnvironment,
            deadline_monotonic: float,
            candidate_digest: str,
        ) -> CommandReceipt:
            self._journal.append(
                "completion_check_started",
                {"attempt_id": request.attempt_id, "check_id": check.id, "ordinal": next(ordinals)},
            )
            return await execute(check, environment, deadline_monotonic, candidate_digest)

        result = await super().verify(request, journaled)
        self._journal.append(
            "completion_candidate_committed",
            {
                "attempt_id": request.attempt_id,
                "candidate_image_id": result.evidence.candidate_image_id,
            },
        )
        for record in result.evidence.checks:
            self._journal.append(
                "completion_check_isolated",
                record.model_copy(update={"disposed": False}),
            )
        return result


class _FailingReviewModel(ScriptedModel):
    def __init__(self, decisions: list[AgentDecision], error: Exception) -> None:
        super().__init__(decisions)
        self._error = error

    async def review(self, prompt: str) -> ReviewDecision:
        raise self._error


def _command(command_id: str, script: str, mode: CommandMode = CommandMode.CHANGE) -> ShellCommand:
    return ShellCommand(
        id=command_id,
        script=script,
        purpose=f"run {command_id}",
        cwd="/workspace",
        mode=mode,
    )


def _execute(*commands: ShellCommand) -> AgentDecision:
    return AgentDecision(
        action=ActionKind.EXECUTE,
        rationale="change the candidate",
        plan=("change the candidate", "verify it"),
        commands=commands,
    )


def _check(
    check_id: str = "check-answer",
    script: str = 'test "$(cat answer.txt)" = good',
    *,
    kind: CheckKind = CheckKind.ARTIFACT,
    timeout_sec: int = 120,
) -> VerificationCheck:
    return VerificationCheck(
        id=check_id,
        kind=kind,
        script=script,
        proves=f"{check_id} holds",
        cwd="/workspace",
        timeout_sec=timeout_sec,
    )


def _finish(
    *checks: VerificationCheck,
    coverage: tuple[RequirementCoverage, ...] | None = None,
    summary: str = "answer.txt contains good",
) -> AgentDecision:
    checks = checks or (_check(),)
    return AgentDecision(
        action=ActionKind.FINISH,
        rationale="the candidate is ready for fresh verification",
        summary=summary,
        checks=checks,
        coverage=coverage
        or (RequirementCoverage(requirement="REQ-1", check_ids=tuple(c.id for c in checks)),),
    )


async def _episode(
    tmp_path,
    model: ScriptedModel,
    environment: FakeEnvironment,
    *,
    options: LoopOptions = _OPTIONS,
    requirements: tuple[TaskRequirement, ...] | None = None,
) -> tuple[RunReport, ControlAuditReport]:
    journal = RunJournal(tmp_path, inline_bytes=512)
    report = await EvidenceLoop(
        model=model,
        journal=journal,
        options=options,
        completion_isolation=_JournaledIsolation(environment, journal),
        completion_contract=(
            None
            if requirements is None
            else CompletionContract.create(requirements=requirements, options=options)
        ),
    ).run(_INSTRUCTION, environment)
    data = journal.events_path.read_bytes()
    prefix = load_state_prefix(
        data,
        source_commit=_SOURCE_COMMIT,
        expected_journal_sha256=hashlib.sha256(data).hexdigest(),
    )
    return report, audit_control_trace(prefix)


def _assert_verified_clean(report: RunReport, audit: ControlAuditReport) -> None:
    assert report.stop_reason is StopReason.VERIFIED
    assert audit.violations == ()
    assert audit.passed


def _assert_unverified_clean(audit: ControlAuditReport) -> None:
    assert audit.verified_attempts == 0
    assert audit.violations == ()


async def test_work_after_a_passing_check_requires_a_new_passing_check(tmp_path) -> None:
    check = _check()
    model = ScriptedModel(
        [
            _execute(_command("write-good", "printf good > answer.txt")),
            _finish(check),
            _execute(_command("rewrite", "printf bad > answer.txt")),
            _finish(check),
            _execute(_command("restore", "printf good > answer.txt")),
            _finish(check),
        ],
        [_REPAIR, _ACCEPT],
    )
    environment = FakeEnvironment(
        {
            check.script: [
                FakeExecResult(),
                FakeExecResult(return_code=1, stderr="answer.txt contains bad"),
                FakeExecResult(),
            ]
        }
    )

    report, audit = await _episode(tmp_path, model, environment, options=_TWO_REPAIRS)

    _assert_verified_clean(report, audit)
    assert report.repairs_used == 2
    assert report.latest_evidence is not None
    assert report.latest_evidence.work_epoch == 3


async def test_failing_completion_check_returns_to_repair(tmp_path) -> None:
    check = _check()
    model = ScriptedModel(
        [
            _execute(_command("write-bad", "printf bad > answer.txt")),
            _finish(check),
            _execute(_command("write-good", "printf good > answer.txt")),
            _finish(check),
        ],
        [_ACCEPT, _ACCEPT],
    )
    environment = FakeEnvironment(
        {check.script: [FakeExecResult(return_code=1, stderr="wrong value"), FakeExecResult()]}
    )

    report, audit = await _episode(tmp_path, model, environment)

    _assert_verified_clean(report, audit)
    assert report.repairs_used == 1
    assert len(model.review_prompts) == 1


async def test_every_proposed_check_runs_before_verified(tmp_path) -> None:
    requirements = (
        TaskRequirement(
            id="artifact", statement="answer.txt exists", evidence_kinds=(CheckKind.ARTIFACT,)
        ),
        TaskRequirement(
            id="behavior", statement="the program prints good", evidence_kinds=(CheckKind.BEHAVIOR,)
        ),
    )
    quick = _check("quick", "test -s answer.txt", timeout_sec=10)
    slow = _check("slow", "python3 run_suite.py", kind=CheckKind.BEHAVIOR, timeout_sec=240)
    proposal = _finish(
        quick,
        slow,
        coverage=(
            RequirementCoverage(requirement="artifact", check_ids=("quick",)),
            RequirementCoverage(requirement="behavior", check_ids=("slow",)),
        ),
    )
    model = ScriptedModel(
        [proposal, _execute(_command("fix", "printf good > answer.txt")), proposal],
        [_ACCEPT],
    )
    environment = FakeEnvironment(
        {slow.script: [FakeExecResult(return_code=1, stderr="1 failed"), FakeExecResult()]}
    )

    report, audit = await _episode(tmp_path, model, environment, requirements=requirements)

    _assert_verified_clean(report, audit)
    assert report.repairs_used == 1
    assert report.latest_evidence is not None
    assert [receipt.command_id for receipt in report.latest_evidence.checks] == ["quick", "slow"]


@pytest.mark.parametrize(
    ("error", "failure_category"),
    [
        (ModelProtocolError("review output is not valid JSON"), "completion_review_protocol"),
        (ModelServiceError("review endpoint returned 503"), "completion_review_service"),
        (TimeoutError("review exceeded its deadline"), "completion_review_service"),
    ],
    ids=["protocol_error", "service_error", "timeout"],
)
async def test_required_review_failure_is_never_verified(
    tmp_path, error: Exception, failure_category: str
) -> None:
    model = _FailingReviewModel([_finish()], error)

    report, audit = await _episode(tmp_path, model, FakeEnvironment())

    assert report.stop_reason is StopReason.MODEL_FAILURE
    assert report.failure_category == failure_category
    assert report.latest_evidence is not None
    assert report.latest_evidence.accepted is False
    _assert_unverified_clean(audit)


async def test_repeated_finish_after_review_rejection_needs_a_new_acceptance(tmp_path) -> None:
    model = ScriptedModel([_finish(), _finish(), _finish()], [_REPAIR, _REPAIR, _ACCEPT])

    report, audit = await _episode(tmp_path, model, FakeEnvironment(), options=_TWO_REPAIRS)

    _assert_verified_clean(report, audit)
    assert report.repairs_used == 2
    assert len(model.review_prompts) == 3


async def test_turn_budget_finalization_still_requires_semantic_review(tmp_path) -> None:
    model = ScriptedModel(
        [
            _execute(_command("write", "printf good > answer.txt")),
            _finish(),
            _execute(_command("fix", "printf good > answer.txt && sync")),
            _finish(),
        ],
        [_REPAIR, _ACCEPT],
    )

    report, audit = await _episode(
        tmp_path, model, FakeEnvironment(), options=replace(_OPTIONS, max_turns=4)
    )

    _assert_verified_clean(report, audit)
    assert report.repairs_used == 1
    assert report.turns_used == 4
    assert len(model.review_prompts) == 2


async def test_agent_reported_test_run_cannot_replace_the_isolated_check(tmp_path) -> None:
    check = _check("tests", "pytest -q", kind=CheckKind.BEHAVIOR)
    model = ScriptedModel(
        [
            _execute(
                _command("run-tests", "pytest -q", CommandMode.OBSERVE),
                _command("refactor", "printf bad > answer.txt"),
            ),
            _finish(check, summary="pytest -q passed in my last run"),
            _execute(_command("fix", "printf good > answer.txt")),
            _finish(check, summary="pytest -q passed in my last run"),
        ],
        [_ACCEPT, _ACCEPT],
    )
    environment = FakeEnvironment(
        {
            check.script: [
                FakeExecResult(stdout="1 passed\n"),
                FakeExecResult(return_code=1, stdout="1 failed\n"),
                FakeExecResult(stdout="1 passed\n"),
            ]
        }
    )

    report, audit = await _episode(tmp_path, model, environment)

    _assert_verified_clean(report, audit)
    assert report.repairs_used == 1
    assert len(model.review_prompts) == 1


async def test_failed_work_batch_is_not_counted_as_progress(tmp_path) -> None:
    model = ScriptedModel(
        [
            _execute(_command("broken", "make answer")),
            _execute(_command("write", "printf good > answer.txt")),
            _finish(),
        ],
        [_ACCEPT],
    )
    environment = FakeEnvironment(
        {"make answer": [FakeExecResult(return_code=2, stderr="no rule")]}
    )

    report, audit = await _episode(tmp_path, model, environment)

    _assert_verified_clean(report, audit)
