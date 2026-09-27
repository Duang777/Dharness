from __future__ import annotations

import secrets
import shlex

from evidence_harness.protocol import EnvironmentResult, ShellEnvironment

_CONTAINER_KILL_GRACE_SEC = 5
_HOST_TIMEOUT_GRACE_SEC = 10
_TIMEOUT_MARKER_PREFIX = "__EVIDENCE_HARNESS_TIMEOUT_"
_TIMEOUT_UNAVAILABLE_MARKER_PREFIX = "__EVIDENCE_HARNESS_TIMEOUT_UNAVAILABLE_"


class _ProcessContainedEnvironment:
    def __init__(self, environment: ShellEnvironment) -> None:
        self._environment = environment

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        timeout_sec: int | None = None,
    ) -> EnvironmentResult:
        if timeout_sec is None:
            return await self._environment.exec(command=command, cwd=cwd)

        nonce = secrets.token_hex(16)
        timeout_marker = f"{_TIMEOUT_MARKER_PREFIX}{nonce}__"
        unavailable_marker = f"{_TIMEOUT_UNAVAILABLE_MARKER_PREFIX}{nonce}__"
        result = await self._environment.exec(
            command=_bounded_command(
                command,
                timeout_sec=timeout_sec,
                timeout_marker=timeout_marker,
                unavailable_marker=unavailable_marker,
            ),
            cwd=cwd,
            timeout_sec=timeout_sec + _HOST_TIMEOUT_GRACE_SEC,
        )
        output = (result.stdout or "") + (result.stderr or "")
        if timeout_marker in output:
            raise TimeoutError(f"Command timed out after {timeout_sec} seconds")
        if unavailable_marker in output:
            raise RuntimeError(
                "Docker task environment does not provide a compatible timeout command"
            )
        return result


def contain_harbor_environment(environment: ShellEnvironment) -> ShellEnvironment:
    environment_type = getattr(environment, "type", None)
    if not callable(environment_type):
        return environment
    detected_type = environment_type()
    if getattr(detected_type, "value", detected_type) != "docker":
        return environment
    return _ProcessContainedEnvironment(environment)


def _bounded_command(
    command: str,
    *,
    timeout_sec: int,
    timeout_marker: str,
    unavailable_marker: str,
) -> str:
    quoted_command = shlex.quote(command)
    status_file_token = secrets.token_hex(16)
    quoted_runner = shlex.quote(
        'bash -c "$1"\ncommand_status=$?\nprintf "%s\\n" "$command_status" > "$2"\nexit 0'
    )
    timeout_split = len(timeout_marker) // 2
    quoted_timeout_marker_parts = (
        shlex.quote(timeout_marker[:timeout_split]),
        shlex.quote(timeout_marker[timeout_split:]),
    )
    unavailable_split = len(unavailable_marker) // 2
    quoted_unavailable_marker_parts = (
        shlex.quote(unavailable_marker[:unavailable_split]),
        shlex.quote(unavailable_marker[unavailable_split:]),
    )
    return f"""\
set +e
status_file="${{TMPDIR:-/tmp}}/.evidence-harness-status-{status_file_token}-$$"
rm -f "$status_file"
trap 'rm -f "$status_file"' EXIT
if ! command -v timeout >/dev/null 2>&1; then
  printf '%s%s\\n' {quoted_unavailable_marker_parts[0]} \
{quoted_unavailable_marker_parts[1]} >&2
  exit 125
fi
if timeout --help 2>&1 | grep -q -- '--kill-after'; then
  timeout --signal=TERM --kill-after={_CONTAINER_KILL_GRACE_SEC}s \
{timeout_sec}s bash -c {quoted_runner} _ {quoted_command} "$status_file"
else
  timeout -s TERM -k {_CONTAINER_KILL_GRACE_SEC} {timeout_sec} \
bash -c {quoted_runner} _ {quoted_command} "$status_file"
fi
status=$?
if [ -f "$status_file" ]; then
  command_status="$(cat "$status_file")"
  exit "$command_status"
fi
if [ "$status" -eq 124 ] || [ "$status" -eq 137 ]; then
  printf '%s%s\\n' {quoted_timeout_marker_parts[0]} \
{quoted_timeout_marker_parts[1]} >&2
fi
exit "$status"
"""
