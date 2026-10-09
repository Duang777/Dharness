from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from evidence_harness.protocol import (
    CommandReceipt,
    CompletionIsolationEvidence,
    ShellEnvironment,
    VerificationCheck,
)


class IsolationFailureKind(StrEnum):
    UNSUPPORTED = "completion_isolation_unsupported"
    PREPARE = "completion_isolation_prepare"
    CHILD_START = "completion_isolation_child_start"
    INSPECTION = "completion_isolation_inspection"
    DEADLINE = "completion_isolation_deadline"
    CLEANUP = "completion_isolation_cleanup"


@dataclass(frozen=True, slots=True)
class CompletionIsolationRequest:
    attempt_id: int
    work_epoch: int
    checks: tuple[VerificationCheck, ...]
    deadline_monotonic: float


@dataclass(frozen=True, slots=True)
class CompletionIsolationResult:
    receipts: tuple[CommandReceipt, ...]
    evidence: CompletionIsolationEvidence


CheckExecutor = Callable[
    [VerificationCheck, ShellEnvironment, float, str],
    Awaitable[CommandReceipt],
]


class CompletionIsolation(Protocol):
    async def verify(
        self,
        request: CompletionIsolationRequest,
        execute: CheckExecutor,
    ) -> CompletionIsolationResult: ...


class CompletionIsolationError(RuntimeError):
    def __init__(
        self,
        kind: IsolationFailureKind,
        detail: str,
        *,
        attempt_id: int,
    ) -> None:
        super().__init__(detail)
        self.kind = kind
        self.detail = detail
        self.attempt_id = attempt_id


class UnsupportedCompletionIsolation:
    def __init__(self, detail: str) -> None:
        self._detail = detail

    async def verify(
        self,
        request: CompletionIsolationRequest,
        execute: CheckExecutor,
    ) -> CompletionIsolationResult:
        del execute
        raise CompletionIsolationError(
            IsolationFailureKind.UNSUPPORTED,
            self._detail,
            attempt_id=request.attempt_id,
        )
