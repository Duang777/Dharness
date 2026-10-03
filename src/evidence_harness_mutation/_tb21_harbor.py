from __future__ import annotations

import asyncio
import fcntl
import hashlib
import importlib.metadata
import json
import os
import shutil
import signal
import subprocess
import tomllib
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import Field, ValidationError, model_validator

from evidence_harness.collection_profile import PREFIXBENCH_V1
from evidence_harness.protocol import ProducerAttestation
from evidence_harness.source_binding import attest_git_runtime_source
from evidence_harness_mutation._tb21_manifest import (
    MODEL,
    BoundExecutable,
    FrozenRunPlan,
    PlannedTask,
    RegistryMember,
    Tb21TaskKey,
    file_binding,
    git_bytes,
    git_text,
    invocation_command,
    read_regular_file,
)
from evidence_harness_mutation.model import FrozenModel, Sha256
from evidence_harness_mutation.prefixbench import PrefixBenchFileBinding
from evidence_harness_mutation.tb21_sensitivity_protocol import (
    SENSITIVITY_RUN_ROOT,
    TB21_COMMIT,
    TB21_MANIFEST_BLOB,
    TB21_MANIFEST_SHA256,
    TB21_REPOSITORY,
    TB21_TASKS_TREE,
    TB21_TREE,
)

_INTERRUPTION_EXCEPTIONS = frozenset({"CancelledError", "KeyboardInterrupt"})
_RECEIPT_NAMES = ("intent.json", "launch.json", "interrupted.json", "terminal.json", "fault.json")
_DEBIAN_BOOKWORM_HTTPS_TASKS = frozenset(
    {
        "break-filter-js-from-html",
        "cancel-async-tasks",
        "circuit-fibsqrt",
        "cobol-modernization",
        "code-from-image",
        "count-dataset-tokens",
        "distribution-search",
        "feal-differential-cryptanalysis",
        "feal-linear-cryptanalysis",
        "filter-js-from-html",
        "fix-git",
        "gcode-to-text",
        "headless-terminal",
        "large-scale-text-editing",
        "llm-inference-batching-scheduler",
        "log-summary-date-ranges",
        "make-doom-for-mips",
        "make-mips-interpreter",
        "model-extraction-relu-logits",
        "modernize-scientific-stack",
        "mteb-leaderboard",
        "mteb-retrieve",
        "nginx-request-logging",
        "openssl-selfsigned-cert",
        "portfolio-optimization",
        "protein-assembly",
        "pypi-server",
        "pytorch-model-cli",
        "pytorch-model-recovery",
        "raman-fitting",
        "regex-chess",
        "sanitize-git-repo",
        "schemelike-metacircular-eval",
        "sqlite-db-truncate",
        "train-fasttext",
        "tune-mjcf",
        "video-processing",
        "vulnerable-secret",
    }
)
_DEBIAN_BULLSEYE_MAIN_TASKS = frozenset({"qemu-alpine-ssh", "qemu-startup"})
_DEBIAN_TRIXIE_HTTPS_TASKS = frozenset({"build-pmars"})


class HarborRuntimeIdentity(FrozenModel):
    version: str = Field(min_length=1)
    executable: PrefixBenchFileBinding
    distribution_files: int = Field(ge=1)
    distribution_sha256: Sha256


class ResolvedMember(FrozenModel):
    registry_name: str = Field(pattern=r"^terminal-bench/[^/\x00]+$")
    package_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")


class SelectorProof(FrozenModel):
    status: Literal["compatible"] = "compatible"
    runtime: HarborRuntimeIdentity
    observed_registry_path: Literal["tasks/dataset.toml"] = "tasks/dataset.toml"
    members: tuple[ResolvedMember, ...] = Field(min_length=61, max_length=61)
    provider_calls: Literal[0] = 0


