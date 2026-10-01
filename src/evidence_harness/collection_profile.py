from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from evidence_harness.protocol import CollectionAttestation, ProducerAttestation

type JsonScalar = str | int | float | bool | None
type CollectionProfileName = Literal["prefixbench-v1"]

_PROVENANCE_OPTION_KEYS = frozenset(
    {
        "prefixbench_profile",
        "producer_commit",
        "producer_tree",
        "producer_source_sha256",
    }
)


@dataclass(frozen=True, slots=True)
class FrozenCollectionProfile:
    name: CollectionProfileName
    controlled_agent_options: tuple[tuple[str, JsonScalar], ...]
    journal_schema_version: Literal[2] = 2

    @property
    def reserved_agent_option_keys(self) -> frozenset[str]:
        return _PROVENANCE_OPTION_KEYS | {key for key, _value in self.controlled_agent_options}

    def reject_reserved_overrides(self, raw_agent_kwargs: Sequence[str]) -> None:
        overridden = sorted(
            {
                raw.partition("=")[0].strip()
                for raw in raw_agent_kwargs
                if raw.partition("=")[0].strip() in self.reserved_agent_option_keys
            }
        )
        if overridden:
            raise ValueError(
                "collection profile options cannot be overridden: " + ", ".join(overridden)
            )

    def validate_effective_options(self, options: Mapping[str, object]) -> None:
        changed = [
            key for key, expected in self.controlled_agent_options if options.get(key) != expected
        ]
        if changed:
            raise ValueError(f"{self.name} controlled options do not match: " + ", ".join(changed))

    def harbor_agent_kwargs(
        self,
        producer: ProducerAttestation,
    ) -> tuple[str, ...]:
        controlled = tuple(
            f"{key}={_wire_value(value)}"
            for key, value in self.controlled_agent_options
            if value is not None
        )
        return (
            *controlled,
            f"prefixbench_profile={self.name}",
            f"producer_commit={producer.commit}",
            f"producer_tree={producer.tree}",
            f"producer_source_sha256={producer.source_sha256}",
        )

    def attestation(
        self,
        producer: ProducerAttestation,
        effective_options: Mapping[str, object],
    ) -> CollectionAttestation:
        self.validate_effective_options(effective_options)
        return CollectionAttestation(
            prefixbench_profile=self.name,
            producer=producer,
            options=dict(self.controlled_agent_options),
        )


PREFIXBENCH_V1 = FrozenCollectionProfile(
    name="prefixbench-v1",
    controlled_agent_options=(
        ("max_turns", 40),
        ("max_environment_calls", 80),
        ("max_repairs", 4),
        ("max_recoveries", 2),
        ("max_completion_reviews", 2),
        ("max_wall_time_sec", 1_800),
        ("max_model_call_timeout_sec", 360),
        ("max_command_timeout_sec", 300),
        ("verification_environment_reserve", 3),
        ("recent_observation_count", 8),
        ("output_inline_bytes", 12_000),
        ("context_max_chars", 80_000),
        ("enable_completion_review", True),
        ("max_output_tokens", 8_192),
        ("transport_attempts", 3),
        ("temperature", None),
        ("reasoning_effort", None),
    ),
)


def collection_profile(name: str) -> FrozenCollectionProfile:
    if name != PREFIXBENCH_V1.name:
        raise ValueError(f"unsupported collection profile: {name}")
    return PREFIXBENCH_V1


def reject_provenance_overrides(raw_agent_kwargs: Sequence[str]) -> None:
    overridden = sorted(
        {
            raw.partition("=")[0].strip()
            for raw in raw_agent_kwargs
            if raw.partition("=")[0].strip() in _PROVENANCE_OPTION_KEYS
        }
    )
    if overridden:
        raise ValueError("collection provenance options are reserved: " + ", ".join(overridden))


def _wire_value(value: JsonScalar) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)
