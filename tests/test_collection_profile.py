from __future__ import annotations

from dataclasses import asdict

import pytest
from pydantic import ValidationError

from evidence_harness.collection_profile import PREFIXBENCH_V1
from evidence_harness.harbor_agent import EvidenceHarnessOptions
from evidence_harness.protocol import ProducerAttestation


def _producer() -> ProducerAttestation:
    return ProducerAttestation(
        commit="a" * 40,
        tree="b" * 40,
        source_sha256="c" * 64,
    )


def _profile_options() -> dict[str, object]:
    return dict(PREFIXBENCH_V1.controlled_agent_options)


def test_prefixbench_profile_expands_frozen_options_and_provenance() -> None:
    kwargs = PREFIXBENCH_V1.harbor_agent_kwargs(_producer())

    assert kwargs[:4] == (
        "max_turns=40",
        "max_environment_calls=80",
        "max_repairs=4",
        "max_recoveries=2",
    )
    assert "enable_completion_review=true" in kwargs
    assert not any(item.startswith("temperature=") for item in kwargs)
    assert kwargs[-4:] == (
        "prefixbench_profile=prefixbench-v1",
        f"producer_commit={'a' * 40}",
        f"producer_tree={'b' * 40}",
        f"producer_source_sha256={'c' * 64}",
    )


def test_prefixbench_profile_rejects_controlled_overrides() -> None:
    with pytest.raises(ValueError, match="max_turns"):
        PREFIXBENCH_V1.reject_reserved_overrides(["api_base=https://example.test", "max_turns=41"])


def test_harbor_options_require_complete_profile_provenance() -> None:
    with pytest.raises(ValidationError, match="provided together"):
        EvidenceHarnessOptions(prefixbench_profile="prefixbench-v1")


def test_harbor_options_reject_profile_policy_drift() -> None:
    values = {
        **_profile_options(),
        "max_turns": 41,
        "prefixbench_profile": "prefixbench-v1",
        "producer_commit": "a" * 40,
        "producer_tree": "b" * 40,
        "producer_source_sha256": "c" * 64,
    }
    with pytest.raises(ValidationError, match="max_turns"):
        EvidenceHarnessOptions(**values)


def test_collection_attestation_does_not_enter_loop_options() -> None:
    options = EvidenceHarnessOptions(
        **_profile_options(),
        prefixbench_profile="prefixbench-v1",
        producer_commit="a" * 40,
        producer_tree="b" * 40,
        producer_source_sha256="c" * 64,
    )

    collection = options.collection_attestation()

    assert collection is not None
    assert collection.producer == _producer()
    assert collection.options == _profile_options()
    assert "prefixbench_profile" not in asdict(options.loop_options())
