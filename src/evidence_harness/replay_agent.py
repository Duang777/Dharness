from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from harbor.agents.base import BaseAgent
from harbor.agents.capabilities import AgentCapabilities
from harbor.agents.options import AgentOptions
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from pydantic import Field

from evidence_harness.journal import RunJournal
from evidence_harness.policy import command_fingerprint, observation_fingerprint
from evidence_harness.process_containment import contain_harbor_environment
from evidence_harness.protocol import (
    ActionKind,
    AgentDecision,
    CommandReceipt,
    FailureKind,
    LoopOptions,
    ShellCommand,
)
from evidence_harness.shell import CommandRunner


class JournalReplayOptions(AgentOptions):
    journal_path: Path
    max_command_timeout_sec: int = Field(default=300, ge=5, le=3_600)
    output_inline_bytes: int = Field(default=12_000, ge=1_000, le=100_000)
    legacy_replay_all_decisions: bool = False


@dataclass(frozen=True)
class RecordedCommand:
    command: ShellCommand
    expected_return_code: int | None
    expected_failure: FailureKind | None
    sequence: int
    work_epoch: int


class JournalReplayAgent(BaseAgent):
    capabilities = AgentCapabilities()
    options_model = JournalReplayOptions
    options: JournalReplayOptions

    @staticmethod
    def name() -> str:
        return "evidence-journal-replay"

    def version(self) -> str:
        return "0.1.0"

    async def setup(self, environment: BaseEnvironment) -> None:
        del environment
        (self.logs_dir / "evidence-harness-replay").mkdir(parents=True, exist_ok=True)

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        del instruction
        source_bytes = self.options.journal_path.read_bytes()
        replay_mode = "recorded_receipts"
        if self.options.legacy_replay_all_decisions:
            replay_mode = "legacy_all_decisions"
            batches = _legacy_recorded_batches(load_replay_batches(source_bytes))
        else:
            batches = load_recorded_replay_batches(source_bytes)
        root = self.logs_dir / "evidence-harness-replay"
        journal = RunJournal(root, inline_bytes=self.options.output_inline_bytes)
        runner = CommandRunner(
            contain_harbor_environment(environment),
            journal,
            LoopOptions(max_command_timeout_sec=self.options.max_command_timeout_sec),
        )
        source_sha256 = hashlib.sha256(source_bytes).hexdigest()
        command_count = sum(len(batch) for batch in batches)
        recorded_failure_count = sum(
            step.expected_failure is not None for batch in batches for step in batch
        )
        context.metadata = {
            "evidence_harness_replay": {
                "source_sha256": source_sha256,
                "command_count": command_count,
                "recorded_failure_count": recorded_failure_count,
                "replay_mode": replay_mode,
                "completed": False,
            }
        }
        journal.append(
            "replay_started",
            {
                "source_sha256": source_sha256,
                "batch_count": len(batches),
                "command_count": command_count,
                "recorded_failure_count": recorded_failure_count,
                "replay_mode": replay_mode,
            },
        )

        for batch in batches:
            for step in batch:
                receipt = await runner.execute(
                    step.command,
                    sequence=step.sequence,
                    work_epoch=step.work_epoch,
                )
                if (
                    receipt.return_code != step.expected_return_code
                    or receipt.failure is not step.expected_failure
                ):
                    raise RuntimeError(
                        f"replayed command '{step.command.id}' produced "
                        f"return_code={receipt.return_code}, "
                        f"failure={_failure_name(receipt.failure)}; expected "
                        f"return_code={step.expected_return_code}, "
                        f"failure={_failure_name(step.expected_failure)}"
                    )

        context.metadata["evidence_harness_replay"]["completed"] = True
        journal.append(
            "replay_finished",
            {
                "source_sha256": source_sha256,
                "command_count": command_count,
            },
        )


