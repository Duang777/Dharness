from __future__ import annotations

import json
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from conftest import FakeCompletionIsolation, FakeEnvironment, FakeExecResult, ScriptedModel
from mutation.support import _receipt
from test_run_loop import AdvancingModel, MutableClock

from evidence_harness.completion_contract import CompletionContract, TaskRequirement
from evidence_harness.evidence import EvidenceGate
from evidence_harness.journal import RunJournal
from evidence_harness.protocol import (
    AgentDecision,
    CheckKind,
    EnvironmentResult,
    LoopOptions,
    ModelGateway,
    RequirementCoverage,
    ReviewDecision,
    RunReport,
    StopReason,
    VerificationCheck,
)
from evidence_harness.run_loop import EvidenceLoop
from evidence_harness_mutation.historical_cases import (
    ChangeCommandSpec,
    CoverageSpec,
    FailedChangeScenario,
    HistoricalCaseId,
    HistoricalManifest,
    LoopOptionsSpec,
    MaxRepairsScenario,
    ReorderReceiptsScenario,
    ReviewQuotaScenario,
    ReviewReceiptOrderScenario,
    ReviewTimeoutScenario,
    WallTimeScenario,
)

_MANIFEST = HistoricalManifest.model_validate_json(
    (Path(__file__).resolve().parents[2] / "experiments/historical-7/manifest.json").read_bytes()
)
_SCENARIOS = {case.case_id: case.scenario for case in _MANIFEST.cases}


def _scenario[S](case_id: HistoricalCaseId, kind: type[S]) -> S:
    scenario = _SCENARIOS[case_id]
    assert isinstance(scenario, kind)
    return scenario


def _checks(scenario: Any) -> tuple[VerificationCheck, ...]:
    return tuple(VerificationCheck.model_validate(check.model_dump()) for check in scenario.checks)


def _coverage(scenario: Any) -> tuple[RequirementCoverage, ...]:
    return tuple(
        RequirementCoverage.model_validate(item.model_dump()) for item in scenario.coverage
    )


def _finish(scenario: Any) -> AgentDecision:
    return AgentDecision.model_validate(
        {
            "action": "finish",
            "rationale": "the artifact is ready for fresh verification",
            "checks": _checks(scenario),
            "coverage": _coverage(scenario),
            "summary": "verified the requested artifact",
        }
    )


def _execute(command: ChangeCommandSpec) -> AgentDecision:
    return AgentDecision.model_validate(
        {
            "action": "execute",
            "rationale": command.purpose,
            "commands": [{**command.model_dump(), "mode": "change"}],
        }
    )


def _stop(category: str) -> AgentDecision:
    return AgentDecision.model_validate(
        {
            "action": "stop",
            "rationale": "the task cannot proceed",
            "stop_category": category,
            "summary": "stopped after the scripted attempts",
        }
    )


async def _run(
    tmp_path: Path,
    *,
    instruction: str,
    options_spec: LoopOptionsSpec,
    model: ModelGateway,
    environment: FakeEnvironment,
    coverage: tuple[CoverageSpec, ...] = (),
    clock: Callable[[], float] = time.monotonic,
) -> tuple[RunReport, list[dict[str, Any]]]:
    options = LoopOptions(**options_spec.model_dump())
    # The manifest names requirements in prose; the current loop binds coverage to contract ids.
    contract = (
        CompletionContract.create(
            requirements=tuple(
                TaskRequirement(
                    id=item.requirement,
                    statement=instruction,
                    evidence_kinds=tuple(CheckKind),
                )
                for item in coverage
            ),
            options=options,
        )
        if coverage
        else None
    )
    journal = RunJournal(tmp_path, inline_bytes=512)
    report = await EvidenceLoop(
        model=model,
        journal=journal,
        options=options,
        completion_isolation=FakeCompletionIsolation(environment),
        completion_contract=contract,
        clock=clock,
    ).run(instruction, environment)
    events = [json.loads(line) for line in journal.events_path.read_text().splitlines()]
    return report, events


class _FailingReviewModel(ScriptedModel):
    def __init__(self, decisions: list[AgentDecision], error: str) -> None:
        super().__init__(decisions)
        self._error = error

    async def review(self, prompt: str) -> ReviewDecision:
        self.review_prompts.append(prompt)
        raise TimeoutError(self._error)


