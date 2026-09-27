from __future__ import annotations

import json
from pathlib import Path

from scripts.freeze_evaluation import sanitize_result
from scripts.verify_delivery import (
    PROJECT_ROOT,
    _validate_canonical_manifest,
    _validate_frozen_provenance,
    _validate_snapshot_layout,
    _validate_zero_error_retry_matrix,
    validate_delivery,
)


def test_repository_delivery_is_complete() -> None:
    assert validate_delivery(PROJECT_ROOT) == []


def test_snapshot_layout_rejects_nested_and_mismatched_results(tmp_path: Path) -> None:
    trials_dir = Path("evaluation/trials")
    valid_path = tmp_path / trials_dir / "expected" / "result.json"
    valid_path.parent.mkdir(parents=True)
    valid_path.write_text(json.dumps({"task_name": "wrong"}), encoding="utf-8")
    nested_path = tmp_path / trials_dir / "stale" / "deep" / "result.json"
    nested_path.parent.mkdir(parents=True)
    nested_path.write_text("{}", encoding="utf-8")

    errors = _validate_snapshot_layout(tmp_path, trials_dir, {"expected"})

    assert any("invalid trial path" in error for error in errors)
    assert any("snapshot task does not match path" in error for error in errors)


def test_canonical_manifest_rejects_stale_outcomes_and_absolute_paths(
    tmp_path: Path,
) -> None:
    matrix_path = Path("evaluation/matrix.json")
    manifest_path = Path("evaluation/canonical.json")
    results_path = Path("evaluation/results.json")
    (tmp_path / matrix_path).parent.mkdir(parents=True)
    (tmp_path / matrix_path).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dataset": "terminal-bench@2.0",
                "selection_method": "test",
                "tasks": [
                    {
                        "name": "example",
                        "difficulty": "easy",
                        "category": "test",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / manifest_path).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dataset": "terminal-bench@2.0",
                "matrix_sha256": "wrong",
                "counts": {"completed": 1, "passed": 0, "failed": 1, "error": 0},
                "tasks": [
                    {
                        "name": "example",
                        "status": "failed",
                        "reward": 0.0,
                        "result_path": "/private/result.json",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / results_path).write_text(
        json.dumps(
            {
                "executed_tasks": 1,
                "passed_tasks": 1,
                "failed_tasks": 0,
                "errored_tasks": 0,
                "tasks": [{"name": "example", "status": "passed", "reward": 1.0}],
            }
        ),
        encoding="utf-8",
    )

    errors = _validate_canonical_manifest(
        tmp_path,
        manifest_path=manifest_path,
        matrix_path=matrix_path,
        results_json=results_path,
    )

    assert any("matrix hash" in error for error in errors)
    assert any("outcomes" in error for error in errors)
    assert any("counts" in error for error in errors)
    assert any("absolute result path" in error for error in errors)


def test_frozen_provenance_requires_well_formed_task_and_config_bindings(
    tmp_path: Path,
) -> None:
    trials_dir = Path("evaluation/trials")
    snapshot_path = tmp_path / trials_dir / "example" / "result.json"
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_text(
        json.dumps(
            {
                "task_name": "example",
                "snapshot": {
                    "schema_version": 2,
                    "source_result_sha256": "a" * 64,
                    "source_config_sha256": "b" * 64,
                },
                "task": {
                    "checksum": "c" * 64,
                    "git_url": "https://example.com/tasks.git",
                    "git_commit_id": "d" * 40,
                },
                "config": {"agent": {}, "environment": {"type": "docker"}},
            }
        ),
        encoding="utf-8",
    )

    assert _validate_frozen_provenance(tmp_path, trials_dir) == []

    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    snapshot["task"]["checksum"] = "invalid"
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

    errors = _validate_frozen_provenance(tmp_path, trials_dir)
    assert any("task checksum" in error for error in errors)


def test_frozen_provenance_matches_available_raw_result_and_config(tmp_path: Path) -> None:
    trials_dir = Path("evaluation/trials")
    manifest_path = Path("evaluation/canonical.json")
    raw_path = Path("runs/terminal-bench-2/job/example/result.json")
    config = {
        "job_id": "job-1",
        "trial_name": "example__trial",
        "task": {
            "path": "example",
            "source": "terminal-bench",
            "git_url": "https://example.com/tasks.git",
            "git_commit_id": "b" * 40,
        },
        "agent": {
            "name": "evidence_harness.harbor_agent:EvidenceHarnessAgent",
            "model_name": "openai/test-model",
            "kwargs": {"max_turns": 40},
        },
        "environment": {"type": "docker"},
    }
    source = {
        "trial_name": "example__trial",
        "task_name": "example",
        "source": "terminal-bench",
        "task_checksum": "a" * 64,
        "task_id": {
            "git_url": "https://example.com/tasks.git",
            "git_commit_id": "b" * 40,
            "path": "example",
        },
        "config": config,
        "agent_result": {"metadata": {}},
        "verifier_result": {"rewards": {"reward": 1.0}},
    }
    source_bytes = json.dumps(source).encode()
    config_bytes = json.dumps(config).encode()
    source_path = tmp_path / raw_path
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(source_bytes)
    (source_path.parent / "config.json").write_bytes(config_bytes)
    snapshot_path = tmp_path / trials_dir / "example" / "result.json"
    snapshot_path.parent.mkdir(parents=True)
    snapshot = sanitize_result(source, source_bytes, config_bytes)
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    (tmp_path / manifest_path).write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "name": "example",
                        "result_path": str(raw_path),
                        "config_sha256": snapshot["snapshot"]["source_config_sha256"],
                        "task_checksum": snapshot["task"]["checksum"],
                        "task_git_url": snapshot["task"]["git_url"],
                        "task_git_commit_id": snapshot["task"]["git_commit_id"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    assert (
        _validate_frozen_provenance(
            tmp_path,
            trials_dir,
            manifest_path=manifest_path,
        )
        == []
    )

    snapshot["snapshot"]["source_config_sha256"] = "c" * 64
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")
    errors = _validate_frozen_provenance(
        tmp_path,
        trials_dir,
        manifest_path=manifest_path,
    )
    assert any("config hash does not match local source" in error for error in errors)

    snapshot = sanitize_result(source, source_bytes, config_bytes)
    snapshot["task"]["checksum"] = "c" * 64
    snapshot_path.write_text(json.dumps(snapshot), encoding="utf-8")

    errors = _validate_frozen_provenance(
        tmp_path,
        trials_dir,
        manifest_path=manifest_path,
    )
    assert any("does not match local source" in error for error in errors)


def test_frozen_provenance_rejects_legacy_replay(tmp_path: Path) -> None:
    trials_dir = Path("evaluation/trials")
    snapshot_path = tmp_path / trials_dir / "example" / "result.json"
    snapshot_path.parent.mkdir(parents=True)
    snapshot_path.write_text(
        json.dumps(
            {
                "task_name": "example",
                "snapshot": {
                    "schema_version": 2,
                    "source_result_sha256": "a" * 64,
                    "source_config_sha256": "b" * 64,
                },
                "task": {
                    "checksum": "c" * 64,
                    "git_url": "https://example.com/tasks.git",
                    "git_commit_id": "d" * 40,
                },
                "config": {"agent": {}, "environment": {"type": "docker"}},
                "agent_result": {
                    "metadata": {
                        "evidence_harness_replay": {
                            "replay_mode": "legacy_all_decisions",
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    errors = _validate_frozen_provenance(tmp_path, trials_dir)

    assert any("used legacy replay" in error for error in errors)


def test_zero_error_retry_matrix_must_be_empty(tmp_path: Path) -> None:
    retry_path = Path("evaluation/retry.json")
    results_path = Path("evaluation/results.json")
    (tmp_path / retry_path).parent.mkdir(parents=True)
    (tmp_path / retry_path).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dataset": "terminal-bench@2.0",
                "tasks": [{"name": "stale-error"}],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / results_path).write_text(
        json.dumps(
            {
                "dataset": "terminal-bench@2.0",
                "errored_tasks": 0,
            }
        ),
        encoding="utf-8",
    )

    errors = _validate_zero_error_retry_matrix(
        tmp_path,
        retry_matrix_path=retry_path,
        results_json=results_path,
    )

    assert any("must be empty" in error for error in errors)
