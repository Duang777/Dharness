from __future__ import annotations

from collections import Counter

from evidence_harness.completion_contract import CompletionContract
from evidence_harness.policy import PolicyViolation, normalize_command, validate_check
from evidence_harness.protocol import (
    CommandReceipt,
    CompletionIsolationEvidence,
    LoopOptions,
    RequirementCoverage,
    VerificationCheck,
    VerificationReceipt,
)


class EvidenceGate:
    def __init__(self, options: LoopOptions) -> None:
        self._options = options

    def validate_proposal(
        self,
        checks: tuple[VerificationCheck, ...],
        coverage: tuple[RequirementCoverage, ...],
        *,
        contract: CompletionContract | None = None,
        isolated: bool = False,
    ) -> tuple[str, ...]:
        reasons: list[str] = []
        check_ids = {check.id for check in checks}
        duplicate_scripts = [
            script
            for script, count in Counter(normalize_command(item.script) for item in checks).items()
            if count > 1
        ]
        if duplicate_scripts:
            reasons.append("completion checks contain duplicate commands")

        covered_ids = {check_id for item in coverage for check_id in item.check_ids}
        unused_ids = check_ids - covered_ids
        if unused_ids:
            reasons.append(f"checks are not mapped to a requirement: {sorted(unused_ids)}")

        requirement_ids = [item.requirement for item in coverage]
        normalized_requirements = [item.casefold() for item in requirement_ids]
        if len(normalized_requirements) != len(set(normalized_requirements)):
            reasons.append("requirement coverage contains duplicate requirements")

        if contract is not None:
            contract_ids = {item.id for item in contract.e_req}
            covered_requirements = set(requirement_ids)
            missing = contract_ids - covered_requirements
            unknown = covered_requirements - contract_ids
            if missing:
                reasons.append(f"contract requirements are not covered: {sorted(missing)}")
            if unknown:
                reasons.append(
                    f"coverage references unknown contract requirements: {sorted(unknown)}"
                )

            checks_by_id = {check.id: check for check in checks}
            requirements_by_id = {item.id: item for item in contract.e_req}
            for item in coverage:
                requirement = requirements_by_id.get(item.requirement)
                if requirement is None:
                    continue
                allowed_kinds = set(requirement.evidence_kinds)
                for check_id in item.check_ids:
                    check = checks_by_id.get(check_id)
                    if check is not None and check.kind not in allowed_kinds:
                        reasons.append(
                            f"check '{check.id}' has kind '{check.kind}'; "
                            f"requirement '{requirement.id}' allows "
                            f"{sorted(kind.value for kind in allowed_kinds)}"
                        )

        for check in checks:
            try:
                validate_check(
                    check,
                    self._options.max_command_timeout_sec,
                    allow_potential_writes=isolated,
                )
            except PolicyViolation as exc:
                reasons.append(str(exc))
        return tuple(reasons)

    def decide(
        self,
        *,
        contract: CompletionContract | None = None,
        work_epoch: int,
        checks: tuple[CommandReceipt, ...],
        proposed_checks: tuple[VerificationCheck, ...] = (),
        coverage: tuple[RequirementCoverage, ...],
        expected_check_ids: tuple[str, ...] = (),
        attempt_id: int | None = None,
        isolation: CompletionIsolationEvidence | None = None,
        require_isolation: bool = False,
        prior_rejections: tuple[str, ...] = (),
    ) -> VerificationReceipt:
        reasons = list(prior_rejections)
        if contract is not None:
            if not proposed_checks:
                reasons.append("proposed verification checks are missing")
            else:
                reasons.extend(
                    self.validate_proposal(
                        proposed_checks,
                        coverage,
                        contract=contract,
                        isolated=require_isolation,
                    )
                )
        if not checks:
            reasons.append("no verification commands were executed")
        if any(item.work_epoch != work_epoch for item in checks):
            reasons.append("verification evidence is stale")
        if attempt_id is not None and any(item.attempt_id != attempt_id for item in checks):
            reasons.append("verification receipt attempt does not match")
        if expected_check_ids and tuple(item.command_id for item in checks) != expected_check_ids:
            reasons.append("not all proposed verification commands were executed in order")
        if proposed_checks and len(proposed_checks) == len(checks):
            for proposed, receipt in zip(proposed_checks, checks, strict=True):
                if (
                    proposed.id != receipt.command_id
                    or proposed.script != receipt.script
                    or proposed.proves != receipt.purpose
                    or proposed.cwd != receipt.cwd
                ):
                    reasons.append(
                        f"verification receipt does not match proposed check: {proposed.id}"
                    )
        failed_ids = [item.command_id for item in checks if not item.succeeded]
        if failed_ids:
            reasons.append(f"verification commands failed: {failed_ids}")
        if require_isolation:
            reasons.extend(
                _isolation_rejections(
                    isolation=isolation,
                    attempt_id=attempt_id,
                    work_epoch=work_epoch,
                    checks=checks,
                )
            )
        reasons = list(dict.fromkeys(reasons))

        return VerificationReceipt(
            work_epoch=work_epoch,
            checks=checks,
            coverage=coverage,
            isolation=isolation,
            accepted=not reasons,
            rejection_reasons=tuple(reasons),
        )


