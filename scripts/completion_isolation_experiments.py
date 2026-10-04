from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from evidence_harness.evidence import EvidenceGate
from evidence_harness.policy import command_fingerprint, observation_fingerprint
from evidence_harness.protocol import (
    ActionKind,
    AgentDecision,
    CommandMode,
    CommandReceipt,
    LoopOptions,
    ReviewDecision,
    VerificationReceipt,
)
from evidence_harness.replay_agent import RecordedCommand, load_recorded_replay_batches
from evidence_harness.source_binding import (
    archived_runtime_source_binding,
    runtime_source_binding,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS = PROJECT_ROOT / "evaluation" / "completion-disagreements.json"
DEFAULT_CALIBRATION = PROJECT_ROOT / "evaluation" / "completion-calibration.json"
DEFAULT_REPORT = PROJECT_ROOT / "evaluation" / "completion-isolation-experiments.json"
DEFAULT_MARKDOWN = PROJECT_ROOT / "docs" / "completion-isolation-experiments.md"
DEFAULT_TRIALS = PROJECT_ROOT / "evaluation" / "completion-isolation-trials"
FACTORY_SOURCE = PROJECT_ROOT / "src" / "evidence_harness" / "docker_completion_isolation.py"
AGENT_SOURCE = PROJECT_ROOT / "src" / "evidence_harness" / "isolation_experiment_agent.py"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build or check source-bound completion-isolation experiments."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--result", type=Path, action="append", required=True)
    build.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    build.add_argument("--calibration", type=Path, default=DEFAULT_CALIBRATION)
    build.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    build.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    build.add_argument("--snapshot-dir", type=Path, default=DEFAULT_TRIALS)

    check = subparsers.add_parser("check")
    check.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    check.add_argument("--calibration", type=Path, default=DEFAULT_CALIBRATION)
    check.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    check.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
    return parser.parse_args()


def build_experiment_report(
    *,
    corpus_path: Path,
    calibration_path: Path,
    result_paths: list[Path],
    project_root: Path = PROJECT_ROOT,
    factory_source_path: Path = FACTORY_SOURCE,
    agent_source_path: Path = AGENT_SOURCE,
    source_revision: str | None = None,
) -> dict[str, Any]:
    corpus_bytes, corpus = _read_json_object(corpus_path)
    calibration_bytes, calibration = _read_json_object(calibration_path)
    runtime_binding = (
        archived_runtime_source_binding(project_root, source_revision)
        if source_revision is not None
        else runtime_source_binding(project_root)
    )
    factory_binding = _source_file_binding(
        factory_source_path,
        project_root,
        revision=source_revision,
    )
    agent_binding = _source_file_binding(
        agent_source_path,
        project_root,
        revision=source_revision,
    )
    candidates = calibration.get("isolation_experiment_candidates")
    if not isinstance(candidates, list) or not candidates:
        raise ValueError("calibration has no isolation experiment candidates")
    expected = {
        _required_string(_object(value, "calibration candidate"), "task_name"): _object(
            value,
            "calibration candidate",
        )
        for value in candidates
    }
    if len(expected) != len(candidates):
        raise ValueError("calibration contains duplicate experiment task names")

    results: dict[str, tuple[Path, dict[str, Any]]] = {}
    for path in result_paths:
        _, result = _read_json_object(path)
        task_name = _required_string(result, "task_name")
        if task_name in results:
            raise ValueError(f"multiple experiment results for {task_name}")
        results[task_name] = (path, result)
    missing = sorted(set(expected) - set(results))
    unexpected = sorted(set(results) - set(expected))
    if missing:
        raise ValueError(f"missing experiment results: {', '.join(missing)}")
    if unexpected:
        raise ValueError(f"unexpected experiment results: {', '.join(unexpected)}")

    corpus_cases = corpus.get("cases")
    if not isinstance(corpus_cases, list):
        raise ValueError("completion corpus cases must be an array")
    cases_by_id = {
        _required_string(_object(value, "completion case"), "case_id"): _object(
            value,
            "completion case",
        )
        for value in corpus_cases
    }
    rows = [
        _build_experiment_row(
            task_name=task_name,
            candidate=expected[task_name],
            case=cases_by_id[_required_string(expected[task_name], "case_id")],
            result_path=results[task_name][0],
            result=results[task_name][1],
            project_root=project_root,
            factory_source_sha256=_required_string(factory_binding, "sha256"),
            agent_source_sha256=_required_string(agent_binding, "sha256"),
            runtime_source_sha256=runtime_binding["sha256"],
        )
        for task_name in expected
    ]
    return {
        "schema_version": 1,
        "scope": {
            "kind": "prospective_source_bound_replay",
            "benchmark_score_updated": False,
            "statement": (
                "These experiments replay frozen historical candidates and test the "
                "new isolation path. They are not fresh full-agent benchmark trials."
            ),
        },
        "sources": {
            "corpus": _file_binding(corpus_path, project_root, corpus_bytes),
            "calibration": _file_binding(
                calibration_path,
                project_root,
                calibration_bytes,
            ),
            "docker_isolation": factory_binding,
            "experiment_agent": agent_binding,
            "runtime": runtime_binding,
        },
        "summary": {
            "experiments": len(rows),
            "command_trajectories_replayed": sum(
                row["command_trajectory_replayed"] for row in rows
            ),
            "mechanical_isolation_passed": sum(row["mechanical_isolation_passed"] for row in rows),
            "semantic_review_accepted": sum(
                row["semantic_review_status"] == "accepted" for row in rows
            ),
            "semantic_review_unavailable": sum(
                row["semantic_review_status"] == "unavailable" for row in rows
            ),
            "external_verifier_passed": sum(row["external_reward"] == 1.0 for row in rows),
            "internally_verified": sum(row["internal_stop_reason"] == "verified" for row in rows),
        },
        "experiments": rows,
    }


