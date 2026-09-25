from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from scripts import run_full_evaluation


def _write_matrix(path: Path, names: list[str]) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dataset": "terminal-bench@2.0",
                "selection_method": "fixture",
                "tasks": [
                    {
                        "name": name,
                        "difficulty": "medium",
                        "category": "software-engineering",
                        "tags": [],
                    }
                    for name in names
                ],
            }
        ),
        encoding="utf-8",
    )


def _write_trial_result(job_dir: Path, task_name: str, reward: float = 1.0) -> Path:
    result_path = job_dir / f"{task_name}__trial" / "result.json"
    result_path.parent.mkdir(parents=True)
    result_path.write_text(
        json.dumps(
            {
                "trial_name": f"{task_name}__trial",
                "task_name": task_name,
                "verifier_result": {"rewards": {"reward": reward}},
                "exception_info": None,
            }
        ),
        encoding="utf-8",
    )
    return result_path


def test_build_task_command_is_serial_and_scopes_https_mount() -> None:
    command = run_full_evaluation.build_task_command(
        model="openai/test-model",
        env_file=Path("/tmp/test.env"),
        matrix=Path("/tmp/matrix.json"),
        run_root=Path("/tmp/jobs/full"),
        job_name="001-fix-git",
        task_name="fix-git",
        agent_kwargs=["api_base=https://example.test/v1"],
    )

    concurrency_index = command.index("--n-concurrent")
    assert command[concurrency_index + 1] == "1"
    assert "--debian-https-sources" in command
    assert command[-2:] == ["--agent-kwarg", "api_base=https://example.test/v1"]

    other_command = run_full_evaluation.build_task_command(
        model="openai/test-model",
        env_file=Path("/tmp/test.env"),
        matrix=Path("/tmp/matrix.json"),
        run_root=Path("/tmp/jobs/full"),
        job_name="002-other",
        task_name="other",
        agent_kwargs=[],
    )
    assert "--debian-https-sources" not in other_command


def test_completed_task_results_reads_trials_and_ignores_job_summary(tmp_path: Path) -> None:
    (tmp_path / "result.json").write_text('{"n_total_trials": 1}', encoding="utf-8")
    result_path = _write_trial_result(tmp_path / "001-task-a", "task-a")

    assert run_full_evaluation.completed_task_results(tmp_path) == {"task-a": result_path}


def test_full_run_resumes_from_real_result_and_runs_remaining_task(
    tmp_path: Path,
    monkeypatch,
) -> None:
    matrix = tmp_path / "matrix.json"
    env_file = tmp_path / "provider.env"
    jobs_dir = tmp_path / "jobs"
    run_root = jobs_dir / "full-run"
    _write_matrix(matrix, ["task-a", "task-b"])
    env_file.write_text("OPENAI_API_KEY=secret\n", encoding="utf-8")
    _write_trial_result(run_root / "001-task-a", "task-a")
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        job_name = command[command.index("--job-name") + 1]
        task_name = command[command.index("--include-task-name") + 1]
        _write_trial_result(run_root / job_name, task_name, reward=0.0)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(run_full_evaluation.subprocess, "run", fake_run)
    args = argparse.Namespace(
        model="openai/test-model",
        env_file=env_file,
        matrix=matrix,
        jobs_dir=jobs_dir,
        run_name="full-run",
        agent_kwarg=[],
    )

    assert run_full_evaluation.run_full_evaluation(args) == 0
    assert len(calls) == 1
    assert calls[0][calls[0].index("--include-task-name") + 1] == "task-b"

    events = [
        json.loads(line)
        for line in (run_root / "progress.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [event["event"] for event in events] == [
        "run_started",
        "task_started",
        "task_completed",
        "run_completed",
    ]
    assert events[-2]["reward"] == 0.0
