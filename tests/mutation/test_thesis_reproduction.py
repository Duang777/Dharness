from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from evidence_harness_mutation import thesis_reproduction as reproduction
from evidence_harness_mutation.main_analysis_protocol import (
    MainAnalysisMethod,
    MainAnalysisOutcome,
)


def _frozen(root: Path) -> Any:
    cohort = tuple(
        SimpleNamespace(index=index, instance_id=f"task-{index}") for index in range(1, 21)
    )
    return SimpleNamespace(
        root=root,
        protocol=SimpleNamespace(preregistration_commit="a" * 40),
        executable=SimpleNamespace(
            executable_commit="b" * 40,
            spec=SimpleNamespace(cohort=SimpleNamespace(tasks=cohort)),
        ),
    )


def _write_required_artifacts(root: Path) -> None:
    for relative in reproduction._REQUIRED_ARTIFACTS:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")


def _manifests(
    root: Path,
) -> tuple[reproduction._RawManifest, ...]:
    manifests = []
    for ordinal, group in enumerate(reproduction.RawInputGroup, start=1):
        anchor = root / "runs" / f"group-{ordinal}"
        files = reproduction._EXPECTED_RAW_FILES[group]
        manifests.append(
            reproduction._RawManifest(
                group=group,
                anchor=anchor,
                paths=tuple(anchor / f"source-{index}" for index in range(files)),
            )
        )
    return tuple(manifests)


def _patch_complete_prefix(
    monkeypatch: pytest.MonkeyPatch,
    root: Path,
    *,
    manifests: tuple[reproduction._RawManifest, ...],
) -> Any:
    frozen = _frozen(root)
    snapshot = SimpleNamespace()
    monkeypatch.setattr(reproduction, "_pass_freeze_gate", lambda _root: frozen)
    monkeypatch.setattr(reproduction, "_load_outcome_snapshot", lambda _frozen: snapshot)
    monkeypatch.setattr(
        reproduction,
        "_validate_outcome_structure",
        lambda _frozen, _snapshot: (),
    )
    monkeypatch.setattr(
        reproduction,
        "_raw_manifests",
        lambda _frozen, _snapshot: manifests,
    )
    return snapshot


def test_gate_failure_does_not_probe_or_read_outcomes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        reproduction,
        "_pass_freeze_gate",
        lambda _root: (_ for _ in ()).throw(ValueError("frozen source changed")),
    )
    monkeypatch.setattr(
        reproduction,
        "_artifact_topology",
        lambda _root: pytest.fail("outcome topology was inspected before the gate"),
    )

    result = reproduction.check_thesis_reproduction(tmp_path)

    assert result.state is reproduction.ReproductionState.PARTIAL_INVALID
    assert result.raw_inputs == ()
    assert result.errors == ("freeze gate failed: frozen source changed",)


def test_precollection_has_four_artifact_only_declarations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reproduction, "_pass_freeze_gate", lambda _root: _frozen(tmp_path))
    monkeypatch.setattr(reproduction, "_check_precollection", lambda _frozen: ())

    result = reproduction.check_thesis_reproduction(tmp_path)

    assert result.state is reproduction.ReproductionState.PRE_COLLECTION
    assert tuple(item.group for item in result.raw_inputs) == tuple(reproduction.RawInputGroup)
    assert (
        tuple(item.mode for item in result.raw_inputs)
        == (reproduction.RawInputMode.ARTIFACT_ONLY,) * 4
    )
    assert tuple(item.expected_files for item in result.raw_inputs) == (185, 61, 61, 20)
    assert all(item.present_files == 0 for item in result.raw_inputs)


def test_raw_root_without_artifacts_is_partial_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reproduction, "_pass_freeze_gate", lambda _root: _frozen(tmp_path))
    monkeypatch.setattr(
        reproduction,
        "_check_precollection",
        lambda _frozen: pytest.fail("preflight must not accept a started package"),
    )
    (tmp_path / reproduction.TEST_RUN_ROOT).mkdir(parents=True)

    result = reproduction.check_thesis_reproduction(tmp_path)

    assert result.state is reproduction.ReproductionState.PARTIAL_INVALID
    assert "without the complete artifact set" in result.errors[0]


