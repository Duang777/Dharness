from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MATRIX = PROJECT_ROOT / "evaluation" / "matrix-89.json"
DEFAULT_JOBS_DIR = PROJECT_ROOT / "runs" / "terminal-bench-2"
HTTPS_APT_TASKS = frozenset({"fix-git"})
RUN_CONFIG_NAME = "run-config.json"
ACTIVE_CHILD_NAME = "active-child.json"
SIGNAL_GRACE_SEC = 5.0
PROCESS_GROUP_EXIT_GRACE_SEC = 2.0
PROCESS_GROUP_TERM_GRACE_SEC = 5.0
PROCESS_GROUP_KILL_GRACE_SEC = 5.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a full evaluation matrix serially as resumable single-task jobs."
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("EVIDENCE_HARNESS_MODEL"),
        help="LiteLLM model name. Defaults to EVIDENCE_HARNESS_MODEL.",
    )
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument("--jobs-dir", type=Path, default=DEFAULT_JOBS_DIR)
    parser.add_argument(
        "--run-name",
        default=datetime.now(UTC).strftime("full89-%Y-%m-%d__%H-%M-%S"),
    )
    parser.add_argument(
        "--agent-kwarg",
        action="append",
        default=[],
        metavar="KEY=VALUE",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def completed_task_results(run_root: Path) -> dict[str, Path]:
    results: dict[str, Path] = {}
    if not run_root.exists():
        return results
    for result_path in sorted(run_root.rglob("result.json")):
        try:
            data = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict) or not data.get("trial_name"):
            continue
        if _is_interrupted_result(data):
            continue
        task_name = _task_name(data)
        if task_name is None:
            continue
        previous = results.get(task_name)
        if previous is not None:
            raise ValueError(
                f"multiple completed trials for {task_name}: {previous} and {result_path}"
            )
        results[task_name] = result_path
    return results


def load_task_names(matrix_path: Path) -> list[str]:
    data = json.loads(matrix_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError(f"unsupported matrix schema in {matrix_path}")
    tasks = data.get("tasks")
    if not isinstance(tasks, list) or not tasks:
        raise ValueError(f"matrix must contain at least one task: {matrix_path}")
    names = [item.get("name") if isinstance(item, dict) else None for item in tasks]
    if not all(isinstance(name, str) and name for name in names):
        raise ValueError(f"every matrix task must have a non-empty name: {matrix_path}")
    task_names = [str(name) for name in names]
    if len(task_names) != len(set(task_names)):
        raise ValueError(f"matrix task names must be unique: {matrix_path}")
    return task_names


def run_configuration(args: argparse.Namespace) -> dict[str, object]:
    agent_kwargs = json.dumps(args.agent_kwarg, separators=(",", ":"), ensure_ascii=True)
    return {
        "schema_version": 1,
        "matrix_sha256": _sha256_file(args.matrix),
        "model": args.model,
        "env_file_sha256": _sha256_file(args.env_file),
        "agent_kwargs_sha256": hashlib.sha256(agent_kwargs.encode()).hexdigest(),
        "source_sha256": _source_sha256(),
        "https_apt_tasks": sorted(HTTPS_APT_TASKS),
    }


def bind_run_configuration(run_root: Path, current: dict[str, object]) -> None:
    config_path = run_root / RUN_CONFIG_NAME
    if not config_path.exists():
        if completed_task_results(run_root):
            raise ValueError(f"existing run has no {RUN_CONFIG_NAME}: {run_root}")
        _write_json(config_path, current)
        return

    existing = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(existing, dict):
        raise ValueError(f"invalid run configuration: {config_path}")
    if existing != current:
        changed = sorted(set(existing) | set(current))
        changed = [key for key in changed if existing.get(key) != current.get(key)]
        raise ValueError("run configuration changed: " + ", ".join(changed))


def assert_no_active_child(run_root: Path) -> None:
    active_path = run_root / ACTIVE_CHILD_NAME
    if not active_path.exists():
        return
    try:
        active = json.loads(active_path.read_text(encoding="utf-8"))
        pid = active.get("pid") if isinstance(active, dict) else None
        process_group_id = active.get("process_group_id") if isinstance(active, dict) else None
    except (OSError, json.JSONDecodeError):
        pid = None
        process_group_id = None
    if isinstance(process_group_id, int) and _process_group_is_alive(process_group_id):
        raise ValueError(f"Harbor process group {process_group_id} is still active for {run_root}")
    if process_group_id is None and isinstance(pid, int) and _pid_is_alive(pid):
        raise ValueError(f"Harbor child process {pid} is still active for {run_root}")
    active_path.unlink(missing_ok=True)


def build_task_command(
    *,
    model: str,
    env_file: Path,
    matrix: Path,
    run_root: Path,
    job_name: str,
    task_name: str,
    agent_kwargs: list[str],
) -> list[str]:
    command = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "run_evaluation.py"),
        "--model",
        model,
        "--env-file",
        str(env_file),
        "--matrix",
        str(matrix),
        "--jobs-dir",
        str(run_root),
        "--job-name",
        job_name,
        "--n-concurrent",
        "1",
        "--include-task-name",
        task_name,
    ]
    if task_name in HTTPS_APT_TASKS:
        command.append("--debian-https-sources")
    for value in agent_kwargs:
        command.extend(("--agent-kwarg", value))
    return command


