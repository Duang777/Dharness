from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path, PurePosixPath
from typing import Any

from harbor.models.task.config import TaskConfig

from evidence_harness.docker_completion_isolation import (
    docker_isolation_static_rejection_reasons,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MATRIX = PROJECT_ROOT / "evaluation" / "matrix-89.json"
DEFAULT_CACHE_ROOT = Path.home() / ".cache" / "harbor" / "tasks"
DEFAULT_REPORT = PROJECT_ROOT / "evaluation" / "completion-isolation-support.json"
DEFAULT_MARKDOWN = PROJECT_ROOT / "docs" / "completion-isolation-support.md"
FACTORY_SOURCE = PROJECT_ROOT / "src" / "evidence_harness" / "docker_completion_isolation.py"
SUPPORTED_HARBOR_VERSION = "0.23.0"
SOURCE_COMMIT_PATTERN = re.compile(r"\bsource commit ([0-9a-f]{40})\b")
RUNTIME_REQUIREMENTS = (
    "The Docker daemon reports a Linux OSType.",
    "The Harbor Compose project has exactly one running main container and no sidecars.",
    "The source is running, unpaused, and has only Harbor's standard keepalive process.",
    "The effective image declares no Docker volumes.",
    "The effective HostConfig uses only the supported flags and namespaces.",
    "Runtime mounts target Harbor control paths only and do not expose docker.sock.",
    "The source diff remains unchanged while the source stays paused for the snapshot.",
    "A mount-free, network-disabled child starts and contains the shell and check dependencies.",
)
REPORT_SCOPE = {
    "proves": (
        "Task source inputs do not trigger the Docker completion-isolation "
        "factory's task-config rejection rules."
    ),
    "does_not_prove": (
        "The runtime container, process tree, image metadata, mounts, or "
        "isolated check execution satisfy the transaction's runtime checks."
    ),
}
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build or check the Terminal-Bench completion-isolation support census."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("build", "check"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
        subparser.add_argument("--cache-root", type=Path, default=DEFAULT_CACHE_ROOT)
        subparser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
        subparser.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN)
        if command == "check":
            subparser.add_argument(
                "--allow-missing-cache",
                action="store_true",
                help=(
                    "Validate committed source bindings and rendered artifacts when the "
                    "Harbor task cache is completely absent."
                ),
            )
    return parser.parse_args()


def build_support_census(
    matrix_path: Path,
    cache_root: Path,
    *,
    factory_source_path: Path = FACTORY_SOURCE,
) -> dict[str, Any]:
    matrix_bytes, matrix, names = _load_matrix(matrix_path)
    task_paths = _resolve_task_paths(cache_root, names)

    harbor_version = _harbor_version()

    rows = [
        _classify_task(
            index=index,
            task_name=name,
            task_path=task_paths[name],
            cache_root=cache_root,
        )
        for index, name in enumerate(names, start=1)
    ]
    supported = sum(row["static_status"] == "supported" for row in rows)
    unsupported = len(rows) - supported
    return {
        "schema_version": 1,
        "dataset": _required_string(matrix, "dataset"),
        "dataset_source_commit": _dataset_source_commit(matrix),
        "matrix_sha256": _sha256(matrix_bytes),
        "harbor_version": harbor_version,
        "factory_source": _factory_source_name(factory_source_path),
        "factory_source_sha256": _sha256(factory_source_path.read_bytes()),
        "scope": REPORT_SCOPE,
        "summary": {
            "tasks": len(rows),
            "statically_supported": supported,
            "statically_unsupported": unsupported,
            "runtime_evaluated": 0,
        },
        "runtime_requirements_not_evaluated": list(RUNTIME_REQUIREMENTS),
        "tasks": rows,
    }


