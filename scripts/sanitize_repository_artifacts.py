from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ISOLATION_REPORT = Path("evaluation/completion-isolation-experiments.json")
TEST_PROTOCOL = Path("experiments/prefixbench-v1/test-mutation-protocol-v1.json")

_INTERNAL_SERVICE_URL = re.compile(
    r"^https?://[^/]+\.bytedance\.(?:net|com)(?:/.*)?$",
    re.IGNORECASE,
)


def sanitize_json(value: Any, root: Path) -> tuple[Any, bool]:
    changed = False
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            if (
                key == "api_base"
                and isinstance(item, str)
                and _INTERNAL_SERVICE_URL.fullmatch(item)
            ):
                changed = True
                continue
            replacement, item_changed = sanitize_json(item, root)
            sanitized[key] = replacement
            changed = changed or item_changed
        return sanitized, changed
    if isinstance(value, list):
        sanitized_items: list[Any] = []
        for item in value:
            replacement, item_changed = sanitize_json(item, root)
            sanitized_items.append(replacement)
            changed = changed or item_changed
        return sanitized_items, changed
    if isinstance(value, str):
        replacement = _repository_relative_path(value, root)
        return replacement, replacement != value
    return value, False


def sanitize_tracked_json(root: Path = PROJECT_ROOT) -> tuple[Path, ...]:
    changed: list[Path] = []
    for path in _tracked_json_files(root):
        if path.relative_to(root) == TEST_PROTOCOL:
            continue
        try:
            source = _committed_source(root, path).decode()
            value = json.loads(source)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        sanitized, was_changed = sanitize_json(value, root)
        if not was_changed:
            continue
        expected = _dump_json_like(source, sanitized)
        if path.read_text(encoding="utf-8") != expected:
            path.write_text(expected, encoding="utf-8")
            changed.append(path.relative_to(root))

    report_path = root / ISOLATION_REPORT
    if report_path.is_file():
        report = json.loads(report_path.read_text(encoding="utf-8"))
        report_changed = _refresh_isolation_result_bindings(report, root)
        if "source_revision" not in report:
            report["source_revision"] = _find_report_source_revision(report, root)
            report_changed = True
        if report_changed:
            report_path.write_text(_canonical_json(report), encoding="utf-8")
            relative_report = report_path.relative_to(root)
            if relative_report not in changed:
                changed.append(relative_report)
    protocol_path = _refresh_test_protocol(root)
    if protocol_path is not None and protocol_path not in changed:
        changed.append(protocol_path)
    for analysis_path in _refresh_main_analysis_artifacts(root):
        if analysis_path not in changed:
            changed.append(analysis_path)
    for sensitivity_path in _refresh_tb21_sensitivity_artifacts(root):
        if sensitivity_path not in changed:
            changed.append(sensitivity_path)
    return tuple(changed)


def _repository_relative_path(value: str, root: Path) -> str:
    root_text = root.resolve().as_posix().rstrip("/")
    plain_prefix = root_text + "/"
    uri_prefix = "file://" + plain_prefix
    if value.startswith(uri_prefix):
        return value.removeprefix(uri_prefix)
    if value.startswith(plain_prefix):
        return value.removeprefix(plain_prefix)
    return value


