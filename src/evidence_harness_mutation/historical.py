from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from evidence_harness_mutation.historical_cases import (
    ArchiveBinding,
    CompletedCaseResult,
    ControlBinding,
    ErrorCaseResult,
    FailedChangeCase,
    FailedChangeObservation,
    FileBinding,
    HistoricalCase,
    HistoricalManifest,
    HistoricalReport,
    MaxRepairsCase,
    MaxRepairsObservation,
    MutationOutcome,
    ProbeRequest,
    ProbeResponse,
    ProductionObservation,
    ReorderReceiptsCase,
    ReorderReceiptsObservation,
    ReviewQuotaCase,
    ReviewQuotaObservation,
    ReviewReceiptOrderCase,
    ReviewReceiptOrderObservation,
    ReviewTimeoutCase,
    ReviewTimeoutObservation,
    RevisionSpec,
    SourceSetBinding,
    WallTimeCase,
    WallTimeObservation,
)

MANIFEST_RELATIVE_PATH = Path("experiments/historical-7/manifest.json")
WORKER_RELATIVE_PATH = Path("scripts/historical_mutation_probe.py")
COORDINATOR_RELATIVE_PATH = Path("src/evidence_harness_mutation/historical.py")
SCHEMA_RELATIVE_PATH = Path("src/evidence_harness_mutation/historical_cases.py")
_EXPECTED_LOCK_SHA256 = "aa764a8aed8e494f737c15dac597a52b8ca9fea89caa665eaf25630c80bab3d8"


class HistoricalInfrastructureError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class HistoricalRunRequest:
    repo_root: Path
    output_path: Path
    subprocess_timeout_sec: float = 30.0


@dataclass(frozen=True, slots=True)
class BoundManifest:
    relative_path: str
    sha256: str
    value: HistoricalManifest


@dataclass(frozen=True, slots=True)
class MaterializedRevision:
    source_root: Path
    archive_path: Path
    binding: ArchiveBinding


def run_historical7(request: HistoricalRunRequest) -> HistoricalReport:
    repo_root = _validated_repo_root(request.repo_root)
    coordinator_path = (repo_root / COORDINATOR_RELATIVE_PATH).resolve()
    if Path(__file__).resolve() != coordinator_path:
        raise HistoricalInfrastructureError(
            "loaded Historical-7 coordinator does not match the repository source"
        )
    if request.subprocess_timeout_sec <= 0:
        raise HistoricalInfrastructureError("subprocess timeout must be positive")

    manifest = _load_bound_manifest(repo_root)
    if manifest.value.uv_lock_sha256 != _EXPECTED_LOCK_SHA256:
        raise HistoricalInfrastructureError("Historical-7 uses an unexpected uv.lock")
    current_lock_sha256 = _hash_required_file(repo_root / "uv.lock", "current uv.lock")
    if current_lock_sha256 != manifest.value.uv_lock_sha256:
        raise HistoricalInfrastructureError(
            "current uv.lock does not match the Historical-7 locked environment"
        )
    control = _capture_control_binding(repo_root)
    worker_path = repo_root / WORKER_RELATIVE_PATH

    results: list[CompletedCaseResult | ErrorCaseResult] = []
    materialized_by_revision: dict[tuple[str, str], MaterializedRevision] = {}
    materialization_errors: dict[tuple[str, str], str] = {}
    with tempfile.TemporaryDirectory(prefix="historical-7-") as temporary:
        temporary_root = Path(temporary)
        for revision in _unique_revisions(manifest.value):
            key = (revision.commit, revision.tree)
            try:
                materialized_by_revision[key] = _materialize_revision(
                    repo_root=repo_root,
                    revision=revision,
                    destination=temporary_root / revision.commit[:12],
                    expected_lock_sha256=manifest.value.uv_lock_sha256,
                )
            except Exception as exc:
                materialization_errors[key] = _stable_error(exc, temporary_root, repo_root)

        for case in manifest.value.cases:
            for revision in case.revisions:
                key = (revision.commit, revision.tree)
                materialized = materialized_by_revision.get(key)
                if materialized is None:
                    results.append(
                        _build_error_result(
                            case=case,
                            revision=revision,
                            archive_source_sha256=None,
                            error=materialization_errors[key],
                        )
                    )
                    continue
                try:
                    observation = _invoke_probe(
                        worker_path=worker_path,
                        revision=materialized,
                        manifest=manifest,
                        case=case,
                        working_directory=temporary_root / f"{case.case_id}-{revision.role}",
                        timeout_sec=request.subprocess_timeout_sec,
                    )
                    outcome = classify_observation(case, observation)
                    results.append(
                        CompletedCaseResult(
                            kind="completed",
                            case_id=case.case_id,
                            property_id=case.property.id,
                            revision_role=revision.role,
                            commit=revision.commit,
                            tree=revision.tree,
                            archive_source_sha256=materialized.binding.source_set.sha256,
                            expected_outcome=revision.expected_outcome,
                            observed_outcome=outcome,
                            matches_expected=outcome == revision.expected_outcome,
                            observation=observation,
                        )
                    )
                except Exception as exc:
                    results.append(
                        _build_error_result(
                            case=case,
                            revision=revision,
                            archive_source_sha256=materialized.binding.source_set.sha256,
                            error=_stable_error(exc, temporary_root, repo_root),
                        )
                    )

        for materialized in materialized_by_revision.values():
            if _bind_production_source(materialized.source_root) != materialized.binding.source_set:
                raise HistoricalInfrastructureError(
                    f"archived production source changed during the run: "
                    f"{materialized.binding.commit}"
                )
            if _hash_required_file(materialized.archive_path, "Git archive") != (
                materialized.binding.archive_sha256
            ):
                raise HistoricalInfrastructureError(
                    f"Git archive changed during the run: {materialized.binding.commit}"
                )

    if _capture_control_binding(repo_root) != control:
        raise HistoricalInfrastructureError("Historical-7 control files changed during the run")

    verdict = _report_verdict(results)
    report = HistoricalReport(
        schema_version=2,
        suite_id=manifest.value.suite_id,
        uv_lock_sha256=manifest.value.uv_lock_sha256,
        control=control,
        archives=tuple(item.binding for item in materialized_by_revision.values()),
        results=tuple(results),
        verdict=verdict,
    )
    output_path = (
        request.output_path
        if request.output_path.is_absolute()
        else repo_root / request.output_path
    )
    write_report_atomic(output_path, report)
    return report


