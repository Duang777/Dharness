from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from scripts.completion_calibration import (
    CohortExpectation,
    analyze_completion_corpus,
    build_completion_corpus,
    check_artifacts,
    dump_json,
)

TASK_URL = "https://example.com/terminal-bench-2.git"
TASK_COMMIT = "b" * 40
TASK_CHECKSUM = "c" * 64


def _write_json(path: Path, value: object) -> bytes:
    data = dump_json(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def _event(event_type: str, payload: dict[str, object], second: int) -> dict[str, object]:
    return {
        "timestamp": f"2026-09-27T00:00:{second:02d}+00:00",
        "type": event_type,
        "payload": payload,
    }


def _check() -> dict[str, Any]:
    return {
        "id": "behavior",
        "kind": "behavior",
        "script": "python3 check.py",
        "proves": "the requested behavior works",
        "cwd": "/app",
        "timeout_sec": 60,
    }


def _receipt(check: dict[str, Any]) -> dict[str, Any]:
    empty = {
        "head": "",
        "tail": "",
        "total_bytes": 0,
        "omitted_bytes": 0,
        "sha256": hashlib.sha256(b"").hexdigest(),
        "archive_path": None,
    }
    return {
        "sequence": 2,
        "command_id": check["id"],
        "script": check["script"],
        "purpose": check["proves"],
        "cwd": check["cwd"],
        "mode": "observe",
        "work_epoch": 1,
        "return_code": 0,
        "failure": None,
        "duration_sec": 0.1,
        "stdout": empty,
        "stderr": empty,
        "command_fingerprint": "d" * 64,
        "observation_fingerprint": "e" * 64,
    }


def _finish_payload(check: dict[str, Any]) -> dict[str, Any]:
    return {
        "action": "finish",
        "rationale": "work is complete",
        "plan": [],
        "commands": [],
        "checks": [check],
        "coverage": [{"requirement": "complete the task", "check_ids": [check["id"]]}],
        "summary": "done",
        "stop_category": None,
    }


def _legacy_false_positive_journal(
    *,
    receipt_first: bool = False,
    include_review: bool = True,
) -> list[dict[str, object]]:
    check = _check()
    receipt = _receipt(check)
    sensitive_output = "admin_pass = runtime-password\n"
    receipt["stdout"] = {
        **receipt["stdout"],
        "head": sensitive_output,
        "total_bytes": len(sensitive_output.encode()),
        "sha256": hashlib.sha256(sensitive_output.encode()).hexdigest(),
    }
    review = _event(
        "completion_review",
        {
            "verdict": "accept",
            "rationale": "covered",
            "missing_requirements": [],
            "suggested_checks": [],
        },
        3,
    )
    check_event = _event("command_receipt", receipt, 4)
    if not include_review:
        middle = [check_event]
    else:
        middle = [check_event, review] if receipt_first else [review, check_event]
    return [
        _event(
            "run_started",
            {"instruction": "do the task", "options": {"max_completion_reviews": 2}},
            0,
        ),
        _event("agent_decision", _finish_payload(check), 2),
        *middle,
        _event(
            "verification_receipt",
            {
                "work_epoch": 1,
                "checks": [receipt],
                "coverage": [{"requirement": "complete the task", "check_ids": ["behavior"]}],
                "semantic_assessment": None,
                "accepted": True,
                "rejection_reasons": [],
            },
            5,
        ),
        _event(
            "run_finished",
            {
                "stop_reason": "verified",
                "failure_category": None,
                "turns_used": 1,
                "environment_calls_used": 2,
                "repairs_used": 0,
                "recoveries_used": 0,
            },
            6,
        ),
    ]


def _legacy_false_negative_journal() -> list[dict[str, object]]:
    check = _check()
    return [
        _event(
            "run_started",
            {"instruction": "do the task", "options": {"max_completion_reviews": 2}},
            0,
        ),
        _event("agent_decision", _finish_payload(check), 2),
        _event(
            "completion_rejected",
            {
                "reasons": ["verification check 'behavior' appears to modify task state"],
                "repair_count": 1,
            },
            3,
        ),
        _event(
            "run_finished",
            {
                "stop_reason": "budget_exhausted",
                "failure_category": "harness_control",
                "turns_used": 1,
                "environment_calls_used": 1,
                "repairs_used": 1,
                "recoveries_used": 0,
            },
            4,
        ),
    ]


def _schema2_false_positive_journal() -> list[dict[str, object]]:
    started, decision, receipt, review, verification, finished = _legacy_false_positive_journal(
        receipt_first=True
    )
    started_payload = started["payload"]
    verification_payload = verification["payload"]
    assert isinstance(started_payload, dict)
    assert isinstance(verification_payload, dict)
    started_payload["journal_schema_version"] = 2
    verification_payload["isolation"] = {
        "attempt_id": 1,
        "work_epoch": 1,
    }
    return [
        started,
        decision,
        _event(
            "completion_isolation_started",
            {"attempt_id": 1, "work_epoch": 1},
            3,
        ),
        _event("completion_candidate_committed", {"attempt_id": 1}, 3),
        _event("completion_check_started", {"attempt_id": 1, "check_id": "behavior"}, 3),
        receipt,
        _event("completion_check_isolated", {"check_id": "behavior"}, 4),
        _event("completion_check_disposed", {"check_id": "behavior"}, 4),
        _event("completion_source_attested", {"attempt_id": 1}, 4),
        _event("completion_snapshot_disposed", {"attempt_id": 1}, 4),
        _event("completion_source_resumed", {"attempt_id": 1}, 4),
        review,
        verification,
        finished,
    ]


def _write_trial(
    root: Path,
    *,
    index: int,
    task_name: str,
    reward: float,
    stop_reason: str | None,
    events: list[dict[str, object]] | None,
    replay: bool = False,
) -> dict[str, Any]:
    trial_dir = root / "runs" / "terminal-bench-2" / task_name / f"{task_name}__trial"
    result_path = trial_dir / "result.json"
    config = {
        "job_id": "job-1",
        "trial_name": f"{task_name}__trial",
        "task": {
            "path": task_name,
            "git_url": TASK_URL,
            "git_commit_id": TASK_COMMIT,
        },
        "agent": {"name": "evidence-harness"},
    }
    config_bytes = _write_json(trial_dir / "config.json", config)
    metadata: dict[str, object]
    if replay:
        metadata = {"evidence_harness_replay": {"completed": True}}
    else:
        metadata = {
            "evidence_harness": {
                "phase": "terminated",
                "turns_used": 1,
                "environment_calls_used": 2,
                "repairs_used": 0,
                "recoveries_used": 0,
                "work_epoch": 1,
                "stop_reason": stop_reason,
                "failure_category": None,
                "latest_evidence_accepted": stop_reason == "verified",
            }
        }
    result = {
        "trial_name": f"{task_name}__trial",
        "task_name": task_name,
        "source": "terminal-bench",
        "task_checksum": TASK_CHECKSUM,
        "task_id": {
            "git_url": TASK_URL,
            "git_commit_id": TASK_COMMIT,
        },
        "config": config,
        "agent_result": {"metadata": metadata},
        "verifier_result": {"rewards": {"reward": reward}},
        "exception_info": None,
    }
    result_bytes = _write_json(result_path, result)
    if events is not None:
        journal = trial_dir / "agent" / "evidence-harness" / "events.jsonl"
        journal.parent.mkdir(parents=True, exist_ok=True)
        journal.write_text(
            "".join(json.dumps(event, sort_keys=True) + "\n" for event in events),
            encoding="utf-8",
        )
    return {
        "index": index,
        "name": task_name,
        "status": "passed" if reward == 1.0 else "failed",
        "reward": reward,
        "exception_type": None,
        "result_path": str(result_path.relative_to(root)),
        "result_sha256": hashlib.sha256(result_bytes).hexdigest(),
        "config_sha256": hashlib.sha256(config_bytes).hexdigest(),
        "task_checksum": TASK_CHECKSUM,
        "task_git_url": TASK_URL,
        "task_git_commit_id": TASK_COMMIT,
    }


def _write_fixture(
    root: Path,
    *,
    receipt_first: bool = False,
    include_review: bool = True,
    schema2_false_positive: bool = False,
) -> tuple[Path, Path]:
    rows = [
        _write_trial(
            root,
            index=1,
            task_name="false-positive",
            reward=0,
            stop_reason="verified",
            events=(
                _schema2_false_positive_journal()
                if schema2_false_positive
                else _legacy_false_positive_journal(
                    receipt_first=receipt_first,
                    include_review=include_review,
                )
            ),
        ),
        _write_trial(
            root,
            index=2,
            task_name="false-negative",
            reward=1,
            stop_reason="budget_exhausted",
            events=_legacy_false_negative_journal(),
        ),
        _write_trial(
            root,
            index=3,
            task_name="replay",
            reward=1,
            stop_reason=None,
            events=None,
            replay=True,
        ),
    ]
    matrix_path = root / "evaluation" / "matrix.json"
    matrix = {
        "schema_version": 1,
        "dataset": "terminal-bench@2.0",
        "tasks": [{"name": row["name"]} for row in rows],
    }
    matrix_bytes = _write_json(matrix_path, matrix)
    canonical_path = root / "evaluation" / "canonical.json"
    _write_json(
        canonical_path,
        {
            "schema_version": 1,
            "dataset": "terminal-bench@2.0",
            "matrix_sha256": hashlib.sha256(matrix_bytes).hexdigest(),
            "tasks": rows,
        },
    )
    return canonical_path, matrix_path


def _build_fixture(
    root: Path,
    *,
    receipt_first: bool = False,
    include_review: bool = True,
) -> dict[str, Any]:
    canonical_path, matrix_path = _write_fixture(
        root,
        receipt_first=receipt_first,
        include_review=include_review,
    )
    return build_completion_corpus(
        canonical_path,
        matrix_path,
        root,
        expected=CohortExpectation(
            total=3,
            live=2,
            replay=1,
            false_positive=1,
            false_negative=1,
        ),
    )


def test_builds_deterministic_source_bound_disagreement_corpus(tmp_path: Path) -> None:
    first = _build_fixture(tmp_path)
    second = _build_fixture(tmp_path)

    assert dump_json(first) == dump_json(second)
    assert first["counts"] == {
        "canonical": 3,
        "live": 2,
        "replay": 1,
        "cases": 2,
        "false_positive": 1,
        "false_negative": 1,
    }
    cases = first["cases"]
    assert [case["task_name"] for case in cases] == ["false-positive", "false-negative"]
    assert cases[0]["attempts"][0]["review"]["payload"]["verdict"] == "accept"
    assert cases[0]["attempts"][0]["check_receipts"][0]["payload"]["return_code"] == 0
    assert cases[0]["sources"]["journal"]["sha256"]
    encoded = dump_json(first).decode()
    assert "runtime-password" not in encoded
    assert "[OMITTED FROM COMMITTED CORPUS]" in encoded


def test_calibration_separates_proven_decisions_from_isolation_candidates(
    tmp_path: Path,
) -> None:
    report = analyze_completion_corpus(_build_fixture(tmp_path))

    policies = {policy["name"]: policy for policy in report["policies"]}
    assert policies["recorded_terminal_v1"]["decisions"]["conflicts_external"] == 2
    assert policies["same_attempt_review_required_v1"]["decisions"] == {
        "accept": 1,
        "reject": 1,
        "matches_external": 0,
        "conflicts_external": 2,
        "changed_from_recorded": 0,
    }
    assert report["proven_control_path_corrections"]["count"] == 0
    assert [candidate["task_name"] for candidate in report["isolation_experiment_candidates"]] == [
        "false-negative"
    ]
    assert report["coverage"] == {
        "proven_control_path_cases": 0,
        "counterfactual_isolation_cases": 1,
        "distinct_cases": 1,
        "known_disagreement_cases": 2,
    }
    assert report["claim_boundary"] == ("historical_evidence_only_no_benchmark_score_projection")


def test_calibration_rejects_unsanitized_corpus_output(tmp_path: Path) -> None:
    corpus = _build_fixture(tmp_path)
    corpus["cases"][0]["attempts"][0]["check_receipts"][0]["payload"]["stdout"]["head"] = (
        "unredacted output"
    )

    with pytest.raises(ValueError, match="completion corpus contains unsanitized values"):
        analyze_completion_corpus(corpus)


def test_review_required_policy_blocks_legacy_no_review_acceptance(
    tmp_path: Path,
) -> None:
    report = analyze_completion_corpus(_build_fixture(tmp_path, include_review=False))

    policy = next(
        item for item in report["policies"] if item["name"] == "same_attempt_review_required_v1"
    )
    assert policy["decisions"] == {
        "accept": 0,
        "reject": 2,
        "matches_external": 1,
        "conflicts_external": 1,
        "changed_from_recorded": 1,
    }
    assert report["proven_control_path_corrections"]["task_names"] == ["false-positive"]


def test_builder_rejects_result_source_drift(tmp_path: Path) -> None:
    canonical_path, matrix_path = _write_fixture(tmp_path)
    canonical = json.loads(canonical_path.read_text(encoding="utf-8"))
    result_path = tmp_path / canonical["tasks"][0]["result_path"]
    result_path.write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="canonical result hash does not match"):
        build_completion_corpus(
            canonical_path,
            matrix_path,
            tmp_path,
            expected=CohortExpectation(3, 2, 1, 1, 1),
        )