def _isolation_rejections(
    *,
    isolation: CompletionIsolationEvidence | None,
    attempt_id: int | None,
    work_epoch: int,
    checks: tuple[CommandReceipt, ...],
) -> tuple[str, ...]:
    if isolation is None:
        return ("completion isolation evidence is missing",)

    reasons: list[str] = []
    if attempt_id is None or isolation.attempt_id != attempt_id:
        reasons.append("completion isolation attempt does not match")
    if isolation.work_epoch != work_epoch:
        reasons.append("completion isolation evidence is stale")
    if len(isolation.checks) != len(checks):
        reasons.append("completion isolation records do not match executed checks")

    isolated_by_id = {item.check_id: item for item in isolation.checks}
    if len(isolated_by_id) != len(isolation.checks):
        reasons.append("completion isolation records contain duplicate check ids")
    child_ids = [item.child_id_sha256 for item in isolation.checks]
    if len(child_ids) != len(set(child_ids)):
        reasons.append("completion checks reused an isolated child")

    for receipt in checks:
        record = isolated_by_id.get(receipt.command_id)
        if record is None:
            continue
        if record.receipt_sequence != receipt.sequence:
            reasons.append(f"completion isolation sequence mismatch: {receipt.command_id}")
        if record.receipt_observation_sha256 != receipt.observation_fingerprint:
            reasons.append(f"completion isolation observation mismatch: {receipt.command_id}")
        if record.started_from_image_id != isolation.candidate_image_id:
            reasons.append(f"completion check used the wrong candidate image: {receipt.command_id}")
        if not record.disposed:
            reasons.append(f"completion child was not disposed: {receipt.command_id}")
        if record.delta.modified:
            reasons.append(
                f"completion check modified pre-existing paths: "
                f"{receipt.command_id} {list(record.delta.modified)}"
            )
        if record.delta.deleted:
            reasons.append(
                f"completion check deleted pre-existing paths: "
                f"{receipt.command_id} {list(record.delta.deleted)}"
            )

    if isolation.cost.child_count != len(isolation.checks):
        reasons.append("completion isolation child count does not match")
    if not isolation.source.unchanged:
        reasons.append("live candidate changed during completion isolation")
    if not isolation.source.remained_paused:
        reasons.append("live candidate did not remain paused during completion isolation")
    if not isolation.source.resumed:
        reasons.append("live candidate was not resumed after completion isolation")
    if not isolation.snapshot_image_disposed:
        reasons.append("completion snapshot image was not disposed")
    return tuple(reasons)