def freeze_experiment_inputs(
    *,
    corpus_path: Path,
    result_paths: list[Path],
    snapshot_dir: Path,
    project_root: Path = PROJECT_ROOT,
) -> list[Path]:
    _, corpus = _read_json_object(corpus_path)
    cases = corpus.get("cases")
    if not isinstance(cases, list):
        raise ValueError("completion corpus cases must be an array")
    cases_by_task = {
        _required_string(_object(value, "completion case"), "task_name"): _object(
            value,
            "completion case",
        )
        for value in cases
    }
    frozen_results: list[Path] = []
    seen: set[str] = set()
    for result_path in result_paths:
        _, result = _read_json_object(result_path)
        task_name = _required_string(result, "task_name")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", task_name):
            raise ValueError(f"unsafe experiment task name: {task_name}")
        if task_name in seen:
            raise ValueError(f"multiple experiment results for {task_name}")
        seen.add(task_name)
        case = cases_by_task.get(task_name)
        if case is None:
            raise ValueError(f"experiment task is absent from completion corpus: {task_name}")

        trial_dir = result_path.parent
        source_journal = _source_journal_path(
            case=case,
            result_path=result_path,
            project_root=project_root,
        )
        destination = snapshot_dir / task_name
        files = (
            (result_path, destination / "result.json"),
            (
                trial_dir / "agent" / "completion-isolation-experiment" / "replay" / "events.jsonl",
                destination
                / "agent"
                / "completion-isolation-experiment"
                / "replay"
                / "events.jsonl",
            ),
            (
                trial_dir
                / "agent"
                / "completion-isolation-experiment"
                / "completion"
                / "events.jsonl",
                destination
                / "agent"
                / "completion-isolation-experiment"
                / "completion"
                / "events.jsonl",
            ),
            (source_journal, destination / "source-events.jsonl"),
        )
        for source, target in files:
            _write_atomic(target, source.read_bytes())
        frozen_results.append(destination / "result.json")
    return frozen_results


