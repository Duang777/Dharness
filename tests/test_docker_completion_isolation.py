from __future__ import annotations

import asyncio
import hashlib
import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import cast

import pytest

import evidence_harness.docker_completion_isolation as isolation_module
from evidence_harness.completion_isolation import (
    CompletionIsolationError,
    CompletionIsolationRequest,
    IsolationFailureKind,
)
from evidence_harness.docker_completion_isolation import (
    DockerCliResult,
    DockerCompletionIsolation,
    DockerIsolationTarget,
)
from evidence_harness.journal import RunJournal
from evidence_harness.protocol import (
    CheckKind,
    CommandMode,
    CommandReceipt,
    FailureKind,
    OutputExcerpt,
    VerificationCheck,
)

SOURCE_ID = "1" * 64
CHILD_ID = "2" * 64
INSPECTOR_ID = "6" * 64
IMAGE_ID = "sha256:" + "3" * 64
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


@dataclass(frozen=True)
class _Response:
    matches: Callable[[tuple[str, ...]], bool]
    result: DockerCliResult | BaseException
    elapsed_sec: float = 0
    delay_sec: float = 0


class ScriptedDockerCli:
    def __init__(
        self,
        responses: Sequence[_Response],
        *,
        clock: _Clock | None = None,
    ) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, ...]] = []
        self.timeouts: list[float] = []
        self.clock = clock

    async def run(
        self,
        args: Sequence[str],
        *,
        timeout_sec: float,
    ) -> DockerCliResult:
        argv = tuple(args)
        self.calls.append(argv)
        self.timeouts.append(timeout_sec)
        if not self.responses:
            raise AssertionError(f"unexpected Docker call: {argv}")
        response = self.responses.pop(0)
        assert response.matches(argv), f"unexpected Docker call: {argv}"
        if response.delay_sec:
            await asyncio.sleep(response.delay_sec)
        if self.clock is not None:
            self.clock.advance(response.elapsed_sec)
        if isinstance(response.result, BaseException):
            raise response.result
        return response.result


class HangingCleanupDockerCli(ScriptedDockerCli):
    async def run(
        self,
        args: Sequence[str],
        *,
        timeout_sec: float,
    ) -> DockerCliResult:
        if tuple(args[:1]) == ("unpause",):
            self.calls.append(tuple(args))
            self.timeouts.append(timeout_sec)
            await asyncio.Event().wait()
        return await super().run(args, timeout_sec=timeout_sec)


@dataclass
class _Clock:
    value: float = 0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _exact(
    *args: str,
    stdout: str = "",
    return_code: int = 0,
    elapsed_sec: float = 0,
    delay_sec: float = 0,
) -> _Response:
    return _Response(
        matches=lambda actual: actual == args,
        result=DockerCliResult(return_code=return_code, stdout=stdout, stderr=""),
        elapsed_sec=elapsed_sec,
        delay_sec=delay_sec,
    )


def _prefix(
    *args: str,
    stdout: str = "",
    return_code: int = 0,
    elapsed_sec: float = 0,
    delay_sec: float = 0,
) -> _Response:
    return _Response(
        matches=lambda actual: actual[: len(args)] == args,
        result=DockerCliResult(return_code=return_code, stdout=stdout, stderr=""),
        elapsed_sec=elapsed_sec,
        delay_sec=delay_sec,
    )


def _raises(*args: str, error: BaseException) -> _Response:
    return _Response(
        matches=lambda actual: actual == args,
        result=error,
    )