def run_full_evaluation(args: argparse.Namespace) -> int:
    if not args.model:
        raise ValueError("no model configured; pass --model or set EVIDENCE_HARNESS_MODEL")
    if not args.dry_run and not args.env_file.is_file():
        raise ValueError(f"env file does not exist: {args.env_file}")
    if Path(args.run_name).name != args.run_name or args.run_name in {".", ".."}:
        raise ValueError("--run-name must be a single path component")

    task_names = load_task_names(args.matrix)
    current_config = run_configuration(args)
    if args.dry_run:
        print(
            f"Dry run OK - {len(task_names)} serial task(s); "
            f"configuration {current_config['matrix_sha256']}"
        )
        return 0

    run_root = args.jobs_dir / args.run_name
    run_root.mkdir(parents=True, exist_ok=True)
    lock_path = run_root / "orchestrator.lock"
    with lock_path.open("w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError(f"another orchestrator is active for {run_root}") from exc
        assert_no_active_child(run_root)
        bind_run_configuration(run_root, current_config)
        return _run_tasks(args, run_root, task_names, current_config)


def _run_tasks(
    args: argparse.Namespace,
    run_root: Path,
    task_names: list[str],
    bound_config: dict[str, object],
) -> int:
    pid_path = run_root / "orchestrator.pid"
    progress_path = run_root / "progress.jsonl"
    pid_path.write_text(f"{os.getpid()}\n", encoding="ascii")
    completed = completed_task_results(run_root)
    _append_event(
        progress_path,
        "run_started",
        completed=len(completed),
        total=len(task_names),
    )

    for index, task_name in enumerate(task_names, start=1):
        current_config = run_configuration(args)
        if current_config != bound_config:
            raise ValueError("run configuration changed after the orchestrator started")
        bind_run_configuration(run_root, current_config)
        if task_name in completed:
            print(f"[{index}/{len(task_names)}] skip completed {task_name}", flush=True)
            continue

        job_name = _next_job_name(run_root, index, task_name)
        command = build_task_command(
            model=args.model,
            env_file=args.env_file,
            matrix=args.matrix,
            run_root=run_root,
            job_name=job_name,
            task_name=task_name,
            agent_kwargs=args.agent_kwarg,
        )
        print(f"[{index}/{len(task_names)}] start {task_name}", flush=True)
        _append_event(
            progress_path,
            "task_launching",
            index=index,
            total=len(task_names),
            task=task_name,
            job=job_name,
        )
        try:
            returncode = _run_child(command, run_root, task_name, job_name)
        except OSError as exc:
            _append_event(
                progress_path,
                "run_stopped",
                index=index,
                total=len(task_names),
                task=task_name,
                job=job_name,
                reason=f"child launch failed: {type(exc).__name__}",
            )
            raise
        if returncode:
            _append_event(
                progress_path,
                "run_stopped",
                index=index,
                total=len(task_names),
                task=task_name,
                job=job_name,
                exit_code=returncode,
            )
            return returncode

        completed = completed_task_results(run_root)
        result_path = completed.get(task_name)
        if result_path is None:
            _append_event(
                progress_path,
                "run_stopped",
                index=index,
                total=len(task_names),
                task=task_name,
                job=job_name,
                reason="missing trial result",
            )
            return 3
        result = json.loads(result_path.read_text(encoding="utf-8"))
        _append_event(
            progress_path,
            "task_completed",
            index=index,
            total=len(task_names),
            task=task_name,
            job=job_name,
            reward=_reward(result),
            exception_type=_exception_type(result),
            result_path=str(result_path.relative_to(run_root)),
        )
        print(f"[{index}/{len(task_names)}] completed {task_name}", flush=True)

    _append_event(progress_path, "run_completed", completed=len(task_names), total=len(task_names))
    return 0


def _run_child(command: list[str], run_root: Path, task_name: str, job_name: str) -> int:
    active_path = run_root / ACTIVE_CHILD_NAME
    previous_handlers: dict[signal.Signals, Any] = {}
    pending_signals: list[int] = []
    child: subprocess.Popen[bytes] | None = None

    def forward(signum: int, _frame: object) -> None:
        pending_signals.append(signum)
        if child is None:
            return
        with suppress(ProcessLookupError):
            os.killpg(child.pid, signum)

    for signum in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[signum] = signal.signal(signum, forward)
    try:
        if pending_signals:
            return -pending_signals[-1]
        child = subprocess.Popen(command, cwd=PROJECT_ROOT, start_new_session=True)
        _write_json(
            active_path,
            {
                "pid": child.pid,
                "process_group_id": child.pid,
                "task": task_name,
                "job": job_name,
                "started_at": datetime.now(UTC).isoformat(),
            },
        )
        for pending_signum in pending_signals:
            with suppress(ProcessLookupError):
                os.killpg(child.pid, pending_signum)
        returncode = _wait_for_child(child, pending_signals)
        return returncode
    finally:
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        if child is not None:
            _ensure_process_group_stopped(child.pid)
        if child is None or not _process_group_is_alive(child.pid):
            active_path.unlink(missing_ok=True)


def _next_job_name(run_root: Path, index: int, task_name: str) -> str:
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", task_name).strip("-") or "task"
    base = f"{index:03d}-{safe_name}"
    candidate = base
    attempt = 2
    while (run_root / candidate).exists():
        candidate = f"{base}-attempt-{attempt}"
        attempt += 1
    return candidate


def _task_name(data: dict[str, Any]) -> str | None:
    value = data.get("task_name")
    if isinstance(value, str) and value:
        return value.rsplit("/", 1)[-1]
    task_id = data.get("task_id")
    if isinstance(task_id, dict):
        for key in ("name", "path"):
            value = task_id.get(key)
            if isinstance(value, str) and value:
                return Path(value).name
    return None


def _reward(data: dict[str, Any]) -> float | None:
    verifier = data.get("verifier_result")
    if not isinstance(verifier, dict):
        return None
    rewards = verifier.get("rewards")
    if not isinstance(rewards, dict):
        return None
    reward = rewards.get("reward")
    return float(reward) if isinstance(reward, int | float) else None


def _exception_type(data: dict[str, Any]) -> str | None:
    exception = data.get("exception_info")
    if not isinstance(exception, dict):
        return None
    value = exception.get("exception_type") or exception.get("type")
    return value if isinstance(value, str) else None


def _is_interrupted_result(data: dict[str, Any]) -> bool:
    return _exception_type(data) in {"CancelledError", "KeyboardInterrupt"}


def _append_event(path: Path, event: str, **fields: object) -> None:
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "event": event,
        **fields,
    }
    with path.open("a", encoding="utf-8") as stream:
        _write_json_line(stream, record)