def _build_experiment_row(
    *,
    task_name: str,
    candidate: dict[str, Any],
    case: dict[str, Any],
    result_path: Path,
    result: dict[str, Any],
    project_root: Path,
    factory_source_sha256: str,
    agent_source_sha256: str,
    runtime_source_sha256: str,
) -> dict[str, Any]:
    case_id = _required_string(candidate, "case_id")
    if case.get("task_name") != task_name or case.get("case_id") != case_id:
        raise ValueError(f"calibration candidate does not match corpus case: {task_name}")
    metadata = _object(
        _object(result.get("agent_result"), "agent result")
        .get("metadata", {})
        .get("completion_isolation_experiment"),
        "completion isolation experiment metadata",
    )
    expected_ordinal = _required_int(candidate, "attempt_ordinal")
    expected_journal_sha256 = _required_string(
        _object(_object(case.get("sources"), "case sources").get("journal"), "journal"),
        "sha256",
    )
    source_journal = _source_journal_path(
        case=case,
        result_path=result_path,
        project_root=project_root,
    )
    source_journal_bytes = source_journal.read_bytes()
    if hashlib.sha256(source_journal_bytes).hexdigest() != expected_journal_sha256:
        raise ValueError(f"source journal hash does not match: {task_name}")
    expected_replay = load_recorded_replay_batches(source_journal_bytes)
    expected_attempt = _completion_attempt(case, expected_ordinal)
    expected_proposal_sha256 = _proposal_sha256(expected_attempt)
    expected_decision = AgentDecision.model_validate(
        {
            "action": ActionKind.FINISH,
            **_object(expected_attempt.get("proposal"), "completion proposal"),
        }
    )
    expected_decision = _validate_source_proposal(
        source_journal_bytes=source_journal_bytes,
        attempt=expected_attempt,
        expected_decision=expected_decision,
        task_name=task_name,
    )
    expected_metadata = {
        "case_id": case_id,
        "task_name": task_name,
        "attempt_ordinal": expected_ordinal,
        "source_journal_sha256": expected_journal_sha256,
        "proposal_event_line_sha256": expected_proposal_sha256,
        "experiment_agent_source_sha256": agent_source_sha256,
        "docker_isolation_source_sha256": factory_source_sha256,
        "runtime_source_sha256": runtime_source_sha256,
    }
    for key, expected_value in expected_metadata.items():
        if metadata.get(key) != expected_value:
            raise ValueError(f"experiment metadata {key} does not match: {task_name}")

    task_id = _object(result.get("task_id"), "task id")
    case_sources = _object(case.get("sources"), "case sources")
    for result_key, source_key in (
        ("git_url", "task_git_url"),
        ("git_commit_id", "task_git_commit_id"),
    ):
        if task_id.get(result_key) != case_sources.get(source_key):
            raise ValueError(f"experiment task source {result_key} does not match: {task_name}")
    if result.get("task_checksum") != case_sources.get("task_checksum"):
        raise ValueError(f"experiment task checksum does not match: {task_name}")

    evidence = VerificationReceipt.model_validate(metadata.get("latest_evidence"))
    expected_check_ids = tuple(str(value) for value in candidate.get("check_ids", []))
    proposal_check_ids = tuple(check.id for check in expected_decision.checks)
    if expected_check_ids != proposal_check_ids:
        raise ValueError(f"calibration check ids do not match frozen proposal: {task_name}")
    _validate_evidence_matches_proposal(
        evidence=evidence,
        decision=expected_decision,
        task_name=task_name,
    )
    mechanical = EvidenceGate(LoopOptions(enable_completion_review=False)).decide(
        work_epoch=evidence.work_epoch,
        checks=evidence.checks,
        coverage=evidence.coverage,
        expected_check_ids=expected_check_ids,
        attempt_id=evidence.isolation.attempt_id if evidence.isolation else None,
        isolation=evidence.isolation,
        require_isolation=True,
    )
    semantic_status = _semantic_review_status(evidence, metadata)
    trial_dir = result_path.parent
    replay_journal = (
        trial_dir / "agent" / "completion-isolation-experiment" / "replay" / "events.jsonl"
    )
    completion_journal = (
        trial_dir / "agent" / "completion-isolation-experiment" / "completion" / "events.jsonl"
    )
    _validate_experiment_journals(
        replay_journal,
        completion_journal,
        metadata=metadata,
        task_name=task_name,
        expected_source_sha256=expected_journal_sha256,
        expected_replay=expected_replay,
        expected_decision=expected_decision,
        evidence=evidence,
    )
    verifier_result = _object(result.get("verifier_result"), "verifier result")
    rewards = _object(verifier_result.get("rewards"), "verifier rewards")
    isolation = evidence.isolation
    if isolation is None:
        raise ValueError(f"experiment has no isolation evidence: {task_name}")
    replay_command_count = _required_int(metadata, "replay_command_count")
    return {
        "task_name": task_name,
        "case_id": case_id,
        "attempt_ordinal": expected_ordinal,
        "historical_source": {
            "journal_sha256": expected_journal_sha256,
            "proposal_event_line_sha256": expected_proposal_sha256,
            "result_sha256": _required_string(
                _object(case_sources.get("result"), "historical result"),
                "sha256",
            ),
            "task_checksum": _required_string(case_sources, "task_checksum"),
            "task_git_commit_id": _required_string(
                case_sources,
                "task_git_commit_id",
            ),
        },
        "prospective_sources": {
            "result": _file_binding(result_path, project_root),
            "replay_journal": _file_binding(replay_journal, project_root),
            "completion_journal": _file_binding(completion_journal, project_root),
            "source_journal": _file_binding(source_journal, project_root),
        },
        "model_name": _required_string(
            _object(_object(result.get("config"), "trial config").get("agent"), "agent config"),
            "model_name",
        ),
        "command_trajectory_replayed": replay_command_count
        == len([step for batch in expected_replay for step in batch]),
        "replay_command_count": replay_command_count,
        "replay_last_sequence": _required_int(metadata, "replay_last_sequence"),
        "replay_last_work_epoch": _required_int(metadata, "replay_last_work_epoch"),
        "mechanical_isolation_passed": mechanical.accepted,
        "mechanical_rejection_reasons": list(mechanical.rejection_reasons),
        "semantic_review_status": semantic_status,
        "internal_stop_reason": _required_string(metadata, "stop_reason"),
        "internal_failure_category": metadata.get("failure_category"),
        "external_reward": rewards.get("reward"),
        "checks": [
            {
                "id": receipt.command_id,
                "return_code": receipt.return_code,
                "failure": receipt.failure,
                "command_sha256": receipt.command_fingerprint,
                "observation_sha256": receipt.observation_fingerprint,
                "delta": isolation.checks[index].delta.model_dump(mode="json"),
                "child_disposed": isolation.checks[index].disposed,
            }
            for index, receipt in enumerate(evidence.checks)
        ],
        "source_attestation": isolation.source.model_dump(mode="json"),
        "snapshot_image_disposed": isolation.snapshot_image_disposed,
        "isolation_cost": isolation.cost.model_dump(mode="json"),
    }


