from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.completion_isolation_census import (
    _dump_json,
    build_source_snapshot,
    build_support_census,
    check_artifacts,
    render_markdown,
)

SOURCE_COMMIT = "a" * 40


def _write_matrix(path: Path, names: list[str]) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dataset": "terminal-bench@2.0",
                "selection_method": (
                    "All tasks from the registry at source commit "
                    f"{SOURCE_COMMIT}; metadata copied from task.toml."
                ),
                "tasks": [{"name": name} for name in names],
            }
        ),
        encoding="utf-8",
    )


def _write_task(
    cache_root: Path,
    name: str,
    *,
    environment: str = "",
    compose: bool = False,
) -> Path:
    task_path = cache_root / f"cache-{name}" / name
    environment_path = task_path / "environment"
    environment_path.mkdir(parents=True)
    (task_path / "task.toml").write_text(
        f'version = "1.0"\n\n[environment]\n{environment}',
        encoding="utf-8",
    )
    (environment_path / "Dockerfile").write_text(
        "FROM ubuntu:24.04\nWORKDIR /app\n",
        encoding="utf-8",
    )
    if compose:
        (environment_path / "docker-compose.yaml").write_text(
            "services:\n  main:\n    image: ubuntu:24.04\n",
            encoding="utf-8",
        )
    return task_path


def test_build_census_matches_factory_task_source_rejections(tmp_path: Path) -> None:
    matrix = tmp_path / "matrix.json"
    cache = tmp_path / "cache"
    factory = tmp_path / "factory.py"
    names = ["supported", "composed", "accelerated"]
    _write_matrix(matrix, names)
    _write_task(cache, "supported")
    _write_task(cache, "composed", compose=True)
    _write_task(
        cache,
        "accelerated",
        environment=(
            'os = "windows"\n'
            "gpus = 1\n"
            "\n[environment.tpu]\n"
            'type = "v4"\n'
            'topology = "2x2"\n'
            "\n[environment.healthcheck]\n"
            'command = "true"\n'
        ),
    )
    factory.write_text("factory source\n", encoding="utf-8")

    report = build_support_census(
        matrix,
        cache,
        factory_source_path=factory,
    )

    assert report["dataset_source_commit"] == SOURCE_COMMIT
    assert report["summary"] == {
        "tasks": 3,
        "statically_supported": 1,
        "statically_unsupported": 2,
        "runtime_evaluated": 0,
    }
    rows = {row["name"]: row for row in report["tasks"]}
    assert rows["supported"]["rejection_reasons"] == []
    assert rows["composed"]["rejection_reasons"] == [
        "task-authored Docker Compose is not supported"
    ]
    assert rows["accelerated"]["rejection_reasons"] == [
        "completion isolation requires a Linux task container",
        "task environment healthchecks are not supported",
        "GPU task environments are not supported",
        "TPU task environments are not supported",
    ]
    assert rows["supported"]["source_files"][0]["path"] == "task.toml"
    assert rows["supported"]["runtime_status"] == "not_evaluated"

    markdown = render_markdown(report)
    assert "| 1 | `supported` | supported |" in markdown
    assert "Runtime evaluated by this census: 0" in markdown
    assert "It is not evidence that the attempt will succeed." in markdown


def test_check_artifacts_rebuilds_from_bound_sources(tmp_path: Path) -> None:
    matrix = tmp_path / "matrix.json"
    cache = tmp_path / "cache"
    factory = tmp_path / "factory.py"
    report_path = tmp_path / "support.json"
    markdown_path = tmp_path / "support.md"
    _write_matrix(matrix, ["supported"])
    task_path = _write_task(cache, "supported")
    factory.write_text("factory source\n", encoding="utf-8")
    report = build_support_census(
        matrix,
        cache,
        factory_source_path=factory,
    )
    report_path.write_bytes(_dump_json(report))
    markdown_path.write_text(render_markdown(report), encoding="utf-8")

    assert (
        check_artifacts(
            matrix,
            cache,
            report_path,
            markdown_path,
            factory_source_path=factory,
        )
        == []
    )

    (task_path / "task.toml").write_text(
        'version = "1.0"\n\n[environment]\nos = "windows"\n',
        encoding="utf-8",
    )
    assert check_artifacts(
        matrix,
        cache,
        report_path,
        markdown_path,
        factory_source_path=factory,
    ) == [
        "completion isolation census report is stale",
        "completion isolation census markdown is stale",
    ]


