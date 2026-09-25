from __future__ import annotations

import argparse
import json
import tomllib
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = "terminal-bench@2.0"
DEFAULT_OUTPUT = PROJECT_ROOT / "evaluation" / "matrix-89.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a full evaluation matrix from an official Harbor registry snapshot."
    )
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--tasks-dir", type=Path, required=True)
    parser.add_argument("--dataset", default=DEFAULT_DATASET)
    parser.add_argument("--expected-count", type=int, default=89)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    return parser.parse_args()


def build_matrix(
    registry_path: Path,
    tasks_dir: Path,
    dataset_ref: str,
    expected_count: int,
) -> dict[str, Any]:
    dataset_name, dataset_version = _split_dataset_ref(dataset_ref)
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    if not isinstance(registry, list):
        raise ValueError("registry root must be an array")

    matches = [
        item
        for item in registry
        if isinstance(item, dict)
        and item.get("name") == dataset_name
        and item.get("version") == dataset_version
    ]
    if len(matches) != 1:
        raise ValueError(f"expected one registry entry for {dataset_ref}, found {len(matches)}")

    registry_tasks = matches[0].get("tasks")
    if not isinstance(registry_tasks, list):
        raise ValueError(f"registry entry for {dataset_ref} has no task list")
    if len(registry_tasks) != expected_count:
        raise ValueError(
            f"{dataset_ref} contains {len(registry_tasks)} tasks, expected {expected_count}"
        )

    task_entries = [_validate_registry_task(item) for item in registry_tasks]
    names = [item["name"] for item in task_entries]
    if len(names) != len(set(names)):
        raise ValueError(f"{dataset_ref} contains duplicate task names")

    discovered_names = {
        path.parent.name for path in tasks_dir.glob("*/task.toml") if path.is_file()
    }
    expected_names = set(names)
    if discovered_names != expected_names:
        missing = sorted(expected_names - discovered_names)
        extra = sorted(discovered_names - expected_names)
        raise ValueError(f"task directory mismatch; missing={missing}, extra={extra}")

    commits = {item["git_commit_id"] for item in task_entries}
    if len(commits) != 1:
        raise ValueError(f"{dataset_ref} must resolve to one source commit")
    source_commit = commits.pop()

    tasks = [_load_public_metadata(tasks_dir, name) for name in names]
    return {
        "schema_version": 1,
        "dataset": dataset_ref,
        "selection_method": (
            f"All {len(tasks)} tasks from the official {dataset_ref} registry entry at "
            f"source commit {source_commit}; metadata copied from public task.toml files."
        ),
        "tasks": tasks,
    }


def _split_dataset_ref(dataset_ref: str) -> tuple[str, str]:
    if dataset_ref.count("@") != 1:
        raise ValueError("dataset must use the name@version form")
    name, version = dataset_ref.split("@", 1)
    if not name or not version:
        raise ValueError("dataset must use the name@version form")
    return name, version


def _validate_registry_task(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("every registry task must be an object")
    required = ("name", "path", "git_url", "git_commit_id")
    result: dict[str, str] = {}
    for key in required:
        field = value.get(key)
        if not isinstance(field, str) or not field:
            raise ValueError(f"registry task must have a non-empty {key}")
        result[key] = field
    if result["path"] != result["name"]:
        raise ValueError(
            f"registry task path differs from its name: {result['name']} != {result['path']}"
        )
    return result


def _load_public_metadata(tasks_dir: Path, name: str) -> dict[str, Any]:
    task_path = tasks_dir / name / "task.toml"
    data = tomllib.loads(task_path.read_text(encoding="utf-8"))
    metadata = data.get("metadata")
    if not isinstance(metadata, dict):
        raise ValueError(f"{name} has no metadata table")

    difficulty = metadata.get("difficulty")
    category = metadata.get("category")
    tags = metadata.get("tags")
    if not isinstance(difficulty, str) or not difficulty:
        raise ValueError(f"{name} has no difficulty")
    if not isinstance(category, str) or not category:
        raise ValueError(f"{name} has no category")
    if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
        raise ValueError(f"{name} has invalid tags")
    return {
        "name": name,
        "difficulty": difficulty,
        "category": category,
        "tags": tags,
    }


def main() -> int:
    args = parse_args()
    try:
        matrix = build_matrix(
            registry_path=args.registry,
            tasks_dir=args.tasks_dir,
            dataset_ref=args.dataset,
            expected_count=args.expected_count,
        )
    except (OSError, ValueError, json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
        print(f"matrix generation failed: {exc}")
        return 2

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(matrix, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(matrix['tasks'])} tasks to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
