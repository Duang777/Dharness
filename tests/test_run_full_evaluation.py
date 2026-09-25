from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

import pytest

from scripts import run_full_evaluation


def _write_matrix(path: Path, names: list[str]) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "dataset": "terminal-bench@2.0",
                "selection_method": "fixture",
                "tasks": [
                    {
                        "name": name,
                        "difficulty": "medium",
                        "category": "software-engineering",
                        "tags": [],
                    }
                    for name in names
                ],
            }
        ),
        encoding="utf-8",
    )


def _write_trial_result(
    job_dir: Path,
    task_name: str,
    reward: float | None = 1.0,
    exception_type: str | None = None,
) -> Path:
    result_path = job_dir / f"{task_name}__trial" / "result.json"
    result_path.parent.mkdir(parents=True)
    result_path.write_text(
        json.dumps(
            {
                "trial_name": f"{task_name}__trial",
                "task_name": task_name,
                "verifier_result": (
                    {"rewards": {"reward": reward}} if reward is not None else None
                ),
                "exception_info": ({"exception_type": exception_type} if exception_type else None),
            }
        ),
        encoding="utf-8",
    )
    return result_path


def test_build_task_command_is_serial_and_scopes_https_mount() -> None:
    command = run_full_evaluation.build_task_command(
        model="openai/test-model",
        env_file=Path("/tmp/test.env"),
        matrix=Path("/tmp/matrix.json"),
        run_root=Path("/tmp/jobs/full"),
        job_name="001-fix-git",
        task_name="fix-git",
        agent_kwargs=["api_base=https://example.test/v1"],
    )

    concurrency_index = command.index("--n-concurrent")
    assert command[concurrency_index + 1] == "1"
    assert "--debian-https-sources" in command
    assert command[-2:] == ["--agent-kwarg", "api_base=https://example.test/v1"]

    other_command = run_full_evaluation.build_task_command(
        model="openai/test-model",
        env_file=Path("/tmp/test.env"),
        matrix=Path("/tmp/matrix.json"),
        run_root=Path("/tmp/jobs/full"),
        job_name="002-other",
        task_name="other",
        agent_kwargs=[],
    )
    assert "--debian-https-sources" not in other_command


def test_completed_task_results_reads_trials_and_ignores_job_summary(tmp_path: Path) -> None:
    (tmp_path / "result.json").write_text('{"n_total_trials": 1}', encoding="utf-8")
    result_path = _write_trial_result(tmp_path / "001-task-a", "task-a")

    assert run_full_evaluation.completed_task_results(tmp_path) == {"task-a": result_path}


def test_completed_task_results_ignores_interrupted_trials(tmp_path: Path) -> None:
    _write_trial_result(
        tmp_path / "001-task-a",
        "task-a",
        reward=None,
        exception_type="CancelledError",
    )

    assert run_full_evaluation.completed_task_results(tmp_path) == {}


