from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from evidence_harness_mutation import thesis_tables as thesis_tables_module
from evidence_harness_mutation.model import FrozenModel
from evidence_harness_mutation.thesis_tables import (
    ArtifactBinding,
    ArtifactId,
    CellDerivation,
    EvidenceCell,
    SourceRef,
    _CanonicalInputs,
    _LoadedArtifact,
    _project_bundle,
    _resolve_pointer,
    _validate_cell,
    _validate_partition_analyses,
    _validate_traceability,
    build_thesis_tables,
    check_thesis_tables,
    render_thesis_tables,
)


class _SyntheticModel(FrozenModel):
    pass


def _binding(path: str) -> dict[str, object]:
    return {"path": path, "bytes": 1, "sha256": "a" * 64}


def _exact(value: str) -> dict[str, object]:
    return {"numerator": 0, "denominator": 1, "decimal": value}


def _interval(lower: str, upper: str) -> dict[str, object]:
    return {
        "lower": _exact(lower),
        "upper": _exact(upper),
        "resamples": 10_000,
        "lower_one_based_rank": 250,
        "upper_one_based_rank": 9_750,
    }


def _synthetic_inputs() -> _CanonicalInputs:
    paths = {
        "protocol": "experiments/prefixbench-v1/main-analysis-protocol-v1.json",
        "executable_protocol": ("experiments/prefixbench-v1/main-analysis-executable-v1.json"),
        "miniswe_cohort": "experiments/prefixbench-v1/miniswe-cohort-v1.json",
        "test_run_root": "runs/terminal-bench-2/prefixbench-v1-test-20261002",
        "test_canonical": "evaluation/prefixbench-v1-test-canonical.json",
        "test_readiness": "evaluation/prefixbench-v1-test-readiness.json",
        "test_campaign": "evaluation/prefixbench-v1-test-offline-campaign.json",
        "rq2_report": "evaluation/prefixbench-v1-test-method-comparison.json",
        "rq3_report": "evaluation/prefixbench-v1-test-reducer-comparison.json",
        "rq4_report": "evaluation/miniswe-agent-transfer-v1.json",
        "main_report": "evaluation/thesis-main-analysis-v1.json",
        "tb21_report": "evaluation/thesis-tb21-sensitivity-v1.json",
        "auxiliary_report": "evaluation/thesis-auxiliary-v1.json",
    }
    protocol = {
        "paths": paths,
        "chronology": {
            "design_base_commit": "1" * 40,
            "test_matrix_commit": "2" * 40,
            "test_protocol_commit": "3" * 40,
        },
        "rq1": {"engineering_threshold": "at-least-6-of-7-pairs"},
    }
    executable = {"protocol": _binding(paths["protocol"])}
    historical = {
        "results": [
            {
                "case_id": "case-a",
                "revision_role": "vulnerable",
                "kind": "completed",
                "expected_outcome": "survived",
                "observed_outcome": "survived",
                "matches_expected": True,
                "commit": "4" * 40,
            }
        ]
    }
    rq2 = {
        "source_campaign": _binding(paths["test_campaign"]),
        "analysis": {
            "methods": [
                {
                    "method": "state_aware",
                    "tasks": 1,
                    "scheduled_slots": 1,
                    "outcomes": {
                        "input_rejected": 0,
                        "target_not_reached": 0,
                        "oracle_invalid": 0,
                        "oracle_equivalent": 0,
                        "target_violation": 1,
                        "other_oracle_change": 0,
                    },
                    "tasks_with_target_violation": 1,
                    "input_acceptance_rate": _exact("1.000000000000"),
                    "target_reach_rate": _exact("1.000000000000"),
                    "target_violation_rate": _exact("0.333333333333"),
                    "oracle_equivalent_rate": _exact("0.000000000000"),
                    "other_oracle_change_rate": _exact("0.000000000000"),
                    "distinct_target_invariants": 1,
                    "target_violations_per_100_slots": _exact("100.000000000000"),
                }
            ],
            "contrasts": [
                {
                    "comparator": "random_json",
                    "mcnemar": {
                        "tasks": 1,
                        "risk_difference": _exact("1.000000000000"),
                        "p_value": _exact("0.500000000000"),
                    },
                    "risk_difference_interval": _interval(
                        "0.125000000000",
                        "1.000000000000",
                    ),
                    "holm_adjusted_p_value": _exact("1.000000000000"),
                    "disposition": "not_confirmed",
                }
            ],
            "disposition": "broad_superiority_not_confirmed",
        },
        "tasks": [
            {
                "task_name": "task-a",
                "methods": [
                    {
                        "method": "state_aware",
                        "outcomes": [
                            {
                                "slot": {
                                    "slot_ordinal": 1,
                                    "attempt_ordinal": 1,
                                    "operator": "stale_evidence_epoch",
                                    "target_invariant": "I1",
                                    "synthetic_attempt": False,
                                },
                                "outcome": "target_violation",
                            }
                        ],
                    }
                ],
            }
        ],
    }
    rq3 = {
        "source_rq2": _binding(paths["rq2_report"]),
        "analysis": {
            "cases": 0,
            "tasks": 0,
            "reducers": [],
            "status": "insufficient_cases",
            "contrasts": [],
            "disposition": "reduction_advantage_not_confirmed",
        },
        "comparisons": [],
    }
    rq4 = {
        "cohort": _binding(paths["miniswe_cohort"]),
        "analysis": {
            "collection_complete": True,
            "families": [
                {
                    "family": "I3",
                    "mapping": "unsupported_by_design",
                    "scheduled": 1,
                    "executable": 0,
                    "adapter_rejected": 0,
                    "preexisting_violation": 0,
                    "not_applicable": 0,
                    "applicable": 0,
                    "target_violation": 0,
                    "other_oracle_change": 0,
                    "deterministic_replay": 0,
                    "disposition": "unsupported_by_design",
                }
            ],
            "disposition": "transfer_not_confirmed",
        },
        "tasks": [
            {
                "index": 1,
                "instance_id": "instance-a",
                "families": [
                    {
                        "family": "I3",
                        "status": "unsupported_by_design",
                        "deterministic_replay": False,
                    }
                ],
            }
        ],
    }
    campaign = {
        "sources": {
            "matrix": _binding("evaluation/matrix-prefixbench-test.json"),
            "canonical": _binding(paths["test_canonical"]),
            "readiness": _binding(paths["test_readiness"]),
            "collection": {
                "run_config": _binding(f"{paths['test_run_root']}/run-config.json"),
                "progress": _binding(f"{paths['test_run_root']}/progress.jsonl"),
            },
        },
        "producer": {
            "commit": "5" * 40,
            "tree": "6" * 40,
            "source_sha256": "b" * 64,
        },
        "protocol": {"spec": {"provider_credentials": "external-file-run-config-hash-only"}},
    }
    main = {
        "lineage": {
            "protocol_commit": "7" * 40,
            "executable_commit": "8" * 40,
        },
        "source_bindings": {
            "historical_7": _binding("evaluation/historical-7.json"),
            "development_analysis": _binding(
                "evaluation/prefixbench-v1-development-offline-analysis.json"
            ),
            "rq2": _binding(paths["rq2_report"]),
            "rq3": _binding(paths["rq3_report"]),
            "rq4": _binding(paths["rq4_report"]),
        },
        "rq1": {
            "evidence_role": "locked_retrospective",
            "case_pairs": 7,
            "successful_pairs": 7,
            "disposition": "threshold_met",
            "inferential_test": "none",
        },
        "deviations": [],
    }
    documents: dict[ArtifactId, object] = {
        ArtifactId.PROTOCOL: protocol,
        ArtifactId.EXECUTABLE: executable,
        ArtifactId.MINISWE_COHORT: {"tasks": [{"index": 1}]},
        ArtifactId.HISTORICAL_7: historical,
        ArtifactId.DEVELOPMENT: {"claims": {"test_split_generalization": "not_evaluated"}},
        ArtifactId.TEST_READINESS: {"status": "ready"},
        ArtifactId.TEST_CAMPAIGN: campaign,
        ArtifactId.RQ2: rq2,
        ArtifactId.RQ3: rq3,
        ArtifactId.RQ4: rq4,
        ArtifactId.MAIN: main,
    }
    artifacts = {}
    for artifact in ArtifactId:
        document = documents[artifact]
        assert isinstance(document, dict)
        data = (json.dumps(document, sort_keys=True) + "\n").encode()
        artifacts[artifact] = _LoadedArtifact(
            binding=ArtifactBinding(
                artifact=artifact,
                path=f"synthetic/{artifact.value}.json",
                bytes=len(data),
                sha256=__import__("hashlib").sha256(data).hexdigest(),
            ),
            data=data,
            document=document,
            model=_SyntheticModel(),
        )
    return _CanonicalInputs(artifacts)