def test_legacy_decoder_rejects_receipt_first_event_order(tmp_path: Path) -> None:
    canonical_path, matrix_path = _write_fixture(tmp_path, receipt_first=True)

    with pytest.raises(ValueError, match="receipt-first events do not match"):
        build_completion_corpus(
            canonical_path,
            matrix_path,
            tmp_path,
            expected=CohortExpectation(3, 2, 1, 1, 1),
        )


def test_schema2_decoder_accepts_isolated_receipt_first_event_order(
    tmp_path: Path,
) -> None:
    canonical_path, matrix_path = _write_fixture(
        tmp_path,
        schema2_false_positive=True,
    )

    corpus = build_completion_corpus(
        canonical_path,
        matrix_path,
        tmp_path,
        expected=CohortExpectation(3, 2, 1, 1, 1),
    )

    assert corpus["source_dialect"] == "mixed_legacy_v1_and_isolated_v2"
    assert corpus["cases"][0]["attempts"][0]["outcome"] == "accepted"


def test_legacy_decoder_rejects_events_after_run_finished(tmp_path: Path) -> None:
    canonical_path, matrix_path = _write_fixture(tmp_path)
    canonical = json.loads(canonical_path.read_text(encoding="utf-8"))
    result_path = tmp_path / canonical["tasks"][0]["result_path"]
    journal_path = result_path.parent / "agent" / "evidence-harness" / "events.jsonl"
    events = _legacy_false_positive_journal()
    finished = events.pop()
    events.insert(2, finished)
    journal_path.write_text(
        "".join(json.dumps(event, sort_keys=True) + "\n" for event in events),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="run_finished is not the final journal event"):
        build_completion_corpus(
            canonical_path,
            matrix_path,
            tmp_path,
            expected=CohortExpectation(3, 2, 1, 1, 1),
        )