def _semantic_review_status(
    evidence: VerificationReceipt,
    metadata: dict[str, Any],
) -> str:
    if evidence.semantic_assessment is not None:
        return "accepted" if evidence.semantic_assessment.accepted else "rejected"
    if metadata.get("failure_category") in {
        "completion_review_protocol",
        "completion_review_service",
    }:
        return "unavailable"
    return "missing"


def _validate_experiment_journals(
    replay_path: Path,
    completion_path: Path,
    *,
    metadata: dict[str, Any],
    task_name: str,
    expected_source_sha256: str,
    expected_replay: tuple[tuple[RecordedCommand, ...], ...],
    expected_decision: AgentDecision,
    evidence: VerificationReceipt,
) -> None:
    replay_events = _read_json_lines(replay_path)
    completion_events = _read_json_lines(completion_path)
    expected_steps = [step for batch in expected_replay for step in batch]
    run_started = [
        (index, event)
        for index, event in enumerate(completion_events)
        if event.get("type") == "run_started"
    ]
    if (
        len(run_started) != 1
        or _object(
            run_started[0][1].get("payload"),
            "run start",
        ).get("journal_schema_version")
        != 2
    ):
        raise ValueError(f"experiment has no unique schema-2 run start: {task_name}")

    replay_started = [event for event in replay_events if event.get("type") == "replay_started"]
    if len(replay_started) != 1:
        raise ValueError(f"experiment replay did not start exactly once: {task_name}")
    started_payload = _object(replay_started[0].get("payload"), "replay start")
    if started_payload.get(
        "source_journal_sha256"
    ) != expected_source_sha256 or started_payload.get("command_count") != len(expected_steps):
        raise ValueError(f"experiment replay source binding does not match: {task_name}")

    replay_receipts = [
        CommandReceipt.model_validate(event.get("payload"))
        for event in replay_events
        if event.get("type") == "command_receipt"
    ]
    if len(replay_receipts) != len(expected_steps):
        raise ValueError(f"experiment replay command count does not match: {task_name}")
    for expected, actual in zip(expected_steps, replay_receipts, strict=True):
        command = expected.command
        expected_fields = (
            expected.sequence,
            expected.work_epoch,
            command.id,
            command.script,
            command.purpose,
            command.cwd,
            command.mode,
            expected.expected_return_code,
            expected.expected_failure,
        )
        actual_fields = (
            actual.sequence,
            actual.work_epoch,
            actual.command_id,
            actual.script,
            actual.purpose,
            actual.cwd,
            actual.mode,
            actual.return_code,
            actual.failure,
        )
        if actual_fields != expected_fields:
            raise ValueError(
                f"experiment replay diverged at command {expected.sequence}: {task_name}"
            )
        if not _receipt_fingerprints_match(actual):
            raise ValueError(f"experiment replay receipt fingerprint does not match: {task_name}")

    replay_finished = [event for event in replay_events if event.get("type") == "replay_finished"]
    if len(replay_finished) != 1:
        raise ValueError(f"experiment replay did not finish exactly once: {task_name}")
    if _object(replay_finished[0].get("payload"), "replay finish").get(
        "receipt_count"
    ) != metadata.get("replay_command_count"):
        raise ValueError(f"experiment replay receipt count does not match: {task_name}")
    if expected_steps and (
        metadata.get("replay_last_sequence") != expected_steps[-1].sequence
        or metadata.get("replay_last_work_epoch") != expected_steps[-1].work_epoch
    ):
        raise ValueError(f"experiment replay endpoint does not match: {task_name}")

    decision_events = [
        (index, event)
        for index, event in enumerate(completion_events)
        if event.get("type") == "agent_decision"
    ]
    if (
        len(decision_events) != 1
        or AgentDecision.model_validate(decision_events[0][1].get("payload")) != expected_decision
    ):
        raise ValueError(f"completion decision does not match frozen proposal: {task_name}")

    completion_receipt_events = [
        (index, event)
        for index, event in enumerate(completion_events)
        if event.get("type") == "command_receipt"
    ]
    completion_receipts = [
        CommandReceipt.model_validate(event.get("payload"))
        for _, event in completion_receipt_events
    ]
    if (
        len(completion_receipts) != len(evidence.checks) + 1
        or completion_receipts[0].sequence != 1
        or completion_receipts[0].command_id != "bootstrap-environment"
        or completion_receipts[0].work_epoch != 0
        or tuple(completion_receipts[1:]) != evidence.checks
    ):
        raise ValueError(f"completion command receipts do not match result: {task_name}")
    if any(not _receipt_fingerprints_match(receipt) for receipt in completion_receipts):
        raise ValueError(f"completion receipt fingerprint does not match: {task_name}")

    verification_events = [
        (index, event)
        for index, event in enumerate(completion_events)
        if event.get("type") == "verification_receipt"
    ]
    if len(verification_events) != 1:
        raise ValueError(f"experiment has no unique verification receipt: {task_name}")
    recorded_evidence = VerificationReceipt.model_validate(verification_events[0][1].get("payload"))
    if recorded_evidence != evidence:
        raise ValueError(f"experiment verification receipt does not match result: {task_name}")

    finished_events = [
        (index, event)
        for index, event in enumerate(completion_events)
        if event.get("type") == "run_finished"
    ]
    if len(finished_events) != 1 or finished_events[0][0] != len(completion_events) - 1:
        raise ValueError(f"experiment has no unique final run_finished event: {task_name}")
    finished = _object(finished_events[0][1].get("payload"), "run finish")
    if (
        finished.get("stop_reason") != metadata.get("stop_reason")
        or finished.get("failure_category") != metadata.get("failure_category")
        or finished.get("turns_used") != 1
        or finished.get("environment_calls_used") != len(completion_receipts)
    ):
        raise ValueError(f"experiment run outcome does not match result: {task_name}")

    if not (
        run_started[0][0]
        < completion_receipt_events[0][0]
        < decision_events[0][0]
        < completion_receipt_events[1][0]
        <= completion_receipt_events[-1][0]
        < verification_events[0][0]
        < finished_events[0][0]
    ):
        raise ValueError(f"experiment completion event order does not match: {task_name}")

    _validate_semantic_events(
        completion_events,
        metadata=metadata,
        evidence=evidence,
        last_receipt_index=completion_receipt_events[-1][0],
        verification_index=verification_events[0][0],
        finished_index=finished_events[0][0],
        task_name=task_name,
    )


