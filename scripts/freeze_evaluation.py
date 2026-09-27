from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evidence_harness.evaluation import EvaluationMatrix, load_matrix

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
AGENT_OPTION_FIELDS = (
    "max_turns",
    "max_environment_calls",
    "max_wall_time_sec",
    "verification_environment_reserve",
    "api_base",
    "max_output_tokens",
    "max_model_call_timeout_sec",
    "max_completion_reviews",
    "continue_on_recorded_failure",
    "legacy_replay_all_decisions",
)


@dataclass(frozen=True)
class SourceResult:
    path: Path
    data: dict[str, Any]
    result_bytes: bytes
    config_bytes: bytes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Freeze sanitized, reproducible Harbor trial evidence."
    )
    parser.add_argument("results_dir", type=Path, nargs="*")
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--manifest",
        type=Path,
        help="Canonical result manifest produced by collect_evaluation_results.py.",
    )
    return parser.parse_args()


def freeze_results(results_dir: Path, matrix_path: Path, output_dir: Path) -> int:
    return freeze_result_sets((results_dir,), matrix_path, output_dir)


def freeze_result_sets(results_dirs: Iterable[Path], matrix_path: Path, output_dir: Path) -> int:
    matrix = load_matrix(matrix_path)
    sources = _load_sources(results_dirs)
    return _freeze_sources(matrix, sources, output_dir)


def freeze_manifest(manifest_path: Path, matrix_path: Path, output_dir: Path) -> int:
    matrix = load_matrix(matrix_path)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read canonical manifest: {manifest_path}") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise ValueError(f"unsupported canonical manifest: {manifest_path}")
    if manifest.get("dataset") != matrix.dataset:
        raise ValueError(f"manifest dataset does not match matrix: {manifest_path}")
    expected_matrix_sha256 = hashlib.sha256(matrix_path.read_bytes()).hexdigest()
    if manifest.get("matrix_sha256") != expected_matrix_sha256:
        raise ValueError(f"manifest matrix hash does not match: {manifest_path}")

    rows = manifest.get("tasks")
    if not isinstance(rows, list):
        raise ValueError(f"manifest tasks must be a list: {manifest_path}")
    sources: dict[str, SourceResult] = {}
    for index, row in enumerate(rows, start=1):
        if not isinstance(row, dict):
            raise ValueError(f"manifest task {index} must be an object: {manifest_path}")
        task_name = row.get("name")
        result_path_raw = row.get("result_path")
        result_sha256 = row.get("result_sha256")
        if not isinstance(task_name, str) or not task_name:
            raise ValueError(f"manifest task {index} has no name: {manifest_path}")
        if not isinstance(result_path_raw, str) or not result_path_raw:
            raise ValueError(f"manifest task {task_name} has no result path: {manifest_path}")
        if not isinstance(result_sha256, str) or len(result_sha256) != 64:
            raise ValueError(f"manifest task {task_name} has no valid result hash: {manifest_path}")
        if task_name in sources:
            raise ValueError(f"duplicate manifest task: {task_name}")
        result_path = Path(result_path_raw)
        if not result_path.is_absolute():
            result_path = PROJECT_ROOT / result_path
        source = _load_source_result(
            result_path,
            expected_result_sha256=result_sha256,
        )
        if _task_name(source.data) != task_name:
            raise ValueError(f"manifest task does not match result: {task_name}")
        task_id = _mapping(source.data.get("task_id"))
        task_config = _mapping(_mapping(source.data.get("config")).get("task"))
        expected_provenance = {
            "config_sha256": hashlib.sha256(source.config_bytes).hexdigest(),
            "task_checksum": source.data.get("task_checksum"),
            "task_git_url": task_id.get("git_url") or task_config.get("git_url"),
            "task_git_commit_id": (
                task_id.get("git_commit_id") or task_config.get("git_commit_id")
            ),
        }
        for field, expected_value in expected_provenance.items():
            if row.get(field) != expected_value:
                raise ValueError(f"manifest task {field} does not match result: {task_name}")
        sources[task_name] = source

    return _freeze_sources(matrix, sources, output_dir)