def _source_inspect(*, mounts: list[dict[str, object]] | None = None) -> str:
    return json.dumps(
        [
            {
                "Id": SOURCE_ID,
                "Image": "sha256:" + "4" * 64,
                "Path": "sh",
                "Args": ["-c", "sleep infinity"],
                "State": {"Running": True, "Paused": False, "Status": "running"},
                "Config": {
                    "Cmd": ["sh", "-c", "sleep infinity"],
                    "Entrypoint": None,
                    "Labels": {
                        "com.docker.compose.project": "trial",
                        "com.docker.compose.service": "main",
                    },
                    "User": "",
                    "Volumes": None,
                    "WorkingDir": "/app",
                },
                "HostConfig": {
                    "AutoRemove": False,
                    "Binds": [],
                    "CapAdd": None,
                    "CapDrop": None,
                    "CgroupnsMode": "private",
                    "CpuPeriod": 0,
                    "CpuQuota": 0,
                    "CpuShares": 0,
                    "CpusetCpus": "",
                    "DeviceRequests": [],
                    "Devices": [],
                    "IpcMode": "private",
                    "Memory": 536_870_912,
                    "MemoryReservation": 0,
                    "MemorySwap": 1_073_741_824,
                    "NanoCpus": 1_000_000_000,
                    "NetworkMode": "trial_default",
                    "OomKillDisable": None,
                    "PidMode": "",
                    "PidsLimit": None,
                    "Privileged": False,
                    "ReadonlyRootfs": False,
                    "SecurityOpt": None,
                    "UTSMode": "",
                },
                "Mounts": mounts
                if mounts is not None
                else [
                    {
                        "Type": "bind",
                        "Source": "/host/trial/logs",
                        "Destination": "/logs",
                        "RW": True,
                    }
                ],
            }
        ]
    )


def _child_inspect(container_id: str = CHILD_ID) -> str:
    return json.dumps(
        [
            {
                "Id": container_id,
                "Image": IMAGE_ID,
                "State": {"Running": True, "Paused": False, "Status": "running"},
                "Mounts": [],
            }
        ]
    )


def _success_responses(
    *,
    child_diff: str = "A /tmp/check-output\n",
    classification: str | None = None,
    baseline_classification: str | None = None,
) -> list[_Response]:
    responses = [
        _exact("info", "--format", "{{json .OSType}}", stdout='"linux"\n'),
        _exact(
            "ps",
            "--filter",
            "label=com.docker.compose.project=trial",
            "--format",
            "{{.ID}}",
            stdout=f"{SOURCE_ID}\n",
        ),
        _exact("inspect", SOURCE_ID, stdout=_source_inspect()),
        _exact(
            "top",
            SOURCE_ID,
            "-eo",
            "pid,ppid,comm",
            stdout="PID PPID COMMAND\n1 0 sh\n7 1 sleep\n",
        ),
        _exact("pause", SOURCE_ID, stdout=f"{SOURCE_ID}\n"),
        _exact("diff", SOURCE_ID),
        _exact(
            "inspect",
            "--format",
            "{{json .State.Paused}}",
            SOURCE_ID,
            stdout="true\n",
        ),
        _prefix(
            "commit",
            "--pause=false",
            SOURCE_ID,
            stdout=(
                "Flag --pause has been deprecated, and enabled by default. "
                "Use --no-pause to disable pausing during commit.\n"
                f"{IMAGE_ID}\n"
            ),
        ),
        _prefix("run", "-d", "--network", "none", stdout=f"{CHILD_ID}\n"),
        _exact("inspect", CHILD_ID, stdout=_child_inspect()),
        _exact(
            "exec",
            "--workdir",
            "/app",
            CHILD_ID,
            "bash",
            "-c",
            "python3 verify.py > /tmp/check-output",
            stdout="verified\n",
        ),
        _exact("diff", CHILD_ID, stdout=child_diff),
    ]
    if classification is not None:
        responses.append(_prefix("exec", CHILD_ID, "sh", "-c", stdout=classification))
        responses.extend(
            [
                _prefix("run", "-d", "--network", "none", stdout=f"{INSPECTOR_ID}\n"),
                _exact(
                    "inspect",
                    INSPECTOR_ID,
                    stdout=_child_inspect(INSPECTOR_ID),
                ),
                _prefix(
                    "exec",
                    INSPECTOR_ID,
                    "sh",
                    "-c",
                    stdout=baseline_classification or classification,
                ),
            ]
        )
    responses.extend(
        [
            _exact("rm", "-f", CHILD_ID, stdout=CHILD_ID),
            _exact(
                "inspect",
                "--format",
                "{{json .State.Paused}}",
                SOURCE_ID,
                stdout="true\n",
            ),
            _exact("diff", SOURCE_ID),
        ]
    )
    responses.extend(
        [
            _exact("unpause", SOURCE_ID, stdout=SOURCE_ID),
            _exact(
                "inspect",
                "--format",
                "{{json .State}}",
                SOURCE_ID,
                stdout='{"Status":"running","Running":true,"Paused":false}\n',
            ),
        ]
    )
    if classification is not None:
        responses.append(_exact("rm", "-f", INSPECTOR_ID, stdout=INSPECTOR_ID))
    responses.append(_prefix("image", "rm", IMAGE_ID, stdout=IMAGE_ID))
    return responses