def _validate_semantic_events(
    completion_events: list[dict[str, Any]],
    *,
    metadata: dict[str, Any],
    evidence: VerificationReceipt,
    last_receipt_index: int,
    verification_index: int,
    finished_index: int,
    task_name: str,
) -> None:
    reviews = [
        (index, event)
        for index, event in enumerate(completion_events)
        if event.get("type") == "completion_review"
    ]
    review_errors = [
        (index, event)
        for index, event in enumerate(completion_events)
        if event.get("type") == "completion_review_error"
    ]
    rejections = [
        (index, event)
        for index, event in enumerate(completion_events)
        if event.get("type") == "completion_rejected"
    ]
    failure_category = metadata.get("failure_category")
    if failure_category in {
        "completion_review_protocol",
        "completion_review_service",
    }:
        expected_error_types = (
            {"ModelProtocolError"}
            if failure_category == "completion_review_protocol"
            else {"ModelServiceError", "TimeoutError"}
        )
        if (
            reviews
            or len(review_errors) != 1
            or _object(review_errors[0][1].get("payload"), "completion review error").get(
                "error_type"
            )
            not in expected_error_types
            or not (last_receipt_index < review_errors[0][0] < verification_index)
        ):
            raise ValueError(f"completion review failure does not match result: {task_name}")
        return

    if evidence.semantic_assessment is None:
        if reviews or review_errors or len(rejections) != 1:
            raise ValueError(f"mechanical rejection events do not match result: {task_name}")
    else:
        if (
            len(reviews) != 1
            or review_errors
            or not (last_receipt_index < reviews[0][0] < verification_index)
        ):
            raise ValueError(f"completion review events do not match result: {task_name}")
        review = ReviewDecision.model_validate(reviews[0][1].get("payload"))
        findings = tuple(dict.fromkeys((*review.missing_requirements, *review.suggested_checks)))
        if review.verdict == "repair" and not findings:
            findings = (review.rationale,)
        assessment = evidence.semantic_assessment
        if (
            assessment.accepted != (review.verdict == "accept")
            or assessment.rationale != review.rationale
            or assessment.findings != findings
        ):
            raise ValueError(f"completion review does not match evidence: {task_name}")
        expected_rejections = 0 if assessment.accepted else 1
        if len(rejections) != expected_rejections:
            raise ValueError(f"completion rejection events do not match evidence: {task_name}")

    if any(not (verification_index < index < finished_index) for index, _ in rejections):
        raise ValueError(f"completion rejection event order does not match: {task_name}")