def test_partial_artifact_set_is_rejected_before_reading_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(reproduction, "_pass_freeze_gate", lambda _root: _frozen(tmp_path))
    first = tmp_path / reproduction._REQUIRED_ARTIFACTS[0]
    first.parent.mkdir(parents=True)
    first.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(
        reproduction,
        "_load_outcome_snapshot",
        lambda _frozen: pytest.fail("partial artifact set was read"),
    )

    result = reproduction.check_thesis_reproduction(tmp_path)

    assert result.state is reproduction.ReproductionState.PARTIAL_INVALID
    assert result.raw_inputs == ()
    assert result.errors[0].startswith("required thesis artifacts are missing:")


def test_complete_artifact_only_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_required_artifacts(tmp_path)
    manifests = _manifests(tmp_path)
    _patch_complete_prefix(monkeypatch, tmp_path, manifests=manifests)
    observed: list[tuple[reproduction.RawInputMode | None, ...]] = []
    monkeypatch.setattr(
        reproduction,
        "_check_complete_artifacts",
        lambda _frozen, _snapshot, raw: observed.append(tuple(item.mode for item in raw)) or (),
    )

    result = reproduction.check_thesis_reproduction(tmp_path)

    assert result.state is reproduction.ReproductionState.COMPLETE
    assert (
        tuple(item.mode for item in result.raw_inputs)
        == (reproduction.RawInputMode.ARTIFACT_ONLY,) * 4
    )
    assert observed == [
        (
            reproduction.RawInputMode.ARTIFACT_ONLY,
            reproduction.RawInputMode.ARTIFACT_ONLY,
            reproduction.RawInputMode.ARTIFACT_ONLY,
            reproduction.RawInputMode.ARTIFACT_ONLY,
        )
    ]


def test_complete_full_rebuild_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_required_artifacts(tmp_path)
    manifests = _manifests(tmp_path)
    for manifest in manifests:
        manifest.anchor.mkdir(parents=True, exist_ok=True)
        for path in manifest.paths:
            path.write_text("raw\n", encoding="utf-8")
    snapshot = _patch_complete_prefix(monkeypatch, tmp_path, manifests=manifests)
    monkeypatch.setattr(
        reproduction,
        "_check_complete_artifacts",
        lambda frozen, actual, _raw: (
            ()
            if (frozen.root, actual) == (tmp_path, snapshot)
            else pytest.fail("unexpected complete-package inputs")
        ),
    )

    result = reproduction.check_thesis_reproduction(tmp_path)

    assert result.state is reproduction.ReproductionState.COMPLETE
    assert (
        tuple(item.mode for item in result.raw_inputs)
        == (reproduction.RawInputMode.FULL_REBUILD,) * 4
    )