def classify_observation(
    case: HistoricalCase,
    observation: ProductionObservation,
) -> MutationOutcome:
    if isinstance(case, ReviewTimeoutCase):
        if not isinstance(observation, ReviewTimeoutObservation):
            return MutationOutcome.INCONCLUSIVE
        return _classify_review_timeout(observation)
    if isinstance(case, ReorderReceiptsCase):
        if not isinstance(observation, ReorderReceiptsObservation):
            return MutationOutcome.INCONCLUSIVE
        return _classify_reordered_receipts(case, observation)
    if isinstance(case, ReviewReceiptOrderCase):
        if not isinstance(observation, ReviewReceiptOrderObservation):
            return MutationOutcome.INCONCLUSIVE
        return _classify_review_receipt_order(observation)
    if isinstance(case, ReviewQuotaCase):
        if not isinstance(observation, ReviewQuotaObservation):
            return MutationOutcome.INCONCLUSIVE
        return _classify_review_quota(observation)
    if isinstance(case, FailedChangeCase):
        if not isinstance(observation, FailedChangeObservation):
            return MutationOutcome.INCONCLUSIVE
        return _classify_failed_change(case, observation)
    if isinstance(case, MaxRepairsCase):
        if not isinstance(observation, MaxRepairsObservation):
            return MutationOutcome.INCONCLUSIVE
        return _classify_max_repairs(observation)
    if not isinstance(observation, WallTimeObservation):
        return MutationOutcome.INCONCLUSIVE
    return _classify_wall_time(case, observation)


