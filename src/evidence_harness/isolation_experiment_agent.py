from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from harbor.agents.base import BaseAgent
from harbor.agents.capabilities import AgentCapabilities
from harbor.agents.model_connection import ModelConnectionSpec
from harbor.agents.options import AgentOptions
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext, ModelUsage
from pydantic import Field

from evidence_harness.docker_completion_isolation import (
    completion_isolation_for_harbor,
)
from evidence_harness.journal import RunJournal
from evidence_harness.model_client import LiteLLMModelGateway
from evidence_harness.process_containment import contain_harbor_environment
from evidence_harness.protocol import (
    ActionKind,
    AgentDecision,
    LoopOptions,
    ReviewDecision,
    UsageTotals,
)
from evidence_harness.replay_agent import (
    load_recorded_replay_batches,
    replay_recorded_batches,
)
from evidence_harness.run_loop import EvidenceLoop
from evidence_harness.shell import CommandRunner
from evidence_harness.source_binding import runtime_source_binding

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOCKER_ISOLATION_SOURCE = Path(__file__).with_name("docker_completion_isolation.py")
EXPERIMENT_AGENT_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
DOCKER_ISOLATION_SOURCE_SHA256 = hashlib.sha256(DOCKER_ISOLATION_SOURCE.read_bytes()).hexdigest()
RUNTIME_SOURCE_SHA256 = str(runtime_source_binding(PROJECT_ROOT)["sha256"])


class CompletionIsolationExperimentOptions(AgentOptions):
    corpus_path: Path
    journal_path: Path
    case_id: str
    attempt_ordinal: int = Field(default=1, ge=1)
    max_command_timeout_sec: int = Field(default=300, ge=5, le=900)
    max_wall_time_sec: int = Field(default=900, ge=60, le=3_600)
    max_model_call_timeout_sec: int = Field(default=360, ge=30, le=1_800)
    output_inline_bytes: int = Field(default=12_000, ge=1_000, le=100_000)
    max_output_tokens: int = Field(default=4_096, ge=1_024, le=64_000)
    transport_attempts: int = Field(default=3, ge=1, le=6)
    api_base: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    reasoning_effort: (
        Literal["none", "minimal", "low", "medium", "high", "xhigh", "max", "default"] | None
    ) = None


@dataclass(frozen=True, slots=True)
class CompletionCandidate:
    case_id: str
    task_name: str
    attempt_ordinal: int
    source_journal_sha256: str
    proposal_event_line_sha256: str
    decision: AgentDecision


class _RecordedFinishModel:
    def __init__(
        self,
        decision: AgentDecision,
        reviewer: LiteLLMModelGateway,
    ) -> None:
        self._decision = decision
        self._reviewer = reviewer
        self._decision_used = False

    @property
    def usage(self) -> UsageTotals:
        return self._reviewer.usage

    async def decide(self, prompt: str) -> AgentDecision:
        del prompt
        if self._decision_used:
            raise RuntimeError("the recorded completion proposal was already used")
        self._decision_used = True
        return self._decision

    async def review(self, prompt: str) -> ReviewDecision:
        return await self._reviewer.review(prompt)


