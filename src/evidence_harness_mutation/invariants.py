from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from evidence_harness.protocol import (
    CheckIsolationEvidence,
    CommandReceipt,
    CompletionIsolationEvidence,
    ReviewDecision,
    VerificationReceipt,
)
from evidence_harness_mutation.attempts import (
    CompletionAttempt,
    parse_event,
    project_completion_attempts,
)
from evidence_harness_mutation.model import (
    AuditReport,
    InvariantId,
    InvariantResult,
    InvariantStatus,
    InvariantViolation,
    OfflineTrace,
    RecordedEvent,
)


class _EventPayload(BaseModel):
    model_config = ConfigDict(extra="ignore")


class _IsolationStarted(_EventPayload):
    attempt_id: int = Field(ge=1)
    work_epoch: int = Field(ge=0)
    check_ids: tuple[str, ...]


class _CandidateCommitted(_EventPayload):
    attempt_id: int = Field(ge=1)
    candidate_image_id: str = Field(min_length=1)


class _CheckStarted(_EventPayload):
    attempt_id: int = Field(ge=1)
    check_id: str = Field(min_length=1)
    ordinal: int = Field(ge=1)


class _CompletionReviewError(_EventPayload):
    error_type: str = Field(min_length=1)
    error: str


def audit_completion_trace(trace: OfflineTrace) -> AuditReport:
    attempts = tuple(
        attempt for attempt in project_completion_attempts(trace.events) if attempt.verified
    )
    evaluators = (
        (InvariantId.I1, _evaluate_i1),
        (InvariantId.I2, _evaluate_i2),
        (
            InvariantId.I3,
            lambda attempt: _evaluate_i3(
                attempt,
                review_enabled=trace.completion_review_enabled,
            ),
        ),
        (InvariantId.I4, _evaluate_i4),
    )
    results: list[InvariantResult] = []
    for invariant, evaluator in evaluators:
        if not attempts:
            results.append(
                InvariantResult(
                    invariant=invariant,
                    status=InvariantStatus.NOT_APPLICABLE,
                )
            )
            continue
        violations = tuple(
            violation for attempt in attempts if (violation := evaluator(attempt)) is not None
        )
        results.append(
            InvariantResult(
                invariant=invariant,
                status=InvariantStatus.FAIL if violations else InvariantStatus.PASS,
                violations=violations,
            )
        )
    return AuditReport(
        source=trace.source,
        through_line=trace.through_line,
        verified_attempts=len(attempts),
        results=tuple(results),
    )


def _evaluate_i1(attempt: CompletionAttempt) -> InvariantViolation | None:
    details: list[str] = []
    related = _related_lines(attempt, "completion_isolation_started", "verification_receipt")
    starts = attempt.of_type("completion_isolation_started")
    verification = _accepted_verification(attempt, details)
    if len(starts) != 1:
        details.append("verified attempt must contain exactly one isolation start")
    if verification is not None and len(starts) == 1:
        current_epoch = parse_event(starts[0], _IsolationStarted).work_epoch
        evidence_epochs = [verification.work_epoch]
        evidence_epochs.extend(
            parse_event(event, CommandReceipt).work_epoch
            for event in attempt.of_type("command_receipt")
        )
        evidence_epochs.extend(receipt.work_epoch for receipt in verification.checks)
        if verification.isolation is not None:
            evidence_epochs.append(verification.isolation.work_epoch)
        if any(epoch != current_epoch for epoch in evidence_epochs):
            details.append(
                f"completion evidence epochs {evidence_epochs} do not all match "
                f"current work epoch {current_epoch}"
            )
    return _violation(InvariantId.I1, attempt, details, related)


