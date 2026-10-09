from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Sequence
from contextlib import suppress
from dataclasses import dataclass
from decimal import Decimal
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Protocol

from harbor.environments.base import BaseEnvironment
from harbor.environments.docker.docker import DockerEnvironment
from harbor.models.task.config import EnvironmentConfig

from evidence_harness.completion_isolation import (
    CheckExecutor,
    CompletionIsolation,
    CompletionIsolationError,
    CompletionIsolationRequest,
    CompletionIsolationResult,
    IsolationFailureKind,
    UnsupportedCompletionIsolation,
)
from evidence_harness.journal import RunJournal
from evidence_harness.protocol import (
    CheckIsolationEvidence,
    CheckKind,
    CommandReceipt,
    CompletionIsolationEvidence,
    EnvironmentResult,
    FilesystemDelta,
    IsolationCost,
    ShellEnvironment,
    SourceAttestation,
)

_HARBOR_VERSION = "0.23.0"
_CONTROL_MOUNT_TARGETS = frozenset({"/logs", "/harbor/skills"})
_MAX_DOCKER_OPERATIONS = 40
_RESERVED_CLEANUP_OPERATIONS = 6
_CLEANUP_HARD_TIMEOUT_SEC = 90.0
_CLEANUP_OPERATION_TIMEOUT_SEC = 80.0
_CLEANUP_RESERVE_SEC = _CLEANUP_HARD_TIMEOUT_SEC + 5.0
_PAUSE_ACK_TIMEOUT_SEC = 15.0
_PAUSE_OPERATION_TIMEOUT_SEC = 45.0
_PROCESS_TERMINATE_GRACE_SEC = 2.0
_OUTPUT_DRAIN_GRACE_SEC = 2.0
_MAX_DELTA_PATHS_PER_KIND = 100
_MAX_CLI_OUTPUT_BYTES = 2_000_000
_CLASSIFY_SCRIPT = (
    'for path do if [ -L "$path" ]; then printf "l\\n"; '
    'elif [ -d "$path" ]; then printf "d\\n"; '
    'elif [ -e "$path" ]; then printf "f\\n"; '
    'else printf "m\\n"; fi; done'
)
_COMMIT_PAUSE_DEPRECATION = (
    "Flag --pause has been deprecated, and enabled by default. "
    "Use --no-pause to disable pausing during commit."
)
_CANDIDATE_DIGEST_DOMAIN = b"evidence-harness-candidate-rootfs-v1\0"


@dataclass(slots=True)
class DockerCliResult:
    return_code: int
    stdout: str | None
    stderr: str | None


class DockerCli(Protocol):
    async def run(
        self,
        args: Sequence[str],
        *,
        timeout_sec: float,
    ) -> DockerCliResult: ...