def _receipt_fingerprints_match(receipt: CommandReceipt) -> bool:
    expected_command_sha256 = command_fingerprint(receipt.script, receipt.cwd)
    expected_observation_sha256 = observation_fingerprint(
        expected_command_sha256,
        receipt.return_code,
        receipt.failure.value if receipt.failure is not None else None,
        receipt.stdout.sha256,
        receipt.stderr.sha256,
    )
    return (
        receipt.command_fingerprint == expected_command_sha256
        and receipt.observation_fingerprint == expected_observation_sha256
    )


def _validate_evidence_matches_proposal(
    *,
    evidence: VerificationReceipt,
    decision: AgentDecision,
    task_name: str,
) -> None:
    if evidence.coverage != decision.coverage:
        raise ValueError(f"completion coverage does not match frozen proposal: {task_name}")
    if len(evidence.checks) > len(decision.checks):
        raise ValueError(f"completion receipts exceed frozen proposal: {task_name}")
    for expected, actual in zip(decision.checks, evidence.checks, strict=False):
        expected_fields = (
            expected.id,
            expected.script,
            expected.proves,
            expected.cwd,
            CommandMode.OBSERVE,
        )
        actual_fields = (
            actual.command_id,
            actual.script,
            actual.purpose,
            actual.cwd,
            actual.mode,
        )
        if actual_fields != expected_fields:
            raise ValueError(f"completion receipt does not match frozen proposal: {task_name}")


def _source_journal_path(
    *,
    case: dict[str, Any],
    result_path: Path,
    project_root: Path,
) -> Path:
    frozen = result_path.parent / "source-events.jsonl"
    if frozen.is_file():
        return frozen
    source = _object(case.get("sources"), "completion case sources")
    journal = _object(source.get("journal"), "completion case journal source")
    relative = Path(_required_string(journal, "path"))
    if relative.is_absolute():
        raise ValueError("completion source journal path is absolute")
    resolved = (project_root / relative).resolve()
    try:
        resolved.relative_to(project_root.resolve())
    except ValueError as exc:
        raise ValueError("completion source journal path escapes the project") from exc
    return resolved


def render_markdown(report: dict[str, Any]) -> str:
    scope = _object(report.get("scope"), "scope")
    summary = _object(report.get("summary"), "summary")
    experiments = report.get("experiments")
    if not isinstance(experiments, list):
        raise ValueError("experiment rows must be an array")
    lines = [
        "# Completion isolation experiments",
        "",
        str(scope["statement"]),
        "",
        "The canonical 59/89 result is unchanged.",
        "",
        "## Summary",
        "",
        (
            "- Command trajectories replayed: "
            f"{summary['command_trajectories_replayed']} / {summary['experiments']}"
        ),
        (
            "- Mechanical isolation passed: "
            f"{summary['mechanical_isolation_passed']} / {summary['experiments']}"
        ),
        (
            "- Receipt-first semantic review accepted: "
            f"{summary['semantic_review_accepted']} / {summary['experiments']}"
        ),
        (
            "- Receipt-first semantic review unavailable: "
            f"{summary['semantic_review_unavailable']} / {summary['experiments']}"
        ),
        (
            "- Official verifier reward 1.0: "
            f"{summary['external_verifier_passed']} / {summary['experiments']}"
        ),
        (f"- Internally verified: {summary['internally_verified']} / {summary['experiments']}"),
        "",
        "## Results",
        "",
        (
            "| Task | Replay commands | Mechanical isolation | Semantic review | "
            "Internal outcome | External reward |"
        ),
        "|---|---:|---|---|---|---:|",
    ]
    for value in experiments:
        row = _object(value, "experiment row")
        mechanical = "passed" if row["mechanical_isolation_passed"] else "failed"
        internal_outcome = str(row["internal_stop_reason"])
        if row["internal_failure_category"] is not None:
            internal_outcome += f" / {row['internal_failure_category']}"
        lines.append(
            f"| `{row['task_name']}` | {row['replay_command_count']} | {mechanical} | "
            f"{row['semantic_review_status']} | {internal_outcome} | "
            f"{row['external_reward']} |"
        )
    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "A mechanical pass means every frozen check ran in its own mount-free,",
            "network-disabled child; each check passed; the live source diff stayed",
            "unchanged while paused; and all owned Docker resources were removed.",
            "",
            "A reviewer result is independent. Missing model credentials produce",
            "`unavailable`, which cannot become verified completion even when the",
            "mechanical checks and the official verifier pass.",
            "",
            (
                f"Internally verified: {summary['internally_verified']} / "
                f"{summary['experiments']}. Mechanical isolation passed "
                f"{summary['mechanical_isolation_passed']} / {summary['experiments']}, "
                "and the official verifier awarded reward 1.0 to "
                f"{summary['external_verifier_passed']} / {summary['experiments']}."
            ),
            "",
            "A frozen check failure stops the attempt before semantic review. These",
            "results validate the isolation lifecycle and expose an internal/external",
            "disagreement; they do not establish benchmark score improvement.",
            "",
            "## Source bindings",
            "",
        ]
    )
    sources = _object(report.get("sources"), "report sources")
    for name in sorted(sources):
        value = sources[name]
        binding = _object(value, f"{name} binding")
        lines.append(f"- `{binding['path']}`: `{binding['sha256']}`")
    lines.append("")
    return "\n".join(lines)


