from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from evidence_harness.evaluation import EvaluationMatrix
from evidence_harness_mutation import tb21_sensitivity_protocol as protocol

PROJECT_ROOT = Path(__file__).resolve().parents[2]
MATRIX_PATH = PROJECT_ROOT / protocol.SENSITIVITY_MATRIX
PROTOCOL_PATH = PROJECT_ROOT / protocol.SENSITIVITY_PROTOCOL


def _git_oid(value: str) -> str:
    return hashlib.sha1(value.encode(), usedforsecurity=False).hexdigest()


def _fake_tb20() -> tuple[protocol.Tb20RepositorySnapshot, dict[str, str]]:
    source = EvaluationMatrix.model_validate_json(
        (PROJECT_ROOT / protocol._KNOWN_INPUTS[0][0]).read_bytes()
    )
    return protocol.Tb20RepositorySnapshot(), {
        task.name: _git_oid(f"tb20:{task.name}") for task in source.tasks
    } | {f"development-{index}": _git_oid(f"tb20:development-{index}") for index in range(28)}


def _fake_tb21() -> tuple[
    protocol.Tb21RepositorySnapshot,
    dict[str, str],
    dict[str, tuple[str, str]],
]:
    tb20, tb20_trees = _fake_tb20()
    del tb20
    trees = {name: _git_oid(f"tb21:{name}") for name in tb20_trees}
    packages = {
        name: (
            f"terminal-bench/{name}",
            f"sha256:{hashlib.sha256(f'package:{name}'.encode()).hexdigest()}",
        )
        for name in tb20_trees
    }
    snapshot = protocol.Tb21RepositorySnapshot(
        manifest=protocol.GitBlobBinding(
            path="tasks/dataset.toml",
            git_blob=protocol.TB21_MANIFEST_BLOB,
            bytes=12570,
            sha256=protocol.TB21_MANIFEST_SHA256,
        )
    )
    return snapshot, trees, packages


def _mock_external_sources(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(protocol, "_load_tb20_snapshot", lambda _path: _fake_tb20())
    monkeypatch.setattr(protocol, "_load_tb21_snapshot", lambda _path: _fake_tb21())


def test_build_matrix_preserves_frozen_test_order_and_separates_identities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _mock_external_sources(monkeypatch)

    matrix = protocol._build_matrix(
        PROJECT_ROOT,
        protocol.ExternalRepositories(Path("/tb20"), Path("/tb21")),
    )
    source = EvaluationMatrix.model_validate_json(
        (PROJECT_ROOT / protocol._KNOWN_INPUTS[0][0]).read_bytes()
    )

    assert tuple(task.match_name for task in matrix.tasks) == tuple(
        task.name for task in source.tasks
    )
    assert len(matrix.tasks) == 61
    assert all(
        task.tb20.source_identity_sha256 != task.tb21.source_identity_sha256
        for task in matrix.tasks
    )
    assert matrix.tasks[0].tb21.registry_name == (f"terminal-bench/{matrix.tasks[0].match_name}")


def test_matrix_and_protocol_bytes_are_canonical_and_current() -> None:
    matrix_data = MATRIX_PATH.read_bytes()
    matrix = protocol.Tb21SensitivityMatrix.model_validate_json(matrix_data)
    protocol_data = PROTOCOL_PATH.read_bytes()
    frozen = protocol.Tb21SensitivityProtocol.model_validate_json(protocol_data)

    assert matrix_data == matrix.canonical_bytes()
    assert protocol_data == frozen.canonical_bytes()
    assert frozen == protocol._current_protocol(PROJECT_ROOT, matrix)


def test_protocol_freezes_separate_sensitivity_contract() -> None:
    frozen = protocol.Tb21SensitivityProtocol.model_validate_json(PROTOCOL_PATH.read_bytes())

    assert frozen.protocol_id == "thesis-tb21-sensitivity-v1"
    assert frozen.analysis.tasks == 61
    assert frozen.analysis.methods == tuple(protocol.MainAnalysisMethod)
    assert frozen.analysis.outcomes == tuple(protocol.MainAnalysisOutcome)
    assert frozen.analysis.choices.namespace == "thesis-tb21-sensitivity-v1"
    assert frozen.analysis.holm_family == "tb21-sensitivity-rq2-v1-four-contrasts"
    assert frozen.separation.cross_version_pooling == "forbidden"
    assert frozen.separation.main_rq_disposition_changes == "forbidden"
    assert frozen.protected_sources.sha256 == protocol.FROZEN_TEST_SOURCE_SET_SHA256


def test_protocol_contains_no_checkout_or_credential_path() -> None:
    frozen = protocol.Tb21SensitivityProtocol.model_validate_json(PROTOCOL_PATH.read_bytes())
    serialized = json.dumps(frozen.model_dump(mode="json"), sort_keys=True)

    assert "/tmp/harness4-terminal-bench" not in serialized
    assert "provider.env" not in serialized
    assert "credential_sha256" not in serialized


def test_source_identity_is_length_framed_and_domain_separated() -> None:
    tb20 = protocol._source_identity("tb20-git-task-v1", "ab", "c")
    ambiguous = protocol._source_identity("tb20-git-task-v1", "a", "bc")
    tb21 = protocol._source_identity("tb21-registry-task-v1", "ab", "c")

    assert tb20 != ambiguous
    assert tb20 != tb21
    assert len(tb20) == 64


def test_matrix_rejects_stale_correspondence_identity() -> None:
    matrix = protocol.Tb21SensitivityMatrix.model_validate_json(MATRIX_PATH.read_bytes())
    payload = matrix.model_dump(mode="json")
    payload["tasks"][0]["correspondence_sha256"] = "0" * 64

    with pytest.raises(ValidationError, match="correspondence identity is stale"):
        protocol.Tb21SensitivityMatrix.model_validate(payload)


def test_protocol_rejects_method_order_drift() -> None:
    frozen = protocol.Tb21SensitivityProtocol.model_validate_json(PROTOCOL_PATH.read_bytes())
    payload = frozen.model_dump(mode="json")
    payload["analysis"]["methods"][0], payload["analysis"]["methods"][1] = (
        payload["analysis"]["methods"][1],
        payload["analysis"]["methods"][0],
    )

    with pytest.raises(ValidationError, match="method order has changed"):
        protocol.Tb21SensitivityProtocol.model_validate(payload)


def test_manifest_parser_rejects_duplicate_package_digests() -> None:
    rows = []
    for index in range(89):
        digest_source = "duplicate" if index < 2 else f"task-{index}"
        digest = hashlib.sha256(digest_source.encode()).hexdigest()
        rows.extend(
            (
                "[[tasks]]",
                f'name = "terminal-bench/task-{index}"',
                f'digest = "sha256:{digest}"',
            )
        )
    data = "\n".join(
        (
            "[dataset]",
            'name = "terminal-bench/terminal-bench-2-1"',
            *rows,
            "",
        )
    ).encode()

    with pytest.raises(ValueError, match=r"duplicate TB2\.1 package digest"):
        protocol._parse_tb21_manifest(data)


def test_freeze_rejects_existing_artifact_before_reading_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "outcome.json").write_text("{}\n", encoding="utf-8")
    input_read = False

    def unexpected_build(
        _root: Path,
        _repositories: protocol.ExternalRepositories,
    ) -> protocol.Tb21SensitivityMatrix:
        nonlocal input_read
        input_read = True
        raise AssertionError("protocol inputs were read")

    monkeypatch.setattr(protocol, "_project_root", lambda _path: tmp_path)
    monkeypatch.setattr(protocol, "_PREFREEZE_PATHS", ("outcome.json",))
    monkeypatch.setattr(protocol, "_build_matrix", unexpected_build)

    with pytest.raises(ValueError, match="must precede artifacts"):
        protocol.freeze_tb21_sensitivity_protocol(
            tmp_path,
            protocol.ExternalRepositories(Path("/tb20"), Path("/tb21")),
        )

    assert input_read is False


