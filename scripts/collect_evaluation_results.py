from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from evidence_harness.evaluation import EvaluationMatrix, load_matrix

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MATRIX = PROJECT_ROOT / "evaluation" / "matrix-89.json"
Status = Literal["passed", "failed", "error"]


@dataclass(frozen=True)
class CompletedResult:
    task_name: str
    status: Status
    reward: float | None
    exception_type: str | None
    completed_at: datetime
    run_dir: Path
    result_path: Path
    config_sha256: str | None
    task_checksum: str | None
    task_git_url: str | None
    task_git_commit_id: str | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Select the latest completed result for every evaluation task."
    )
    parser.add_argument("run_dir", type=Path, nargs="+")
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--manifest-out", type=Path, required=True)
    parser.add_argument("--retry-matrix-out", type=Path)
    parser.add_argument(
        "--retry-status",
        action="append",
        choices=("failed", "error"),
        default=[],
        help="Include this canonical status in the retry matrix; repeat as needed.",
    )
    return parser.parse_args()


def collect_latest_results(
    run_dirs: list[Path],
    matrix: EvaluationMatrix,
) -> dict[str, CompletedResult]:
    expected = {task.name for task in matrix.tasks}
    latest: dict[str, CompletedResult] = {}

    for run_dir in run_dirs:
        progress_path = run_dir / "progress.jsonl"
        recorded_result_paths: set[Path] = set()
        if not progress_path.is_file():
            completed_results = _load_direct_job_results(run_dir)
        else:
            for line_number, line in enumerate(
                progress_path.read_text(encoding="utf-8").splitlines(),
                start=1,
            ):
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"invalid progress event: {progress_path}:{line_number}"
                    ) from exc
                if not isinstance(event, dict) or event.get("event") != "task_completed":
                    continue
                completed = _completed_result(run_dir, progress_path, line_number, event)
                _record_latest(latest, expected, completed)
                recorded_result_paths.add(completed.result_path.resolve())
            completed_results = _load_direct_job_results(
                run_dir,
                excluded_paths=recorded_result_paths,
            )
        if not progress_path.is_file() and not completed_results:
            raise ValueError(f"run has no progress or trial results: {run_dir}")
        for completed in completed_results:
            _record_latest(latest, expected, completed)

    missing = sorted(expected - latest.keys())
    if missing:
        raise ValueError("matrix tasks have no completed result: " + ", ".join(missing))
    return latest


def _load_direct_job_results(
    run_dir: Path,
    *,
    excluded_paths: set[Path] | None = None,
) -> list[CompletedResult]:
    excluded = excluded_paths or set()
    completed_results: list[CompletedResult] = []
    for result_path in sorted(run_dir.rglob("result.json")):
        if result_path.resolve() in excluded:
            continue
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"cannot read trial result: {result_path}") from exc
        if not isinstance(result, dict) or not result.get("trial_name"):
            continue
        if _exception_type(result) in {"CancelledError", "KeyboardInterrupt"}:
            continue
        completed_at_raw = result.get("finished_at")
        if not isinstance(completed_at_raw, str) or not completed_at_raw:
            raise ValueError(f"trial result has no completion timestamp: {result_path}")
        try:
            completed_at = datetime.fromisoformat(completed_at_raw.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"trial result has invalid timestamp: {result_path}") from exc
        task_name = _task_name(result)
        if task_name is None:
            raise ValueError(f"trial result has no task name: {result_path}")
        completed_results.append(
            _completed_result_from_data(
                run_dir=run_dir,
                result_path=result_path,
                task_name=task_name,
                completed_at=completed_at,
                result=result,
            )
        )
    return completed_results


def _record_latest(
    latest: dict[str, CompletedResult],
    expected: set[str],
    completed: CompletedResult,
) -> None:
    if completed.task_name not in expected:
        raise ValueError(f"completed task is not in the matrix: {completed.task_name}")
    previous = latest.get(completed.task_name)
    if previous is None or _completion_key(completed) > _completion_key(previous):
        latest[completed.task_name] = completed


def build_manifest(
    matrix: EvaluationMatrix,
    matrix_path: Path,
    latest: dict[str, CompletedResult],
) -> dict[str, Any]:
    task_rows = [
        {
            "index": index,
            "name": task.name,
            "status": latest[task.name].status,
            "reward": latest[task.name].reward,
            "exception_type": latest[task.name].exception_type,
            "completed_at": latest[task.name].completed_at.isoformat(),
            "run_dir": _display_path(latest[task.name].run_dir),
            "result_path": _display_path(latest[task.name].result_path),
            "result_sha256": hashlib.sha256(latest[task.name].result_path.read_bytes()).hexdigest(),
            "config_sha256": latest[task.name].config_sha256,
            "task_checksum": latest[task.name].task_checksum,
            "task_git_url": latest[task.name].task_git_url,
            "task_git_commit_id": latest[task.name].task_git_commit_id,
        }
        for index, task in enumerate(matrix.tasks, start=1)
    ]
    counts = {
        status: sum(row["status"] == status for row in task_rows)
        for status in ("passed", "failed", "error")
    }
    return {
        "schema_version": 1,
        "dataset": matrix.dataset,
        "matrix_sha256": hashlib.sha256(matrix_path.read_bytes()).hexdigest(),
        "counts": {"completed": len(task_rows), **counts},
        "tasks": task_rows,
    }