def check_artifacts(
    *,
    corpus_path: Path,
    calibration_path: Path,
    report_path: Path,
    markdown_path: Path,
    project_root: Path = PROJECT_ROOT,
    factory_source_path: Path = FACTORY_SOURCE,
    agent_source_path: Path = AGENT_SOURCE,
    source_revision: str | None = None,
) -> list[str]:
    errors: list[str] = []
    try:
        _, report = _read_json_object(report_path)
        _validate_source_bindings(
            report,
            corpus_path=corpus_path,
            calibration_path=calibration_path,
            project_root=project_root,
            factory_source_path=factory_source_path,
            agent_source_path=agent_source_path,
            source_revision=source_revision,
        )
        expected_markdown = render_markdown(report)
        if markdown_path.read_text(encoding="utf-8") != expected_markdown:
            errors.append("completion isolation experiment markdown is stale")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [f"invalid completion isolation experiment artifacts: {exc}"]

    source_paths = [
        project_root
        / _required_string(
            _object(
                _object(
                    _object(row, "experiment").get("prospective_sources"),
                    "sources",
                ).get("result"),
                "result binding",
            ),
            "path",
        )
        for row in report.get("experiments", [])
    ]
    existing = sum(path.is_file() for path in source_paths)
    if existing not in {0, len(source_paths)}:
        errors.append("only part of the raw completion isolation experiment set is available")
    elif existing == len(source_paths):
        try:
            rebuilt = build_experiment_report(
                corpus_path=corpus_path,
                calibration_path=calibration_path,
                result_paths=source_paths,
                project_root=project_root,
                factory_source_path=factory_source_path,
                agent_source_path=agent_source_path,
                source_revision=source_revision,
            )
            if "source_revision" in report:
                rebuilt["source_revision"] = source_revision
            if _dump_json(rebuilt) != _dump_json(report):
                errors.append("completion isolation experiment report is stale")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"cannot rebuild completion isolation experiments: {exc}")
    return errors


def _validate_source_bindings(
    report: dict[str, Any],
    *,
    corpus_path: Path,
    calibration_path: Path,
    project_root: Path,
    factory_source_path: Path,
    agent_source_path: Path,
    source_revision: str | None,
) -> None:
    if report.get("schema_version") != 1:
        raise ValueError("unsupported completion isolation experiment schema")
    sources = _object(report.get("sources"), "report sources")
    expected = {
        "corpus": corpus_path,
        "calibration": calibration_path,
        "docker_isolation": factory_source_path,
        "experiment_agent": agent_source_path,
        "runtime": None,
    }
    for key, path in expected.items():
        binding = _object(sources.get(key), f"{key} binding")
        actual: object
        if path is None:
            actual = (
                archived_runtime_source_binding(project_root, source_revision)
                if source_revision is not None
                else runtime_source_binding(project_root)
            )
        elif key in {"docker_isolation", "experiment_agent"}:
            actual = _source_file_binding(path, project_root, revision=source_revision)
        else:
            actual = _file_binding(path, project_root)
        if binding != actual:
            raise ValueError(f"{key} source binding does not match")


def _completion_attempt(case: dict[str, Any], ordinal: int) -> dict[str, Any]:
    attempts = case.get("attempts")
    if not isinstance(attempts, list):
        raise ValueError("completion case attempts must be an array")
    matching = [
        _object(value, "completion attempt")
        for value in attempts
        if isinstance(value, dict) and value.get("ordinal") == ordinal
    ]
    if len(matching) != 1:
        raise ValueError(f"expected one completion attempt {ordinal}")
    return matching[0]


def _proposal_sha256(attempt: dict[str, Any]) -> str:
    proposal_event = _object(attempt.get("proposal_event"), "proposal event")
    return _required_string(proposal_event, "line_sha256")


