from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from evidence_harness.policy import command_fingerprint, observation_fingerprint
from evidence_harness.protocol import (
    ActionKind,
    AgentDecision,
    CheckIsolationEvidence,
    CheckKind,
    CommandMode,
    CommandReceipt,
    CompletionIsolationEvidence,
    FilesystemDelta,
    IsolationCost,
    OutputExcerpt,
    RequirementCoverage,
    ShellCommand,
    SourceAttestation,
    VerificationCheck,
    VerificationReceipt,
)
from evidence_harness.source_binding import runtime_source_binding
from scripts.completion_isolation_experiments import (
    _dump_json,
    build_experiment_report,
    check_artifacts,
    freeze_experiment_inputs,
    render_markdown,
)

EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _write_jsonl(path: Path, values: list[object]) -> bytes:
    data = ("\n".join(json.dumps(value) for value in values) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def _completion_decision() -> AgentDecision:
    return AgentDecision(
        action=ActionKind.FINISH,
        rationale="the requested artifact is complete",
        summary="created answer.txt",
        checks=(
            VerificationCheck(
                id="check-answer",
                kind=CheckKind.ARTIFACT,
                script="test -s /app/answer.txt",
                proves="answer.txt exists",
                cwd="/app",
            ),
        ),
        coverage=(
            RequirementCoverage(
                requirement="create answer.txt",
                check_ids=("check-answer",),
            ),
        ),
    )


def _evidence() -> VerificationReceipt:
    decision = _completion_decision()
    check = decision.checks[0]
    output = OutputExcerpt(
        head="",
        tail="",
        total_bytes=0,
        omitted_bytes=0,
        sha256=EMPTY_SHA256,
    )
    receipt = CommandReceipt(
        sequence=2,
        command_id=check.id,
        script=check.script,
        purpose=check.proves,
        cwd=check.cwd,
        mode=CommandMode.OBSERVE,
        work_epoch=0,
        return_code=0,
        duration_sec=0.1,
        stdout=output,
        stderr=output,
        command_fingerprint=command_fingerprint(check.script, check.cwd),
        observation_fingerprint=observation_fingerprint(
            command_fingerprint(check.script, check.cwd),
            0,
            None,
            EMPTY_SHA256,
            EMPTY_SHA256,
        ),
    )
    isolation = CompletionIsolationEvidence(
        backend="docker-commit-v1",
        attempt_id=1,
        work_epoch=0,
        candidate_image_id="sha256:" + "c" * 64,
        environment_identity_sha256="d" * 64,
        checks=(
            CheckIsolationEvidence(
                check_id="check-answer",
                receipt_sequence=2,
                receipt_observation_sha256=receipt.observation_fingerprint,
                child_id_sha256="e" * 64,
                started_from_image_id="sha256:" + "c" * 64,
                delta=FilesystemDelta(sha256=EMPTY_SHA256),
                disposed=True,
            ),
        ),
        source=SourceAttestation(
            container_id_sha256="f" * 64,
            diff_sha256_before=EMPTY_SHA256,
            diff_sha256_after=EMPTY_SHA256,
            remained_paused=True,
            resumed=True,
        ),
        snapshot_image_disposed=True,
        cost=IsolationCost(host_operations=18, child_count=1, duration_sec=1.0),
    )
    return VerificationReceipt(
        work_epoch=0,
        checks=(receipt,),
        coverage=decision.coverage,
        isolation=isolation,
        accepted=False,
        rejection_reasons=("completion semantic review was not accepted",),
    )


def _write_fixture(root: Path) -> tuple[Path, Path, Path, Path, Path]:
    case_id = "1" * 64
    checksum = "4" * 64
    commit = "5" * 40
    corpus = root / "evaluation" / "completion-disagreements.json"
    calibration = root / "evaluation" / "completion-calibration.json"
    factory = root / "src" / "evidence_harness" / "docker_completion_isolation.py"
    agent = root / "src" / "evidence_harness" / "isolation_experiment_agent.py"
    result = root / "runs" / "example" / "trial" / "result.json"
    source_command = ShellCommand(
        id="write-answer",
        script="printf done > /app/answer.txt",
        purpose="create answer.txt",
        cwd="/app",
        mode=CommandMode.CHANGE,
    )
    output = OutputExcerpt(
        head="",
        tail="",
        total_bytes=0,
        omitted_bytes=0,
        sha256=EMPTY_SHA256,
    )
    source_command_sha256 = command_fingerprint(
        source_command.script,
        source_command.cwd,
    )
    source_receipt = CommandReceipt(
        sequence=1,
        command_id=source_command.id,
        script=source_command.script,
        purpose=source_command.purpose,
        cwd=source_command.cwd,
        mode=source_command.mode,
        work_epoch=1,
        return_code=0,
        duration_sec=0.1,
        stdout=output,
        stderr=output,
        command_fingerprint=source_command_sha256,
        observation_fingerprint=observation_fingerprint(
            source_command_sha256,
            0,
            None,
            EMPTY_SHA256,
            EMPTY_SHA256,
        ),
    )
    source_journal = result.parent / "source-events.jsonl"
    completion_decision = _completion_decision()
    source_bytes = _write_jsonl(
        source_journal,
        [
            {
                "type": "agent_decision",
                "payload": AgentDecision(
                    action=ActionKind.EXECUTE,
                    rationale="create the requested file",
                    commands=(source_command,),
                ).model_dump(mode="json"),
            },
            {
                "type": "command_receipt",
                "payload": source_receipt.model_dump(mode="json"),
            },
            {
                "type": "agent_decision",
                "payload": completion_decision.model_dump(mode="json"),
            },
            {"type": "run_finished", "payload": {}},
        ],
    )
    journal_sha256 = hashlib.sha256(source_bytes).hexdigest()
    proposal_sha256 = hashlib.sha256(source_bytes.splitlines(keepends=True)[2]).hexdigest()
    proposal = completion_decision.model_dump(mode="json")
    del proposal["action"]
    _write_json(
        corpus,
        {
            "cases": [
                {
                    "case_id": case_id,
                    "task_name": "example",
                    "sources": {
                        "journal": {
                            "path": source_journal.relative_to(root).as_posix(),
                            "sha256": journal_sha256,
                        },
                        "result": {"sha256": "6" * 64},
                        "task_checksum": checksum,
                        "task_git_commit_id": commit,
                        "task_git_url": "https://example.test/tasks.git",
                    },
                    "attempts": [
                        {
                            "ordinal": 1,
                            "proposal": proposal,
                            "proposal_event": {
                                "line": 3,
                                "line_sha256": proposal_sha256,
                            },
                        }
                    ],
                }
            ]
        },
    )
    _write_json(
        calibration,
        {
            "isolation_experiment_candidates": [
                {
                    "task_name": "example",
                    "case_id": case_id,
                    "attempt_ordinal": 1,
                    "check_ids": ["check-answer"],
                }
            ]
        },
    )
    factory.parent.mkdir(parents=True, exist_ok=True)
    factory.write_text("factory\n", encoding="utf-8")
    agent.write_text("agent\n", encoding="utf-8")
    replay_journal = (
        result.parent / "agent" / "completion-isolation-experiment" / "replay" / "events.jsonl"
    )
    completion_journal = (
        result.parent / "agent" / "completion-isolation-experiment" / "completion" / "events.jsonl"
    )
    _write_jsonl(
        replay_journal,
        [
            {
                "type": "replay_started",
                "payload": {
                    "source_journal_sha256": journal_sha256,
                    "command_count": 1,
                },
            },
            {
                "type": "command_receipt",
                "payload": source_receipt.model_dump(mode="json"),
            },
            {"type": "replay_finished", "payload": {"receipt_count": 1}},
        ],
    )
    evidence = _evidence()
    bootstrap_command_sha256 = command_fingerprint("pwd", None)
    bootstrap_receipt = CommandReceipt(
        sequence=1,
        command_id="bootstrap-environment",
        script="pwd",
        purpose="inspect the environment",
        mode=CommandMode.OBSERVE,
        cwd=None,
        work_epoch=0,
        return_code=0,
        duration_sec=0.1,
        stdout=output,
        stderr=output,
        command_fingerprint=bootstrap_command_sha256,
        observation_fingerprint=observation_fingerprint(
            bootstrap_command_sha256,
            0,
            None,
            EMPTY_SHA256,
            EMPTY_SHA256,
        ),
    )
    _write_jsonl(
        completion_journal,
        [
            {
                "type": "run_started",
                "payload": {"journal_schema_version": 2},
            },
            {
                "type": "command_receipt",
                "payload": bootstrap_receipt.model_dump(mode="json"),
            },
            {
                "type": "agent_decision",
                "payload": completion_decision.model_dump(mode="json"),
            },
            {
                "type": "command_receipt",
                "payload": evidence.checks[0].model_dump(mode="json"),
            },
            {
                "type": "completion_review_error",
                "payload": {
                    "error_type": "ModelServiceError",
                    "error": "model credentials are unavailable",
                },
            },
            {
                "type": "verification_receipt",
                "payload": evidence.model_dump(mode="json"),
            },
            {
                "type": "run_finished",
                "payload": {
                    "stop_reason": "model_failure",
                    "failure_category": "completion_review_service",
                    "turns_used": 1,
                    "environment_calls_used": 2,
                    "repairs_used": 0,
                    "recoveries_used": 0,
                },
            },
        ],
    )
    runtime_sha256 = runtime_source_binding(root)["sha256"]
    _write_json(
        result,
        {
            "task_name": "example",
            "task_checksum": checksum,
            "task_id": {
                "git_url": "https://example.test/tasks.git",
                "git_commit_id": commit,
                "path": "example",
            },
            "config": {
                "agent": {"model_name": "openai/test"},
            },
            "agent_result": {
                "metadata": {
                    "completion_isolation_experiment": {
                        "case_id": case_id,
                        "task_name": "example",
                        "attempt_ordinal": 1,
                        "source_journal_sha256": journal_sha256,
                        "proposal_event_line_sha256": proposal_sha256,
                        "experiment_agent_source_sha256": hashlib.sha256(
                            agent.read_bytes()
                        ).hexdigest(),
                        "docker_isolation_source_sha256": hashlib.sha256(
                            factory.read_bytes()
                        ).hexdigest(),
                        "runtime_source_sha256": runtime_sha256,
                        "replay_command_count": 1,
                        "replay_last_sequence": 1,
                        "replay_last_work_epoch": 1,
                        "stop_reason": "model_failure",
                        "failure_category": "completion_review_service",
                        "latest_evidence": evidence.model_dump(mode="json"),
                    }
                }
            },
            "verifier_result": {"rewards": {"reward": 1.0}},
        },
    )
    return corpus, calibration, factory, agent, result


def test_build_experiment_report_separates_mechanical_and_semantic_results(
    tmp_path: Path,
) -> None:
    corpus, calibration, factory, agent, result = _write_fixture(tmp_path)

    report = build_experiment_report(
        corpus_path=corpus,
        calibration_path=calibration,
        result_paths=[result],
        project_root=tmp_path,
        factory_source_path=factory,
        agent_source_path=agent,
    )

    assert report["summary"] == {
        "experiments": 1,
        "command_trajectories_replayed": 1,
        "mechanical_isolation_passed": 1,
        "semantic_review_accepted": 0,
        "semantic_review_unavailable": 1,
        "external_verifier_passed": 1,
        "internally_verified": 0,
    }
    row = report["experiments"][0]
    assert row["mechanical_rejection_reasons"] == []
    assert row["semantic_review_status"] == "unavailable"
    assert row["external_reward"] == 1.0
    assert row["source_attestation"]["diff_sha256_before"] == EMPTY_SHA256
    assert "canonical 59/89 result is unchanged" in render_markdown(report)
    assert "Command trajectories replayed: 1 / 1" in render_markdown(report)


def test_check_artifacts_rebuilds_available_results(tmp_path: Path) -> None:
    corpus, calibration, factory, agent, result = _write_fixture(tmp_path)
    report_path = tmp_path / "evaluation" / "experiments.json"
    markdown_path = tmp_path / "docs" / "experiments.md"
    report = build_experiment_report(
        corpus_path=corpus,
        calibration_path=calibration,
        result_paths=[result],
        project_root=tmp_path,
        factory_source_path=factory,
        agent_source_path=agent,
    )
    report_path.write_bytes(_dump_json(report))
    markdown_path.parent.mkdir(parents=True)
    markdown_path.write_text(render_markdown(report), encoding="utf-8")

    assert (
        check_artifacts(
            corpus_path=corpus,
            calibration_path=calibration,
            report_path=report_path,
            markdown_path=markdown_path,
            project_root=tmp_path,
            factory_source_path=factory,
            agent_source_path=agent,
        )
        == []
    )

    factory.write_text("changed\n", encoding="utf-8")
    assert check_artifacts(
        corpus_path=corpus,
        calibration_path=calibration,
        report_path=report_path,
        markdown_path=markdown_path,
        project_root=tmp_path,
        factory_source_path=factory,
        agent_source_path=agent,
    ) == [
        "invalid completion isolation experiment artifacts: "
        "docker_isolation source binding does not match"
    ]


def test_freeze_experiment_inputs_copies_rebuildable_evidence(tmp_path: Path) -> None:
    corpus, _, _, _, result = _write_fixture(tmp_path)
    snapshot_dir = tmp_path / "evaluation" / "completion-isolation-trials"

    frozen = freeze_experiment_inputs(
        corpus_path=corpus,
        result_paths=[result],
        snapshot_dir=snapshot_dir,
        project_root=tmp_path,
    )

    assert frozen == [snapshot_dir / "example" / "result.json"]
    assert frozen[0].read_bytes() == result.read_bytes()
    assert (frozen[0].parent / "source-events.jsonl").is_file()
    assert (
        frozen[0].parent / "agent" / "completion-isolation-experiment" / "replay" / "events.jsonl"
    ).is_file()


def test_build_experiment_report_rejects_replay_command_drift(tmp_path: Path) -> None:
    corpus, calibration, factory, agent, result = _write_fixture(tmp_path)
    replay_journal = (
        result.parent / "agent" / "completion-isolation-experiment" / "replay" / "events.jsonl"
    )
    events = [json.loads(line) for line in replay_journal.read_text(encoding="utf-8").splitlines()]
    events[1]["payload"]["script"] = "printf changed > /app/answer.txt"
    _write_jsonl(replay_journal, events)

    with pytest.raises(ValueError, match="replay diverged at command 1"):
        build_experiment_report(
            corpus_path=corpus,
            calibration_path=calibration,
            result_paths=[result],
            project_root=tmp_path,
            factory_source_path=factory,
            agent_source_path=agent,
        )


def test_build_experiment_report_rejects_corpus_proposal_drift(
    tmp_path: Path,
) -> None:
    corpus, calibration, factory, agent, result = _write_fixture(tmp_path)
    corpus_value = json.loads(corpus.read_text(encoding="utf-8"))
    corpus_value["cases"][0]["attempts"][0]["proposal"]["checks"][0]["script"] = (
        "test -s /app/weaker.txt"
    )
    _write_json(corpus, corpus_value)

    with pytest.raises(ValueError, match="frozen proposal does not match source journal"):
        build_experiment_report(
            corpus_path=corpus,
            calibration_path=calibration,
            result_paths=[result],
            project_root=tmp_path,
            factory_source_path=factory,
            agent_source_path=agent,
        )


def test_build_experiment_report_rejects_completion_journal_proposal_drift(
    tmp_path: Path,
) -> None:
    corpus, calibration, factory, agent, result = _write_fixture(tmp_path)
    completion_journal = (
        result.parent / "agent" / "completion-isolation-experiment" / "completion" / "events.jsonl"
    )
    events = [
        json.loads(line) for line in completion_journal.read_text(encoding="utf-8").splitlines()
    ]
    decision_event = next(event for event in events if event["type"] == "agent_decision")
    decision_event["payload"]["checks"][0]["script"] = "test -s /app/weaker.txt"
    _write_jsonl(completion_journal, events)

    with pytest.raises(ValueError, match="completion decision does not match frozen proposal"):
        build_experiment_report(
            corpus_path=corpus,
            calibration_path=calibration,
            result_paths=[result],
            project_root=tmp_path,
            factory_source_path=factory,
            agent_source_path=agent,
        )


def test_build_experiment_report_rejects_unbound_completion_receipt(
    tmp_path: Path,
) -> None:
    corpus, calibration, factory, agent, result = _write_fixture(tmp_path)
    completion_journal = (
        result.parent / "agent" / "completion-isolation-experiment" / "completion" / "events.jsonl"
    )
    events = [
        json.loads(line) for line in completion_journal.read_text(encoding="utf-8").splitlines()
    ]
    check_receipt = next(
        event
        for event in events
        if event["type"] == "command_receipt" and event["payload"]["command_id"] == "check-answer"
    )
    events.insert(3, check_receipt)
    _write_jsonl(completion_journal, events)

    with pytest.raises(ValueError, match="completion command receipts do not match result"):
        build_experiment_report(
            corpus_path=corpus,
            calibration_path=calibration,
            result_paths=[result],
            project_root=tmp_path,
            factory_source_path=factory,
            agent_source_path=agent,
        )


def test_build_experiment_report_rejects_outcome_not_bound_to_journal(
    tmp_path: Path,
) -> None:
    corpus, calibration, factory, agent, result = _write_fixture(tmp_path)
    completion_journal = (
        result.parent / "agent" / "completion-isolation-experiment" / "completion" / "events.jsonl"
    )
    events = [
        json.loads(line) for line in completion_journal.read_text(encoding="utf-8").splitlines()
    ]
    events[-1]["payload"]["stop_reason"] = "verified"
    _write_jsonl(completion_journal, events)

    with pytest.raises(ValueError, match="run outcome does not match result"):
        build_experiment_report(
            corpus_path=corpus,
            calibration_path=calibration,
            result_paths=[result],
            project_root=tmp_path,
            factory_source_path=factory,
            agent_source_path=agent,
        )


def test_build_experiment_report_rejects_missing_review_failure_event(
    tmp_path: Path,
) -> None:
    corpus, calibration, factory, agent, result = _write_fixture(tmp_path)
    completion_journal = (
        result.parent / "agent" / "completion-isolation-experiment" / "completion" / "events.jsonl"
    )
    events = [
        json.loads(line) for line in completion_journal.read_text(encoding="utf-8").splitlines()
    ]
    events = [event for event in events if event["type"] != "completion_review_error"]
    _write_jsonl(completion_journal, events)

    with pytest.raises(ValueError, match="review failure does not match result"):
        build_experiment_report(
            corpus_path=corpus,
            calibration_path=calibration,
            result_paths=[result],
            project_root=tmp_path,
            factory_source_path=factory,
            agent_source_path=agent,
        )
