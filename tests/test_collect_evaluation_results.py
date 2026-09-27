from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from evidence_harness.evaluation import load_matrix
from scripts.collect_evaluation_results import (
    build_manifest,
    build_retry_matrix,
    collect_latest_results,
)


def _write_matrix(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dataset": "terminal-bench@2.0",
                "selection_method": "fixture",
                "tasks": [
                    {
                        "name": "task-a",
                        "difficulty": "easy",
                        "category": "test",
                        "tags": [],
                    },
                    {
                        "name": "task-b",
                        "difficulty": "hard",
                        "category": "test",
                        "tags": ["retry"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )


def _write_completion(
    run_dir: Path,
    *,
    task_name: str,
    timestamp: str,
    reward: float | None,
    exception_type: str | None = None,
) -> None:
    trial_dir = run_dir / task_name / f"{task_name}__trial"
    result_path = trial_dir / "result.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    config_bytes = json.dumps({"trial_name": f"{task_name}__trial"}).encode()
    (trial_dir / "config.json").write_bytes(config_bytes)
    result_path.write_text(
        json.dumps(
            {
                "trial_name": f"{task_name}__trial",
                "task_name": task_name,
                "task_checksum": "a" * 64,
                "task_id": {
                    "git_url": "https://example.com/tasks.git",
                    "git_commit_id": "b" * 40,
                    "path": task_name,
                },
                "verifier_result": (
                    {"rewards": {"reward": reward}} if reward is not None else None
                ),
                "exception_info": ({"exception_type": exception_type} if exception_type else None),
            }
        ),
        encoding="utf-8",
    )
    with (run_dir / "progress.jsonl").open("a", encoding="utf-8") as progress:
        progress.write(
            json.dumps(
                {
                    "event": "task_completed",
                    "task": task_name,
                    "ts": timestamp,
                    "result_path": str(result_path.relative_to(run_dir)),
                }
            )
            + "\n"
        )


def test_collect_latest_results_selects_latest_completion_and_classifies_errors(
    tmp_path: Path,
) -> None:
    matrix_path = tmp_path / "matrix.json"
    first_run = tmp_path / "first"
    second_run = tmp_path / "second"
    _write_matrix(matrix_path)
    _write_completion(
        first_run,
        task_name="task-a",
        timestamp="2026-09-25T10:00:00Z",
        reward=0.0,
    )
    _write_completion(
        first_run,
        task_name="task-b",
        timestamp="2026-09-25T10:01:00Z",
        reward=None,
        exception_type="VerifierTimeoutError",
    )
    _write_completion(
        second_run,
        task_name="task-a",
        timestamp="2026-09-25T11:00:00Z",
        reward=1.0,
    )
    matrix = load_matrix(matrix_path)

    latest = collect_latest_results([first_run, second_run], matrix)
    manifest = build_manifest(matrix, matrix_path, latest)
    retry_matrix = build_retry_matrix(matrix, latest, {"error"})

    assert latest["task-a"].result_path.is_relative_to(second_run)
    assert manifest["counts"] == {
        "completed": 2,
        "passed": 1,
        "failed": 0,
        "error": 1,
    }
    assert (
        manifest["tasks"][0]["result_sha256"]
        == hashlib.sha256(latest["task-a"].result_path.read_bytes()).hexdigest()
    )
    assert (
        manifest["tasks"][0]["config_sha256"]
        == hashlib.sha256(
            (latest["task-a"].result_path.parent / "config.json").read_bytes()
        ).hexdigest()
    )
    assert manifest["tasks"][0]["task_checksum"] == "a" * 64
    assert manifest["tasks"][0]["task_git_commit_id"] == "b" * 40
    assert retry_matrix["tasks"] == [
        {
            "name": "task-b",
            "difficulty": "hard",
            "category": "test",
            "tags": ["retry"],
        }
    ]


def test_collect_latest_results_requires_every_matrix_task(tmp_path: Path) -> None:
    matrix_path = tmp_path / "matrix.json"
    run_dir = tmp_path / "run"
    _write_matrix(matrix_path)
    _write_completion(
        run_dir,
        task_name="task-a",
        timestamp="2026-09-25T10:00:00Z",
        reward=1.0,
    )

    with pytest.raises(ValueError, match="matrix tasks have no completed result: task-b"):
        collect_latest_results([run_dir], load_matrix(matrix_path))


def test_collect_latest_results_rejects_event_result_task_mismatch(tmp_path: Path) -> None:
    matrix_path = tmp_path / "matrix.json"
    run_dir = tmp_path / "run"
    _write_matrix(matrix_path)
    _write_completion(
        run_dir,
        task_name="task-a",
        timestamp="2026-09-25T10:00:00Z",
        reward=1.0,
    )
    result_path = next(run_dir.rglob("result.json"))
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["task_name"] = "task-b"
    result_path.write_text(json.dumps(result), encoding="utf-8")

    with pytest.raises(ValueError, match="task mismatch"):
        collect_latest_results([run_dir], load_matrix(matrix_path))


def test_collect_latest_results_reads_direct_harbor_job(tmp_path: Path) -> None:
    matrix_path = tmp_path / "matrix.json"
    direct_run = tmp_path / "direct"
    orchestrated_run = tmp_path / "orchestrated"
    _write_matrix(matrix_path)
    _write_completion(
        direct_run,
        task_name="task-a",
        timestamp="2026-09-25T10:00:00Z",
        reward=1.0,
    )
    (direct_run / "progress.jsonl").unlink()
    result_path = next(direct_run.rglob("result.json"))
    result = json.loads(result_path.read_text(encoding="utf-8"))
    result["finished_at"] = "2026-09-25T10:00:00Z"
    result_path.write_text(json.dumps(result), encoding="utf-8")
    _write_completion(
        orchestrated_run,
        task_name="task-b",
        timestamp="2026-09-25T10:01:00Z",
        reward=0.0,
    )

    latest = collect_latest_results(
        [direct_run, orchestrated_run],
        load_matrix(matrix_path),
    )

    assert latest["task-a"].result_path == result_path
    assert latest["task-b"].status == "failed"


def test_collect_latest_results_recovers_result_written_before_progress_event(
    tmp_path: Path,
) -> None:
    matrix_path = tmp_path / "matrix.json"
    run_dir = tmp_path / "run"
    _write_matrix(matrix_path)
    _write_completion(
        run_dir,
        task_name="task-a",
        timestamp="2026-09-25T10:00:00Z",
        reward=1.0,
    )
    orphaned_result = run_dir / "task-b" / "task-b__trial" / "result.json"
    orphaned_result.parent.mkdir(parents=True)
    orphaned_result.write_text(
        json.dumps(
            {
                "trial_name": "task-b__trial",
                "task_name": "task-b",
                "finished_at": "2026-09-25T10:01:00Z",
                "verifier_result": {"rewards": {"reward": 0.0}},
                "exception_info": None,
            }
        ),
        encoding="utf-8",
    )

    latest = collect_latest_results([run_dir], load_matrix(matrix_path))

    assert latest["task-a"].status == "passed"
    assert latest["task-b"].result_path == orphaned_result
    assert latest["task-b"].status == "failed"
