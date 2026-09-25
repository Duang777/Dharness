from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

DIFFICULTY_LABELS = {
    "easy": "简单",
    "medium": "中等",
    "hard": "困难",
}
CATEGORY_LABELS = {
    "debugging": "调试",
    "software-engineering": "软件工程",
    "data-processing": "数据处理",
    "security": "安全",
    "scientific-computing": "科学计算",
    "system-administration": "系统管理",
    "mathematics": "数学",
    "file-operations": "文件操作",
    "data-science": "数据科学",
    "test": "测试",
}
MODE_LABELS = {
    "live": "实时",
    "replay": "回放",
    "not_run": "未运行",
}
STATUS_LABELS = {
    "passed": "通过",
    "failed": "失败",
    "error": "错误",
    "not_run": "未运行",
}
STOP_REASON_LABELS = {
    "verified": "已验证",
    "budget_exhausted": "预算耗尽",
    "doom_loop": "重复循环",
    "model_stopped": "模型主动停止",
    "policy_blocked": "策略阻止",
    "infrastructure_failure": "基础设施失败",
    "model_failure": "模型失败",
}


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
    model_name: str | None = None
    status: str
    execution_mode: Literal["live", "replay", "not_run"]
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
    source_result_sha256: str | None = None
    source_journal_sha256: str | None = None


class EvaluationSummary(BaseModel):
    schema_version: int = 4
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
        source_dir=_display_path(results_dir),
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
    pass_rate = f"{summary.pass_rate:.1%}" if summary.pass_rate is not None else "不适用"
    scored_pass_rate = (
        f"{summary.scored_pass_rate:.1%}" if summary.scored_pass_rate is not None else "不适用"
    )
    lines = [
        "# Terminal-Bench 2.0 评测结果",
        "",
        f"- 数据集: `{summary.dataset}`",
        f"- 评分完整: `{'是' if summary.complete else '否'}`",
        f"- 所有任务均已尝试: `{'是' if summary.all_tasks_attempted else '否'}`",
        f"- 已执行: `{summary.executed_tasks}/{summary.selected_tasks}`",
        f"- 已评分: `{summary.scored_tasks}/{summary.selected_tasks}`",
        f"- 通过 / 失败 / 错误: "
        f"`{summary.passed_tasks} / {summary.failed_tasks} / {summary.errored_tasks}`",
        f"- 已尝试任务通过率: `{pass_rate}`",
        f"- 已评分任务通过率: `{scored_pass_rate}`",
        f"- 执行覆盖率: `{summary.execution_coverage:.1%}`",
        f"- 评分覆盖率: `{summary.scored_coverage:.1%}`",
        "",
        "| 任务 | 难度 | 类别 | 模型 | 模式 | 状态 | 奖励 | 停止原因 |",
        "|---|---|---|---|---|---|---:|---|",
    ]
    for task in summary.tasks:
        reward = "不适用" if task.reward is None else f"{task.reward:g}"
        model_name = task.model_name or (
            "不适用 (日志回放)" if task.execution_mode == "replay" else ""
        )
        lines.append(
            "| "
            + " | ".join(
                (
                    _escape_cell(task.name),
                    _display_label(DIFFICULTY_LABELS, task.difficulty),
                    _display_label(CATEGORY_LABELS, task.category),
                    _escape_cell(model_name),
                    _display_label(MODE_LABELS, task.execution_mode),
                    _display_label(STATUS_LABELS, task.status),
                    reward,
                    _display_label(STOP_REASON_LABELS, task.stop_reason or ""),
                )
            )
            + " |"
        )

    if summary.errored_tasks or summary.not_run_tasks:
        lines.extend(
            (
                "",
                "> `error` 任务计入已尝试任务通过率。不计入已评分任务通过率。"
                "`not_run` 任务不计入这两个通过率。",
            )
        )
    if any(task.execution_mode == "replay" for task in summary.tasks):
        lines.extend(
            (
                "",
                "> `replay` 运行会执行先前记录的 Agent 决策。不会发起新的模型调用。",
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
                f"multiple trial results for task '{task_name}': {previous[1]} and {result_path}"
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
            execution_mode="not_run",
        )

    data, result_path = trial
    verifier_result = _mapping(data.get("verifier_result"))
    rewards = _mapping(verifier_result.get("rewards"))
    reward = _number_or_none(rewards.get("reward"))
    exception = _mapping(data.get("exception_info"))
    metadata = _mapping(_mapping(data.get("agent_result")).get("metadata"))
    harness = _mapping(metadata.get("evidence_harness"))
    replay = _mapping(metadata.get("evidence_harness_replay"))
    agent_config = _mapping(_mapping(data.get("config")).get("agent"))
    snapshot = _mapping(data.get("snapshot"))

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
        model_name=_string_or_none(agent_config.get("model_name")),
        status=status,
        execution_mode="replay" if replay else "live",
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
        source_result_sha256=_string_or_none(snapshot.get("source_result_sha256")),
        source_journal_sha256=_string_or_none(replay.get("source_sha256")),
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


def _display_label(labels: dict[str, str], value: str) -> str:
    return _escape_cell(labels.get(value, value))


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path)
