from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from evidence_harness.protocol import (
    AgentDecision,
    EnvironmentResult,
    ReviewDecision,
    UsageTotals,
)


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
