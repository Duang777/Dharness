from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.build_full_matrix import build_matrix


def _write_task(tasks_dir: Path, name: str, difficulty: str = "medium") -> None:
    task_dir = tasks_dir / name
    task_dir.mkdir(parents=True)
    (task_dir / "task.toml").write_text(
        "\n".join(
            (
                'version = "1.0"',
                "",
                "[metadata]",
                f'difficulty = "{difficulty}"',
                'category = "software-engineering"',
                'tags = ["coding"]',
                "",
            )
        ),
        encoding="utf-8",
    )


def _write_registry(path: Path, names: list[str]) -> None:
    tasks = [
        {
            "name": name,
            "path": name,
            "git_url": "https://example.test/terminal-bench-2.git",
            "git_commit_id": "abc123",
        }
        for name in names
    ]
    path.write_text(
        json.dumps(
            [
                {
                    "name": "terminal-bench",
                    "version": "2.0",
                    "description": "fixture",
                    "tasks": tasks,
                }
            ]
        ),
        encoding="utf-8",
    )


def test_build_matrix_preserves_registry_order_and_reads_public_metadata(
    tmp_path: Path,
) -> None:
    registry_path = tmp_path / "registry.json"
    tasks_dir = tmp_path / "tasks"
    _write_registry(registry_path, ["task-b", "task-a"])
    _write_task(tasks_dir, "task-a", difficulty="easy")
    _write_task(tasks_dir, "task-b", difficulty="hard")

    matrix = build_matrix(
        registry_path=registry_path,
        tasks_dir=tasks_dir,
        dataset_ref="terminal-bench@2.0",
        expected_count=2,
    )

    assert matrix["schema_version"] == 1
    assert matrix["dataset"] == "terminal-bench@2.0"
    assert [task["name"] for task in matrix["tasks"]] == ["task-b", "task-a"]
    assert [task["difficulty"] for task in matrix["tasks"]] == ["hard", "easy"]
    assert "abc123" in matrix["selection_method"]


def test_build_matrix_rejects_task_directory_drift(tmp_path: Path) -> None:
    registry_path = tmp_path / "registry.json"
    tasks_dir = tmp_path / "tasks"
    _write_registry(registry_path, ["task-a", "task-b"])
    _write_task(tasks_dir, "task-a")
    _write_task(tasks_dir, "unexpected-task")

    with pytest.raises(
        ValueError,
        match=r"missing=\['task-b'\], extra=\['unexpected-task'\]",
    ):
        build_matrix(
            registry_path=registry_path,
            tasks_dir=tasks_dir,
            dataset_ref="terminal-bench@2.0",
            expected_count=2,
        )
