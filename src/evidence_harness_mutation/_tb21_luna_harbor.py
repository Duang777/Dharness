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
    IntentReceipt,
    InterruptedReceipt,
    LaunchReceipt,
    SelectorProof,
    TerminalReceipt,
    _collection_summary,
    _debian_mount_arguments,
    _json_object,
    _mapping,
    _require_same_runtime,
    _task_root,
    _terminal_from_harbor,
    fold_task_receipts,
    preflight_harbor,
    read_regular_file,
    write_bytes_once,
    write_receipt,
)
from evidence_harness_mutation._tb21_luna_manifest import (
    MODEL,
    BoundExecutable,
    PlannedTask,
    invocation_command,
)
from evidence_harness_mutation.model import FrozenModel, Sha256
from evidence_harness_mutation.prefixbench import PrefixBenchFileBinding
from evidence_harness_mutation.tb21_luna_rq2_protocol import (
    LUNA_RUN_ROOT,
    PROVIDER_ENDPOINT_SHA256,
    provider_bundle_sha256,
)

_INTERRUPTION_EXCEPTIONS = frozenset({"CancelledError", "KeyboardInterrupt"})
_PROVIDER_KEYS = ("OPENAI_API_KEY", "OPENAI_BASE_URL")


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
    child_env, provider = provider_environment(environment)
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


def fold_luna_task_receipts(
    project_root: Path,
    run_root: Path,
    task: PlannedTask,
):
    state = fold_task_receipts(run_root, task)
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

    terminal = _terminal_from_harbor(
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