def test_check_artifacts_rebuilds_from_a_source_snapshot_without_a_cache(
    tmp_path: Path,
) -> None:
    matrix = tmp_path / "matrix.json"
    source_cache = tmp_path / "source-cache"
    empty_cache = tmp_path / "empty-cache"
    factory = tmp_path / "factory.py"
    snapshot_path = tmp_path / "source.json"
    report_path = tmp_path / "support.json"
    markdown_path = tmp_path / "support.md"
    _write_matrix(matrix, ["supported"])
    _write_task(source_cache, "supported")
    factory.write_text("factory source\n", encoding="utf-8")
    snapshot_path.write_bytes(_dump_json(build_source_snapshot(matrix, source_cache)))
    report = build_support_census(
        matrix,
        source_cache,
        factory_source_path=factory,
    )
    report_path.write_bytes(_dump_json(report))
    markdown_path.write_text(render_markdown(report), encoding="utf-8")

    assert (
        check_artifacts(
            matrix,
            empty_cache,
            report_path,
            markdown_path,
            factory_source_path=factory,
            source_snapshot_path=snapshot_path,
        )
        == []
    )

    report["tasks"][0]["static_status"] = "unsupported"
    report["tasks"][0]["rejection_reasons"] = ["forged rejection"]
    report_path.write_bytes(_dump_json(report))
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    assert check_artifacts(
        matrix,
        empty_cache,
        report_path,
        markdown_path,
        factory_source_path=factory,
        source_snapshot_path=snapshot_path,
    ) == [
        "completion isolation census report is stale",
        "completion isolation census markdown is stale",
    ]


def test_check_artifacts_rejects_tampered_source_snapshot(tmp_path: Path) -> None:
    matrix = tmp_path / "matrix.json"
    source_cache = tmp_path / "source-cache"
    factory = tmp_path / "factory.py"
    snapshot_path = tmp_path / "source.json"
    report_path = tmp_path / "support.json"
    markdown_path = tmp_path / "support.md"
    _write_matrix(matrix, ["supported"])
    _write_task(source_cache, "supported")
    factory.write_text("factory source\n", encoding="utf-8")
    snapshot = build_source_snapshot(matrix, source_cache)
    report = build_support_census(matrix, source_cache, factory_source_path=factory)
    report_path.write_bytes(_dump_json(report))
    markdown_path.write_text(render_markdown(report), encoding="utf-8")

    snapshot["tasks"][0]["files"][0]["text"] += "\nforged = true\n"
    snapshot_path.write_bytes(_dump_json(snapshot))

    assert check_artifacts(
        matrix,
        tmp_path / "empty-cache",
        report_path,
        markdown_path,
        factory_source_path=factory,
        source_snapshot_path=snapshot_path,
    ) == [
        "cannot rebuild completion isolation census: "
        "invalid source snapshot: task source file content does not match its binding"
    ]


def test_check_artifacts_requires_a_complete_cache_without_a_snapshot(tmp_path: Path) -> None:
    matrix = tmp_path / "matrix.json"
    source_cache = tmp_path / "source-cache"
    partial_cache = tmp_path / "partial-cache"
    factory = tmp_path / "factory.py"
    report_path = tmp_path / "support.json"
    markdown_path = tmp_path / "support.md"
    _write_matrix(matrix, ["task-a", "task-b"])
    _write_task(source_cache, "task-a")
    _write_task(source_cache, "task-b")
    _write_task(partial_cache, "task-a")
    factory.write_text("factory source\n", encoding="utf-8")
    report = build_support_census(
        matrix,
        source_cache,
        factory_source_path=factory,
    )
    report_path.write_bytes(_dump_json(report))
    markdown_path.write_text(render_markdown(report), encoding="utf-8")

    missing_cache = "Harbor cache is missing matrix tasks: task-b"
    assert check_artifacts(
        matrix,
        partial_cache,
        report_path,
        markdown_path,
        factory_source_path=factory,
    ) == [f"cannot rebuild completion isolation census: {missing_cache}"]


def test_census_rejects_missing_or_duplicate_cache_entries(tmp_path: Path) -> None:
    matrix = tmp_path / "matrix.json"
    cache = tmp_path / "cache"
    factory = tmp_path / "factory.py"
    _write_matrix(matrix, ["task-a"])
    factory.write_text("factory source\n", encoding="utf-8")

    with pytest.raises(ValueError, match="cache is missing matrix tasks: task-a"):
        build_support_census(matrix, cache, factory_source_path=factory)

    _write_task(cache, "task-a")
    duplicate = cache / "other-entry" / "task-a"
    duplicate.mkdir(parents=True)
    (duplicate / "task.toml").write_text(
        'version = "1.0"\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="cache has duplicate matrix tasks: task-a"):
        build_support_census(matrix, cache, factory_source_path=factory)
