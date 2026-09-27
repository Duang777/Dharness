from __future__ import annotations

import asyncio
import time

import pytest
from conftest import FakeEnvironment, FakeExecResult, ScriptedModel

from evidence_harness.journal import RunJournal
from evidence_harness.protocol import (
    ActionKind,
    AgentDecision,
    CheckKind,
    CommandMode,
    FailureKind,
    LoopOptions,
    ModelGateway,
    RequirementCoverage,
    ReviewDecision,
    RunPhase,
    RunState,
    ShellCommand,
    StopReason,
    UsageTotals,
    VerificationCheck,
)
from evidence_harness.run_loop import EvidenceLoop
from evidence_harness.shell import CommandRunner


def _execute(script: str, *, command_id: str = "change") -> AgentDecision:
    return AgentDecision(
        action=ActionKind.EXECUTE,
        rationale="apply the requested state",
        plan=("write the artifact", "verify it"),
        commands=(
            ShellCommand(
                id=command_id,
                script=script,
                purpose="write the requested artifact",
                cwd="/workspace",
                mode=CommandMode.CHANGE,
            ),
        ),
    )


def _finish(script: str = "test -s answer.txt") -> AgentDecision:
    return AgentDecision(
        action=ActionKind.FINISH,
        rationale="the artifact is ready for a fresh check",
        summary="created the requested artifact",
        checks=(
            VerificationCheck(
                id="check-answer",
                kind=CheckKind.ARTIFACT,
                script=script,
                proves="the requested artifact has the expected value",
                cwd="/workspace",
            ),
        ),
        coverage=(
            RequirementCoverage(
                requirement="create the requested artifact",
                check_ids=("check-answer",),
            ),
        ),
    )


def _loop(tmp_path, model: ModelGateway, **option_overrides) -> EvidenceLoop:
    clock = option_overrides.pop("clock", time.monotonic)
    options = LoopOptions(
        max_turns=option_overrides.pop("max_turns", 12),
        max_environment_calls=option_overrides.pop("max_environment_calls", 20),
        verification_environment_reserve=option_overrides.pop(
            "verification_environment_reserve", 3
        ),
        **option_overrides,
    )
    return EvidenceLoop(
        model=model,
        journal=RunJournal(tmp_path, inline_bytes=512),
        options=options,
        clock=clock,
    )


class BlockingModel:
    @property
    def usage(self) -> UsageTotals:
        return UsageTotals()

    async def decide(self, prompt: str) -> AgentDecision:
        del prompt
        await asyncio.Event().wait()
        raise AssertionError("unreachable")

    async def review(self, prompt: str) -> ReviewDecision:
        del prompt
        raise AssertionError("unreachable")


class BlockingReviewModel:
    @property
    def usage(self) -> UsageTotals:
        return UsageTotals()

    async def decide(self, prompt: str) -> AgentDecision:
        del prompt
        return _finish()

    async def review(self, prompt: str) -> ReviewDecision:
        del prompt
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class BlockingEnvironment:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None, int | None]] = []
        self.cancelled = False
        self.finished = False

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        timeout_sec: int | None = None,
    ) -> FakeExecResult:
        self.calls.append((command, cwd, timeout_sec))
        try:
            await asyncio.sleep(0.03)
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        finally:
            self.finished = True
        return FakeExecResult()


class ReleasableEnvironment:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = False

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        timeout_sec: int | None = None,
    ) -> FakeExecResult:
        del command, cwd, timeout_sec
        self.started.set()
        try:
            await self.release.wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        return FakeExecResult()


async def test_model_call_cannot_exceed_its_timeout(tmp_path) -> None:
    report = await _loop(
        tmp_path,
        BlockingModel(),
        max_model_call_timeout_sec=0.01,
    ).run("Create answer.txt", FakeEnvironment())

    assert report.stop_reason is StopReason.MODEL_FAILURE
    assert report.failure_category == "model_service"
    assert report.environment_calls_used == 1


async def test_wall_budget_stops_before_starting_an_unusable_model_call(tmp_path) -> None:
    times = iter((0.0, 0.0, 0.0, 0.0, 1796.0))
    model = ScriptedModel([_finish()])
    loop = _loop(tmp_path, model, clock=lambda: next(times))

    report = await loop.run("Create answer.txt", FakeEnvironment())

    assert report.stop_reason is StopReason.BUDGET_EXHAUSTED
    assert report.failure_category == "harness_control"
    assert report.environment_calls_used == 1
    assert model.prompts == []