class SubprocessDockerCli:
    async def run(
        self,
        args: Sequence[str],
        *,
        timeout_sec: float,
    ) -> DockerCliResult:
        process = await asyncio.create_subprocess_exec(
            "docker",
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert process.stdout is not None
        assert process.stderr is not None
        stdout_task = asyncio.create_task(_read_bounded(process.stdout))
        stderr_task = asyncio.create_task(_read_bounded(process.stderr))
        try:
            async with asyncio.timeout(timeout_sec):
                return_code = await process.wait()
        except BaseException:
            await _terminate_process(process)
            raise
        finally:
            stdout, stderr = await _collect_output_tasks(stdout_task, stderr_task)
        return DockerCliResult(
            return_code=return_code,
            stdout=stdout,
            stderr=stderr,
        )


@dataclass(frozen=True, slots=True)
class DockerIsolationTarget:
    project_name: str
    exec_user: str | None = None
    rejection_reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _DiffEntry:
    kind: str
    path: str


@dataclass(frozen=True, slots=True)
class _Source:
    container_id: str
    image_id: str
    excluded_mounts: tuple[str, ...]
    run_resource_args: tuple[str, ...]
    identity_sha256: str


@dataclass(slots=True)
class _OwnedResources:
    source_id: str | None = None
    source_pause_attempted: bool = False
    pause_task: asyncio.Task[DockerCliResult] | None = None
    source_paused: bool = False
    child_id: str | None = None
    inspector_id: str | None = None
    image_id: str | None = None
    image_reference: str | None = None
    candidate_digest: str | None = None
    snapshot_disposed: bool = False
    source_resumed: bool = False


class DockerCompletionIsolation:
    def __init__(
        self,
        *,
        target: DockerIsolationTarget,
        journal: RunJournal,
        cli: DockerCli | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._target = target
        self._journal = journal
        self._cli = cli or SubprocessDockerCli()
        self._clock = clock

    async def verify(
        self,
        request: CompletionIsolationRequest,
        execute: CheckExecutor,
    ) -> CompletionIsolationResult:
        transaction = _DockerIsolationTransaction(
            target=self._target,
            journal=self._journal,
            cli=self._cli,
            clock=self._clock,
            request=request,
        )
        return await transaction.verify(execute)


class _DockerIsolationTransaction:
    def __init__(
        self,
        *,
        target: DockerIsolationTarget,
        journal: RunJournal,
        cli: DockerCli,
        clock: Callable[[], float],
        request: CompletionIsolationRequest,
    ) -> None:
        self._target = target
        self._journal = journal
        self._cli = cli
        self._clock = clock
        self._request = request
        self._started = clock()
        self._operations = 0
        self._owned = _OwnedResources()
        self._receipts: list[CommandReceipt] = []
        self._checks: list[CheckIsolationEvidence] = []
        self._source: _Source | None = None
        self._source_diff_before = ""
        self._source_diff_after = ""
        self._source_remained_paused = False
        self._cleanup_deadline_monotonic: float | None = None

    async def verify(self, execute: CheckExecutor) -> CompletionIsolationResult:
        self._journal.append(
            "completion_isolation_started",
            {
                "attempt_id": self._request.attempt_id,
                "work_epoch": self._request.work_epoch,
                "backend": "docker-commit-v1",
                "check_ids": [check.id for check in self._request.checks],
            },
        )
        pending: BaseException | None = None
        try:
            await self._prepare()
            await self._run_checks(execute)
            await self._attest_source()
        except asyncio.CancelledError as exc:
            pending = exc
        except CompletionIsolationError as exc:
            pending = exc
        except BaseException as exc:
            pending = self._error(
                IsolationFailureKind.INSPECTION,
                f"{type(exc).__name__}: {exc}",
            )

        cleanup_errors = await self._shielded_cleanup()
        if cleanup_errors:
            raise self._error(
                IsolationFailureKind.CLEANUP,
                "; ".join(cleanup_errors),
            )
        if pending is not None:
            raise pending

        assert self._source is not None
        evidence = CompletionIsolationEvidence(
            backend="docker-commit-v1",
            attempt_id=self._request.attempt_id,
            work_epoch=self._request.work_epoch,
            candidate_image_id=self._owned.image_id or "",
            candidate_digest=self._owned.candidate_digest,
            environment_identity_sha256=self._source.identity_sha256,
            excluded_control_mounts=self._source.excluded_mounts,
            checks=tuple(self._checks),
            source=SourceAttestation(
                container_id_sha256=_sha256_text(self._source.container_id),
                diff_sha256_before=_sha256_text(self._source_diff_before),
                diff_sha256_after=_sha256_text(self._source_diff_after),
                remained_paused=self._source_remained_paused,
                resumed=self._owned.source_resumed,
            ),
            snapshot_image_disposed=self._owned.snapshot_disposed,
            cost=IsolationCost(
                host_operations=self._operations,
                child_count=len(self._checks),
                duration_sec=max(0.0, self._clock() - self._started),
            ),
        )
        return CompletionIsolationResult(
            receipts=tuple(self._receipts),
            evidence=evidence,
        )

    async def _prepare(self) -> None:
        if self._target.rejection_reasons:
            raise self._error(
                IsolationFailureKind.UNSUPPORTED,
                "; ".join(self._target.rejection_reasons),
            )
        if any(check.kind is CheckKind.SERVICE for check in self._request.checks):
            raise self._error(
                IsolationFailureKind.UNSUPPORTED,
                "service completion checks cannot be reproduced in an isolated child",
            )

        daemon_os = (
            await self._run(
                ("info", "--format", "{{json .OSType}}"),
                kind=IsolationFailureKind.PREPARE,
                timeout_sec=10,
            )
        ).stdout
        daemon_os = (daemon_os or "").strip()
        if daemon_os != '"linux"':
            raise self._error(
                IsolationFailureKind.UNSUPPORTED,
                f"Docker daemon OS is not supported: {daemon_os or 'unknown'}",
            )

        source_ids_output = (
            await self._run(
                (
                    "ps",
                    "--filter",
                    f"label=com.docker.compose.project={self._target.project_name}",
                    "--format",
                    "{{.ID}}",
                ),
                kind=IsolationFailureKind.PREPARE,
                timeout_sec=10,
            )
        ).stdout
        source_ids = (source_ids_output or "").split()
        if not source_ids:
            raise self._error(
                IsolationFailureKind.PREPARE,
                "the running Harbor main container could not be resolved",
            )
        if len(source_ids) != 1:
            raise self._error(
                IsolationFailureKind.UNSUPPORTED,
                "the Harbor Compose project contains sidecars or multiple containers",
            )

        source_id = source_ids[0]
        inspect = await self._inspect_container(
            source_id,
            kind=IsolationFailureKind.PREPARE,
        )
        self._source = self._validate_source(inspect)
        self._owned.source_id = self._source.container_id
        await self._validate_process_shape(self._source.container_id)

        self._owned.source_pause_attempted = True
        await self._pause_source(self._source.container_id)
        self._owned.source_paused = True
        self._source_diff_before = await self._canonical_diff(
            self._source.container_id,
            kind=IsolationFailureKind.INSPECTION,
        )
        if not await self._is_source_paused():
            raise self._error(
                IsolationFailureKind.INSPECTION,
                "the live source did not remain paused before commit",
            )

        suffix = uuid.uuid4().hex[:12]
        image_reference = (
            "evidence-harness-isolation:"
            f"{self._source.container_id[:12]}-a{self._request.attempt_id}-{suffix}"
        )
        self._owned.image_reference = image_reference
        commit = await self._run(
            (
                "commit",
                "--pause=false",
                self._source.container_id,
                image_reference,
            ),
            kind=IsolationFailureKind.PREPARE,
            timeout_sec=120,
        )
        image_id = _parse_commit_image_id(commit.stdout or "")
        if image_id is None:
            raise self._error(
                IsolationFailureKind.PREPARE,
                "Docker commit returned an invalid image identifier",
            )
        self._owned.image_id = image_id
        self._owned.candidate_digest = await self._candidate_digest(image_id)
        self._journal.append(
            "completion_candidate_committed",
            {
                "attempt_id": self._request.attempt_id,
                "candidate_image_id": image_id,
                "candidate_digest": self._owned.candidate_digest,
                "source_container_id_sha256": _sha256_text(self._source.container_id),
            },
        )

    async def _candidate_digest(self, image_id: str) -> str:
        result = await self._run(
            (
                "image",
                "inspect",
                "--format",
                "{{json .RootFS.Layers}}",
                image_id,
            ),
            kind=IsolationFailureKind.PREPARE,
            timeout_sec=15,
        )
        try:
            value = json.loads((result.stdout or "").strip())
        except json.JSONDecodeError as exc:
            raise self._error(
                IsolationFailureKind.PREPARE,
                "Docker image rootfs layers are not valid JSON",
            ) from exc
        if (
            not isinstance(value, list)
            or not value
            or any(
                not isinstance(layer, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", layer) is None
                for layer in value
            )
        ):
            raise self._error(
                IsolationFailureKind.PREPARE,
                "Docker image rootfs layers are invalid",
            )
        return _candidate_content_digest(tuple(value))

    async def _pause_source(self, source_id: str) -> None:
        task = asyncio.create_task(
            self._run(
                ("pause", source_id),
                kind=IsolationFailureKind.PREPARE,
                timeout_sec=_PAUSE_OPERATION_TIMEOUT_SEC,
            )
        )
        self._owned.pause_task = task
        done, _ = await asyncio.wait({task}, timeout=_PAUSE_ACK_TIMEOUT_SEC)
        if not done:
            raise self._error(
                IsolationFailureKind.DEADLINE,
                "Docker pause acknowledgement timed out",
            )
        self._owned.pause_task = None
        task.result()

    async def _run_checks(self, execute: CheckExecutor) -> None:
        assert self._source is not None
        assert self._owned.image_id is not None
        assert self._owned.candidate_digest is not None
        for ordinal, check in enumerate(self._request.checks, start=1):
            self._journal.append(
                "completion_check_started",
                {
                    "attempt_id": self._request.attempt_id,
                    "check_id": check.id,
                    "ordinal": ordinal,
                },
            )
            child_id = await self._start_child(check.id, ordinal)
            environment = _DockerCheckEnvironment(
                child_id=child_id,
                exec_user=self._target.exec_user,
                run=self._run,
            )
            receipt = await execute(
                check,
                environment,
                self._request.deadline_monotonic - _CLEANUP_RESERVE_SEC,
                self._owned.candidate_digest,
            )
            self._receipts.append(receipt)
            delta = await self._inspect_delta(child_id)
            isolated = CheckIsolationEvidence(
                check_id=check.id,
                receipt_sequence=receipt.sequence,
                receipt_observation_sha256=receipt.observation_fingerprint,
                child_id_sha256=_sha256_text(child_id),
                started_from_image_id=self._owned.image_id,
                delta=delta,
                disposed=False,
            )
            self._journal.append("completion_check_isolated", isolated)
            await self._remove_child(child_id, cleanup=False)
            isolated = isolated.model_copy(update={"disposed": True})
            self._checks.append(isolated)
            self._journal.append(
                "completion_check_disposed",
                {
                    "attempt_id": self._request.attempt_id,
                    "check_id": check.id,
                    "child_id_sha256": isolated.child_id_sha256,
                },
            )
            if not receipt.succeeded:
                break

    async def _start_child(self, check_id: str, ordinal: int) -> str:
        return await self._start_container(
            role="check",
            suffix=f"c{ordinal}",
            check_id=check_id,
        )

    async def _ensure_inspector(self) -> str:
        if self._owned.inspector_id is not None:
            return self._owned.inspector_id
        inspector_id = await self._start_container(
            role="inspector",
            suffix="baseline",
            check_id=None,
        )
        self._journal.append(
            "completion_candidate_inspector_started",
            {
                "attempt_id": self._request.attempt_id,
                "container_id_sha256": _sha256_text(inspector_id),
            },
        )
        return inspector_id

    async def _start_container(
        self,
        *,
        role: str,
        suffix: str,
        check_id: str | None,
    ) -> str:
        assert self._source is not None
        assert self._owned.image_id is not None
        name = (
            f"evidence-harness-{self._source.container_id[:12]}-"
            f"a{self._request.attempt_id}-{suffix}-{uuid.uuid4().hex[:8]}"
        )
        labels = [
            "--label",
            "evidence-harness.isolation=true",
            "--label",
            f"evidence-harness.attempt={self._request.attempt_id}",
            "--label",
            f"evidence-harness.role={role}",
        ]
        if check_id is not None:
            labels.extend(["--label", f"evidence-harness.check={check_id}"])
        if role == "check":
            self._owned.child_id = name
        else:
            self._owned.inspector_id = name
        result = await self._run(
            (
                "run",
                "-d",
                "--network",
                "none",
                "--name",
                name,
                *labels,
                *self._source.run_resource_args,
                "--entrypoint",
                "sh",
                self._owned.image_id,
                "-c",
                "while :; do sleep 3600; done",
            ),
            kind=IsolationFailureKind.CHILD_START,
            timeout_sec=30,
        )
        child_id = (result.stdout or "").strip()
        if not re.fullmatch(r"[0-9a-f]{12,64}", child_id):
            raise self._error(
                IsolationFailureKind.CHILD_START,
                "Docker run returned an invalid child identifier",
            )
        if role == "check":
            self._owned.child_id = child_id
        else:
            self._owned.inspector_id = child_id
        inspect = await self._inspect_container(
            child_id,
            kind=IsolationFailureKind.CHILD_START,
        )
        if inspect.get("Image") != self._owned.image_id:
            raise self._error(
                IsolationFailureKind.CHILD_START,
                "the child did not start from the committed candidate image",
            )
        state = _object(inspect.get("State"), "child state")
        if state.get("Running") is not True or state.get("Paused") is not False:
            raise self._error(
                IsolationFailureKind.CHILD_START,
                "the isolated child is not running",
            )
        if inspect.get("Mounts"):
            raise self._error(
                IsolationFailureKind.CHILD_START,
                "the isolated child unexpectedly has runtime mounts",
            )
        return child_id

    async def _inspect_delta(self, child_id: str) -> FilesystemDelta:
        raw = await self._canonical_diff(
            child_id,
            kind=IsolationFailureKind.INSPECTION,
        )
        entries = _parse_canonical_diff(raw)
        changed_paths = tuple(sorted(entry.path for entry in entries if entry.kind == "C"))
        classifications: dict[str, str] = {}
        baseline_classifications: dict[str, str] = {}
        if changed_paths:
            classifications = await self._classify_paths(
                child_id,
                changed_paths,
            )
            baseline_classifications = await self._classify_paths(
                await self._ensure_inspector(),
                changed_paths,
            )

        added = sorted({entry.path for entry in entries if entry.kind == "A"})
        modified = sorted(
            {
                entry.path
                for entry in entries
                if entry.kind == "C"
                and (
                    classifications[entry.path] != "d"
                    or baseline_classifications[entry.path] != "d"
                )
            }
        )
        deleted = sorted({entry.path for entry in entries if entry.kind == "D"})
        bounded_added, omitted_added = _bounded_paths(added)
        bounded_modified, omitted_modified = _bounded_paths(modified)
        bounded_deleted, omitted_deleted = _bounded_paths(deleted)
        return FilesystemDelta(
            sha256=_sha256_text(raw),
            added=bounded_added,
            modified=bounded_modified,
            deleted=bounded_deleted,
            omitted_count=omitted_added + omitted_modified + omitted_deleted,
        )

    async def _classify_paths(
        self,
        container_id: str,
        paths: Sequence[str],
    ) -> dict[str, str]:
        result = await self._run(
            (
                "exec",
                container_id,
                "sh",
                "-c",
                _CLASSIFY_SCRIPT,
                "classify",
                *paths,
            ),
            kind=IsolationFailureKind.INSPECTION,
            timeout_sec=15,
        )
        values = (result.stdout or "").splitlines()
        if len(values) != len(paths) or any(value not in {"d", "f", "l", "m"} for value in values):
            raise self._error(
                IsolationFailureKind.INSPECTION,
                "could not classify changed child paths",
            )
        return dict(zip(paths, values, strict=True))

    async def _attest_source(self) -> None:
        self._source_remained_paused = await self._is_source_paused()
        if not self._source_remained_paused:
            raise self._error(
                IsolationFailureKind.INSPECTION,
                "the live source was resumed before attestation",
            )
        assert self._owned.source_id is not None
        self._source_diff_after = await self._canonical_diff(
            self._owned.source_id,
            kind=IsolationFailureKind.INSPECTION,
        )
        self._journal.append(
            "completion_source_attested",
            {
                "attempt_id": self._request.attempt_id,
                "diff_sha256_before": _sha256_text(self._source_diff_before),
                "diff_sha256_after": _sha256_text(self._source_diff_after),
                "remained_paused": self._source_remained_paused,
            },
        )

    async def _validate_process_shape(self, source_id: str) -> None:
        result = await self._run(
            ("top", source_id, "-eo", "pid,ppid,comm"),
            kind=IsolationFailureKind.PREPARE,
            timeout_sec=10,
        )
        processes = []
        lines = (result.stdout or "").splitlines()
        if not lines or tuple(lines[0].casefold().split()) != (
            "pid",
            "ppid",
            "command",
        ):
            raise self._error(
                IsolationFailureKind.UNSUPPORTED,
                "the source process table has an unsupported format",
            )
        for line in lines[1:]:
            fields = line.split(maxsplit=2)
            if len(fields) != 3:
                raise self._error(
                    IsolationFailureKind.UNSUPPORTED,
                    "the source process tree could not be classified",
                )
            processes.append(fields[2])
        if tuple(processes) not in {("sh", "sleep"), ("sleep",), ("tail",)}:
            raise self._error(
                IsolationFailureKind.UNSUPPORTED,
                "the source contains a non-standard or agent-started process",
            )

    def _validate_source(self, inspect: dict[str, Any]) -> _Source:
        state = _object(inspect.get("State"), "source state")
        if state.get("Running") is not True or state.get("Paused") is not False:
            raise self._error(
                IsolationFailureKind.PREPARE,
                "the Harbor source container is not running and unpaused",
            )
        config = _object(inspect.get("Config"), "source config")
        labels = _object(config.get("Labels"), "source labels")
        if labels.get("com.docker.compose.project") != self._target.project_name:
            raise self._error(
                IsolationFailureKind.PREPARE,
                "the source Compose project label does not match",
            )
        if labels.get("com.docker.compose.service") != "main":
            raise self._error(
                IsolationFailureKind.PREPARE,
                "the resolved source is not the Compose main service",
            )
        if config.get("Volumes"):
            raise self._error(
                IsolationFailureKind.UNSUPPORTED,
                "image-declared volumes are not supported",
            )

        host = _object(inspect.get("HostConfig"), "source host config")
        unsupported_flags = {
            "Privileged": True,
            "ReadonlyRootfs": True,
            "OomKillDisable": True,
        }
        for field, unsupported in unsupported_flags.items():
            if host.get(field) == unsupported:
                raise self._error(
                    IsolationFailureKind.UNSUPPORTED,
                    f"source HostConfig.{field} is not supported",
                )
        for field in ("Devices", "DeviceRequests", "CapAdd", "CapDrop", "SecurityOpt"):
            if host.get(field):
                raise self._error(
                    IsolationFailureKind.UNSUPPORTED,
                    f"source HostConfig.{field} is not supported",
                )
        allowed_namespace_values = {
            "PidMode": {"", None},
            "IpcMode": {"", "private", None},
            "UTSMode": {"", None},
            "CgroupnsMode": {"", "private", None},
        }
        for field, allowed in allowed_namespace_values.items():
            if host.get(field) not in allowed:
                raise self._error(
                    IsolationFailureKind.UNSUPPORTED,
                    f"source HostConfig.{field} is not supported",
                )

        excluded_mounts: list[str] = []
        mounts = inspect.get("Mounts") or []
        if not isinstance(mounts, list):
            raise self._error(
                IsolationFailureKind.PREPARE,
                "source mounts are not a list",
            )
        for value in mounts:
            mount = _object(value, "source mount")
            destination = mount.get("Destination")
            source = mount.get("Source")
            if not isinstance(destination, str):
                raise self._error(
                    IsolationFailureKind.PREPARE,
                    "source mount has no destination",
                )
            if not _is_control_mount_target(destination):
                raise self._error(
                    IsolationFailureKind.UNSUPPORTED,
                    f"unsupported mount target: {destination}",
                )
            if "docker.sock" in str(source) or "docker.sock" in destination:
                raise self._error(
                    IsolationFailureKind.UNSUPPORTED,
                    "Docker socket mounts are not supported",
                )
            excluded_mounts.append(destination)

        container_id = inspect.get("Id")
        image_id = inspect.get("Image")
        if not isinstance(container_id, str) or not re.fullmatch(r"[0-9a-f]{12,64}", container_id):
            raise self._error(
                IsolationFailureKind.PREPARE,
                "source inspect returned an invalid container identifier",
            )
        if not isinstance(image_id, str) or not image_id:
            raise self._error(
                IsolationFailureKind.PREPARE,
                "source inspect returned no image identifier",
            )
        identity = {
            "container_id_sha256": _sha256_text(container_id),
            "image_id": image_id,
            "project_name": self._target.project_name,
            "working_dir": config.get("WorkingDir"),
            "user": config.get("User"),
            "excluded_mounts": sorted(excluded_mounts),
        }
        return _Source(
            container_id=container_id,
            image_id=image_id,
            excluded_mounts=tuple(sorted(excluded_mounts)),
            run_resource_args=_resource_args(host),
            identity_sha256=_sha256_json(identity),
        )

    async def _inspect_container(
        self,
        container_id: str,
        *,
        kind: IsolationFailureKind,
    ) -> dict[str, Any]:
        result = await self._run(
            ("inspect", container_id),
            kind=kind,
            timeout_sec=15,
        )
        try:
            payload = json.loads(result.stdout or "")
        except json.JSONDecodeError as exc:
            raise self._error(kind, f"Docker inspect returned invalid JSON: {exc}") from exc
        if not isinstance(payload, list) or len(payload) != 1:
            raise self._error(kind, "Docker inspect did not return one container")
        return _object(payload[0], "container inspect")

    async def _canonical_diff(
        self,
        container_id: str,
        *,
        kind: IsolationFailureKind,
    ) -> str:
        result = await self._run(
            ("diff", container_id),
            kind=kind,
            timeout_sec=30,
        )
        entries = _parse_diff(
            result.stdout or "",
            error=lambda detail: self._error(kind, detail),
        )
        return "".join(f"{entry.kind} {entry.path}\n" for entry in entries)

    async def _is_source_paused(self) -> bool:
        assert self._owned.source_id is not None
        result = await self._run(
            (
                "inspect",
                "--format",
                "{{json .State.Paused}}",
                self._owned.source_id,
            ),
            kind=IsolationFailureKind.INSPECTION,
            timeout_sec=10,
        )
        return (result.stdout or "").strip() == "true"

    async def _remove_child(self, child_id: str, *, cleanup: bool) -> None:
        result = await self._run(
            ("rm", "-f", child_id),
            kind=IsolationFailureKind.CLEANUP,
            timeout_sec=15,
            cleanup=cleanup,
            allow_nonzero=cleanup,
        )
        if result.return_code != 0 and not _is_missing_resource(result, "container"):
            raise self._error(
                IsolationFailureKind.CLEANUP,
                _docker_failure_detail(("rm", "-f"), result),
            )
        if self._owned.child_id == child_id:
            self._owned.child_id = None

    async def _shielded_cleanup(self) -> list[str]:
        hard_deadline = self._clock() + _CLEANUP_HARD_TIMEOUT_SEC
        self._cleanup_deadline_monotonic = hard_deadline - (
            _CLEANUP_HARD_TIMEOUT_SEC - _CLEANUP_OPERATION_TIMEOUT_SEC
        )
        task = asyncio.create_task(self._cleanup())
        cancelled = False
        while not task.done() and self._clock() < self._cleanup_deadline_monotonic:
            try:
                await asyncio.wait(
                    {task},
                    timeout=max(0.0, self._cleanup_deadline_monotonic - self._clock()),
                )
                if not task.done():
                    break
            except asyncio.CancelledError:
                cancelled = True
        if not task.done():
            task.cancel()
            while not task.done() and self._clock() < hard_deadline:
                try:
                    await asyncio.wait(
                        {task},
                        timeout=max(0.0, hard_deadline - self._clock()),
                    )
                    if not task.done():
                        break
                except asyncio.CancelledError:
                    cancelled = True
            if not task.done():
                task.add_done_callback(_consume_task_result)
                return ["isolation cleanup exceeded its hard deadline"]
        if task.cancelled():
            return ["isolation cleanup exceeded its hard deadline"]
        errors = task.result()
        if cancelled and not errors:
            errors.append("isolation cleanup was interrupted by cancellation")
        return errors

    async def _cleanup(self) -> list[str]:
        errors: list[str] = []
        errors.extend(await self._resume_source())

        if self._owned.child_id is not None:
            child_id = self._owned.child_id
            try:
                await self._remove_child(child_id, cleanup=True)
                self._journal.append(
                    "completion_check_disposed",
                    {
                        "attempt_id": self._request.attempt_id,
                        "child_id_sha256": _sha256_text(child_id),
                        "after_failure": True,
                    },
                )
            except CompletionIsolationError as exc:
                errors.append(exc.detail)

        if self._owned.inspector_id is not None:
            inspector_id = self._owned.inspector_id
            try:
                result = await self._run(
                    ("rm", "-f", inspector_id),
                    kind=IsolationFailureKind.CLEANUP,
                    timeout_sec=15,
                    cleanup=True,
                    allow_nonzero=True,
                )
                if result.return_code != 0 and not _is_missing_resource(result, "container"):
                    raise self._error(
                        IsolationFailureKind.CLEANUP,
                        _docker_failure_detail(("rm", "-f"), result),
                    )
                self._owned.inspector_id = None
                self._journal.append(
                    "completion_candidate_inspector_disposed",
                    {
                        "attempt_id": self._request.attempt_id,
                        "container_id_sha256": _sha256_text(inspector_id),
                    },
                )
            except CompletionIsolationError as exc:
                errors.append(exc.detail)

        image_selector = self._owned.image_id or self._owned.image_reference
        if image_selector is not None:
            try:
                result = await self._run(
                    ("image", "rm", image_selector),
                    kind=IsolationFailureKind.CLEANUP,
                    timeout_sec=20,
                    cleanup=True,
                    allow_nonzero=True,
                )
                if result.return_code != 0 and not _is_missing_resource(result, "image"):
                    raise self._error(
                        IsolationFailureKind.CLEANUP,
                        _docker_failure_detail(("image", "rm"), result),
                    )
                self._owned.snapshot_disposed = True
                self._journal.append(
                    "completion_snapshot_disposed",
                    {
                        "attempt_id": self._request.attempt_id,
                        "candidate_image_id": self._owned.image_id,
                    },
                )
            except CompletionIsolationError as exc:
                errors.append(exc.detail)

        return errors

    async def _resume_source(self) -> list[str]:
        if not self._owned.source_pause_attempted or self._owned.source_id is None:
            return []

        errors: list[str] = []
        source_id = self._owned.source_id
        pause_task = self._owned.pause_task
        if pause_task is not None:
            assert self._cleanup_deadline_monotonic is not None
            remaining = max(0.0, self._cleanup_deadline_monotonic - self._clock())
            done, _ = await asyncio.wait({pause_task}, timeout=remaining)
            if not done:
                pause_task.cancel()
                pause_task.add_done_callback(_consume_task_result)
                errors.append("the Docker pause command did not settle before cleanup")
            else:
                try:
                    pause_task.result()
                    self._owned.source_paused = True
                except BaseException as exc:
                    errors.append(f"the Docker pause command failed during cleanup: {exc}")
            self._owned.pause_task = None
        try:
            await self._run(
                ("unpause", source_id),
                kind=IsolationFailureKind.CLEANUP,
                timeout_sec=15,
                cleanup=True,
                allow_nonzero=True,
            )
            result = await self._run(
                (
                    "inspect",
                    "--format",
                    "{{json .State}}",
                    source_id,
                ),
                kind=IsolationFailureKind.CLEANUP,
                timeout_sec=10,
                cleanup=True,
            )
            state = json.loads(result.stdout or "")
            self._owned.source_resumed = bool(
                isinstance(state, dict)
                and state.get("Running") is True
                and state.get("Paused") is False
            )
            if not self._owned.source_resumed:
                errors.append("the live source did not resume after isolation")
            self._journal.append(
                "completion_source_resumed",
                {
                    "attempt_id": self._request.attempt_id,
                    "resumed": self._owned.source_resumed,
                },
            )
        except (CompletionIsolationError, json.JSONDecodeError) as exc:
            errors.append(
                exc.detail
                if isinstance(exc, CompletionIsolationError)
                else f"source resume inspection returned invalid JSON: {exc}"
            )
        return errors

    async def _run(
        self,
        args: Sequence[str],
        *,
        kind: IsolationFailureKind,
        timeout_sec: float,
        cleanup: bool = False,
        allow_nonzero: bool = False,
    ) -> DockerCliResult:
        normal_limit = _MAX_DOCKER_OPERATIONS - _RESERVED_CLEANUP_OPERATIONS
        if (not cleanup and self._operations >= normal_limit) or (
            cleanup and self._operations >= _MAX_DOCKER_OPERATIONS
        ):
            raise self._error(kind, "Docker isolation operation limit exceeded")

        effective_timeout = timeout_sec
        if cleanup:
            if self._cleanup_deadline_monotonic is None:
                raise self._error(kind, "cleanup deadline is not initialized")
            remaining = self._cleanup_deadline_monotonic - self._clock()
            if remaining <= 0:
                raise self._error(kind, "Docker isolation cleanup deadline exceeded")
            effective_timeout = min(timeout_sec, remaining)
        else:
            usable = self._request.deadline_monotonic - self._clock() - _CLEANUP_RESERVE_SEC
            if usable <= 0:
                raise self._error(
                    IsolationFailureKind.DEADLINE,
                    "completion isolation reached its cleanup reserve",
                )
            effective_timeout = min(timeout_sec, usable)

        self._operations += 1
        try:
            result = await self._cli.run(args, timeout_sec=max(0.1, effective_timeout))
        except TimeoutError as exc:
            failure_kind = (
                IsolationFailureKind.CLEANUP if cleanup else IsolationFailureKind.DEADLINE
            )
            raise self._error(
                failure_kind,
                f"Docker {' '.join(args[:2])} timed out",
            ) from exc
        except CompletionIsolationError:
            raise
        except Exception as exc:
            raise self._error(
                kind,
                f"Docker {' '.join(args[:2])} failed: {type(exc).__name__}: {exc}",
            ) from exc
        if result.return_code != 0 and not allow_nonzero:
            detail = (result.stderr or result.stdout or "").strip()
            if len(detail) > 1_000:
                detail = detail[:1_000] + "...[truncated]"
            raise self._error(
                kind,
                f"Docker {' '.join(args[:2])} exited {result.return_code}: {detail}",
            )
        return result

    def _error(
        self,
        kind: IsolationFailureKind,
        detail: str,
    ) -> CompletionIsolationError:
        return CompletionIsolationError(
            kind,
            detail,
            attempt_id=self._request.attempt_id,
        )


class _DockerCheckEnvironment(ShellEnvironment):
    def __init__(
        self,
        *,
        child_id: str,
        exec_user: str | None,
        run: Callable[..., Awaitable[DockerCliResult]],
    ) -> None:
        self._child_id = child_id
        self._exec_user = exec_user
        self._run = run
        self._used = False

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        timeout_sec: int | None = None,
    ) -> EnvironmentResult:
        if self._used:
            raise RuntimeError("an isolated check environment permits one exec")
        self._used = True
        args = ["exec"]
        if cwd is not None:
            args.extend(["--workdir", cwd])
        if self._exec_user is not None:
            args.extend(["--user", self._exec_user])
        args.extend([self._child_id, "bash", "-c", command])
        return await self._run(
            tuple(args),
            kind=IsolationFailureKind.INSPECTION,
            timeout_sec=float(timeout_sec or 120),
            allow_nonzero=True,
        )


def completion_isolation_for_harbor(
    *,
    journal: RunJournal,
    environment: BaseEnvironment,
    cli: DockerCli | None = None,
) -> CompletionIsolation:
    reasons: list[str] = []
    try:
        installed_harbor = version("harbor")
    except PackageNotFoundError:
        installed_harbor = "missing"
    if installed_harbor != _HARBOR_VERSION:
        reasons.append(f"Harbor {_HARBOR_VERSION} is required, found {installed_harbor}")
    if type(environment) is not DockerEnvironment:
        reasons.append("completion isolation requires Harbor's concrete local DockerEnvironment")
        project_name = "unsupported"
        exec_user = None
    else:
        project_name = _sanitize_project_name(environment.session_id)
        exec_user = str(environment.default_user) if environment.default_user is not None else None
        reasons.extend(
            docker_isolation_static_rejection_reasons(
                environment_dir=Path(environment.environment_dir),
                config=environment.task_env_config,
                extra_docker_compose_paths=environment.extra_docker_compose_paths,
                mounts=getattr(environment, "_mounts", ()),
            )
        )

    if reasons:
        return UnsupportedCompletionIsolation("; ".join(dict.fromkeys(reasons)))
    return DockerCompletionIsolation(
        target=DockerIsolationTarget(
            project_name=project_name,
            exec_user=exec_user,
        ),
        journal=journal,
        cli=cli,
    )


def docker_isolation_static_rejection_reasons(
    *,
    environment_dir: Path,
    config: EnvironmentConfig,
    extra_docker_compose_paths: Sequence[object] = (),
    mounts: Sequence[dict[str, Any]] = (),
) -> tuple[str, ...]:
    reasons: list[str] = []
    if getattr(config.os, "value", config.os) != "linux":
        reasons.append("completion isolation requires a Linux task container")
    if (environment_dir / "docker-compose.yaml").exists():
        reasons.append("task-authored Docker Compose is not supported")
    if extra_docker_compose_paths:
        reasons.append("extra Docker Compose overlays are not supported")
    if config.healthcheck is not None:
        reasons.append("task environment healthchecks are not supported")
    if (config.gpus or 0) > 0:
        reasons.append("GPU task environments are not supported")
    if config.tpu is not None:
        reasons.append("TPU task environments are not supported")
    for mount in mounts:
        destination = mount.get("target")
        if not isinstance(destination, str) or not _is_control_mount_target(destination):
            reasons.append(f"task-defined mount is not supported: {destination}")
    return tuple(dict.fromkeys(reasons))


async def _read_bounded(reader: asyncio.StreamReader) -> str:
    head_limit = _MAX_CLI_OUTPUT_BYTES * 2 // 3
    tail_limit = _MAX_CLI_OUTPUT_BYTES - head_limit
    head = bytearray()
    tail = bytearray()
    total = 0
    while chunk := await reader.read(64 * 1024):
        total += len(chunk)
        if len(head) < head_limit:
            take = min(len(chunk), head_limit - len(head))
            head.extend(chunk[:take])
            chunk = chunk[take:]
        if chunk:
            tail.extend(chunk)
            if len(tail) > tail_limit:
                del tail[: len(tail) - tail_limit]
    if total <= _MAX_CLI_OUTPUT_BYTES:
        data = bytes(head + tail)
    else:
        marker = f"\n...[{total - _MAX_CLI_OUTPUT_BYTES} bytes omitted]...\n".encode()
        data = bytes(head) + marker + bytes(tail)
    return data.decode("utf-8", errors="replace")


async def _terminate_process(process: asyncio.subprocess.Process) -> None:
    if process.returncode is not None:
        return
    process.terminate()
    try:
        async with asyncio.timeout(_PROCESS_TERMINATE_GRACE_SEC):
            await process.wait()
    except TimeoutError:
        process.kill()
        try:
            async with asyncio.timeout(_PROCESS_TERMINATE_GRACE_SEC):
                await process.wait()
        except TimeoutError:
            return


async def _collect_output_tasks(
    stdout_task: asyncio.Task[str],
    stderr_task: asyncio.Task[str],
) -> tuple[str, str]:
    tasks = (stdout_task, stderr_task)
    done, pending = await asyncio.wait(tasks, timeout=_OUTPUT_DRAIN_GRACE_SEC)
    for task in pending:
        task.cancel()
        task.add_done_callback(_consume_task_result)

    values: list[str] = []
    for task in tasks:
        if task not in done or task.cancelled():
            values.append("")
            continue
        try:
            values.append(task.result())
        except BaseException:
            values.append("")
    return values[0], values[1]


def _consume_task_result(task: asyncio.Task[Any]) -> None:
    with suppress(BaseException):
        task.result()


def _parse_diff(
    raw: str,
    *,
    error: Callable[[str], CompletionIsolationError],
) -> tuple[_DiffEntry, ...]:
    entries: set[_DiffEntry] = set()
    for line in raw.splitlines():
        if len(line) < 3 or line[0] not in {"A", "C", "D"} or line[1] != " ":
            raise error("Docker diff returned an unrecognized record")
        path = line[2:]
        if not path.startswith("/") or "\x00" in path:
            raise error("Docker diff returned an invalid path")
        entries.add(_DiffEntry(kind=line[0], path=path))
    return tuple(sorted(entries, key=lambda item: (item.path, item.kind)))


def _parse_canonical_diff(raw: str) -> tuple[_DiffEntry, ...]:
    def fail(detail: str) -> CompletionIsolationError:
        raise ValueError(detail)

    return _parse_diff(raw, error=fail)


def _resource_args(host: dict[str, Any]) -> tuple[str, ...]:
    args: list[str] = []
    memory = _nonnegative_int(host.get("Memory"), "HostConfig.Memory")
    memory_reservation = _nonnegative_int(
        host.get("MemoryReservation"),
        "HostConfig.MemoryReservation",
    )
    memory_swap = _integer(host.get("MemorySwap"), "HostConfig.MemorySwap")
    nano_cpus = _nonnegative_int(host.get("NanoCpus"), "HostConfig.NanoCpus")
    cpu_shares = _nonnegative_int(host.get("CpuShares"), "HostConfig.CpuShares")
    cpu_period = _nonnegative_int(host.get("CpuPeriod"), "HostConfig.CpuPeriod")
    cpu_quota = _integer(host.get("CpuQuota"), "HostConfig.CpuQuota")
    cpuset = host.get("CpusetCpus")
    pids_limit = host.get("PidsLimit")

    if memory:
        args.extend(["--memory", str(memory)])
    if memory_reservation:
        args.extend(["--memory-reservation", str(memory_reservation)])
    if memory_swap not in {0, -1}:
        args.extend(["--memory-swap", str(memory_swap)])
    elif memory_swap == -1:
        args.extend(["--memory-swap", "-1"])
    if nano_cpus:
        cpus = Decimal(nano_cpus) / Decimal(1_000_000_000)
        args.extend(["--cpus", format(cpus.normalize(), "f")])
    if cpu_shares:
        args.extend(["--cpu-shares", str(cpu_shares)])
    if cpu_period:
        args.extend(["--cpu-period", str(cpu_period)])
    if cpu_quota:
        args.extend(["--cpu-quota", str(cpu_quota)])
    if cpuset:
        if not isinstance(cpuset, str):
            raise ValueError("HostConfig.CpusetCpus is not a string")
        args.extend(["--cpuset-cpus", cpuset])
    if pids_limit is not None:
        if not isinstance(pids_limit, int):
            raise ValueError("HostConfig.PidsLimit is not an integer")
        args.extend(["--pids-limit", str(pids_limit)])
    return tuple(args)


def _parse_commit_image_id(stdout: str) -> str | None:
    lines = [line for line in stdout.splitlines() if line]
    if lines and lines[0] == _COMMIT_PAUSE_DEPRECATION:
        lines.pop(0)
    if len(lines) != 1 or not re.fullmatch(r"sha256:[0-9a-f]{64}", lines[0]):
        return None
    return lines[0]


def _is_missing_resource(result: DockerCliResult, kind: str) -> bool:
    detail = result.stderr or result.stdout or ""
    return f"No such {kind}" in detail


def _docker_failure_detail(args: Sequence[str], result: DockerCliResult) -> str:
    detail = (result.stderr or result.stdout or "").strip()
    if len(detail) > 1_000:
        detail = detail[:1_000] + "...[truncated]"
    return f"Docker {' '.join(args[:2])} exited {result.return_code}: {detail}"


def _integer(value: Any, field: str) -> int:
    if value is None:
        return 0
    if not isinstance(value, int):
        raise ValueError(f"{field} is not an integer")
    return value


def _nonnegative_int(value: Any, field: str) -> int:
    parsed = _integer(value, field)
    if parsed < 0:
        raise ValueError(f"{field} is negative")
    return parsed


def _bounded_paths(paths: Sequence[str]) -> tuple[tuple[str, ...], int]:
    bounded = tuple(paths[:_MAX_DELTA_PATHS_PER_KIND])
    return bounded, len(paths) - len(bounded)


def _is_control_mount_target(path: str) -> bool:
    return any(path == root or path.startswith(f"{root}/") for root in _CONTROL_MOUNT_TARGETS)


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} is not an object")
    return value


def _candidate_content_digest(layers: Sequence[str]) -> str:
    digest = hashlib.sha256(_CANDIDATE_DIGEST_DOMAIN)
    digest.update(len(layers).to_bytes(8, "big"))
    for layer in layers:
        encoded = layer.encode()
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _sha256_json(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _sanitize_project_name(value: str) -> str:
    sanitized = value.lower()
    if not re.match(r"^[a-z0-9]", sanitized):
        sanitized = "0" + sanitized
    return re.sub(r"[^a-z0-9_-]", "-", sanitized)
