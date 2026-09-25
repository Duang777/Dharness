from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.freeze_evaluation import freeze_result_sets, freeze_results


def _write_matrix(path: Path, *task_names: str) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dataset": "terminal-bench@2.0",
                "selection_method": "test",
                "tasks": [
                    {
                        "name": name,
                        "difficulty": "easy",
                        "category": "test",
                    }
                    for name in task_names
                ],
            }
        ),
        encoding="utf-8",
    )


def _write_result(path: Path, task_name: str) -> bytes:
    payload = {
        "trial_name": f"{task_name}__trial",
        "task_name": task_name,
        "config": {
            "task": {"path": task_name},
            "agent": {
                "name": "evidence_harness.harbor_agent:EvidenceHarnessAgent",
                "model_name": "openai/test-model",
                "kwargs": {
                    "api_base": "https://private.example/v1",
                    "api_key": "sk-not-for-snapshots",
                },
            },
            "trials_dir": "/private/absolute/path",
        },
        "agent_result": {
            "n_input_tokens": 10,
            "n_cache_tokens": 2,
            "n_output_tokens": 3,
            "cost_usd": 0.01,
            "metadata": {
                "evidence_harness": {
                    "phase": "terminated",
                    "turns_used": 2,
                    "environment_calls_used": 3,
                    "repairs_used": 0,
                    "recoveries_used": 0,
                    "work_epoch": 1,
                    "stop_reason": "verified",
                    "failure_category": None,
                    "journal": "/private/journal/events.jsonl",
                    "final_summary": "task-private output",
                    "latest_evidence_accepted": True,
                }
            },
        },
        "verifier_result": {"rewards": {"reward": 1.0}},
        "exception_info": {
            "exception_type": "ExampleError",
            "exception_traceback": "/private/traceback",
        },
        "started_at": "2026-09-24T10:00:00Z",
        "finished_at": "2026-09-24T10:00:05Z",
    }
    raw = json.dumps(payload).encode()
    path.parent.mkdir(parents=True)
    path.write_bytes(raw)
    return raw


def test_freeze_results_writes_sanitized_provenance_snapshot(tmp_path: Path) -> None:
    matrix_path = tmp_path / "matrix.json"
    results_dir = tmp_path / "raw"
    output_dir = tmp_path / "frozen"
    _write_matrix(matrix_path, "example")
    source_bytes = _write_result(results_dir / "example__trial" / "result.json", "example")

    count = freeze_results(results_dir, matrix_path, output_dir)

    assert count == 1
    snapshot_path = output_dir / "example" / "result.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert snapshot["snapshot"]["source_result_sha256"] == hashlib.sha256(source_bytes).hexdigest()
    assert snapshot["config"]["agent"]["model_name"] == "openai/test-model"
    assert snapshot["verifier_result"]["rewards"]["reward"] == 1.0
    assert snapshot["exception_info"] == {"exception_type": "ExampleError"}
    serialized = snapshot_path.read_text(encoding="utf-8")
    for forbidden in (
        "api_base",
        "api_key",
        "sk-not-for-snapshots",
        "/private/",
        "traceback",
        "final_summary",
    ):
        assert forbidden not in serialized


def test_freeze_results_requires_exact_matrix_coverage(tmp_path: Path) -> None:
    matrix_path = tmp_path / "matrix.json"
    results_dir = tmp_path / "raw"
    _write_matrix(matrix_path, "present", "missing")
    _write_result(results_dir / "present__trial" / "result.json", "present")

    with pytest.raises(ValueError, match="missing: missing"):
        freeze_results(results_dir, matrix_path, tmp_path / "frozen")


def test_freeze_result_sets_combines_distinct_jobs(tmp_path: Path) -> None:
    matrix_path = tmp_path / "matrix.json"
    first_results = tmp_path / "first"
    second_results = tmp_path / "second"
    output_dir = tmp_path / "frozen"
    stale_snapshot = output_dir / "stale-task" / "result.json"
    _write_matrix(matrix_path, "first-task", "second-task")
    _write_result(first_results / "first-task__trial" / "result.json", "first-task")
    _write_result(second_results / "second-task__trial" / "result.json", "second-task")
    _write_result(stale_snapshot, "stale-task")

    count = freeze_result_sets((first_results, second_results), matrix_path, output_dir)

    assert count == 2
    assert (output_dir / "first-task" / "result.json").is_file()
    assert (output_dir / "second-task" / "result.json").is_file()
    assert not stale_snapshot.exists()


def test_freeze_result_sets_rejects_duplicates_across_jobs(tmp_path: Path) -> None:
    matrix_path = tmp_path / "matrix.json"
    first_results = tmp_path / "first"
    second_results = tmp_path / "second"
    _write_matrix(matrix_path, "duplicate")
    _write_result(first_results / "duplicate__first" / "result.json", "duplicate")
    _write_result(second_results / "duplicate__second" / "result.json", "duplicate")

    with pytest.raises(ValueError, match="multiple trial results for task 'duplicate'"):
        freeze_result_sets(
            (first_results, second_results),
            matrix_path,
            tmp_path / "frozen",
        )
