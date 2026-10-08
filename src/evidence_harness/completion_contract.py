from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evidence_harness.protocol import CheckKind, LoopOptions


class _FrozenModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)


class TaskRequirement(_FrozenModel):
    id: str = Field(min_length=1, max_length=1_000)
    statement: str = Field(min_length=1, max_length=4_000)
    evidence_kinds: tuple[CheckKind, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_evidence_kinds(self) -> Self:
        if len(self.evidence_kinds) != len(set(self.evidence_kinds)):
            raise ValueError("requirement evidence kinds must be unique")
        return self


class EvidenceOrderRequirements(_FrozenModel):
    execute_in_proposal_order: Literal[True] = True


class EvidenceValidityRequirements(_FrozenModel):
    require_fresh: Literal[True] = True
    require_success: Literal[True] = True
    require_integrity: Literal[True] = True
    require_process_bound: Literal[True] = True
    require_isolation: Literal[True] = True


class CompletionBudget(_FrozenModel):
    max_turns: int = Field(ge=1)
    max_environment_calls: int = Field(ge=1)
    max_repairs: int = Field(ge=0)
    max_recoveries: int = Field(ge=0)
    max_completion_reviews: int = Field(ge=0)
    max_wall_time_sec: int = Field(ge=1)
    verification_environment_reserve: int = Field(ge=0)

    @classmethod
    def from_options(cls, options: LoopOptions) -> Self:
        return cls(
            max_turns=options.max_turns,
            max_environment_calls=options.max_environment_calls,
            max_repairs=options.max_repairs,
            max_recoveries=options.max_recoveries,
            max_completion_reviews=options.max_completion_reviews,
            max_wall_time_sec=options.max_wall_time_sec,
            verification_environment_reserve=options.verification_environment_reserve,
        )


class CompletionContract(_FrozenModel):
    schema_version: Literal[1] = 1
    e_req: tuple[TaskRequirement, ...] = Field(min_length=1, max_length=20)
    o_req: EvidenceOrderRequirements = Field(default_factory=EvidenceOrderRequirements)
    v_req: EvidenceValidityRequirements = Field(default_factory=EvidenceValidityRequirements)
    b_req: CompletionBudget

    @model_validator(mode="after")
    def validate_requirement_ids(self) -> Self:
        requirement_ids = tuple(item.id.casefold() for item in self.e_req)
        if len(requirement_ids) != len(set(requirement_ids)):
            raise ValueError("completion contract requirement ids must be unique")
        return self

    @classmethod
    def create(
        cls,
        *,
        requirements: tuple[TaskRequirement, ...],
        options: LoopOptions,
    ) -> Self:
        return cls(
            e_req=requirements,
            b_req=CompletionBudget.from_options(options),
        )

    @classmethod
    def from_instruction(cls, instruction: str, options: LoopOptions) -> Self:
        return cls.create(
            requirements=(
                TaskRequirement(
                    id="REQ-1",
                    statement=instruction,
                    evidence_kinds=tuple(CheckKind),
                ),
            ),
            options=options,
        )
