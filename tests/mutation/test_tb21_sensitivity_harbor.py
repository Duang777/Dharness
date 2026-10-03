from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

from evidence_harness_mutation import _tb21_harbor as harbor
from evidence_harness_mutation._tb21_manifest import (
    PlannedTask,
    RegistryMember,
    Tb21TaskKey,
)
from evidence_harness_mutation.prefixbench import PrefixBenchFileBinding
from evidence_harness_mutation.tb21_sensitivity_protocol import SENSITIVITY_RUN_ROOT
from scripts.run_full_evaluation import (
    DEBIAN_BOOKWORM_HTTPS_TASKS,
    DEBIAN_BULLSEYE_MAIN_TASKS,
    DEBIAN_TRIXIE_HTTPS_TASKS,
)


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _task() -> PlannedTask:
    return PlannedTask(
        ordinal=1,
        key=Tb21TaskKey(source_identity_sha256=_sha("source")),
        member=RegistryMember(
            registry_name="terminal-bench/example",
            package_digest="sha256:" + _sha("package"),
        ),
        task_tree=hashlib.sha1(b"tree").hexdigest(),
        invocation_sha256=_sha("invocation"),
    )


def _intent(task: PlannedTask, attempt: int = 1) -> harbor.IntentReceipt:
    return harbor.IntentReceipt(
        task=task.key,
        member=task.member,
        ordinal=task.ordinal,
        attempt=attempt,
        invocation_sha256=task.invocation_sha256,
        command_sha256=_sha("command"),
    )


def _terminal(task: PlannedTask, attempt: int = 1) -> harbor.TerminalReceipt:
    return harbor.TerminalReceipt(
        task=task.key,
        member=task.member,
        ordinal=task.ordinal,
        attempt=attempt,
        command_sha256=_sha("command"),
        status="failed",
        reward=0.0,
        exception_type=None,
        result=PrefixBenchFileBinding(path="result.json", bytes=1, sha256=_sha("result")),
        config=PrefixBenchFileBinding(path="config.json", bytes=1, sha256=_sha("config")),
        journal=None,
    )


def _attempt(root: Path, task: PlannedTask, attempt: int) -> Path:
    return (
        root
        / "tasks"
        / f"{task.ordinal:03d}-{task.key.source_identity_sha256[:12]}"
        / f"attempt-{attempt:03d}"
    )


def _write_launch(
    attempt_path: Path,
    task: PlannedTask,
    *,
    attempt: int = 1,
) -> None:
    harbor.write_receipt(
        attempt_path / "launch.json",
        harbor.LaunchReceipt(
            task=task.key,
            attempt=attempt,
            command_sha256=_sha("command"),
            pid=999_999_999,
            process_group_id=999_999_999,
            started_at="2026-10-03T00:00:00+00:00",
        ),
    )
    (attempt_path / "process.lease").write_text("", encoding="ascii")


def test_harbor_023_registry_path_blocks_without_provider(
    monkeypatch,
) -> None:
    runtime = harbor.HarborRuntimeIdentity(
        version="0.23.0",
        executable=PrefixBenchFileBinding(path="/bin/harbor", bytes=1, sha256=_sha("binary")),
        distribution_files=1,
        distribution_sha256=_sha("distribution"),
    )
    executable = cast(
        Any,
        SimpleNamespace(
            plan=SimpleNamespace(tasks=()),
            executable_commit="a" * 40,
        ),
    )
    monkeypatch.setattr(harbor, "_verify_checkout", lambda *_args: None)
    monkeypatch.setattr(harbor, "attest_harbor", lambda: runtime)
    monkeypatch.setattr(
        harbor,
        "_observed_registry_path",
        lambda: "tasks/dataset.toml/registry.json",
    )

    result = harbor.preflight_harbor(executable, tb21_checkout=Path("/unused"))

    assert result.ready_for_provider_execution is False
    assert result.blockers[0].code == "repo_dataset_toml_not_resolved"
    assert result.blockers[0].observed_registry_path == ("tasks/dataset.toml/registry.json")


def test_installed_harbor_023_does_not_resolve_the_toml_manifest() -> None:
    assert harbor._observed_registry_path() == "tasks/dataset.toml/registry.json"


def test_debian_mount_task_map_matches_the_frozen_tb20_runner() -> None:
    assert harbor._DEBIAN_BOOKWORM_HTTPS_TASKS == DEBIAN_BOOKWORM_HTTPS_TASKS
    assert harbor._DEBIAN_BULLSEYE_MAIN_TASKS == DEBIAN_BULLSEYE_MAIN_TASKS
    assert harbor._DEBIAN_TRIXIE_HTTPS_TASKS == DEBIAN_TRIXIE_HTTPS_TASKS


