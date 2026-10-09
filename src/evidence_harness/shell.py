from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Callable
from contextlib import suppress

from evidence_harness.journal import RunJournal
from evidence_harness.policy import (
    command_fingerprint,
    observation_fingerprint,
    validate_command,
)
from evidence_harness.protocol import (
    CommandReceipt,
    EnvironmentResult,
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
        *,
        completion_attempt_id: int | None = None,
    ) -> None:
        self._environment = environment
        self._journal = journal
        self._options = options
        self._clock = clock
        self._completion_attempt_id = completion_attempt_id

    async def execute(
        self,
        command: ShellCommand,
        *,
        sequence: int,
        work_epoch: int,
        wall_timeout_sec: float | None = None,
    ) -> CommandReceipt:
        validate_command(command, self._options.max_command_timeout_sec)
        started = self._clock()
        return_code: int | None
        failure: FailureKind | None = None
        stdout = ""
        stderr = ""

        try:
            environment_timeout_sec = command.timeout_sec
            if wall_timeout_sec is not None:
                environment_timeout_sec = max(
                    1,
                    min(command.timeout_sec, math.ceil(wall_timeout_sec)),
                )
            environment_task = asyncio.create_task(
                self._environment.exec(
                    command=command.script,
                    cwd=command.cwd,
                    timeout_sec=environment_timeout_sec,
                )
            )
            try:
                if wall_timeout_sec is None:
                    result = await asyncio.shield(environment_task)
                else:
                    async with asyncio.timeout(max(0.0, wall_timeout_sec)):
                        result = await asyncio.shield(environment_task)
            except BaseException:
                await _drain_environment_task(environment_task)
                raise
            stdout = result.stdout or ""
            stderr = result.stderr or ""
            return_code = result.return_code
            if return_code != 0:
                failure = FailureKind.NONZERO
        except TimeoutError as exc:
            return_code = None
            failure = FailureKind.TIMEOUT
            if wall_timeout_sec is not None and wall_timeout_sec <= command.timeout_sec:
                stderr = (
                    str(exc)
                    or f"command exceeded remaining wall-clock budget ({wall_timeout_sec:g}s)"
                )
            else:
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
            attempt_id=self._completion_attempt_id,
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


async def _drain_environment_task(
    task: asyncio.Task[EnvironmentResult],
) -> None:
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled():
                break
        except BaseException:
            break
    if task.done() and not task.cancelled():
        with suppress(BaseException):
            task.result()
