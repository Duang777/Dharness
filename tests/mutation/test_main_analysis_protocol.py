from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from evidence_harness_mutation import main_analysis_protocol as protocol

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_PATH = PROJECT_ROOT / protocol.MAIN_PROTOCOL


def test_freeze_captures_the_fixed_main_analysis_contract() -> None:
    frozen = protocol.freeze_main_analysis_protocol(PROJECT_ROOT)

    assert frozen.protocol_id == "thesis-main-analysis-v1"
    assert frozen.rq_order == ("RQ1", "RQ2", "RQ3", "RQ4")
    assert frozen.rq1.evidence_role == "locked_retrospective"
    assert frozen.rq2.tasks == 61
    assert frozen.rq2.methods == tuple(protocol.MainAnalysisMethod)
    assert frozen.rq2.outcomes == tuple(protocol.MainAnalysisOutcome)
    assert frozen.rq3.reducers == tuple(protocol.MainAnalysisReducer)
    assert frozen.rq3.minimum_counterexamples == 10
    assert frozen.rq3.minimum_tasks == 5
    assert frozen.rq4.tasks == 20
    assert tuple(family.status for family in frozen.rq4.families) == (
        "mapped",
        "mapped",
        "unsupported_by_design",
        "mapped",
    )
    assert frozen.development.claim_status == "descriptive_not_evaluated"
    assert frozen.protected_sources.sha256 == protocol.FROZEN_TEST_SOURCE_SET_SHA256
    assert tuple(item.path for item in frozen.protocol_sources.files) == (
        protocol._IMPLEMENTATION_SOURCE_PATHS
    )


def test_protocol_bytes_are_canonical_and_match_the_current_sources() -> None:
    data = PROTOCOL_PATH.read_bytes()
    frozen = protocol.MainAnalysisProtocol.model_validate_json(data)

    assert data == frozen.canonical_bytes()
    assert frozen == protocol.freeze_main_analysis_protocol(PROJECT_ROOT)


def test_known_input_bindings_match_fixed_hashes() -> None:
    frozen = protocol.freeze_main_analysis_protocol(PROJECT_ROOT)

    assert tuple((item.path, item.sha256) for item in frozen.known_inputs) == (
        protocol._KNOWN_INPUTS
    )
    for binding in frozen.known_inputs:
        data = (PROJECT_ROOT / binding.path).read_bytes()
        assert len(data) == binding.bytes
        assert hashlib.sha256(data).hexdigest() == binding.sha256


def test_protected_sources_match_the_design_base() -> None:
    frozen = protocol.freeze_main_analysis_protocol(PROJECT_ROOT)

    assert tuple(item.path for item in frozen.protected_sources.files) == (
        protocol._PROTECTED_SOURCE_PATHS
    )
    for binding in frozen.protected_sources.files:
        amended_sha256 = protocol._PROTECTED_SOURCE_AMENDMENTS.get(binding.path)
        if amended_sha256 is not None:
            assert binding.sha256 == amended_sha256
            continue
        committed = protocol._run_git(
            PROJECT_ROOT,
            "show",
            f"{protocol.DESIGN_BASE_COMMIT}:{binding.path}",
        )
        assert len(committed) == binding.bytes
        assert hashlib.sha256(committed).hexdigest() == binding.sha256


def test_protocol_contains_no_future_producer_or_credential_binding() -> None:
    frozen = protocol.freeze_main_analysis_protocol(PROJECT_ROOT)
    serialized = json.dumps(frozen.model_dump(mode="json"), sort_keys=True)

    assert '"producer_commit"' not in serialized
    assert '"producer_tree"' not in serialized
    assert "provider.env" not in serialized
    assert "credential_sha256" not in serialized


def test_freeze_rejects_existing_outcome_before_reading_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "outcome.json").write_text("{}\n", encoding="utf-8")
    read_attempted = False

    def unexpected_read(_root: Path) -> protocol.MainAnalysisProtocol:
        nonlocal read_attempted
        read_attempted = True
        raise AssertionError("protocol inputs were read")

    monkeypatch.setattr(protocol, "_project_root", lambda _root: tmp_path)
    monkeypatch.setattr(protocol, "_OUTCOME_PATHS", ("outcome.json",))
    monkeypatch.setattr(protocol, "_current_protocol", unexpected_read)

    with pytest.raises(ValueError, match="must precede outcome artifacts"):
        protocol.freeze_main_analysis_protocol(tmp_path)

    assert read_attempted is False


