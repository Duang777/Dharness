from __future__ import annotations

import argparse
import hashlib
import os
import tempfile
from pathlib import Path

from evidence_harness_mutation.tb21_sensitivity_protocol import (
    SENSITIVITY_MATRIX,
    SENSITIVITY_PROTOCOL,
    ExternalRepositories,
    Tb21SensitivityFreeze,
    check_tb21_sensitivity_protocol,
    freeze_tb21_sensitivity_protocol,
    preflight_tb21_sensitivity_protocol,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = PROJECT_ROOT / SENSITIVITY_MATRIX
PROTOCOL_PATH = PROJECT_ROOT / SENSITIVITY_PROTOCOL


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Freeze or verify the Terminal-Bench 2.1 sensitivity preregistration."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    freeze = subparsers.add_parser(
        "freeze",
        help="Create the fixed matrix and protocol before any sensitivity outcome.",
    )
    _add_checkouts(freeze)
    preflight = subparsers.add_parser(
        "preflight",
        help="Revalidate the committed protocol and pinned external sources.",
    )
    _add_checkouts(preflight)
    subparsers.add_parser(
        "check",
        help="Verify the committed matrix, protocol, source bindings, and chronology.",
    )
    return parser.parse_args()


def _add_checkouts(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--tb20-checkout", type=Path, required=True)
    parser.add_argument("--tb21-checkout", type=Path, required=True)


def _repositories(args: argparse.Namespace) -> ExternalRepositories:
    return ExternalRepositories(
        tb20=args.tb20_checkout,
        tb21=args.tb21_checkout,
    )


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


def _existing_state(path: Path, expected: bytes, label: str) -> str:
    if not os.path.lexists(path):
        return "missing"
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} output must be a regular file")
    if path.read_bytes() != expected:
        raise ValueError(f"refusing to replace a different {label} output")
    return "unchanged"


def _write_bundle(bundle: Tb21SensitivityFreeze) -> tuple[str, str]:
    matrix_data = bundle.matrix.canonical_bytes()
    protocol_data = bundle.protocol.canonical_bytes()
    matrix_state = _existing_state(MATRIX_PATH, matrix_data, "sensitivity matrix")
    protocol_state = _existing_state(PROTOCOL_PATH, protocol_data, "sensitivity protocol")
    if matrix_state == "missing":
        _write_atomic(MATRIX_PATH, matrix_data)
        matrix_state = "created"
    if protocol_state == "missing":
        _write_atomic(PROTOCOL_PATH, protocol_data)
        protocol_state = "created"
    return matrix_state, protocol_state


def main() -> int:
    args = parse_args()
    try:
        if args.command == "freeze":
            bundle = freeze_tb21_sensitivity_protocol(
                PROJECT_ROOT,
                _repositories(args),
            )
            matrix_state, protocol_state = _write_bundle(bundle)
            matrix_data = bundle.matrix.canonical_bytes()
            protocol_data = bundle.protocol.canonical_bytes()
            print(
                f"protocol={bundle.protocol.protocol_id} "
                f"protocol_state={protocol_state} matrix_state={matrix_state} "
                f"tasks={len(bundle.matrix.tasks)}"
            )
            print(
                f"protocol_sha256={hashlib.sha256(protocol_data).hexdigest()} "
                f"matrix_sha256={hashlib.sha256(matrix_data).hexdigest()}"
            )
            return 0

        if args.command == "preflight":
            result = preflight_tb21_sensitivity_protocol(
                PROJECT_ROOT,
                _repositories(args),
            )
            print(
                "ready_for_executable_freeze="
                f"{str(result.ready_for_executable_freeze).lower()} "
                "ready_for_provider_execution="
                f"{str(result.ready_for_provider_execution).lower()} "
                f"protocol_commit={result.protocol.preregistration_commit}"
            )
            print(f"required_executable={result.required_executable}")
            return 0

        errors = check_tb21_sensitivity_protocol(PROJECT_ROOT)
        if errors:
            for error in errors:
                print(error)
            return 1
        print("Terminal-Bench 2.1 sensitivity preregistration verified")
        return 0
    except (OSError, ValueError) as exc:
        print(f"Terminal-Bench 2.1 sensitivity preregistration failed: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
