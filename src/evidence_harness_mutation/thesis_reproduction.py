from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, ValidationError, model_validator

from evidence_harness.evaluation import load_matrix
from evidence_harness_mutation.main_analysis_executable import (
    BoundMainAnalysisExecutable,
    load_main_analysis_executable,
)
from evidence_harness_mutation.main_analysis_protocol import (
    AUXILIARY_REPORT,
    MAIN_REPORT,
    RQ2_REPORT,
    RQ3_REPORT,
    RQ4_REPORT,
    TB21_REPORT,
    BoundMainAnalysisProtocol,
    MainAnalysisMethod,
    MainAnalysisOutcome,
    load_main_analysis_protocol,
    preflight_main_analysis_protocol,
)
from evidence_harness_mutation.main_analysis_report import (
    RQ2Report,
    RQ3Report,
    RQ4Report,
    build_main_analysis_report,
    build_rq2_report,
    build_rq3_report,
    build_rq4_report,
)
from evidence_harness_mutation.main_analysis_transfer_runtime import TRANSFER_RUN_ROOT
from evidence_harness_mutation.model import FrozenModel
from evidence_harness_mutation.prefixbench import PrefixBenchReadinessV2
from evidence_harness_mutation.prefixbench_test_campaign import (
    TEST_CAMPAIGN,
    TEST_CANONICAL,
    TEST_MATRIX,
    TEST_READINESS,
    TEST_RUN_ROOT,
    PrefixBenchTestCampaignReport,
    check_prefixbench_test_campaign,
    preflight_prefixbench_test_campaign,
)
from evidence_harness_mutation.thesis_tables import (
    TABLE_BUNDLE,
    TABLE_MARKDOWN,
    check_thesis_tables,
)


class ReproductionState(StrEnum):
    PRE_COLLECTION = "pre_collection"
    PARTIAL_INVALID = "partial_invalid"
    COMPLETE = "complete"


class RawInputGroup(StrEnum):
    HELD_OUT_COLLECTION = "held_out_collection"
    RQ2_METHOD_COMPARISON = "rq2_method_comparison"
    RQ3_REDUCER_COMPARISON = "rq3_reducer_comparison"
    CROSS_HARNESS_COLLECTION = "cross_harness_collection"


class RawInputMode(StrEnum):
    ARTIFACT_ONLY = "artifact_only"
    FULL_REBUILD = "full_rebuild"


class RawInputDeclaration(FrozenModel):
    group: RawInputGroup
    mode: RawInputMode | None
    expected_files: int = Field(ge=1)
    present_files: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        if self.present_files > self.expected_files:
            raise ValueError("present raw files exceed the expected count")
        if self.mode is RawInputMode.ARTIFACT_ONLY and self.present_files != 0:
            raise ValueError("artifact-only raw input must be absent")
        if self.mode is RawInputMode.FULL_REBUILD and self.present_files != self.expected_files:
            raise ValueError("full-rebuild raw input must be complete")
        return self


class ThesisReproductionResult(FrozenModel):
    schema_version: Literal[1] = 1
    state: ReproductionState
    protocol_commit: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    executable_commit: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")
    raw_inputs: tuple[RawInputDeclaration, ...] = Field(max_length=4)
    errors: tuple[str, ...]

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        if self.raw_inputs and tuple(item.group for item in self.raw_inputs) != tuple(
            RawInputGroup
        ):
            raise ValueError("raw input declarations are out of protocol order")
        for item in self.raw_inputs:
            if item.expected_files != _EXPECTED_RAW_FILES[item.group]:
                raise ValueError(f"{item.group.value} raw input count has changed")
        if self.state is ReproductionState.PRE_COLLECTION:
            if self.errors or len(self.raw_inputs) != 4:
                raise ValueError("pre-collection result must have four valid raw declarations")
            if any(
                item.mode is not RawInputMode.ARTIFACT_ONLY or item.present_files != 0
                for item in self.raw_inputs
            ):
                raise ValueError("pre-collection raw inputs must be absent")
        if self.state is ReproductionState.COMPLETE:
            if self.errors or len(self.raw_inputs) != 4:
                raise ValueError("complete result must have four valid raw declarations")
            if any(item.mode is None for item in self.raw_inputs):
                raise ValueError("complete result cannot contain a partial raw input")
        if self.state is ReproductionState.PARTIAL_INVALID and not self.errors:
            raise ValueError("partial-invalid result must explain its failure")
        if self.state is not ReproductionState.PARTIAL_INVALID and (
            self.protocol_commit is None or self.executable_commit is None
        ):
            raise ValueError("valid result must identify both frozen commits")
        return self

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.model_dump(mode="json"))


