from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from evidence_harness.evaluation import EvaluationMatrix
from evidence_harness.protocol import ProducerAttestation
from evidence_harness_mutation import prefixbench_test_campaign as campaign
from evidence_harness_mutation.campaign import OfflineCampaignReport
from evidence_harness_mutation.prefixbench import (
    PrefixBenchExecutionMode,
    PrefixBenchFileBinding,
    PrefixBenchLegacyCounts,
    PrefixBenchPhaseEligibility,
    PrefixBenchReadinessV2,
    PrefixBenchSourceAdmission,
    PrefixBenchSources,
    PrefixBenchSplit,
    PrefixBenchSummaryV2,
    PrefixBenchTaskAssessmentV2,
    prefixbench_task_split,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEVELOPMENT_PROTOCOL = PROJECT_ROOT / "experiments" / "prefixbench-v1" / "mutation-protocol-v1.json"
TEST_PROTOCOL = PROJECT_ROOT / "experiments" / "prefixbench-v1" / "test-mutation-protocol-v1.json"


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _progress_record(event: str, **values: object) -> dict[str, object]:
    return {"ts": "2026-10-02T00:00:00+00:00", "event": event, **values}


def _valid_progress() -> bytes:
    records: list[dict[str, object]] = [
        _progress_record(
            "run_started",
            completed=0,
            start_at=None,
            start_index=1,
            total=campaign.TEST_TASKS,
        )
    ]
    expected_results: dict[str, str] = {}
    for index, name in enumerate(campaign._FROZEN_TEST_TASK_ORDER, start=1):
        job = f"{index:03d}-{name}"
        result_path = f"{job}/{name}__trial/result.json"
        expected_results[name] = result_path
        records.append(
            _progress_record(
                "task_launching",
                index=index,
                total=campaign.TEST_TASKS,
                task=name,
                job=job,
            )
        )
        if index == 1:
            records.append(
                _progress_record(
                    "run_stopped",
                    index=index,
                    total=campaign.TEST_TASKS,
                    task=name,
                    job=job,
                    exit_code=130,
                )
            )
            records.append(
                _progress_record(
                    "run_started",
                    completed=0,
                    start_at=None,
                    start_index=1,
                    total=campaign.TEST_TASKS,
                )
            )
            job = f"{job}-attempt-2"
            result_path = f"{job}/{name}__trial/result.json"
            expected_results[name] = result_path
            records.append(
                _progress_record(
                    "task_launching",
                    index=index,
                    total=campaign.TEST_TASKS,
                    task=name,
                    job=job,
                )
            )
        records.append(
            _progress_record(
                "task_completed",
                index=index,
                total=campaign.TEST_TASKS,
                task=name,
                job=job,
                reward=0.0,
                exception_type=None,
                result_path=result_path,
            )
        )
    records.append(
        _progress_record(
            "run_completed",
            completed=campaign.TEST_TASKS,
            skipped_before_start=0,
            total=campaign.TEST_TASKS,
        )
    )
    data = b"".join(
        (json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n").encode()
        for record in records
    )
    return data


def _expected_progress_results(data: bytes) -> dict[str, str]:
    return {
        row["task"]: row["result_path"]
        for raw in data.splitlines()
        if (row := json.loads(raw))["event"] == "task_completed"
    }


def _synthetic_test_cohort(
    protocol: campaign.PrefixBenchTestMutationProtocol,
) -> tuple[
    PrefixBenchReadinessV2,
    dict[str, object],
    tuple[dict[str, object], ...],
    EvaluationMatrix,
    tuple[PrefixBenchFileBinding, PrefixBenchFileBinding, PrefixBenchFileBinding],
]:
    matrix = EvaluationMatrix.model_validate_json(
        (PROJECT_ROOT / campaign.TEST_MATRIX).read_bytes()
    )
    source_rows = {
        row["name"]: row
        for row in json.loads((PROJECT_ROOT / "evaluation" / "canonical-89.json").read_bytes())[
            "tasks"
        ]
    }
    producer = ProducerAttestation(
        commit="a" * 40,
        tree="b" * 40,
        source_sha256=campaign.FROZEN_RUNTIME_SOURCE_SHA256,
    )
    assessments: list[PrefixBenchTaskAssessmentV2] = []
    rows: list[dict[str, object]] = []
    for index, matrix_task in enumerate(matrix.tasks, start=1):
        source_row = source_rows[matrix_task.name]
        split, bucket, identity = prefixbench_task_split(
            dataset=matrix.dataset,
            name=matrix_task.name,
            task_checksum=source_row["task_checksum"],
            task_git_url=source_row["task_git_url"],
            task_git_commit_id=source_row["task_git_commit_id"],
        )
        assert split is PrefixBenchSplit.TEST
        run_prefix = campaign.TEST_RUN_ROOT / f"{index:03d}-{matrix_task.name}"
        trial = run_prefix / f"{matrix_task.name}__trial"
        result = PrefixBenchFileBinding(
            path=(trial / "result.json").as_posix(),
            bytes=1,
            sha256=_sha(f"result-{matrix_task.name}"),
        )
        config = PrefixBenchFileBinding(
            path=(trial / "config.json").as_posix(),
            bytes=1,
            sha256=_sha(f"config-{matrix_task.name}"),
        )
        journal = PrefixBenchFileBinding(
            path=(trial / "agent/evidence-harness/events.jsonl").as_posix(),
            bytes=1,
            sha256=_sha(f"journal-{matrix_task.name}"),
        )
        assessments.append(
            PrefixBenchTaskAssessmentV2(
                index=index,
                name=matrix_task.name,
                split=split,
                split_bucket=bucket,
                task_identity_sha256=identity,
                execution_mode=PrefixBenchExecutionMode.LIVE,
                sources=PrefixBenchSources(result=result, config=config, journal=journal),
                journal_schema_version=2,
                producer=producer,
                source_admission=PrefixBenchSourceAdmission(admitted=True),
                phase_eligibility=PrefixBenchPhaseEligibility(),
                legacy_events=PrefixBenchLegacyCounts(),
            )
        )
        rows.append(
            {
                **source_row,
                "index": index,
                "name": matrix_task.name,
                "run_dir": campaign.TEST_RUN_ROOT.as_posix(),
                "result_path": result.path,
                "result_sha256": result.sha256,
                "config_sha256": config.sha256,
                "journal_path": journal.path,
                "journal_sha256": journal.sha256,
                "prefixbench_profile": "prefixbench-v1",
                "producer_commit": producer.commit,
                "producer_tree": producer.tree,
                "producer_source_sha256": producer.source_sha256,
            }
        )

    matrix_binding = protocol.matrix
    canonical_binding = PrefixBenchFileBinding(
        path=campaign.TEST_CANONICAL.as_posix(),
        bytes=1,
        sha256="c" * 64,
    )
    readiness_binding = PrefixBenchFileBinding(
        path=campaign.TEST_READINESS.as_posix(),
        bytes=1,
        sha256="d" * 64,
    )
    template = json.loads(
        (PROJECT_ROOT / "evaluation" / "prefixbench-v1-development-readiness.json").read_bytes()
    )
    summary = PrefixBenchSummaryV2.from_tasks(tuple(assessments))
    template.update(
        {
            "status": "phase_coverage_incomplete",
            "sources": {
                "canonical": canonical_binding.model_dump(mode="json"),
                "matrix": matrix_binding.model_dump(mode="json"),
            },
            "tasks": [task.model_dump(mode="json") for task in assessments],
            "summary": summary.model_dump(mode="json"),
        }
    )
    readiness = PrefixBenchReadinessV2.model_validate(template)
    canonical: dict[str, object] = {
        "schema_version": 2,
        "collection_profile": "prefixbench-v1",
        "dataset": matrix.dataset,
        "matrix_sha256": matrix_binding.sha256,
        "tasks": rows,
    }
    return (
        readiness,
        canonical,
        tuple(rows),
        matrix,
        (matrix_binding, canonical_binding, readiness_binding),
    )


def test_protocol_freezes_held_out_contract_without_a_producer_revision() -> None:
    protocol = campaign.freeze_prefixbench_test_protocol(PROJECT_ROOT)
    payload = protocol.model_dump(mode="json")

    assert protocol.task_order == campaign._FROZEN_TEST_TASK_ORDER
    assert protocol.matrix.sha256 == campaign.FROZEN_TEST_MATRIX_SHA256
    assert protocol.collection.model == campaign.TEST_MODEL
    assert "api_base" not in payload["collection"]
    assert (
        protocol.collection.orchestration_source_sha256
        == "868d63fb26011ddd692f8b04f7b3bea0072ad5ddbd3086200e442280c55f1e0c"
    )
    assert protocol.producer.runtime_source_sha256 == campaign.FROZEN_RUNTIME_SOURCE_SHA256
    assert tuple(file.path for file in protocol.source_set.files) == (
        campaign._TEST_PROTOCOL_SOURCE_PATHS
    )
    assert "src/evidence_harness_mutation/prefixbench_campaign.py" not in {
        file.path for file in protocol.source_set.files
    }
    serialized = json.dumps(payload)
    assert '"producer_commit"' not in serialized
    assert '"producer_tree"' not in serialized


def test_committed_protocol_bytes_match_the_current_manifest() -> None:
    data = TEST_PROTOCOL.read_bytes()
    protocol = campaign.PrefixBenchTestMutationProtocol.model_validate_json(data)

    assert data == protocol.canonical_bytes()
    assert protocol == campaign.freeze_prefixbench_test_protocol(PROJECT_ROOT)


def test_development_protocol_remains_frozen() -> None:
    assert hashlib.sha256(DEVELOPMENT_PROTOCOL.read_bytes()).hexdigest() == (
        campaign.FROZEN_DEVELOPMENT_PROTOCOL_SHA256
    )


def test_test_admission_accepts_complete_phase_incomplete_cohort() -> None:
    protocol = campaign.freeze_prefixbench_test_protocol(PROJECT_ROOT)
    readiness, canonical, rows, matrix, sources = _synthetic_test_cohort(protocol)

    producer = campaign._validate_test_cohort(
        readiness=readiness,
        canonical=canonical,
        rows=rows,
        matrix=matrix,
        sources=sources,
        protocol=protocol,
    )

    assert producer.source_sha256 == campaign.FROZEN_RUNTIME_SOURCE_SHA256


def test_test_admission_rejects_source_census_drift() -> None:
    protocol = campaign.freeze_prefixbench_test_protocol(PROJECT_ROOT)
    readiness, canonical, rows, matrix, sources = _synthetic_test_cohort(protocol)
    changed_summary = readiness.summary.model_copy(update={"source_excluded": 1})
    changed = readiness.model_copy(update={"summary": changed_summary})

    with pytest.raises(ValueError, match="complete 61-task test cohort"):
        campaign._validate_test_cohort(
            readiness=changed,
            canonical=canonical,
            rows=rows,
            matrix=matrix,
            sources=sources,
            protocol=protocol,
        )


def test_progress_accepts_interrupted_resume_and_all_completed_outcomes() -> None:
    data = _valid_progress()
    expected_results = _expected_progress_results(data)

    assert (
        campaign._validate_progress(
            data,
            expected_names=campaign._FROZEN_TEST_TASK_ORDER,
            expected_results=expected_results,
        )
        == campaign._FROZEN_TEST_TASK_ORDER
    )


def test_progress_rejects_duplicate_completed_task() -> None:
    records = [json.loads(raw) for raw in _valid_progress().splitlines()]
    duplicate = next(row for row in records if row["event"] == "task_completed")
    records.insert(-1, duplicate)
    data = b"".join((json.dumps(row) + "\n").encode() for row in records)

    with pytest.raises(ValueError, match="no active launch"):
        campaign._validate_progress(
            data,
            expected_names=campaign._FROZEN_TEST_TASK_ORDER,
            expected_results=_expected_progress_results(_valid_progress()),
        )


def test_collection_evidence_binds_run_configuration_and_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    protocol = campaign.freeze_prefixbench_test_protocol(PROJECT_ROOT)
    readiness, _canonical, rows, _matrix, sources = _synthetic_test_cohort(protocol)
    producer = readiness.tasks[0].producer
    assert producer is not None
    run_root = Path("run")
    run_config = run_root / "run-config.json"
    progress = run_root / "progress.jsonl"
    monkeypatch.setattr(campaign, "TEST_RUN_ROOT", run_root)
    monkeypatch.setattr(campaign, "TEST_RUN_CONFIG", run_config)
    monkeypatch.setattr(campaign, "TEST_PROGRESS", progress)
    (tmp_path / run_root).mkdir()
    (tmp_path / run_config).write_text(
        json.dumps(
            {
                "schema_version": 2,
                "matrix_sha256": protocol.matrix.sha256,
                "model": protocol.collection.model,
                "env_file_sha256": "e" * 64,
                "agent_kwargs_sha256": protocol.collection.agent_kwargs_sha256,
                "source_sha256": protocol.collection.orchestration_source_sha256,
                "start_at": None,
                "debian_bookworm_https_tasks": [],
                "debian_bullseye_main_tasks": [],
                "debian_trixie_https_tasks": [],
                "collection": {
                    "profile": protocol.collection.profile,
                    "producer": producer.model_dump(mode="json"),
                },
            }
        ),
        encoding="utf-8",
    )
    progress_bytes = _valid_progress()
    (tmp_path / progress).write_bytes(progress_bytes)
    expected_results = _expected_progress_results(progress_bytes)
    adjusted: list[dict[str, object]] = []
    for row in rows:
        name = row["name"]
        assert isinstance(name, str)
        adjusted.append(
            {
                **row,
                "result_path": (run_root / expected_results[name]).as_posix(),
            }
        )
    adjusted_rows = tuple(adjusted)
    bound_protocol = campaign.BoundPrefixBenchTestMutationProtocol(
        file=PrefixBenchFileBinding(
            path=campaign.TEST_PROTOCOL.as_posix(),
            bytes=1,
            sha256="f" * 64,
        ),
        spec=protocol,
        preregistration_commit="a" * 40,
    )
    artifacts = campaign._TestArtifacts(
        root=tmp_path,
        sources=sources,
        protocol=bound_protocol,
        readiness=readiness,
        canonical_rows=adjusted_rows,
        producer=producer,
    )

    evidence = campaign._load_collection_evidence(artifacts)

    assert evidence.completed_tasks == campaign._FROZEN_TEST_TASK_ORDER
    assert evidence.producer == producer
    assert evidence.run_config.path == run_config.as_posix()
    assert evidence.progress.sha256 == hashlib.sha256(progress_bytes).hexdigest()


def test_report_derives_all_counts_and_round_trips() -> None:
    protocol_spec = campaign.freeze_prefixbench_test_protocol(PROJECT_ROOT)
    development = json.loads(
        (
            PROJECT_ROOT / "evaluation" / "prefixbench-v1-development-offline-campaign.json"
        ).read_bytes()
    )
    source_task = development["tasks"][0]
    offline = OfflineCampaignReport.model_validate(source_task["campaign"])
    producer = ProducerAttestation(
        commit=offline.source.source_commit,
        tree="b" * 40,
        source_sha256=campaign.FROZEN_RUNTIME_SOURCE_SHA256,
    )
    tasks = tuple(
        campaign.PrefixBenchTestCampaignTask(
            index=index,
            name=name,
            task_identity_sha256=_sha(name),
            journal=PrefixBenchFileBinding(
                path=(campaign.TEST_RUN_ROOT / f"{index:03d}-{name}/events.jsonl").as_posix(),
                bytes=source_task["journal"]["bytes"],
                sha256=offline.source.journal_sha256,
            ),
            journal_lines=offline.through_line,
            projected_attempts=source_task["projected_attempts"],
            campaign=offline,
        )
        for index, name in enumerate(campaign._FROZEN_TEST_TASK_ORDER, start=1)
    )
    bound_protocol = campaign.BoundPrefixBenchTestMutationProtocol(
        file=PrefixBenchFileBinding(
            path=campaign.TEST_PROTOCOL.as_posix(),
            bytes=1,
            sha256="f" * 64,
        ),
        spec=protocol_spec,
        preregistration_commit="a" * 40,
    )
    collection = campaign.PrefixBenchTestCollectionEvidence(
        run_config=PrefixBenchFileBinding(
            path=campaign.TEST_RUN_CONFIG.as_posix(),
            bytes=1,
            sha256="1" * 64,
        ),
        progress=PrefixBenchFileBinding(
            path=campaign.TEST_PROGRESS.as_posix(),
            bytes=1,
            sha256="2" * 64,
        ),
        producer=producer,
        completed_tasks=campaign._FROZEN_TEST_TASK_ORDER,
    )
    report = campaign.PrefixBenchTestCampaignReport(
        readiness_status=campaign.PrefixBenchStatus.PHASE_COVERAGE_INCOMPLETE,
        sources=campaign.PrefixBenchTestCampaignSources(
            matrix=protocol_spec.matrix,
            canonical=PrefixBenchFileBinding(
                path=campaign.TEST_CANONICAL.as_posix(),
                bytes=1,
                sha256="3" * 64,
            ),
            readiness=PrefixBenchFileBinding(
                path=campaign.TEST_READINESS.as_posix(),
                bytes=1,
                sha256="4" * 64,
            ),
            collection=collection,
        ),
        protocol=bound_protocol,
        producer=producer,
        tasks=tasks,
        summary=campaign.PrefixBenchTestCampaignSummary.from_tasks(tasks),
        claims=campaign.PrefixBenchTestClaims(),
    )

    assert report.summary.tasks == 61
    assert report.summary.outcomes.scheduled == 61 * len(offline.cases)
    assert (
        campaign.PrefixBenchTestCampaignReport.model_validate_json(report.canonical_bytes())
        == report
    )


def test_claim_schema_rejects_unregistered_score() -> None:
    payload = campaign.PrefixBenchTestClaims().model_dump(mode="json")
    payload["mutation_score"] = 0.5

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        campaign.PrefixBenchTestClaims.model_validate(payload)


def test_protocol_generation_rejects_existing_held_out_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(campaign, "TEST_CANONICAL", Path("test-canonical.json"))
    (tmp_path / "test-canonical.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="must precede held-out artifacts"):
        campaign._require_precollection_state(tmp_path)


def test_task_model_rejects_request_order_drift() -> None:
    development = json.loads(
        (
            PROJECT_ROOT / "evaluation" / "prefixbench-v1-development-offline-campaign.json"
        ).read_bytes()
    )
    task = development["tasks"][0]
    candidate = campaign.PrefixBenchTestCampaignTask.model_validate(task)
    payload = candidate.model_dump(mode="json")
    payload["campaign"]["cases"][0]["request"], payload["campaign"]["cases"][1]["request"] = (
        payload["campaign"]["cases"][1]["request"],
        payload["campaign"]["cases"][0]["request"],
    )

    with pytest.raises(ValidationError, match="exact default request schedule"):
        campaign.PrefixBenchTestCampaignTask.model_validate(payload)
