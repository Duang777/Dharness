from __future__ import annotations

import json
from pathlib import Path

from scripts.verify_delivery import (
    PROJECT_ROOT,
    _validate_snapshot_layout,
    validate_delivery,
)


def test_repository_delivery_is_complete() -> None:
    assert validate_delivery(PROJECT_ROOT) == []


def test_snapshot_layout_rejects_nested_and_mismatched_results(tmp_path: Path) -> None:
    trials_dir = Path("evaluation/trials")
    valid_path = tmp_path / trials_dir / "expected" / "result.json"
    valid_path.parent.mkdir(parents=True)
    valid_path.write_text(json.dumps({"task_name": "wrong"}), encoding="utf-8")
    nested_path = tmp_path / trials_dir / "stale" / "deep" / "result.json"
    nested_path.parent.mkdir(parents=True)
    nested_path.write_text("{}", encoding="utf-8")

    errors = _validate_snapshot_layout(tmp_path, trials_dir, {"expected"})

    assert any("invalid trial path" in error for error in errors)
    assert any("snapshot task does not match path" in error for error in errors)