async def test_environment_call_cannot_exceed_remaining_wall_time(tmp_path) -> None:
    options = LoopOptions()

    def clock() -> float:
        return 0.0

    environment = BlockingEnvironment()
    journal = RunJournal(tmp_path, inline_bytes=512)
    loop = EvidenceLoop(
        model=ScriptedModel(()),
        journal=journal,
        options=options,
        clock=clock,
    )
    runner = CommandRunner(environment, journal, options, clock)
    state = RunState(
        instruction="run a bounded command",
        phase=RunPhase.EXECUTING,
        started_monotonic=0.0,
        deadline_monotonic=0.01,
    )
    command = ShellCommand(
        id="blocking",
        script="sleep 300",
        purpose="exercise the wall-clock deadline",
        mode=CommandMode.OBSERVE,
        timeout_sec=300,
    )

    receipt = await asyncio.wait_for(
        loop._run_command(state, runner, command),
        timeout=0.2,
    )

    assert receipt.failure is FailureKind.TIMEOUT
    assert environment.calls[0][2] == 1
    assert environment.finished is True
    assert environment.cancelled is False


async def test_external_cancellation_waits_for_environment_cleanup(tmp_path) -> None:
    options = LoopOptions()
    environment = ReleasableEnvironment()
    runner = CommandRunner(
        environment,
        RunJournal(tmp_path, inline_bytes=512),
        options,
    )
    command = ShellCommand(
        id="blocking",
        script="sleep 300",
        purpose="exercise cancellation cleanup",
        mode=CommandMode.OBSERVE,
        timeout_sec=300,
    )

    task = asyncio.create_task(
        runner.execute(command, sequence=1, work_epoch=1),
    )
    await environment.started.wait()
    task.cancel()
    await asyncio.sleep(0)

    assert task.done() is False
    assert environment.cancelled is False

    environment.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert environment.cancelled is False


async def test_completion_check_timeout_is_clamped_to_wall_budget(tmp_path) -> None:
    environment = FakeEnvironment()

    report = await _loop(
        tmp_path,
        ScriptedModel([_finish()]),
        max_wall_time_sec=6,
    ).run("Create answer.txt", environment)

    assert report.stop_reason is StopReason.VERIFIED
    assert environment.calls[-1][2] is not None
    assert environment.calls[-1][2] <= 6


async def test_reviewer_timeout_falls_back_to_verification_checks(tmp_path) -> None:
    report = await _loop(
        tmp_path,
        BlockingReviewModel(),
        max_model_call_timeout_sec=0.01,
    ).run("Create answer.txt", FakeEnvironment())

    assert report.stop_reason is StopReason.VERIFIED
    assert report.environment_calls_used == 2


async def test_success_requires_fresh_check_after_change(tmp_path) -> None:
    model = ScriptedModel(
        [_execute("printf good > answer.txt"), _finish()],
        [ReviewDecision(verdict="accept", rationale="all requirements are checked")],
    )
    environment = FakeEnvironment()

    report = await _loop(tmp_path, model).run(
        "Create a non-empty answer.txt",
        environment,
    )

    assert report.stop_reason is StopReason.VERIFIED
    assert report.latest_evidence is not None
    assert report.latest_evidence.accepted is True
    assert report.latest_evidence.work_epoch == 1
    assert report.latest_evidence.checks[0].sequence == 3
    assert [call[0] for call in environment.calls[1:]] == [
        "printf good > answer.txt",
        "test -s answer.txt",
    ]