def _check() -> VerificationCheck:
    return VerificationCheck(
        id="behavior",
        kind=CheckKind.BEHAVIOR,
        script="python3 verify.py > /tmp/check-output",
        proves="the requested behavior works",
        cwd="/app",
        timeout_sec=60,
    )


def _receipt(check: VerificationCheck) -> CommandReceipt:
    empty = OutputExcerpt(
        head="",
        tail="",
        total_bytes=0,
        omitted_bytes=0,
        sha256=EMPTY_SHA256,
    )
    return CommandReceipt(
        sequence=2,
        command_id=check.id,
        script=check.script,
        purpose=check.proves,
        cwd=check.cwd,
        mode=CommandMode.OBSERVE,
        work_epoch=1,
        return_code=0,
        duration_sec=0.1,
        stdout=empty,
        stderr=empty,
        command_fingerprint="5" * 64,
        observation_fingerprint="6" * 64,
    )


async def _execute_check(
    check: VerificationCheck,
    environment,
    deadline_monotonic: float,
) -> CommandReceipt:
    del deadline_monotonic
    result = await environment.exec(
        check.script,
        cwd=check.cwd,
        timeout_sec=check.timeout_sec,
    )
    assert result.return_code == 0
    return _receipt(check)


def _provider(
    tmp_path,
    cli: ScriptedDockerCli,
    *,
    clock: Callable[[], float] | None = None,
) -> DockerCompletionIsolation:
    return DockerCompletionIsolation(
        target=DockerIsolationTarget(project_name="trial"),
        journal=RunJournal(tmp_path, inline_bytes=512),
        cli=cli,
        **({"clock": clock} if clock is not None else {}),
    )


async def test_runs_unchanged_check_once_in_mount_free_child(tmp_path) -> None:
    cli = ScriptedDockerCli(_success_responses())
    check = _check()

    result = await _provider(tmp_path, cli).verify(
        CompletionIsolationRequest(
            attempt_id=1,
            work_epoch=1,
            checks=(check,),
            deadline_monotonic=10**12,
        ),
        _execute_check,
    )

    check_execs = [
        call for call in cli.calls if call[:5] == ("exec", "--workdir", "/app", CHILD_ID, "bash")
    ]
    assert check_execs == [
        (
            "exec",
            "--workdir",
            "/app",
            CHILD_ID,
            "bash",
            "-c",
            check.script,
        )
    ]
    run_call = next(call for call in cli.calls if call[:2] == ("run", "-d"))
    assert "--network" in run_call
    assert run_call[run_call.index("--network") + 1] == "none"
    assert "--mount" not in run_call
    assert "--volume" not in run_call
    assert result.evidence.excluded_control_mounts == ("/logs",)
    assert result.evidence.checks[0].disposed is True
    assert result.evidence.source.remained_paused is True
    assert result.evidence.source.resumed is True
    assert result.evidence.snapshot_image_disposed is True
    assert cli.responses == []


