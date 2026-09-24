from __future__ import annotations

import json
import sys

import pytest

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
    assert run_evaluation.DEBIAN_HTTPS_SOURCES.read_text(encoding="utf-8").count(
        "URIs: https://deb.debian.org"
    ) == 2


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
