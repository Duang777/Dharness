from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

STATIC_GATES = (
    ("Ruff lint", ("uv", "run", "ruff", "check", ".")),
    ("Ruff format", ("uv", "run", "ruff", "format", "--check", ".")),
    ("mypy", ("uv", "run", "mypy", "src", "scripts", "tests")),
    (
        "pytest",
        (
            "uv",
            "run",
            "pytest",
            "--cov=evidence_harness",
            "--cov=evidence_harness_mutation",
        ),
    ),
    ("build", ("uv", "build")),
    (
        "EvidenceHarness schema",
        (
            "uv",
            "run",
            "harbor",
            "agent",
            "schema",
            "evidence_harness.harbor_agent:EvidenceHarnessAgent",
        ),
    ),
    (
        "JournalReplayAgent schema",
        (
            "uv",
            "run",
            "harbor",
            "agent",
            "schema",
            "evidence_harness.replay_agent:JournalReplayAgent",
        ),
    ),
    (
        "CompletionIsolationExperimentAgent schema",
        (
            "uv",
            "run",
            "harbor",
            "agent",
            "schema",
            "evidence_harness.isolation_experiment_agent:CompletionIsolationExperimentAgent",
        ),
    ),
    (
        "10-task dry-run",
        (
            "uv",
            "run",
            "python",
            "scripts/run_evaluation.py",
            "--model",
            "openai/mock-model",
            "--dry-run",
        ),
    ),
    (
        "20-task dry-run",
        (
            "uv",
            "run",
            "python",
            "scripts/run_evaluation.py",
            "--matrix",
            "evaluation/matrix-20.json",
            "--model",
            "openai/mock-model",
            "--dry-run",
        ),
    ),
    (
        "89-task dry-run",
        (
            "uv",
            "run",
            "python",
            "scripts/run_evaluation.py",
            "--matrix",
            "evaluation/matrix-89.json",
            "--model",
            "openai/mock-model",
            "--dry-run",
        ),
    ),
    (
        "full-run orchestrator dry-run",
        (
            "uv",
            "run",
            "python",
            "scripts/run_full_evaluation.py",
            "--model",
            "openai/mock-model",
            "--env-file",
            "/dev/null",
            "--dry-run",
        ),
    ),
    (
        "completion calibration artifacts",
        (
            "uv",
            "run",
            "python",
            "scripts/completion_calibration.py",
            "check",
        ),
    ),
    (
        "PrefixBench readiness",
        (
            "uv",
            "run",
            "python",
            "scripts/prefixbench.py",
            "check",
        ),
    ),
    (
        "PrefixBench development readiness",
        (
            "uv",
            "run",
            "python",
            "scripts/prefixbench.py",
            "check",
            "--canonical",
            "evaluation/prefixbench-v1-development-canonical.json",
            "--matrix",
            "evaluation/matrix-prefixbench-development.json",
            "--expected-task-count",
            "28",
            "--report",
            "evaluation/prefixbench-v1-development-readiness.json",
        ),
    ),
    (
        "PrefixBench development offline campaign",
        (
            "uv",
            "run",
            "python",
            "scripts/prefixbench_campaign.py",
            "check",
        ),
    ),
    (
        "PrefixBench test preregistration",
        (
            "uv",
            "run",
            "python",
            "scripts/prefixbench_test_campaign.py",
            "preflight",
        ),
    ),
    (
        "PrefixBench development offline analysis",
        (
            "uv",
            "run",
            "python",
            "scripts/prefixbench_analysis.py",
            "check",
        ),
    ),
    (
        "completion isolation support census",
        (
            "uv",
            "run",
            "python",
            "scripts/completion_isolation_census.py",
            "check",
        ),
    ),
    (
        "completion isolation experiments",
        (
            "uv",
            "run",
            "python",
            "scripts/completion_isolation_experiments.py",
            "check",
        ),
    ),
    (
        "delivery artifacts",
        ("uv", "run", "python", "scripts/verify_delivery.py"),
    ),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run every deterministic delivery gate and Docker smoke test."
    )
    parser.add_argument(
        "--skip-smoke",
        action="store_true",
        help="Skip the real Harbor/Docker smoke test.",
    )
    return parser.parse_args()


def run_gate(name: str, command: tuple[str, ...]) -> bool:
    print(f"\n==> {name}", flush=True)
    completed = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
    if completed.returncode:
        print(f"{name} failed with exit code {completed.returncode}", file=sys.stderr)
        return False
    return True