async def test_preserves_nonzero_check_result_as_command_failure(tmp_path) -> None:
    check = _check()
    responses = _success_responses()
    check_exec_index = next(
        index
        for index, response in enumerate(responses)
        if response.matches(
            (
                "exec",
                "--workdir",
                "/app",
                CHILD_ID,
                "bash",
                "-c",
                check.script,
            )
        )
    )
    responses[check_exec_index] = _exact(
        "exec",
        "--workdir",
        "/app",
        CHILD_ID,
        "bash",
        "-c",
        check.script,
        return_code=1,
    )
    cli = ScriptedDockerCli(responses)

    async def execute_nonzero(
        candidate: VerificationCheck,
        environment,
        deadline_monotonic: float,
    ) -> CommandReceipt:
        del deadline_monotonic
        result = await environment.exec(
            candidate.script,
            cwd=candidate.cwd,
            timeout_sec=candidate.timeout_sec,
        )
        assert result.return_code == 1
        return _receipt(candidate).model_copy(
            update={
                "return_code": 1,
                "failure": FailureKind.NONZERO,
            }
        )

    result = await _provider(tmp_path, cli).verify(
        CompletionIsolationRequest(
            attempt_id=1,
            work_epoch=1,
            checks=(check,),
            deadline_monotonic=10**12,
        ),
        execute_nonzero,
    )

    assert result.receipts[0].return_code == 1
    assert result.receipts[0].failure is FailureKind.NONZERO
    assert result.evidence.source.resumed is True
    assert result.evidence.snapshot_image_disposed is True
    assert cli.responses == []


async def test_diff_classification_omits_changed_directories_not_files(tmp_path) -> None:
    cli = ScriptedDockerCli(
        _success_responses(
            child_diff=("C /app\nC /app/existing.txt\nA /tmp/check-output\nD /app/deleted.txt\n"),
            classification="d\nf\n",
        )
    )

    result = await _provider(tmp_path, cli).verify(
        CompletionIsolationRequest(
            attempt_id=1,
            work_epoch=1,
            checks=(_check(),),
            deadline_monotonic=10**12,
        ),
        _execute_check,
    )

    delta = result.evidence.checks[0].delta
    assert delta.added == ("/tmp/check-output",)
    assert delta.modified == ("/app/existing.txt",)
    assert delta.deleted == ("/app/deleted.txt",)
    assert delta.sha256 != EMPTY_SHA256


async def test_diff_classification_rejects_file_replaced_by_directory(tmp_path) -> None:
    cli = ScriptedDockerCli(
        _success_responses(
            child_diff="C /app/existing.txt\n",
            classification="d\n",
            baseline_classification="f\n",
        )
    )

    result = await _provider(tmp_path, cli).verify(
        CompletionIsolationRequest(
            attempt_id=1,
            work_epoch=1,
            checks=(_check(),),
            deadline_monotonic=10**12,
        ),
        _execute_check,
    )

    assert result.evidence.checks[0].delta.modified == ("/app/existing.txt",)
    assert cli.responses == []


async def test_rejects_task_mount_before_pausing_source(tmp_path) -> None:
    cli = ScriptedDockerCli(
        [
            _exact("info", "--format", "{{json .OSType}}", stdout='"linux"\n'),
            _exact(
                "ps",
                "--filter",
                "label=com.docker.compose.project=trial",
                "--format",
                "{{.ID}}",
                stdout=f"{SOURCE_ID}\n",
            ),
            _exact(
                "inspect",
                SOURCE_ID,
                stdout=_source_inspect(
                    mounts=[
                        {
                            "Type": "bind",
                            "Source": "/host/workspace",
                            "Destination": "/app",
                            "RW": True,
                        }
                    ]
                ),
            ),
        ]
    )

    with pytest.raises(
        CompletionIsolationError,
        match="unsupported mount target",
    ) as caught:
        await _provider(tmp_path, cli).verify(
            CompletionIsolationRequest(
                attempt_id=7,
                work_epoch=1,
                checks=(_check(),),
                deadline_monotonic=10**12,
            ),
            _execute_check,
        )

    assert caught.value.kind is IsolationFailureKind.UNSUPPORTED
    assert caught.value.attempt_id == 7
    assert not any(call[0] == "pause" for call in cli.calls)
    assert cli.responses == []