class _ReceiptJudgingModel(ScriptedModel):
    def __init__(self, decisions: list[AgentDecision], marker: str) -> None:
        super().__init__(decisions)
        self._marker = marker

    async def review(self, prompt: str) -> ReviewDecision:
        self.review_prompts.append(prompt)
        if self._marker in prompt:
            return ReviewDecision(
                verdict="repair",
                rationale="the executed receipt reports the wrong value",
                missing_requirements=("answer.txt must contain good",),
            )
        return ReviewDecision(verdict="accept", rationale="the proposed check looks sufficient")


class _TimedEnvironment(FakeEnvironment):
    def __init__(self, clock: MutableClock, script: str, duration_sec: float) -> None:
        super().__init__()
        self._clock = clock
        self._script = script
        self._duration_sec = duration_sec

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        timeout_sec: int | None = None,
    ) -> EnvironmentResult:
        result = await super().exec(command, cwd, timeout_sec)
        if command == self._script and timeout_sec is not None:
            if timeout_sec < self._duration_sec:
                self._clock.now += timeout_sec
                raise TimeoutError
            self._clock.now += self._duration_sec
        return result


async def test_review_timeout_fallback(tmp_path: Path) -> None:
    scenario = _scenario(HistoricalCaseId.REVIEW_TIMEOUT_FALLBACK, ReviewTimeoutScenario)

    report, events = await _run(
        tmp_path,
        instruction=scenario.instruction,
        options_spec=scenario.options,
        model=_FailingReviewModel([_finish(scenario)], scenario.review_error),
        environment=FakeEnvironment(),
        coverage=scenario.coverage,
    )

    assert report.stop_reason is StopReason.MODEL_FAILURE
    assert report.failure_category == "completion_review_service"
    assert report.latest_evidence is not None
    assert report.latest_evidence.accepted is False
    assert "completion semantic review was not accepted" in (
        report.latest_evidence.rejection_reasons
    )
    assert [event["type"] for event in events].count("completion_review_error") == 1


def test_reorder_check_receipts() -> None:
    scenario = _scenario(HistoricalCaseId.REORDER_CHECK_RECEIPTS, ReorderReceiptsScenario)
    checks = _checks(scenario)
    coverage = _coverage(scenario)
    gate = EvidenceGate(LoopOptions(enable_completion_review=False))
    receipts = tuple(_receipt(check, sequence) for sequence, check in enumerate(checks, start=1))

    def decide(supplied: tuple[Any, ...]) -> Any:
        return gate.decide(
            work_epoch=scenario.work_epoch,
            checks=supplied,
            proposed_checks=checks,
            coverage=coverage,
            expected_check_ids=tuple(check.id for check in checks),
        )

    assert gate.validate_proposal(checks, coverage) == ()
    assert decide(receipts).accepted is True
    reordered = decide(tuple(reversed(receipts)))
    assert reordered.accepted is False
    assert reordered.rejection_reasons


async def test_review_receipt_order(tmp_path: Path) -> None:
    scenario = _scenario(HistoricalCaseId.REVIEW_RECEIPT_ORDER, ReviewReceiptOrderScenario)
    check = scenario.checks[0]
    model = _ReceiptJudgingModel([_finish(scenario)], scenario.receipt_marker)

    report, events = await _run(
        tmp_path,
        instruction=scenario.instruction,
        options_spec=scenario.options,
        model=model,
        environment=FakeEnvironment(
            {check.script: [FakeExecResult(stdout=f"{scenario.receipt_marker}\n")]}
        ),
        coverage=scenario.coverage,
    )

    order = [
        "review" if event["type"] == "completion_review" else "check"
        for event in events
        if event["type"] == "completion_review"
        or (event["type"] == "command_receipt" and event["payload"]["command_id"] == check.id)
    ]
    assert order == ["check", "review"]
    assert len(model.review_prompts) == 1
    assert scenario.receipt_marker in model.review_prompts[0]
    assert report.stop_reason is StopReason.BUDGET_EXHAUSTED
    assert report.failure_category == "completion_repair_budget"
    assert report.latest_evidence is not None
    assert report.latest_evidence.accepted is False


