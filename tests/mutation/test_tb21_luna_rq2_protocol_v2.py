from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from evidence_harness_mutation import tb21_luna_rq2_protocol_v2 as protocol
from evidence_harness_mutation.main_analysis_protocol import (
    MainAnalysisMethod,
    MainAnalysisOutcome,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_PATH = PROJECT_ROOT / protocol.LUNA_PROTOCOL


def test_protocol_bytes_are_canonical_and_current() -> None:
    data = PROTOCOL_PATH.read_bytes()
    frozen = protocol.LunaRq2ProtocolV2.model_validate_json(data)
    base = protocol.load_tb21_sensitivity_protocol(PROJECT_ROOT)
    prior = protocol.load_luna_rq2_protocol(PROJECT_ROOT)

    assert data == frozen.canonical_bytes()
    assert frozen == protocol._current_protocol(PROJECT_ROOT, base, prior)


def test_protocol_freezes_an_independent_luna_rq2_replication() -> None:
    frozen = protocol.LunaRq2ProtocolV2.model_validate_json(PROTOCOL_PATH.read_bytes())

    assert frozen.protocol_id == "thesis-tb21-luna-rq2-replication-v2"
    assert frozen.analysis.model == "openai/modelhub/gpt-5.6-luna"
    assert frozen.analysis.tasks == 61
    assert frozen.analysis.methods == tuple(MainAnalysisMethod)
    assert frozen.analysis.outcomes == tuple(MainAnalysisOutcome)
    assert frozen.analysis.choices.namespace == ("thesis-tb21-luna-rq2-replication-v1")
    assert frozen.analysis.bootstrap_namespace == (
        "thesis-tb21-luna-rq2-replication-v1/bootstrap-rq2-v1"
    )
    assert frozen.analysis.choices.seed == 20261003
    assert frozen.analysis.bootstrap_resamples == 10000
    assert frozen.claim_boundary.terra_outcomes == "forbidden-input"
    assert frozen.claim_boundary.tb20_outcomes == "forbidden-input"
    assert frozen.claim_boundary.dataset_sensitivity_claim == "forbidden"
    assert frozen.claim_boundary.model_change_causality_claim == "forbidden"


def test_protocol_freezes_the_v1_operational_correction_only() -> None:
    frozen = protocol.LunaRq2ProtocolV2.model_validate_json(PROTOCOL_PATH.read_bytes())
    prior = protocol.load_luna_rq2_protocol(PROJECT_ROOT)

    assert frozen.analysis == prior.spec.analysis
    assert frozen.claim_boundary == prior.spec.claim_boundary
    assert frozen.superseded_protocol == prior.protocol_file
    assert frozen.superseded_executable.path == protocol.V1_EXECUTABLE.as_posix()
    assert frozen.operational_correction.observed_model_calls == 0
    assert frozen.operational_correction.prior_run_handling == (
        "preserve-locally-never-retry-or-input"
    )
    assert frozen.operational_correction.harbor_agent_source == (
        "executable-project-root-src-prepended-to-child-pythonpath"
    )
    assert frozen.operational_correction.result_selection == (
        "exactly-one-trial-result-excluding-job-aggregate"
    )


def test_protocol_reuses_only_the_frozen_tb21_cohort_identity() -> None:
    frozen = protocol.LunaRq2ProtocolV2.model_validate_json(PROTOCOL_PATH.read_bytes())
    base = protocol.load_tb21_sensitivity_protocol(PROJECT_ROOT)

    assert frozen.cohort == base.matrix_file
    assert frozen.tb21 == base.matrix_spec.tb21
    assert frozen.cohort_origin_commit == protocol.COHORT_ORIGIN_COMMIT
    assert len(base.matrix_spec.tasks) == 61
    assert len({task.tb21.source_identity_sha256 for task in base.matrix_spec.tasks}) == 61


def test_protocol_freezes_secretless_provider_transport() -> None:
    frozen = protocol.LunaRq2ProtocolV2.model_validate_json(PROTOCOL_PATH.read_bytes())
    serialized = json.dumps(frozen.model_dump(mode="json"), sort_keys=True)

    assert frozen.provider_environment.required_keys == (
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
    )
    assert frozen.provider_environment.endpoint_sha256 == (
        "42c964b5536d7dd77320e537c8717a08c7397f93e3398db3e7dc1ef923400dac"
    )
    assert frozen.provider_environment.env_file == "forbidden"
    assert frozen.provider_environment.credential_argv == "forbidden"
    assert frozen.provider_environment.persisted_values == "forbidden"
    assert "laneai.dev" not in serialized
    assert re.search(r"\bsk-[0-9a-f]{20,}\b", serialized) is None


def test_protocol_rejects_terra_choice_domain() -> None:
    frozen = protocol.LunaRq2ProtocolV2.model_validate_json(PROTOCOL_PATH.read_bytes())
    payload = frozen.model_dump(mode="json")
    payload["analysis"]["choices"]["namespace"] = "thesis-tb21-sensitivity-v1"

    with pytest.raises(ValidationError):
        protocol.LunaRq2ProtocolV2.model_validate(payload)


def test_provider_bundle_digest_is_framed_and_secretless() -> None:
    first = protocol.provider_bundle_sha256("ab", "c")
    ambiguous = protocol.provider_bundle_sha256("a", "bc")

    assert first != ambiguous
    assert len(first) == 64
    assert "ab" not in first


def test_freeze_checks_future_paths_before_loading_the_cohort(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "outcome.json").write_text("{}\n", encoding="utf-8")
    loaded = False

    def unexpected_load(_root: Path) -> object:
        nonlocal loaded
        loaded = True
        raise AssertionError("cohort was loaded")

    monkeypatch.setattr(protocol, "_project_root", lambda _path: tmp_path)
    monkeypatch.setattr(protocol, "_PREFREEZE_PATHS", ("outcome.json",))
    monkeypatch.setattr(protocol, "load_tb21_sensitivity_protocol", unexpected_load)

    with pytest.raises(ValueError, match="must precede artifacts"):
        protocol.freeze_luna_rq2_protocol_v2(tmp_path)

    assert loaded is False


def test_check_returns_a_stable_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(_root: Path) -> protocol.BoundLunaRq2ProtocolV2:
        raise ValueError("stale")

    monkeypatch.setattr(protocol, "load_luna_rq2_protocol_v2", fail)

    assert protocol.check_luna_rq2_protocol_v2(PROJECT_ROOT) == (
        "invalid Luna RQ2 preregistration: stale",
    )
