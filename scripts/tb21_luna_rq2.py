from __future__ import annotations

import argparse
import hashlib
import os
import tempfile
from pathlib import Path

from evidence_harness_mutation.tb21_luna_rq2 import (
    build,
    check,
    collect,
    freeze,
    preflight,
)
from evidence_harness_mutation.tb21_luna_rq2_protocol import LUNA_EXECUTABLE

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Freeze, run, build, or verify the TB2.1 Luna RQ2 replication."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("freeze", help="Freeze the outcome-blind Luna executable.")
    executable_preflight = subparsers.add_parser(
        "preflight",
        help="Prove the frozen Harbor selector without calling a Provider.",
    )
    executable_preflight.add_argument("--tb21-checkout", required=True, type=Path)
    collection = subparsers.add_parser(
        "collect",
        help="Collect or resume the fixed 61-task Luna cohort.",
    )
    collection.add_argument("--tb21-checkout", required=True, type=Path)
    subparsers.add_parser("build", help="Build available Luna RQ2 artifacts.")
    subparsers.add_parser("check", help="Verify the executable and present artifacts.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "freeze":
            manifest = freeze(PROJECT_ROOT)
            state = _write_bundle(((LUNA_EXECUTABLE, manifest.canonical_bytes()),))[0]
            data = manifest.canonical_bytes()
            print(
                f"executable={manifest.executable_id} state={state} "
                f"tasks={manifest.harbor.expected_member_count}"
            )
            print(f"sha256={hashlib.sha256(data).hexdigest()}")
            return 0

        if args.command == "preflight":
            result = preflight(PROJECT_ROOT, tb21_checkout=args.tb21_checkout)
            print(
                "ready_for_provider_execution="
                f"{str(result.ready_for_provider_execution).lower()} "
                f"executable_commit={result.executable_commit}"
            )
            for blocker in result.blockers:
                print(f"blocker={blocker.code}")
                print(f"reason={blocker.message}")
            return 0 if result.ready_for_provider_execution else 1

        if args.command == "collect":
            summary = collect(PROJECT_ROOT, tb21_checkout=args.tb21_checkout)
            print(
                f"status={summary.status} tasks={summary.tasks} "
                f"terminal={summary.terminal} pending={summary.pending} "
                f"interrupted={summary.interrupted}"
            )
            return 0 if summary.status == "complete" else 1

        if args.command == "build":
            bundle = build(PROJECT_ROOT)
            states = _write_bundle(
                tuple((Path(entry.path), entry.data) for entry in bundle.entries)
            )
            for entry, state in zip(bundle.entries, states, strict=True):
                print(
                    f"artifact={entry.path} state={state} "
                    f"sha256={hashlib.sha256(entry.data).hexdigest()}"
                )
            if bundle.waiting_for:
                print("waiting_for=" + ",".join(bundle.waiting_for))
            return 0 if bundle.final_available else 1

        errors = check(PROJECT_ROOT)
        if errors:
            for error in errors:
                print(error)
            return 1
        print("TB2.1 Luna RQ2 replication executable verified")
        return 0
    except (OSError, ValueError) as exc:
        print(f"TB2.1 Luna RQ2 replication failed: {exc}")
        return 2


def _write_bundle(entries: tuple[tuple[Path, bytes], ...]) -> tuple[str, ...]:
    states: list[str] = []
    for relative, data in entries:
        target = PROJECT_ROOT / relative
        if os.path.lexists(target):
            if target.is_symlink() or not target.is_file():
                raise ValueError(f"output must be a regular file: {relative}")
            if target.read_bytes() != data:
                raise ValueError(f"refusing to replace a different output: {relative}")
            states.append("unchanged")
        else:
            states.append("missing")
    for index, ((relative, data), state) in enumerate(zip(entries, states, strict=True)):
        if state == "missing":
            _write_atomic(PROJECT_ROOT / relative, data)
            states[index] = "created"
    return tuple(states)


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


if __name__ == "__main__":
    raise SystemExit(main())
