import hashlib

from evidence_harness.completion_control import ReviewGate
from evidence_harness.evidence import EvidenceGate
from evidence_harness.protocol import (
    CheckIsolationEvidence,
    CommandMode,
    CommandReceipt,
    CompletionIsolationEvidence,
    FilesystemDelta,
    IsolationCost,
    LoopOptions,
    OutputExcerpt,
    RequirementCoverage,
    SemanticAssessment,
    SourceAttestation,
)

_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def _successful_receipt() -> CommandReceipt:
    empty = OutputExcerpt(
        head="",
        tail="",
        total_bytes=0,
        omitted_bytes=0,
        sha256="0" * 64,
    )
    return CommandReceipt(
        sequence=1,
        command_id="check-answer",
        script="test -s answer.txt",
        purpose="answer.txt is non-empty",
        cwd="/workspace",
        mode=CommandMode.OBSERVE,
        work_epoch=1,
        return_code=0,
        duration_sec=0.1,
        stdout=empty,
        stderr=empty,
        command_fingerprint="1" * 64,
        observation_fingerprint="2" * 64,
    )


def _coverage() -> tuple[RequirementCoverage, ...]:
    return (
        RequirementCoverage(
            requirement="create a non-empty answer.txt",
            check_ids=("check-answer",),
        ),
    )


def _isolation() -> CompletionIsolationEvidence:
    receipt = _successful_receipt()
    return CompletionIsolationEvidence(
        backend="test-isolation-v1",
        attempt_id=1,
        work_epoch=1,
        candidate_image_id="sha256:candidate",
        environment_identity_sha256="3" * 64,
        checks=(
            CheckIsolationEvidence(
                check_id=receipt.command_id,
                receipt_sequence=receipt.sequence,
                receipt_observation_sha256=receipt.observation_fingerprint,
                child_id_sha256="4" * 64,
                started_from_image_id="sha256:candidate",
                delta=FilesystemDelta(sha256=_EMPTY_SHA256),
                disposed=True,
            ),
        ),
        source=SourceAttestation(
            container_id_sha256="5" * 64,
            diff_sha256_before=_EMPTY_SHA256,
            diff_sha256_after=_EMPTY_SHA256,
            remained_paused=True,
            resumed=True,
        ),
        snapshot_image_disposed=True,
        cost=IsolationCost(host_operations=1, child_count=1, duration_sec=0),
    )


def test_required_semantic_review_cannot_be_missing() -> None:
    result = ReviewGate().evaluate(
        required=True,
        assessment=None,
    )

    assert result.accepted is False
    assert result.reasons == ("completion semantic review was not accepted",)


def test_semantic_rejection_is_part_of_verification_evidence() -> None:
    assessment = SemanticAssessment(
        accepted=False,
        rationale="the check omits the required value",
        findings=("answer.txt content is not proven",),
    )

    result = ReviewGate().evaluate(
        required=True,
        assessment=assessment,
    )

    assert result.accepted is False
    assert result.reasons == (
        "the check omits the required value",
        "answer.txt content is not proven",
    )


def test_semantic_acceptance_completes_verification_evidence() -> None:
    assessment = SemanticAssessment(
        accepted=True,
        rationale="the receipt proves the required value",
    )

    result = ReviewGate().evaluate(
        required=True,
        assessment=assessment,
    )

    assert result.accepted is True
    assert result.reasons == ()


def test_explicit_review_opt_out_keeps_mechanical_mode() -> None:
    result = ReviewGate().evaluate(
        required=False,
        assessment=None,
    )

    assert result.accepted is True
    assert result.reasons == ()


def test_isolation_evidence_accepts_only_a_fully_bound_attempt() -> None:
    receipt = _successful_receipt()

    evidence = EvidenceGate(LoopOptions(enable_completion_review=False)).decide(
        work_epoch=1,
        checks=(receipt,),
        coverage=_coverage(),
        expected_check_ids=(receipt.command_id,),
        attempt_id=1,
        isolation=_isolation(),
        require_isolation=True,
    )

    assert evidence.accepted is True
    assert evidence.isolation == _isolation()


def test_isolation_evidence_rejects_missing_or_mismatched_bindings() -> None:
    receipt = _successful_receipt()
    gate = EvidenceGate(LoopOptions(enable_completion_review=False))
    missing = gate.decide(
        work_epoch=1,
        checks=(receipt,),
        coverage=_coverage(),
        expected_check_ids=(receipt.command_id,),
        attempt_id=1,
        require_isolation=True,
    )

    assert missing.rejection_reasons == ("completion isolation evidence is missing",)

    base = _isolation()
    mismatches = (
        (
            base.model_copy(update={"attempt_id": 2}),
            "completion isolation attempt does not match",
        ),
        (
            base.model_copy(update={"work_epoch": 2}),
            "completion isolation evidence is stale",
        ),
        (
            base.model_copy(
                update={"checks": (base.checks[0].model_copy(update={"receipt_sequence": 2}),)}
            ),
            "completion isolation sequence mismatch",
        ),
        (
            base.model_copy(
                update={
                    "checks": (
                        base.checks[0].model_copy(update={"receipt_observation_sha256": "9" * 64}),
                    )
                }
            ),
            "completion isolation observation mismatch",
        ),
        (
            base.model_copy(
                update={
                    "checks": (
                        base.checks[0].model_copy(
                            update={
                                "delta": FilesystemDelta(
                                    sha256="8" * 64,
                                    modified=("/workspace/answer.txt",),
                                )
                            }
                        ),
                    )
                }
            ),
            "completion check modified pre-existing paths",
        ),
        (
            base.model_copy(
                update={"source": base.source.model_copy(update={"diff_sha256_after": "7" * 64})}
            ),
            "live candidate changed during completion isolation",
        ),
        (
            base.model_copy(update={"snapshot_image_disposed": False}),
            "completion snapshot image was not disposed",
        ),
    )

    for isolation, expected_reason in mismatches:
        evidence = gate.decide(
            work_epoch=1,
            checks=(receipt,),
            coverage=_coverage(),
            expected_check_ids=(receipt.command_id,),
            attempt_id=1,
            isolation=isolation,
            require_isolation=True,
        )
        assert evidence.accepted is False
        assert any(expected_reason in reason for reason in evidence.rejection_reasons)
