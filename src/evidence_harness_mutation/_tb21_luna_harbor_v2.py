from __future__ import annotations

import fcntl
import hashlib
import json
import os
import signal
import subprocess
from collections.abc import Mapping
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Self, cast

from pydantic import Field, ValidationError, model_validator

from evidence_harness.collection_profile import PREFIXBENCH_V1
from evidence_harness.protocol import ProducerAttestation
from evidence_harness.source_binding import attest_git_runtime_source
from evidence_harness_mutation._tb21_harbor import (
    CollectionSummary,
    ExecutablePreflight,
    FaultReceipt,
    FoldedTask,
    IntentReceipt,
    InterruptedReceipt,
    LaunchReceipt,
    SelectorProof,
    TerminalReceipt,
    _collection_summary,
    _debian_mount_arguments,
    _exception_type,
    _json_object,
    _lease_is_held,
    _mapping,
    _process_group_is_alive,
    _project_file_binding,
    _read_model,
    _require_same_runtime,
    _result_identity,
    _reward,
    _task_root,
    _validate_end_receipt,
    _validate_terminal,
    preflight_harbor,
    read_regular_file,
    write_bytes_once,
    write_receipt,
)
from evidence_harness_mutation._tb21_luna_manifest_v2 import (
    MODEL,
    BoundExecutable,
    PlannedTask,
    invocation_command,
)
from evidence_harness_mutation.model import FrozenModel, Sha256
from evidence_harness_mutation.prefixbench import PrefixBenchFileBinding
from evidence_harness_mutation.tb21_luna_rq2_protocol_v2 import (
    LUNA_RUN_ROOT,
    PROVIDER_ENDPOINT_SHA256,
    provider_bundle_sha256,
)

_INTERRUPTION_EXCEPTIONS = frozenset({"CancelledError", "KeyboardInterrupt"})
_PROVIDER_KEYS = ("OPENAI_API_KEY", "OPENAI_BASE_URL")
_RECEIPT_NAMES = ("intent.json", "launch.json", "interrupted.json", "terminal.json", "fault.json")
_PYTHON_ENV_KEYS = (
    "PYTHONHOME",
    "PYTHONPATH",
    "PYTHONSTARTUP",
    "PYTHONUSERBASE",
    "VIRTUAL_ENV",
    "UV_PROJECT_ENVIRONMENT",
)


class ProviderEnvironmentAttestation(FrozenModel):
    transport: Literal["inherited-environment-only"] = "inherited-environment-only"
    keys: tuple[str, str] = _PROVIDER_KEYS
    present: tuple[Literal[True], Literal[True]] = (True, True)
    endpoint_sha256: Literal["42c964b5536d7dd77320e537c8717a08c7397f93e3398db3e7dc1ef923400dac"] = (
        PROVIDER_ENDPOINT_SHA256
    )
    bundle_sha256: Sha256
    values: Literal["omitted"] = "omitted"

    @model_validator(mode="after")
    def validate_keys(self) -> Self:
        if self.keys != _PROVIDER_KEYS:
            raise ValueError("Luna Provider environment keys have changed")
        return self

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.model_dump(mode="json"))


class HarborModelProof(FrozenModel):
    requested_model: Literal["openai/modelhub/gpt-5.6-luna"] = MODEL
    saved_config_model: Literal["openai/modelhub/gpt-5.6-luna"]
    result_config_model: Literal["openai/modelhub/gpt-5.6-luna"]
    result_model_provider: Literal["openai"]
    result_model_name: Literal["modelhub/gpt-5.6-luna"]
    model_usage_keys: tuple[Literal["openai/modelhub/gpt-5.6-luna"], ...] = Field(
        min_length=1,
        max_length=1,
    )

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.model_dump(mode="json"))


class RunSeal(FrozenModel):
    schema_version: Literal[1] = 1
    executable: PrefixBenchFileBinding
    executable_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    protocol: PrefixBenchFileBinding
    cohort: PrefixBenchFileBinding
    run_plan_sha256: Sha256
    selector_proof: SelectorProof
    producer: ProducerAttestation
    model: Literal["openai/modelhub/gpt-5.6-luna"] = MODEL
    collection_profile: Literal["prefixbench-v1"] = "prefixbench-v1"
    provider_environment: ProviderEnvironmentAttestation

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.model_dump(mode="json"))


