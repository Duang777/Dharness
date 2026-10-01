from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from evidence_harness.protocol import ProducerAttestation
from evidence_harness.source_binding import (
    archived_runtime_source_binding,
    attest_git_runtime_source,
    runtime_source_binding,
    verify_git_runtime_source,
)


def _git(root: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _source_repo(root: Path) -> None:
    package = root / "src" / "evidence_harness"
    package.mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
    (root / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    (package / "__init__.py").write_text('VALUE = "committed"\n', encoding="utf-8")
    (package / "worker.py").write_text("def work():\n    return 1\n", encoding="utf-8")
    _git(root, "init")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "add", ".")
    _git(root, "commit", "-m", "fixture")


def test_git_attestation_binds_workspace_to_head_archive(tmp_path: Path) -> None:
    _source_repo(tmp_path)

    workspace = runtime_source_binding(tmp_path)
    archived = archived_runtime_source_binding(tmp_path)
    producer = attest_git_runtime_source(tmp_path)

    assert workspace == archived
    assert producer.commit == _git(tmp_path, "rev-parse", "HEAD")
    assert producer.tree == _git(tmp_path, "rev-parse", "HEAD^{tree}")
    assert producer.source_sha256 == workspace["sha256"]
    assert verify_git_runtime_source(tmp_path, producer) == producer


@pytest.mark.parametrize("change", ["modified", "untracked", "deleted"])
def test_git_attestation_rejects_runtime_source_drift(
    tmp_path: Path,
    change: str,
) -> None:
    _source_repo(tmp_path)
    package = tmp_path / "src" / "evidence_harness"
    if change == "modified":
        (package / "worker.py").write_text("def work():\n    return 2\n", encoding="utf-8")
    elif change == "untracked":
        (package / "new.py").write_text("NEW = True\n", encoding="utf-8")
    else:
        (package / "worker.py").unlink()

    with pytest.raises(ValueError, match="differs from git archive"):
        attest_git_runtime_source(tmp_path)


def test_runtime_binding_rejects_symlinked_source(tmp_path: Path) -> None:
    package = tmp_path / "src" / "evidence_harness"
    package.mkdir(parents=True)
    target = tmp_path / "target.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    (package / "linked.py").symlink_to(target)

    with pytest.raises(ValueError, match="regular file"):
        runtime_source_binding(tmp_path)


def test_runtime_verification_rejects_a_different_expected_producer(
    tmp_path: Path,
) -> None:
    _source_repo(tmp_path)
    actual = attest_git_runtime_source(tmp_path)
    expected = actual.model_copy(update={"source_sha256": "0" * 64})

    with pytest.raises(ValueError, match="source_sha256"):
        verify_git_runtime_source(tmp_path, expected)


def test_producer_attestation_accepts_sha256_git_object_ids() -> None:
    producer = ProducerAttestation(
        commit="a" * 64,
        tree="b" * 64,
        source_sha256="c" * 64,
    )

    assert len(producer.commit) == 64
    assert len(producer.tree) == 64