@dataclass(frozen=True, slots=True)
class _FrozenContext:
    root: Path
    protocol: BoundMainAnalysisProtocol
    executable: BoundMainAnalysisExecutable


@dataclass(frozen=True, slots=True)
class _OutcomeSnapshot:
    canonical_data: bytes
    readiness: PrefixBenchReadinessV2
    campaign: PrefixBenchTestCampaignReport
    rq2_data: bytes
    rq2: RQ2Report
    rq3_data: bytes
    rq3: RQ3Report
    rq4_data: bytes
    rq4: RQ4Report


@dataclass(frozen=True, slots=True)
class _RawManifest:
    group: RawInputGroup
    anchor: Path
    paths: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class _ArtifactTopology:
    regular: tuple[Path, ...]
    missing: tuple[Path, ...]
    invalid: tuple[Path, ...]

    @property
    def all_absent(self) -> bool:
        return not self.regular and not self.invalid

    @property
    def complete(self) -> bool:
        return not self.missing and not self.invalid


_REQUIRED_ARTIFACTS = (
    TEST_CANONICAL,
    TEST_READINESS,
    TEST_CAMPAIGN,
    RQ2_REPORT,
    RQ3_REPORT,
    RQ4_REPORT,
    MAIN_REPORT,
    TABLE_BUNDLE,
    TABLE_MARKDOWN,
)
_START_MARKERS = (
    *_REQUIRED_ARTIFACTS,
    TEST_RUN_ROOT,
    TRANSFER_RUN_ROOT,
    TB21_REPORT,
    AUXILIARY_REPORT,
)
_EXTRA_PRECOLLECTION_HISTORY_PATHS = (
    TRANSFER_RUN_ROOT,
    TABLE_BUNDLE,
    TABLE_MARKDOWN,
)
_EXPECTED_RAW_FILES = {
    RawInputGroup.HELD_OUT_COLLECTION: 185,
    RawInputGroup.RQ2_METHOD_COMPARISON: 61,
    RawInputGroup.RQ3_REDUCER_COMPARISON: 61,
    RawInputGroup.CROSS_HARNESS_COLLECTION: 20,
}


def check_thesis_reproduction(project_root: Path) -> ThesisReproductionResult:
    try:
        root = _project_root(project_root)
        frozen = _pass_freeze_gate(root)
    except (OSError, TypeError, ValueError) as exc:
        return _invalid_result((f"freeze gate failed: {exc}",))

    protocol_commit = frozen.protocol.preregistration_commit
    executable_commit = frozen.executable.executable_commit
    topology = _artifact_topology(root)

    if topology.all_absent:
        started = tuple(path for path in _START_MARKERS if _entry_present(root / path))
        if started:
            return _invalid_result(
                (
                    "downstream work exists without the complete artifact set: "
                    + ", ".join(path.as_posix() for path in started),
                ),
                frozen=frozen,
            )
        errors = _check_precollection(frozen)
        if errors:
            return _invalid_result(errors, frozen=frozen)
        return ThesisReproductionResult(
            state=ReproductionState.PRE_COLLECTION,
            protocol_commit=protocol_commit,
            executable_commit=executable_commit,
            raw_inputs=_precollection_raw_inputs(),
            errors=(),
        )

    if not topology.complete:
        errors = _topology_errors(topology)
        return _invalid_result(errors, frozen=frozen)

    try:
        snapshot = _load_outcome_snapshot(frozen)
        structure_errors = _validate_outcome_structure(frozen, snapshot)
        if structure_errors:
            return _invalid_result(structure_errors, frozen=frozen)
        manifests = _raw_manifests(frozen, snapshot)
        raw_inputs, raw_errors = _classify_raw_inputs(frozen, manifests)
    except (OSError, TypeError, ValueError) as exc:
        return _invalid_result(
            (f"cannot load the complete artifact set: {exc}",),
            frozen=frozen,
        )

    if raw_errors:
        return _invalid_result(raw_errors, frozen=frozen, raw_inputs=raw_inputs)

    errors = _check_complete_artifacts(frozen, snapshot, raw_inputs)
    if errors:
        return _invalid_result(errors, frozen=frozen, raw_inputs=raw_inputs)
    return ThesisReproductionResult(
        state=ReproductionState.COMPLETE,
        protocol_commit=protocol_commit,
        executable_commit=executable_commit,
        raw_inputs=raw_inputs,
        errors=(),
    )