async def test_failed_verification_returns_to_repair(tmp_path) -> None:
    check = 'test "$(cat answer.txt)" = good'
    model = ScriptedModel(
        [
            _execute("printf bad > answer.txt", command_id="write-bad"),
            _finish(check),
            _execute("printf good > answer.txt", command_id="write-good"),
            _finish(check),
        ],
        [
            ReviewDecision(verdict="accept", rationale="the assertion is specific"),
            ReviewDecision(verdict="accept", rationale="the assertion is specific"),
        ],
    )
    environment = FakeEnvironment(
        {
            check: [
                FakeExecResult(return_code=1, stderr="wrong value"),
                FakeExecResult(return_code=0),
            ]
        }
    )

    report = await _loop(tmp_path, model).run("Write good to answer.txt", environment)

    assert report.stop_reason is StopReason.VERIFIED
    assert report.repairs_used == 1
    assert report.latest_evidence is not None
    assert report.latest_evidence.work_epoch == 2
    assert any("verification commands failed" in error for error in model.prompts[-1].splitlines())


async def test_reviewer_can_reject_weak_requirement_coverage(tmp_path) -> None:
    model = ScriptedModel(
        [_finish("test -e answer.txt"), _finish('test "$(cat answer.txt)" = good')],
        [
            ReviewDecision(
                verdict="repair",
                rationale="existence does not prove the required value",
                missing_requirements=("answer.txt must contain good",),
            ),
            ReviewDecision(verdict="accept", rationale="the value is asserted"),
        ],
    )
    environment = FakeEnvironment()

    report = await _loop(tmp_path, model).run("Write good to answer.txt", environment)

    assert report.stop_reason is StopReason.VERIFIED
    assert report.repairs_used == 1
    assert "test -e answer.txt" not in [call[0] for call in environment.calls]
    assert 'test "$(cat answer.txt)" = good' in [call[0] for call in environment.calls]


async def test_default_review_budget_verifies_after_two_rejections(tmp_path) -> None:
    check = 'test "$(cat answer.txt)" = good'
    model = ScriptedModel(
        [_finish(check), _finish(check), _finish(check)],
        [
            ReviewDecision(verdict="repair", rationale="first evidence gap"),
            ReviewDecision(verdict="repair", rationale="second evidence gap"),
        ],
    )
    environment = FakeEnvironment()

    report = await _loop(tmp_path, model).run("Write good to answer.txt", environment)

    assert report.stop_reason is StopReason.VERIFIED
    assert report.repairs_used == 2
    assert len(model.review_prompts) == 2
    assert check in [call[0] for call in environment.calls]


async def test_forbidden_command_never_reaches_environment(tmp_path) -> None:
    forbidden = _execute("cat /tests/test_task.py", command_id="read-verifier")
    stop = AgentDecision(
        action=ActionKind.STOP,
        rationale="the forbidden path is not needed",
        summary="stopping",
        stop_category="blocked",
    )
    model = ScriptedModel([forbidden, stop])
    environment = FakeEnvironment()

    report = await _loop(tmp_path, model).run("Inspect a safe file", environment)

    assert report.stop_reason is StopReason.MODEL_STOPPED
    assert all("/tests" not in call[0] for call in environment.calls)


async def test_repeated_commands_end_in_bounded_stop(tmp_path) -> None:
    repeated = AgentDecision(
        action=ActionKind.EXECUTE,
        rationale="check again",
        plan=("inspect status",),
        commands=(
            ShellCommand(
                id="status",
                script="cat status.txt",
                purpose="inspect status",
                cwd="/workspace",
                mode=CommandMode.OBSERVE,
            ),
        ),
    )
    model = ScriptedModel([repeated, repeated, repeated, repeated])
    environment = FakeEnvironment({"cat status.txt": [FakeExecResult(stdout="pending\n")]})

    report = await _loop(
        tmp_path,
        model,
        max_recoveries=0,
        max_turns=8,
    ).run("Wait for status to become ready", environment)

    assert report.stop_reason is StopReason.DOOM_LOOP
    assert len(environment.calls) == 2


async def test_work_cannot_consume_verification_reserve(tmp_path) -> None:
    model = ScriptedModel(
        [_execute("touch should-not-run"), _finish("test -s answer.txt")],
        [ReviewDecision(verdict="accept", rationale="the task state is checked")],
    )
    environment = FakeEnvironment()

    report = await _loop(
        tmp_path,
        model,
        max_environment_calls=4,
        verification_environment_reserve=3,
    ).run("Ensure answer.txt is non-empty", environment)

    assert report.stop_reason is StopReason.VERIFIED
    assert "touch should-not-run" not in [call[0] for call in environment.calls]
