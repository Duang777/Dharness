from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import prefixbench

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_matrix_command_writes_the_frozen_development_split(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "development.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prefixbench.py",
            "matrix",
            "--split",
            "development",
            "--out",
            str(output),
        ],
    )

    assert prefixbench.main() == 0

    matrix = json.loads(output.read_bytes())
    assert output.read_bytes().endswith(b"\n")
    assert matrix["schema_version"] == 1
    assert matrix["dataset"] == "terminal-bench@2.0"
    assert len(matrix["tasks"]) == 28
    assert matrix["tasks"][0]["name"] == "adaptive-rejection-sampler"
    assert matrix["tasks"][-1]["name"] == "video-processing"
    assert prefixbench.DEFAULT_MATRIX == PROJECT_ROOT / "evaluation" / "matrix-89.json"


def test_build_command_passes_expected_task_count(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "readiness.json"
    captured: dict[str, int] = {}

    def inspect(*_args: object, expected_task_count: int) -> SimpleNamespace:
        captured["expected_task_count"] = expected_task_count
        return SimpleNamespace(
            canonical_bytes=lambda: b"{}\n",
            status=SimpleNamespace(value="ready"),
            summary=SimpleNamespace(
                tasks=28,
                source_admitted=28,
                development=28,
                test=0,
            ),
        )

    monkeypatch.setattr(prefixbench, "inspect_prefixbench", inspect)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prefixbench.py",
            "build",
            "--expected-task-count",
            "28",
            "--out",
            str(output),
        ],
    )

    assert prefixbench.main() == 0
    assert captured == {"expected_task_count": 28}
    assert output.read_bytes() == b"{}\n"


def test_check_command_passes_expected_task_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, int] = {}

    def check(**kwargs: object) -> tuple[str, ...]:
        expected_task_count = kwargs["expected_task_count"]
        assert isinstance(expected_task_count, int)
        captured["expected_task_count"] = expected_task_count
        return ()

    monkeypatch.setattr(prefixbench, "check_prefixbench_readiness", check)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "prefixbench.py",
            "check",
            "--expected-task-count",
            "28",
        ],
    )

    assert prefixbench.main() == 0
    assert captured == {"expected_task_count": 28}
