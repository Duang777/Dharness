from __future__ import annotations

import json
import sys
from pathlib import Path

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
