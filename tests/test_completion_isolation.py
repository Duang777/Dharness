from __future__ import annotations

import json

from conftest import FakeCompletionIsolation, FakeEnvironment, ScriptedModel

from evidence_harness.completion_isolation import (
    CompletionIsolationError,
    IsolationFailureKind,
)
from evidence_harness.journal import RunJournal
from evidence_harness.protocol import (
    ActionKind,
    AgentDecision,
    CheckKind,
    LoopOptions,
    RequirementCoverage,
    ReviewDecision,
    StopReason,
    VerificationCheck,
)
from evidence_harness.run_loop import EvidenceLoop


def _finish(script: str = "test -s answer.txt") -> AgentDecision:
    return AgentDecision(
        action=ActionKind.FINISH,
        rationale="verify the saved candidate",
        summary="verified answer.txt",
        checks=(
            VerificationCheck(
                id="check-answer",
                kind=CheckKind.ARTIFACT,
                script=script,
                proves="answer.txt has the requested content",
                cwd="/workspace",
            ),
        ),
        coverage=(
            RequirementCoverage(
                requirement="answer.txt has the requested content",
                check_ids=("check-answer",),
            ),
        ),
    )


async def test_completion_check_runs_only_in_isolated_environment(tmp_path) -> None:
    live = FakeEnvironment()
    isolated = FakeEnvironment()
    isolation = FakeCompletionIsolation(isolated)
    model = ScriptedModel(
        [_finish()],
        [ReviewDecision(verdict="accept", rationale="the receipt proves the value")],
    )
    loop = EvidenceLoop(
        model=model,
        journal=RunJournal(tmp_path, inline_bytes=512),
        options=LoopOptions(max_turns=4, max_environment_calls=5),
        completion_isolation=isolation,
    )

    report = await loop.run("Create answer.txt", live)

    assert report.stop_reason is StopReason.VERIFIED
    assert [call[0] for call in live.calls] != ["test -s answer.txt"]
    assert [call[0] for call in isolated.calls] == ["test -s answer.txt"]
    assert report.environment_calls_used == 2
    assert report.latest_evidence is not None
    assert report.latest_evidence.isolation is not None


async def test_repeated_finish_gets_new_attempt_and_global_sequence(tmp_path) -> None:
    isolation = FakeCompletionIsolation(FakeEnvironment())
    model = ScriptedModel(
        [_finish(), _finish()],
        [
            ReviewDecision(verdict="repair", rationale="more evidence is required"),
            ReviewDecision(verdict="accept", rationale="the receipt proves the value"),
        ],
    )
    loop = EvidenceLoop(
        model=model,
        journal=RunJournal(tmp_path, inline_bytes=512),
        options=LoopOptions(max_turns=4, max_environment_calls=5),
        completion_isolation=isolation,
    )

    report = await loop.run("Create answer.txt", FakeEnvironment())

    assert report.stop_reason is StopReason.VERIFIED
    assert [request.attempt_id for request in isolation.requests] == [1, 2]
    evidence = report.latest_evidence
    assert evidence is not None
    assert [receipt.sequence for receipt in evidence.checks] == [3]
    assert report.environment_calls_used == 3


async def test_isolation_failure_stops_before_semantic_review(tmp_path) -> None:
    isolation = FakeCompletionIsolation(
        FakeEnvironment(),
        failure=CompletionIsolationError(
            IsolationFailureKind.UNSUPPORTED,
            "the environment has a task-defined mount",
            attempt_id=1,
        ),
    )
    model = ScriptedModel(
        [_finish()],
        [ReviewDecision(verdict="accept", rationale="must not be called")],
    )
    loop = EvidenceLoop(
        model=model,
        journal=RunJournal(tmp_path, inline_bytes=512),
        options=LoopOptions(max_turns=4, max_environment_calls=5),
        completion_isolation=isolation,
    )

    report = await loop.run("Create answer.txt", FakeEnvironment())

    assert report.stop_reason is StopReason.INFRA_FAILURE
    assert report.failure_category == "completion_isolation_unsupported"
    assert model.review_prompts == []
    events = [
        json.loads(line)
        for line in (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert events[-2]["type"] == "completion_isolation_failed"
    assert events[-1]["type"] == "run_finished"


async def test_isolation_allows_a_write_producing_completion_check(tmp_path) -> None:
    script = "python3 verify.py > /tmp/verification-output"
    isolated = FakeEnvironment()
    isolation = FakeCompletionIsolation(isolated)
    model = ScriptedModel(
        [_finish(script)],
        [ReviewDecision(verdict="accept", rationale="the receipt proves the behavior")],
    )
    loop = EvidenceLoop(
        model=model,
        journal=RunJournal(tmp_path, inline_bytes=512),
        options=LoopOptions(max_turns=4, max_environment_calls=5),
        completion_isolation=isolation,
    )

    report = await loop.run("Verify answer.txt behavior", FakeEnvironment())

    assert report.stop_reason is StopReason.VERIFIED
    assert isolated.calls[0][0] == script