def test_check_artifacts_rebuilds_sources_and_detects_stale_report(
    tmp_path: Path,
) -> None:
    canonical_path, matrix_path = _write_fixture(tmp_path)
    expected = CohortExpectation(3, 2, 1, 1, 1)
    corpus = build_completion_corpus(
        canonical_path,
        matrix_path,
        tmp_path,
        expected=expected,
    )
    corpus_path = tmp_path / "evaluation" / "completion-disagreements.json"
    report_path = tmp_path / "evaluation" / "completion-calibration.json"
    corpus_path.write_bytes(dump_json(corpus))
    report_path.write_bytes(dump_json(analyze_completion_corpus(corpus)))

    assert (
        check_artifacts(
            canonical_path=canonical_path,
            matrix_path=matrix_path,
            project_root=tmp_path,
            corpus_path=corpus_path,
            report_path=report_path,
            expected=expected,
        )
        == []
    )

    report_path.write_text("{}\n", encoding="utf-8")
    assert check_artifacts(
        canonical_path=canonical_path,
        matrix_path=matrix_path,
        project_root=tmp_path,
        corpus_path=corpus_path,
        report_path=report_path,
        expected=expected,
    ) == ["completion calibration report is stale"]


def test_check_artifacts_binds_corpus_to_canonical_without_raw_runs(
    tmp_path: Path,
) -> None:
    canonical_path, matrix_path = _write_fixture(tmp_path)
    expected = CohortExpectation(3, 2, 1, 1, 1)
    corpus = build_completion_corpus(
        canonical_path,
        matrix_path,
        tmp_path,
        expected=expected,
    )
    corpus_path = tmp_path / "evaluation" / "completion-disagreements.json"
    report_path = tmp_path / "evaluation" / "completion-calibration.json"
    shutil.rmtree(tmp_path / "runs")

    corpus_path.write_bytes(dump_json(corpus))
    report_path.write_bytes(dump_json(analyze_completion_corpus(corpus)))
    assert (
        check_artifacts(
            canonical_path=canonical_path,
            matrix_path=matrix_path,
            project_root=tmp_path,
            corpus_path=corpus_path,
            report_path=report_path,
            expected=expected,
        )
        == []
    )

    corpus["cases"][0]["sources"]["config"]["sha256"] = "f" * 64
    corpus_path.write_bytes(dump_json(corpus))
    report_path.write_bytes(dump_json(analyze_completion_corpus(corpus)))

    assert check_artifacts(
        canonical_path=canonical_path,
        matrix_path=matrix_path,
        project_root=tmp_path,
        corpus_path=corpus_path,
        report_path=report_path,
        expected=expected,
    ) == [
        "completion corpus does not match canonical sources: "
        "config hash does not match canonical manifest: false-positive"
    ]