def _pass_freeze_gate(root: Path) -> _FrozenContext:
    protocol = load_main_analysis_protocol(root)
    executable = load_main_analysis_executable(root)
    if executable.spec.protocol != protocol.file:
        raise ValueError("executable protocol binding differs from the main protocol")
    if executable.spec.protocol_commit != protocol.preregistration_commit:
        raise ValueError("executable protocol commit differs from the main protocol")
    if executable.spec.protected_sources != protocol.spec.protected_sources:
        raise ValueError("executable protected sources differ from the main protocol")
    return _FrozenContext(root=root, protocol=protocol, executable=executable)


def _check_precollection(frozen: _FrozenContext) -> tuple[str, ...]:
    errors: list[str] = []
    try:
        preflight_main_analysis_protocol(frozen.root)
    except (OSError, ValueError) as exc:
        errors.append(f"main protocol preflight failed: {exc}")
    try:
        preflight_prefixbench_test_campaign(frozen.root)
    except (OSError, ValueError) as exc:
        errors.append(f"held-out preflight failed: {exc}")
    historical = tuple(
        path
        for path in _EXTRA_PRECOLLECTION_HISTORY_PATHS
        if _path_has_git_history(frozen.root, path)
    )
    if historical:
        errors.append(
            "downstream path already exists in Git history: "
            + ", ".join(path.as_posix() for path in historical)
        )
    return tuple(errors)


def _artifact_topology(root: Path) -> _ArtifactTopology:
    regular: list[Path] = []
    missing: list[Path] = []
    invalid: list[Path] = []
    for relative in _REQUIRED_ARTIFACTS:
        state = _regular_file_state(root / relative, root)
        if state == "regular":
            regular.append(relative)
        elif state == "missing":
            missing.append(relative)
        else:
            invalid.append(relative)
    return _ArtifactTopology(
        regular=tuple(regular),
        missing=tuple(missing),
        invalid=tuple(invalid),
    )


def _topology_errors(topology: _ArtifactTopology) -> tuple[str, ...]:
    errors: list[str] = []
    if topology.missing:
        errors.append(
            "required thesis artifacts are missing: "
            + ", ".join(path.as_posix() for path in topology.missing)
        )
    if topology.invalid:
        errors.append(
            "required thesis artifacts are not regular files: "
            + ", ".join(path.as_posix() for path in topology.invalid)
        )
    return tuple(errors)


def _load_outcome_snapshot(frozen: _FrozenContext) -> _OutcomeSnapshot:
    root = frozen.root
    canonical_data = _read_canonical_object(root, TEST_CANONICAL, "test canonical")
    _, readiness = _read_canonical_model(
        root,
        TEST_READINESS,
        PrefixBenchReadinessV2,
        "test readiness",
    )
    _, campaign = _read_canonical_model(
        root,
        TEST_CAMPAIGN,
        PrefixBenchTestCampaignReport,
        "test campaign",
    )
    rq2_data, rq2 = _read_canonical_model(root, RQ2_REPORT, RQ2Report, "RQ2 report")
    rq3_data, rq3 = _read_canonical_model(root, RQ3_REPORT, RQ3Report, "RQ3 report")
    rq4_data, rq4 = _read_canonical_model(root, RQ4_REPORT, RQ4Report, "RQ4 report")
    return _OutcomeSnapshot(
        canonical_data=canonical_data,
        readiness=readiness,
        campaign=campaign,
        rq2_data=rq2_data,
        rq2=rq2,
        rq3_data=rq3_data,
        rq3=rq3,
        rq4_data=rq4_data,
        rq4=rq4,
    )