def run_smoke() -> bool:
    port = _available_port()
    baseline_resources = _docker_isolation_resources()
    if baseline_resources is None:
        return False
    with tempfile.TemporaryDirectory(prefix="evidence-harness-smoke-") as temporary:
        jobs_dir = Path(temporary) / "jobs"
        server = subprocess.Popen(
            [
                sys.executable,
                str(PROJECT_ROOT / "scripts/mock_openai_server.py"),
                "--port",
                str(port),
            ],
            cwd=PROJECT_ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            if not _wait_for_server(port, server):
                print("mock model server did not start", file=sys.stderr)
                return False

            env = os.environ.copy()
            env["OPENAI_API_KEY"] = "test-key"
            command = (
                "uv",
                "run",
                "harbor",
                "run",
                "--path",
                "tests/fixtures/hello-world",
                "--agent",
                "evidence_harness.harbor_agent:EvidenceHarnessAgent",
                "--model",
                "openai/mock-model",
                "--agent-kwarg",
                f"api_base=http://127.0.0.1:{port}/v1",
                "--agent-kwarg",
                "max_turns=6",
                "--agent-kwarg",
                "max_environment_calls=10",
                "--agent-kwarg",
                "max_model_call_timeout_sec=30",
                "--jobs-dir",
                str(jobs_dir),
                "--job-name",
                "delivery-smoke",
                "--n-concurrent",
                "1",
            )
            completed = subprocess.run(
                command,
                cwd=PROJECT_ROOT,
                env=env,
                check=False,
            )
            if completed.returncode:
                print(
                    f"Docker smoke failed with exit code {completed.returncode}",
                    file=sys.stderr,
                )
                return False
            return _validate_smoke_result(jobs_dir, baseline_resources)
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)


def _available_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_server(port: int, process: subprocess.Popen[bytes]) -> bool:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return False
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def _validate_smoke_result(
    jobs_dir: Path,
    baseline_resources: frozenset[str],
) -> bool:
    trial_results = []
    for result_path in jobs_dir.rglob("result.json"):
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(result, dict) and result.get("trial_name"):
            trial_results.append(result)

    if len(trial_results) != 1:
        print(
            f"Docker smoke produced {len(trial_results)} trial results instead of one",
            file=sys.stderr,
        )
        return False
    result = trial_results[0]
    reward = (
        result.get("verifier_result", {}).get("rewards", {}).get("reward")
        if isinstance(result.get("verifier_result"), dict)
        else None
    )
    metadata = (
        result.get("agent_result", {}).get("metadata", {}).get("evidence_harness", {})
        if isinstance(result.get("agent_result"), dict)
        else {}
    )
    if reward != 1.0 or metadata.get("stop_reason") != "verified":
        print(
            "Docker smoke did not produce reward 1.0 with verified stop reason",
            file=sys.stderr,
        )
        return False

    journal_paths = list(jobs_dir.rglob("agent/evidence-harness/events.jsonl"))
    if len(journal_paths) != 1:
        print(
            f"Docker smoke produced {len(journal_paths)} Harness journals instead of one",
            file=sys.stderr,
        )
        return False
    try:
        events = [
            json.loads(line)
            for line in journal_paths[0].read_text(encoding="utf-8").splitlines()
            if line
        ]
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Docker smoke journal is invalid: {exc}", file=sys.stderr)
        return False
    verification_events = [event for event in events if event.get("type") == "verification_receipt"]
    if len(verification_events) != 1:
        print("Docker smoke did not produce one verification receipt", file=sys.stderr)
        return False
    isolation = verification_events[0].get("payload", {}).get("isolation", {})
    source = isolation.get("source", {}) if isinstance(isolation, dict) else {}
    checks = isolation.get("checks", []) if isinstance(isolation, dict) else []
    added_paths = {
        path
        for check in checks
        if isinstance(check, dict)
        for path in check.get("delta", {}).get("added", [])
    }
    if (
        "/app/verification-only.txt" not in added_paths
        or source.get("diff_sha256_before") != source.get("diff_sha256_after")
        or source.get("remained_paused") is not True
        or source.get("resumed") is not True
        or isolation.get("snapshot_image_disposed") is not True
    ):
        print(
            "Docker smoke did not prove isolated writes and unchanged live state",
            file=sys.stderr,
        )
        return False
    if _docker_isolation_resources() != baseline_resources:
        print("Docker smoke leaked an isolation container or image", file=sys.stderr)
        return False
    print("Docker smoke passed: reward=1.0, isolated write observed, live source unchanged")
    return True


def _docker_isolation_resources() -> frozenset[str] | None:
    commands = (
        (
            "container",
            (
                "docker",
                "ps",
                "-aq",
                "--filter",
                "label=evidence-harness.isolation=true",
            ),
        ),
        (
            "image",
            (
                "docker",
                "image",
                "ls",
                "--filter",
                "reference=evidence-harness-isolation:*",
                "--format",
                "{{.ID}}",
            ),
        ),
    )
    resources: set[str] = set()
    for kind, command in commands:
        completed = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        if completed.returncode:
            print(
                f"Could not inspect Docker isolation {kind} resources: {completed.stderr.strip()}",
                file=sys.stderr,
            )
            return None
        resources.update(f"{kind}:{item}" for item in completed.stdout.split())
    return frozenset(resources)


def main() -> int:
    args = parse_args()
    for name, command in STATIC_GATES:
        if not run_gate(name, command):
            return 1
    if not args.skip_smoke:
        print("\n==> Harbor/Docker smoke", flush=True)
        if not run_smoke():
            return 1
    print("\nall delivery gates passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
