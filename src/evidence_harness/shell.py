from __future__ import annotations

import time
from collections.abc import Callable

from evidence_harness.journal import RunJournal
from evidence_harness.policy import (
    command_fingerprint,
    observation_fingerprint,
    validate_command,
)
from evidence_harness.protocol import (
    CommandReceipt,
    FailureKind,
    LoopOptions,
    ShellCommand,
    ShellEnvironment,
)


class CommandRunner:
    def __init__(
        self,
        environment: ShellEnvironment,
        journal: RunJournal,
        options: LoopOptions,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._environment = environment
        self._journal = journal
        self._options = options
        self._clock = clock

    async def execute(
        self,
        command: ShellCommand,
        *,
        sequence: int,
        work_epoch: int,
    ) -> CommandReceipt:
        validate_command(command, self._options.max_command_timeout_sec)
        started = self._clock()
        return_code: int | None
        failure: FailureKind | None = None
        stdout = ""
        stderr = ""

        try:
            result = await self._environment.exec(
                command=command.script,
                cwd=command.cwd,
                timeout_sec=command.timeout_sec,
            )
            stdout = result.stdout or ""
            stderr = result.stderr or ""
            return_code = result.return_code
            if return_code != 0:
                failure = FailureKind.NONZERO
        except TimeoutError as exc:
            return_code = None
            failure = FailureKind.TIMEOUT
            stderr = str(exc) or f"command exceeded {command.timeout_sec}s"
        except Exception as exc:
            return_code = None
            failure = FailureKind.TRANSPORT
            stderr = f"{type(exc).__name__}: {exc}"

        duration = max(0.0, self._clock() - started)
        stdout_excerpt = self._journal.capture_output(sequence, "stdout", stdout)
        stderr_excerpt = self._journal.capture_output(sequence, "stderr", stderr)
        command_hash = command_fingerprint(command.script, command.cwd)
        receipt = CommandReceipt(
            sequence=sequence,
            command_id=command.id,
            script=command.script,
            purpose=command.purpose,
            cwd=command.cwd,
            mode=command.mode,
            work_epoch=work_epoch,
            return_code=return_code,
            failure=failure,
            duration_sec=duration,
            stdout=stdout_excerpt,
            stderr=stderr_excerpt,
            command_fingerprint=command_hash,
            observation_fingerprint=observation_fingerprint(
                command_hash,
                return_code,
                failure.value if failure else None,
                stdout_excerpt.sha256,
                stderr_excerpt.sha256,
            ),
        )
        self._journal.append("command_receipt", receipt)
        return receipt