def preflight_luna_harbor(
    executable: BoundExecutable,
    *,
    tb21_checkout: Path,
) -> ExecutablePreflight:
    return preflight_harbor(cast(Any, executable), tb21_checkout=tb21_checkout)


def collect_luna_harbor(
    project_root: Path,
    executable: BoundExecutable,
    preflight: ExecutablePreflight,
    *,
    environment: Mapping[str, str],
) -> CollectionSummary:
    if not preflight.ready_for_provider_execution or preflight.proof is None:
        raise ValueError("Luna collection requires a passing executable preflight")
    child_env, provider = _harbor_child_environment(project_root, environment)
    root = project_root / LUNA_RUN_ROOT
    if os.path.lexists(root):
        if root.is_symlink() or not root.is_dir():
            raise ValueError("Luna run root must be a directory")
    else:
        root.mkdir(parents=True, mode=0o700)
    lock_path = root / "collector.lock"
    with lock_path.open("w", encoding="ascii") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("another Luna collector is active") from exc
        seal = _bind_run_seal(project_root, root, executable, preflight.proof, provider)
        terminal = 0
        for task in executable.plan.tasks:
            state = fold_luna_task_receipts(project_root, root, task)
            if state.status == "terminal":
                if state.terminal is None:
                    raise AssertionError("terminal task state has no receipt")
                attempt_dir = _task_root(root, task) / f"attempt-{state.terminal.attempt:03d}"
                _reject_persisted_provider_values(
                    attempt_dir,
                    (
                        environment["OPENAI_API_KEY"],
                        environment["OPENAI_BASE_URL"],
                    ),
                )
                recovered = attempt_dir / "terminal.json"
                if not recovered.exists():
                    write_receipt(recovered, state.terminal)
                terminal += 1
                continue
            if state.status == "active":
                raise ValueError(f"Luna Harbor task is still active: {task.member.registry_name}")
            if state.status == "faulted":
                raise ValueError(
                    f"Luna task is faulted and cannot be retried: "
                    f"{task.member.registry_name}: {state.reason}"
                )
            _require_same_runtime(seal.selector_proof.runtime)
            result = _launch_task(
                project_root,
                root,
                task,
                attempt=state.next_attempt,
                environment=child_env,
                provider_values=(
                    environment["OPENAI_API_KEY"],
                    environment["OPENAI_BASE_URL"],
                ),
                producer=seal.producer,
            )
            if result == "interrupted":
                return _collection_summary(root, executable.plan)
            terminal += 1
        return CollectionSummary(
            terminal=terminal,
            pending=0,
            interrupted=0,
            status="complete",
        )


def provider_environment(
    environment: Mapping[str, str],
) -> tuple[dict[str, str], ProviderEnvironmentAttestation]:
    values = tuple(environment.get(key, "") for key in _PROVIDER_KEYS)
    if any(not value for value in values):
        missing = tuple(key for key, value in zip(_PROVIDER_KEYS, values, strict=True) if not value)
        raise ValueError("missing inherited Provider environment: " + ", ".join(missing))
    api_key, base_url = values
    if hashlib.sha256(base_url.encode()).hexdigest() != PROVIDER_ENDPOINT_SHA256:
        raise ValueError("OPENAI_BASE_URL does not match the frozen endpoint")

    child = dict(environment)
    for key in tuple(child):
        upper = key.upper()
        if key not in _PROVIDER_KEYS and (
            upper.startswith(("OPENAI_", "AZURE_OPENAI_", "ANTHROPIC_"))
            or upper.endswith(("_API_KEY", "_ACCESS_TOKEN", "_SECRET"))
        ):
            child.pop(key)
    child["PYTHONNOUSERSITE"] = "1"
    return child, ProviderEnvironmentAttestation(
        bundle_sha256=provider_bundle_sha256(api_key, base_url)
    )


def _harbor_child_environment(
    project_root: Path,
    environment: Mapping[str, str],
) -> tuple[dict[str, str], ProviderEnvironmentAttestation]:
    child, attestation = provider_environment(environment)
    source_root = project_root / "src"
    if source_root.is_symlink() or not source_root.is_dir():
        raise ValueError("Luna Harbor agent source root must be a directory")
    for key in _PYTHON_ENV_KEYS:
        child.pop(key, None)
    child["PYTHONPATH"] = source_root.resolve().as_posix()
    child["PYTHONNOUSERSITE"] = "1"
    return child, attestation


