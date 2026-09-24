from __future__ import annotations

from conftest import FakeEnvironment, FakeExecResult, ScriptedModel

from evidence_harness.journal import RunJournal
from evidence_harness.protocol import (
    ActionKind,
    AgentDecision,
    CheckKind,
    CommandMode,
    LoopOptions,
    RequirementCoverage,
    ReviewDecision,
    ShellCommand,
    StopReason,
    VerificationCheck,
)
from evidence_harness.run_loop import EvidenceLoop


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


def _loop(tmp_path, model: ScriptedModel, **option_overrides) -> EvidenceLoop:
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
    )


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
