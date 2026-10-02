from __future__ import annotations

import argparse
import hashlib
import os
import tempfile
from pathlib import Path

from evidence_harness_mutation.main_analysis_protocol import (
    MAIN_PROTOCOL,
    check_main_analysis_protocol,
    freeze_main_analysis_protocol,
    preflight_main_analysis_protocol,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_PATH = PROJECT_ROOT / MAIN_PROTOCOL


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Freeze, preflight, or verify the thesis main-analysis protocol."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("freeze", help="Create the protocol before any outcome exists.")
    subparsers.add_parser("preflight", help="Verify prerequisites before outcome collection.")
    subparsers.add_parser("check", help="Verify the committed protocol and source bindings.")
    return parser.parse_args()


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


def main() -> int:
    args = parse_args()
    try:
        if args.command == "freeze":
            protocol = freeze_main_analysis_protocol(PROJECT_ROOT)
            data = protocol.canonical_bytes()
            if PROTOCOL_PATH.exists():
                if PROTOCOL_PATH.read_bytes() != data:
                    raise ValueError("refusing to replace a different main-analysis protocol")
                state = "unchanged"
            else:
                _write_atomic(PROTOCOL_PATH, data)
                state = "created"
            print(f"protocol={protocol.protocol_id} state={state}")
            print(f"sha256={hashlib.sha256(data).hexdigest()}")
            return 0

        if args.command == "preflight":
            result = preflight_main_analysis_protocol(PROJECT_ROOT)
            print(
                "ready_for_held_out_collection="
                f"{str(result.ready_for_held_out_collection).lower()} "
                f"protocol_commit={result.protocol.preregistration_commit}"
            )
            print(
                f"protocol_sha256={result.protocol.file.sha256} "
                f"protected_source_sha256={result.protected_source_sha256} "
                f"protocol_source_sha256={result.protocol_source_sha256}"
            )
            return 0

        errors = check_main_analysis_protocol(PROJECT_ROOT)
        if errors:
            for error in errors:
                print(error)
            return 1
        print("Thesis main-analysis protocol verified")
        return 0
    except (OSError, ValueError) as exc:
        print(f"Thesis main-analysis protocol failed: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
