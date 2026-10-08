from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest
from conftest import FakeCompletionIsolation, FakeEnvironment, FakeExecResult, ScriptedModel

from evidence_harness.completion_contract import CompletionContract, TaskRequirement
from evidence_harness.completion_control import CompletionController
from evidence_harness.journal import RunJournal
from evidence_harness.protocol import (
    ActionKind,
    AgentDecision,
    CheckKind,
    CommandMode,
    EnvironmentResult,
    FailureKind,
    LoopOptions,
    ModelGateway,
    RequirementCoverage,
    ReviewDecision,
    RunPhase,
    RunState,
    ShellCommand,
    ShellEnvironment,
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
                requirement="REQ-1",
                check_ids=("check-answer",),
            ),
        ),
    )


def _events(tmp_path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]


class _LiveBoundTestLoop(EvidenceLoop):
    async def run(self, instruction: str, environment: ShellEnvironment):
        self._completion_isolation = FakeCompletionIsolation(environment)
        return await super().run(instruction, environment)


def _loop(tmp_path, model: ModelGateway, **option_overrides) -> EvidenceLoop:
    clock = option_overrides.pop("clock", time.monotonic)
    completion_contract = option_overrides.pop("completion_contract", None)
    options = LoopOptions(
        max_turns=option_overrides.pop("max_turns", 12),
        max_environment_calls=option_overrides.pop("max_environment_calls", 20),
        verification_environment_reserve=option_overrides.pop(
            "verification_environment_reserve", 3
        ),
        **option_overrides,
    )
    return _LiveBoundTestLoop(
        model=model,
        journal=RunJournal(tmp_path, inline_bytes=512),
        options=options,
        completion_isolation=FakeCompletionIsolation(FakeEnvironment()),
        completion_contract=completion_contract,
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


class MutableClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class AdvancingEnvironment(FakeEnvironment):
    def __init__(self, clock: MutableClock, script: str, target: float) -> None:
        super().__init__()
        self._clock = clock
        self._script = script
        self._target = target

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        timeout_sec: int | None = None,
    ) -> EnvironmentResult:
        result = await super().exec(command, cwd, timeout_sec)
        if command == self._script:
            self._clock.now = self._target
        return result


class AdvancingCompletionIsolation(FakeCompletionIsolation):
    def __init__(
        self,
        environment: ShellEnvironment,
        clock: MutableClock,
        target: float,
    ) -> None:
        super().__init__(environment)
        self._clock = clock
        self._target = target

    async def verify(self, request, execute):
        result = await super().verify(request, execute)
        self._clock.now = self._target
        return result


class AdvancingModel(ScriptedModel):
    def __init__(
        self,
        decisions: list[AgentDecision],
        clock: MutableClock,
        target: float,
        reviews: list[ReviewDecision] | None = None,
    ) -> None:
        super().__init__(decisions, reviews or ())
        self._clock = clock
        self._target = target

    async def decide(self, prompt: str) -> AgentDecision:
        decision = await super().decide(prompt)
        if len(self.prompts) == 1:
            self._clock.now = self._target
        return decision


class BoundaryTimeoutModel:
    def __init__(self, clock: MutableClock) -> None:
        self._clock = clock
        self._interrupted = False
        self.prompts: list[str] = []

    @property
    def usage(self) -> UsageTotals:
        return UsageTotals(model_calls=len(self.prompts))

    async def decide(self, prompt: str) -> AgentDecision:
        self.prompts.append(prompt)
        if not self._interrupted:
            self._interrupted = True
            self._clock.now = 900.0
            raise TimeoutError
        return _finish()

    async def review(self, prompt: str) -> ReviewDecision:
        del prompt
        return ReviewDecision(verdict="accept", rationale="the receipt proves the value")


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
        completion_isolation=FakeCompletionIsolation(environment),
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


