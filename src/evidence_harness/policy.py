from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence

from evidence_harness.protocol import CommandMode, CommandReceipt, ShellCommand, VerificationCheck

_FORBIDDEN_PATH = re.compile(
    r"""(?ix)
    (?:^|[\s"'=])
    (?:
        /(?:tests|solution|solutions|verifier|reward)(?:/|[\s"']|$)
        |/logs/verifier(?:/|[\s"']|$)
        |\.\./(?:tests|solution|solutions|verifier|reward)(?:/|[\s"']|$)
        |/var/run/docker\.sock(?:[\s"']|$)
        |/(?:root/)?\.ssh(?:/|[\s"']|$)
        |/(?:root/)?\.aws/credentials(?:[\s"']|$)
        |/proc/(?:self|\d+)/environ(?:[\s"']|$)
    )
    """
)
_BARE_ENV_DUMP = re.compile(r"(?m)^\s*(?:env|printenv|set)\s*(?:[;&|]\s*)?$")
_TRIVIAL_CHECK = re.compile(
    r"""(?ix)^\s*(?:
        true|:|exit\s+0|echo(?:\s+.*)?|printf(?:\s+.*)?|pwd|
        ls(?:\s+.*)?|find(?:\s+.*)?|cat\s+\S+|head(?:\s+.*)?|tail(?:\s+.*)?
    )\s*;?\s*$"""
)
_CHECK_MUTATION = re.compile(
    r"""(?ix)
    (?:
        (^|[;&|]\s*)(?:rm|mv|cp|install|mkdir|touch|truncate|chmod|chown)\s
        |\bsed\s+[^\n;]*\s-i(?:\s|$)
        |(?:^|[^<>])>{1,2}(?!=)
        |\btee\s+
        |\b(?:apt|apt-get|dnf|yum|apk|brew)\s+(?:install|remove|upgrade)\b
        |\b(?:pip|pip3|npm|pnpm|yarn)\s+(?:install|uninstall|add|remove)\b
        |\bgit\s+(?:commit|reset|checkout|switch|clean|rebase|merge)\b
    )
    """
)
_WHITESPACE = re.compile(r"\s+")
_SENSITIVE_VALUE = re.compile(
    r"""(?ix)
    \b(api[_-]?key|access[_-]?token|auth(?:orization)?|password|passwd|secret)
    (\s*[:=]\s*)
    (["']?)[^\s"'`]+(\3)
    """
)
_BEARER_TOKEN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_OPAQUE_SECRET = re.compile(
    r"""(?x)\b(?:
        sk-(?:proj-)?[A-Za-z0-9_-]{16,}
        |gh[pousr]_[A-Za-z0-9]{20,}
        |xox[baprs]-[A-Za-z0-9-]{16,}
        |AKIA[A-Z0-9]{16}
    )\b"""
)
_JWT = re.compile(
    r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"
)


class PolicyViolation(ValueError):
    pass


def normalize_command(script: str) -> str:
    return _WHITESPACE.sub(" ", script.strip())


def command_fingerprint(script: str, cwd: str | None) -> str:
    normalized = f"{cwd or '<default>'}\0{normalize_command(script)}"
    return hashlib.sha256(normalized.encode()).hexdigest()


def observation_fingerprint(
    command_hash: str,
    return_code: int | None,
    failure: str | None,
    stdout_hash: str,
    stderr_hash: str,
) -> str:
    value = "\0".join(
        [
            command_hash,
            str(return_code),
            failure or "",
            stdout_hash,
            stderr_hash,
        ]
    )
    return hashlib.sha256(value.encode()).hexdigest()


def redact_sensitive(text: str) -> str:
    text = _SENSITIVE_VALUE.sub(r"\1\2[REDACTED]", text)
    text = _BEARER_TOKEN.sub("Bearer [REDACTED]", text)
    text = _OPAQUE_SECRET.sub("[REDACTED]", text)
    return _JWT.sub("[REDACTED]", text)


def validate_command(command: ShellCommand, max_timeout_sec: int) -> None:
    _validate_script(command.script, command.timeout_sec, max_timeout_sec)


def validate_check(check: VerificationCheck, max_timeout_sec: int) -> None:
    _validate_script(check.script, check.timeout_sec, max_timeout_sec)
    if _TRIVIAL_CHECK.fullmatch(check.script):
        raise PolicyViolation(f"verification check '{check.id}' is display-only or a no-op")
    if _CHECK_MUTATION.search(check.script):
        raise PolicyViolation(f"verification check '{check.id}' appears to modify task state")


def _validate_script(script: str, timeout_sec: int, max_timeout_sec: int) -> None:
    if not script.strip():
        raise PolicyViolation("empty command")
    if timeout_sec > max_timeout_sec:
        raise PolicyViolation(
            f"command timeout {timeout_sec}s exceeds policy maximum {max_timeout_sec}s"
        )
    if _FORBIDDEN_PATH.search(script):
        raise PolicyViolation("command references benchmark, credential, or host-control paths")
    if _BARE_ENV_DUMP.search(script):
        raise PolicyViolation("bulk environment dumps are not allowed")


def repeated_command_block_reason(
    command: ShellCommand,
    receipts: Sequence[CommandReceipt],
) -> str | None:
    if not receipts:
        return None
    previous = receipts[-1]
    current_hash = command_fingerprint(command.script, command.cwd)
    if previous.command_fingerprint != current_hash:
        return None
    if command.mode is CommandMode.OBSERVE and command.repeat_reason:
        return None
    return (
        f"command '{command.id}' repeats the immediately preceding command without "
        "an observed state change"
    )


def find_repeated_cycle(
    receipts: Sequence[CommandReceipt],
    *,
    repeats: int = 3,
    max_period: int = 3,
    window: int = 20,
) -> tuple[str, ...] | None:
    signatures = [
        f"{item.command_fingerprint}:{item.observation_fingerprint}"
        for item in receipts[-window:]
    ]
    for period in range(1, max_period + 1):
        required = period * repeats
        if len(signatures) < required:
            continue
        tail = signatures[-required:]
        cycle = tail[:period]
        if all(tail[index : index + period] == cycle for index in range(0, required, period)):
            return tuple(cycle)
    return None
