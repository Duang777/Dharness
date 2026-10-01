from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from evidence_harness.collection_profile import PREFIXBENCH_V1
from evidence_harness_mutation import (
    PrefixBenchExclusion,
    PrefixBenchReadiness,
    PrefixBenchReadinessV2,
    PrefixBenchSplit,
    check_prefixbench_readiness,
    inspect_prefixbench,
    prefixbench_task_split,
    validate_phase_trace,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _dump(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode()


def _event(event_type: str, payload: dict[str, object]) -> str:
    return json.dumps(
        {
            "timestamp": "2026-10-02T00:00:00+00:00",
            "type": event_type,
            "payload": payload,
        },
        sort_keys=True,
    )


def _journal(*, schema: int, ready: bool) -> bytes:
    producer = {
        "commit": "a" * 40,
        "tree": "b" * 40,
        "source_sha256": "c" * 64,
    }
    started: dict[str, object] = {
        "journal_schema_version": schema,
        "instruction": "test instruction",
        "options": {"enable_completion_review": True},
    }
    if ready:
        started["prefixbench_profile"] = "prefixbench-v1"
        started["producer"] = producer
    events = [_event("run_started", started)]
    if ready:
        events.extend(
            _event(event_type, {})
            for event_type in (
                "executor_turn_started",
                "work_batch_started",
                "finalization_started",
                "completion_review_started",
                "recovery_required",
            )
        )
    events.extend(
        (
            _event("agent_decision", {"action": "finish"}),
            _event("completion_review", {}),
            _event("completion_rejected", {}),
            _event("replanned", {}),
            _event("run_finished", {"stop_reason": "verified"}),
        )
    )
    return ("\n".join(events) + "\n").encode()


def _create_source_project(
    root: Path,
    *,
    ready: bool = False,
    include_replay: bool = False,
) -> tuple[Path, Path, tuple[Path, ...]]:
    names = ["live-task"]
    if include_replay:
        names.append("replay-task")
    matrix = {
        "schema_version": 1,
        "dataset": "terminal-bench@2.0",
        "selection_method": "test",
        "tasks": [{"name": name} for name in names],
    }
    matrix_path = root / "evaluation" / "matrix.json"
    matrix_path.parent.mkdir(parents=True)
    matrix_bytes = _dump(matrix)
    matrix_path.write_bytes(matrix_bytes)

    rows: list[dict[str, Any]] = []
    raw_paths: list[Path] = []
    for index, name in enumerate(names, start=1):
        run_dir = Path("runs") / "terminal-bench-2" / "job"
        trial_dir = run_dir / f"{index:03d}-{name}" / f"{name}__trial"
        result_path = trial_dir / "result.json"
        config_path = trial_dir / "config.json"
        absolute_trial = root / trial_dir
        absolute_trial.mkdir(parents=True)

        config: dict[str, Any] = {
            "task": {
                "path": name,
                "git_url": "https://example.test/terminal-bench.git",
                "git_commit_id": "d" * 40,
            },
            "agent": {"name": "evidence_harness.harbor_agent:EvidenceHarnessAgent"},
            "job_id": "test-job",
            "trial_name": f"{name}__trial",
        }
        replay = name == "replay-task"
        metadata = (
            {"evidence_harness_replay": {"source": "test"}}
            if replay
            else {"evidence_harness": {"stop_reason": "verified"}}
        )
        result: dict[str, Any] = {
            "task_name": name,
            "task_checksum": hashlib.sha256(name.encode()).hexdigest(),
            "task_id": {
                "name": name,
                "git_url": config["task"]["git_url"],
                "git_commit_id": config["task"]["git_commit_id"],
            },
            "config": config,
            "verifier_result": {"rewards": {"reward": 1.0}},
            "exception_info": None,
            "agent_result": {"metadata": metadata},
        }
        config_bytes = _dump(config)
        result_bytes = _dump(result)
        (root / config_path).write_bytes(config_bytes)
        (root / result_path).write_bytes(result_bytes)
        raw_paths.extend((root / result_path, root / config_path))

        row = {
            "index": index,
            "name": name,
            "result_path": str(result_path),
            "result_sha256": hashlib.sha256(result_bytes).hexdigest(),
            "config_sha256": hashlib.sha256(config_bytes).hexdigest(),
            "run_dir": str(run_dir),
            "task_checksum": result["task_checksum"],
            "task_git_url": config["task"]["git_url"],
            "task_git_commit_id": config["task"]["git_commit_id"],
            "reward": 1.0,
            "status": "passed",
            "exception_type": None,
        }
        if not replay:
            journal_path = trial_dir / "agent" / "evidence-harness" / "events.jsonl"
            journal_bytes = _journal(schema=2 if ready else 1, ready=ready)
            (root / journal_path).parent.mkdir(parents=True)
            (root / journal_path).write_bytes(journal_bytes)
            raw_paths.append(root / journal_path)
            if ready:
                row.update(
                    {
                        "journal_path": str(journal_path),
                        "journal_sha256": hashlib.sha256(journal_bytes).hexdigest(),
                        "producer_commit": "a" * 40,
                        "producer_tree": "b" * 40,
                        "producer_source_sha256": "c" * 64,
                    }
                )
        rows.append(row)

    canonical = {
        "schema_version": 1,
        "dataset": matrix["dataset"],
        "matrix_sha256": hashlib.sha256(matrix_bytes).hexdigest(),
        "counts": {
            "completed": len(rows),
            "passed": len(rows),
            "failed": 0,
            "error": 0,
        },
        "tasks": rows,
    }
    canonical_path = root / "evaluation" / "canonical.json"
    canonical_path.write_bytes(_dump(canonical))
    return canonical_path, matrix_path, tuple(raw_paths)


def _development_task_name() -> str:
    for index in range(100):
        name = f"development-task-{index}"
        split, _bucket, _digest = prefixbench_task_split(
            dataset="terminal-bench@2.0",
            name=name,
            task_checksum=hashlib.sha256(name.encode()).hexdigest(),
            task_git_url="https://example.test/terminal-bench.git",
            task_git_commit_id="d" * 40,
        )
        if split is PrefixBenchSplit.DEVELOPMENT:
            return name
    raise AssertionError("fixture could not find a development task")


def _v2_events(*, include_recovery: bool = True, review_has_outcome: bool = True) -> list[str]:
    producer = {
        "commit": "a" * 40,
        "tree": "b" * 40,
        "source_sha256": "c" * 64,
    }
    events = [
        _event(
            "run_started",
            {
                "journal_schema_version": 2,
                "instruction": "test instruction",
                "options": dict(PREFIXBENCH_V1.controlled_agent_options),
                "prefixbench_profile": "prefixbench-v1",
                "producer": producer,
            },
        ),
        _event(
            "executor_turn_started",
            {
                "attempt_id": 1,
                "turns_completed": 0,
                "work_epoch": 0,
                "recovery_required": False,
            },
        ),
        _event("agent_decision", {"action": "execute"}),
        _event(
            "work_batch_started",
            {
                "work_epoch": 1,
                "command_ids": ["change"],
                "started_in_finalization": False,
            },
        ),
        _event("command_receipt", {"command_id": "change", "work_epoch": 1}),
    ]
    if include_recovery:
        events.extend(
            (
                _event(
                    "recovery_required",
                    {
                        "recovery_ordinal": 1,
                        "cycle": None,
                        "stagnant_batches": 3,
                        "work_epoch": 1,
                    },
                ),
                _event("replanned", {"recovery_count": 1}),
            )
        )
    events.extend(
        (
            _event(
                "finalization_started",
                {
                    "triggers": ["turn_budget"],
                    "turns_remaining": 3,
                    "turn_reserve": 3,
                    "wall_time_remaining_sec": 500.0,
                    "wall_time_reserve_sec": 180.0,
                    "completion_findings": [],
                },
            ),
            _event(
                "completion_review_started",
                {
                    "attempt_id": 1,
                    "review_ordinal": 1,
                    "work_epoch": 1,
                },
            ),
        )
    )
    if review_has_outcome:
        events.append(_event("completion_review", {"verdict": "accept"}))
    events.append(_event("run_finished", {"stop_reason": "verified"}))
    return events


def _create_v2_source_project(
    root: Path,
    *,
    include_recovery: bool = True,
    review_has_outcome: bool = True,
    options_match: bool = True,
) -> tuple[Path, Path, Path]:
    name = _development_task_name()
    matrix = {
        "schema_version": 1,
        "dataset": "terminal-bench@2.0",
        "selection_method": "test",
        "tasks": [{"name": name}],
    }
    matrix_path = root / "evaluation" / "matrix.json"
    matrix_path.parent.mkdir(parents=True)
    matrix_bytes = _dump(matrix)
    matrix_path.write_bytes(matrix_bytes)

    run_dir = Path("runs") / "terminal-bench-2" / "profiled"
    trial_dir = run_dir / name / f"{name}__trial"
    result_path = trial_dir / "result.json"
    config_path = trial_dir / "config.json"
    producer = {
        "commit": "a" * 40,
        "tree": "b" * 40,
        "source_sha256": "c" * 64,
    }
    agent_kwargs = {
        **dict(PREFIXBENCH_V1.controlled_agent_options),
        "prefixbench_profile": "prefixbench-v1",
        "producer_commit": producer["commit"],
        "producer_tree": producer["tree"],
        "producer_source_sha256": producer["source_sha256"],
    }
    if not options_match:
        agent_kwargs["max_turns"] = 41
    config: dict[str, Any] = {
        "task": {
            "path": name,
            "git_url": "https://example.test/terminal-bench.git",
            "git_commit_id": "d" * 40,
        },
        "agent": {
            "name": "evidence_harness.harbor_agent:EvidenceHarnessAgent",
            "kwargs": agent_kwargs,
        },
        "job_id": "test-job",
        "trial_name": f"{name}__trial",
    }
    result: dict[str, Any] = {
        "task_name": name,
        "task_checksum": hashlib.sha256(name.encode()).hexdigest(),
        "task_id": {
            "name": name,
            "git_url": config["task"]["git_url"],
            "git_commit_id": config["task"]["git_commit_id"],
        },
        "config": config,
        "verifier_result": {"rewards": {"reward": 1.0}},
        "exception_info": None,
        "agent_result": {"metadata": {"evidence_harness": {"stop_reason": "verified"}}},
    }
    config_bytes = _dump(config)
    result_bytes = _dump(result)
    (root / trial_dir).mkdir(parents=True)
    (root / config_path).write_bytes(config_bytes)
    (root / result_path).write_bytes(result_bytes)
    journal_path = trial_dir / "agent" / "evidence-harness" / "events.jsonl"
    journal_bytes = (
        "\n".join(
            _v2_events(
                include_recovery=include_recovery,
                review_has_outcome=review_has_outcome,
            )
        )
        + "\n"
    ).encode()
    (root / journal_path).parent.mkdir(parents=True)
    (root / journal_path).write_bytes(journal_bytes)

    canonical = {
        "schema_version": 2,
        "collection_profile": "prefixbench-v1",
        "dataset": matrix["dataset"],
        "matrix_sha256": hashlib.sha256(matrix_bytes).hexdigest(),
        "counts": {"completed": 1, "passed": 1, "failed": 0, "error": 0},
        "tasks": [
            {
                "index": 1,
                "name": name,
                "result_path": str(result_path),
                "result_sha256": hashlib.sha256(result_bytes).hexdigest(),
                "config_sha256": hashlib.sha256(config_bytes).hexdigest(),
                "run_dir": str(run_dir),
                "task_checksum": result["task_checksum"],
                "task_git_url": config["task"]["git_url"],
                "task_git_commit_id": config["task"]["git_commit_id"],
                "reward": 1.0,
                "status": "passed",
                "exception_type": None,
                "journal_path": str(journal_path),
                "journal_sha256": hashlib.sha256(journal_bytes).hexdigest(),
                "prefixbench_profile": "prefixbench-v1",
                "producer_commit": producer["commit"],
                "producer_tree": producer["tree"],
                "producer_source_sha256": producer["source_sha256"],
            }
        ],
    }
    canonical_path = root / "evaluation" / "canonical-v2.json"
    canonical_path.write_bytes(_dump(canonical))
    return canonical_path, matrix_path, root / journal_path


def test_legacy_inventory_is_not_admitted_or_used_as_phase_evidence(
    tmp_path: Path,
) -> None:
    canonical, matrix, _ = _create_source_project(tmp_path, include_replay=True)

    report = inspect_prefixbench(
        canonical,
        matrix,
        tmp_path,
        expected_task_count=2,
    )

    assert isinstance(report, PrefixBenchReadiness)
    assert report.status == "source_cohort_unavailable"
    assert report.summary.live == 1
    assert report.summary.replay == 1
    assert report.summary.admitted == 0
    assert report.summary.phase_events.model_dump() == {
        "thinking": 0,
        "executing": 0,
        "finalizing": 0,
        "reviewing": 0,
        "recovering": 0,
    }
    assert report.summary.legacy_events.model_dump() == {
        "finish_proposals": 1,
        "completion_reviews": 1,
        "completion_rejections": 1,
        "replans": 1,
    }
    assert report.tasks[0].exclusion_reasons == (
        PrefixBenchExclusion.JOURNAL_NOT_BOUND_BY_CANONICAL,
        PrefixBenchExclusion.UNSUPPORTED_JOURNAL_SCHEMA,
        PrefixBenchExclusion.PRODUCER_COMMIT_UNATTESTED,
        PrefixBenchExclusion.PREFIXBENCH_PROFILE_MISSING,
        PrefixBenchExclusion.PHASE_EVIDENCE_INCOMPLETE,
    )
    assert report.tasks[1].exclusion_reasons == (PrefixBenchExclusion.NON_LIVE_RESULT,)


def test_attested_schema2_task_requires_all_explicit_phase_witnesses(
    tmp_path: Path,
) -> None:
    canonical, matrix, _ = _create_source_project(tmp_path, ready=True)

    report = inspect_prefixbench(
        canonical,
        matrix,
        tmp_path,
        expected_task_count=1,
    )

    assert isinstance(report, PrefixBenchReadiness)
    assert report.status == "ready"
    assert report.summary.admitted == 1
    assert report.tasks[0].admitted
    assert report.tasks[0].exclusion_reasons == ()
    assert all(value == 1 for value in report.tasks[0].phase_events.model_dump().values())


def test_task_split_is_stable_and_outcome_blind() -> None:
    identity = {
        "dataset": "terminal-bench@2.0",
        "name": "adaptive-rejection-sampler",
        "task_checksum": "4" * 64,
        "task_git_url": "https://example.test/tasks.git",
        "task_git_commit_id": "6" * 40,
    }

    first = prefixbench_task_split(**identity)
    second = prefixbench_task_split(**identity)

    assert first == second
    assert first[0] is (PrefixBenchSplit.DEVELOPMENT if first[1] < 3 else PrefixBenchSplit.TEST)
    assert len(first[2]) == 64


def test_checker_rebuilds_all_sources_allows_none_and_rejects_partial(
    tmp_path: Path,
) -> None:
    canonical, matrix, raw_paths = _create_source_project(tmp_path)
    report = inspect_prefixbench(
        canonical,
        matrix,
        tmp_path,
        expected_task_count=1,
    )
    report_path = tmp_path / "evaluation" / "readiness.json"
    report_path.write_bytes(report.canonical_bytes())

    assert (
        check_prefixbench_readiness(
            readiness_path=report_path,
            canonical_path=canonical,
            matrix_path=matrix,
            project_root=tmp_path,
            expected_task_count=1,
        )
        == ()
    )

    raw_bytes = {path: path.read_bytes() for path in raw_paths}
    for path in raw_paths:
        path.unlink()
    assert (
        check_prefixbench_readiness(
            readiness_path=report_path,
            canonical_path=canonical,
            matrix_path=matrix,
            project_root=tmp_path,
            expected_task_count=1,
        )
        == ()
    )

    raw_paths[0].write_bytes(raw_bytes[raw_paths[0]])
    errors = check_prefixbench_readiness(
        readiness_path=report_path,
        canonical_path=canonical,
        matrix_path=matrix,
        project_root=tmp_path,
        expected_task_count=1,
    )
    assert errors == ("PrefixBench raw sources are partially available: 1/3 bound files exist",)


def test_builder_rejects_result_path_outside_run_store(tmp_path: Path) -> None:
    canonical, matrix, _ = _create_source_project(tmp_path)
    payload = json.loads(canonical.read_bytes())
    payload["tasks"][0]["result_path"] = "../result.json"
    canonical.write_bytes(_dump(payload))

    with pytest.raises(ValueError, match="outside the run store"):
        inspect_prefixbench(
            canonical,
            matrix,
            tmp_path,
            expected_task_count=1,
        )


def test_schema2_readiness_uses_aggregate_development_phase_coverage(
    tmp_path: Path,
) -> None:
    canonical, matrix, _journal = _create_v2_source_project(tmp_path)

    report = inspect_prefixbench(
        canonical,
        matrix,
        tmp_path,
        expected_task_count=1,
    )

    assert isinstance(report, PrefixBenchReadinessV2)
    assert report.status == "ready"
    assert report.summary.source_admitted == 1
    assert report.tasks[0].source_admission.admitted
    assert report.tasks[0].phase_errors == ()
    assert report.summary.phase_coverage.development.model_dump() == {
        "thinking": 1,
        "executing": 1,
        "finalizing": 1,
        "reviewing": 1,
        "recovering": 1,
    }
    assert report.summary.phase_coverage.test == report.coverage_policy.test


def test_schema2_readiness_distinguishes_missing_phase_from_source_exclusion(
    tmp_path: Path,
) -> None:
    canonical, matrix, _journal = _create_v2_source_project(
        tmp_path,
        include_recovery=False,
    )

    report = inspect_prefixbench(
        canonical,
        matrix,
        tmp_path,
        expected_task_count=1,
    )

    assert isinstance(report, PrefixBenchReadinessV2)
    assert report.status == "phase_coverage_incomplete"
    assert report.summary.source_admitted == 1
    assert report.tasks[0].phase_eligibility.recovering == ()


def test_schema2_readiness_rejects_presence_without_review_outcome(
    tmp_path: Path,
) -> None:
    canonical, matrix, journal = _create_v2_source_project(
        tmp_path,
        review_has_outcome=False,
    )

    report = inspect_prefixbench(
        canonical,
        matrix,
        tmp_path,
        expected_task_count=1,
    )
    parsed_events = [
        {"line": line, **json.loads(raw)}
        for line, raw in enumerate(journal.read_text(encoding="utf-8").splitlines(), start=1)
    ]
    eligibility, errors = validate_phase_trace(parsed_events)

    assert isinstance(report, PrefixBenchReadinessV2)
    assert report.status == "phase_coverage_incomplete"
    assert report.tasks[0].phase_eligibility.reviewing == ()
    assert any(
        "completion review has no outcome" in error for error in report.tasks[0].phase_errors
    )
    assert eligibility.reviewing == ()
    assert errors == report.tasks[0].phase_errors


def test_schema2_source_policy_mismatch_is_not_phase_failure(tmp_path: Path) -> None:
    canonical, matrix, _journal = _create_v2_source_project(
        tmp_path,
        options_match=False,
    )

    report = inspect_prefixbench(
        canonical,
        matrix,
        tmp_path,
        expected_task_count=1,
    )

    assert isinstance(report, PrefixBenchReadinessV2)
    assert report.status == "source_cohort_unavailable"
    assert report.summary.source_admitted == 0
    assert report.tasks[0].source_admission.exclusion_reasons == ("profile_options_mismatch",)
    assert report.tasks[0].phase_eligibility.model_dump() == {
        "thinking": (),
        "executing": (),
        "finalizing": (),
        "reviewing": (),
        "recovering": (),
    }


def test_schema2_readiness_checker_rebuilds_semantic_report(tmp_path: Path) -> None:
    canonical, matrix, _journal = _create_v2_source_project(tmp_path)
    report = inspect_prefixbench(
        canonical,
        matrix,
        tmp_path,
        expected_task_count=1,
    )
    report_path = tmp_path / "evaluation" / "readiness-v2.json"
    report_path.write_bytes(report.canonical_bytes())

    assert (
        check_prefixbench_readiness(
            readiness_path=report_path,
            canonical_path=canonical,
            matrix_path=matrix,
            project_root=tmp_path,
            expected_task_count=1,
        )
        == ()
    )


def test_real_canonical89_readiness_census_is_stable() -> None:
    first = inspect_prefixbench(
        PROJECT_ROOT / "evaluation" / "canonical-89.json",
        PROJECT_ROOT / "evaluation" / "matrix-89.json",
        PROJECT_ROOT,
    )
    second = inspect_prefixbench(
        PROJECT_ROOT / "evaluation" / "canonical-89.json",
        PROJECT_ROOT / "evaluation" / "matrix-89.json",
        PROJECT_ROOT,
    )

    assert isinstance(first, PrefixBenchReadiness)
    assert isinstance(second, PrefixBenchReadiness)
    assert first == second
    assert first.canonical_bytes() == second.canonical_bytes()
    assert PrefixBenchReadiness.model_validate_json(first.canonical_bytes()) == first
    assert first.summary.model_dump(mode="json") == {
        "tasks": 89,
        "live": 85,
        "replay": 4,
        "admitted": 0,
        "excluded": 89,
        "development": 28,
        "test": 61,
        "journal_schemas": {
            "schema_1": 85,
            "schema_2": 0,
            "other": 0,
            "missing": 4,
        },
        "phase_events": {
            "thinking": 0,
            "executing": 0,
            "finalizing": 0,
            "reviewing": 0,
            "recovering": 0,
        },
        "legacy_events": {
            "finish_proposals": 202,
            "completion_reviews": 132,
            "completion_rejections": 147,
            "replans": 56,
        },
        "exclusions": {
            "non_live_result": 4,
            "journal_not_bound_by_canonical": 85,
            "unsupported_journal_schema": 85,
            "producer_commit_unattested": 85,
            "prefixbench_profile_missing": 85,
            "phase_evidence_incomplete": 85,
        },
    }