def test_json_pointer_resolver_handles_rfc6901_escapes() -> None:
    document = {"a/b": {"~key": [10]}}

    assert _resolve_pointer(document, "/a~1b/~0key/0") == 10

    with pytest.raises(ValueError, match="invalid escape"):
        _resolve_pointer(document, "/a~2b")
    with pytest.raises(ValueError, match="out of range"):
        _resolve_pointer(document, "/a~1b/~0key/1")


def test_projection_has_fixed_tables_and_traceable_partition_rows() -> None:
    inputs = _synthetic_inputs()

    bundle = _project_bundle(inputs)
    _validate_traceability(bundle, inputs)

    assert tuple(table.table_id for table in bundle.tables) == (
        "rq1",
        "rq2",
        "rq3",
        "rq4",
        "historical_partition",
        "rq2_partition",
        "rq3_partition",
        "rq4_partition",
        "protocol_deviations",
        "source_bindings",
        "git_chronology",
        "publication_inventory",
    )
    assert all(entry.sources for entry in bundle.reproducibility_index)
    assert any(
        cell.text == "0.333333333333"
        for table in bundle.tables
        for panel in table.panels
        for row in panel.rows
        for cell in row.cells
    )
    assert len(bundle.tables[5].panels[0].rows) == 1
    assert len(bundle.tables[7].panels[0].rows) == 1
    assert tuple(column.key for column in bundle.tables[1].panels[0].columns) == (
        "method",
        "tasks",
        "slots",
        "target_violations",
        "tasks_detected",
        "input_acceptance",
        "target_reach",
        "violation_rate",
        "oracle_equivalent",
        "other_oracle_change",
        "distinct_invariants",
        "violations_per_100_slots",
    )
    assert tuple(column.key for column in bundle.tables[3].panels[0].columns)[2:11] == (
        "scheduled",
        "executable",
        "adapter_rejected",
        "preexisting_violation",
        "not_applicable",
        "applicable",
        "target_violations",
        "other_changes",
        "deterministic_replay",
    )
    rq1_values = {row.row_id: row.cells[1].text for row in bundle.tables[0].panels[0].rows}
    assert rq1_values["vulnerable-reproduction-rate"] == "1/1"
    assert rq1_values["fixed-confirmation-rate"] == "0/0"
    assert rq1_values["paired-success-rate"] == "0/1"
    assert rq1_values["inconclusive-count"] == "0"
    assert rq1_values["error-count"] == "0"
    assert rq1_values["byte-identical-rebuild"] == "unavailable_in_canonical_artifacts"