def _refresh_isolation_result_bindings(report: dict[str, Any], root: Path) -> bool:
    changed = False
    experiments = report.get("experiments")
    if not isinstance(experiments, list):
        return False
    for experiment in experiments:
        if not isinstance(experiment, dict):
            continue
        sources = experiment.get("prospective_sources")
        if not isinstance(sources, dict):
            continue
        binding = sources.get("result")
        if not isinstance(binding, dict):
            continue
        relative = binding.get("path")
        if not isinstance(relative, str):
            continue
        path = root / relative
        if not path.is_file():
            continue
        data = path.read_bytes()
        expected = {
            "bytes": len(data),
            "path": relative,
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        if binding != expected:
            sources["result"] = expected
            changed = True
    return changed


def _find_report_source_revision(report: dict[str, Any], root: Path) -> str:
    from evidence_harness.source_binding import archived_runtime_source_binding

    sources = report.get("sources")
    if not isinstance(sources, dict):
        raise ValueError("completion isolation report has no source bindings")
    expected = sources.get("runtime")
    completed = subprocess.run(
        (
            "git",
            "-C",
            str(root),
            "log",
            "--all",
            "--format=%H",
            "--",
            ISOLATION_REPORT.as_posix(),
        ),
        check=True,
        capture_output=True,
        text=True,
    )
    for revision in completed.stdout.splitlines():
        if archived_runtime_source_binding(root, revision) == expected:
            return revision
    raise ValueError("cannot identify completion isolation source revision")


def _refresh_test_protocol(root: Path) -> Path | None:
    from evidence_harness_mutation.prefixbench_test_campaign import (
        TEST_PROTOCOL,
        freeze_prefixbench_test_protocol,
    )

    path = root / TEST_PROTOCOL
    expected = freeze_prefixbench_test_protocol(root).canonical_bytes()
    if path.read_bytes() == expected:
        return None
    path.write_bytes(expected)
    return path.relative_to(root)


def _refresh_main_analysis_artifacts(root: Path) -> tuple[Path, ...]:
    from evidence_harness_mutation.main_analysis_executable import (
        _IMPLEMENTATION_PATHS,
        MainAnalysisExecutable,
        _file_binding,
        _source_set,
    )
    from evidence_harness_mutation.main_analysis_protocol import (
        EXECUTABLE_PROTOCOL,
        MAIN_PROTOCOL,
        freeze_main_analysis_protocol,
    )

    changed: list[Path] = []
    protocol = freeze_main_analysis_protocol(root)
    protocol_data = protocol.canonical_bytes()
    protocol_path = root / MAIN_PROTOCOL
    if protocol_path.read_bytes() != protocol_data:
        protocol_path.write_bytes(protocol_data)
        changed.append(MAIN_PROTOCOL)

    executable_path = root / EXECUTABLE_PROTOCOL
    executable = MainAnalysisExecutable.model_validate_json(executable_path.read_bytes())
    payload = executable.model_dump(mode="json")
    payload.update(
        {
            "protocol": _file_binding(MAIN_PROTOCOL.as_posix(), protocol_data).model_dump(
                mode="json"
            ),
            "protected_sources": protocol.protected_sources.model_dump(mode="json"),
            "implementation_sources": _source_set(
                root,
                _IMPLEMENTATION_PATHS,
            ).model_dump(mode="json"),
        }
    )
    executable_data = MainAnalysisExecutable.model_validate(payload).canonical_bytes()
    if executable_path.read_bytes() != executable_data:
        executable_path.write_bytes(executable_data)
        changed.append(EXECUTABLE_PROTOCOL)
    return tuple(changed)


def _refresh_tb21_sensitivity_artifacts(root: Path) -> tuple[Path, ...]:
    from evidence_harness_mutation._tb21_manifest import (
        IMPLEMENTATION_PATHS,
        IMPORTED_KERNEL_PATHS,
        ExecutableManifest,
        file_binding,
        source_set,
    )
    from evidence_harness_mutation.tb21_sensitivity_protocol import (
        SENSITIVITY_EXECUTABLE,
        SENSITIVITY_MATRIX,
        SENSITIVITY_PROTOCOL,
        Tb21SensitivityMatrix,
        _current_protocol,
    )

    changed: list[Path] = []
    matrix = Tb21SensitivityMatrix.model_validate_json((root / SENSITIVITY_MATRIX).read_bytes())
    protocol = _current_protocol(root, matrix)
    protocol_data = protocol.canonical_bytes()
    protocol_path = root / SENSITIVITY_PROTOCOL
    if protocol_path.read_bytes() != protocol_data:
        protocol_path.write_bytes(protocol_data)
        changed.append(SENSITIVITY_PROTOCOL)

    executable_path = root / SENSITIVITY_EXECUTABLE
    executable = ExecutableManifest.model_validate_json(executable_path.read_bytes())
    payload = executable.model_dump(mode="json")
    payload.update(
        {
            "protocol": file_binding(SENSITIVITY_PROTOCOL, protocol_data).model_dump(mode="json"),
            "protected_sources": protocol.protected_sources.model_dump(mode="json"),
            "implementation_sources": source_set(
                root,
                IMPLEMENTATION_PATHS,
            ).model_dump(mode="json"),
            "imported_kernel_sources": source_set(
                root,
                IMPORTED_KERNEL_PATHS,
            ).model_dump(mode="json"),
        }
    )
    executable_data = ExecutableManifest.model_validate(payload).canonical_bytes()
    if executable_path.read_bytes() != executable_data:
        executable_path.write_bytes(executable_data)
        changed.append(SENSITIVITY_EXECUTABLE)

    runtime_spec_path = root / "config" / "tb21-harbor-runtime.json"
    runtime_spec = json.loads(runtime_spec_path.read_text(encoding="utf-8"))
    runtime_spec["acceptance"]["executable_artifact_sha256"] = hashlib.sha256(
        executable_data
    ).hexdigest()
    runtime_spec_data = _canonical_json(runtime_spec)
    if runtime_spec_path.read_text(encoding="utf-8") != runtime_spec_data:
        runtime_spec_path.write_text(runtime_spec_data, encoding="utf-8")
        changed.append(runtime_spec_path.relative_to(root))
    return tuple(changed)


def _tracked_json_files(root: Path) -> tuple[Path, ...]:
    completed = subprocess.run(
        ("git", "-C", str(root), "ls-files", "-z", "*.json"),
        check=True,
        capture_output=True,
    )
    return tuple(root / value.decode() for value in completed.stdout.split(b"\0") if value)


def _committed_source(root: Path, path: Path) -> bytes:
    relative = path.resolve().relative_to(root.resolve()).as_posix()
    completed = subprocess.run(
        ("git", "-C", str(root), "show", f"HEAD:{relative}"),
        check=False,
        capture_output=True,
    )
    return completed.stdout if completed.returncode == 0 else path.read_bytes()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"


def _dump_json_like(source: str, value: Any) -> str:
    first_key = re.search(r"\n( +)\"", source)
    indent = len(first_key.group(1)) if first_key is not None else 2
    return json.dumps(value, ensure_ascii=True, indent=indent) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Remove machine-specific paths and internal routing from JSON artifacts."
    )
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = args.root.resolve()
    for path in sanitize_tracked_json(root):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