def fold_luna_task_receipts(
    project_root: Path,
    run_root: Path,
    task: PlannedTask,
) -> FoldedTask:
    state = _fold_luna_receipts(run_root, task)
    if state.status != "terminal" or state.terminal is None:
        return state
    attempt_dir = _task_root(run_root, task) / f"attempt-{state.terminal.attempt:03d}"
    proof = _model_proof(project_root, state.terminal)
    proof_path = attempt_dir / "model-proof.json"
    if os.path.lexists(proof_path):
        data = read_regular_file(proof_path, "Luna model proof")
        try:
            existing = HarborModelProof.model_validate_json(data)
        except ValidationError as exc:
            raise ValueError("invalid Luna model proof") from exc
        if data != existing.canonical_bytes() or existing != proof:
            raise ValueError("Luna model proof changed")
    else:
        write_bytes_once(proof_path, proof.canonical_bytes())
    return state


def _fold_luna_receipts(run_root: Path, task: PlannedTask) -> FoldedTask:
    task_root = _task_root(run_root, task)
    if not task_root.exists():
        return FoldedTask(status="pending", next_attempt=1)
    if task_root.is_symlink() or not task_root.is_dir():
        return FoldedTask(
            status="faulted",
            next_attempt=1,
            reason="task receipt path is not a directory",
        )
    attempts = tuple(sorted(task_root.glob("attempt-*")))
    if not attempts:
        return FoldedTask(status="pending", next_attempt=1)

    terminals: list[TerminalReceipt] = []
    last_status: Literal["pending", "active", "interrupted", "terminal", "faulted"] = "pending"
    last_reason: str | None = None
    first_command_sha256: str | None = None
    next_attempt = 1
    for expected_attempt, attempt_dir in enumerate(attempts, start=1):
        if attempt_dir.name != f"attempt-{expected_attempt:03d}":
            return FoldedTask(
                status="faulted",
                next_attempt=expected_attempt,
                reason="attempt directories are not contiguous",
            )
        next_attempt = expected_attempt + 1
        try:
            status, terminal, reason = _fold_luna_attempt(
                attempt_dir,
                task,
                expected_attempt,
                project_root=run_root.parents[2],
            )
        except (OSError, ValueError) as exc:
            return FoldedTask(
                status="faulted",
                next_attempt=next_attempt,
                reason=str(exc),
            )
        intent = _read_model(attempt_dir / "intent.json", IntentReceipt, "intent receipt")
        if first_command_sha256 is None:
            first_command_sha256 = intent.command_sha256
        elif intent.command_sha256 != first_command_sha256:
            return FoldedTask(
                status="faulted",
                next_attempt=next_attempt,
                reason="resumed attempt changed the command digest",
            )
        if terminals or last_status in {"active", "faulted"}:
            return FoldedTask(
                status="faulted",
                next_attempt=next_attempt,
                reason="an attempt exists after an absorbing task state",
            )
        if terminal is not None:
            terminals.append(terminal)
        last_status = status
        last_reason = reason
    if len(terminals) > 1:
        return FoldedTask(
            status="faulted",
            next_attempt=next_attempt,
            reason="multiple terminal results exist for one task",
        )
    if terminals:
        return FoldedTask(status="terminal", next_attempt=next_attempt, terminal=terminals[0])
    if last_status == "faulted":
        return FoldedTask(
            status="faulted",
            next_attempt=next_attempt,
            reason=last_reason or "unknown receipt fault",
        )
    return FoldedTask(status=last_status, next_attempt=next_attempt)