def _validate_outcome_structure(
    frozen: _FrozenContext,
    snapshot: _OutcomeSnapshot,
) -> tuple[str, ...]:
    errors: list[str] = []
    campaign_tasks = tuple(
        (task.name, task.task_identity_sha256) for task in snapshot.campaign.tasks
    )
    rq2_tasks = tuple((task.task_name, task.task_identity_sha256) for task in snapshot.rq2.tasks)
    if rq2_tasks != campaign_tasks:
        errors.append("RQ2 task order or identity differs from the held-out campaign")

    expected_cases: list[tuple[str, int]] = []
    for task in snapshot.rq2.tasks:
        state_aware = next(
            method for method in task.methods if method.method is MainAnalysisMethod.STATE_AWARE
        )
        case_ordinal = 0
        for outcome in state_aware.outcomes:
            if outcome.outcome is MainAnalysisOutcome.TARGET_VIOLATION:
                case_ordinal += 1
                expected_cases.append((task.task_identity_sha256, case_ordinal))
    actual_cases = tuple(
        (comparison.task_identity_sha256, comparison.case_ordinal)
        for comparison in snapshot.rq3.comparisons
    )
    if actual_cases != tuple(expected_cases):
        errors.append("RQ3 cases differ from the RQ2 state-aware target violations")

    cohort_tasks = tuple(
        (task.index, task.instance_id) for task in frozen.executable.spec.cohort.tasks
    )
    rq4_tasks = tuple((task.index, task.instance_id) for task in snapshot.rq4.tasks)
    if rq4_tasks != cohort_tasks:
        errors.append("RQ4 task order or identity differs from the frozen cohort")
    if not snapshot.rq4.analysis.collection_complete:
        errors.append("RQ4 collection is incomplete")
    for rq4_task in snapshot.rq4.tasks:
        expected = (
            TRANSFER_RUN_ROOT / rq4_task.instance_id / f"{rq4_task.instance_id}.traj.json"
        ).as_posix()
        if rq4_task.trajectory is None or rq4_task.trajectory.path != expected:
            errors.append(f"RQ4 trajectory binding is incomplete: task {rq4_task.index}")
    return tuple(errors)


def _raw_manifests(
    frozen: _FrozenContext,
    snapshot: _OutcomeSnapshot,
) -> tuple[_RawManifest, ...]:
    root = frozen.root
    held_out_bindings = (
        *(
            binding
            for task in snapshot.readiness.tasks
            for binding in (
                task.sources.result,
                task.sources.config,
            )
        ),
        *(task.journal for task in snapshot.campaign.tasks),
        snapshot.campaign.sources.collection.run_config,
        snapshot.campaign.sources.collection.progress,
    )
    journal_bindings = tuple(task.journal for task in snapshot.campaign.tasks)
    trajectory_bindings = tuple(
        task.trajectory for task in snapshot.rq4.tasks if task.trajectory is not None
    )
    return (
        _raw_manifest(
            root,
            RawInputGroup.HELD_OUT_COLLECTION,
            TEST_RUN_ROOT,
            tuple(binding.path for binding in held_out_bindings),
        ),
        _raw_manifest(
            root,
            RawInputGroup.RQ2_METHOD_COMPARISON,
            TEST_RUN_ROOT,
            tuple(binding.path for binding in journal_bindings),
        ),
        _raw_manifest(
            root,
            RawInputGroup.RQ3_REDUCER_COMPARISON,
            TEST_RUN_ROOT,
            tuple(binding.path for binding in journal_bindings),
        ),
        _raw_manifest(
            root,
            RawInputGroup.CROSS_HARNESS_COLLECTION,
            TRANSFER_RUN_ROOT,
            tuple(binding.path for binding in trajectory_bindings),
        ),
    )


def _raw_manifest(
    root: Path,
    group: RawInputGroup,
    anchor: Path,
    raw_paths: tuple[str, ...],
) -> _RawManifest:
    expected = _EXPECTED_RAW_FILES[group]
    if len(raw_paths) != expected or len(set(raw_paths)) != expected:
        raise ValueError(
            f"{group.value} raw manifest has {len(raw_paths)} paths; expected {expected}"
        )
    paths = tuple(_bound_path(root, raw, anchor) for raw in raw_paths)
    return _RawManifest(group=group, anchor=root / anchor, paths=paths)