def test_reward_zero_terminal_receipt_is_absorbing(tmp_path: Path) -> None:
    run_root = tmp_path / SENSITIVITY_RUN_ROOT
    task = _task()
    attempt = _attempt(run_root, task, 1)
    attempt.mkdir(parents=True)
    harbor.write_receipt(attempt / "intent.json", _intent(task))
    _write_launch(attempt, task)
    harbor.write_receipt(attempt / "terminal.json", _terminal(task))

    state = harbor.fold_task_receipts(run_root, task)

    assert state.status == "terminal"
    assert state.terminal is not None
    assert state.terminal.reward == 0.0
    assert state.next_attempt == 2


def test_only_interrupted_attempt_can_advance_to_next_attempt(tmp_path: Path) -> None:
    task = _task()
    run_root = tmp_path / SENSITIVITY_RUN_ROOT
    attempt = _attempt(run_root, task, 1)
    attempt.mkdir(parents=True)
    intent = _intent(task)
    harbor.write_receipt(attempt / "intent.json", intent)
    _write_launch(attempt, task)
    harbor.write_receipt(
        attempt / "interrupted.json",
        harbor.InterruptedReceipt(
            task=task.key,
            attempt=1,
            command_sha256=intent.command_sha256,
            reason="signal",
        ),
    )

    state = harbor.fold_task_receipts(run_root, task)

    assert state.status == "interrupted"
    assert state.next_attempt == 2


def test_dead_launch_without_result_is_faulted(tmp_path: Path) -> None:
    task = _task()
    run_root = tmp_path / SENSITIVITY_RUN_ROOT
    attempt = _attempt(run_root, task, 1)
    attempt.mkdir(parents=True)
    intent = _intent(task)
    harbor.write_receipt(attempt / "intent.json", intent)
    harbor.write_receipt(
        attempt / "launch.json",
        harbor.LaunchReceipt(
            task=task.key,
            attempt=1,
            command_sha256=intent.command_sha256,
            pid=999_999_999,
            process_group_id=999_999_999,
            started_at="2026-10-03T00:00:00+00:00",
        ),
    )
    (attempt / "process.lease").write_text("", encoding="ascii")

    state = harbor.fold_task_receipts(run_root, task)

    assert state.status == "faulted"
    assert state.reason == "dead launch has no terminal or interruption receipt"


def test_result_written_before_terminal_receipt_is_recovered(tmp_path: Path) -> None:
    task = _task()
    run_root = tmp_path / SENSITIVITY_RUN_ROOT
    attempt = _attempt(run_root, task, 1)
    trial = attempt / "harbor" / "run" / "trial"
    trial.mkdir(parents=True)
    intent = _intent(task)
    harbor.write_receipt(attempt / "intent.json", intent)
    harbor.write_receipt(
        attempt / "launch.json",
        harbor.LaunchReceipt(
            task=task.key,
            attempt=1,
            command_sha256=intent.command_sha256,
            pid=999_999_999,
            process_group_id=999_999_999,
            started_at="2026-10-03T00:00:00+00:00",
        ),
    )
    (attempt / "process.lease").write_text("", encoding="ascii")
    (trial / "config.json").write_text("{}\n", encoding="utf-8")
    (trial / "result.json").write_text(
        json.dumps(
            {
                "task_name": task.member.registry_name,
                "task_id": {
                    "org": "terminal-bench",
                    "name": "example",
                    "ref": task.member.package_digest,
                },
                "verifier_result": {"rewards": {"reward": 0}},
            }
        ),
        encoding="utf-8",
    )

    state = harbor.fold_task_receipts(run_root, task)

    assert state.status == "terminal"
    assert state.terminal is not None
    assert state.terminal.status == "failed"


def test_attempt_after_terminal_is_faulted(tmp_path: Path) -> None:
    task = _task()
    run_root = tmp_path / SENSITIVITY_RUN_ROOT
    first = _attempt(run_root, task, 1)
    first.mkdir(parents=True)
    harbor.write_receipt(first / "intent.json", _intent(task))
    _write_launch(first, task)
    harbor.write_receipt(first / "terminal.json", _terminal(task))
    second = _attempt(run_root, task, 2)
    second.mkdir(parents=True)
    harbor.write_receipt(second / "intent.json", _intent(task, attempt=2))
    _write_launch(second, task, attempt=2)
    harbor.write_receipt(
        second / "interrupted.json",
        harbor.InterruptedReceipt(
            task=task.key,
            attempt=2,
            command_sha256=_sha("command"),
            reason="signal",
        ),
    )

    state = harbor.fold_task_receipts(run_root, task)

    assert state.status == "faulted"
    assert state.reason == "an attempt exists after an absorbing task state"