async def test_cleans_up_child_when_post_start_inspection_fails(tmp_path) -> None:
    responses = _success_responses()
    child_inspect_index = next(
        index for index, response in enumerate(responses) if response.matches(("inspect", CHILD_ID))
    )
    responses[child_inspect_index] = _exact(
        "inspect",
        CHILD_ID,
        stdout=json.dumps(
            [
                {
                    "Id": CHILD_ID,
                    "Image": IMAGE_ID,
                    "State": {"Running": False, "Paused": False, "Status": "exited"},
                    "Mounts": [],
                }
            ]
        ),
    )
    del responses[child_inspect_index + 1 :]
    responses.extend(
        [
            _exact("unpause", SOURCE_ID, stdout=SOURCE_ID),
            _exact(
                "inspect",
                "--format",
                "{{json .State}}",
                SOURCE_ID,
                stdout='{"Status":"running","Running":true,"Paused":false}\n',
            ),
            _exact("rm", "-f", CHILD_ID, stdout=CHILD_ID),
            _prefix("image", "rm", IMAGE_ID, stdout=IMAGE_ID),
        ]
    )
    cli = ScriptedDockerCli(responses)

    with pytest.raises(
        CompletionIsolationError,
        match="isolated child is not running",
    ) as caught:
        await _provider(tmp_path, cli).verify(
            CompletionIsolationRequest(
                attempt_id=1,
                work_epoch=1,
                checks=(_check(),),
                deadline_monotonic=10**12,
            ),
            _execute_check,
        )

    assert caught.value.kind is IsolationFailureKind.CHILD_START
    assert ("rm", "-f", CHILD_ID) in cli.calls
    assert cli.responses == []


async def test_cleans_up_child_by_reserved_name_when_docker_run_times_out(tmp_path) -> None:
    responses = _success_responses()[:8]
    responses.extend(
        [
            _Response(
                matches=lambda actual: actual[:4] == ("run", "-d", "--network", "none"),
                result=TimeoutError(),
            ),
            _exact("unpause", SOURCE_ID, stdout=SOURCE_ID),
            _exact(
                "inspect",
                "--format",
                "{{json .State}}",
                SOURCE_ID,
                stdout='{"Status":"running","Running":true,"Paused":false}\n',
            ),
            _Response(
                matches=lambda actual: (
                    actual[:2] == ("rm", "-f") and actual[2].startswith("evidence-harness-")
                ),
                result=DockerCliResult(
                    return_code=1,
                    stdout="",
                    stderr="Error response from daemon: No such container",
                ),
            ),
            _prefix("image", "rm", IMAGE_ID, stdout=IMAGE_ID),
        ]
    )
    cli = ScriptedDockerCli(responses)

    with pytest.raises(CompletionIsolationError, match="Docker run -d timed out") as caught:
        await _provider(tmp_path, cli).verify(
            CompletionIsolationRequest(
                attempt_id=1,
                work_epoch=1,
                checks=(_check(),),
                deadline_monotonic=10**12,
            ),
            _execute_check,
        )

    assert caught.value.kind is IsolationFailureKind.DEADLINE
    assert any(call[:2] == ("rm", "-f") for call in cli.calls)
    assert cli.responses == []


async def test_commit_timeout_allows_cleanup_when_image_was_not_created(tmp_path) -> None:
    responses = _success_responses()[:7]
    responses.extend(
        [
            _Response(
                matches=lambda actual: actual[:3] == ("commit", "--pause=false", SOURCE_ID),
                result=TimeoutError(),
            ),
            _exact("unpause", SOURCE_ID, stdout=SOURCE_ID),
            _exact(
                "inspect",
                "--format",
                "{{json .State}}",
                SOURCE_ID,
                stdout='{"Status":"running","Running":true,"Paused":false}\n',
            ),
            _Response(
                matches=lambda actual: actual[:2] == ("image", "rm"),
                result=DockerCliResult(
                    return_code=1,
                    stdout="",
                    stderr="Error response from daemon: No such image",
                ),
            ),
        ]
    )
    cli = ScriptedDockerCli(responses)

    with pytest.raises(
        CompletionIsolationError,
        match="Docker commit --pause=false timed out",
    ) as caught:
        await _provider(tmp_path, cli).verify(
            CompletionIsolationRequest(
                attempt_id=1,
                work_epoch=1,
                checks=(_check(),),
                deadline_monotonic=10**12,
            ),
            _execute_check,
        )

    assert caught.value.kind is IsolationFailureKind.DEADLINE
    assert any(call[:2] == ("image", "rm") for call in cli.calls)
    assert cli.responses == []