def _validate_source_proposal(
    *,
    source_journal_bytes: bytes,
    attempt: dict[str, Any],
    expected_decision: AgentDecision,
    task_name: str,
) -> AgentDecision:
    proposal_event = _object(attempt.get("proposal_event"), "proposal event")
    line_number = _required_int(proposal_event, "line")
    lines = source_journal_bytes.splitlines(keepends=True)
    if line_number < 1 or line_number > len(lines):
        raise ValueError(f"frozen proposal line is outside source journal: {task_name}")
    raw_line = lines[line_number - 1]
    if hashlib.sha256(raw_line).hexdigest() != _proposal_sha256(attempt):
        raise ValueError(f"frozen proposal line hash does not match source journal: {task_name}")
    try:
        event = _object(json.loads(raw_line), "source proposal event")
    except json.JSONDecodeError as exc:
        raise ValueError(f"source proposal event is invalid JSON: {task_name}") from exc
    if event.get("type") != "agent_decision":
        raise ValueError(f"source proposal line is not an agent decision: {task_name}")
    recorded_decision = AgentDecision.model_validate(event.get("payload"))
    if _completion_proposal(recorded_decision) != _completion_proposal(expected_decision):
        raise ValueError(f"frozen proposal does not match source journal: {task_name}")
    return recorded_decision


def _completion_proposal(decision: AgentDecision) -> dict[str, Any]:
    return decision.model_dump(
        mode="json",
        include={"rationale", "summary", "checks", "coverage"},
    )


def _file_binding(
    path: Path,
    project_root: Path,
    data: bytes | None = None,
) -> dict[str, Any]:
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(project_root.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError(f"source path is outside the project: {path}") from exc
    payload = resolved.read_bytes() if data is None else data
    return {
        "path": relative,
        "bytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


def _source_file_binding(
    path: Path,
    project_root: Path,
    *,
    revision: str | None,
) -> dict[str, Any]:
    if revision is None:
        return _file_binding(path, project_root)
    relative = _relative_project_path(path, project_root)
    payload = _run_git(project_root, "show", f"{revision}:{relative}")
    return _file_binding(path, project_root, payload)


def _tracked_file_revision(path: Path, project_root: Path) -> str:
    relative = _relative_project_path(path, project_root)
    revision = (
        _run_git(
            project_root,
            "log",
            "-1",
            "--format=%H",
            "--",
            relative,
        )
        .decode()
        .strip()
    )
    if not revision:
        raise ValueError(f"source artifact is not tracked by Git: {relative}")
    return revision


def _artifact_source_revision(path: Path, project_root: Path) -> str:
    _, report = _read_json_object(path)
    revision = report.get("source_revision")
    if revision is None:
        return _tracked_file_revision(path, project_root)
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("completion isolation source revision is invalid")
    return revision


def _relative_project_path(path: Path, project_root: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(project_root.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError(f"source path is outside the project: {path}") from exc


def _run_git(project_root: Path, *args: str) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(project_root), *args],
        check=False,
        capture_output=True,
    )
    if completed.returncode:
        detail = completed.stderr.decode(errors="replace").strip()
        raise ValueError(f"Git {' '.join(args)} failed: {detail}")
    return completed.stdout


def _read_json_lines(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid JSON at {path}:{line_number}") from exc
        events.append(_object(value, "journal event"))
    return events


def _read_json_object(path: Path) -> tuple[bytes, dict[str, Any]]:
    data = path.read_bytes()
    return data, _object(json.loads(data), str(path))


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        temporary_path.replace(path)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def _dump_json(value: object) -> bytes:
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n").encode()


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _required_string(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ValueError(f"{key} must be a non-empty string")
    return item


def _required_int(value: dict[str, Any], key: str) -> int:
    item = value.get(key)
    if not isinstance(item, int):
        raise ValueError(f"{key} must be an integer")
    return item


def main() -> int:
    args = parse_args()
    try:
        if args.command == "build":
            build_experiment_report(
                corpus_path=args.corpus,
                calibration_path=args.calibration,
                result_paths=args.result,
            )
            result_paths = freeze_experiment_inputs(
                corpus_path=args.corpus,
                result_paths=args.result,
                snapshot_dir=args.snapshot_dir,
            )
            report = build_experiment_report(
                corpus_path=args.corpus,
                calibration_path=args.calibration,
                result_paths=result_paths,
            )
            _write_atomic(args.report, _dump_json(report))
            _write_atomic(args.markdown, render_markdown(report).encode())
            summary = report["summary"]
            print(
                "completion isolation experiments built: "
                f"{summary['mechanical_isolation_passed']}/{summary['experiments']} "
                "mechanical passes, "
                f"{summary['semantic_review_accepted']}/{summary['experiments']} "
                "semantic accepts"
            )
            return 0

        errors = check_artifacts(
            corpus_path=args.corpus,
            calibration_path=args.calibration,
            report_path=args.report,
            markdown_path=args.markdown,
            source_revision=_artifact_source_revision(args.report, PROJECT_ROOT),
        )
        if errors:
            for error in errors:
                print(error)
            return 1
        print("completion isolation experiment artifacts verified")
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"completion isolation experiments failed: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