def write_report_atomic(path: Path, report: HistoricalReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = _canonical_json(report.model_dump(mode="json"))
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary_path = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _load_bound_manifest(repo_root: Path) -> BoundManifest:
    path = repo_root / MANIFEST_RELATIVE_PATH
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise HistoricalInfrastructureError(f"cannot read Historical-7 manifest: {exc}") from exc
    try:
        value = HistoricalManifest.model_validate_json(data)
    except ValidationError as exc:
        raise HistoricalInfrastructureError(f"invalid Historical-7 manifest: {exc}") from exc
    return BoundManifest(
        relative_path=MANIFEST_RELATIVE_PATH.as_posix(),
        sha256=hashlib.sha256(data).hexdigest(),
        value=value,
    )


def _materialize_revision(
    *,
    repo_root: Path,
    revision: RevisionSpec,
    destination: Path,
    expected_lock_sha256: str,
) -> MaterializedRevision:
    resolved_commit = _run_git(
        repo_root,
        "rev-parse",
        "--verify",
        f"{revision.commit}^{{commit}}",
    )
    if resolved_commit != revision.commit:
        raise HistoricalInfrastructureError(
            f"resolved commit does not match manifest: {resolved_commit}"
        )
    resolved_tree = _run_git(repo_root, "rev-parse", f"{revision.commit}^{{tree}}")
    if resolved_tree != revision.tree:
        raise HistoricalInfrastructureError(
            f"tree for {revision.commit} does not match manifest: {resolved_tree}"
        )

    destination.mkdir()
    archive_path = destination / "source.tar"
    _run_git(
        repo_root,
        "archive",
        "--format=tar",
        "--prefix=source/",
        f"--output={archive_path}",
        revision.commit,
    )
    archive_sha256 = _hash_required_file(archive_path, "Git archive")
    archive_commit = _archive_commit(repo_root, archive_path)
    if archive_commit != revision.commit:
        raise HistoricalInfrastructureError(
            f"archive commit does not match manifest: {archive_commit}"
        )

    extraction_root = destination / "archive"
    extraction_root.mkdir()
    try:
        with tarfile.open(archive_path, mode="r:") as archive:
            archive.extractall(extraction_root, filter="data")
    except (OSError, tarfile.TarError) as exc:
        raise HistoricalInfrastructureError(f"cannot extract Git archive: {exc}") from exc
    source_root = extraction_root / "source"
    source_set = _bind_production_source(source_root)
    lock_binding = next(item for item in source_set.files if item.path == "uv.lock")
    if lock_binding.sha256 != expected_lock_sha256:
        raise HistoricalInfrastructureError(
            f"archived uv.lock for {revision.commit} does not match the manifest"
        )
    return MaterializedRevision(
        source_root=source_root,
        archive_path=archive_path,
        binding=ArchiveBinding(
            commit=revision.commit,
            tree=revision.tree,
            archive_commit=archive_commit,
            archive_sha256=archive_sha256,
            source_set=source_set,
        ),
    )


def _invoke_probe(
    *,
    worker_path: Path,
    revision: MaterializedRevision,
    manifest: BoundManifest,
    case: HistoricalCase,
    working_directory: Path,
    timeout_sec: float,
) -> ProductionObservation:
    working_directory.mkdir()
    request = _build_probe_request(manifest=manifest, case=case, revision=revision)
    environment = os.environ.copy()
    for name in ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "PYTHONUSERBASE"):
        environment.pop(name, None)
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        completed = subprocess.run(
            (sys.executable, "-I", "-B", str(worker_path)),
            cwd=working_directory,
            env=environment,
            input=request.model_dump_json(),
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise HistoricalInfrastructureError(
            f"probe timed out after {timeout_sec:g} seconds"
        ) from exc
    except OSError as exc:
        raise HistoricalInfrastructureError(f"cannot start historical probe: {exc}") from exc
    if completed.returncode:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise HistoricalInfrastructureError(
            f"probe exited with {completed.returncode}: {_bounded(detail)}"
        )
    try:
        response = ProbeResponse.model_validate_json(completed.stdout)
    except ValidationError as exc:
        raise HistoricalInfrastructureError(f"invalid probe response: {exc}") from exc
    expected_identity = (
        request.suite_id,
        request.case_id,
        request.commit,
        request.tree,
        request.manifest_sha256,
        request.production_source_sha256,
    )
    observed_identity = (
        response.suite_id,
        response.case_id,
        response.commit,
        response.tree,
        response.manifest_sha256,
        response.production_source_sha256,
    )
    if observed_identity != expected_identity:
        raise HistoricalInfrastructureError("probe response source binding does not match request")
    if response.observation.binding.entrypoint != case.entrypoint:
        raise HistoricalInfrastructureError("probe called the wrong production entrypoint")
    if response.observation.binding.production_source_sha256 != revision.binding.source_set.sha256:
        raise HistoricalInfrastructureError("probe used an unexpected production source set")
    return response.observation


def _build_probe_request(
    *,
    manifest: BoundManifest,
    case: HistoricalCase,
    revision: MaterializedRevision,
) -> ProbeRequest:
    return ProbeRequest(
        schema_version=2,
        suite_id=manifest.value.suite_id,
        case_id=case.case_id,
        commit=revision.binding.commit,
        tree=revision.binding.tree,
        manifest_sha256=manifest.sha256,
        production_source_sha256=revision.binding.source_set.sha256,
        source_root=str(revision.source_root),
        scenario=case.scenario,
    )


def _capture_control_binding(repo_root: Path) -> ControlBinding:
    return ControlBinding(
        manifest=_file_binding(repo_root, MANIFEST_RELATIVE_PATH),
        worker=_file_binding(repo_root, WORKER_RELATIVE_PATH),
        coordinator=_file_binding(repo_root, COORDINATOR_RELATIVE_PATH),
        schema_module=_file_binding(repo_root, SCHEMA_RELATIVE_PATH),
    )


def _bind_production_source(source_root: Path) -> SourceSetBinding:
    relative_paths = [
        Path("pyproject.toml"),
        Path("uv.lock"),
        *sorted(
            (
                path.relative_to(source_root)
                for path in (source_root / "src" / "evidence_harness").rglob("*.py")
            ),
            key=lambda path: path.as_posix(),
        ),
    ]
    digest = hashlib.sha256()
    bindings: list[FileBinding] = []
    for relative_path in relative_paths:
        path = source_root / relative_path
        if path.is_symlink() or not path.is_file():
            raise HistoricalInfrastructureError(
                f"production source is not a regular file: {relative_path.as_posix()}"
            )
        try:
            content = path.read_bytes()
        except OSError as exc:
            raise HistoricalInfrastructureError(
                f"cannot read production source {relative_path.as_posix()}: {exc}"
            ) from exc
        encoded_path = relative_path.as_posix().encode()
        digest.update(len(encoded_path).to_bytes(8, "big"))
        digest.update(encoded_path)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
        bindings.append(
            FileBinding(
                path=relative_path.as_posix(),
                sha256=hashlib.sha256(content).hexdigest(),
            )
        )
    return SourceSetBinding(
        algorithm="sha256-length-framed-path-content-v1",
        sha256=digest.hexdigest(),
        files=tuple(bindings),
    )


def _classify_review_timeout(observation: ReviewTimeoutObservation) -> MutationOutcome:
    run = observation.run
    common = (
        run.decision_calls == 1
        and run.review_calls == 1
        and run.environment_calls == 2
        and run.turns_used == 1
        and run.repairs_used == 0
        and run.review_error_count == 1
        and run.verification_attempts == 1
    )
    if (
        common
        and run.stop_reason == "verified"
        and run.failure_category is None
        and run.evidence_accepted is True
        and run.rejection_reasons == ()
        and run.accepted_verification_count == 1
    ):
        return MutationOutcome.SURVIVED
    if (
        common
        and run.stop_reason == "model_failure"
        and run.failure_category == "completion_review_service"
        and run.evidence_accepted is False
        and run.rejection_reasons == ("completion semantic review was not accepted",)
        and run.accepted_verification_count == 0
    ):
        return MutationOutcome.KILLED
    return MutationOutcome.INCONCLUSIVE


def _classify_reordered_receipts(
    case: ReorderReceiptsCase,
    observation: ReorderReceiptsObservation,
) -> MutationOutcome:
    expected_ids = tuple(check.id for check in case.scenario.checks)
    mutation_applied = (
        observation.proposal_rejections == ()
        and observation.proposed_check_ids == expected_ids
        and observation.supplied_receipt_ids == tuple(reversed(expected_ids))
    )
    if mutation_applied and observation.accepted and observation.rejection_reasons == ():
        return MutationOutcome.SURVIVED
    if (
        mutation_applied
        and not observation.accepted
        and observation.rejection_reasons
        == ("not all proposed verification commands were executed in order",)
    ):
        return MutationOutcome.KILLED
    return MutationOutcome.INCONCLUSIVE


def _classify_review_receipt_order(
    observation: ReviewReceiptOrderObservation,
) -> MutationOutcome:
    run = observation.run
    common = (
        run.decision_calls == 1
        and run.review_calls == 1
        and run.environment_calls == 2
        and run.turns_used == 1
        and run.review_error_count == 0
        and run.verification_attempts == 1
    )
    if (
        common
        and observation.call_order == ("review", "check")
        and observation.review_saw_receipt_marker == (False,)
        and run.stop_reason == "verified"
        and run.failure_category is None
        and run.evidence_accepted is True
        and run.repairs_used == 0
        and run.accepted_verification_count == 1
    ):
        return MutationOutcome.SURVIVED
    if (
        common
        and observation.call_order == ("check", "review")
        and observation.review_saw_receipt_marker == (True,)
        and run.stop_reason == "budget_exhausted"
        and run.failure_category == "completion_repair_budget"
        and run.evidence_accepted is False
        and run.repairs_used == 0
        and run.accepted_verification_count == 0
    ):
        return MutationOutcome.KILLED
    return MutationOutcome.INCONCLUSIVE


def _classify_review_quota(observation: ReviewQuotaObservation) -> MutationOutcome:
    run = observation.run
    if (
        run.stop_reason == "verified"
        and run.failure_category is None
        and run.evidence_accepted is True
        and run.decision_calls == 3
        and run.review_calls == 2
        and observation.check_calls == 1
        and run.environment_calls == 2
        and run.turns_used == 3
        and run.repairs_used == 2
        and run.verification_attempts == 1
        and run.accepted_verification_count == 1
        and run.completion_rejection_count == 2
    ):
        return MutationOutcome.SURVIVED
    if (
        run.stop_reason == "budget_exhausted"
        and run.failure_category == "completion_review_budget"
        and run.evidence_accepted is False
        and run.decision_calls == 2
        and run.review_calls == 2
        and observation.check_calls == 2
        and run.environment_calls == 3
        and run.turns_used == 2
        and run.repairs_used == 1
        and run.verification_attempts == 2
        and run.accepted_verification_count == 0
        and run.completion_rejection_count == 2
    ):
        return MutationOutcome.KILLED
    return MutationOutcome.INCONCLUSIVE


def _classify_failed_change(
    case: FailedChangeCase,
    observation: FailedChangeObservation,
) -> MutationOutcome:
    run = observation.run
    expected_ids = tuple(command.id for command in case.scenario.failed_commands)
    common = (
        observation.failed_change_ids == expected_ids
        and run.review_calls == 0
        and run.environment_calls == 4
        and run.repairs_used == 0
        and run.recoveries_used == 0
        and run.verification_attempts == 0
    )
    if (
        common
        and observation.stop_decision_consumed
        and run.stop_reason == "model_stopped"
        and run.failure_category == "model_blocked"
        and run.decision_calls == 4
        and run.turns_used == 4
    ):
        return MutationOutcome.SURVIVED
    if (
        common
        and not observation.stop_decision_consumed
        and run.stop_reason == "doom_loop"
        and run.failure_category == "model_reasoning"
        and run.decision_calls == 3
        and run.turns_used == 3
    ):
        return MutationOutcome.KILLED
    return MutationOutcome.INCONCLUSIVE


def _classify_max_repairs(observation: MaxRepairsObservation) -> MutationOutcome:
    run = observation.run
    common = (
        run.stop_reason == "budget_exhausted"
        and run.evidence_accepted is False
        and run.decision_calls == 2
        and run.review_calls == 0
        and run.environment_calls == 3
        and run.turns_used == 2
        and run.verification_attempts == 2
        and run.accepted_verification_count == 0
        and run.completion_rejection_count == 2
        and observation.failed_check_calls == 2
    )
    if common and run.failure_category == "model_protocol" and run.repairs_used == 2:
        return MutationOutcome.SURVIVED
    if common and run.failure_category == "completion_repair_budget" and run.repairs_used == 1:
        return MutationOutcome.KILLED
    return MutationOutcome.INCONCLUSIVE


def _classify_wall_time(
    case: WallTimeCase,
    observation: WallTimeObservation,
) -> MutationOutcome:
    run = observation.run
    scenario = case.scenario
    vulnerable_timeout = int(scenario.options.max_wall_time_sec - scenario.model_clock_target_sec)
    fixed_timeout = int(vulnerable_timeout - scenario.options.max_wall_time_sec * 0.1)
    common = (
        run.stop_reason == "model_stopped"
        and run.failure_category == f"model_{scenario.terminal_stop_category}"
        and run.evidence_accepted is None
        and run.decision_calls == 2
        and run.review_calls == 0
        and run.environment_calls == 2
        and run.turns_used == 2
        and run.repairs_used == 0
        and run.recoveries_used == 0
        and run.verification_attempts == 0
    )
    if (
        common
        and observation.work_command_timeout_sec == vulnerable_timeout
        and observation.work_command_return_code == 0
        and observation.work_command_failure is None
        and observation.final_clock_sec
        == scenario.model_clock_target_sec + scenario.command_duration_sec
        and observation.finalization_triggers == ()
    ):
        return MutationOutcome.SURVIVED
    if (
        common
        and observation.work_command_timeout_sec == fixed_timeout
        and observation.work_command_return_code is None
        and observation.work_command_failure == "timeout"
        and observation.final_clock_sec == scenario.model_clock_target_sec + fixed_timeout
        and observation.finalization_triggers == ("wall_clock",)
    ):
        return MutationOutcome.KILLED
    return MutationOutcome.INCONCLUSIVE


def _build_error_result(
    *,
    case: HistoricalCase,
    revision: RevisionSpec,
    archive_source_sha256: str | None,
    error: str,
) -> ErrorCaseResult:
    return ErrorCaseResult(
        kind="error",
        case_id=case.case_id,
        property_id=case.property.id,
        revision_role=revision.role,
        commit=revision.commit,
        tree=revision.tree,
        expected_outcome=revision.expected_outcome,
        archive_source_sha256=archive_source_sha256,
        error=error,
    )


def _report_verdict(
    results: list[CompletedCaseResult | ErrorCaseResult],
) -> Literal["passed", "failed", "error"]:
    if any(result.kind == "error" for result in results):
        return "error"
    if all(
        result.matches_expected for result in results if isinstance(result, CompletedCaseResult)
    ):
        return "passed"
    return "failed"


def _unique_revisions(manifest: HistoricalManifest) -> tuple[RevisionSpec, ...]:
    unique: dict[tuple[str, str], RevisionSpec] = {}
    for case in manifest.cases:
        for revision in case.revisions:
            unique.setdefault((revision.commit, revision.tree), revision)
    return tuple(unique.values())


def _archive_commit(repo_root: Path, archive_path: Path) -> str:
    try:
        with archive_path.open("rb") as stream:
            completed = subprocess.run(
                ("git", "get-tar-commit-id"),
                cwd=repo_root,
                stdin=stream,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HistoricalInfrastructureError(f"cannot inspect Git archive: {exc}") from exc
    if completed.returncode:
        raise HistoricalInfrastructureError(
            f"git get-tar-commit-id failed: {_bounded(completed.stderr)}"
        )
    return completed.stdout.strip()


def _file_binding(repo_root: Path, relative_path: Path) -> FileBinding:
    return FileBinding(
        path=relative_path.as_posix(),
        sha256=_hash_required_file(repo_root / relative_path, relative_path.as_posix()),
    )


def _validated_repo_root(path: Path) -> Path:
    candidate = path.expanduser().resolve()
    resolved = _run_git(candidate, "rev-parse", "--show-toplevel")
    root = Path(resolved).resolve()
    if root != candidate:
        raise HistoricalInfrastructureError(f"repo_root must be the Git top level: {root}")
    return root


def _run_git(repo_root: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ("git", "-C", str(repo_root), *arguments),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HistoricalInfrastructureError(f"cannot run git {' '.join(arguments)}: {exc}") from exc
    if completed.returncode:
        raise HistoricalInfrastructureError(
            f"git {' '.join(arguments)} failed: {_bounded(completed.stderr)}"
        )
    return completed.stdout.strip()


def _hash_required_file(path: Path, label: str) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise HistoricalInfrastructureError(f"cannot read {label}: {exc}") from exc


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode()


def _stable_error(exc: Exception, temporary_root: Path, repo_root: Path) -> str:
    value = f"{type(exc).__name__}: {exc}"
    value = value.replace(str(temporary_root), "<temporary>")
    value = value.replace(str(repo_root), "<repo>")
    return _bounded(value)


def _bounded(value: str, limit: int = 1_000) -> str:
    compact = " ".join(value.split())
    return compact if len(compact) <= limit else compact[: limit - 3] + "..."
