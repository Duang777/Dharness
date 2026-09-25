from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest

from evidence_harness.evaluation import (
    EvaluationMatrix,
    MatrixTask,
    load_matrix,
    render_markdown,
    summarize_results,
)


def _write_trial(
    root: Path,
    *,
    task: str,
    reward: float | None,
    exception_type: str | None = None,
    trial_id: str = "trial",
    replay_source_sha256: str | None = None,
    model_name: str | None = "openai/test-model",
) -> None:
    trial_dir = root / f"{task}__{trial_id}"
    trial_dir.mkdir(parents=True)
    payload = {
        "snapshot": {"source_result_sha256": "b" * 64},
        "trial_name": f"{task}__{trial_id}",
        "task_name": f"terminal-bench/{task}",
        "config": {
            "task": {"name": task},
            "agent": {"model_name": model_name},
        },
        "agent_result": {
            "n_input_tokens": 100,
            "n_output_tokens": 20,
            "cost_usd": 0.01,
            "metadata": {
                "evidence_harness": {
                    "stop_reason": "verified",
                    "turns_used": 3,
                    "environment_calls_used": 5,
                    "failure_category": None,
                },
                **(
                    {
                        "evidence_harness_replay": {
                            "source_sha256": replay_source_sha256,
                            "completed": True,
                        }
                    }
                    if replay_source_sha256 is not None
                    else {}
                ),
            },
        },
        "verifier_result": ({"rewards": {"reward": reward}} if reward is not None else None),
        "exception_info": (
            {"exception_type": exception_type} if exception_type is not None else None
        ),
        "started_at": "2026-09-24T10:00:00Z",
        "finished_at": "2026-09-24T10:01:30Z",
    }
    (trial_dir / "result.json").write_text(json.dumps(payload), encoding="utf-8")


def test_repository_matrix_is_balanced_and_unique() -> None:
    matrix = load_matrix(Path("evaluation/matrix.json"))

    assert matrix.dataset == "terminal-bench@2.0"
    assert len(matrix.tasks) == 10
    assert len({task.name for task in matrix.tasks}) == 10
    assert Counter(task.difficulty for task in matrix.tasks) == {
        "easy": 3,
        "medium": 4,
        "hard": 3,
    }
    assert len({task.category for task in matrix.tasks}) >= 6


def test_summary_keeps_missing_tasks_out_of_failure_count(tmp_path) -> None:
    matrix = EvaluationMatrix(
        schema_version=1,
        dataset="terminal-bench@2.0",
        selection_method="test",
        tasks=tuple(
            MatrixTask(name=name, difficulty="medium", category="test")
            for name in ("passed", "failed", "errored", "missing")
        ),
    )
    _write_trial(tmp_path, task="passed", reward=1.0)
    _write_trial(tmp_path, task="failed", reward=0.0)
    _write_trial(
        tmp_path,
        task="errored",
        reward=None,
        exception_type="AgentAuthenticationError",
    )

    summary = summarize_results(matrix, tmp_path)

    assert summary.complete is False
    assert summary.all_tasks_attempted is False
    assert summary.executed_tasks == 3
    assert summary.passed_tasks == 1
    assert summary.failed_tasks == 1
    assert summary.errored_tasks == 1
    assert summary.not_run_tasks == 1
    assert summary.scored_tasks == 2
    assert summary.pass_rate == pytest.approx(1 / 3)
    assert summary.scored_pass_rate == pytest.approx(1 / 2)
    assert summary.execution_coverage == pytest.approx(3 / 4)
    assert summary.scored_coverage == pytest.approx(1 / 2)
    assert summary.tasks[0].duration_sec == 90
    assert summary.tasks[0].execution_mode == "live"
    assert summary.tasks[0].model_name == "openai/test-model"
    assert summary.tasks[0].source_result_sha256 == "b" * 64
    assert summary.tasks[-1].status == "not_run"
    assert summary.tasks[-1].execution_mode == "not_run"

    markdown = render_markdown(summary)
    assert "已尝试任务通过率: `33.3%`" in markdown
    assert "已评分任务通过率: `50.0%`" in markdown
    assert "`error` 任务计入已尝试任务通过率" in markdown
    assert "| passed | 中等 | 测试 | openai/test-model | 实时 | 通过 | 1 | 已验证 |" in markdown


def test_empty_results_report_has_no_pass_rate(tmp_path) -> None:
    matrix = EvaluationMatrix(
        schema_version=1,
        dataset="terminal-bench@2.0",
        selection_method="test",
        tasks=(
            MatrixTask(
                name="not-started",
                difficulty="easy",
                category="debugging",
            ),
        ),
    )

    summary = summarize_results(matrix, tmp_path / "does-not-exist")

    assert summary.executed_tasks == 0
    assert summary.scored_tasks == 0
    assert summary.pass_rate is None
    assert summary.scored_pass_rate is None
    assert summary.execution_coverage == 0
    assert summary.scored_coverage == 0
    assert "已尝试任务通过率: `不适用`" in render_markdown(summary)
    assert "已评分任务通过率: `不适用`" in render_markdown(summary)


def test_error_only_results_have_no_pass_rate(tmp_path) -> None:
    matrix = EvaluationMatrix(
        schema_version=1,
        dataset="terminal-bench@2.0",
        selection_method="test",
        tasks=(
            MatrixTask(
                name="errored",
                difficulty="easy",
                category="debugging",
            ),
        ),
    )
    _write_trial(
        tmp_path,
        task="errored",
        reward=None,
        exception_type="VerifierTimeoutError",
    )

    summary = summarize_results(matrix, tmp_path)

    assert summary.complete is False
    assert summary.all_tasks_attempted is True
    assert summary.executed_tasks == 1
    assert summary.scored_tasks == 0
    assert summary.pass_rate == 0
    assert summary.scored_pass_rate is None
    assert summary.execution_coverage == 1
    assert summary.scored_coverage == 0


def test_duplicate_task_results_are_rejected(tmp_path) -> None:
    matrix = EvaluationMatrix(
        schema_version=1,
        dataset="terminal-bench@2.0",
        selection_method="test",
        tasks=(
            MatrixTask(
                name="duplicate",
                difficulty="easy",
                category="debugging",
            ),
        ),
    )
    _write_trial(tmp_path, task="duplicate", reward=0.0, trial_id="first")
    _write_trial(tmp_path, task="duplicate", reward=1.0, trial_id="second")

    with pytest.raises(ValueError, match="multiple trial results"):
        summarize_results(matrix, tmp_path)


def test_replay_result_is_labeled_with_source_hash(tmp_path) -> None:
    matrix = EvaluationMatrix(
        schema_version=1,
        dataset="terminal-bench@2.0",
        selection_method="test",
        tasks=(
            MatrixTask(
                name="replayed",
                difficulty="easy",
                category="debugging",
            ),
        ),
    )
    source_sha256 = "a" * 64
    _write_trial(
        tmp_path,
        task="replayed",
        reward=1.0,
        replay_source_sha256=source_sha256,
        model_name=None,
    )

    summary = summarize_results(matrix, tmp_path)

    assert summary.schema_version == 4
    assert summary.tasks[0].execution_mode == "replay"
    assert summary.tasks[0].source_journal_sha256 == source_sha256
    markdown = render_markdown(summary)
    assert ("| replayed | 简单 | 调试 | 不适用 (日志回放) | 回放 | 通过 | 1 | 已验证 |") in markdown
    assert "`replay` 运行会执行先前记录的 Agent 决策" in markdown
