from __future__ import annotations

import json

import pytest
from conftest import FakeEnvironment
from harbor.models.agent.context import AgentContext

from evidence_harness.protocol import ActionKind, AgentDecision, CommandMode, ShellCommand
from evidence_harness.replay_agent import JournalReplayAgent, load_replay_batches


def _execute(*commands: ShellCommand) -> AgentDecision:
    return AgentDecision(
        action=ActionKind.EXECUTE,
        rationale="replay recorded commands",
        commands=commands,
    )


def _event(event_type: str, decision: AgentDecision) -> str:
    return json.dumps(
        {
            "type": event_type,
            "payload": decision.model_dump(mode="json"),
        }
    )


async def test_replay_agent_executes_only_agent_decisions(tmp_path) -> None:
    inspect = ShellCommand(
        id="inspect",
        script="git status --short",
        purpose="inspect repository",
        cwd="/workspace",
        mode=CommandMode.OBSERVE,
    )
    change = ShellCommand(
        id="change",
        script="touch recovered.txt",
        purpose="restore the artifact",
        cwd="/workspace",
        mode=CommandMode.CHANGE,
    )
    source = tmp_path / "source.jsonl"
    source.write_text(
        "\n".join(
            (
                _event("model_decision", _execute(inspect)),
                _event("agent_decision", _execute(inspect)),
                _event("agent_decision", _execute(change)),
            )
        )
        + "\n",
        encoding="utf-8",
    )
    environment = FakeEnvironment()
    context = AgentContext()
    agent = JournalReplayAgent(
        logs_dir=tmp_path / "logs",
        journal_path=source,
    )

    await agent.setup(environment)
    await agent.run("ignored during replay", environment, context)

    assert [call[0] for call in environment.calls] == [
        "git status --short",
        "touch recovered.txt",
    ]
    assert context.metadata is not None
    assert context.metadata["evidence_harness_replay"]["command_count"] == 2
    assert context.metadata["evidence_harness_replay"]["completed"] is True


def test_replay_rejects_redacted_commands() -> None:
    command = ShellCommand(
        id="redacted",
        script="curl -H 'Authorization: Bearer [REDACTED]' https://example.invalid",
        purpose="call a service",
        mode=CommandMode.OBSERVE,
    )
    source = (_event("agent_decision", _execute(command)) + "\n").encode()

    with pytest.raises(ValueError, match="redacted command"):
        load_replay_batches(source)


def test_replay_requires_an_execute_decision() -> None:
    source = (
        json.dumps(
            {
                "type": "run_finished",
                "payload": {"stop_reason": "verified"},
            }
        )
        + "\n"
    ).encode()

    with pytest.raises(ValueError, match="no executable"):
        load_replay_batches(source)
