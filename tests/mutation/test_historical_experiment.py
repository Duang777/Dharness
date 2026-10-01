from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from evidence_harness_mutation.historical import (
    COORDINATOR_RELATIVE_PATH,
    MANIFEST_RELATIVE_PATH,
    SCHEMA_RELATIVE_PATH,
    WORKER_RELATIVE_PATH,
    HistoricalInfrastructureError,
    HistoricalRunRequest,
    MaterializedRevision,
    _build_probe_request,
    _load_bound_manifest,
    _materialize_revision,
    run_historical7,
)
from evidence_harness_mutation.historical_cases import (
    CompletedCaseResult,
    FailedChangeObservation,
    HistoricalCaseId,
    HistoricalPropertyId,
    HistoricalReport,
    MaxRepairsCase,
    MaxRepairsObservation,
    MutationOutcome,
    ReorderReceiptsObservation,
    ReviewQuotaObservation,
    ReviewReceiptOrderObservation,
    ReviewTimeoutObservation,
    RevisionRole,
    WallTimeObservation,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def historical_run(
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[HistoricalReport, Path]:
    output_path = tmp_path_factory.mktemp("historical-report") / "report.json"
    before = _git_status()
    report = run_historical7(
        HistoricalRunRequest(
            repo_root=PROJECT_ROOT,
            output_path=output_path,
        )
    )
    assert _git_status() == before
    return report, output_path


def test_manifest_freezes_all_seven_historical_pairs() -> None:
    manifest = _load_bound_manifest(PROJECT_ROOT)

    assert manifest.relative_path == MANIFEST_RELATIVE_PATH.as_posix()
    assert tuple(case.case_id for case in manifest.value.cases) == tuple(HistoricalCaseId)
    assert tuple(case.property.id for case in manifest.value.cases) == tuple(HistoricalPropertyId)
    assert all(
        tuple(revision.role for revision in case.revisions)
        == (RevisionRole.VULNERABLE, RevisionRole.FIXED)
        for case in manifest.value.cases
    )
    assert {revision.commit for case in manifest.value.cases for revision in case.revisions} == {
        "ba2cbaebb1ceb97b024c549ee3e00790bd7e5f78",
        "5f64d669a3bd8078dba075717d044fbc29694cd5",
        "6f74c19f74b68dd7d79d64c4c205274ae2cd1788",
        "289bea40d10e0e94b98b1247617d06a12fc46f52",
        "3a3b83f8315304d2fd39130c524fa2d41e4807d5",
    }
    max_repairs = manifest.value.cases[5]
    assert isinstance(max_repairs, MaxRepairsCase)
    assert max_repairs.scenario.options.max_repairs == 1


def test_materialization_rejects_a_tree_outside_the_manifest(tmp_path: Path) -> None:
    manifest = _load_bound_manifest(PROJECT_ROOT)
    revision = manifest.value.cases[0].revisions[0].model_copy(update={"tree": "0" * 40})

    with pytest.raises(HistoricalInfrastructureError, match="does not match manifest"):
        _materialize_revision(
            repo_root=PROJECT_ROOT,
            revision=revision,
            destination=tmp_path,
            expected_lock_sha256=manifest.value.uv_lock_sha256,
        )


def test_worker_request_has_no_expected_result_or_classifier_data(
    historical_run: tuple[HistoricalReport, Path],
) -> None:
    report, _ = historical_run
    manifest = _load_bound_manifest(PROJECT_ROOT)
    archive = report.archives[0]
    materialized = MaterializedRevision(
        source_root=PROJECT_ROOT,
        archive_path=PROJECT_ROOT / "unused.tar",
        binding=archive,
    )

    request = _build_probe_request(
        manifest=manifest,
        case=manifest.value.cases[0],
        revision=materialized,
    )
    payload = request.model_dump(mode="json")

    assert set(payload) == {
        "schema_version",
        "suite_id",
        "case_id",
        "commit",
        "tree",
        "manifest_sha256",
        "production_source_sha256",
        "source_root",
        "scenario",
    }
    serialized = request.model_dump_json()
    assert "expected_outcome" not in serialized
    assert "revision_role" not in serialized
    assert "matches_expected" not in serialized
    assert "property_id" not in serialized


def test_worker_rejects_an_extra_expected_outcome_field() -> None:
    manifest = _load_bound_manifest(PROJECT_ROOT)
    case = manifest.value.cases[0]
    payload = {
        "schema_version": 2,
        "suite_id": "historical-7",
        "case_id": case.case_id,
        "commit": case.revisions[0].commit,
        "tree": case.revisions[0].tree,
        "manifest_sha256": manifest.sha256,
        "production_source_sha256": "0" * 64,
        "source_root": str(PROJECT_ROOT),
        "scenario": case.scenario.model_dump(mode="json"),
        "expected_outcome": "survived",
    }

    completed = subprocess.run(
        (sys.executable, "-I", "-B", str(PROJECT_ROOT / WORKER_RELATIVE_PATH)),
        cwd=PROJECT_ROOT,
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "request fields do not match schema version 2" in completed.stderr


def test_real_historical_production_kills_only_fixed_revisions(
    historical_run: tuple[HistoricalReport, Path],
) -> None:
    report, _ = historical_run

    assert report.verdict == "passed"
    assert len(report.archives) == 5
    assert len(report.results) == 14
    assert [
        (
            result.case_id,
            result.revision_role,
            result.observed_outcome,
            result.matches_expected,
        )
        for result in report.results
        if isinstance(result, CompletedCaseResult)
    ] == [
        (case_id, role, outcome, True)
        for case_id in HistoricalCaseId
        for role, outcome in (
            (RevisionRole.VULNERABLE, MutationOutcome.SURVIVED),
            (RevisionRole.FIXED, MutationOutcome.KILLED),
        )
    ]


def test_report_binds_all_control_and_production_sources(
    historical_run: tuple[HistoricalReport, Path],
) -> None:
    report, output_path = historical_run
    controls = {
        MANIFEST_RELATIVE_PATH: report.control.manifest,
        WORKER_RELATIVE_PATH: report.control.worker,
        COORDINATOR_RELATIVE_PATH: report.control.coordinator,
        SCHEMA_RELATIVE_PATH: report.control.schema_module,
    }
    for relative_path, binding in controls.items():
        assert binding.path == relative_path.as_posix()
        assert binding.sha256 == _sha256(PROJECT_ROOT / relative_path)

    source_by_revision = {
        (archive.commit, archive.tree): archive.source_set.sha256 for archive in report.archives
    }
    for archive in report.archives:
        assert archive.archive_commit == archive.commit
        expected_paths = _production_paths_at_revision(archive.commit)
        assert tuple(item.path for item in archive.source_set.files) == expected_paths
        for file_binding in archive.source_set.files:
            content = subprocess.run(
                ("git", "show", f"{archive.commit}:{file_binding.path}"),
                cwd=PROJECT_ROOT,
                capture_output=True,
                check=True,
            ).stdout
            assert file_binding.sha256 == hashlib.sha256(content).hexdigest()

    for result in report.results:
        assert isinstance(result, CompletedCaseResult)
        source_sha256 = source_by_revision[(result.commit, result.tree)]
        assert result.archive_source_sha256 == source_sha256
        assert result.observation.binding.production_source_sha256 == source_sha256
        module_source = subprocess.run(
            (
                "git",
                "show",
                f"{result.commit}:{result.observation.binding.source_path}",
            ),
            cwd=PROJECT_ROOT,
            capture_output=True,
            check=True,
        ).stdout
        assert result.observation.binding.source_sha256 == hashlib.sha256(module_source).hexdigest()

    serialized = output_path.read_text(encoding="utf-8")
    assert json.loads(serialized) == report.model_dump(mode="json")
    assert str(output_path.parent) not in serialized
    assert "timestamp" not in serialized


def test_observations_capture_all_seven_control_flow_changes(
    historical_run: tuple[HistoricalReport, Path],
) -> None:
    report, _ = historical_run

    timeout_v = _observation(report, HistoricalCaseId.REVIEW_TIMEOUT_FALLBACK, "vulnerable")
    timeout_f = _observation(report, HistoricalCaseId.REVIEW_TIMEOUT_FALLBACK, "fixed")
    assert isinstance(timeout_v, ReviewTimeoutObservation)
    assert isinstance(timeout_f, ReviewTimeoutObservation)
    assert (timeout_v.run.stop_reason, timeout_f.run.stop_reason) == (
        "verified",
        "model_failure",
    )
    assert (
        timeout_v.run.accepted_verification_count,
        timeout_f.run.accepted_verification_count,
    ) == (
        1,
        0,
    )

    reorder_v = _observation(report, HistoricalCaseId.REORDER_CHECK_RECEIPTS, "vulnerable")
    reorder_f = _observation(report, HistoricalCaseId.REORDER_CHECK_RECEIPTS, "fixed")
    assert isinstance(reorder_v, ReorderReceiptsObservation)
    assert isinstance(reorder_f, ReorderReceiptsObservation)
    assert reorder_v.accepted is True
    assert reorder_f.rejection_reasons == (
        "not all proposed verification commands were executed in order",
    )

    order_v = _observation(report, HistoricalCaseId.REVIEW_RECEIPT_ORDER, "vulnerable")
    order_f = _observation(report, HistoricalCaseId.REVIEW_RECEIPT_ORDER, "fixed")
    assert isinstance(order_v, ReviewReceiptOrderObservation)
    assert isinstance(order_f, ReviewReceiptOrderObservation)
    assert order_v.call_order == ("review", "check")
    assert order_v.review_saw_receipt_marker == (False,)
    assert order_f.call_order == ("check", "review")
    assert order_f.review_saw_receipt_marker == (True,)

    quota_v = _observation(report, HistoricalCaseId.REVIEW_QUOTA_NO_BYPASS, "vulnerable")
    quota_f = _observation(report, HistoricalCaseId.REVIEW_QUOTA_NO_BYPASS, "fixed")
    assert isinstance(quota_v, ReviewQuotaObservation)
    assert isinstance(quota_f, ReviewQuotaObservation)
    assert (quota_v.check_calls, quota_v.run.repairs_used) == (1, 2)
    assert (quota_f.check_calls, quota_f.run.repairs_used) == (2, 1)
    assert quota_f.run.failure_category == "completion_review_budget"

    failed_v = _observation(report, HistoricalCaseId.FAILED_CHANGE_PROGRESS, "vulnerable")
    failed_f = _observation(report, HistoricalCaseId.FAILED_CHANGE_PROGRESS, "fixed")
    assert isinstance(failed_v, FailedChangeObservation)
    assert isinstance(failed_f, FailedChangeObservation)
    assert failed_v.stop_decision_consumed is True
    assert failed_v.run.stop_reason == "model_stopped"
    assert failed_f.stop_decision_consumed is False
    assert failed_f.run.stop_reason == "doom_loop"

    repairs_v = _observation(report, HistoricalCaseId.MAX_REPAIRS_EXACT, "vulnerable")
    repairs_f = _observation(report, HistoricalCaseId.MAX_REPAIRS_EXACT, "fixed")
    assert isinstance(repairs_v, MaxRepairsObservation)
    assert isinstance(repairs_f, MaxRepairsObservation)
    assert (repairs_v.run.repairs_used, repairs_v.run.failure_category) == (
        2,
        "model_protocol",
    )
    assert (repairs_f.run.repairs_used, repairs_f.run.failure_category) == (
        1,
        "completion_repair_budget",
    )

    wall_v = _observation(report, HistoricalCaseId.WALL_TIME_COMMAND_DEADLINE, "vulnerable")
    wall_f = _observation(report, HistoricalCaseId.WALL_TIME_COMMAND_DEADLINE, "fixed")
    assert isinstance(wall_v, WallTimeObservation)
    assert isinstance(wall_f, WallTimeObservation)
    assert (
        wall_v.work_command_timeout_sec,
        wall_v.work_command_return_code,
        wall_v.final_clock_sec,
        wall_v.finalization_triggers,
    ) == (150, 0, 930.0, ())
    assert (
        wall_f.work_command_timeout_sec,
        wall_f.work_command_failure,
        wall_f.final_clock_sec,
        wall_f.finalization_triggers,
    ) == (50, "timeout", 900.0, ("wall_clock",))


def test_repeated_run_is_byte_identical(
    historical_run: tuple[HistoricalReport, Path],
    tmp_path: Path,
) -> None:
    _, first_path = historical_run
    second_path = tmp_path / "report.json"

    run_historical7(
        HistoricalRunRequest(
            repo_root=PROJECT_ROOT,
            output_path=second_path,
        )
    )

    assert second_path.read_bytes() == first_path.read_bytes()


def _observation(
    report: HistoricalReport,
    case_id: HistoricalCaseId,
    role: RevisionRole | str,
):
    result = next(
        result
        for result in report.results
        if result.case_id == case_id and result.revision_role == role
    )
    assert isinstance(result, CompletedCaseResult)
    return result.observation


def _production_paths_at_revision(commit: str) -> tuple[str, ...]:
    paths = subprocess.run(
        ("git", "ls-tree", "-r", "--name-only", commit),
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    return tuple(
        [
            "pyproject.toml",
            "uv.lock",
            *sorted(
                path
                for path in paths
                if path.startswith("src/evidence_harness/") and path.endswith(".py")
            ),
        ]
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_status() -> bytes:
    return subprocess.run(
        ("git", "status", "--porcelain=v1", "-z"),
        cwd=PROJECT_ROOT,
        capture_output=True,
        check=True,
    ).stdout