async def test_cleanup_order_survives_executor_failure(tmp_path) -> None:
    responses = _success_responses()[:10]
    responses.extend(
        [
            _exact("unpause", SOURCE_ID, stdout=SOURCE_ID),
            _exact(
                "inspect",
                "--format",
                "{{json .State}}",
                SOURCE_ID,
                stdout='{"Status":"running","Running":true,"Paused":false}\n',
            ),
            _exact("rm", "-f", CHILD_ID, stdout=CHILD_ID),
            _prefix("image", "rm", IMAGE_ID, stdout=IMAGE_ID),
        ]
    )
    cli = ScriptedDockerCli(responses)

    async def fail_execute(*args) -> CommandReceipt:
        del args
        raise RuntimeError("executor failed")

    with pytest.raises(CompletionIsolationError, match="executor failed") as caught:
        await _provider(tmp_path, cli).verify(
            CompletionIsolationRequest(
                attempt_id=1,
                work_epoch=1,
                checks=(_check(),),
                deadline_monotonic=10**12,
            ),
            fail_execute,
        )

    assert caught.value.kind is IsolationFailureKind.INSPECTION
    child_remove = cli.calls.index(("rm", "-f", CHILD_ID))
    image_remove = next(
        index for index, call in enumerate(cli.calls) if call[:2] == ("image", "rm")
    )
    source_unpause = cli.calls.index(("unpause", SOURCE_ID))
    assert source_unpause < child_remove < image_remove
    assert cli.responses == []


async def test_pause_timeout_still_recovers_a_source_paused_by_docker(tmp_path) -> None:
    responses = _success_responses()[:4]
    responses.extend(
        [
            _exact(
                "pause",
                SOURCE_ID,
                stdout=f"{SOURCE_ID}\n",
                delay_sec=0.02,
            ),
            _exact("unpause", SOURCE_ID, stdout=SOURCE_ID),
            _exact(
                "inspect",
                "--format",
                "{{json .State}}",
                SOURCE_ID,
                stdout='{"Status":"running","Running":true,"Paused":false}\n',
            ),
        ]
    )
    cli = ScriptedDockerCli(responses)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(isolation_module, "_PAUSE_ACK_TIMEOUT_SEC", 0.001)
        with pytest.raises(
            CompletionIsolationError,
            match="Docker pause acknowledgement timed out",
        ) as caught:
            await _provider(tmp_path, cli).verify(
                CompletionIsolationRequest(
                    attempt_id=1,
                    work_epoch=1,
                    checks=(_check(),),
                    deadline_monotonic=10**12,
                ),
                _execute_check,
            )

    assert caught.value.kind is IsolationFailureKind.DEADLINE
    assert cli.calls.index(("pause", SOURCE_ID)) < cli.calls.index(("unpause", SOURCE_ID))
    assert cli.responses == []


class _HangingProcess:
    returncode = None

    def __init__(self) -> None:
        self.terminated = False
        self.killed = False

    def terminate(self) -> None:
        self.terminated = True

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        await asyncio.Event().wait()
        return 0


async def test_process_termination_and_output_drain_are_bounded(monkeypatch) -> None:
    monkeypatch.setattr(isolation_module, "_PROCESS_TERMINATE_GRACE_SEC", 0.001)
    monkeypatch.setattr(isolation_module, "_OUTPUT_DRAIN_GRACE_SEC", 0.001)
    process = _HangingProcess()
    started = time.monotonic()

    await isolation_module._terminate_process(cast(asyncio.subprocess.Process, process))
    output_tasks = (
        asyncio.create_task(_never_returns()),
        asyncio.create_task(_never_returns()),
    )
    assert await isolation_module._collect_output_tasks(*output_tasks) == ("", "")
    await asyncio.sleep(0)

    assert time.monotonic() - started < 1
    assert process.terminated is True
    assert process.killed is True
    assert all(task.cancelled() for task in output_tasks)