def render_markdown(report: dict[str, Any]) -> str:
    summary = _object(report.get("summary"), "summary")
    scope = _object(report.get("scope"), "scope")
    rows = report.get("tasks")
    runtime_requirements = report.get("runtime_requirements_not_evaluated")
    if not isinstance(rows, list) or not isinstance(runtime_requirements, list):
        raise ValueError("census report has invalid task or runtime requirement lists")
    proves = str(scope["proves"])
    does_not_prove = str(scope["does_not_prove"])

    lines = [
        "# Completion isolation support census",
        "",
        "This report classifies the frozen Terminal-Bench 2.0 matrix against the",
        "task-source checks used by the Docker completion-isolation factory.",
        "",
        "## Source bindings",
        "",
        f"- Dataset: `{report['dataset']}`",
        f"- Dataset source commit: `{report['dataset_source_commit']}`",
        f"- Matrix SHA-256: `{report['matrix_sha256']}`",
        f"- Harbor version: `{report['harbor_version']}`",
        f"- Factory source: `{report['factory_source']}`",
        f"- Factory source SHA-256: `{report['factory_source_sha256']}`",
        "",
        "## Result",
        "",
        f"- Tasks inspected: {summary['tasks']}",
        f"- Statically supported: {summary['statically_supported']}",
        f"- Statically unsupported: {summary['statically_unsupported']}",
        f"- Runtime evaluated by this census: {summary['runtime_evaluated']}",
        "",
        f"This proves only that {proves[0].lower() + proves[1:]}",
        f"It does not prove that {does_not_prove[0].lower() + does_not_prove[1:]}",
        "",
        "## Task coverage",
        "",
        "| # | Task | Static status | Source input SHA-256 | Rejection reasons |",
        "|---:|---|---|---|---|",
    ]
    for value in rows:
        row = _object(value, "task row")
        reasons = "; ".join(row["rejection_reasons"]) or "None"
        lines.append(
            f"| {row['index']} | `{row['name']}` | {row['static_status']} | "
            f"`{row['static_input_sha256']}` | {reasons} |"
        )

    lines.extend(
        [
            "",
            "## Runtime boundary",
            "",
            "The census cannot prove these transaction-time requirements:",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in runtime_requirements)
    lines.extend(
        [
            "",
            "A task marked `supported` is eligible for a runtime isolation attempt.",
            "It is not evidence that the attempt will succeed.",
            "",
        ]
    )
    return "\n".join(lines)


def check_artifacts(
    matrix_path: Path,
    cache_root: Path,
    report_path: Path,
    markdown_path: Path,
    *,
    factory_source_path: Path = FACTORY_SOURCE,
    allow_missing_cache: bool = False,
) -> list[str]:
    if allow_missing_cache and not any(cache_root.glob("*/*/task.toml")):
        return _check_committed_artifacts(
            matrix_path,
            report_path,
            markdown_path,
            factory_source_path=factory_source_path,
        )

    errors: list[str] = []
    try:
        expected = build_support_census(
            matrix_path,
            cache_root,
            factory_source_path=factory_source_path,
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [f"cannot rebuild completion isolation census: {exc}"]

    try:
        actual = _object(json.loads(report_path.read_bytes()), "census report")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        errors.append(f"invalid completion isolation census report: {exc}")
    else:
        if actual != expected:
            errors.append("completion isolation census report is stale")

    try:
        actual_markdown = markdown_path.read_text(encoding="utf-8")
    except OSError as exc:
        errors.append(f"cannot read completion isolation census markdown: {exc}")
    else:
        if actual_markdown != render_markdown(expected):
            errors.append("completion isolation census markdown is stale")
    return errors


def _check_committed_artifacts(
    matrix_path: Path,
    report_path: Path,
    markdown_path: Path,
    *,
    factory_source_path: Path,
) -> list[str]:
    errors: list[str] = []
    try:
        matrix_bytes, matrix, names = _load_matrix(matrix_path)
        report_data = report_path.read_bytes()
        actual = _object(json.loads(report_data), "census report")
        harbor_version = _harbor_version()
        expected_bindings = {
            "schema_version": 1,
            "dataset": _required_string(matrix, "dataset"),
            "dataset_source_commit": _dataset_source_commit(matrix),
            "matrix_sha256": _sha256(matrix_bytes),
            "harbor_version": harbor_version,
            "factory_source": _factory_source_name(factory_source_path),
            "factory_source_sha256": _sha256(factory_source_path.read_bytes()),
            "scope": REPORT_SCOPE,
            "runtime_requirements_not_evaluated": list(RUNTIME_REQUIREMENTS),
        }
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [f"invalid completion isolation census inputs: {exc}"]

    if any(actual.get(key) != value for key, value in expected_bindings.items()):
        errors.append("completion isolation census source bindings are stale")

    try:
        rows = actual.get("tasks")
        if not isinstance(rows, list):
            raise ValueError("tasks must be an array")
        row_objects = [_object(value, "task row") for value in rows]
        row_names = [_required_string(row, "name") for row in row_objects]
        row_indices = [row.get("index") for row in row_objects]
        if row_names != names or row_indices != list(range(1, len(names) + 1)):
            raise ValueError("task rows do not match the matrix")
        statuses = [row.get("static_status") for row in row_objects]
        if any(status not in {"supported", "unsupported"} for status in statuses):
            raise ValueError("task row has an invalid static status")
        for row, name, status in zip(row_objects, names, statuses, strict=True):
            _validate_committed_task_row(row, name, str(status))
        expected_summary = {
            "tasks": len(row_objects),
            "statically_supported": statuses.count("supported"),
            "statically_unsupported": statuses.count("unsupported"),
            "runtime_evaluated": 0,
        }
        if actual.get("summary") != expected_summary:
            raise ValueError("summary does not match the task rows")
    except (TypeError, ValueError) as exc:
        errors.append(f"invalid completion isolation census report: {exc}")

    if report_data != _dump_json(actual):
        errors.append("completion isolation census report is not canonical JSON")

    try:
        actual_markdown = markdown_path.read_text(encoding="utf-8")
        expected_markdown = render_markdown(actual)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        errors.append(f"invalid completion isolation census markdown: {exc}")
    else:
        if actual_markdown != expected_markdown:
            errors.append("completion isolation census markdown is stale")
    return errors


def _validate_committed_task_row(row: dict[str, Any], name: str, status: str) -> None:
    cache_entry = PurePosixPath(_required_string(row, "cache_entry"))
    if cache_entry.is_absolute() or ".." in cache_entry.parts or cache_entry.name != name:
        raise ValueError("task row has an invalid cache entry")
    if row.get("runtime_status") != "not_evaluated" or not SHA256_PATTERN.fullmatch(
        str(row.get("static_input_sha256", ""))
    ):
        raise ValueError("task row has an invalid source binding")

    source_files = row.get("source_files")
    if not isinstance(source_files, list) or not source_files:
        raise ValueError("task row has no source files")
    source_paths: list[str] = []
    for value in source_files:
        source = _object(value, "task source file")
        source_path = PurePosixPath(_required_string(source, "path"))
        source_paths.append(source_path.as_posix())
        size = source.get("bytes")
        if (
            source_path.is_absolute()
            or ".." in source_path.parts
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 1
            or not SHA256_PATTERN.fullmatch(str(source.get("sha256", "")))
        ):
            raise ValueError("task row has an invalid source file binding")
    if len(source_paths) != len(set(source_paths)) or "task.toml" not in source_paths:
        raise ValueError("task row source files are incomplete")

    rejection_reasons = row.get("rejection_reasons")
    if not isinstance(rejection_reasons, list) or not all(
        isinstance(reason, str) and reason for reason in rejection_reasons
    ):
        raise ValueError("task row has invalid rejection reasons")
    if (status == "supported") != (not rejection_reasons):
        raise ValueError("task row status disagrees with its rejection reasons")


def _classify_task(
    *,
    index: int,
    task_name: str,
    task_path: Path,
    cache_root: Path,
) -> dict[str, Any]:
    task_toml = task_path / "task.toml"
    config = TaskConfig.model_validate_toml(task_toml.read_text(encoding="utf-8"))
    environment_dir = task_path / "environment"
    reasons = docker_isolation_static_rejection_reasons(
        environment_dir=environment_dir,
        config=config.environment,
    )
    input_paths = [task_toml]
    for name in ("Dockerfile", "docker-compose.yaml"):
        path = environment_dir / name
        if path.is_file():
            input_paths.append(path)
    source_files = [_source_file(path, task_path) for path in input_paths]
    return {
        "index": index,
        "name": task_name,
        "cache_entry": task_path.relative_to(cache_root).as_posix(),
        "static_input_sha256": _source_set_sha256(input_paths, task_path),
        "source_files": source_files,
        "environment": {
            "os": getattr(config.environment.os, "value", config.environment.os),
            "task_compose": (environment_dir / "docker-compose.yaml").is_file(),
            "healthcheck": config.environment.healthcheck is not None,
            "gpus": config.environment.gpus or 0,
            "tpu": config.environment.tpu is not None,
            "runtime_mounts_represented": False,
            "extra_compose_overlays_represented": False,
        },
        "static_status": "unsupported" if reasons else "supported",
        "rejection_reasons": list(reasons),
        "runtime_status": "not_evaluated",
    }


def _resolve_task_paths(cache_root: Path, names: list[str]) -> dict[str, Path]:
    expected = set(names)
    matches: dict[str, list[Path]] = {name: [] for name in names}
    for task_toml in cache_root.glob("*/*/task.toml"):
        task_name = task_toml.parent.name
        if task_name in expected:
            matches[task_name].append(task_toml.parent)

    missing = sorted(name for name, paths in matches.items() if not paths)
    duplicates = sorted(name for name, paths in matches.items() if len(paths) > 1)
    if missing:
        raise ValueError(f"Harbor cache is missing matrix tasks: {', '.join(missing)}")
    if duplicates:
        raise ValueError(f"Harbor cache has duplicate matrix tasks: {', '.join(duplicates)}")
    return {name: paths[0] for name, paths in matches.items()}


def _load_matrix(matrix_path: Path) -> tuple[bytes, dict[str, Any], list[str]]:
    matrix_bytes = matrix_path.read_bytes()
    matrix = _object(json.loads(matrix_bytes), "matrix")
    if matrix.get("schema_version") != 1:
        raise ValueError("unsupported matrix schema")
    _required_string(matrix, "dataset")
    matrix_tasks = matrix.get("tasks")
    if not isinstance(matrix_tasks, list):
        raise ValueError("matrix tasks must be an array")
    names = [_required_string(_object(value, "matrix task"), "name") for value in matrix_tasks]
    if len(names) != len(set(names)):
        raise ValueError("matrix task names must be unique")
    return matrix_bytes, matrix, names


def _harbor_version() -> str:
    try:
        harbor_version = version("harbor")
    except PackageNotFoundError as exc:
        raise ValueError("Harbor is not installed") from exc
    if harbor_version != SUPPORTED_HARBOR_VERSION:
        raise ValueError(f"Harbor {SUPPORTED_HARBOR_VERSION} is required, found {harbor_version}")
    return harbor_version


def _dataset_source_commit(matrix: dict[str, Any]) -> str | None:
    source_match = SOURCE_COMMIT_PATTERN.search(str(matrix.get("selection_method", "")))
    return source_match.group(1) if source_match else None


def _factory_source_name(factory_source_path: Path) -> str:
    try:
        return str(factory_source_path.relative_to(PROJECT_ROOT))
    except ValueError:
        return factory_source_path.name


def _source_file(path: Path, task_path: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "path": path.relative_to(task_path).as_posix(),
        "bytes": len(data),
        "sha256": _sha256(data),
    }


def _source_set_sha256(paths: list[Path], task_path: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths, key=lambda value: value.relative_to(task_path).as_posix()):
        relative = path.relative_to(task_path).as_posix().encode()
        data = path.read_bytes()
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


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


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _required_string(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ValueError(f"{key} must be a non-empty string")
    return item


def main() -> int:
    args = parse_args()
    if args.command == "build":
        report = build_support_census(args.matrix, args.cache_root)
        _write_atomic(args.report, _dump_json(report))
        _write_atomic(args.markdown, render_markdown(report).encode())
        summary = report["summary"]
        print(
            "completion isolation census built: "
            f"{summary['statically_supported']} supported, "
            f"{summary['statically_unsupported']} unsupported, "
            f"{summary['runtime_evaluated']} runtime evaluated"
        )
        return 0

    errors = check_artifacts(
        args.matrix,
        args.cache_root,
        args.report,
        args.markdown,
        allow_missing_cache=args.allow_missing_cache,
    )
    if errors:
        for error in errors:
            print(error)
        return 1
    if args.allow_missing_cache and not any(args.cache_root.glob("*/*/task.toml")):
        print(
            "completion isolation census committed bindings verified "
            "(Harbor task cache unavailable)"
        )
    else:
        print("completion isolation census artifacts verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