def _fold_luna_attempt(
    attempt_dir: Path,
    task: PlannedTask,
    attempt: int,
    *,
    project_root: Path,
) -> tuple[
    Literal["active", "interrupted", "terminal", "faulted"],
    TerminalReceipt | None,
    str | None,
]:
    if attempt_dir.is_symlink() or not attempt_dir.is_dir():
        raise ValueError(f"attempt path is not a directory: {attempt_dir}")
    present = tuple(name for name in _RECEIPT_NAMES if os.path.lexists(attempt_dir / name))
    for name in present:
        path = attempt_dir / name
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"receipt must be a regular file: {path}")
    intent = _read_model(attempt_dir / "intent.json", IntentReceipt, "intent receipt")
    if (
        intent.task != task.key
        or intent.member != task.member
        or intent.ordinal != task.ordinal
        or intent.attempt != attempt
        or intent.invocation_sha256 != task.invocation_sha256
    ):
        raise ValueError(f"intent receipt does not match the run plan: {attempt_dir}")

    end_receipts = tuple(
        name for name in ("interrupted.json", "terminal.json", "fault.json") if name in present
    )
    if len(end_receipts) > 1:
        raise ValueError(f"attempt contains conflicting end receipts: {attempt_dir}")
    launch: LaunchReceipt | None = None
    launch_path = attempt_dir / "launch.json"
    if launch_path.exists():
        launch = _read_model(launch_path, LaunchReceipt, "launch receipt")
        _validate_end_receipt(
            launch.task,
            launch.attempt,
            launch.command_sha256,
            task,
            attempt,
            intent,
        )
        lease_path = attempt_dir / "process.lease"
        if not os.path.lexists(lease_path):
            raise ValueError(f"launch receipt exists without a process lease: {attempt_dir}")
        if lease_path.is_symlink() or not lease_path.is_file():
            raise ValueError(f"process lease is not a regular file: {attempt_dir}")

    terminal_path = attempt_dir / "terminal.json"
    if terminal_path.exists():
        if launch is None:
            raise ValueError(f"terminal receipt exists without a launch receipt: {attempt_dir}")
        terminal = _read_model(terminal_path, TerminalReceipt, "terminal receipt")
        _validate_terminal(terminal, task, attempt, intent.command_sha256)
        return "terminal", terminal, None
    fault_path = attempt_dir / "fault.json"
    if fault_path.exists():
        fault = _read_model(fault_path, FaultReceipt, "fault receipt")
        _validate_end_receipt(
            fault.task,
            fault.attempt,
            fault.command_sha256,
            task,
            attempt,
            intent,
        )
        return "faulted", None, fault.reason
    interrupted_path = attempt_dir / "interrupted.json"
    if interrupted_path.exists():
        if launch is None:
            raise ValueError(f"interrupted receipt exists without a launch receipt: {attempt_dir}")
        interrupted = _read_model(
            interrupted_path,
            InterruptedReceipt,
            "interrupted receipt",
        )
        _validate_end_receipt(
            interrupted.task,
            interrupted.attempt,
            interrupted.command_sha256,
            task,
            attempt,
            intent,
        )
        return "interrupted", None, None
    if launch is None:
        return "faulted", None, "intent exists without a launch receipt"
    lease_path = attempt_dir / "process.lease"
    if _lease_is_held(lease_path):
        return "active", None, None
    if _process_group_is_alive(launch.process_group_id):
        return "faulted", None, "process group is alive without a held lease"
    discovered = _terminal_from_luna_harbor(
        attempt_dir,
        task,
        attempt,
        intent.command_sha256,
        project_root=project_root,
    )
    if discovered is not None:
        return "terminal", discovered, None
    return "faulted", None, "dead launch has no terminal or interruption receipt"