def test_partial_raw_group_is_rejected_before_rebuild(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_required_artifacts(tmp_path)
    manifests = _manifests(tmp_path)
    partial = manifests[0]
    partial.anchor.mkdir(parents=True)
    partial.paths[0].write_text("raw\n", encoding="utf-8")
    _patch_complete_prefix(monkeypatch, tmp_path, manifests=manifests)
    monkeypatch.setattr(
        reproduction,
        "_check_complete_artifacts",
        lambda *_args: pytest.fail("partial raw input reached a rebuild"),
    )

    result = reproduction.check_thesis_reproduction(tmp_path)

    assert result.state is reproduction.ReproductionState.PARTIAL_INVALID
    assert result.raw_inputs[0].mode is None
    assert result.raw_inputs[0].present_files == 1
    assert "held_out_collection is partial or invalid" in result.errors[0]


def test_symlinked_raw_file_is_invalid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_required_artifacts(tmp_path)
    manifests = _manifests(tmp_path)
    first = manifests[0]
    first.anchor.mkdir(parents=True)
    target = tmp_path / "target"
    target.write_text("raw\n", encoding="utf-8")
    first.paths[0].symlink_to(target)
    _patch_complete_prefix(monkeypatch, tmp_path, manifests=manifests)
    monkeypatch.setattr(
        reproduction,
        "_check_complete_artifacts",
        lambda *_args: pytest.fail("invalid raw input reached a rebuild"),
    )

    result = reproduction.check_thesis_reproduction(tmp_path)

    assert result.state is reproduction.ReproductionState.PARTIAL_INVALID
    assert result.raw_inputs[0].mode is None


def test_complete_checker_runs_only_selected_full_rebuilds(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    frozen = _frozen(tmp_path)
    snapshot = SimpleNamespace(
        canonical_data=b"canonical",
        rq2_data=b"rq2",
        rq3_data=b"rq3",
        rq4_data=b"rq4",
    )
    modes = (
        reproduction.RawInputDeclaration(
            group=reproduction.RawInputGroup.HELD_OUT_COLLECTION,
            mode=reproduction.RawInputMode.FULL_REBUILD,
            expected_files=1,
            present_files=1,
        ),
        reproduction.RawInputDeclaration(
            group=reproduction.RawInputGroup.RQ2_METHOD_COMPARISON,
            mode=reproduction.RawInputMode.FULL_REBUILD,
            expected_files=1,
            present_files=1,
        ),
        reproduction.RawInputDeclaration(
            group=reproduction.RawInputGroup.RQ3_REDUCER_COMPARISON,
            mode=reproduction.RawInputMode.ARTIFACT_ONLY,
            expected_files=1,
            present_files=0,
        ),
        reproduction.RawInputDeclaration(
            group=reproduction.RawInputGroup.CROSS_HARNESS_COLLECTION,
            mode=reproduction.RawInputMode.FULL_REBUILD,
            expected_files=1,
            present_files=1,
        ),
    )
    monkeypatch.setattr(
        reproduction,
        "check_prefixbench_test_campaign",
        lambda _root: calls.append("campaign") or (),
    )
    monkeypatch.setattr(
        reproduction,
        "_check_held_out_canonical_rebuild",
        lambda _root, _data: calls.append("canonical") or (),
    )
    monkeypatch.setattr(
        reproduction,
        "_check_report_rebuild",
        lambda label, _root, _data, _builder: calls.append(label) or (),
    )
    monkeypatch.setattr(
        reproduction,
        "_check_main_report_rebuild",
        lambda _root: calls.append("main") or (),
    )
    monkeypatch.setattr(
        reproduction,
        "check_thesis_tables",
        lambda _root: calls.append("tables") or (),
    )

    assert reproduction._check_complete_artifacts(frozen, snapshot, modes) == ()
    assert calls == ["campaign", "canonical", "RQ2", "RQ4", "main", "tables"]


def test_raw_manifests_come_from_the_fixed_canonical_bindings(tmp_path: Path) -> None:
    frozen = _frozen(tmp_path)
    readiness_tasks = tuple(
        SimpleNamespace(
            sources=SimpleNamespace(
                result=SimpleNamespace(
                    path=(reproduction.TEST_RUN_ROOT / f"task-{index}" / "result.json").as_posix()
                ),
                config=SimpleNamespace(
                    path=(reproduction.TEST_RUN_ROOT / f"task-{index}" / "config.json").as_posix()
                ),
            )
        )
        for index in range(1, 62)
    )
    campaign_tasks = tuple(
        SimpleNamespace(
            journal=SimpleNamespace(
                path=(
                    reproduction.TEST_RUN_ROOT
                    / f"task-{index}"
                    / "agent/evidence-harness/events.jsonl"
                ).as_posix()
            )
        )
        for index in range(1, 62)
    )
    rq4_tasks = tuple(
        SimpleNamespace(
            trajectory=SimpleNamespace(
                path=(
                    reproduction.TRANSFER_RUN_ROOT / f"task-{index}" / f"task-{index}.traj.json"
                ).as_posix()
            )
        )
        for index in range(1, 21)
    )
    snapshot = SimpleNamespace(
        readiness=SimpleNamespace(tasks=readiness_tasks),
        campaign=SimpleNamespace(
            tasks=campaign_tasks,
            sources=SimpleNamespace(
                collection=SimpleNamespace(
                    run_config=SimpleNamespace(
                        path=(reproduction.TEST_RUN_ROOT / "run-config.json").as_posix()
                    ),
                    progress=SimpleNamespace(
                        path=(reproduction.TEST_RUN_ROOT / "progress.jsonl").as_posix()
                    ),
                )
            ),
        ),
        rq4=SimpleNamespace(tasks=rq4_tasks),
    )

    manifests = reproduction._raw_manifests(frozen, snapshot)

    assert tuple(len(manifest.paths) for manifest in manifests) == (185, 61, 61, 20)
    assert tuple(manifest.group for manifest in manifests) == tuple(reproduction.RawInputGroup)
    assert all(path.is_relative_to(tmp_path) for manifest in manifests for path in manifest.paths)


def test_held_out_full_rebuild_compares_canonical_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts import collect_evaluation_results as collector

    matrix = object()
    latest = {"task": object()}
    document = {"schema_version": 2, "tasks": []}
    monkeypatch.setattr(collector, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(reproduction, "load_matrix", lambda _path: matrix)
    monkeypatch.setattr(
        collector,
        "collect_latest_results",
        lambda run_dirs, actual_matrix, *, collection_profile_name: (
            latest
            if (
                run_dirs == [tmp_path / reproduction.TEST_RUN_ROOT]
                and actual_matrix is matrix
                and collection_profile_name == "prefixbench-v1"
            )
            else pytest.fail("collector received unexpected inputs")
        ),
    )
    monkeypatch.setattr(
        collector,
        "build_manifest",
        lambda actual_matrix, matrix_path, actual_latest, *, collection_profile_name: (
            document
            if (
                actual_matrix is matrix
                and matrix_path == tmp_path / reproduction.TEST_MATRIX
                and actual_latest is latest
                and collection_profile_name == "prefixbench-v1"
            )
            else pytest.fail("manifest builder received unexpected inputs")
        ),
    )

    assert (
        reproduction._check_held_out_canonical_rebuild(
            tmp_path,
            reproduction._canonical_json(document),
        )
        == ()
    )
    assert (
        "does not match a full rebuild"
        in (reproduction._check_held_out_canonical_rebuild(tmp_path, b"{}\n")[0])
    )


def test_outcome_structure_requires_exact_cross_artifact_membership(tmp_path: Path) -> None:
    frozen = _frozen(tmp_path)
    state_aware = SimpleNamespace(
        method=MainAnalysisMethod.STATE_AWARE,
        outcomes=(
            SimpleNamespace(outcome=MainAnalysisOutcome.TARGET_VIOLATION),
            SimpleNamespace(outcome=MainAnalysisOutcome.ORACLE_EQUIVALENT),
        ),
    )
    campaign_tasks = tuple(
        SimpleNamespace(name=f"case-{index}", task_identity_sha256=f"{index:064x}")
        for index in range(1, 62)
    )
    rq2_tasks = tuple(
        SimpleNamespace(
            task_name=task.name,
            task_identity_sha256=task.task_identity_sha256,
            methods=(state_aware,),
        )
        for task in campaign_tasks
    )
    rq3_cases = tuple(
        SimpleNamespace(task_identity_sha256=task.task_identity_sha256, case_ordinal=1)
        for task in rq2_tasks
    )
    rq4_tasks = tuple(
        SimpleNamespace(
            index=task.index,
            instance_id=task.instance_id,
            trajectory=SimpleNamespace(
                path=(
                    reproduction.TRANSFER_RUN_ROOT
                    / task.instance_id
                    / f"{task.instance_id}.traj.json"
                ).as_posix()
            ),
        )
        for task in frozen.executable.spec.cohort.tasks
    )
    snapshot = SimpleNamespace(
        campaign=SimpleNamespace(tasks=campaign_tasks),
        rq2=SimpleNamespace(tasks=rq2_tasks),
        rq3=SimpleNamespace(comparisons=rq3_cases),
        rq4=SimpleNamespace(
            tasks=rq4_tasks,
            analysis=SimpleNamespace(collection_complete=True),
        ),
    )

    assert reproduction._validate_outcome_structure(frozen, snapshot) == ()

    snapshot.rq4.analysis.collection_complete = False
    assert reproduction._validate_outcome_structure(frozen, snapshot) == (
        "RQ4 collection is incomplete",
    )


def test_result_rejects_complete_state_with_partial_raw_input() -> None:
    raw_inputs = list(reproduction._precollection_raw_inputs())
    raw_inputs[0] = raw_inputs[0].model_copy(update={"mode": None})

    with pytest.raises(ValueError, match="partial raw input"):
        reproduction.ThesisReproductionResult(
            state=reproduction.ReproductionState.COMPLETE,
            protocol_commit="a" * 40,
            executable_commit="b" * 40,
            raw_inputs=tuple(raw_inputs),
            errors=(),
        )


def test_result_rejects_a_changed_raw_input_denominator() -> None:
    raw_inputs = list(reproduction._precollection_raw_inputs())
    raw_inputs[0] = raw_inputs[0].model_copy(update={"expected_files": 184})

    with pytest.raises(ValueError, match="raw input count has changed"):
        reproduction.ThesisReproductionResult(
            state=reproduction.ReproductionState.PRE_COLLECTION,
            protocol_commit="a" * 40,
            executable_commit="b" * 40,
            raw_inputs=tuple(raw_inputs),
            errors=(),
        )