class BlockReason(FrozenModel):
    code: Literal[
        "checkout_identity_mismatch",
        "manifest_membership_mismatch",
        "harbor_unavailable",
        "repo_dataset_toml_not_resolved",
        "selector_resolution_failed",
        "selector_not_package_identity",
        "selector_membership_mismatch",
    ]
    message: str = Field(min_length=1)
    harbor_version: str | None = None
    observed_registry_path: str | None = None


class ExecutablePreflight(FrozenModel):
    schema_version: Literal[1] = 1
    ready_for_provider_execution: bool
    executable_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    runtime: HarborRuntimeIdentity | None
    proof: SelectorProof | None
    blockers: tuple[BlockReason, ...]

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        if self.ready_for_provider_execution != (self.proof is not None and not self.blockers):
            raise ValueError("TB2.1 preflight decision is inconsistent")
        return self


class RunSeal(FrozenModel):
    schema_version: Literal[1] = 1
    executable: PrefixBenchFileBinding
    executable_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    protocol: PrefixBenchFileBinding
    matrix: PrefixBenchFileBinding
    run_plan_sha256: Sha256
    selector_proof: SelectorProof
    producer: ProducerAttestation
    model: Literal["openai/modelhub/gpt-5.6-terra"] = MODEL
    collection_profile: Literal["prefixbench-v1"] = "prefixbench-v1"
    provider_env_sha256: Sha256

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.model_dump(mode="json"))


class IntentReceipt(FrozenModel):
    schema_version: Literal[1] = 1
    receipt: Literal["intent"] = "intent"
    task: Tb21TaskKey
    member: RegistryMember
    ordinal: int = Field(ge=1, le=61)
    attempt: int = Field(ge=1)
    invocation_sha256: Sha256
    command_sha256: Sha256


class LaunchReceipt(FrozenModel):
    schema_version: Literal[1] = 1
    receipt: Literal["launch"] = "launch"
    task: Tb21TaskKey
    attempt: int = Field(ge=1)
    command_sha256: Sha256
    pid: int = Field(ge=1)
    process_group_id: int = Field(ge=1)
    started_at: str = Field(min_length=1)


class InterruptedReceipt(FrozenModel):
    schema_version: Literal[1] = 1
    receipt: Literal["interrupted"] = "interrupted"
    task: Tb21TaskKey
    attempt: int = Field(ge=1)
    command_sha256: Sha256
    reason: Literal["signal", "cancelled", "keyboard_interrupt"]


class TerminalReceipt(FrozenModel):
    schema_version: Literal[1] = 1
    receipt: Literal["terminal"] = "terminal"
    task: Tb21TaskKey
    member: RegistryMember
    ordinal: int = Field(ge=1, le=61)
    attempt: int = Field(ge=1)
    command_sha256: Sha256
    status: Literal["passed", "failed", "error"]
    reward: float | None
    exception_type: str | None
    result: PrefixBenchFileBinding
    config: PrefixBenchFileBinding
    journal: PrefixBenchFileBinding | None


class FaultReceipt(FrozenModel):
    schema_version: Literal[1] = 1
    receipt: Literal["fault"] = "fault"
    task: Tb21TaskKey
    attempt: int = Field(ge=1)
    command_sha256: Sha256
    reason: str = Field(min_length=1)


class FoldedTask(FrozenModel):
    status: Literal["pending", "active", "interrupted", "terminal", "faulted"]
    next_attempt: int = Field(ge=1)
    terminal: TerminalReceipt | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def validate_state(self) -> Self:
        if (self.status == "terminal") != (self.terminal is not None):
            raise ValueError("terminal folded state must contain one terminal receipt")
        if (self.status == "faulted") != (self.reason is not None):
            raise ValueError("faulted folded state must contain one reason")
        return self


class CollectionSummary(FrozenModel):
    schema_version: Literal[1] = 1
    tasks: Literal[61] = 61
    terminal: int = Field(ge=0, le=61)
    pending: int = Field(ge=0, le=61)
    interrupted: int = Field(ge=0, le=61)
    status: Literal["complete", "interrupted"]

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        if self.terminal + self.pending + self.interrupted != self.tasks:
            raise ValueError("TB2.1 collection counts do not sum to 61")
        if (self.status == "complete") != (self.terminal == self.tasks):
            raise ValueError("TB2.1 collection completion status is inconsistent")
        return self