def test_partition_analysis_validation_rejects_a_stale_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rq2 = SimpleNamespace(tasks=("rq2 rows",), analysis="rq2 summary")
    rq3 = SimpleNamespace(comparisons=("rq3 rows",), analysis="rq3 summary")
    rq4 = SimpleNamespace(tasks=("rq4 rows",), analysis="rq4 summary")
    monkeypatch.setattr(thesis_tables_module, "analyze_rq2", lambda _rows: "rq2 summary")
    monkeypatch.setattr(thesis_tables_module, "analyze_rq3", lambda _rows: "rq3 summary")
    monkeypatch.setattr(thesis_tables_module, "analyze_rq4", lambda _rows: "different")

    with pytest.raises(ValueError, match="does not match its partition rows"):
        _validate_partition_analyses(rq2, rq3, rq4)  # type: ignore[arg-type]


def test_traceability_rejects_a_changed_exact_decimal() -> None:
    inputs = _synthetic_inputs()
    cell = EvidenceCell(
        text="0.500000000000",
        sources=(
            SourceRef(
                artifact=ArtifactId.RQ2,
                pointer="/analysis/methods/0/target_violation_rate/decimal",
            ),
        ),
        derivation=CellDerivation.EXACT_DECIMAL_COPY,
    )

    with pytest.raises(ValueError, match="exact-decimal"):
        _validate_cell(cell, inputs)


