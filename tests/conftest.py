from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from evidence_harness.completion_isolation import (
    CompletionIsolationError,
    CompletionIsolationRequest,
    CompletionIsolationResult,
)
from evidence_harness.protocol import (
    AgentDecision,
    CheckIsolationEvidence,
    CompletionIsolationEvidence,
    EnvironmentResult,
    FilesystemDelta,
    IsolationCost,
    ReviewDecision,
    ShellEnvironment,
    SourceAttestation,
    UsageTotals,
)

_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


@dataclass
class FakeExecResult:
    return_code: int = 0
    stdout: str | None = ""
    stderr: str | None = ""


class FakeEnvironment:
    def __init__(
        self,
        responses: dict[str, Iterable[FakeExecResult]] | None = None,
    ) -> None:
        self.calls: list[tuple[str, str | None, int | None]] = []
        self._responses: defaultdict[str, list[FakeExecResult]] = defaultdict(list)
        for command, values in (responses or {}).items():
            self._responses[command].extend(values)

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        timeout_sec: int | None = None,
    ) -> EnvironmentResult:
        self.calls.append((command, cwd, timeout_sec))
        queued = self._responses[command]
        if queued:
            return queued.pop(0)
        if command.startswith("set +e\n"):
            return FakeExecResult(stdout="/workspace\n")
        return FakeExecResult()


class FakeCompletionIsolation:
    def __init__(
        self,
        environment: ShellEnvironment,
        *,
        failure: CompletionIsolationError | None = None,
    ) -> None:
        self.environment = environment
        self.failure = failure
        self.requests: list[CompletionIsolationRequest] = []

    async def verify(self, request, execute):
        self.requests.append(request)
        if self.failure is not None:
            raise self.failure

        receipts = []
        isolated_checks = []
        for index, check in enumerate(request.checks, start=1):
            receipt = await execute(check, self.environment, request.deadline_monotonic)
            receipts.append(receipt)
            isolated_checks.append(
                CheckIsolationEvidence(
                    check_id=check.id,
                    receipt_sequence=receipt.sequence,
                    receipt_observation_sha256=receipt.observation_fingerprint,
                    child_id_sha256=f"{request.attempt_id:032x}{index:032x}",
                    started_from_image_id="sha256:test-candidate",
                    delta=FilesystemDelta(sha256=_EMPTY_SHA256),
                    disposed=True,
                )
            )
            if not receipt.succeeded:
                break

        return CompletionIsolationResult(
            receipts=tuple(receipts),
            evidence=CompletionIsolationEvidence(
                backend="test-isolation-v1",
                attempt_id=request.attempt_id,
                work_epoch=request.work_epoch,
                candidate_image_id="sha256:test-candidate",
                environment_identity_sha256="a" * 64,
                checks=tuple(isolated_checks),
                source=SourceAttestation(
                    container_id_sha256="b" * 64,
                    diff_sha256_before=_EMPTY_SHA256,
                    diff_sha256_after=_EMPTY_SHA256,
                    remained_paused=True,
                    resumed=True,
                ),
                snapshot_image_disposed=True,
                cost=IsolationCost(
                    host_operations=len(isolated_checks),
                    child_count=len(isolated_checks),
                    duration_sec=0,
                ),
            ),
        )


class ScriptedModel:
    def __init__(
        self,
        decisions: Iterable[AgentDecision],
        reviews: Iterable[ReviewDecision] = (),
    ) -> None:
        self._decisions = list(decisions)
        self._reviews = list(reviews)
        self.prompts: list[str] = []
        self.review_prompts: list[str] = []
        self._usage = UsageTotals()

    @property
    def usage(self) -> UsageTotals:
        return self._usage.model_copy()

    async def decide(self, prompt: str) -> AgentDecision:
        self.prompts.append(prompt)
        self._usage.model_calls += 1
        if not self._decisions:
            raise AssertionError("scripted model has no executor decision left")
        return self._decisions.pop(0)

    async def review(self, prompt: str) -> ReviewDecision:
        self.review_prompts.append(prompt)
        self._usage.model_calls += 1
        if not self._reviews:
            return ReviewDecision(verdict="accept", rationale="checks cover the task")
        return self._reviews.pop(0)