def load_replay_batches(source_bytes: bytes) -> tuple[tuple[ShellCommand, ...], ...]:
    batches: list[tuple[ShellCommand, ...]] = []
    for line_number, raw_line in enumerate(source_bytes.splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid journal JSON on line {line_number}") from exc
        if not isinstance(event, dict) or event.get("type") != "agent_decision":
            continue
        decision = AgentDecision.model_validate(_mapping(event.get("payload")))
        if decision.action is not ActionKind.EXECUTE:
            continue
        if any("[REDACTED]" in command.script for command in decision.commands):
            raise ValueError(
                f"journal line {line_number} contains a redacted command and cannot be replayed"
            )
        batches.append(decision.commands)
    if not batches:
        raise ValueError("journal contains no executable agent decisions")
    return tuple(batches)


def load_recorded_replay_batches(
    source_bytes: bytes,
) -> tuple[tuple[RecordedCommand, ...], ...]:
    batches: list[tuple[RecordedCommand, ...]] = []
    pending: tuple[ShellCommand, ...] = ()
    completed: list[RecordedCommand] = []
    batch_work_epoch: int | None = None
    previous_work_epoch = 0
    last_sequence = 0
    last_work_epoch = 0

    for line_number, raw_line in enumerate(source_bytes.splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            event = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid journal JSON on line {line_number}") from exc
        if not isinstance(event, dict):
            continue

        event_type = event.get("type")
        payload = _mapping(event.get("payload"))
        if event_type == "agent_decision":
            _validate_completed_batch(
                pending,
                completed,
                line_number,
                allow_unexecuted=True,
            )
            if completed:
                batches.append(tuple(completed))
            completed = []
            batch_work_epoch = None
            previous_work_epoch = last_work_epoch
            decision = AgentDecision.model_validate(payload)
            pending = decision.commands if decision.action is ActionKind.EXECUTE else ()
            if any("[REDACTED]" in command.script for command in pending):
                raise ValueError(
                    f"journal line {line_number} contains a redacted command and cannot be replayed"
                )
            continue
        if event_type == "policy_rejection":
            if not pending:
                raise ValueError(
                    f"journal line {line_number} has no command pending for policy rejection"
                )
            if completed and completed[-1].expected_failure is not None:
                raise ValueError(
                    f"journal line {line_number} records a policy rejection after a failed command"
                )
            if payload.get("command_id") != pending[0].id:
                raise ValueError(
                    f"journal line {line_number} rejects '{payload.get('command_id')}', "
                    f"expected pending command '{pending[0].id}'"
                )
            if completed:
                batches.append(tuple(completed))
            pending = ()
            completed = []
            batch_work_epoch = None
            continue
        if event_type == "run_finished":
            if completed:
                batches.append(tuple(completed))
            pending = ()
            completed = []
            break
        if event_type != "command_receipt":
            continue

        try:
            receipt = CommandReceipt.model_validate(payload)
        except ValueError as exc:
            raise ValueError(f"invalid command receipt on journal line {line_number}") from exc
        if receipt.sequence != last_sequence + 1:
            raise ValueError(
                f"journal line {line_number} has command sequence {receipt.sequence}; "
                f"expected {last_sequence + 1}"
            )
        if receipt.work_epoch < last_work_epoch:
            raise ValueError(
                f"journal line {line_number} has decreasing work epoch {receipt.work_epoch}"
            )
        last_sequence = receipt.sequence
        last_work_epoch = receipt.work_epoch
        if not pending:
            continue
        if completed and completed[-1].expected_failure is not None:
            raise ValueError(f"journal line {line_number} records a command after a failed command")

        command = pending[0]
        if (
            receipt.command_id != command.id
            or receipt.script != command.script
            or receipt.purpose != command.purpose
            or receipt.cwd != command.cwd
            or receipt.mode is not command.mode
        ):
            raise ValueError(
                f"journal line {line_number} does not match pending command '{command.id}'"
            )
        recorded_fingerprint = command_fingerprint(receipt.script, receipt.cwd)
        if receipt.command_fingerprint != recorded_fingerprint:
            raise ValueError(
                f"journal line {line_number} command fingerprint does not match "
                "the recorded command"
            )
        expected_fingerprint = command_fingerprint(command.script, command.cwd)
        if receipt.command_fingerprint != expected_fingerprint:
            raise ValueError(
                f"journal line {line_number} command fingerprint does not match "
                f"pending command '{command.id}'"
            )
        if batch_work_epoch is None:
            if receipt.work_epoch <= previous_work_epoch:
                raise ValueError(
                    f"journal line {line_number} does not advance work epoch "
                    f"past {previous_work_epoch}"
                )
            batch_work_epoch = receipt.work_epoch
        elif receipt.work_epoch != batch_work_epoch:
            raise ValueError(
                f"journal line {line_number} changes work epoch within a command batch"
            )
        _validate_recorded_outcome(receipt, line_number)
        completed.append(
            RecordedCommand(
                command=command,
                expected_return_code=receipt.return_code,
                expected_failure=receipt.failure,
                sequence=receipt.sequence,
                work_epoch=receipt.work_epoch,
            )
        )
        pending = pending[1:]

    _validate_completed_batch(pending, completed, line_number if source_bytes else 0)
    if completed:
        batches.append(tuple(completed))
    if not batches:
        raise ValueError("journal contains no recorded command receipts")
    return tuple(batches)


def _legacy_recorded_batches(
    batches: tuple[tuple[ShellCommand, ...], ...],
) -> tuple[tuple[RecordedCommand, ...], ...]:
    sequence = 0
    recorded_batches: list[tuple[RecordedCommand, ...]] = []
    for work_epoch, batch in enumerate(batches, start=1):
        recorded: list[RecordedCommand] = []
        for command in batch:
            sequence += 1
            recorded.append(
                RecordedCommand(
                    command=command,
                    expected_return_code=0,
                    expected_failure=None,
                    sequence=sequence,
                    work_epoch=work_epoch,
                )
            )
        recorded_batches.append(tuple(recorded))
    return tuple(recorded_batches)


def _validate_recorded_outcome(receipt: CommandReceipt, line_number: int) -> None:
    if receipt.failure is None and receipt.return_code != 0:
        raise ValueError(
            f"journal line {line_number} records a nonzero return code without a failure"
        )
    if receipt.failure is FailureKind.NONZERO and (
        receipt.return_code is None or receipt.return_code == 0
    ):
        raise ValueError(f"journal line {line_number} records an invalid nonzero command outcome")
    if receipt.failure in {FailureKind.TIMEOUT, FailureKind.TRANSPORT} and (
        receipt.return_code is not None
    ):
        raise ValueError(
            f"journal line {line_number} records a return code for {receipt.failure.value}"
        )
    expected_observation = observation_fingerprint(
        receipt.command_fingerprint,
        receipt.return_code,
        receipt.failure.value if receipt.failure is not None else None,
        receipt.stdout.sha256,
        receipt.stderr.sha256,
    )
    if receipt.observation_fingerprint != expected_observation:
        raise ValueError(f"journal line {line_number} has an invalid observation fingerprint")


def _failure_name(failure: FailureKind | None) -> str:
    return failure.value if failure is not None else "success"


def _validate_completed_batch(
    pending: tuple[ShellCommand, ...],
    completed: list[RecordedCommand],
    line_number: int,
    *,
    allow_unexecuted: bool = False,
) -> None:
    if not pending:
        return
    if not completed and allow_unexecuted:
        return
    if completed and completed[-1].expected_failure is not None:
        return
    raise ValueError(
        f"journal line {line_number} ends a successful command batch "
        f"before receipt for '{pending[0].id}'"
    )


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
