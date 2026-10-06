from __future__ import annotations

import argparse
import hashlib
import os
import tempfile
from pathlib import Path

from evidence_harness_mutation.tb21_luna_rq2_protocol import (
    LUNA_PROTOCOL,
    LunaRq2Protocol,
    check_luna_rq2_protocol,
    freeze_luna_rq2_protocol,
    preflight_luna_rq2_protocol,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_PATH = PROJECT_ROOT / LUNA_PROTOCOL


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Freeze or verify the TB2.1 Luna RQ2 replication protocol."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "freeze",
        help="Create the outcome-blind Luna protocol before its executable.",
    )
    subparsers.add_parser(
        "preflight",
        help="Verify that the committed protocol is ready for executable freeze.",
    )
    subparsers.add_parser(
        "check",
        help="Verify the committed protocol, source bindings, and chronology.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "freeze":
            protocol = freeze_luna_rq2_protocol(PROJECT_ROOT)
            state = _write_protocol(protocol)
            data = protocol.canonical_bytes()
            print(
                f"protocol={protocol.protocol_id} state={state} "
                f"tasks={protocol.analysis.tasks}"
            )
            print(f"sha256={hashlib.sha256(data).hexdigest()}")
            return 0

        if args.command == "preflight":
            result = preflight_luna_rq2_protocol(PROJECT_ROOT)
            print(
                "ready_for_executable_freeze="
                f"{str(result.ready_for_executable_freeze).lower()} "
                "ready_for_provider_execution="
                f"{str(result.ready_for_provider_execution).lower()} "
                f"protocol_commit={result.protocol.preregistration_commit}"
            )
            print(f"required_executable={result.required_executable}")
            return 0

        errors = check_luna_rq2_protocol(PROJECT_ROOT)
        if errors:
            for error in errors:
                print(error)
            return 1
        print("TB2.1 Luna RQ2 replication protocol verified")
        return 0
    except (OSError, ValueError) as exc:
        print(f"TB2.1 Luna RQ2 replication protocol failed: {exc}")
        return 2


def _write_protocol(protocol: LunaRq2Protocol) -> str:
    data = protocol.canonical_bytes()
    if os.path.lexists(PROTOCOL_PATH):
        if PROTOCOL_PATH.is_symlink() or not PROTOCOL_PATH.is_file():
            raise ValueError("Luna protocol output must be a regular file")
        if PROTOCOL_PATH.read_bytes() != data:
            raise ValueError("refusing to replace a different Luna protocol")
        return "unchanged"
    _write_atomic(PROTOCOL_PATH, data)
    return "created"


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