def build_retry_matrix(
    matrix: EvaluationMatrix,
    latest: dict[str, CompletedResult],
    statuses: set[Status],
) -> dict[str, Any]:
    tasks = [
        task.model_dump(mode="json")
        for task in matrix.tasks
        if latest[task.name].status in statuses
    ]
    labels = ", ".join(sorted(statuses))
    return {
        "schema_version": 1,
        "dataset": matrix.dataset,
        "selection_method": f"Canonical tasks with status in: {labels}.",
        "tasks": tasks,
    }


def _completed_result(
    run_dir: Path,
    progress_path: Path,
    line_number: int,
    event: dict[str, Any],
) -> CompletedResult:
    task_name = event.get("task")
    completed_at_raw = event.get("ts")
    result_path_raw = event.get("result_path")
    if not isinstance(task_name, str) or not task_name:
        raise ValueError(f"task_completed event has no task: {progress_path}:{line_number}")
    if not isinstance(completed_at_raw, str) or not completed_at_raw:
        raise ValueError(f"task_completed event has no timestamp: {progress_path}:{line_number}")
    if not isinstance(result_path_raw, str) or not result_path_raw:
        raise ValueError(f"task_completed event has no result path: {progress_path}:{line_number}")
    try:
        completed_at = datetime.fromisoformat(completed_at_raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            f"task_completed event has invalid timestamp: {progress_path}:{line_number}"
        ) from exc

    result_path = run_dir / result_path_raw
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read completed result: {result_path}") from exc
    if not isinstance(result, dict):
        raise ValueError(f"completed result is not an object: {result_path}")
    result_task_name = _task_name(result)
    if result_task_name != task_name:
        raise ValueError(
            f"task mismatch for {result_path}: event={task_name}, result={result_task_name}"
        )

    return _completed_result_from_data(
        run_dir=run_dir,
        result_path=result_path,
        task_name=task_name,
        completed_at=completed_at,
        result=result,
    )


def _completed_result_from_data(
    *,
    run_dir: Path,
    result_path: Path,
    task_name: str,
    completed_at: datetime,
    result: dict[str, Any],
) -> CompletedResult:
    reward = _reward(result)
    exception_type = _exception_type(result)
    status: Status
    if exception_type is not None or reward is None:
        status = "error"
    elif reward == 1.0:
        status = "passed"
    else:
        status = "failed"
    config_path = result_path.parent / "config.json"
    config_sha256 = (
        hashlib.sha256(config_path.read_bytes()).hexdigest() if config_path.is_file() else None
    )
    task_id = _mapping(result.get("task_id"))
    task_config = _mapping(_mapping(result.get("config")).get("task"))
    return CompletedResult(
        task_name=task_name,
        status=status,
        reward=reward,
        exception_type=exception_type,
        completed_at=completed_at,
        run_dir=run_dir,
        result_path=result_path,
        config_sha256=config_sha256,
        task_checksum=_string_or_none(result.get("task_checksum")),
        task_git_url=_string_or_none(task_id.get("git_url") or task_config.get("git_url")),
        task_git_commit_id=_string_or_none(
            task_id.get("git_commit_id") or task_config.get("git_commit_id")
        ),
    )


def _completion_key(result: CompletedResult) -> tuple[datetime, str]:
    return result.completed_at, str(result.result_path)


def _task_name(result: dict[str, Any]) -> str | None:
    config = _mapping(result.get("config"))
    config_task = _mapping(config.get("task"))
    for value in (
        config_task.get("name"),
        _mapping(result.get("task_id")).get("name"),
        result.get("task_name"),
    ):
        if isinstance(value, str) and value:
            return value.rsplit("/", 1)[-1]
    path = config_task.get("path")
    return Path(path).name if isinstance(path, str) and path else None


def _reward(result: dict[str, Any]) -> float | None:
    verifier_result = _mapping(result.get("verifier_result"))
    value = _mapping(verifier_result.get("rewards")).get("reward")
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _exception_type(result: dict[str, Any]) -> str | None:
    exception = _mapping(result.get("exception_info"))
    value = exception.get("exception_type") or exception.get("type")
    return value if isinstance(value, str) and value else None


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path.resolve())


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    args = parse_args()
    try:
        matrix = load_matrix(args.matrix)
        latest = collect_latest_results(args.run_dir, matrix)
        manifest = build_manifest(matrix, args.matrix, latest)
        _write_json(args.manifest_out, manifest)
        if args.retry_matrix_out is not None:
            statuses = set(args.retry_status or ["error"])
            retry_matrix = build_retry_matrix(matrix, latest, statuses)
            _write_json(args.retry_matrix_out, retry_matrix)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"collection failed: {exc}", file=sys.stderr)
        return 2

    counts = manifest["counts"]
    print(
        f"collected {counts['completed']} tasks: "
        f"{counts['passed']} passed, {counts['failed']} failed, {counts['error']} errors"
    )
    if args.retry_matrix_out is not None:
        print(f"wrote {len(retry_matrix['tasks'])} retry tasks to {args.retry_matrix_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
