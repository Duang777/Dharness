from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class MatrixTask(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    difficulty: str
    category: str
    tags: tuple[str, ...] = ()


class EvaluationMatrix(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int
    dataset: str
    selection_method: str
    tasks: tuple[MatrixTask, ...] = Field(min_length=1)


class TaskResult(BaseModel):
    name: str
    difficulty: str
    category: str
    status: str
    reward: float | None = None
    stop_reason: str | None = None
    failure_category: str | None = None
    turns_used: int | None = None
    environment_calls_used: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    duration_sec: float | None = None
    trial_path: str | None = None
    exception_type: str | None = None


class EvaluationSummary(BaseModel):
    schema_version: int = 2
    dataset: str
    source_dir: str
    complete: bool
    all_tasks_attempted: bool
    selected_tasks: int
    executed_tasks: int
    scored_tasks: int
    passed_tasks: int
    failed_tasks: int
    errored_tasks: int
    not_run_tasks: int
    pass_rate: float | None
    scored_pass_rate: float | None
    execution_coverage: float
    scored_coverage: float
    tasks: tuple[TaskResult, ...]


def load_matrix(path: Path) -> EvaluationMatrix:
    matrix = EvaluationMatrix.model_validate_json(path.read_text(encoding="utf-8"))
    if matrix.schema_version != 1:
        raise ValueError(f"unsupported matrix schema version: {matrix.schema_version}")
    names = [task.name for task in matrix.tasks]
    if len(names) != len(set(names)):
        raise ValueError("matrix task names must be unique")
    return matrix


def summarize_results(matrix: EvaluationMatrix, results_dir: Path) -> EvaluationSummary:
    trials = _load_trial_results(results_dir)
    task_results = tuple(
        _summarize_task(task, trials.get(task.name), results_dir) for task in matrix.tasks
    )
    passed = sum(item.status == "passed" for item in task_results)
    failed = sum(item.status == "failed" for item in task_results)
    errored = sum(item.status == "error" for item in task_results)
    not_run = sum(item.status == "not_run" for item in task_results)
    scored = passed + failed
    executed = scored + errored

    return EvaluationSummary(
        dataset=matrix.dataset,
        source_dir=str(results_dir),
        complete=scored == len(task_results),
        all_tasks_attempted=not_run == 0,
        selected_tasks=len(task_results),
        executed_tasks=executed,
        scored_tasks=scored,
        passed_tasks=passed,
        failed_tasks=failed,
        errored_tasks=errored,
        not_run_tasks=not_run,
        pass_rate=passed / executed if executed else None,
        scored_pass_rate=passed / scored if scored else None,
        execution_coverage=executed / len(task_results),
        scored_coverage=scored / len(task_results),
        tasks=task_results,
    )


def render_markdown(summary: EvaluationSummary) -> str:
    pass_rate = (
        f"{summary.pass_rate:.1%}" if summary.pass_rate is not None else "N/A"
    )
    scored_pass_rate = (
        f"{summary.scored_pass_rate:.1%}"
        if summary.scored_pass_rate is not None
        else "N/A"
    )
    lines = [
        "# Terminal-Bench 2.0 Evaluation",
        "",
        f"- Dataset: `{summary.dataset}`",
        f"- Scoring complete: `{'yes' if summary.complete else 'no'}`",
        f"- All tasks attempted: `{'yes' if summary.all_tasks_attempted else 'no'}`",
        f"- Executed: `{summary.executed_tasks}/{summary.selected_tasks}`",
        f"- Scored: `{summary.scored_tasks}/{summary.selected_tasks}`",
        f"- Passed / failed / errored: "
        f"`{summary.passed_tasks} / {summary.failed_tasks} / {summary.errored_tasks}`",
        f"- Pass rate over attempted tasks: `{pass_rate}`",
        f"- Pass rate over scored tasks: `{scored_pass_rate}`",
        f"- Execution coverage: `{summary.execution_coverage:.1%}`",
        f"- Scored coverage: `{summary.scored_coverage:.1%}`",
        "",
        "| Task | Difficulty | Category | Status | Reward | Stop reason |",
        "|---|---|---|---|---:|---|",
    ]
    for task in summary.tasks:
        reward = "N/A" if task.reward is None else f"{task.reward:g}"
        lines.append(
            "| "
            + " | ".join(
                (
                    _escape_cell(task.name),
                    _escape_cell(task.difficulty),
                    _escape_cell(task.category),
                    _escape_cell(task.status),
                    reward,
                    _escape_cell(task.stop_reason or ""),
                )
            )
            + " |"
        )

    if summary.errored_tasks or summary.not_run_tasks:
        lines.extend(
            (
                "",
                "> `error` tasks count in the attempted pass rate but not the scored pass "
                "rate. `not_run` tasks are excluded from both.",
            )
        )
    return "\n".join(lines) + "\n"


def _load_trial_results(results_dir: Path) -> dict[str, tuple[dict[str, Any], Path]]:
    trials: dict[str, tuple[dict[str, Any], Path]] = {}
    if not results_dir.exists():
        return trials

    for result_path in sorted(results_dir.rglob("result.json")):
        try:
            data = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or "trial_name" not in data:
            continue

        task_name = _task_name(data)
        if task_name is None:
            continue
        previous = trials.get(task_name)
        if previous is not None:
            raise ValueError(
                f"multiple trial results for task '{task_name}': "
                f"{previous[1]} and {result_path}"
            )
        trials[task_name] = (data, result_path)
    return trials


def _task_name(data: dict[str, Any]) -> str | None:
    config_task = _mapping(_mapping(data.get("config")).get("task"))
    for value in (
        config_task.get("name"),
        _mapping(data.get("task_id")).get("name"),
        data.get("task_name"),
    ):
        if isinstance(value, str) and value:
            return value.rsplit("/", 1)[-1]

    path = config_task.get("path")
    if isinstance(path, str) and path:
        return Path(path).name
    return None


def _summarize_task(
    task: MatrixTask,
    trial: tuple[dict[str, Any], Path] | None,
    results_dir: Path,
) -> TaskResult:
    if trial is None:
        return TaskResult(
            name=task.name,
            difficulty=task.difficulty,
            category=task.category,
            status="not_run",
        )

    data, result_path = trial
    verifier_result = _mapping(data.get("verifier_result"))
    rewards = _mapping(verifier_result.get("rewards"))
    reward = _number_or_none(rewards.get("reward"))
    exception = _mapping(data.get("exception_info"))
    metadata = _mapping(_mapping(data.get("agent_result")).get("metadata"))
    harness = _mapping(metadata.get("evidence_harness"))

    if exception or reward is None:
        status = "error"
    elif reward == 1.0:
        status = "passed"
    else:
        status = "failed"

    return TaskResult(
        name=task.name,
        difficulty=task.difficulty,
        category=task.category,
        status=status,
        reward=reward,
        stop_reason=_string_or_none(harness.get("stop_reason")),
        failure_category=_string_or_none(harness.get("failure_category")),
        turns_used=_int_or_none(harness.get("turns_used")),
        environment_calls_used=_int_or_none(harness.get("environment_calls_used")),
        input_tokens=_int_or_none(_mapping(data.get("agent_result")).get("n_input_tokens")),
        output_tokens=_int_or_none(_mapping(data.get("agent_result")).get("n_output_tokens")),
        cost_usd=_number_or_none(_mapping(data.get("agent_result")).get("cost_usd")),
        duration_sec=_duration_sec(data),
        trial_path=str(result_path.relative_to(results_dir)),
        exception_type=_string_or_none(exception.get("exception_type") or exception.get("type")),
    )


def _duration_sec(data: dict[str, Any]) -> float | None:
    started = data.get("started_at")
    finished = data.get("finished_at")
    if not isinstance(started, str) or not isinstance(finished, str):
        return None
    try:
        start_time = datetime.fromisoformat(started.replace("Z", "+00:00"))
        finish_time = datetime.fromisoformat(finished.replace("Z", "+00:00"))
    except ValueError:
        return None
    return max(0.0, (finish_time - start_time).total_seconds())


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _number_or_none(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _string_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _escape_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