def _freeze_sources(
    matrix: EvaluationMatrix,
    sources: dict[str, SourceResult],
    output_dir: Path,
) -> int:
    expected = {task.name for task in matrix.tasks}
    missing = sorted(expected - sources.keys())
    unexpected = sorted(sources.keys() - expected)
    if missing or unexpected:
        details = []
        if missing:
            details.append("missing: " + ", ".join(missing))
        if unexpected:
            details.append("unexpected: " + ", ".join(unexpected))
        raise ValueError("trial set does not match matrix (" + "; ".join(details) + ")")

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f".{output_dir.name}-",
        dir=output_dir.parent,
    ) as temporary:
        staged_dir = Path(temporary) / "next"
        for task in matrix.tasks:
            source = sources[task.name]
            destination = staged_dir / task.name / "result.json"
            destination.parent.mkdir(parents=True, exist_ok=True)
            snapshot = sanitize_result(
                source.data,
                source.result_bytes,
                source.config_bytes,
            )
            destination.write_text(
                json.dumps(snapshot, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        _replace_directory(staged_dir, output_dir)
    return len(matrix.tasks)


def sanitize_result(
    source: dict[str, Any],
    source_bytes: bytes,
    config_bytes: bytes,
) -> dict[str, Any]:
    config = _mapping(source.get("config"))
    task_config = _mapping(config.get("task"))
    agent_config = _mapping(config.get("agent"))
    agent_options = _mapping(agent_config.get("kwargs"))
    task_id = _mapping(source.get("task_id"))
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
            field: replay[field]
            for field in (
                "source_sha256",
                "command_count",
                "recorded_failure_count",
                "replay_mode",
                "completed",
            )
            if field in replay
        }
        if "replay_mode" not in sanitized_metadata["evidence_harness_replay"]:
            sanitized_metadata["evidence_harness_replay"]["replay_mode"] = (
                "recorded_receipts_v1"
                if agent_options.get("continue_on_recorded_failure") is True
                else "legacy_all_decisions_v1"
            )

    verifier_result = _mapping(source.get("verifier_result"))
    rewards = _mapping(verifier_result.get("rewards"))
    reward = rewards.get("reward")

    return {
        "snapshot": {
            "schema_version": 2,
            "source_result_sha256": hashlib.sha256(source_bytes).hexdigest(),
            "source_config_sha256": hashlib.sha256(config_bytes).hexdigest(),
        },
        "trial_name": source.get("trial_name"),
        "task_name": task_name,
        "task": {
            "source": source.get("source") or task_config.get("source"),
            "checksum": source.get("task_checksum"),
            "git_url": task_id.get("git_url") or task_config.get("git_url"),
            "git_commit_id": (task_id.get("git_commit_id") or task_config.get("git_commit_id")),
        },
        "config": {
            "task": {"name": task_name},
            "agent": {
                "name": agent_config.get("name"),
                "model_name": agent_config.get("model_name"),
                "options": {
                    field: agent_options[field]
                    for field in AGENT_OPTION_FIELDS
                    if field in agent_options
                },
            },
            "environment": {
                "type": _mapping(config.get("environment")).get("type"),
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


def _load_sources(
    results_dirs: Iterable[Path],
) -> dict[str, SourceResult]:
    sources: dict[str, SourceResult] = {}
    for results_dir in results_dirs:
        if not results_dir.is_dir():
            raise ValueError(f"results directory does not exist: {results_dir}")
        for result_path in sorted(results_dir.rglob("result.json")):
            source = _load_source_result(result_path)
            if "trial_name" not in source.data:
                continue
            task_name = _task_name(source.data)
            if task_name is None:
                raise ValueError(f"trial result has no task name: {result_path}")
            previous = sources.get(task_name)
            if previous is not None:
                raise ValueError(
                    f"multiple trial results for task '{task_name}': "
                    f"{previous.path} and {result_path}"
                )
            sources[task_name] = source
    return sources


def _load_source_result(
    result_path: Path,
    *,
    expected_result_sha256: str | None = None,
) -> SourceResult:
    config_path = result_path.parent / "config.json"
    try:
        result_bytes = result_path.read_bytes()
        if (
            expected_result_sha256 is not None
            and hashlib.sha256(result_bytes).hexdigest() != expected_result_sha256
        ):
            raise ValueError(f"manifest task result hash does not match: {result_path}")
        source = json.loads(result_bytes)
        config_bytes = config_path.read_bytes()
        config = json.loads(config_bytes)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read trial result and config: {result_path}") from exc
    if not isinstance(source, dict):
        raise ValueError(f"trial result must be an object: {result_path}")
    if not isinstance(config, dict):
        raise ValueError(f"trial config must be an object: {config_path}")
    result_config = _mapping(source.get("config"))
    for section, field in (
        ("agent", "name"),
        ("task", "path"),
        ("task", "git_url"),
        ("task", "git_commit_id"),
    ):
        if _mapping(result_config.get(section)).get(field) != _mapping(config.get(section)).get(
            field
        ):
            raise ValueError(f"trial result config does not match: {config_path}")
    for field in ("job_id", "trial_name"):
        if result_config.get(field) != config.get(field):
            raise ValueError(f"trial result config does not match: {config_path}")
    return SourceResult(
        path=result_path,
        data=source,
        result_bytes=result_bytes,
        config_bytes=config_bytes,
    )


def _replace_directory(staged_dir: Path, output_dir: Path) -> None:
    if output_dir.exists() and not output_dir.is_dir():
        raise ValueError(f"output path is not a directory: {output_dir}")

    backup_dir = staged_dir.parent / "previous"
    if output_dir.exists():
        output_dir.replace(backup_dir)
    try:
        staged_dir.replace(output_dir)
    except OSError:
        if backup_dir.exists():
            backup_dir.replace(output_dir)
        raise


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
        if args.manifest is not None:
            if args.results_dir:
                raise ValueError("pass either result directories or --manifest, not both")
            count = freeze_manifest(args.manifest, args.matrix, args.output_dir)
        else:
            if not args.results_dir:
                raise ValueError("pass at least one result directory or --manifest")
            count = freeze_result_sets(args.results_dir, args.matrix, args.output_dir)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"freeze failed: {exc}", file=sys.stderr)
        return 2
    print(f"froze {count} sanitized trial results in {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