def _source_sha256() -> str:
    paths = [
        PROJECT_ROOT / "pyproject.toml",
        PROJECT_ROOT / "uv.lock",
        PROJECT_ROOT / "evaluation" / "debian-https.sources",
        PROJECT_ROOT / "scripts" / "run_evaluation.py",
        PROJECT_ROOT / "scripts" / "run_full_evaluation.py",
        *sorted((PROJECT_ROOT / "src" / "evidence_harness").rglob("*.py")),
    ]
    digest = hashlib.sha256()
    for path in paths:
        digest.update(str(path.relative_to(PROJECT_ROOT)).encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _pid_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _process_group_is_alive(process_group_id: int) -> bool:
    try:
        completed = subprocess.run(
            ["ps", "-axo", "pgid=,stat="],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        completed = None
    if completed is not None and completed.returncode == 0:
        for line in completed.stdout.splitlines():
            fields = line.split(maxsplit=1)
            if len(fields) != 2:
                continue
            try:
                listed_group_id = int(fields[0])
            except ValueError:
                continue
            if listed_group_id == process_group_id and not fields[1].startswith("Z"):
                return True
        return False

    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _wait_for_child(
    child: subprocess.Popen[bytes],
    pending_signals: list[int],
) -> int:
    termination_deadline: float | None = None
    while True:
        if pending_signals and termination_deadline is None:
            termination_deadline = time.monotonic() + SIGNAL_GRACE_SEC
        try:
            return child.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            if termination_deadline is None or time.monotonic() < termination_deadline:
                continue
            with suppress(ProcessLookupError):
                os.killpg(child.pid, signal.SIGKILL)
            try:
                return child.wait(timeout=5)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(f"Harbor child process {child.pid} did not stop") from exc


def _ensure_process_group_stopped(process_group_id: int) -> None:
    if _wait_for_process_group_exit(
        process_group_id,
        timeout_sec=PROCESS_GROUP_EXIT_GRACE_SEC,
    ):
        return
    with suppress(ProcessLookupError):
        os.killpg(process_group_id, signal.SIGTERM)
    if _wait_for_process_group_exit(
        process_group_id,
        timeout_sec=PROCESS_GROUP_TERM_GRACE_SEC,
    ):
        return
    with suppress(ProcessLookupError):
        os.killpg(process_group_id, signal.SIGKILL)
    if not _wait_for_process_group_exit(
        process_group_id,
        timeout_sec=PROCESS_GROUP_KILL_GRACE_SEC,
    ):
        raise RuntimeError(f"process group {process_group_id} did not stop")


def _wait_for_process_group_exit(process_group_id: int, timeout_sec: float) -> bool:
    deadline = time.monotonic() + timeout_sec
    while _process_group_is_alive(process_group_id):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)
    return True


def _write_json(path: Path, value: dict[str, object]) -> None:
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary_path.replace(path)


def _write_json_line(stream: TextIO, record: dict[str, object]) -> None:
    stream.write(json.dumps(record, sort_keys=True) + "\n")
    stream.flush()


def main() -> int:
    args = parse_args()
    try:
        return run_full_evaluation(args)
    except (OSError, RuntimeError, ValueError, json.JSONDecodeError) as exc:
        print(f"full evaluation preflight failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