async def test_review_quota_no_bypass(tmp_path: Path) -> None:
    scenario = _scenario(HistoricalCaseId.REVIEW_QUOTA_NO_BYPASS, ReviewQuotaScenario)
    model = ScriptedModel(
        [_finish(scenario) for _ in range(3)],
        [
            ReviewDecision(verdict="repair", rationale=rationale)
            for rationale in scenario.rejection_rationales
        ],
    )

    report, events = await _run(
        tmp_path,
        instruction=scenario.instruction,
        options_spec=scenario.options,
        model=model,
        environment=FakeEnvironment(),
        coverage=scenario.coverage,
    )

    assert report.stop_reason is StopReason.BUDGET_EXHAUSTED
    assert report.failure_category == "completion_review_budget"
    assert len(model.review_prompts) == scenario.options.max_completion_reviews
    assert not any(
        event["type"] == "verification_receipt" and event["payload"]["accepted"] for event in events
    )


async def test_failed_change_progress(tmp_path: Path) -> None:
    scenario = _scenario(HistoricalCaseId.FAILED_CHANGE_PROGRESS, FailedChangeScenario)
    model = ScriptedModel(
        [
            *(_execute(command) for command in scenario.failed_commands),
            _stop(scenario.terminal_stop_category),
        ]
    )
    environment = FakeEnvironment(
        {
            command.script: [FakeExecResult(return_code=1, stderr=scenario.failure_stderr)]
            for command in scenario.failed_commands
        }
    )

    report, events = await _run(
        tmp_path,
        instruction=scenario.instruction,
        options_spec=scenario.options,
        model=model,
        environment=environment,
    )

    batches = [event["payload"] for event in events if event["type"] == "work_batch_finished"]
    assert [batch["failed_command_ids"] for batch in batches] == [
        [command.id] for command in scenario.failed_commands
    ]
    assert [batch["progressed"] for batch in batches] == [False, False, False]
    assert report.stop_reason is StopReason.DOOM_LOOP
    assert len(model.prompts) == len(scenario.failed_commands)


async def test_max_repairs_exact(tmp_path: Path) -> None:
    scenario = _scenario(HistoricalCaseId.MAX_REPAIRS_EXACT, MaxRepairsScenario)
    attempts = scenario.options.max_repairs + 2
    check = scenario.checks[0]
    model = ScriptedModel([_finish(scenario) for _ in range(attempts)])
    environment = FakeEnvironment(
        {
            check.script: [
                FakeExecResult(return_code=1, stderr=scenario.failure_stderr)
                for _ in range(attempts)
            ]
        }
    )

    report, events = await _run(
        tmp_path,
        instruction=scenario.instruction,
        options_spec=scenario.options,
        model=model,
        environment=environment,
        coverage=scenario.coverage,
    )

    admissions = [
        event["payload"]["accepted"] for event in events if event["type"] == "repair_admission"
    ]
    assert admissions == [True] * scenario.options.max_repairs + [False]
    assert report.stop_reason is StopReason.BUDGET_EXHAUSTED
    assert report.failure_category == "completion_repair_budget"
    assert report.repairs_used == scenario.options.max_repairs
    assert len(model.prompts) == scenario.options.max_repairs + 1


async def test_wall_time_command_deadline(tmp_path: Path) -> None:
    scenario = _scenario(HistoricalCaseId.WALL_TIME_COMMAND_DEADLINE, WallTimeScenario)
    command = scenario.command
    max_wall = scenario.options.max_wall_time_sec
    # The reserve is the frozen 10% from the fixed revision, not the candidate's own constant.
    work_deadline = max_wall - max_wall * 0.1
    clock = MutableClock()
    environment = _TimedEnvironment(clock, command.script, scenario.command_duration_sec)

    report, events = await _run(
        tmp_path,
        instruction=scenario.instruction,
        options_spec=scenario.options,
        model=AdvancingModel(
            [_execute(command), _stop(scenario.terminal_stop_category)],
            clock,
            target=scenario.model_clock_target_sec,
        ),
        environment=environment,
        clock=clock,
    )

    work_call = next(call for call in environment.calls if call[0] == command.script)
    work_receipt = next(
        event["payload"]
        for event in events
        if event["type"] == "command_receipt" and event["payload"]["command_id"] == command.id
    )
    assert work_call[2] == work_deadline - scenario.model_clock_target_sec
    assert work_receipt["failure"] == "timeout"
    assert clock.now == work_deadline
    assert report.stop_reason is StopReason.MODEL_STOPPED
    assert report.failure_category == f"model_{scenario.terminal_stop_category}"