def test_full_run_resumes_from_real_result_and_runs_remaining_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matrix = tmp_path / "matrix.json"
    env_file = tmp_path / "provider.env"
    jobs_dir = tmp_path / "jobs"
    run_root = jobs_dir / "full-run"
    _write_matrix(matrix, ["task-a", "task-b"])
    env_file.write_text("OPENAI_API_KEY=secret\n", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(
        command: list[str],
        child_run_root: Path,
        task_name: str,
        job_name: str,
    ) -> int:
        calls.append(command)
        assert child_run_root == run_root
        _write_trial_result(child_run_root / job_name, task_name, reward=0.0)
        return 0

    monkeypatch.setattr(run_full_evaluation, "_run_child", fake_run)
    args = argparse.Namespace(
        model="openai/test-model",
        env_file=env_file,
        matrix=matrix,
        jobs_dir=jobs_dir,
        run_name="full-run",
        agent_kwarg=[],
        dry_run=False,
    )

    assert run_full_evaluation.run_full_evaluation(args) == 0
    assert [command[command.index("--include-task-name") + 1] for command in calls] == [
        "task-a",
        "task-b",
    ]

    calls.clear()
    assert run_full_evaluation.run_full_evaluation(args) == 0
    assert calls == []

    events = [
        json.loads(line)
        for line in (run_root / "progress.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [event["event"] for event in events] == [
        "run_started",
        "task_launching",
        "task_completed",
        "task_launching",
        "task_completed",
        "run_completed",
        "run_started",
        "run_completed",
    ]
    assert events[-4]["reward"] == 0.0


def test_full_run_rejects_configuration_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matrix = tmp_path / "matrix.json"
    env_file = tmp_path / "provider.env"
    jobs_dir = tmp_path / "jobs"
    _write_matrix(matrix, ["task-a"])
    env_file.write_text("OPENAI_API_KEY=secret\n", encoding="utf-8")

    def fake_run(
        _command: list[str],
        run_root: Path,
        task_name: str,
        job_name: str,
    ) -> int:
        _write_trial_result(run_root / job_name, task_name)
        return 0

    monkeypatch.setattr(run_full_evaluation, "_run_child", fake_run)
    args = argparse.Namespace(
        model="openai/test-model",
        env_file=env_file,
        matrix=matrix,
        jobs_dir=jobs_dir,
        run_name="full-run",
        agent_kwarg=[],
        dry_run=False,
    )
    assert run_full_evaluation.run_full_evaluation(args) == 0

    args.model = "openai/different-model"
    with pytest.raises(ValueError, match="run configuration changed: model"):
        run_full_evaluation.run_full_evaluation(args)

    args.model = "openai/test-model"
    env_file.write_text("OPENAI_API_KEY=rotated-secret\n", encoding="utf-8")
    with pytest.raises(ValueError, match="run configuration changed: env_file_sha256"):
        run_full_evaluation.run_full_evaluation(args)


def test_full_run_rechecks_configuration_before_each_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matrix = tmp_path / "matrix.json"
    env_file = tmp_path / "provider.env"
    jobs_dir = tmp_path / "jobs"
    _write_matrix(matrix, ["task-a", "task-b"])
    env_file.write_text("OPENAI_API_KEY=secret\n", encoding="utf-8")
    args = argparse.Namespace(
        model="openai/test-model",
        env_file=env_file,
        matrix=matrix,
        jobs_dir=jobs_dir,
        run_name="full-run",
        agent_kwarg=[],
        dry_run=False,
    )
    initial_config = run_full_evaluation.run_configuration(args)
    configuration_checks = 0

    def changing_configuration(_args: argparse.Namespace) -> dict[str, object]:
        nonlocal configuration_checks
        configuration_checks += 1
        if configuration_checks < 3:
            return initial_config
        return {**initial_config, "source_sha256": "changed"}

    def fake_run(
        _command: list[str],
        run_root: Path,
        task_name: str,
        job_name: str,
    ) -> int:
        _write_trial_result(run_root / job_name, task_name)
        return 0

    monkeypatch.setattr(run_full_evaluation, "run_configuration", changing_configuration)
    monkeypatch.setattr(run_full_evaluation, "_run_child", fake_run)

    with pytest.raises(ValueError, match="configuration changed after"):
        run_full_evaluation.run_full_evaluation(args)
    assert set(run_full_evaluation.completed_task_results(jobs_dir / "full-run")) == {"task-a"}


def test_full_run_records_child_launch_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    matrix = tmp_path / "matrix.json"
    env_file = tmp_path / "provider.env"
    jobs_dir = tmp_path / "jobs"
    run_root = jobs_dir / "full-run"
    _write_matrix(matrix, ["task-a"])
    env_file.write_text("OPENAI_API_KEY=secret\n", encoding="utf-8")

    def fail_to_launch(*_args: object, **_kwargs: object) -> int:
        raise OSError("spawn failed")

    monkeypatch.setattr(run_full_evaluation, "_run_child", fail_to_launch)
    args = argparse.Namespace(
        model="openai/test-model",
        env_file=env_file,
        matrix=matrix,
        jobs_dir=jobs_dir,
        run_name="full-run",
        agent_kwarg=[],
        dry_run=False,
    )

    with pytest.raises(OSError, match="spawn failed"):
        run_full_evaluation.run_full_evaluation(args)
    events = [
        json.loads(line)
        for line in (run_root / "progress.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [event["event"] for event in events] == [
        "run_started",
        "task_launching",
        "run_stopped",
    ]
    assert events[-1]["reason"] == "child launch failed: OSError"


def test_active_child_marker_blocks_a_second_orchestrator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active_path = tmp_path / run_full_evaluation.ACTIVE_CHILD_NAME
    active_path.write_text(
        json.dumps({"pid": 12345, "process_group_id": 12345}),
        encoding="utf-8",
    )
    monkeypatch.setattr(run_full_evaluation, "_process_group_is_alive", lambda _: True)

    with pytest.raises(ValueError, match="process group 12345 is still active"):
        run_full_evaluation.assert_no_active_child(tmp_path)


def test_active_child_marker_checks_the_process_group(tmp_path: Path) -> None:
    grandchild_pid_path = tmp_path / "detached-grandchild.pid"
    grandchild_code = "import time; time.sleep(30)"
    leader_code = "\n".join(
        (
            "import subprocess, sys",
            "from pathlib import Path",
            f"child = subprocess.Popen([{sys.executable!r}, '-c', {grandchild_code!r}])",
            f"Path({str(grandchild_pid_path)!r}).write_text(str(child.pid))",
        )
    )
    leader = subprocess.Popen(
        [sys.executable, "-c", leader_code],
        start_new_session=True,
    )
    try:
        assert leader.wait(timeout=5) == 0
        deadline = time.monotonic() + 5
        while not grandchild_pid_path.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        grandchild_pid = int(grandchild_pid_path.read_text(encoding="utf-8"))
        active_path = tmp_path / run_full_evaluation.ACTIVE_CHILD_NAME
        active_path.write_text(
            json.dumps(
                {
                    "pid": leader.pid,
                    "process_group_id": leader.pid,
                }
            ),
            encoding="utf-8",
        )

        assert not run_full_evaluation._pid_is_alive(leader.pid)
        assert run_full_evaluation._pid_is_alive(grandchild_pid)
        with pytest.raises(ValueError, match=f"process group {leader.pid} is still active"):
            run_full_evaluation.assert_no_active_child(tmp_path)
    finally:
        with suppress(ProcessLookupError):
            os.killpg(leader.pid, signal.SIGKILL)


def test_run_child_installs_signal_handlers_before_spawning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class FakeProcess:
        pid = 12345

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            return 0

    def fake_signal(signum: signal.Signals, _handler: object) -> signal.Handlers:
        events.append(f"signal-{signum}")
        return signal.Handlers.SIG_DFL

    def fake_popen(*_args: object, **_kwargs: object) -> FakeProcess:
        events.append("popen")
        return FakeProcess()

    monkeypatch.setattr(run_full_evaluation.signal, "signal", fake_signal)
    monkeypatch.setattr(run_full_evaluation.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(run_full_evaluation, "_ensure_process_group_stopped", lambda _: None)
    monkeypatch.setattr(run_full_evaluation, "_process_group_is_alive", lambda _: False)

    assert run_full_evaluation._run_child(["command"], tmp_path, "task", "job") == 0
    assert events[:3] == [
        f"signal-{signal.SIGINT}",
        f"signal-{signal.SIGTERM}",
        "popen",
    ]
    assert not (tmp_path / run_full_evaluation.ACTIVE_CHILD_NAME).exists()


def test_run_child_cleans_process_group_when_marker_write_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleaned_groups: list[int] = []

    class FakeProcess:
        pid = 54321

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            return 0

    monkeypatch.setattr(
        run_full_evaluation.subprocess,
        "Popen",
        lambda *_args, **_kwargs: FakeProcess(),
    )
    monkeypatch.setattr(
        run_full_evaluation,
        "_write_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    monkeypatch.setattr(
        run_full_evaluation,
        "_ensure_process_group_stopped",
        cleaned_groups.append,
    )
    monkeypatch.setattr(run_full_evaluation, "_process_group_is_alive", lambda _: False)

    with pytest.raises(OSError, match="disk full"):
        run_full_evaluation._run_child(["command"], tmp_path, "task", "job")
    assert cleaned_groups == [54321]
    assert not (tmp_path / run_full_evaluation.ACTIVE_CHILD_NAME).exists()


def test_run_child_forwards_sigterm_and_clears_marker(tmp_path: Path) -> None:
    grandchild_pid_path = tmp_path / "grandchild.pid"
    grandchild_code = (
        "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)"
    )
    child_code = "\n".join(
        (
            "import signal, subprocess, sys, time",
            "from pathlib import Path",
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)",
            f"child = subprocess.Popen([{sys.executable!r}, '-c', {grandchild_code!r}])",
            f"Path({str(grandchild_pid_path)!r}).write_text(str(child.pid))",
            "time.sleep(30)",
        )
    )
    outer_code = "\n".join(
        (
            "from pathlib import Path",
            "import scripts.run_full_evaluation as runner",
            "runner.SIGNAL_GRACE_SEC = 0.2",
            "runner.PROCESS_GROUP_EXIT_GRACE_SEC = 0.2",
            "runner.PROCESS_GROUP_TERM_GRACE_SEC = 0.2",
            "runner.PROCESS_GROUP_KILL_GRACE_SEC = 0.2",
            (
                "raise SystemExit(runner._run_child("
                f"[{sys.executable!r}, '-c', {child_code!r}], "
                f"Path({str(tmp_path)!r}), 'task', 'job'))"
            ),
        )
    )
    outer = subprocess.Popen(
        [sys.executable, "-c", outer_code],
        cwd=run_full_evaluation.PROJECT_ROOT,
    )
    active_path = tmp_path / run_full_evaluation.ACTIVE_CHILD_NAME
    try:
        deadline = time.monotonic() + 5
        while not active_path.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        active = json.loads(active_path.read_text(encoding="utf-8"))
        child_pid = int(active["pid"])
        while not grandchild_pid_path.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        grandchild_pid = int(grandchild_pid_path.read_text(encoding="utf-8"))
        assert run_full_evaluation._pid_is_alive(grandchild_pid)

        outer.terminate()
        assert outer.wait(timeout=5) == 256 - signal.SIGKILL
        deadline = time.monotonic() + 5
        while (
            run_full_evaluation._process_group_is_alive(child_pid) and time.monotonic() < deadline
        ):
            time.sleep(0.02)
        assert not run_full_evaluation._process_group_is_alive(child_pid)
        assert not active_path.exists()
    finally:
        if outer.poll() is None:
            outer.kill()
            outer.wait(timeout=5)
        if "child_pid" in locals() and run_full_evaluation._process_group_is_alive(child_pid):
            os.killpg(child_pid, signal.SIGKILL)