def _evaluate_i2(attempt: CompletionAttempt) -> InvariantViolation | None:
    details: list[str] = []
    verification = _accepted_verification(attempt, details)
    receipt_events = attempt.of_type("command_receipt")
    receipts = tuple(parse_event(event, CommandReceipt) for event in receipt_events)
    expected_ids = tuple(check.id for check in attempt.decision.checks)
    executed_ids = tuple(receipt.command_id for receipt in receipts)
    if executed_ids != expected_ids:
        details.append(
            f"executed check ids {executed_ids} do not match proposed order {expected_ids}"
        )
    failed_ids = tuple(receipt.command_id for receipt in receipts if not receipt.succeeded)
    if failed_ids:
        details.append(f"executed checks failed: {failed_ids}")
    if verification is not None:
        embedded_ids = tuple(receipt.command_id for receipt in verification.checks)
        if embedded_ids != expected_ids:
            details.append(
                f"verification check ids {embedded_ids} do not match proposed order {expected_ids}"
            )
        if verification.checks != receipts:
            details.append("verification receipt does not contain the recorded check receipts")
        if verification.coverage != attempt.decision.coverage:
            details.append("verification coverage does not match the completion proposal")
        embedded_failures = tuple(
            receipt.command_id for receipt in verification.checks if not receipt.succeeded
        )
        if embedded_failures:
            details.append(f"verification contains failed checks: {embedded_failures}")
    related = _related_lines(attempt, "command_receipt", "verification_receipt")
    return _violation(InvariantId.I2, attempt, details, related)


def _evaluate_i3(
    attempt: CompletionAttempt,
    *,
    review_enabled: bool,
) -> InvariantViolation | None:
    if not review_enabled:
        return None

    details: list[str] = []
    verification_events = attempt.of_type("verification_receipt")
    verification = _accepted_verification(attempt, details)
    review_events = attempt.of_type("completion_review")
    error_events = attempt.of_type("completion_review_error")
    tuple(parse_event(event, _CompletionReviewError) for event in error_events)
    if len(review_events) != 1:
        details.append("verified attempt must contain exactly one completion review")
    if error_events:
        details.append("verified attempt contains a completion review error")
    if len(review_events) == 1:
        review = parse_event(review_events[0], ReviewDecision)
        if review.verdict != "accept":
            details.append("completion review did not accept the attempt")
        last_receipt_line = max(
            (event.line for event in attempt.of_type("command_receipt")),
            default=attempt.proposal_event.line,
        )
        verification_line = verification_events[0].line if len(verification_events) == 1 else None
        if verification_line is None:
            verification_line = attempt.terminal_line
        assert verification_line is not None
        if not last_receipt_line < review_events[0].line < verification_line:
            details.append("completion review is not between check execution and verification")
    if verification is not None and (
        verification.semantic_assessment is None or not verification.semantic_assessment.accepted
    ):
        details.append("verification receipt has no accepted semantic assessment")
    related = _related_lines(
        attempt,
        "command_receipt",
        "completion_review",
        "completion_review_error",
        "verification_receipt",
    )
    return _violation(InvariantId.I3, attempt, details, related)


def _evaluate_i4(attempt: CompletionAttempt) -> InvariantViolation | None:
    details: list[str] = []
    verification = _accepted_verification(attempt, details)
    starts = attempt.of_type("completion_isolation_started")
    commits = attempt.of_type("completion_candidate_committed")
    started_checks = attempt.of_type("completion_check_started")
    isolated_checks = attempt.of_type("completion_check_isolated")
    if len(starts) != 1:
        details.append("verified attempt must contain exactly one isolation start")
    if len(commits) != 1:
        details.append("verified attempt must contain exactly one candidate commit")
    if verification is None or verification.isolation is None:
        details.append("verified attempt has no completion isolation evidence")
    if len(starts) == 1 and len(commits) == 1 and verification is not None:
        start = parse_event(starts[0], _IsolationStarted)
        commit = parse_event(commits[0], _CandidateCommitted)
        isolation = verification.isolation
        if isolation is not None:
            attempt_ids = [start.attempt_id, commit.attempt_id, isolation.attempt_id]
            attempt_ids.extend(
                parse_event(event, _CheckStarted).attempt_id for event in started_checks
            )
            if any(attempt_id != start.attempt_id for attempt_id in attempt_ids):
                details.append(f"completion evidence crosses attempt ids: {attempt_ids}")
            candidate_ids = [commit.candidate_image_id, isolation.candidate_image_id]
            candidate_ids.extend(record.started_from_image_id for record in isolation.checks)
            candidate_ids.extend(
                parse_event(event, CheckIsolationEvidence).started_from_image_id
                for event in isolated_checks
            )
            if any(candidate_id != commit.candidate_image_id for candidate_id in candidate_ids):
                details.append("completion evidence crosses candidate images")
            _compare_isolated_bindings(
                verification=isolation,
                recorded_events=isolated_checks,
                receipts=tuple(
                    parse_event(event, CommandReceipt)
                    for event in attempt.of_type("command_receipt")
                ),
                details=details,
            )
            expected_ids = tuple(check.id for check in attempt.decision.checks)
            if start.check_ids != expected_ids:
                details.append("isolation start does not match the proposed check order")
            if tuple(record.check_id for record in isolation.checks) != expected_ids:
                details.append("isolation evidence does not match the proposed check order")
            started = tuple(
                (
                    parse_event(event, _CheckStarted).ordinal,
                    parse_event(event, _CheckStarted).check_id,
                )
                for event in started_checks
            )
            if started != tuple(enumerate(expected_ids, start=1)):
                details.append("completion check lifecycle does not match the proposed order")
    related = _related_lines(
        attempt,
        "completion_isolation_started",
        "completion_candidate_committed",
        "completion_check_started",
        "completion_check_isolated",
        "verification_receipt",
    )
    return _violation(InvariantId.I4, attempt, details, related)