def _classify_raw_inputs(
    frozen: _FrozenContext,
    manifests: tuple[_RawManifest, ...],
) -> tuple[tuple[RawInputDeclaration, ...], tuple[str, ...]]:
    declarations: list[RawInputDeclaration] = []
    errors: list[str] = []
    for manifest in manifests:
        anchor_state = _directory_state(manifest.anchor, frozen.root)
        states = tuple(_regular_file_state(path, frozen.root) for path in manifest.paths)
        present = sum(state != "missing" for state in states)
        if anchor_state == "missing" and not present:
            mode: RawInputMode | None = RawInputMode.ARTIFACT_ONLY
        elif anchor_state == "directory" and all(state == "regular" for state in states):
            mode = RawInputMode.FULL_REBUILD
        else:
            mode = None
            errors.append(
                f"raw input group {manifest.group.value} is partial or invalid: "
                f"{present}/{len(states)} paths present"
            )
        declarations.append(
            RawInputDeclaration(
                group=manifest.group,
                mode=mode,
                expected_files=len(states),
                present_files=present,
            )
        )
    return tuple(declarations), tuple(errors)


def _check_complete_artifacts(
    frozen: _FrozenContext,
    snapshot: _OutcomeSnapshot,
    raw_inputs: tuple[RawInputDeclaration, ...],
) -> tuple[str, ...]:
    root = frozen.root
    modes = {item.group: item.mode for item in raw_inputs}
    errors = _prefixed("held-out", check_prefixbench_test_campaign(root))
    if errors:
        return errors

    if modes[RawInputGroup.HELD_OUT_COLLECTION] is RawInputMode.FULL_REBUILD:
        errors += _check_held_out_canonical_rebuild(root, snapshot.canonical_data)
    if modes[RawInputGroup.RQ2_METHOD_COMPARISON] is RawInputMode.FULL_REBUILD:
        errors += _check_report_rebuild("RQ2", root, snapshot.rq2_data, build_rq2_report)
    if errors:
        return errors
    if modes[RawInputGroup.RQ3_REDUCER_COMPARISON] is RawInputMode.FULL_REBUILD:
        errors += _check_report_rebuild("RQ3", root, snapshot.rq3_data, build_rq3_report)
    if errors:
        return errors
    if modes[RawInputGroup.CROSS_HARNESS_COLLECTION] is RawInputMode.FULL_REBUILD:
        errors += _check_report_rebuild("RQ4", root, snapshot.rq4_data, build_rq4_report)
    if errors:
        return errors

    errors += _check_main_report_rebuild(root)
    if errors:
        return errors
    return errors + _prefixed("tables", check_thesis_tables(root))


def _check_held_out_canonical_rebuild(
    root: Path,
    actual: bytes,
) -> tuple[str, ...]:
    try:
        from scripts import collect_evaluation_results as collector

        if collector.PROJECT_ROOT.resolve() != root:
            raise ValueError("collector project root differs from the checked repository")
        matrix_path = root / TEST_MATRIX
        matrix = load_matrix(matrix_path)
        latest = collector.collect_latest_results(
            [root / TEST_RUN_ROOT],
            matrix,
            collection_profile_name="prefixbench-v1",
        )
        document = collector.build_manifest(
            matrix,
            matrix_path,
            latest,
            collection_profile_name="prefixbench-v1",
        )
        if _canonical_json(document) != actual:
            raise ValueError("test canonical does not match a full rebuild")
    except (OSError, TypeError, ValueError) as exc:
        return (f"held-out canonical rebuild failed: {exc}",)
    return ()


def _check_report_rebuild(
    label: str,
    root: Path,
    actual: bytes,
    builder: Callable[[Path], BaseModel],
) -> tuple[str, ...]:
    try:
        expected = builder(root)
        canonical = getattr(expected, "canonical_bytes", None)
        if not callable(canonical) or canonical() != actual:
            raise ValueError(f"{label} report does not match a full rebuild")
    except (OSError, TypeError, ValueError) as exc:
        return (f"{label} full rebuild failed: {exc}",)
    return ()


