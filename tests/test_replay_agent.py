from __future__ import annotations

import hashlib
import json

import pytest
from conftest import FakeEnvironment, FakeExecResult
from harbor.models.agent.context import AgentContext

from evidence_harness.policy import command_fingerprint, observation_fingerprint
from evidence_harness.protocol import (
    ActionKind,
    AgentDecision,
    CheckKind,
    CommandMode,
    FailureKind,
    RequirementCoverage,
    ShellCommand,
    VerificationCheck,
)
from evidence_harness.replay_agent import (
    JournalReplayAgent,
    load_recorded_replay_batches,
    load_replay_batches,
)


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


def _receipt(
    command: ShellCommand,
    *,
    return_code: int | None,
    failure: FailureKind | None = None,
    sequence: int = 1,
    work_epoch: int = 1,
) -> str:
    recorded_failure = failure
    if recorded_failure is None and return_code != 0:
        recorded_failure = FailureKind.NONZERO
    empty_sha256 = hashlib.sha256(b"").hexdigest()
    command_hash = command_fingerprint(command.script, command.cwd)
    return json.dumps(
        {
            "type": "command_receipt",
            "payload": {
                "sequence": sequence,
                "command_id": command.id,
                "script": command.script,
                "purpose": command.purpose,
                "cwd": command.cwd,
                "mode": command.mode.value,
                "work_epoch": work_epoch,
                "return_code": return_code,
                "failure": recorded_failure.value if recorded_failure is not None else None,
                "duration_sec": 0.1,
                "stdout": {
                    "head": "",
                    "tail": "",
                    "total_bytes": 0,
                    "omitted_bytes": 0,
                    "sha256": empty_sha256,
                    "archive_path": None,
                },
                "stderr": {
                    "head": "",
                    "tail": "",
                    "total_bytes": 0,
                    "omitted_bytes": 0,
                    "sha256": empty_sha256,
                    "archive_path": None,
                },
                "command_fingerprint": command_hash,
                "observation_fingerprint": observation_fingerprint(
                    command_hash,
                    return_code,
                    recorded_failure.value if recorded_failure is not None else None,
                    empty_sha256,
                    empty_sha256,
                ),
            },
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
        legacy_replay_all_decisions=True,
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


async def test_replay_can_continue_past_failures_recorded_in_source(tmp_path) -> None:
    failed = ShellCommand(
        id="failed-probe",
        script="false",
        purpose="probe unavailable input",
        cwd="/workspace",
        mode=CommandMode.OBSERVE,
    )
    unexecuted = ShellCommand(
        id="not-run",
        script="printf wrong",
        purpose="would follow the failed command",
        cwd="/workspace",
        mode=CommandMode.CHANGE,
    )
    recovered = ShellCommand(
        id="recovered",
        script="touch recovered.txt",
        purpose="restore the artifact",
        cwd="/workspace",
        mode=CommandMode.CHANGE,
    )
    source = tmp_path / "source.jsonl"
    source.write_text(
        "\n".join(
            (
                _event("agent_decision", _execute(failed, unexecuted)),
                _receipt(failed, return_code=1),
                _event("agent_decision", _execute(recovered)),
                _receipt(recovered, return_code=0, sequence=2, work_epoch=2),
            )
        )
        + "\n",
        encoding="utf-8",
    )
    environment = FakeEnvironment(
        responses={"false": [FakeExecResult(return_code=1)]},
    )
    context = AgentContext()
    agent = JournalReplayAgent(
        logs_dir=tmp_path / "logs",
        journal_path=source,
    )

    await agent.setup(environment)
    await agent.run("ignored during replay", environment, context)

    assert [call[0] for call in environment.calls] == ["false", "touch recovered.txt"]
    assert context.metadata is not None
    metadata = context.metadata["evidence_harness_replay"]
    assert metadata["command_count"] == 2
    assert metadata["recorded_failure_count"] == 1
    assert metadata["replay_mode"] == "recorded_receipts"
    assert metadata["completed"] is True


async def test_schema2_replay_ignores_isolated_completion_checks(tmp_path) -> None:
    change = ShellCommand(
        id="change",
        script="touch recovered.txt",
        purpose="restore the artifact",
        cwd="/workspace",
        mode=CommandMode.CHANGE,
    )
    isolated_check = ShellCommand(
        id="check",
        script="printf verification-only > /tmp/isolated-output",
        purpose="verify only in the disposable child",
        cwd="/workspace",
        mode=CommandMode.OBSERVE,
    )
    finish = AgentDecision(
        action=ActionKind.FINISH,
        rationale="verify the completed artifact",
        checks=(
            VerificationCheck(
                id=isolated_check.id,
                kind=CheckKind.BEHAVIOR,
                script=isolated_check.script,
                proves=isolated_check.purpose,
                cwd=isolated_check.cwd,
            ),
        ),
        coverage=(
            RequirementCoverage(
                requirement="the artifact is complete",
                check_ids=(isolated_check.id,),
            ),
        ),
    )
    source = tmp_path / "source.jsonl"
    source.write_text(
        "\n".join(
            (
                json.dumps(
                    {
                        "type": "run_started",
                        "payload": {"journal_schema_version": 2},
                    }
                ),
                _event("agent_decision", _execute(change)),
                _receipt(change, return_code=0),
                _event("agent_decision", finish),
                json.dumps(
                    {
                        "type": "completion_isolation_started",
                        "payload": {"attempt_id": 1, "work_epoch": 1},
                    }
                ),
                _receipt(
                    isolated_check,
                    return_code=0,
                    sequence=2,
                    work_epoch=1,
                ),
                json.dumps(
                    {
                        "type": "completion_check_isolated",
                        "payload": {"check_id": "check"},
                    }
                ),
                json.dumps(
                    {
                        "type": "completion_check_disposed",
                        "payload": {"check_id": "check"},
                    }
                ),
                json.dumps(
                    {
                        "type": "run_finished",
                        "payload": {"stop_reason": "verified"},
                    }
                ),
            )
        )
        + "\n",
        encoding="utf-8",
    )
    environment = FakeEnvironment()
    agent = JournalReplayAgent(
        logs_dir=tmp_path / "logs",
        journal_path=source,
    )

    await agent.setup(environment)
    await agent.run("ignored during replay", environment, AgentContext())

    assert [call[0] for call in environment.calls] == ["touch recovered.txt"]


async def test_replay_still_rejects_new_failures(tmp_path) -> None:
    command = ShellCommand(
        id="originally-successful",
        script="false",
        purpose="reproduce a successful source command",
        cwd="/workspace",
        mode=CommandMode.OBSERVE,
    )
    source = tmp_path / "source.jsonl"
    source.write_text(
        "\n".join(
            (
                _event("agent_decision", _execute(command)),
                _receipt(command, return_code=0),
            )
        )
        + "\n",
        encoding="utf-8",
    )
    environment = FakeEnvironment(
        responses={"false": [FakeExecResult(return_code=1)]},
    )
    agent = JournalReplayAgent(
        logs_dir=tmp_path / "logs",
        journal_path=source,
    )

    await agent.setup(environment)
    with pytest.raises(RuntimeError, match="originally-successful"):
        await agent.run("ignored during replay", environment, AgentContext())


async def test_replay_requires_the_recorded_failure_kind(tmp_path) -> None:
    command = ShellCommand(
        id="original-timeout",
        script="false",
        purpose="reproduce a timed-out source command",
        cwd="/workspace",
        mode=CommandMode.OBSERVE,
    )
    source = tmp_path / "source.jsonl"
    source.write_text(
        "\n".join(
            (
                _event("agent_decision", _execute(command)),
                _receipt(
                    command,
                    return_code=None,
                    failure=FailureKind.TIMEOUT,
                ),
            )
        )
        + "\n",
        encoding="utf-8",
    )
    environment = FakeEnvironment(
        responses={"false": [FakeExecResult(return_code=1)]},
    )
    agent = JournalReplayAgent(
        logs_dir=tmp_path / "logs",
        journal_path=source,
    )

    await agent.setup(environment)
    with pytest.raises(RuntimeError, match="original-timeout"):
        await agent.run("ignored during replay", environment, AgentContext())


async def test_replay_rejects_success_when_source_recorded_failure(tmp_path) -> None:
    command = ShellCommand(
        id="unexpected-success",
        script="true",
        purpose="reproduce a failed source command",
        cwd="/workspace",
        mode=CommandMode.OBSERVE,
    )
    source = tmp_path / "source.jsonl"
    source.write_text(
        "\n".join(
            (
                _event("agent_decision", _execute(command)),
                _receipt(command, return_code=1),
            )
        )
        + "\n",
        encoding="utf-8",
    )
    agent = JournalReplayAgent(
        logs_dir=tmp_path / "logs",
        journal_path=source,
    )

    await agent.setup(FakeEnvironment())
    with pytest.raises(RuntimeError, match="unexpected-success"):
        await agent.run("ignored during replay", FakeEnvironment(), AgentContext())


def test_recorded_replay_rejects_truncated_successful_batch() -> None:
    first = ShellCommand(
        id="completed",
        script="true",
        purpose="complete the first command",
        mode=CommandMode.OBSERVE,
    )
    missing = ShellCommand(
        id="missing",
        script="printf missing",
        purpose="would complete the batch",
        mode=CommandMode.OBSERVE,
    )
    source = (
        "\n".join(
            (
                _event("agent_decision", _execute(first, missing)),
                _receipt(first, return_code=0),
            )
        )
        + "\n"
    ).encode()

    with pytest.raises(ValueError, match="before receipt for 'missing'"):
        load_recorded_replay_batches(source)


def test_recorded_replay_discards_commands_not_run_before_terminal_event() -> None:
    completed = ShellCommand(
        id="completed",
        script="true",
        purpose="complete the first command",
        mode=CommandMode.OBSERVE,
    )
    unexecuted = ShellCommand(
        id="not-run",
        script="printf missing",
        purpose="would exceed the run budget",
        mode=CommandMode.OBSERVE,
    )
    source = (
        "\n".join(
            (
                _event("agent_decision", _execute(completed, unexecuted)),
                _receipt(completed, return_code=0),
                json.dumps(
                    {
                        "type": "run_finished",
                        "payload": {"stop_reason": "budget_exhausted"},
                    }
                ),
            )
        )
        + "\n"
    ).encode()

    [batch] = load_recorded_replay_batches(source)

    assert [step.command.id for step in batch] == ["completed"]


def test_recorded_replay_stops_at_the_terminal_event() -> None:
    completed = ShellCommand(
        id="completed",
        script="true",
        purpose="complete before termination",
        mode=CommandMode.OBSERVE,
    )
    appended = ShellCommand(
        id="appended",
        script="touch appended",
        purpose="must not run after termination",
        mode=CommandMode.CHANGE,
    )
    source = (
        "\n".join(
            (
                _event("agent_decision", _execute(completed)),
                _receipt(completed, return_code=0),
                json.dumps(
                    {
                        "type": "run_finished",
                        "payload": {"stop_reason": "verified"},
                    }
                ),
                _event("agent_decision", _execute(appended)),
                _receipt(appended, return_code=0, sequence=2, work_epoch=2),
            )
        )
        + "\n"
    ).encode()

    [batch] = load_recorded_replay_batches(source)

    assert [step.command.id for step in batch] == ["completed"]


def test_recorded_replay_skips_a_control_rejected_decision() -> None:
    skipped = ShellCommand(
        id="reserved-budget",
        script="touch skipped",
        purpose="would consume the verification reserve",
        mode=CommandMode.CHANGE,
    )
    completed = ShellCommand(
        id="completed",
        script="touch completed",
        purpose="run after the control rejection",
        mode=CommandMode.CHANGE,
    )
    source = (
        "\n".join(
            (
                _event("agent_decision", _execute(skipped)),
                _event("agent_decision", _execute(completed)),
                _receipt(completed, return_code=0, work_epoch=2),
            )
        )
        + "\n"
    ).encode()

    [batch] = load_recorded_replay_batches(source)

    assert [step.command.id for step in batch] == ["completed"]


async def test_replay_requires_the_exact_recorded_return_code(tmp_path) -> None:
    command = ShellCommand(
        id="nonzero",
        script="false",
        purpose="reproduce the exact failure",
        mode=CommandMode.OBSERVE,
    )
    source = tmp_path / "source.jsonl"
    source.write_text(
        "\n".join(
            (
                _event("agent_decision", _execute(command)),
                _receipt(command, return_code=1),
            )
        )
        + "\n",
        encoding="utf-8",
    )
    environment = FakeEnvironment(
        responses={"false": [FakeExecResult(return_code=2)]},
    )
    agent = JournalReplayAgent(logs_dir=tmp_path / "logs", journal_path=source)

    await agent.setup(environment)
    with pytest.raises(RuntimeError, match=r"return_code=2.*return_code=1"):
        await agent.run("ignored during replay", environment, AgentContext())


async def test_replay_discards_a_policy_rejected_command_batch(tmp_path) -> None:
    rejected = ShellCommand(
        id="rejected",
        script="sleep 999",
        purpose="request an invalid timeout",
        mode=CommandMode.CHANGE,
    )
    unexecuted = ShellCommand(
        id="not-run",
        script="printf wrong",
        purpose="follow the rejected command",
        mode=CommandMode.CHANGE,
    )
    recovered = ShellCommand(
        id="recovered",
        script="touch recovered.txt",
        purpose="recover with a valid command",
        mode=CommandMode.CHANGE,
    )
    source = tmp_path / "source.jsonl"
    source.write_text(
        "\n".join(
            (
                _event("agent_decision", _execute(rejected, unexecuted)),
                json.dumps(
                    {
                        "type": "policy_rejection",
                        "payload": {"command_id": rejected.id, "reason": "test rejection"},
                    }
                ),
                _event("agent_decision", _execute(recovered)),
                _receipt(recovered, return_code=0, work_epoch=2),
            )
        )
        + "\n",
        encoding="utf-8",
    )
    environment = FakeEnvironment()
    agent = JournalReplayAgent(logs_dir=tmp_path / "logs", journal_path=source)

    await agent.setup(environment)
    await agent.run("ignored during replay", environment, AgentContext())

    assert [call[0] for call in environment.calls] == ["touch recovered.txt"]


def test_recorded_replay_rejects_wrong_policy_rejection_id() -> None:
    command = ShellCommand(
        id="expected",
        script="true",
        purpose="run expected command",
        mode=CommandMode.OBSERVE,
    )
    source = (
        "\n".join(
            (
                _event("agent_decision", _execute(command)),
                json.dumps(
                    {
                        "type": "policy_rejection",
                        "payload": {"command_id": "different", "reason": "test rejection"},
                    }
                ),
            )
        )
        + "\n"
    ).encode()

    with pytest.raises(ValueError, match="expected pending command 'expected'"):
        load_recorded_replay_batches(source)


def test_recorded_replay_rejects_mismatched_receipt_identity() -> None:
    command = ShellCommand(
        id="inspect",
        script="git status --short",
        purpose="inspect repository",
        mode=CommandMode.OBSERVE,
    )
    receipt = json.loads(_receipt(command, return_code=0))
    receipt["payload"]["command_fingerprint"] = "0" * 64
    source = (
        "\n".join(
            (
                _event("agent_decision", _execute(command)),
                json.dumps(receipt),
            )
        )
        + "\n"
    ).encode()

    with pytest.raises(ValueError, match="command fingerprint"):
        load_recorded_replay_batches(source)


def test_recorded_replay_rejects_receipt_fields_that_do_not_match_decision() -> None:
    command = ShellCommand(
        id="inspect",
        script="printf 'a b'",
        purpose="inspect output",
        mode=CommandMode.OBSERVE,
    )
    receipt = json.loads(_receipt(command, return_code=0))
    receipt["payload"]["script"] = "printf 'a   b'"
    source = (
        "\n".join(
            (
                _event("agent_decision", _execute(command)),
                json.dumps(receipt),
            )
        )
        + "\n"
    ).encode()

    with pytest.raises(ValueError, match="does not match pending command"):
        load_recorded_replay_batches(source)


def test_recorded_replay_rejects_invalid_sequence_and_epoch() -> None:
    first = ShellCommand(
        id="first",
        script="true",
        purpose="run first",
        mode=CommandMode.OBSERVE,
    )
    second = ShellCommand(
        id="second",
        script="printf second",
        purpose="run second",
        mode=CommandMode.OBSERVE,
    )
    duplicate_sequence = (
        "\n".join(
            (
                _event("agent_decision", _execute(first, second)),
                _receipt(first, return_code=0),
                _receipt(second, return_code=0),
            )
        )
        + "\n"
    ).encode()
    changed_epoch = (
        "\n".join(
            (
                _event("agent_decision", _execute(first, second)),
                _receipt(first, return_code=0),
                _receipt(second, return_code=0, sequence=2, work_epoch=2),
            )
        )
        + "\n"
    ).encode()

    with pytest.raises(ValueError, match="has command sequence 1; expected 2"):
        load_recorded_replay_batches(duplicate_sequence)
    with pytest.raises(ValueError, match="changes work epoch"):
        load_recorded_replay_batches(changed_epoch)


def test_recorded_replay_rejects_sequence_gaps_and_reused_batch_epochs() -> None:
    first = ShellCommand(
        id="first",
        script="true",
        purpose="run first",
        mode=CommandMode.OBSERVE,
    )
    second = ShellCommand(
        id="second",
        script="printf second",
        purpose="run second",
        mode=CommandMode.OBSERVE,
    )
    sequence_gap = (
        "\n".join(
            (
                _event("agent_decision", _execute(first)),
                _receipt(first, return_code=0),
                _event("agent_decision", _execute(second)),
                _receipt(second, return_code=0, sequence=3, work_epoch=2),
            )
        )
        + "\n"
    ).encode()
    reused_epoch = (
        "\n".join(
            (
                _event("agent_decision", _execute(first)),
                _receipt(first, return_code=0),
                _event("agent_decision", _execute(second)),
                _receipt(second, return_code=0, sequence=2),
            )
        )
        + "\n"
    ).encode()

    with pytest.raises(ValueError, match="has command sequence 3; expected 2"):
        load_recorded_replay_batches(sequence_gap)
    with pytest.raises(ValueError, match="does not advance work epoch"):
        load_recorded_replay_batches(reused_epoch)


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
