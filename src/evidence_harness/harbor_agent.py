from __future__ import annotations

from typing import Literal

from harbor.agents.base import BaseAgent
from harbor.agents.capabilities import AgentCapabilities
from harbor.agents.model_connection import ModelConnectionSpec
from harbor.agents.options import AgentOptions
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext, ModelUsage
from pydantic import Field, model_validator

from evidence_harness.journal import RunJournal
from evidence_harness.model_client import LiteLLMModelGateway
from evidence_harness.protocol import LoopOptions, RunState
from evidence_harness.run_loop import EvidenceLoop


class EvidenceHarnessOptions(AgentOptions):
    max_turns: int = Field(default=40, ge=2, le=200)
    max_environment_calls: int = Field(default=80, ge=4, le=500)
    max_repairs: int = Field(default=4, ge=0, le=20)
    max_recoveries: int = Field(default=2, ge=0, le=10)
    max_completion_reviews: int = Field(default=4, ge=0, le=20)
    max_wall_time_sec: int = Field(default=1_800, ge=60, le=14_400)
    max_command_timeout_sec: int = Field(default=300, ge=5, le=3_600)
    verification_environment_reserve: int = Field(default=3, ge=1, le=10)
    recent_observation_count: int = Field(default=8, ge=2, le=30)
    output_inline_bytes: int = Field(default=12_000, ge=1_000, le=100_000)
    context_max_chars: int = Field(default=80_000, ge=10_000, le=500_000)
    enable_completion_review: bool = True
    max_output_tokens: int = Field(default=8_192, ge=1_024, le=64_000)
    transport_attempts: int = Field(default=3, ge=1, le=6)
    api_base: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    reasoning_effort: (
        Literal["none", "minimal", "low", "medium", "high", "xhigh", "max", "default"]
        | None
    ) = None

    @model_validator(mode="after")
    def validate_reserve(self) -> EvidenceHarnessOptions:
        if self.verification_environment_reserve >= self.max_environment_calls:
            raise ValueError(
                "verification_environment_reserve must be smaller than "
                "max_environment_calls"
            )
        return self

    def loop_options(self) -> LoopOptions:
        return LoopOptions(
            max_turns=self.max_turns,
            max_environment_calls=self.max_environment_calls,
            max_repairs=self.max_repairs,
            max_recoveries=self.max_recoveries,
            max_completion_reviews=self.max_completion_reviews,
            max_wall_time_sec=self.max_wall_time_sec,
            max_command_timeout_sec=self.max_command_timeout_sec,
            verification_environment_reserve=self.verification_environment_reserve,
            recent_observation_count=self.recent_observation_count,
            output_inline_bytes=self.output_inline_bytes,
            context_max_chars=self.context_max_chars,
            enable_completion_review=self.enable_completion_review,
        )


class EvidenceHarnessAgent(BaseAgent):
    capabilities = AgentCapabilities()
    options_model = EvidenceHarnessOptions
    options: EvidenceHarnessOptions
    MODEL_CONNECTION = ModelConnectionSpec()

    @staticmethod
    def name() -> str:
        return "evidence-harness"

    def version(self) -> str:
        return "0.1.0"

    async def setup(self, environment: BaseEnvironment) -> None:
        del environment
        (self.logs_dir / "evidence-harness").mkdir(parents=True, exist_ok=True)

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        if not self.model_name:
            raise ValueError("EvidenceHarnessAgent requires Harbor --model/-m")

        root = self.logs_dir / "evidence-harness"
        journal = RunJournal(root, inline_bytes=self.options.output_inline_bytes)
        connection = self.model_connection
        model = LiteLLMModelGateway(
            model_name=self.model_name,
            journal=journal,
            api_key=connection.api_key,
            api_base=self.options.api_base or connection.configured_base_url,
            temperature=self.options.temperature,
            reasoning_effort=self.options.reasoning_effort,
            max_output_tokens=self.options.max_output_tokens,
            transport_attempts=self.options.transport_attempts,
        )

        def update_context(state: RunState) -> None:
            usage = model.usage
            context.n_input_tokens = usage.input_tokens
            context.n_cache_tokens = usage.cache_tokens
            context.n_output_tokens = usage.output_tokens
            context.cost_usd = usage.cost_usd
            context.model_usage = {
                self.model_name or "unknown": ModelUsage(
                    n_input_tokens=usage.input_tokens,
                    n_cache_tokens=usage.cache_tokens,
                    n_output_tokens=usage.output_tokens,
                    cost_usd=usage.cost_usd,
                )
            }
            context.metadata = {
                **(context.metadata or {}),
                "evidence_harness": {
                    "phase": state.phase,
                    "turns_used": state.turn_count,
                    "environment_calls_used": state.environment_call_count,
                    "repairs_used": state.repair_count,
                    "recoveries_used": state.recovery_count,
                    "work_epoch": state.work_epoch,
                    "stop_reason": state.stop_reason,
                    "journal": str(root / "events.jsonl"),
                },
            }

        loop = EvidenceLoop(
            model=model,
            journal=journal,
            options=self.options.loop_options(),
            on_progress=update_context,
        )
        report = await loop.run(instruction, environment)
        metadata = context.metadata or {}
        metadata["evidence_harness"] = {
            **metadata.get("evidence_harness", {}),
            "stop_reason": report.stop_reason,
            "failure_category": report.failure_category,
            "final_summary": report.final_summary,
            "latest_evidence_accepted": (
                report.latest_evidence.accepted if report.latest_evidence else False
            ),
        }
        context.metadata = metadata
