from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from evidence_harness.protocol import ProducerAttestation
from scripts import run_evaluation


def _parse_args(monkeypatch: pytest.MonkeyPatch, *args: str):
    monkeypatch.setattr(sys, "argv", ["run_evaluation.py", *args])
    return run_evaluation.parse_args()


def test_build_command_can_select_one_task_and_mount_https_apt_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_evaluation.shutil, "which", lambda _: "/usr/local/bin/harbor")
    args = _parse_args(
        monkeypatch,
        "--model",
        "openai/test-model",
        "--include-task-name",
        "fix-git",
        "--debian-https-sources",
    )

    command = run_evaluation.build_command(args)

    concurrency_index = command.index("--n-concurrent")
    assert command[concurrency_index + 1] == "1"
    assert command.count("--include-task-name") == 1
    task_index = command.index("--include-task-name")
    assert command[task_index + 1] == "fix-git"

    mounts_index = command.index("--mounts")
    mounts = json.loads(command[mounts_index + 1])
    assert mounts == [
        {
            "type": "bind",
            "source": str(run_evaluation.DEBIAN_HTTPS_SOURCES.resolve()),
            "target": "/etc/apt/sources.list.d/debian.sources",
            "read_only": True,
        }
    ]
    assert "--yes" in command
    assert (
        run_evaluation.DEBIAN_HTTPS_SOURCES.read_text(encoding="utf-8").count(
            "URIs: https://deb.debian.org"
        )
        == 2
    )


def test_build_command_can_mount_trixie_https_apt_sources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_evaluation.shutil, "which", lambda _: "/usr/local/bin/harbor")
    args = _parse_args(
        monkeypatch,
        "--model",
        "openai/test-model",
        "--matrix",
        str(run_evaluation.PROJECT_ROOT / "evaluation" / "matrix-89.json"),
        "--include-task-name",
        "build-pmars",
        "--debian-trixie-https-sources",
    )

    command = run_evaluation.build_command(args)

    mounts_index = command.index("--mounts")
    mounts = json.loads(command[mounts_index + 1])
    assert mounts[0]["source"] == str(run_evaluation.DEBIAN_TRIXIE_HTTPS_SOURCES.resolve())
    assert (
        run_evaluation.DEBIAN_TRIXIE_HTTPS_SOURCES.read_text(encoding="utf-8").count(
            "URIs: https://deb.debian.org"
        )
        == 2
    )


def test_build_command_can_mount_bullseye_main_sources_without_security(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(run_evaluation.shutil, "which", lambda _: "/usr/local/bin/harbor")
    args = _parse_args(
        monkeypatch,
        "--model",
        "openai/test-model",
        "--matrix",
        str(run_evaluation.PROJECT_ROOT / "evaluation" / "matrix-89.json"),
        "--include-task-name",
        "qemu-startup",
        "--debian-bullseye-main-sources",
    )

    command = run_evaluation.build_command(args)

    mounts_index = command.index("--mounts")
    mounts = json.loads(command[mounts_index + 1])
    assert mounts[0]["source"] == str(run_evaluation.DEBIAN_BULLSEYE_MAIN_SOURCES.resolve())
    assert mounts[0]["target"] == "/etc/apt/sources.list"
    source_text = run_evaluation.DEBIAN_BULLSEYE_MAIN_SOURCES.read_text(encoding="utf-8")
    assert " bullseye main" in source_text
    assert " bullseye-updates main" in source_text
    assert "security" not in source_text


def test_build_command_rejects_multiple_debian_source_mounts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _parse_args(
        monkeypatch,
        "--model",
        "openai/test-model",
        "--debian-https-sources",
        "--debian-bullseye-main-sources",
        "--debian-trixie-https-sources",
    )

    with pytest.raises(ValueError, match="mutually exclusive"):
        run_evaluation.build_command(args)


def test_build_command_rejects_task_outside_fixed_matrix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _parse_args(
        monkeypatch,
        "--model",
        "openai/test-model",
        "--include-task-name",
        "not-in-matrix",
    )

    with pytest.raises(ValueError, match="not in the fixed evaluation matrix"):
        run_evaluation.build_command(args)


def test_build_command_rejects_duplicate_task_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _parse_args(
        monkeypatch,
        "--model",
        "openai/test-model",
        "--include-task-name",
        "fix-git",
        "--include-task-name",
        "fix-git",
    )

    with pytest.raises(ValueError, match="must be unique"):
        run_evaluation.build_command(args)


def test_prefixbench_profile_attests_source_and_owns_reserved_kwargs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    producer = ProducerAttestation(
        commit="a" * 40,
        tree="b" * 40,
        source_sha256="c" * 64,
    )
    monkeypatch.setattr(run_evaluation.shutil, "which", lambda _: "/usr/local/bin/harbor")
    monkeypatch.setattr(run_evaluation, "attest_git_runtime_source", lambda _: producer)
    args = _parse_args(
        monkeypatch,
        "--model",
        "openai/test-model",
        "--include-task-name",
        "fix-git",
        "--collection-profile",
        "prefixbench-v1",
        "--agent-kwarg",
        "api_base=https://example.test/v1",
    )

    command = run_evaluation.build_command(args)
    kwargs = [command[index + 1] for index, value in enumerate(command) if value == "--agent-kwarg"]

    assert "max_repairs=4" in kwargs
    assert "enable_completion_review=true" in kwargs
    assert "prefixbench_profile=prefixbench-v1" in kwargs
    assert f"producer_commit={producer.commit}" in kwargs
    assert kwargs[-1] == "api_base=https://example.test/v1"

    args.agent_kwarg = ["max_turns=41"]
    with pytest.raises(ValueError, match="cannot be overridden: max_turns"):
        run_evaluation.build_command(args)


def test_standard_run_rejects_spoofed_collection_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = _parse_args(
        monkeypatch,
        "--model",
        "openai/test-model",
        "--agent-kwarg",
        f"producer_commit={'a' * 40}",
    )

    with pytest.raises(ValueError, match="provenance options are reserved"):
        run_evaluation.build_command(args)


def test_load_matrix_accepts_nonempty_custom_task_count(tmp_path: Path) -> None:
    matrix_path = tmp_path / "matrix.json"
    matrix_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dataset": "terminal-bench@2.0",
                "tasks": [{"name": f"task-{index}"} for index in range(20)],
            }
        ),
        encoding="utf-8",
    )

    dataset, tasks = run_evaluation.load_matrix(matrix_path)

    assert dataset == "terminal-bench@2.0"
    assert len(tasks) == 20


def test_load_matrix_rejects_empty_task_list(tmp_path: Path) -> None:
    matrix_path = tmp_path / "matrix.json"
    matrix_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dataset": "terminal-bench@2.0",
                "tasks": [],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="at least one task"):
        run_evaluation.load_matrix(matrix_path)
