from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from harbor.agents.base import BaseAgent
from harbor.agents.capabilities import AgentCapabilities
from harbor.agents.options import AgentOptions
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from pydantic import Field

from evidence_harness.journal import RunJournal
from evidence_harness.protocol import ActionKind, AgentDecision, LoopOptions, ShellCommand
from evidence_harness.shell import CommandRunner


class JournalReplayOptions(AgentOptions):
    journal_path: Path
    max_command_timeout_sec: int = Field(default=300, ge=5, le=3_600)
    output_inline_bytes: int = Field(default=12_000, ge=1_000, le=100_000)


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
        batches = load_replay_batches(source_bytes)
        root = self.logs_dir / "evidence-harness-replay"
        journal = RunJournal(root, inline_bytes=self.options.output_inline_bytes)
        runner = CommandRunner(
            environment,
            journal,
            LoopOptions(max_command_timeout_sec=self.options.max_command_timeout_sec),
        )
        source_sha256 = hashlib.sha256(source_bytes).hexdigest()
        command_count = sum(len(batch) for batch in batches)
        context.metadata = {
            "evidence_harness_replay": {
                "source_sha256": source_sha256,
                "command_count": command_count,
                "completed": False,
            }
        }
        journal.append(
            "replay_started",
            {
                "source_sha256": source_sha256,
                "batch_count": len(batches),
                "command_count": command_count,
            },
        )

        sequence = 0
        for work_epoch, batch in enumerate(batches, start=1):
            for command in batch:
                sequence += 1
                receipt = await runner.execute(
                    command,
                    sequence=sequence,
                    work_epoch=work_epoch,
                )
                if not receipt.succeeded:
                    raise RuntimeError(
                        f"replayed command '{command.id}' failed: "
                        f"{receipt.failure or receipt.return_code}"
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


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}
