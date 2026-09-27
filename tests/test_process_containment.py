from __future__ import annotations

import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
from harbor.models.environment_type import EnvironmentType

from evidence_harness.process_containment import _bounded_command, contain_harbor_environment


@dataclass
class _Result:
    return_code: int = 0
    stdout: str | None = ""
    stderr: str | None = ""


class _Environment:
    def __init__(
        self,
        environment_type: str | EnvironmentType,
        *,
        simulate_timeout: bool = False,
        echo_command: bool = False,
    ) -> None:
        self.environment_type = environment_type
        self.simulate_timeout = simulate_timeout
        self.echo_command = echo_command
        self.calls: list[tuple[str, str | None, int | None]] = []

    def type(self) -> str | EnvironmentType:
        return self.environment_type

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        timeout_sec: int | None = None,
    ) -> _Result:
        self.calls.append((command, cwd, timeout_sec))
        if self.echo_command:
            return _Result(stdout=command)
        if not self.simulate_timeout:
            return _Result(stdout="done\n")
        marker_line = [
            line
            for line in command.splitlines()
            if line.startswith("  printf '%s%s\\n' __EVIDENCE_HARNESS_TIMEOUT_")
        ][-1]
        tokens = shlex.split(marker_line)
        return _Result(return_code=124, stdout=f"{tokens[2]}{tokens[3]}\n")


@pytest.mark.asyncio
async def test_docker_command_uses_an_in_container_timeout_with_host_cleanup_grace() -> None:
    environment = _Environment(EnvironmentType.DOCKER)
    contained = contain_harbor_environment(environment)

    result = await contained.exec("sleep 300", cwd="/app", timeout_sec=7)

    assert result.stdout == "done\n"
    [(command, cwd, timeout_sec)] = environment.calls
    assert cwd == "/app"
    assert timeout_sec is not None and timeout_sec > 7
    assert "timeout --signal=TERM --kill-after=5s 7s bash -c" in command
    assert "sleep 300" in command
    assert 'status_file="${TMPDIR:-/tmp}/.evidence-harness-status-' in command


@pytest.mark.asyncio
async def test_in_container_timeout_is_reported_as_timeout() -> None:
    environment = _Environment("docker", simulate_timeout=True)
    contained = contain_harbor_environment(environment)

    with pytest.raises(TimeoutError, match="Command timed out after 7 seconds"):
        await contained.exec("sleep 300", timeout_sec=7)


@pytest.mark.asyncio
async def test_process_listing_cannot_echo_the_timeout_marker() -> None:
    environment = _Environment("docker", echo_command=True)
    contained = contain_harbor_environment(environment)

    result = await contained.exec("pgrep -af worker", timeout_sec=7)

    assert result.stdout
    assert re.search(r"__EVIDENCE_HARNESS_TIMEOUT_[0-9a-f]{32}__", result.stdout) is None


@pytest.mark.parametrize("return_code", [124, 137])
def test_command_return_code_is_not_misclassified_as_timeout(
    tmp_path: Path,
    return_code: int,
) -> None:
    timeout = tmp_path / "timeout"
    timeout.write_text(
        """#!/bin/sh
if [ "$1" = "--help" ]; then
  printf '%s\n' '--kill-after'
  exit 0
fi
while [ "$#" -gt 0 ]; do
  case "$1" in
    --signal=*|--kill-after=*|*[0-9]s) shift ;;
    *) break ;;
  esac
done
"$@"
""",
        encoding="utf-8",
    )
    timeout.chmod(0o755)
    timeout_marker = "__EVIDENCE_HARNESS_TIMEOUT_test__"
    script = _bounded_command(
        f"exit {return_code}",
        timeout_sec=7,
        timeout_marker=timeout_marker,
        unavailable_marker="__EVIDENCE_HARNESS_TIMEOUT_UNAVAILABLE_test__",
    )

    result = subprocess.run(
        ["bash", "-c", script],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"},
    )

    assert result.returncode == return_code
    assert timeout_marker not in result.stderr


@pytest.mark.asyncio
async def test_non_docker_environment_is_not_wrapped() -> None:
    environment = _Environment("remote")
    contained = contain_harbor_environment(environment)

    await contained.exec("printf ok", cwd="/workspace", timeout_sec=7)

    assert contained is environment
    assert environment.calls == [("printf ok", "/workspace", 7)]