class CompletionIsolationExperimentAgent(BaseAgent):
    capabilities = AgentCapabilities()
    options_model = CompletionIsolationExperimentOptions
    options: CompletionIsolationExperimentOptions
    MODEL_CONNECTION = ModelConnectionSpec()

    @staticmethod
    def name() -> str:
        return "completion-isolation-experiment"

    def version(self) -> str:
        return "0.1.0"

    async def setup(self, environment: BaseEnvironment) -> None:
        del environment
        root = self.logs_dir / "completion-isolation-experiment"
        (root / "replay").mkdir(parents=True, exist_ok=True)
        (root / "completion").mkdir(parents=True, exist_ok=True)

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        if not self.model_name:
            raise ValueError("CompletionIsolationExperimentAgent requires Harbor --model/-m")

        root = self.logs_dir / "completion-isolation-experiment"
        source_bytes = self.options.journal_path.read_bytes()
        candidate = load_completion_candidate(
            self.options.corpus_path,
            case_id=self.options.case_id,
            attempt_ordinal=self.options.attempt_ordinal,
            source_journal_bytes=source_bytes,
        )
        batches = load_recorded_replay_batches(source_bytes)
        replay_journal = RunJournal(
            root / "replay",
            inline_bytes=self.options.output_inline_bytes,
        )
        replay_options = LoopOptions(
            max_command_timeout_sec=self.options.max_command_timeout_sec,
        )
        replay_runner = CommandRunner(
            contain_harbor_environment(environment),
            replay_journal,
            replay_options,
        )
        replay_journal.append(
            "replay_started",
            {
                "case_id": candidate.case_id,
                "source_journal_sha256": candidate.source_journal_sha256,
                "batch_count": len(batches),
                "command_count": sum(len(batch) for batch in batches),
            },
        )
        replay_receipts = await replay_recorded_batches(batches, replay_runner)
        replay_journal.append(
            "replay_finished",
            {
                "case_id": candidate.case_id,
                "receipt_count": len(replay_receipts),
            },
        )

        completion_journal = RunJournal(
            root / "completion",
            inline_bytes=self.options.output_inline_bytes,
        )
        connection = self.model_connection
        reviewer = LiteLLMModelGateway(
            model_name=self.model_name,
            journal=completion_journal,
            api_key=connection.api_key,
            api_base=self.options.api_base or connection.configured_base_url,
            temperature=self.options.temperature,
            reasoning_effort=self.options.reasoning_effort,
            max_output_tokens=self.options.max_output_tokens,
            transport_attempts=self.options.transport_attempts,
        )
        model = _RecordedFinishModel(candidate.decision, reviewer)
        loop_options = LoopOptions(
            max_turns=1,
            max_environment_calls=max(4, len(candidate.decision.checks) + 1),
            max_repairs=0,
            max_recoveries=0,
            max_completion_reviews=1,
            max_wall_time_sec=self.options.max_wall_time_sec,
            max_model_call_timeout_sec=self.options.max_model_call_timeout_sec,
            max_command_timeout_sec=self.options.max_command_timeout_sec,
            verification_environment_reserve=1,
            output_inline_bytes=self.options.output_inline_bytes,
        )
        loop = EvidenceLoop(
            model=model,
            journal=completion_journal,
            options=loop_options,
            completion_isolation=completion_isolation_for_harbor(
                journal=completion_journal,
                environment=environment,
            ),
        )
        report = await loop.run(instruction, contain_harbor_environment(environment))
        usage = reviewer.usage
        context.n_input_tokens = usage.input_tokens
        context.n_cache_tokens = usage.cache_tokens
        context.n_output_tokens = usage.output_tokens
        context.cost_usd = usage.cost_usd
        context.model_usage = {
            self.model_name: ModelUsage(
                n_input_tokens=usage.input_tokens,
                n_cache_tokens=usage.cache_tokens,
                n_output_tokens=usage.output_tokens,
                cost_usd=usage.cost_usd,
            )
        }
        context.metadata = {
            "completion_isolation_experiment": {
                "schema_version": 1,
                "case_id": candidate.case_id,
                "task_name": candidate.task_name,
                "attempt_ordinal": candidate.attempt_ordinal,
                "source_journal_sha256": candidate.source_journal_sha256,
                "proposal_event_line_sha256": candidate.proposal_event_line_sha256,
                "experiment_agent_source_sha256": EXPERIMENT_AGENT_SOURCE_SHA256,
                "docker_isolation_source_sha256": DOCKER_ISOLATION_SOURCE_SHA256,
                "runtime_source_sha256": RUNTIME_SOURCE_SHA256,
                "replay_command_count": len(replay_receipts),
                "replay_last_sequence": replay_receipts[-1].sequence,
                "replay_last_work_epoch": replay_receipts[-1].work_epoch,
                "stop_reason": report.stop_reason,
                "failure_category": report.failure_category,
                "latest_evidence": (
                    report.latest_evidence.model_dump(mode="json")
                    if report.latest_evidence is not None
                    else None
                ),
            }
        }