async def test_reviewer_timeout_cannot_fall_back_to_verified(tmp_path) -> None:
    report = await _loop(
        tmp_path,
        BlockingReviewModel(),
        max_model_call_timeout_sec=0.01,
    ).run("Create answer.txt", FakeEnvironment())

    assert report.stop_reason is StopReason.MODEL_FAILURE
    assert report.failure_category == "completion_review_service"
    assert report.environment_calls_used == 2
    assert report.latest_evidence is not None
    assert report.latest_evidence.accepted is False
    event_types = [event["type"] for event in _events(tmp_path)]
    assert event_types.index("completion_review_started") < event_types.index(
        "completion_review_error"
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
    events = _events(tmp_path)
    event_types = [event["type"] for event in events]
    assert events[0]["payload"]["control_audit_version"] == 1
    assert events[0]["payload"]["completion_contract"]["e_req"][0]["id"] == "REQ-1"
    assert event_types.count("executor_turn_started") == 2
    assert event_types.index("agent_decision") < event_types.index("work_batch_started")
    work_index = event_types.index("work_batch_started")
    change_index = next(
        index
        for index, event in enumerate(events)
        if event["type"] == "command_receipt" and event["payload"]["command_id"] == "change"
    )
    review_index = event_types.index("completion_review_started")
    review_result_index = event_types.index("completion_review")
    assert work_index < change_index
    assert review_index < review_result_index
    assert events[work_index]["payload"] == {
        "command_ids": ["change"],
        "started_in_finalization": False,
        "work_epoch": 1,
    }
    assert events[review_index]["payload"] == {
        "attempt_id": 1,
        "review_ordinal": 1,
        "work_epoch": 1,
    }


async def test_finish_cannot_omit_a_fixed_contract_requirement(tmp_path) -> None:
    options = LoopOptions(
        max_turns=12,
        max_environment_calls=20,
        verification_environment_reserve=3,
    )
    contract = CompletionContract.create(
        requirements=(
            TaskRequirement(
                id="artifact",
                statement="answer.txt exists",
                evidence_kinds=(CheckKind.ARTIFACT,),
            ),
            TaskRequirement(
                id="behavior",
                statement="answer.txt contains good",
                evidence_kinds=(CheckKind.BEHAVIOR,),
            ),
        ),
        options=options,
    )
    incomplete = _finish()
    incomplete = incomplete.model_copy(
        update={
            "coverage": (
                RequirementCoverage(
                    requirement="artifact",
                    check_ids=("check-answer",),
                ),
            )
        }
    )
    stop = AgentDecision(
        action=ActionKind.STOP,
        rationale="the fixed contract cannot be satisfied",
        stop_category="blocked",
    )
    environment = FakeEnvironment()

    report = await _loop(
        tmp_path,
        ScriptedModel([incomplete, stop]),
        completion_contract=contract,
    ).run("Create answer.txt and prove its value", environment)

    assert report.stop_reason is StopReason.MODEL_STOPPED
    assert len(environment.calls) == 1
    events = _events(tmp_path)
    admission = next(event for event in events if event["type"] == "completion_proposal_admission")
    assert admission["payload"]["accepted"] is False
    assert admission["payload"]["evidence"]["reasons"] == [
        "contract requirements are not covered: ['behavior']"
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


async def test_reviewer_can_reject_weak_executed_requirement_coverage(tmp_path) -> None:
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
    assert "test -e answer.txt" in [call[0] for call in environment.calls]
    assert "test -e answer.txt" in model.review_prompts[0]
    assert 'test "$(cat answer.txt)" = good' in [call[0] for call in environment.calls]


async def test_reviewer_judges_executed_receipt_before_acceptance(tmp_path) -> None:
    check = "python3 inspect_answer.py"
    model = ScriptedModel(
        [_finish(check), _finish(check)],
        [
            ReviewDecision(
                verdict="repair",
                rationale="the receipt reports the wrong value",
                missing_requirements=("answer.txt must contain good",),
            ),
            ReviewDecision(verdict="accept", rationale="the receipt proves the value"),
        ],
    )
    environment = FakeEnvironment(
        {
            check: [
                FakeExecResult(stdout="expected=good actual=bad\n"),
                FakeExecResult(stdout="expected=good actual=good\n"),
            ]
        }
    )

    report = await _loop(tmp_path, model).run("Write good to answer.txt", environment)

    assert report.stop_reason is StopReason.VERIFIED
    assert report.repairs_used == 1
    assert "expected=good actual=bad" in model.review_prompts[0]
    assert "expected=good actual=good" in model.review_prompts[1]
    assert report.latest_evidence is not None
    assert report.latest_evidence.accepted is True
    assert report.latest_evidence.semantic_assessment is not None
    assert report.latest_evidence.semantic_assessment.accepted is True


async def test_review_budget_exhaustion_never_bypasses_semantic_review(tmp_path) -> None:
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

    assert report.stop_reason is StopReason.BUDGET_EXHAUSTED
    assert report.failure_category == "completion_review_budget"
    assert report.repairs_used == 1
    assert len(model.prompts) == 2
    assert len(model.review_prompts) == 2
    assert [call[0] for call in environment.calls].count(check) == 2
    assert report.latest_evidence is not None
    assert report.latest_evidence.accepted is False


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


async def test_finalization_blocks_late_exploration_but_allows_finish(tmp_path) -> None:
    model = ScriptedModel(
        [
            _execute("printf good > answer.txt", command_id="write-answer"),
            _execute("touch too-late", command_id="late-exploration"),
            _finish('test "$(cat answer.txt)" = good'),
        ],
        [ReviewDecision(verdict="accept", rationale="the receipt proves the value")],
    )
    environment = FakeEnvironment()

    report = await _loop(tmp_path, model, max_turns=4).run(
        "Write good to answer.txt",
        environment,
    )

    assert report.stop_reason is StopReason.VERIFIED
    assert "touch too-late" not in [call[0] for call in environment.calls]
    assert '"phase": "finalizing"' in model.prompts[1]
    assert '"allowed_actions": [' in model.prompts[1]
    assert '"finish"' in model.prompts[1]
    assert '"execute"' not in model.prompts[1].split('"allowed_actions": [', 1)[1].split("]", 1)[0]


async def test_wall_time_reserve_starts_finalization_before_turn_reserve(tmp_path) -> None:
    clock = MutableClock()
    initial_change = "printf good > answer.txt"
    model = ScriptedModel(
        [
            _execute(initial_change, command_id="write-answer"),
            _execute("touch too-late", command_id="late-exploration"),
            _finish('test "$(cat answer.txt)" = good'),
        ],
        [ReviewDecision(verdict="accept", rationale="the receipt proves the value")],
    )
    environment = AdvancingEnvironment(clock, initial_change, target=900.0)

    report = await _loop(
        tmp_path,
        model,
        max_turns=12,
        max_wall_time_sec=1_000,
        clock=clock,
    ).run("Write good to answer.txt", environment)

    assert report.stop_reason is StopReason.VERIFIED
    assert "touch too-late" not in [call[0] for call in environment.calls]
    assert '"finalization_triggers": [\n      "wall_clock"\n    ]' in model.prompts[1]
    assert '"finalization_wall_time_reserve_sec": 100.0' in model.prompts[1]
    events = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    finalization = next(event for event in events if event["type"] == "finalization_started")
    assert finalization["payload"]["triggers"] == ["wall_clock"]
    assert finalization["payload"]["wall_time_remaining_sec"] == 100.0
    assert finalization["payload"]["wall_time_reserve_sec"] == 100.0


async def test_budget_expiry_after_isolation_skips_review_and_repair(tmp_path) -> None:
    clock = MutableClock()
    environment = FakeEnvironment()
    model = ScriptedModel(
        [_finish()],
        [ReviewDecision(verdict="accept", rationale="must not be called")],
    )
    options = LoopOptions(
        max_turns=4,
        max_environment_calls=5,
        max_wall_time_sec=60,
    )
    loop = EvidenceLoop(
        model=model,
        journal=RunJournal(tmp_path, inline_bytes=512),
        options=options,
        completion_isolation=AdvancingCompletionIsolation(
            environment,
            clock,
            target=61.0,
        ),
        clock=clock,
    )

    report = await loop.run("Create answer.txt", environment)

    assert report.stop_reason is StopReason.BUDGET_EXHAUSTED
    assert report.repairs_used == 0
    assert model.review_prompts == []
    events = _events(tmp_path)
    guard = next(event for event in events if event["type"] == "completion_guard_result")
    assert guard["payload"]["budget"]["accepted"] is False
    assert not any(event["type"] == "repair_admission" for event in events)


async def test_work_command_cannot_consume_wall_time_reserve(tmp_path) -> None:
    clock = MutableClock()
    change = "printf good > answer.txt"
    model = AdvancingModel(
        [_execute(change, command_id="write-answer"), _finish()],
        clock,
        target=850.0,
        reviews=[ReviewDecision(verdict="accept", rationale="the receipt proves the value")],
    )
    environment = FakeEnvironment()

    report = await _loop(
        tmp_path,
        model,
        max_wall_time_sec=1_000,
        clock=clock,
    ).run("Write good to answer.txt", environment)

    assert report.stop_reason is StopReason.VERIFIED
    change_call = next(call for call in environment.calls if call[0] == change)
    assert change_call[2] == 50


async def test_model_call_crossing_wall_boundary_rechecks_allowed_actions(tmp_path) -> None:
    clock = MutableClock()
    late_change = "touch too-late"
    model = AdvancingModel(
        [_execute(late_change, command_id="late-change"), _finish()],
        clock,
        target=900.0,
        reviews=[ReviewDecision(verdict="accept", rationale="the receipt proves the value")],
    )
    environment = FakeEnvironment()

    report = await _loop(
        tmp_path,
        model,
        max_wall_time_sec=1_000,
        clock=clock,
    ).run("Ensure answer.txt is ready", environment)

    assert report.stop_reason is StopReason.VERIFIED
    assert late_change not in [call[0] for call in environment.calls]
    assert '"phase": "thinking"' in model.prompts[0]
    assert '"phase": "finalizing"' in model.prompts[1]


async def test_model_timeout_at_wall_boundary_transitions_to_finalization(tmp_path) -> None:
    clock = MutableClock()
    model = BoundaryTimeoutModel(clock)

    report = await _loop(
        tmp_path,
        model,
        max_wall_time_sec=1_000,
        clock=clock,
    ).run("Ensure answer.txt is ready", FakeEnvironment())

    assert report.stop_reason is StopReason.VERIFIED
    assert '"phase": "thinking"' in model.prompts[0]
    assert '"phase": "finalizing"' in model.prompts[1]
    events = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    interrupted = next(
        event for event in events if event["type"] == "model_decision_interrupted_for_finalization"
    )
    assert interrupted["payload"]["triggers"] == ["wall_clock"]


def test_model_timeout_preserves_wall_time_reserve(tmp_path) -> None:
    clock = MutableClock()
    clock.now = 850.0
    loop = _loop(
        tmp_path,
        ScriptedModel(()),
        max_wall_time_sec=1_000,
        clock=clock,
    )
    state = RunState(
        instruction="finish before the deadline",
        phase=RunPhase.THINKING,
        started_monotonic=0.0,
        deadline_monotonic=1_000.0,
    )

    assert loop._model_call_timeout_sec(state, preserve_finalization_reserve=True) == 50.0
    state.finalization_started = True
    assert loop._model_call_timeout_sec(state, preserve_finalization_reserve=True) == 145.0


async def test_work_batch_stops_when_a_command_reaches_wall_boundary(tmp_path) -> None:
    clock = MutableClock()
    first_change = "touch first"
    decision = AgentDecision(
        action=ActionKind.EXECUTE,
        rationale="apply both changes",
        plan=("write the artifacts", "verify them"),
        commands=(
            ShellCommand(
                id="first",
                script=first_change,
                purpose="write the first artifact",
                mode=CommandMode.CHANGE,
            ),
            ShellCommand(
                id="second",
                script="touch second",
                purpose="write the second artifact",
                mode=CommandMode.CHANGE,
            ),
        ),
    )
    model = ScriptedModel(
        [decision, _finish()],
        [ReviewDecision(verdict="accept", rationale="the receipt proves the value")],
    )
    environment = AdvancingEnvironment(clock, first_change, target=900.0)

    report = await _loop(
        tmp_path,
        model,
        max_wall_time_sec=1_000,
        clock=clock,
    ).run("Write the requested artifacts", environment)

    assert report.stop_reason is StopReason.VERIFIED
    assert "touch second" not in [call[0] for call in environment.calls]
    events = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    interrupted = next(
        event for event in events if event["type"] == "work_batch_interrupted_for_finalization"
    )
    assert interrupted["payload"]["commands_completed"] == 1
    assert interrupted["payload"]["commands_remaining"] == 1


async def test_finalization_allows_one_repair_batch_then_requires_finish(tmp_path) -> None:
    check = 'test "$(cat answer.txt)" = good'
    model = ScriptedModel(
        [
            _execute("printf bad > answer.txt", command_id="write-bad"),
            _finish(check),
            _execute("printf good > answer.txt", command_id="repair-answer"),
            _finish(check),
        ],
        [
            ReviewDecision(verdict="repair", rationale="the value is still bad"),
            ReviewDecision(verdict="accept", rationale="the value is now good"),
        ],
    )
    environment = FakeEnvironment()

    report = await _loop(tmp_path, model, max_turns=4).run(
        "Write good to answer.txt",
        environment,
    )

    assert report.stop_reason is StopReason.VERIFIED
    assert report.repairs_used == 1
    assert "printf good > answer.txt" in [call[0] for call in environment.calls]
    assert '"execute"' in model.prompts[2].split('"allowed_actions": [', 1)[1].split("]", 1)[0]
    assert '"execute"' not in model.prompts[3].split('"allowed_actions": [', 1)[1].split("]", 1)[0]


async def test_finalization_supersedes_a_pending_replan(tmp_path) -> None:
    failed_changes = [
        _execute(f"change-{index}", command_id=f"change-{index}") for index in range(3)
    ]
    model = ScriptedModel(
        [*failed_changes, _finish()],
        [ReviewDecision(verdict="accept", rationale="the receipt proves completion")],
    )
    environment = FakeEnvironment(
        {f"change-{index}": [FakeExecResult(return_code=1)] for index in range(3)}
    )

    report = await _loop(
        tmp_path,
        model,
        max_turns=6,
        max_recoveries=1,
    ).run("Create answer.txt", environment)

    assert report.stop_reason is StopReason.VERIFIED
    assert '"phase": "finalizing"' in model.prompts[-1]
    assert '"recovery_directive": null' in model.prompts[-1]


async def test_failed_change_batches_accumulate_stagnation(tmp_path) -> None:
    commands = [_execute(f"change-{index}", command_id=f"change-{index}") for index in range(3)]
    environment = FakeEnvironment(
        {f"change-{index}": [FakeExecResult(return_code=1, stderr="failed")] for index in range(3)}
    )

    report = await _loop(
        tmp_path,
        ScriptedModel(commands),
        max_recoveries=0,
        max_turns=8,
    ).run("Make the requested change", environment)

    assert report.stop_reason is StopReason.DOOM_LOOP
    assert report.recoveries_used == 0
    assert len(environment.calls) == 4


@pytest.mark.parametrize("max_repairs", [0, 1, 2, 4])
async def test_max_repairs_grants_exactly_n_cycles(tmp_path, max_repairs: int) -> None:
    check = "test -s answer.txt"
    attempt_count = max_repairs + 1
    model = ScriptedModel([_finish(check) for _ in range(attempt_count)])
    environment = FakeEnvironment(
        {check: [FakeExecResult(return_code=1, stderr="missing") for _ in range(attempt_count)]}
    )

    report = await _loop(
        tmp_path,
        model,
        max_repairs=max_repairs,
        max_completion_reviews=10,
        max_turns=12,
    ).run("Create answer.txt", environment)

    assert report.stop_reason is StopReason.BUDGET_EXHAUSTED
    assert report.failure_category == "completion_repair_budget"
    assert report.repairs_used == max_repairs
    assert len(model.prompts) == attempt_count
    assert model.review_prompts == []


async def test_review_findings_fit_the_full_review_schema(tmp_path) -> None:
    missing = tuple(f"missing requirement {index}" for index in range(20))
    suggestions = tuple(f"suggested check {index}" for index in range(5))
    model = ScriptedModel(
        [_finish()],
        [
            ReviewDecision(
                verdict="repair",
                rationale="the evidence is incomplete",
                missing_requirements=missing,
                suggested_checks=suggestions,
            )
        ],
    )

    report = await _loop(
        tmp_path,
        model,
        max_repairs=0,
    ).run("Create answer.txt", FakeEnvironment())

    assert report.stop_reason is StopReason.BUDGET_EXHAUSTED
    assert report.latest_evidence is not None
    assert report.latest_evidence.semantic_assessment is not None
    assert len(report.latest_evidence.semantic_assessment.findings) == 25


async def test_finalization_model_calls_are_not_thinking_entries(tmp_path) -> None:
    model = ScriptedModel(
        [_finish()],
        [ReviewDecision(verdict="accept", rationale="the receipt proves completion")],
    )

    report = await _loop(tmp_path, model, max_turns=1).run(
        "Ensure answer.txt is ready",
        FakeEnvironment(),
    )

    assert report.stop_reason is StopReason.VERIFIED
    events = _events(tmp_path)
    event_types = [event["type"] for event in events]
    assert event_types.count("finalization_started") == 1
    assert "executor_turn_started" not in event_types
    assert event_types.index("finalization_started") < event_types.index("agent_decision")


def test_recovery_entry_is_unique_per_open_episode(tmp_path) -> None:
    loop = _loop(
        tmp_path,
        ScriptedModel(()),
        max_recoveries=2,
    )
    state = RunState(
        instruction="recover",
        phase=RunPhase.THINKING,
        started_monotonic=0,
        deadline_monotonic=1_000,
        stagnant_batches=3,
    )

    loop._apply_recovery_policy(state)
    loop._apply_recovery_policy(state)
    loop._handle_replan(
        state,
        AgentDecision(
            action=ActionKind.REPLAN,
            rationale="try another path",
            plan=("inspect a different input",),
        ),
    )
    state.stagnant_batches = 3
    loop._apply_recovery_policy(state)

    recoveries = [event for event in _events(tmp_path) if event["type"] == "recovery_required"]
    assert [event["payload"]["recovery_ordinal"] for event in recoveries] == [1, 2]
    assert all(event["payload"]["work_epoch"] == 0 for event in recoveries)


def test_verified_finish_rejects_an_illegal_phase_even_with_a_controller(tmp_path) -> None:
    options = LoopOptions(
        max_turns=12,
        max_environment_calls=20,
        verification_environment_reserve=3,
    )
    loop = _loop(tmp_path, ScriptedModel(()))
    controller = CompletionController(
        CompletionContract.from_instruction("finish safely", options),
        options=options,
    )
    state = RunState(
        instruction="finish safely",
        phase=RunPhase.THINKING,
        started_monotonic=0,
        deadline_monotonic=1_000,
    )

    with pytest.raises(RuntimeError, match="not allowed in phase"):
        loop._finish(
            state,
            StopReason.VERIFIED,
            completion_controller=controller,
        )
