from __future__ import annotations

import argparse
import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any

from evidence_harness_mutation.main_analysis_executable import (
    MiniSweCohort,
    check_main_analysis_executable,
    freeze_main_analysis_executable,
    load_main_analysis_executable,
    preflight_main_analysis_executable,
)
from evidence_harness_mutation.main_analysis_protocol import (
    EXECUTABLE_PROTOCOL,
    MAIN_REPORT,
    MINISWE_COHORT,
    RQ2_REPORT,
    RQ3_REPORT,
    RQ4_REPORT,
)
from evidence_harness_mutation.main_analysis_report import (
    RQ2Report,
    RQ3Report,
    RQ4Report,
    ThesisMainAnalysisReport,
    build_main_analysis_report,
    build_rq2_report,
    build_rq3_report,
    build_rq4_report,
    check_main_analysis_report,
    check_rq2_report,
    check_rq3_report,
    check_rq4_report,
)
from evidence_harness_mutation.main_analysis_transfer_runtime import (
    collect_transfer_trajectories,
    preflight_transfer_runtime,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Freeze, run, build, or verify the thesis main analysis."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    freeze = subparsers.add_parser(
        "freeze-executable",
        help="Freeze the implementation and the outcome-blind transfer cohort.",
    )
    _add_checkouts(freeze)

    executable_preflight = subparsers.add_parser(
        "executable-preflight",
        help="Verify the committed implementation before confirmatory execution.",
    )
    _add_checkouts(executable_preflight)

    subparsers.add_parser("build-rq2", help="Build the fixed RQ2 report.")
    subparsers.add_parser("check-rq2", help="Verify the fixed RQ2 report.")
    subparsers.add_parser("build-rq3", help="Build the fixed RQ3 report.")
    subparsers.add_parser("check-rq3", help="Verify the fixed RQ3 report.")

    transfer_preflight = subparsers.add_parser(
        "transfer-preflight",
        help="Verify the fixed mini-swe-agent transfer runtime.",
    )
    _add_checkouts(transfer_preflight)

    transfer_collect = subparsers.add_parser(
        "transfer-collect",
        help="Collect or resume the fixed 20-task transfer cohort.",
    )
    _add_checkouts(transfer_collect)
    transfer_collect.add_argument("--env-file", required=True, type=Path)

    subparsers.add_parser("build-rq4", help="Build the fixed RQ4 report.")
    subparsers.add_parser("check-rq4", help="Verify the fixed RQ4 report.")
    subparsers.add_parser("build", help="Build the fixed thesis main report.")
    subparsers.add_parser("check", help="Verify the fixed thesis main report.")
    return parser.parse_args()


def _add_checkouts(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--mini-swe-checkout", required=True, type=Path)
    parser.add_argument("--programbench-checkout", required=True, type=Path)


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _write_once(path: Path, data: bytes, label: str) -> str:
    if path.exists():
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"{label} output must be a regular file")
        if path.read_bytes() != data:
            raise ValueError(f"refusing to replace a different {label} output")
        return "unchanged"
    _write_atomic(path, data)
    return "created"


def _task_ids(cohort: MiniSweCohort) -> tuple[str, ...]:
    return tuple(task.instance_id for task in cohort.tasks)


def _print_report(
    report: RQ2Report | RQ3Report | RQ4Report | ThesisMainAnalysisReport,
    *,
    state: str,
) -> None:
    data = report.canonical_bytes()
    print(f"report={report.report_id} state={state}")
    print(f"sha256={hashlib.sha256(data).hexdigest()}")


def main() -> int:
    args = parse_args()
    try:
        if args.command == "freeze-executable":
            cohort, executable_spec = freeze_main_analysis_executable(
                PROJECT_ROOT,
                mini_swe_checkout=args.mini_swe_checkout,
                programbench_checkout=args.programbench_checkout,
            )
            cohort_state = _write_once(
                PROJECT_ROOT / MINISWE_COHORT,
                cohort.canonical_bytes(),
                "mini-swe cohort",
            )
            executable_state = _write_once(
                PROJECT_ROOT / EXECUTABLE_PROTOCOL,
                executable_spec.canonical_bytes(),
                "main-analysis executable",
            )
            print(
                f"executable={executable_spec.executable_id} state={executable_state} "
                f"cohort_state={cohort_state} tasks={len(cohort.tasks)}"
            )
            print(f"sha256={hashlib.sha256(executable_spec.canonical_bytes()).hexdigest()}")
            return 0

        if args.command == "executable-preflight":
            executable_preflight = preflight_main_analysis_executable(
                PROJECT_ROOT,
                mini_swe_checkout=args.mini_swe_checkout,
                programbench_checkout=args.programbench_checkout,
            )
            print(
                "ready_for_confirmatory_execution=true "
                f"executable_commit={executable_preflight.executable.executable_commit} "
                f"tasks={len(executable_preflight.executable.spec.cohort.tasks)}"
            )
            return 0

        if args.command == "transfer-preflight":
            transfer_executable = load_main_analysis_executable(
                PROJECT_ROOT,
                mini_swe_checkout=args.mini_swe_checkout,
                programbench_checkout=args.programbench_checkout,
            )
            task_ids = _task_ids(transfer_executable.spec.cohort)
            runtime_preflight = preflight_transfer_runtime(
                args.mini_swe_checkout,
                args.programbench_checkout,
                task_ids=task_ids,
            )
            print(
                f"ready=true tasks={runtime_preflight.tasks} "
                f"model={runtime_preflight.model} "
                f"executable_commit={transfer_executable.executable_commit}"
            )
            return 0

        if args.command == "transfer-collect":
            collection_executable = load_main_analysis_executable(
                PROJECT_ROOT,
                mini_swe_checkout=args.mini_swe_checkout,
                programbench_checkout=args.programbench_checkout,
            )
            collection_summary = collect_transfer_trajectories(
                project_root=PROJECT_ROOT,
                mini_swe_checkout=args.mini_swe_checkout,
                programbench_checkout=args.programbench_checkout,
                env_file=args.env_file,
                task_ids=_task_ids(collection_executable.spec.cohort),
            )
            print(
                f"tasks={collection_summary.tasks} "
                f"collected={collection_summary.collected} "
                f"resumed={collection_summary.resumed} "
                f"failed={collection_summary.failed}"
            )
            return 0 if collection_summary.failed == 0 else 1

        build_commands: dict[
            str,
            tuple[
                Path,
                Any,
            ],
        ] = {
            "build-rq2": (RQ2_REPORT, build_rq2_report),
            "build-rq3": (RQ3_REPORT, build_rq3_report),
            "build-rq4": (RQ4_REPORT, build_rq4_report),
            "build": (MAIN_REPORT, build_main_analysis_report),
        }
        if args.command in build_commands:
            relative, builder = build_commands[args.command]
            report = builder(PROJECT_ROOT)
            state = _write_once(
                PROJECT_ROOT / relative,
                report.canonical_bytes(),
                report.report_id,
            )
            _print_report(report, state=state)
            return 0

        checkers = {
            "check-rq2": check_rq2_report,
            "check-rq3": check_rq3_report,
            "check-rq4": check_rq4_report,
            "check": check_main_analysis_report,
        }
        if args.command in checkers:
            errors = checkers[args.command](PROJECT_ROOT)
        else:
            errors = check_main_analysis_executable(PROJECT_ROOT)
        if errors:
            for error in errors:
                print(error)
            return 1
        print(f"Thesis main analysis verified: {args.command}")
        return 0
    except (OSError, ValueError) as exc:
        print(f"Thesis main analysis failed: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