def test_freeze_rejects_deleted_artifact_history_before_reading_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    input_read = False

    def unexpected_build(
        _root: Path,
        _repositories: protocol.ExternalRepositories,
    ) -> protocol.Tb21SensitivityMatrix:
        nonlocal input_read
        input_read = True
        raise AssertionError("protocol inputs were read")

    monkeypatch.setattr(protocol, "_project_root", lambda _path: tmp_path)
    monkeypatch.setattr(protocol, "_PREFREEZE_PATHS", ("deleted.json",))
    monkeypatch.setattr(protocol, "_path_has_git_history", lambda *_args: True)
    monkeypatch.setattr(protocol, "_build_matrix", unexpected_build)

    with pytest.raises(ValueError, match="already exists in Git history"):
        protocol.freeze_tb21_sensitivity_protocol(
            tmp_path,
            protocol.ExternalRepositories(Path("/tb20"), Path("/tb21")),
        )

    assert input_read is False


def test_freeze_rejects_a_broken_symlink_before_reading_inputs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "outcome.json").symlink_to(tmp_path / "missing.json")
    input_read = False

    def unexpected_build(
        _root: Path,
        _repositories: protocol.ExternalRepositories,
    ) -> protocol.Tb21SensitivityMatrix:
        nonlocal input_read
        input_read = True
        raise AssertionError("protocol inputs were read")

    monkeypatch.setattr(protocol, "_project_root", lambda _path: tmp_path)
    monkeypatch.setattr(protocol, "_PREFREEZE_PATHS", ("outcome.json",))
    monkeypatch.setattr(protocol, "_build_matrix", unexpected_build)

    with pytest.raises(ValueError, match="must precede artifacts"):
        protocol.freeze_tb21_sensitivity_protocol(
            tmp_path,
            protocol.ExternalRepositories(Path("/tb20"), Path("/tb21")),
        )

    assert input_read is False


def test_load_requires_matrix_and_protocol_to_first_appear_together(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matrix = protocol.Tb21SensitivityMatrix.model_validate_json(MATRIX_PATH.read_bytes())
    frozen = protocol.Tb21SensitivityProtocol.model_validate_json(PROTOCOL_PATH.read_bytes())
    monkeypatch.setattr(protocol, "_current_protocol", lambda *_args: frozen)
    monkeypatch.setattr(protocol, "_validate_committed_input", lambda *_args: None)
    monkeypatch.setattr(
        protocol,
        "_first_matching_commit",
        lambda _root, path, _data: "a" * 40 if path == protocol.SENSITIVITY_MATRIX else "b" * 40,
    )

    with pytest.raises(ValueError, match="must first appear in the same commit"):
        protocol.load_tb21_sensitivity_protocol(PROJECT_ROOT)

    assert len(matrix.tasks) == 61


def test_check_returns_a_stable_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(_root: Path) -> protocol.BoundTb21SensitivityProtocol:
        raise ValueError("stale")

    monkeypatch.setattr(protocol, "load_tb21_sensitivity_protocol", fail)

    assert protocol.check_tb21_sensitivity_protocol(PROJECT_ROOT) == (
        "invalid TB2.1 sensitivity preregistration: stale",
    )