def test_markdown_includes_scope_and_source_pointer() -> None:
    bundle = _project_bundle(_synthetic_inputs())

    markdown = render_thesis_tables(bundle)

    assert "does not rerun the model" in markdown
    assert "`rq2#/analysis/methods/0/target_violation_rate/decimal`" in markdown
    assert "0.333333333333" in markdown


def test_build_is_write_once_and_check_is_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inputs = _synthetic_inputs()
    monkeypatch.setattr(
        "evidence_harness_mutation.thesis_tables._load_inputs",
        lambda _root: inputs,
    )

    first = build_thesis_tables(tmp_path)
    second = build_thesis_tables(tmp_path)

    assert (first.bundle_state, first.markdown_state) == ("created", "created")
    assert (second.bundle_state, second.markdown_state) == ("unchanged", "unchanged")
    before = {
        path: path.read_bytes()
        for path in (
            tmp_path / "evaluation/thesis-tables-v1.json",
            tmp_path / "docs/thesis-tables-v1.md",
        )
    }
    assert check_thesis_tables(tmp_path) == ()
    assert {path: path.read_bytes() for path in before} == before


def test_build_rejects_a_conflict_before_writing_the_other_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "evidence_harness_mutation.thesis_tables._load_inputs",
        lambda _root: _synthetic_inputs(),
    )
    conflict = tmp_path / "docs/thesis-tables-v1.md"
    conflict.parent.mkdir(parents=True)
    conflict.write_text("different\n", encoding="utf-8")

    with pytest.raises(ValueError, match="refusing to replace"):
        build_thesis_tables(tmp_path)

    assert not (tmp_path / "evaluation/thesis-tables-v1.json").exists()
    assert conflict.read_text(encoding="utf-8") == "different\n"


def test_build_rejects_a_symlinked_output_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "repository"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (root / "docs").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(
        "evidence_harness_mutation.thesis_tables._load_inputs",
        lambda _root: _synthetic_inputs(),
    )

    with pytest.raises(ValueError, match="output path contains a symbolic link"):
        build_thesis_tables(root)

    assert not (root / "evaluation/thesis-tables-v1.json").exists()
    assert not (outside / "thesis-tables-v1.md").exists()


def test_atomic_publish_does_not_overwrite_a_racing_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "table.json"
    real_link = thesis_tables_module.os.link

    def racing_link(
        source: str,
        destination: str,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
        follow_symlinks: bool = True,
    ) -> None:
        target.write_text("racing writer\n", encoding="utf-8")
        real_link(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
            follow_symlinks=follow_symlinks,
        )

    monkeypatch.setattr(thesis_tables_module.os, "link", racing_link)

    with pytest.raises(ValueError, match="refusing to replace"):
        thesis_tables_module._write_atomic(tmp_path, target, b"our output\n")

    assert target.read_text(encoding="utf-8") == "racing writer\n"
    assert list(tmp_path.glob(".*.tmp")) == []


def test_existing_output_check_does_not_follow_a_racing_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "table.json"
    outside = tmp_path / "outside.json"
    expected = b"expected\n"
    target.write_bytes(expected)
    outside.write_bytes(b"outside\n")
    real_read = thesis_tables_module._read_output_at

    def racing_read(parent_fd: int, name: str) -> bytes | None:
        target.unlink()
        target.symlink_to(outside)
        return real_read(parent_fd, name)

    monkeypatch.setattr(thesis_tables_module, "_read_output_at", racing_read)

    with pytest.raises(ValueError, match="output must be a regular file"):
        thesis_tables_module._existing_output_state(tmp_path, target, expected)

    assert outside.read_bytes() == b"outside\n"


def test_output_publish_revalidates_every_final_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = tmp_path / "first.json"
    second = tmp_path / "second.md"
    real_write = thesis_tables_module._write_atomic

    def racing_write(root: Path, path: Path, data: bytes) -> str:
        state = real_write(root, path, data)
        if path == second:
            first.unlink()
            first.write_bytes(b"racing writer\n")
        return state

    monkeypatch.setattr(thesis_tables_module, "_write_atomic", racing_write)

    with pytest.raises(ValueError, match="refusing to replace"):
        thesis_tables_module._write_outputs_once(
            tmp_path,
            ((first, b"first\n"), (second, b"second\n")),
        )

    assert first.read_bytes() == b"racing writer\n"
    assert second.read_bytes() == b"second\n"