def _launch_task(
    project_root: Path,
    run_root: Path,
    task: PlannedTask,
    *,
    attempt: int,
    environment: Mapping[str, str],
    provider_values: tuple[str, str],
    producer: ProducerAttestation,
) -> Literal["terminal", "interrupted"]:
    attempt_dir = _task_root(run_root, task) / f"attempt-{attempt:03d}"
    attempt_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
    harbor_root = attempt_dir / "harbor"
    command = [*invocation_command(task), "--jobs-dir", str(harbor_root), "--job-name", "run"]
    for value in PREFIXBENCH_V1.harbor_agent_kwargs(producer):
        command.extend(("--agent-kwarg", value))
    command.extend(_debian_mount_arguments(project_root, task))
    if any(value in argument for value in provider_values for argument in command):
        raise ValueError("Provider value entered the Harbor command")
    command_sha256 = hashlib.sha256(_nul_frame(tuple(command))).hexdigest()
    write_receipt(
        attempt_dir / "intent.json",
        IntentReceipt(
            task=task.key,
            member=task.member,
            ordinal=task.ordinal,
            attempt=attempt,
            invocation_sha256=task.invocation_sha256,
            command_sha256=command_sha256,
        ),
    )
    child: subprocess.Popen[bytes] | None = None
    forwarded: list[int] = []
    previous: dict[signal.Signals, Any] = {}

    def forward(signum: int, _frame: object) -> None:
        forwarded.append(signum)
        if child is not None:
            with suppress(ProcessLookupError):
                os.killpg(child.pid, signum)

    for signum in (signal.SIGINT, signal.SIGTERM):
        previous[signum] = signal.signal(signum, forward)
    lease_descriptor = os.open(
        attempt_dir / "process.lease",
        os.O_RDWR | os.O_CREAT | os.O_EXCL,
        0o600,
    )
    try:
        fcntl.flock(lease_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        child = subprocess.Popen(
            command,
            cwd=project_root,
            env=dict(environment),
            start_new_session=True,
            pass_fds=(lease_descriptor,),
        )
        write_receipt(
            attempt_dir / "launch.json",
            LaunchReceipt(
                task=task.key,
                attempt=attempt,
                command_sha256=command_sha256,
                pid=child.pid,
                process_group_id=child.pid,
                started_at=datetime.now(UTC).isoformat(),
            ),
        )
        returncode = child.wait()
    except KeyboardInterrupt:
        forwarded.append(signal.SIGINT)
        if child is not None:
            with suppress(ProcessLookupError):
                os.killpg(child.pid, signal.SIGINT)
            child.wait()
        returncode = -signal.SIGINT
    except OSError as exc:
        write_receipt(
            attempt_dir / "fault.json",
            FaultReceipt(
                task=task.key,
                attempt=attempt,
                command_sha256=command_sha256,
                reason=f"Harbor launch failed: {type(exc).__name__}: {exc}",
            ),
        )
        raise ValueError(f"Harbor launch failed for {task.member.registry_name}") from exc
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)
        os.close(lease_descriptor)

    terminal = _terminal_from_luna_harbor(
        attempt_dir,
        task,
        attempt,
        command_sha256,
        project_root=project_root,
    )
    if terminal is not None:
        _reject_persisted_provider_values(attempt_dir, provider_values)
        proof = _model_proof(project_root, terminal)
        write_bytes_once(attempt_dir / "model-proof.json", proof.canonical_bytes())
        if terminal.exception_type in _INTERRUPTION_EXCEPTIONS:
            write_receipt(
                attempt_dir / "interrupted.json",
                InterruptedReceipt(
                    task=task.key,
                    attempt=attempt,
                    command_sha256=command_sha256,
                    reason=(
                        "keyboard_interrupt"
                        if terminal.exception_type == "KeyboardInterrupt"
                        else "cancelled"
                    ),
                ),
            )
            return "interrupted"
        write_receipt(attempt_dir / "terminal.json", terminal)
        return "terminal"
    if forwarded:
        write_receipt(
            attempt_dir / "interrupted.json",
            InterruptedReceipt(
                task=task.key,
                attempt=attempt,
                command_sha256=command_sha256,
                reason="signal",
            ),
        )
        return "interrupted"
    reason = f"Harbor exited {returncode} without exactly one result"
    write_receipt(
        attempt_dir / "fault.json",
        FaultReceipt(
            task=task.key,
            attempt=attempt,
            command_sha256=command_sha256,
            reason=reason,
        ),
    )
    raise ValueError(f"{task.member.registry_name}: {reason}")


def _terminal_from_luna_harbor(
    attempt_dir: Path,
    task: PlannedTask,
    attempt: int,
    command_sha256: str,
    *,
    project_root: Path,
) -> TerminalReceipt | None:
    result_path = _trial_result_path(attempt_dir)
    if result_path is None:
        return None
    result_data = read_regular_file(result_path, "Luna Harbor trial result")
    result = _json_object(result_data, "Luna Harbor trial result")
    config_path = result_path.parent / "config.json"
    config_data = read_regular_file(config_path, "Luna Harbor trial config")
    config = _json_object(config_data, "Luna Harbor trial config")
    observed_name, observed_digest = _result_identity(result, config)
    if observed_name != task.member.registry_name:
        raise ValueError(
            f"Harbor result task differs from selector: {observed_name!r} "
            f"!= {task.member.registry_name!r}"
        )
    if observed_digest != task.member.package_digest:
        raise ValueError(
            f"Harbor result package digest differs from selector: {observed_digest!r} "
            f"!= {task.member.package_digest!r}"
        )
    journal_path = result_path.parent / "agent" / "evidence-harness" / "events.jsonl"
    journal_binding = (
        _project_file_binding(journal_path, project_root)
        if journal_path.is_file() and not journal_path.is_symlink()
        else None
    )
    reward = _reward(result)
    exception_type = _exception_type(result)
    status: Literal["passed", "failed", "error"]
    if exception_type is not None or reward is None:
        status = "error"
    elif reward == 1.0:
        status = "passed"
    else:
        status = "failed"
    return TerminalReceipt(
        task=task.key,
        member=task.member,
        ordinal=task.ordinal,
        attempt=attempt,
        command_sha256=command_sha256,
        status=status,
        reward=reward,
        exception_type=exception_type,
        result=_project_file_binding(result_path, project_root),
        config=_project_file_binding(config_path, project_root),
        journal=journal_binding,
    )