async def _never_returns() -> str:
    await asyncio.Event().wait()
    return ""


async def test_pause_timeout_error_remains_a_deadline_failure(tmp_path) -> None:
    responses = _success_responses()[:4]
    responses.extend(
        [
            _raises("pause", SOURCE_ID, error=TimeoutError()),
            _exact("unpause", SOURCE_ID, return_code=1),
            _exact(
                "inspect",
                "--format",
                "{{json .State}}",
                SOURCE_ID,
                stdout='{"Status":"running","Running":true,"Paused":false}\n',
            ),
        ]
    )
    cli = ScriptedDockerCli(responses)

    with pytest.raises(CompletionIsolationError, match=r"Docker pause .* timed out") as caught:
        await _provider(tmp_path, cli).verify(
            CompletionIsolationRequest(
                attempt_id=1,
                work_epoch=1,
                checks=(_check(),),
                deadline_monotonic=10**12,
            ),
            _execute_check,
        )

    assert caught.value.kind is IsolationFailureKind.DEADLINE
    assert ("unpause", SOURCE_ID) in cli.calls
    assert cli.responses == []


async def test_cleanup_has_one_bounded_deadline_for_all_owned_resources(tmp_path) -> None:
    responses = _success_responses(
        child_diff="C /app/existing.txt\n",
        classification="f\n",
    )
    inspector_classify = next(
        index
        for index, response in enumerate(responses)
        if response.matches(("exec", INSPECTOR_ID, "sh", "-c"))
    )
    del responses[inspector_classify:]
    responses.extend(
        [
            _Response(
                matches=lambda actual: actual[:4] == ("exec", INSPECTOR_ID, "sh", "-c"),
                result=RuntimeError("inspection failed"),
            ),
            _exact("unpause", SOURCE_ID, stdout=SOURCE_ID, elapsed_sec=15),
            _exact(
                "inspect",
                "--format",
                "{{json .State}}",
                SOURCE_ID,
                stdout='{"Status":"running","Running":true,"Paused":false}\n',
                elapsed_sec=10,
            ),
            _exact("rm", "-f", CHILD_ID, stdout=CHILD_ID, elapsed_sec=15),
            _exact("rm", "-f", INSPECTOR_ID, stdout=INSPECTOR_ID, elapsed_sec=15),
            _prefix("image", "rm", IMAGE_ID, stdout=IMAGE_ID, elapsed_sec=20),
        ]
    )
    clock = _Clock()
    cli = ScriptedDockerCli(responses, clock=clock)

    with pytest.raises(CompletionIsolationError, match="inspection failed"):
        await _provider(tmp_path, cli, clock=clock).verify(
            CompletionIsolationRequest(
                attempt_id=1,
                work_epoch=1,
                checks=(_check(),),
                deadline_monotonic=1_000,
            ),
            _execute_check,
        )

    assert clock.value == 75
    assert cli.timeouts[-5:] == [15, 10, 15, 15, 20]
    assert cli.responses == []


async def test_cleanup_hard_deadline_cancels_a_stuck_docker_call(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(isolation_module, "_CLEANUP_OPERATION_TIMEOUT_SEC", 0.01)
    monkeypatch.setattr(isolation_module, "_CLEANUP_HARD_TIMEOUT_SEC", 0.02)
    responses = [
        *_success_responses()[:4],
        _raises("pause", SOURCE_ID, error=TimeoutError()),
    ]
    cli = HangingCleanupDockerCli(responses)
    started = time.monotonic()

    with pytest.raises(CompletionIsolationError, match="cleanup exceeded its hard deadline"):
        await _provider(tmp_path, cli).verify(
            CompletionIsolationRequest(
                attempt_id=1,
                work_epoch=1,
                checks=(_check(),),
                deadline_monotonic=10**12,
            ),
            _execute_check,
        )

    assert time.monotonic() - started < 1
    assert ("unpause", SOURCE_ID) in cli.calls
    assert cli.responses == []
