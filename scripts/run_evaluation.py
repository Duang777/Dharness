from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MATRIX = PROJECT_ROOT / "evaluation" / "matrix.json"
AGENT_IMPORT = "evidence_harness.harbor_agent:EvidenceHarnessAgent"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the fixed Terminal-Bench 2.0 evaluation matrix."
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("EVIDENCE_HARNESS_MODEL"),
        help="LiteLLM model name. Defaults to EVIDENCE_HARNESS_MODEL.",
    )
    parser.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    parser.add_argument(
        "--jobs-dir",
        type=Path,
        default=PROJECT_ROOT / "runs" / "terminal-bench-2",
    )
    parser.add_argument(
        "--job-name",
        default=datetime.now(UTC).strftime("%Y-%m-%d__%H-%M-%S"),
    )
    parser.add_argument("--n-concurrent", type=int, default=2)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--agent-kwarg",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Additional Harbor --agent-kwarg value; repeat as needed.",
    )
    return parser.parse_args()


def load_matrix(path: Path) -> tuple[str, list[str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError(f"unsupported matrix schema in {path}")

    dataset = data.get("dataset")
    tasks = data.get("tasks")
    if not isinstance(dataset, str) or not dataset:
        raise ValueError(f"matrix dataset must be a non-empty string: {path}")
    if not isinstance(tasks, list) or len(tasks) != 10:
        raise ValueError(f"matrix must contain exactly 10 tasks: {path}")

    names: list[str] = []
    for item in tasks:
        if not isinstance(item, dict):
            raise ValueError(f"every matrix task must be an object: {path}")
        name = item.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"every matrix task must have a non-empty name: {path}")
        names.append(name)
    if len(names) != len(set(names)):
        raise ValueError(f"matrix task names must be unique: {path}")
    return dataset, names


def build_command(args: argparse.Namespace) -> list[str]:
    if not args.model:
        raise ValueError(
            "no model configured; pass --model or set EVIDENCE_HARNESS_MODEL"
        )
    if args.n_concurrent < 1:
        raise ValueError("--n-concurrent must be at least 1")
    if args.env_file is not None and not args.env_file.is_file():
        raise ValueError(f"env file does not exist: {args.env_file}")

    dataset, task_names = load_matrix(args.matrix)
    harbor = shutil.which("harbor")
    if harbor is None:
        raise ValueError(
            "harbor is not on PATH; run this script with "
            "`uv run python scripts/run_evaluation.py ...`"
        )

    command = [
        harbor,
        "run",
        "--dataset",
        dataset,
        "--agent",
        AGENT_IMPORT,
        "--model",
        args.model,
        "--jobs-dir",
        str(args.jobs_dir),
        "--job-name",
        args.job_name,
        "--n-concurrent",
        str(args.n_concurrent),
        "--agent-timeout-multiplier",
        "2.0",
    ]
    for task_name in task_names:
        command.extend(("--include-task-name", task_name))

    default_agent_kwargs = (
        "max_turns=40",
        "max_environment_calls=80",
        "max_wall_time_sec=1800",
        "verification_environment_reserve=3",
    )
    for item in (*default_agent_kwargs, *args.agent_kwarg):
        command.extend(("--agent-kwarg", item))

    if args.env_file is not None:
        command.extend(("--env-file", str(args.env_file)))
    if args.dry_run:
        command.append("--dry-run")
    return command


def main() -> int:
    args = parse_args()
    try:
        command = build_command(args)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"evaluation preflight failed: {exc}", file=sys.stderr)
        return 2

    print(f"$ {shlex.join(command)}", flush=True)
    return subprocess.run(command, cwd=PROJECT_ROOT, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
