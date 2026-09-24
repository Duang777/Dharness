from __future__ import annotations

from collections import Counter

from evidence_harness.policy import PolicyViolation, normalize_command, validate_check
from evidence_harness.protocol import (
    CommandReceipt,
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

        normalized_requirements = [item.requirement.casefold() for item in coverage]
        if len(normalized_requirements) != len(set(normalized_requirements)):
            reasons.append("requirement coverage contains duplicate requirements")

        for check in checks:
            try:
                validate_check(check, self._options.max_command_timeout_sec)
            except PolicyViolation as exc:
                reasons.append(str(exc))
        return tuple(reasons)

    def decide(
        self,
        *,
        work_epoch: int,
        checks: tuple[CommandReceipt, ...],
        coverage: tuple[RequirementCoverage, ...],
        prior_rejections: tuple[str, ...] = (),
    ) -> VerificationReceipt:
        reasons = list(prior_rejections)
        if not checks:
            reasons.append("no verification commands were executed")
        if any(item.work_epoch != work_epoch for item in checks):
            reasons.append("verification evidence is stale")
        failed_ids = [item.command_id for item in checks if not item.succeeded]
        if failed_ids:
            reasons.append(f"verification commands failed: {failed_ids}")

        return VerificationReceipt(
            work_epoch=work_epoch,
            checks=checks,
            coverage=coverage,
            accepted=not reasons,
            rejection_reasons=tuple(reasons),
        )
