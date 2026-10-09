from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from evidence_harness.completion_contract import CompletionContract
from evidence_harness.protocol import (
    CommandReceipt,
    CompletionIsolationEvidence,
    RequirementCoverage,
    RunPhase,
    VerificationCheck,
)


@dataclass(frozen=True, slots=True)
class CandidateIdentity:
    algorithm: str
    value: str

    def __post_init__(self) -> None:
        if not self.algorithm:
            raise ValueError("candidate identity algorithm must not be empty")
        if not self.value:
            raise ValueError("candidate identity value must not be empty")


@dataclass(frozen=True, slots=True)
class CurrentCandidate:
    pass


@dataclass(frozen=True, slots=True)
class CompletionProposal:
    checks: tuple[VerificationCheck, ...]
    coverage: tuple[RequirementCoverage, ...]
    candidate: CurrentCandidate
    summary: str | None = None


@dataclass(frozen=True, slots=True)
class CompletionPolicy:
    review_required: bool
    max_check_timeout_sec: int

    def __post_init__(self) -> None:
        if self.max_check_timeout_sec < 1:
            raise ValueError("maximum check timeout must be positive")


@dataclass(frozen=True, slots=True)
class CompletionCounters:
    turns: int
    environment_calls: int
    repairs: int
    recoveries: int
    completion_reviews: int


@dataclass(frozen=True, slots=True)
class CompletionState:
    phase: RunPhase
    work_epoch: int
    next_completion_attempt: int
    candidate_digest: str | None
    deadline_monotonic: float
    counters: CompletionCounters


@dataclass(frozen=True, slots=True)
class CompletionTrace:
    receipts: tuple[CommandReceipt, ...] = ()


@dataclass(frozen=True, slots=True)
class CompletionTransactionView:
    proposal: CompletionProposal
    contract: CompletionContract
    policy: CompletionPolicy
    trace: CompletionTrace
    state: CompletionState


@dataclass(frozen=True, slots=True)
class CandidateSnapshot:
    identity: CandidateIdentity
    candidate_digest: str
    attempt_id: int
    work_epoch: int


@dataclass(frozen=True, slots=True)
class IsolatedCheckRun:
    snapshot: CandidateSnapshot
    receipts: tuple[CommandReceipt, ...]
    isolation: CompletionIsolationEvidence


class HarnessAdapterError(RuntimeError):
    def __init__(
        self,
        kind: str,
        detail: str,
        *,
        attempt_id: int,
    ) -> None:
        super().__init__(detail)
        self.kind = kind
        self.detail = detail
        self.attempt_id = attempt_id


class HarnessAdapter(ABC):
    @property
    @abstractmethod
    def view(self) -> CompletionTransactionView: ...

    @abstractmethod
    async def run_checks_isolated(self) -> IsolatedCheckRun: ...