def test_freeze_rejects_deleted_outcome_with_all_refs_history_before_reading_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    read_attempted = False

    def unexpected_read(_root: Path) -> protocol.MainAnalysisProtocol:
        nonlocal read_attempted
        read_attempted = True
        raise AssertionError("protocol inputs were read")

    monkeypatch.setattr(protocol, "_project_root", lambda _root: tmp_path)
    monkeypatch.setattr(protocol, "_OUTCOME_PATHS", ("deleted-outcome.json",))
    monkeypatch.setattr(protocol, "_path_has_git_history", lambda *_args: True)
    monkeypatch.setattr(protocol, "_current_protocol", unexpected_read)

    with pytest.raises(ValueError, match="already exists in Git history"):
        protocol.freeze_main_analysis_protocol(tmp_path)

    assert read_attempted is False


def test_protocol_rejects_method_order_drift() -> None:
    frozen = protocol.freeze_main_analysis_protocol(PROJECT_ROOT)
    payload = frozen.model_dump(mode="json")
    payload["rq2"]["methods"][0], payload["rq2"]["methods"][1] = (
        payload["rq2"]["methods"][1],
        payload["rq2"]["methods"][0],
    )

    with pytest.raises(ValidationError, match="RQ2 method order has changed"):
        protocol.MainAnalysisProtocol.model_validate(payload)


def test_protocol_rejects_reducer_comparison_order_drift() -> None:
    frozen = protocol.freeze_main_analysis_protocol(PROJECT_ROOT)
    payload = frozen.model_dump(mode="json")
    payload["rq3"]["comparison_order"] = ["hdd", "flat_ddmin", "no_reduction"]

    with pytest.raises(ValidationError, match="RQ3 comparison order has changed"):
        protocol.MainAnalysisProtocol.model_validate(payload)


def test_protocol_rejects_transfer_denominator_drift() -> None:
    frozen = protocol.freeze_main_analysis_protocol(PROJECT_ROOT)
    payload = frozen.model_dump(mode="json")
    payload["rq4"]["families"][2] = {
        "source_invariant": "I3",
        "target": "invented-reviewer",
        "status": "mapped",
    }

    with pytest.raises(ValidationError, match="RQ4 family mapping has changed"):
        protocol.MainAnalysisProtocol.model_validate(payload)


def test_load_binds_protocol_to_first_matching_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    frozen = protocol.freeze_main_analysis_protocol(PROJECT_ROOT)
    data = frozen.canonical_bytes()
    monkeypatch.setattr(protocol, "_current_protocol", lambda _root: frozen)
    monkeypatch.setattr(protocol, "_validate_committed_input", lambda *_args: None)
    monkeypatch.setattr(protocol, "_first_protocol_commit", lambda *_args: "a" * 40)
    monkeypatch.setattr(protocol, "_require_git_ancestor", lambda *_args: None)
    original_read = protocol._read_regular_file

    def read(path: Path, *, label: str) -> bytes:
        if path == PROTOCOL_PATH:
            return data
        return original_read(path, label=label)

    monkeypatch.setattr(protocol, "_read_regular_file", read)

    bound = protocol.load_main_analysis_protocol(PROJECT_ROOT)

    assert bound.spec == frozen
    assert bound.preregistration_commit == "a" * 40
    assert bound.file.sha256 == hashlib.sha256(data).hexdigest()


def test_check_returns_a_stable_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(_root: Path) -> protocol.BoundMainAnalysisProtocol:
        raise ValueError("stale")

    monkeypatch.setattr(protocol, "load_main_analysis_protocol", fail)

    assert protocol.check_main_analysis_protocol(PROJECT_ROOT) == (
        "invalid main-analysis protocol: stale",
    )