def _accepted_verification(
    attempt: CompletionAttempt,
    details: list[str],
) -> VerificationReceipt | None:
    events = attempt.of_type("verification_receipt")
    receipts = tuple(parse_event(event, VerificationReceipt) for event in events)
    accepted = tuple(receipt for receipt in receipts if receipt.accepted)
    if len(accepted) != 1:
        details.append(
            "verified attempt must contain one accepted verification receipt, "
            f"found {len(accepted)}"
        )
        return None
    if len(receipts) != 1:
        details.append(
            f"verified attempt must contain exactly one verification receipt, found {len(receipts)}"
        )
    return accepted[0]


def _compare_isolated_bindings(
    *,
    verification: CompletionIsolationEvidence,
    recorded_events: tuple[RecordedEvent, ...],
    receipts: tuple[CommandReceipt, ...],
    details: list[str],
) -> None:
    recorded = tuple(parse_event(event, CheckIsolationEvidence) for event in recorded_events)
    if len(recorded) != len(verification.checks):
        details.append("recorded and embedded isolation check counts differ")
        return
    for raw, embedded in zip(recorded, verification.checks, strict=True):
        raw_binding = (
            raw.check_id,
            raw.receipt_sequence,
            raw.receipt_observation_sha256,
            raw.child_id_sha256,
            raw.started_from_image_id,
        )
        embedded_binding = (
            embedded.check_id,
            embedded.receipt_sequence,
            embedded.receipt_observation_sha256,
            embedded.child_id_sha256,
            embedded.started_from_image_id,
        )
        if raw_binding != embedded_binding:
            details.append(f"isolation binding differs for check {embedded.check_id}")
    if len(receipts) != len(verification.checks):
        details.append("receipt and isolation check counts differ")
        return
    for receipt, isolated in zip(receipts, verification.checks, strict=True):
        receipt_binding = (
            receipt.command_id,
            receipt.sequence,
            receipt.observation_fingerprint,
        )
        isolation_binding = (
            isolated.check_id,
            isolated.receipt_sequence,
            isolated.receipt_observation_sha256,
        )
        if receipt_binding != isolation_binding:
            details.append(f"isolation does not bind recorded receipt {receipt.command_id}")


def _related_lines(attempt: CompletionAttempt, *event_types: str) -> tuple[int, ...]:
    selected = {attempt.proposal_event.line, attempt.terminal_line}
    selected.update(event.line for event in attempt.events if event.event_type in event_types)
    return tuple(sorted(line for line in selected if line is not None))


def _violation(
    invariant: InvariantId,
    attempt: CompletionAttempt,
    details: list[str],
    related_lines: tuple[int, ...],
) -> InvariantViolation | None:
    if not details:
        return None
    assert attempt.terminal_line is not None
    return InvariantViolation(
        invariant=invariant,
        terminal_line=attempt.terminal_line,
        attempt_ordinal=attempt.ordinal,
        related_lines=related_lines,
        details=tuple(dict.fromkeys(details)),
    )
