from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MATRIX = PROJECT_ROOT / "evaluation" / "matrix-89.json"
DEFAULT_JOBS_DIR = PROJECT_ROOT / "runs" / "terminal-bench-2"
HTTPS_APT_TASKS = frozenset({"fix-git"})


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
    if not args.env_file.is_file():
        raise ValueError(f"env file does not exist: {args.env_file}")
    if Path(args.run_name).name != args.run_name or args.run_name in {".", ".."}:
        raise ValueError("--run-name must be a single path component")

    task_names = load_task_names(args.matrix)
    run_root = args.jobs_dir / args.run_name
    run_root.mkdir(parents=True, exist_ok=True)
    lock_path = run_root / "orchestrator.lock"
    with lock_path.open("w", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError(f"another orchestrator is active for {run_root}") from exc
        return _run_tasks(args, run_root, task_names)


def _run_tasks(args: argparse.Namespace, run_root: Path, task_names: list[str]) -> int:
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
            "task_started",
            index=index,
            total=len(task_names),
            task=task_name,
            job=job_name,
        )
        completed_process = subprocess.run(command, cwd=PROJECT_ROOT, check=False)
        if completed_process.returncode:
            _append_event(
                progress_path,
                "run_stopped",
                index=index,
                total=len(task_names),
                task=task_name,
                job=job_name,
                exit_code=completed_process.returncode,
            )
            return completed_process.returncode

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


def _append_event(path: Path, event: str, **fields: object) -> None:
    record = {
        "ts": datetime.now(UTC).isoformat(),
        "event": event,
        **fields,
    }
    with path.open("a", encoding="utf-8") as stream:
        _write_json_line(stream, record)


def _write_json_line(stream: TextIO, record: dict[str, object]) -> None:
    stream.write(json.dumps(record, sort_keys=True) + "\n")
    stream.flush()


def main() -> int:
    args = parse_args()
    try:
        return run_full_evaluation(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"full evaluation preflight failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
