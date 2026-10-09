from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from copy import deepcopy

from evidence_harness.harness_adapter import (
    CandidateIdentity,
    CandidateSnapshot,
    CompletionTransactionView,
    HarnessAdapter,
    IsolatedCheckRun,
)
from evidence_harness.protocol import (
    CheckIsolationEvidence,
    CommandMode,
    CommandReceipt,
    CompletionIsolationEvidence,
    FailureKind,
    FilesystemDelta,
    IsolationCost,
    OutputExcerpt,
    SourceAttestation,
)

StubCheckRunner = Callable[[dict[str, bytes]], bool]


class StubHarnessAdapter(HarnessAdapter):
    def __init__(
        self,
        *,
        view: CompletionTransactionView,
        candidate_files: Mapping[str, bytes],
        check_runners: Mapping[str, StubCheckRunner],
    ) -> None:
        self._view = deepcopy(view)
        self._candidate_files = dict(candidate_files)
        self._check_runners = dict(check_runners)
        self._has_run = False
        self._isolated_run_count = 0

    @property
    def view(self) -> CompletionTransactionView:
        return deepcopy(self._view)

    @property
    def isolated_run_count(self) -> int:
        return self._isolated_run_count

    async def run_checks_isolated(self) -> IsolatedCheckRun:
        if self._has_run:
            raise RuntimeError("a completion adapter can run isolated checks only once")
        self._has_run = True
        self._isolated_run_count += 1

        state = self._view.state
        candidate_digest = _files_digest(self._candidate_files)
        identity = CandidateIdentity(
            algorithm="sha256",
            value=f"sha256:{candidate_digest}",
        )
        snapshot = CandidateSnapshot(
            identity=identity,
            attempt_id=state.next_completion_attempt,
            work_epoch=state.work_epoch,
        )
        receipts: list[CommandReceipt] = []
        isolation_records: list[CheckIsolationEvidence] = []
        sequence = max(
            (receipt.sequence for receipt in self._view.trace.receipts),
            default=0,
        )

        for index, check in enumerate(self._view.proposal.checks, start=1):
            sequence += 1
            private_files = dict(self._candidate_files)
            runner = self._check_runners.get(check.id)
            failure: FailureKind | None = None
            stderr = ""
            try:
                if runner is None:
                    raise KeyError(f"no in-memory runner for check '{check.id}'")
                passed = runner(private_files)
                return_code = 0 if passed else 1
                if not passed:
                    failure = FailureKind.NONZERO
                    stderr = "in-memory check returned false"
            except Exception as exc:
                return_code = None
                failure = FailureKind.TRANSPORT
                stderr = f"{type(exc).__name__}: {exc}"

            stdout_excerpt = _excerpt("")
            stderr_excerpt = _excerpt(stderr)
            command_fingerprint = _sha256_text(f"{check.script}\0{check.cwd or ''}")
            observation_fingerprint = _sha256_text(
                "\0".join(
                    (
                        command_fingerprint,
                        str(return_code),
                        failure.value if failure is not None else "",
                        stdout_excerpt.sha256,
                        stderr_excerpt.sha256,
                    )
                )
            )
            receipt = CommandReceipt(
                sequence=sequence,
                command_id=check.id,
                script=check.script,
                purpose=check.proves,
                cwd=check.cwd,
                mode=CommandMode.OBSERVE,
                work_epoch=state.work_epoch,
                return_code=return_code,
                failure=failure,
                duration_sec=0,
                stdout=stdout_excerpt,
                stderr=stderr_excerpt,
                command_fingerprint=command_fingerprint,
                observation_fingerprint=observation_fingerprint,
            )
            receipts.append(receipt)
            isolation_records.append(
                CheckIsolationEvidence(
                    check_id=check.id,
                    receipt_sequence=receipt.sequence,
                    receipt_observation_sha256=receipt.observation_fingerprint,
                    child_id_sha256=_sha256_text(
                        f"{identity.value}\0{snapshot.attempt_id}\0{index}\0{check.id}"
                    ),
                    started_from_image_id=identity.value,
                    delta=_filesystem_delta(self._candidate_files, private_files),
                    disposed=True,
                )
            )
            if not receipt.succeeded:
                break

        isolation = CompletionIsolationEvidence(
            backend="memory-snapshot-v1",
            attempt_id=snapshot.attempt_id,
            work_epoch=snapshot.work_epoch,
            candidate_image_id=identity.value,
            environment_identity_sha256=candidate_digest,
            checks=tuple(isolation_records),
            source=SourceAttestation(
                container_id_sha256=candidate_digest,
                diff_sha256_before=candidate_digest,
                diff_sha256_after=candidate_digest,
                remained_paused=True,
                resumed=True,
            ),
            snapshot_image_disposed=True,
            cost=IsolationCost(
                host_operations=len(isolation_records),
                child_count=len(isolation_records),
                duration_sec=0,
            ),
        )
        return IsolatedCheckRun(
            snapshot=snapshot,
            receipts=tuple(receipts),
            isolation=isolation,
        )


def _filesystem_delta(
    before: Mapping[str, bytes],
    after: Mapping[str, bytes],
) -> FilesystemDelta:
    before_paths = set(before)
    after_paths = set(after)
    added = tuple(sorted(after_paths - before_paths))
    deleted = tuple(sorted(before_paths - after_paths))
    modified = tuple(
        sorted(path for path in before_paths & after_paths if before[path] != after[path])
    )
    digest_input = "\0".join((*added, *modified, *deleted))
    return FilesystemDelta(
        sha256=_sha256_text(digest_input),
        added=added,
        modified=modified,
        deleted=deleted,
    )


def _files_digest(files: Mapping[str, bytes]) -> str:
    digest = hashlib.sha256()
    for path in sorted(files):
        encoded_path = path.encode()
        content = files[path]
        digest.update(len(encoded_path).to_bytes(8, "big"))
        digest.update(encoded_path)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _excerpt(value: str) -> OutputExcerpt:
    encoded = value.encode()
    return OutputExcerpt(
        head=value,
        tail="",
        total_bytes=len(encoded),
        omitted_bytes=0,
        sha256=hashlib.sha256(encoded).hexdigest(),
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
