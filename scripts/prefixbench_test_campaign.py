from __future__ import annotations

import argparse
import hashlib
import os
import tempfile
from pathlib import Path

from evidence_harness_mutation.prefixbench_test_campaign import (
    TEST_CAMPAIGN,
    TEST_PROTOCOL,
    PrefixBenchTestMutationProtocol,
    build_prefixbench_test_campaign,
    check_prefixbench_test_campaign,
    freeze_prefixbench_test_protocol,
    preflight_prefixbench_test_campaign,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PROTOCOL_PATH = PROJECT_ROOT / TEST_PROTOCOL
REPORT_PATH = PROJECT_ROOT / TEST_CAMPAIGN
FROZEN_PROTOCOL_SHA256 = "a6972d068a5595396ccea6bc3d3d66bc0d609c1596aeb16d73fa961e33000d2c"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preregister, preflight, build, or verify the PrefixBench test campaign."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("freeze", help="Create the held-out protocol before collection.")
    subparsers.add_parser("preflight", help="Verify the committed collection prerequisites.")
    subparsers.add_parser(
        "verify-frozen",
        help="Verify the frozen protocol without admitting the current runtime.",
    )
    subparsers.add_parser("build", help="Build the fixed 61-task test campaign.")
    subparsers.add_parser("check", help="Verify the committed test campaign.")
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


def verify_frozen_protocol() -> tuple[PrefixBenchTestMutationProtocol, str]:
    data = PROTOCOL_PATH.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != FROZEN_PROTOCOL_SHA256:
        raise ValueError(f"test protocol hash changed: {digest} != {FROZEN_PROTOCOL_SHA256}")
    protocol = PrefixBenchTestMutationProtocol.model_validate_json(data)
    if data != protocol.canonical_bytes():
        raise ValueError("test protocol is not canonical JSON")
    return protocol, digest


def main() -> int:
    args = parse_args()
    try:
        if args.command == "freeze":
            protocol = freeze_prefixbench_test_protocol(PROJECT_ROOT)
            data = protocol.canonical_bytes()
            if PROTOCOL_PATH.exists():
                if PROTOCOL_PATH.read_bytes() != data:
                    raise ValueError("refusing to replace a different test protocol")
                state = "unchanged"
            else:
                _write_atomic(PROTOCOL_PATH, data)
                state = "created"
            print(f"protocol={protocol.protocol_id} tasks={protocol.task_count} state={state}")
            print(f"sha256={hashlib.sha256(data).hexdigest()}")
            return 0

        if args.command == "preflight":
            result = preflight_prefixbench_test_campaign(PROJECT_ROOT)
            print(
                f"ready_for_collection={str(result.ready_for_collection).lower()} "
                f"tasks={result.task_count} "
                f"run_root={result.collection_run_root}"
            )
            print(
                f"producer_commit={result.protocol.preregistration_commit} "
                f"runtime_source_sha256={result.runtime_source.sha256} "
                f"protocol_sha256={result.protocol.file.sha256}"
            )
            return 0

        if args.command == "verify-frozen":
            protocol, digest = verify_frozen_protocol()
            print(
                "frozen_protocol_valid=true "
                f"tasks={protocol.task_count} "
                f"protocol={protocol.protocol_id}"
            )
            print(f"sha256={digest}")
            return 0

        if args.command == "build":
            report = build_prefixbench_test_campaign(PROJECT_ROOT)
            data = report.canonical_bytes()
            _write_atomic(REPORT_PATH, data)
            outcomes = report.summary.outcomes
            print(
                f"tasks={report.summary.tasks} "
                f"projected_attempts={report.summary.projected_attempts} "
                f"verified_attempts={report.summary.verified_attempts} "
                f"scheduled={outcomes.scheduled}"
            )
            print(
                f"mutation_not_applicable={outcomes.mutation_not_applicable} "
                f"offline_invalid={outcomes.offline_invalid} "
                f"oracle_equivalent={outcomes.oracle_equivalent} "
                f"offline_violation={outcomes.offline_violation} "
                f"other_oracle_change={outcomes.other_oracle_change}"
            )
            print(f"sha256={hashlib.sha256(data).hexdigest()}")
            return 0

        errors = check_prefixbench_test_campaign(
            PROJECT_ROOT,
            report_path=REPORT_PATH,
        )
        if errors:
            for error in errors:
                print(error)
            return 1
        print("PrefixBench test offline campaign verified")
        return 0
    except (OSError, ValueError) as exc:
        print(f"PrefixBench test campaign failed: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
