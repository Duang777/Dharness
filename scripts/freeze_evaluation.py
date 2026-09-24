from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from evidence_harness.evaluation import load_matrix

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MATRIX = PROJECT_ROOT / "evaluation" / "matrix.json"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "evaluation" / "trials"

HARNESS_METADATA_FIELDS = (
    "phase",
    "turns_used",
    "environment_calls_used",
    "repairs_used",
    "recoveries_used",
    "work_epoch",
    "stop_reason",
    "failure_category",
    "latest_evidence_accepted",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Freeze sanitized, reproducible Harbor trial evidence."
    )
    parser.add_argument("results_dir", type=Path)
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def freeze_results(results_dir: Path, matrix_path: Path, output_dir: Path) -> int:
    matrix = load_matrix(matrix_path)
    expected = {task.name for task in matrix.tasks}
    sources = _load_sources(results_dir)
    missing = sorted(expected - sources.keys())
    unexpected = sorted(sources.keys() - expected)
    if missing or unexpected:
        details = []
        if missing:
            details.append("missing: " + ", ".join(missing))
        if unexpected:
            details.append("unexpected: " + ", ".join(unexpected))
        raise ValueError("trial set does not match matrix (" + "; ".join(details) + ")")

    for task in matrix.tasks:
        source_path, source = sources[task.name]
        destination = output_dir / task.name / "result.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        snapshot = sanitize_result(source, source_path.read_bytes())
        destination.write_text(
            json.dumps(snapshot, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return len(matrix.tasks)


def sanitize_result(source: dict[str, Any], source_bytes: bytes) -> dict[str, Any]:
    config = _mapping(source.get("config"))
    agent_config = _mapping(config.get("agent"))
    agent_result = _mapping(source.get("agent_result"))
    metadata = _mapping(agent_result.get("metadata"))
    harness = _mapping(metadata.get("evidence_harness"))
    replay = _mapping(metadata.get("evidence_harness_replay"))
    exception = _mapping(source.get("exception_info"))
    task_name = _task_name(source)
    if task_name is None:
        raise ValueError("trial result has no task name")

    sanitized_metadata: dict[str, Any] = {
        "evidence_harness": {
            field: harness[field] for field in HARNESS_METADATA_FIELDS if field in harness
        }
    }
    if replay:
        sanitized_metadata["evidence_harness_replay"] = {
            field: replay[field] for field in ("source_sha256", "completed") if field in replay
        }

    verifier_result = _mapping(source.get("verifier_result"))
    rewards = _mapping(verifier_result.get("rewards"))
    reward = rewards.get("reward")

    return {
        "snapshot": {
            "schema_version": 1,
            "source_result_sha256": hashlib.sha256(source_bytes).hexdigest(),
        },
        "trial_name": source.get("trial_name"),
        "task_name": task_name,
        "config": {
            "task": {"name": task_name},
            "agent": {
                "name": agent_config.get("name"),
                "model_name": agent_config.get("model_name"),
            },
        },
        "agent_result": {
            "n_input_tokens": agent_result.get("n_input_tokens"),
            "n_cache_tokens": agent_result.get("n_cache_tokens"),
            "n_output_tokens": agent_result.get("n_output_tokens"),
            "cost_usd": agent_result.get("cost_usd"),
            "metadata": sanitized_metadata,
        },
        "verifier_result": ({"rewards": {"reward": reward}} if reward is not None else None),
        "exception_info": (
            {"exception_type": exception.get("exception_type") or exception.get("type")}
            if exception
            else None
        ),
        "started_at": source.get("started_at"),
        "finished_at": source.get("finished_at"),
    }


def _load_sources(results_dir: Path) -> dict[str, tuple[Path, dict[str, Any]]]:
    if not results_dir.is_dir():
        raise ValueError(f"results directory does not exist: {results_dir}")

    sources: dict[str, tuple[Path, dict[str, Any]]] = {}
    for result_path in sorted(results_dir.rglob("result.json")):
        try:
            source = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read trial result: {result_path}") from exc
        if not isinstance(source, dict) or "trial_name" not in source:
            continue
        task_name = _task_name(source)
        if task_name is None:
            raise ValueError(f"trial result has no task name: {result_path}")
        previous = sources.get(task_name)
        if previous is not None:
            raise ValueError(
                f"multiple trial results for task '{task_name}': {previous[0]} and {result_path}"
            )
        sources[task_name] = (result_path, source)
    return sources


def _task_name(source: dict[str, Any]) -> str | None:
    config_task = _mapping(_mapping(source.get("config")).get("task"))
    for value in (
        config_task.get("name"),
        _mapping(source.get("task_id")).get("name"),
        source.get("task_name"),
    ):
        if isinstance(value, str) and value:
            return value.rsplit("/", 1)[-1]
    path = config_task.get("path")
    return Path(path).name if isinstance(path, str) and path else None


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def main() -> int:
    args = parse_args()
    try:
        count = freeze_results(args.results_dir, args.matrix, args.output_dir)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"freeze failed: {exc}", file=sys.stderr)
        return 2
    print(f"froze {count} sanitized trial results in {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
