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
    ("pytest", ("uv", "run", "pytest", "--cov=evidence_harness")),
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
            return _validate_smoke_result(jobs_dir)
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


def _validate_smoke_result(jobs_dir: Path) -> bool:
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
    print("Docker smoke passed: reward=1.0, stop_reason=verified")
    return True


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