def _trial_result_path(attempt_dir: Path) -> Path | None:
    job_root = attempt_dir / "harbor" / "run"
    all_results = tuple(sorted((attempt_dir / "harbor").rglob("result.json")))
    aggregate = job_root / "result.json"
    trials = tuple(path for path in all_results if path.parent.parent == job_root)
    unexpected = tuple(path for path in all_results if path != aggregate and path not in trials)
    if unexpected:
        raise ValueError(f"attempt contains an unexpected Harbor result: {unexpected[0]}")
    if not trials:
        return None
    if len(trials) != 1:
        raise ValueError(f"attempt contains multiple Harbor trial results: {attempt_dir}")
    return trials[0]


def _model_proof(project_root: Path, terminal: TerminalReceipt) -> HarborModelProof:
    result = _json_object(
        read_regular_file(project_root / terminal.result.path, "Luna Harbor result"),
        "Luna Harbor result",
    )
    saved_config = _json_object(
        read_regular_file(project_root / terminal.config.path, "Luna Harbor config"),
        "Luna Harbor config",
    )
    result_config = _mapping(result.get("config"))
    saved_agent = _mapping(saved_config.get("agent"))
    result_agent = _mapping(result_config.get("agent"))
    agent_info = _mapping(result.get("agent_info"))
    model_info = _mapping(agent_info.get("model_info"))
    agent_result = _mapping(result.get("agent_result"))
    model_usage = _mapping(agent_result.get("model_usage"))
    try:
        return HarborModelProof.model_validate(
            {
                "saved_config_model": saved_agent.get("model_name"),
                "result_config_model": result_agent.get("model_name"),
                "result_model_provider": model_info.get("provider"),
                "result_model_name": model_info.get("name"),
                "model_usage_keys": tuple(sorted(model_usage)),
            }
        )
    except ValidationError as exc:
        raise ValueError("Harbor result does not prove the frozen Luna model") from exc


def _reject_persisted_provider_values(
    attempt_dir: Path,
    values: tuple[str, str],
) -> None:
    encoded = tuple(value.encode() for value in values)
    for path in attempt_dir.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        with path.open("rb") as stream:
            tail = b""
            while chunk := stream.read(1024 * 1024):
                data = tail + chunk
                if any(value in data for value in encoded):
                    relative = path.relative_to(attempt_dir)
                    raise ValueError(f"Provider value persisted in {relative}")
                tail = data[-max(len(value) for value in encoded) :]


def _bind_run_seal(
    project_root: Path,
    run_root: Path,
    executable: BoundExecutable,
    proof: SelectorProof,
    provider: ProviderEnvironmentAttestation,
) -> RunSeal:
    producer = attest_git_runtime_source(project_root)
    seal = RunSeal(
        executable=executable.file,
        executable_commit=executable.executable_commit,
        protocol=executable.protocol.protocol_file,
        cohort=executable.protocol.cohort_file,
        run_plan_sha256=executable.spec.run_plan_sha256,
        selector_proof=proof,
        producer=producer,
        provider_environment=provider,
    )
    path = run_root / "run-seal.json"
    if os.path.lexists(path):
        data = read_regular_file(path, "Luna run seal")
        try:
            existing = RunSeal.model_validate_json(data)
        except ValidationError as exc:
            raise ValueError("invalid Luna run seal") from exc
        if data != existing.canonical_bytes() or existing != seal:
            raise ValueError("Luna run configuration changed")
    else:
        write_bytes_once(path, seal.canonical_bytes())
    return seal


def _nul_frame(values: tuple[str, ...]) -> bytes:
    if any("\0" in value for value in values):
        raise ValueError("NUL is not allowed in command fields")
    return b"\0".join(value.encode() for value in values)


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True).encode() + b"\n"