def _check_main_report_rebuild(root: Path) -> tuple[str, ...]:
    try:
        actual = _read_regular_file(root, MAIN_REPORT, "main report")
        expected = build_main_analysis_report(root).canonical_bytes()
        if expected != actual:
            raise ValueError("main report does not match its canonical inputs")
    except (OSError, TypeError, ValueError) as exc:
        return (f"main report rebuild failed: {exc}",)
    return ()


def _read_canonical_model[T: BaseModel](
    root: Path,
    relative: Path,
    model: type[T],
    label: str,
) -> tuple[bytes, T]:
    data = _read_regular_file(root, relative, label)
    try:
        value = model.model_validate_json(data)
    except ValidationError as exc:
        raise ValueError(f"invalid {label}") from exc
    canonical = getattr(value, "canonical_bytes", None)
    if not callable(canonical) or canonical() != data:
        raise ValueError(f"{label} is not canonical JSON")
    return data, value


def _read_canonical_object(root: Path, relative: Path, label: str) -> bytes:
    data = _read_regular_file(root, relative, label)
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid {label}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    if data != _canonical_json(value):
        raise ValueError(f"{label} is not canonical JSON")
    return data


def _read_regular_file(root: Path, relative: Path, label: str) -> bytes:
    path = _bound_path(root, relative.as_posix(), Path("."))
    if _regular_file_state(path, root) != "regular":
        raise ValueError(f"{label} must be a regular file: {relative}")
    return path.read_bytes()


def _bound_path(root: Path, raw: str, anchor: Path) -> Path:
    pure = PurePosixPath(raw)
    anchor_pure = PurePosixPath(anchor.as_posix())
    if (
        pure.is_absolute()
        or pure.as_posix() != raw
        or any(part in {"", ".", ".."} for part in pure.parts)
        or (anchor != Path(".") and not pure.is_relative_to(anchor_pure))
    ):
        raise ValueError(f"raw input path is outside {anchor.as_posix()}: {raw}")
    return root.joinpath(*pure.parts)


def _regular_file_state(path: Path, root: Path) -> Literal["missing", "regular", "invalid"]:
    if _has_symlink_component(path, root):
        return "invalid"
    if not path.exists():
        return "missing"
    return "regular" if path.is_file() else "invalid"


def _directory_state(path: Path, root: Path) -> Literal["missing", "directory", "invalid"]:
    if _has_symlink_component(path, root):
        return "invalid"
    if not path.exists():
        return "missing"
    return "directory" if path.is_dir() else "invalid"


def _has_symlink_component(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            return True
        if not current.exists():
            return False
    return False


def _entry_present(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _path_has_git_history(root: Path, path: Path) -> bool:
    result = subprocess.run(
        ("git", "log", "--all", "--format=%H", "--", path.as_posix()),
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    return bool(result.stdout.strip())


def _precollection_raw_inputs() -> tuple[RawInputDeclaration, ...]:
    return tuple(
        RawInputDeclaration(
            group=group,
            mode=RawInputMode.ARTIFACT_ONLY,
            expected_files=_EXPECTED_RAW_FILES[group],
            present_files=0,
        )
        for group in RawInputGroup
    )


def _prefixed(label: str, errors: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(f"{label}: {error}" for error in errors)


def _invalid_result(
    errors: tuple[str, ...],
    *,
    frozen: _FrozenContext | None = None,
    raw_inputs: tuple[RawInputDeclaration, ...] = (),
) -> ThesisReproductionResult:
    return ThesisReproductionResult(
        state=ReproductionState.PARTIAL_INVALID,
        protocol_commit=(frozen.protocol.preregistration_commit if frozen is not None else None),
        executable_commit=(frozen.executable.executable_commit if frozen is not None else None),
        raw_inputs=raw_inputs,
        errors=errors,
    )


def _canonical_json(value: Any) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode()


def _project_root(project_root: Path) -> Path:
    root = project_root.resolve()
    if not root.is_dir():
        raise ValueError(f"project root is not a directory: {root}")
    return root