def preflight_harbor(
    executable: BoundExecutable,
    *,
    tb21_checkout: Path,
) -> ExecutablePreflight:
    try:
        _verify_checkout(executable.plan, tb21_checkout)
    except (OSError, ValueError) as exc:
        return _blocked(
            executable,
            BlockReason(
                code="checkout_identity_mismatch",
                message=str(exc),
            ),
        )
    try:
        runtime = attest_harbor()
    except (OSError, ValueError, importlib.metadata.PackageNotFoundError) as exc:
        return _blocked(
            executable,
            BlockReason(
                code="harbor_unavailable",
                message=str(exc),
            ),
        )

    try:
        observed = _observed_registry_path()
    except (ImportError, AttributeError, TypeError, ValueError) as exc:
        return _blocked(
            executable,
            BlockReason(
                code="harbor_unavailable",
                message=f"cannot probe Harbor repository resolver: {exc}",
                harbor_version=runtime.version,
            ),
            runtime=runtime,
        )
    if observed != "tasks/dataset.toml":
        return _blocked(
            executable,
            BlockReason(
                code="repo_dataset_toml_not_resolved",
                message=("Harbor does not treat the frozen registry path as a dataset manifest"),
                harbor_version=runtime.version,
                observed_registry_path=observed,
            ),
            runtime=runtime,
        )

    expected = tuple(
        ResolvedMember(
            registry_name=task.member.registry_name,
            package_digest=task.member.package_digest,
        )
        for task in executable.plan.tasks
    )
    try:
        actual = _resolve_package_members(
            executable.spec.harbor.command_prefix,
            tuple(item.registry_name for item in expected),
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        return _blocked(
            executable,
            BlockReason(
                code="selector_resolution_failed",
                message=f"Harbor selector resolution failed: {exc}",
                harbor_version=runtime.version,
                observed_registry_path=observed,
            ),
            runtime=runtime,
        )
    if actual != expected:
        return _blocked(
            executable,
            BlockReason(
                code="selector_membership_mismatch",
                message="Harbor did not preserve all 61 registry names and package digests",
                harbor_version=runtime.version,
                observed_registry_path=observed,
            ),
            runtime=runtime,
        )
    proof = SelectorProof(runtime=runtime, members=actual)
    return ExecutablePreflight(
        ready_for_provider_execution=True,
        executable_commit=executable.executable_commit,
        runtime=runtime,
        proof=proof,
        blockers=(),
    )


def attest_harbor() -> HarborRuntimeIdentity:
    distribution = importlib.metadata.distribution("harbor")
    executable_path_raw = shutil.which("harbor")
    if executable_path_raw is None:
        raise ValueError("harbor is not on PATH")
    executable_path = Path(executable_path_raw).resolve()
    executable_data = read_regular_file(executable_path, "Harbor executable")

    digest = hashlib.sha256()
    count = 0
    for relative in sorted(distribution.files or (), key=lambda item: str(item).encode()):
        path = Path(str(distribution.locate_file(relative)))
        if path.is_symlink() or not path.is_file():
            continue
        data = path.read_bytes()
        encoded = str(relative).encode()
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
        count += 1
    if count == 0:
        raise ValueError("Harbor distribution has no readable files")
    return HarborRuntimeIdentity(
        version=distribution.version,
        executable=PrefixBenchFileBinding(
            path=executable_path.as_posix(),
            bytes=len(executable_data),
            sha256=hashlib.sha256(executable_data).hexdigest(),
        ),
        distribution_files=count,
        distribution_sha256=digest.hexdigest(),
    )


def collect_harbor(
    project_root: Path,
    executable: BoundExecutable,
    preflight: ExecutablePreflight,
    *,
    env_file: Path,
) -> CollectionSummary:
    if not preflight.ready_for_provider_execution or preflight.proof is None:
        raise ValueError("TB2.1 collection requires a passing executable preflight")
    env_data = read_regular_file(env_file, "Provider env file")
    resolved_env_file = env_file.resolve()
    root = project_root / SENSITIVITY_RUN_ROOT
    if os.path.lexists(root):
        if root.is_symlink() or not root.is_dir():
            raise ValueError("TB2.1 run root must be a directory")
    else:
        root.mkdir(parents=True)
    lock_path = root / "collector.lock"
    with lock_path.open("w", encoding="ascii") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("another TB2.1 collector is active") from exc
        seal = _bind_run_seal(
            project_root,
            root,
            executable,
            preflight.proof,
            hashlib.sha256(env_data).hexdigest(),
        )
        terminal = 0
        for task in executable.plan.tasks:
            state = fold_task_receipts(root, task)
            if state.status == "terminal":
                if state.terminal is None:
                    raise AssertionError("terminal task state has no receipt")
                recovered_path = (
                    _task_root(root, task)
                    / f"attempt-{state.terminal.attempt:03d}"
                    / "terminal.json"
                )
                if not recovered_path.exists():
                    write_receipt(recovered_path, state.terminal)
                terminal += 1
                continue
            if state.status == "active":
                raise ValueError(f"Harbor task is still active: {task.member.registry_name}")
            if state.status == "faulted":
                raise ValueError(
                    f"TB2.1 task is faulted and cannot be retried: "
                    f"{task.member.registry_name}: {state.reason}"
                )
            _require_same_runtime(seal.selector_proof.runtime)
            result = _launch_task(
                project_root,
                root,
                task,
                attempt=state.next_attempt,
                env_file=resolved_env_file,
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


def fold_task_receipts(run_root: Path, task: PlannedTask) -> FoldedTask:
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
            status, terminal, reason = _fold_attempt(
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


def _fold_attempt(
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
    discovered = _terminal_from_harbor(
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
    env_file: Path,
    producer: ProducerAttestation,
) -> Literal["terminal", "interrupted"]:
    attempt_dir = _task_root(run_root, task) / f"attempt-{attempt:03d}"
    attempt_dir.mkdir(parents=True, exist_ok=False)
    harbor_root = attempt_dir / "harbor"
    command = [
        *invocation_command(
            (
                "harbor",
                "run",
                "--repo",
                f"{TB21_REPOSITORY}@{TB21_COMMIT}",
                "--registry-path",
                "tasks/dataset.toml",
                "--dataset",
                "terminal-bench-2-1",
            ),
            task,
        ),
        "--jobs-dir",
        str(harbor_root),
        "--job-name",
        "run",
        "--env-file",
        str(env_file),
    ]
    for value in PREFIXBENCH_V1.harbor_agent_kwargs(producer):
        command.extend(("--agent-kwarg", value))
    command.extend(_debian_mount_arguments(project_root, task))
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
        0o644,
    )
    try:
        fcntl.flock(lease_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        child = subprocess.Popen(
            command,
            cwd=project_root,
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


def _terminal_from_harbor(
    attempt_dir: Path,
    task: PlannedTask,
    attempt: int,
    command_sha256: str,
    *,
    project_root: Path,
) -> TerminalReceipt | None:
    results = tuple(sorted((attempt_dir / "harbor").rglob("result.json")))
    if not results:
        return None
    if len(results) != 1:
        raise ValueError(f"attempt contains multiple Harbor results: {attempt_dir}")
    result_path = results[0]
    result_data = read_regular_file(result_path, "Harbor result")
    result = _json_object(result_data, "Harbor result")
    config_path = result_path.parent / "config.json"
    config_data = read_regular_file(config_path, "Harbor trial config")
    config = _json_object(config_data, "Harbor trial config")
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


def _verify_checkout(plan: FrozenRunPlan, checkout: Path) -> None:
    root = checkout.resolve()
    if Path(git_text(root, "rev-parse", "--show-toplevel")).resolve() != root:
        raise ValueError("TB2.1 checkout is not a Git top level")
    if git_text(root, "rev-parse", "HEAD") != TB21_COMMIT:
        raise ValueError("TB2.1 checkout commit differs from the frozen commit")
    if git_text(root, "rev-parse", "HEAD^{tree}") != TB21_TREE:
        raise ValueError("TB2.1 checkout root tree differs from the frozen tree")
    if git_text(root, "rev-parse", f"{TB21_COMMIT}:tasks") != TB21_TASKS_TREE:
        raise ValueError("TB2.1 checkout tasks tree differs from the frozen tree")
    manifest_data = git_bytes(root, "show", f"{TB21_COMMIT}:tasks/dataset.toml")
    if hashlib.sha256(manifest_data).hexdigest() != TB21_MANIFEST_SHA256:
        raise ValueError("TB2.1 dataset manifest hash differs from the frozen hash")
    if git_text(root, "rev-parse", f"{TB21_COMMIT}:tasks/dataset.toml") != TB21_MANIFEST_BLOB:
        raise ValueError("TB2.1 dataset manifest blob differs from the frozen blob")
    members = _manifest_members(manifest_data)
    expected = {task.member.registry_name: task.member.package_digest for task in plan.tasks}
    if any(members.get(name) != digest for name, digest in expected.items()):
        raise ValueError("TB2.1 checkout manifest differs from the frozen run plan")
    for task in plan.tasks:
        short_name = task.member.registry_name.removeprefix("terminal-bench/")
        actual_tree = git_text(root, "rev-parse", f"{TB21_COMMIT}:tasks/{short_name}")
        if actual_tree != task.task_tree:
            raise ValueError(f"TB2.1 task tree differs from the run plan: {short_name}")


def _manifest_members(data: bytes) -> dict[str, str]:
    try:
        parsed = tomllib.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ValueError("invalid TB2.1 dataset manifest") from exc
    rows = parsed.get("tasks")
    if not isinstance(rows, list):
        raise ValueError("TB2.1 dataset manifest has no task list")
    result: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("TB2.1 dataset manifest task is not an object")
        name = row.get("name")
        digest = row.get("digest")
        if not isinstance(name, str) or not isinstance(digest, str):
            raise ValueError("TB2.1 dataset manifest member fields must be strings")
        try:
            member = ResolvedMember(registry_name=name, package_digest=digest)
        except ValidationError as exc:
            raise ValueError("TB2.1 dataset manifest contains an invalid member") from exc
        if member.registry_name in result:
            raise ValueError("TB2.1 dataset manifest contains duplicate members")
        result[member.registry_name] = member.package_digest
    return result


def _observed_registry_path() -> str:
    from harbor.registry.client.git_repo import GitRepoRegistryClient, ResolvedRepo

    repository = ResolvedRepo(
        host="github.com",
        org="harbor-framework",
        name="terminal-bench-2-1",
        git_url=TB21_REPOSITORY,
        ref=TB21_COMMIT,
        resolved_sha=TB21_COMMIT,
    )
    client = GitRepoRegistryClient(
        repository,
        registry_path=Path("tasks/dataset.toml"),
    )
    value = client._registry_rel_path()
    if not isinstance(value, str) or not value:
        raise ValueError("Harbor returned an invalid repository registry path")
    return value


def _resolve_package_members(
    command_prefix: tuple[str, ...],
    selectors: tuple[str, ...],
) -> tuple[ResolvedMember, ...]:
    from harbor.models.job.config import DatasetConfig
    from harbor.models.task.id import PackageTaskId

    if command_prefix[0:2] != ("harbor", "run"):
        raise ValueError("unexpected Harbor command prefix")
    config = DatasetConfig(
        repo=command_prefix[3],
        registry_path=Path(command_prefix[5]),
        name=command_prefix[7],
        task_names=list(selectors),
    )
    task_configs = asyncio.run(config.get_task_configs())
    members: list[ResolvedMember] = []
    for task_config in task_configs:
        task_id = task_config.get_task_id()
        if not isinstance(task_id, PackageTaskId):
            raise ValueError(
                f"selector resolved a non-package task identity: {type(task_id).__name__}"
            )
        members.append(
            ResolvedMember(
                registry_name=f"{task_id.org}/{task_id.name}",
                package_digest=task_id.ref or "",
            )
        )
    return tuple(members)


def _blocked(
    executable: BoundExecutable,
    blocker: BlockReason,
    *,
    runtime: HarborRuntimeIdentity | None = None,
) -> ExecutablePreflight:
    return ExecutablePreflight(
        ready_for_provider_execution=False,
        executable_commit=executable.executable_commit,
        runtime=runtime,
        proof=None,
        blockers=(blocker,),
    )


def _bind_run_seal(
    project_root: Path,
    run_root: Path,
    executable: BoundExecutable,
    proof: SelectorProof,
    provider_env_sha256: str,
) -> RunSeal:
    producer = attest_git_runtime_source(project_root)
    seal = RunSeal(
        executable=executable.file,
        executable_commit=executable.executable_commit,
        protocol=executable.protocol.protocol_file,
        matrix=executable.protocol.matrix_file,
        run_plan_sha256=executable.spec.run_plan_sha256,
        selector_proof=proof,
        producer=producer,
        provider_env_sha256=provider_env_sha256,
    )
    path = run_root / "run-seal.json"
    if os.path.lexists(path):
        data = read_regular_file(path, "TB2.1 run seal")
        try:
            existing = RunSeal.model_validate_json(data)
        except ValidationError as exc:
            raise ValueError("invalid TB2.1 run seal") from exc
        if data != existing.canonical_bytes() or existing != seal:
            raise ValueError("TB2.1 run configuration changed")
    else:
        write_bytes_once(path, seal.canonical_bytes())
    return seal


def _collection_summary(run_root: Path, plan: FrozenRunPlan) -> CollectionSummary:
    states = tuple(fold_task_receipts(run_root, task) for task in plan.tasks)
    faulted = tuple(
        task.member.registry_name
        for task, state in zip(plan.tasks, states, strict=True)
        if state.status == "faulted"
    )
    active = tuple(
        task.member.registry_name
        for task, state in zip(plan.tasks, states, strict=True)
        if state.status == "active"
    )
    if faulted or active:
        raise ValueError("TB2.1 collection cannot summarize faulted or active tasks")
    terminal = sum(state.status == "terminal" for state in states)
    interrupted = sum(state.status == "interrupted" for state in states)
    pending = len(states) - terminal - interrupted
    return CollectionSummary(
        terminal=terminal,
        pending=pending,
        interrupted=interrupted,
        status="complete" if terminal == len(states) else "interrupted",
    )


def _task_root(run_root: Path, task: PlannedTask) -> Path:
    return run_root / "tasks" / (f"{task.ordinal:03d}-{task.key.source_identity_sha256[:12]}")


def _validate_terminal(
    terminal: TerminalReceipt,
    task: PlannedTask,
    attempt: int,
    command_sha256: str,
) -> None:
    _validate_end_receipt(
        terminal.task,
        terminal.attempt,
        terminal.command_sha256,
        task,
        attempt,
        IntentReceipt(
            task=task.key,
            member=task.member,
            ordinal=task.ordinal,
            attempt=attempt,
            invocation_sha256=task.invocation_sha256,
            command_sha256=command_sha256,
        ),
    )
    if terminal.member != task.member or terminal.ordinal != task.ordinal:
        raise ValueError("terminal receipt does not match the run plan")


def _validate_end_receipt(
    key: Tb21TaskKey,
    receipt_attempt: int,
    command_sha256: str,
    task: PlannedTask,
    attempt: int,
    intent: IntentReceipt,
) -> None:
    if key != task.key or receipt_attempt != attempt or command_sha256 != intent.command_sha256:
        raise ValueError("receipt transition does not match its intent")


def _read_model[T: FrozenModel](
    path: Path,
    model: type[T],
    label: str,
) -> T:
    data = read_regular_file(path, label)
    try:
        value = model.model_validate_json(data)
    except ValidationError as exc:
        raise ValueError(f"invalid {label}: {path}") from exc
    if data != _canonical_json(value.model_dump(mode="json")):
        raise ValueError(f"{label} is not canonical JSON: {path}")
    return value


def write_receipt(path: Path, receipt: FrozenModel) -> None:
    write_bytes_once(path, _canonical_json(receipt.model_dump(mode="json")))


def write_bytes_once(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError as exc:
        raise ValueError(f"immutable file already exists: {path}") from exc
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _require_same_runtime(expected: HarborRuntimeIdentity) -> None:
    if attest_harbor() != expected:
        raise ValueError("Harbor runtime changed after TB2.1 preflight")


def _project_file_binding(path: Path, project_root: Path) -> PrefixBenchFileBinding:
    if path.is_symlink():
        raise ValueError(f"TB2.1 raw source cannot be a symlink: {path}")
    resolved = path.resolve()
    root = project_root.resolve()
    try:
        relative = resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"TB2.1 raw source is outside the project: {path}") from exc
    data = read_regular_file(resolved, "TB2.1 raw source")
    return file_binding(relative, data)


def _result_identity(
    result: dict[str, Any],
    saved_config: dict[str, Any],
) -> tuple[str | None, str | None]:
    config = _mapping(result.get("config"))
    task = _mapping(config.get("task")) or _mapping(saved_config.get("task"))
    task_id = _mapping(result.get("task_id"))
    org = task_id.get("org")
    name = task_id.get("name")
    if isinstance(org, str) and org and isinstance(name, str) and name:
        registry_name = f"{org}/{name}"
    else:
        registry_name = None
        for value in (task.get("name"), name, result.get("task_name")):
            if isinstance(value, str) and value:
                registry_name = value
                break
    digest = None
    for value in (
        task_id.get("ref"),
        task_id.get("digest"),
        task.get("ref"),
        task.get("digest"),
        task.get("version"),
    ):
        if isinstance(value, str) and value:
            digest = value
            break
    return registry_name, digest


def _reward(result: dict[str, Any]) -> float | None:
    verifier = _mapping(result.get("verifier_result"))
    rewards = _mapping(verifier.get("rewards"))
    reward = rewards.get("reward")
    return float(reward) if isinstance(reward, int | float) else None


def _exception_type(result: dict[str, Any]) -> str | None:
    exception = _mapping(result.get("exception_info"))
    value = exception.get("exception_type") or exception.get("type")
    return value if isinstance(value, str) and value else None


def _mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _json_object(data: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _process_group_is_alive(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _lease_is_held(path: Path) -> bool:
    with path.open("r+", encoding="ascii") as lease:
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(lease, fcntl.LOCK_UN)
    return False


def _debian_mount_arguments(project_root: Path, task: PlannedTask) -> tuple[str, ...]:
    short_name = task.member.registry_name.removeprefix("terminal-bench/")
    source: Path | None = None
    target = "/etc/apt/sources.list.d/debian.sources"
    if short_name in _DEBIAN_BOOKWORM_HTTPS_TASKS:
        source = project_root / "evaluation" / "debian-https.sources"
    elif short_name in _DEBIAN_BULLSEYE_MAIN_TASKS:
        source = project_root / "evaluation" / "debian-bullseye-main.list"
        target = "/etc/apt/sources.list"
    elif short_name in _DEBIAN_TRIXIE_HTTPS_TASKS:
        source = project_root / "evaluation" / "debian-trixie-https.sources"
    if source is None:
        return ()
    mounts = json.dumps(
        [
            {
                "type": "bind",
                "source": str(source.resolve()),
                "target": target,
                "read_only": True,
            }
        ],
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return "--mounts", mounts, "--yes"


def _nul_frame(values: tuple[str, ...]) -> bytes:
    if any("\0" in value for value in values):
        raise ValueError("NUL is not allowed in command fields")
    return b"\0".join(value.encode("utf-8") for value in values)


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode()