def load_completion_candidate(
    corpus_path: Path,
    *,
    case_id: str,
    attempt_ordinal: int,
    source_journal_bytes: bytes,
) -> CompletionCandidate:
    corpus = _object(json.loads(corpus_path.read_bytes()), "completion corpus")
    cases = corpus.get("cases")
    if not isinstance(cases, list):
        raise ValueError("completion corpus cases must be an array")
    matching_cases = [
        _object(value, "completion case")
        for value in cases
        if isinstance(value, dict) and value.get("case_id") == case_id
    ]
    if len(matching_cases) != 1:
        raise ValueError(f"expected one completion case for {case_id}, found {len(matching_cases)}")
    case = matching_cases[0]
    source = _object(case.get("sources"), "completion case sources")
    journal = _object(source.get("journal"), "completion case journal source")
    expected_journal_sha256 = _required_string(journal, "sha256")
    actual_journal_sha256 = hashlib.sha256(source_journal_bytes).hexdigest()
    if actual_journal_sha256 != expected_journal_sha256:
        raise ValueError(
            "source journal hash does not match the frozen completion case: "
            f"{actual_journal_sha256} != {expected_journal_sha256}"
        )

    attempts = case.get("attempts")
    if not isinstance(attempts, list):
        raise ValueError("completion case attempts must be an array")
    matching_attempts = [
        _object(value, "completion attempt")
        for value in attempts
        if isinstance(value, dict) and value.get("ordinal") == attempt_ordinal
    ]
    if len(matching_attempts) != 1:
        raise ValueError(
            f"expected one completion attempt {attempt_ordinal}, found {len(matching_attempts)}"
        )
    attempt = matching_attempts[0]
    if attempt.get("later_change_receipts") != []:
        raise ValueError("completion candidate has later change receipts")
    proposal = _object(attempt.get("proposal"), "completion proposal")
    corpus_decision = AgentDecision.model_validate(
        {
            "action": ActionKind.FINISH,
            **proposal,
        }
    )
    proposal_event = _object(attempt.get("proposal_event"), "completion proposal event")
    proposal_line = _required_int(proposal_event, "line")
    source_lines = source_journal_bytes.splitlines(keepends=True)
    if proposal_line > len(source_lines):
        raise ValueError("completion proposal line is outside the source journal")
    raw_proposal = source_lines[proposal_line - 1]
    proposal_sha256 = _required_string(proposal_event, "line_sha256")
    if hashlib.sha256(raw_proposal).hexdigest() != proposal_sha256:
        raise ValueError("completion proposal line hash does not match the source journal")
    recorded_event = _object(json.loads(raw_proposal), "completion proposal source event")
    if recorded_event.get("type") != "agent_decision":
        raise ValueError("completion proposal source event is not an agent decision")
    recorded_decision = AgentDecision.model_validate(recorded_event.get("payload"))
    if _completion_proposal(recorded_decision) != _completion_proposal(corpus_decision):
        raise ValueError("completion proposal does not match the source journal")
    return CompletionCandidate(
        case_id=case_id,
        task_name=_required_string(case, "task_name"),
        attempt_ordinal=attempt_ordinal,
        source_journal_sha256=expected_journal_sha256,
        proposal_event_line_sha256=proposal_sha256,
        decision=recorded_decision,
    )


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _required_string(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ValueError(f"{key} must be a non-empty string")
    return item


def _required_int(value: dict[str, Any], key: str) -> int:
    item = value.get(key)
    if not isinstance(item, int) or isinstance(item, bool) or item < 1:
        raise ValueError(f"{key} must be a positive integer")
    return item


def _completion_proposal(decision: AgentDecision) -> dict[str, Any]:
    return decision.model_dump(
        mode="json",
        include={"rationale", "summary", "checks", "coverage"},
    )
