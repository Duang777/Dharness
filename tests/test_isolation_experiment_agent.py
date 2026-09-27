from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from conftest import FakeCompletionIsolation, FakeEnvironment, ScriptedModel
from harbor.models.agent.context import AgentContext

from evidence_harness.isolation_experiment_agent import (
    CompletionIsolationExperimentAgent,
    load_completion_candidate,
)
from evidence_harness.policy import command_fingerprint, observation_fingerprint
from evidence_harness.protocol import (
    ActionKind,
    AgentDecision,
    CheckKind,
    CommandMode,
    OutputExcerpt,
    RequirementCoverage,
    ReviewDecision,
    ShellCommand,
    VerificationCheck,
)


def _finish_decision() -> AgentDecision:
    return AgentDecision(
        action=ActionKind.FINISH,
        rationale="the artifact is ready",
        summary="created answer.txt",
        checks=(
            VerificationCheck(
                id="answer",
                kind=CheckKind.ARTIFACT,
                script="test -s answer.txt",
                proves="answer.txt exists",
                cwd="/app",
            ),
        ),
        coverage=(
            RequirementCoverage(
                requirement="create answer.txt",
                check_ids=("answer",),
            ),
        ),
    )


def _write_corpus(
    path: Path,
    source_bytes: bytes,
    *,
    later_change_receipts: list[object] | None = None,
) -> str:
    case_id = "a" * 64
    decision = _finish_decision()
    source_lines = source_bytes.splitlines(keepends=True)
    proposal_line = 1
    raw_proposal = source_lines[0] if source_lines else b""
    for index, raw_line in enumerate(source_lines, start=1):
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError:
            continue
        if (
            isinstance(event, dict)
            and event.get("type") == "agent_decision"
            and isinstance(event.get("payload"), dict)
            and event["payload"].get("action") == "finish"
        ):
            proposal_line = index
            raw_proposal = raw_line
            break
    proposal = decision.model_dump(mode="json")
    del proposal["action"]
    path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "case_id": case_id,
                        "task_name": "example",
                        "sources": {
                            "journal": {
                                "sha256": hashlib.sha256(source_bytes).hexdigest(),
                            }
                        },
                        "attempts": [
                            {
                                "ordinal": 1,
                                "later_change_receipts": later_change_receipts or [],
                                "proposal_event": {
                                    "line": proposal_line,
                                    "line_sha256": hashlib.sha256(raw_proposal).hexdigest(),
                                },
                                "proposal": proposal,
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return case_id


def test_load_completion_candidate_binds_journal_and_proposal(tmp_path: Path) -> None:
    source_bytes = (
        json.dumps(
            {
                "type": "agent_decision",
                "payload": _finish_decision().model_dump(mode="json"),
            }
        )
        + "\n"
    ).encode()
    corpus_path = tmp_path / "corpus.json"
    case_id = _write_corpus(corpus_path, source_bytes)

    candidate = load_completion_candidate(
        corpus_path,
        case_id=case_id,
        attempt_ordinal=1,
        source_journal_bytes=source_bytes,
    )

    assert candidate.task_name == "example"
    assert candidate.source_journal_sha256 == hashlib.sha256(source_bytes).hexdigest()
    assert (
        candidate.proposal_event_line_sha256
        == hashlib.sha256(source_bytes.splitlines(keepends=True)[0]).hexdigest()
    )
    assert candidate.decision.action is ActionKind.FINISH
    assert [check.id for check in candidate.decision.checks] == ["answer"]


def test_load_completion_candidate_rejects_source_drift(tmp_path: Path) -> None:
    corpus_path = tmp_path / "corpus.json"
    case_id = _write_corpus(corpus_path, b"expected")

    with pytest.raises(ValueError, match="source journal hash does not match"):
        load_completion_candidate(
            corpus_path,
            case_id=case_id,
            attempt_ordinal=1,
            source_journal_bytes=b"changed",
        )


def test_load_completion_candidate_rejects_later_changes(tmp_path: Path) -> None:
    source_bytes = b"source"
    corpus_path = tmp_path / "corpus.json"
    case_id = _write_corpus(
        corpus_path,
        source_bytes,
        later_change_receipts=[{"sequence": 2}],
    )

    with pytest.raises(ValueError, match="later change receipts"):
        load_completion_candidate(
            corpus_path,
            case_id=case_id,
            attempt_ordinal=1,
            source_journal_bytes=source_bytes,
        )


def test_load_completion_candidate_rejects_proposal_drift(tmp_path: Path) -> None:
    source_bytes = (
        json.dumps(
            {
                "type": "agent_decision",
                "payload": _finish_decision().model_dump(mode="json"),
            }
        )
        + "\n"
    ).encode()
    corpus_path = tmp_path / "corpus.json"
    case_id = _write_corpus(corpus_path, source_bytes)
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    corpus["cases"][0]["attempts"][0]["proposal"]["checks"][0]["script"] = "true"
    corpus_path.write_text(json.dumps(corpus), encoding="utf-8")

    with pytest.raises(ValueError, match="proposal does not match the source journal"):
        load_completion_candidate(
            corpus_path,
            case_id=case_id,
            attempt_ordinal=1,
            source_journal_bytes=source_bytes,
        )


async def test_experiment_agent_replays_then_runs_recorded_finish(
    tmp_path: Path,
    monkeypatch,
) -> None:
    command = ShellCommand(
        id="write-answer",
        script="printf done > /app/answer.txt",
        purpose="create answer.txt",
        cwd="/app",
        mode=CommandMode.CHANGE,
    )
    decision = AgentDecision(
        action=ActionKind.EXECUTE,
        rationale="create the requested file",
        commands=(command,),
    )
    empty = OutputExcerpt(
        head="",
        tail="",
        total_bytes=0,
        omitted_bytes=0,
        sha256=hashlib.sha256(b"").hexdigest(),
    )
    command_hash = command_fingerprint(command.script, command.cwd)
    receipt = {
        "sequence": 1,
        "command_id": command.id,
        "script": command.script,
        "purpose": command.purpose,
        "cwd": command.cwd,
        "mode": command.mode,
        "work_epoch": 1,
        "return_code": 0,
        "failure": None,
        "duration_sec": 0.1,
        "stdout": empty.model_dump(mode="json"),
        "stderr": empty.model_dump(mode="json"),
        "command_fingerprint": command_hash,
        "observation_fingerprint": observation_fingerprint(
            command_hash,
            0,
            None,
            empty.sha256,
            empty.sha256,
        ),
    }
    source = tmp_path / "source.jsonl"
    source_bytes = (
        "\n".join(
            (
                json.dumps(
                    {
                        "type": "agent_decision",
                        "payload": decision.model_dump(mode="json"),
                    }
                ),
                json.dumps({"type": "command_receipt", "payload": receipt}),
                json.dumps(
                    {
                        "type": "agent_decision",
                        "payload": _finish_decision().model_dump(mode="json"),
                    }
                ),
                json.dumps({"type": "run_finished", "payload": {}}),
            )
        )
        + "\n"
    ).encode()
    source.write_bytes(source_bytes)
    corpus_path = tmp_path / "corpus.json"
    case_id = _write_corpus(corpus_path, source_bytes)

    model = ScriptedModel(
        [],
        [ReviewDecision(verdict="accept", rationale="the receipt covers the task")],
    )
    monkeypatch.setattr(
        "evidence_harness.isolation_experiment_agent.LiteLLMModelGateway",
        lambda **_: model,
    )
    environment = FakeEnvironment()
    isolation = FakeCompletionIsolation(environment)
    monkeypatch.setattr(
        "evidence_harness.isolation_experiment_agent.completion_isolation_for_harbor",
        lambda **_: isolation,
    )
    agent = CompletionIsolationExperimentAgent(
        logs_dir=tmp_path / "logs",
        model_name="openai/test",
        corpus_path=corpus_path,
        journal_path=source,
        case_id=case_id,
    )
    context = AgentContext()

    await agent.setup(environment)
    await agent.run("Create answer.txt", environment, context)

    assert environment.calls[0][0] == command.script
    assert "=== working directory ===" in environment.calls[1][0]
    assert environment.calls[2][0] == "test -s answer.txt"
    assert context.metadata is not None
    metadata = context.metadata["completion_isolation_experiment"]
    assert metadata["replay_command_count"] == 1
    assert metadata["stop_reason"] == "verified"
    assert metadata["latest_evidence"]["accepted"] is True
