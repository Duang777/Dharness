from evidence_harness.evidence import EvidenceGate
from evidence_harness.protocol import (
    CommandMode,
    CommandReceipt,
    LoopOptions,
    OutputExcerpt,
    RequirementCoverage,
    SemanticAssessment,
)


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


def test_required_semantic_review_cannot_be_missing() -> None:
    evidence = EvidenceGate(LoopOptions()).decide(
        work_epoch=1,
        checks=(_successful_receipt(),),
        coverage=_coverage(),
        require_semantic_review=True,
    )

    assert evidence.accepted is False
    assert evidence.semantic_assessment is None
    assert evidence.rejection_reasons == ("completion semantic review was not accepted",)


def test_semantic_rejection_is_part_of_verification_evidence() -> None:
    assessment = SemanticAssessment(
        accepted=False,
        rationale="the check omits the required value",
        findings=("answer.txt content is not proven",),
    )

    evidence = EvidenceGate(LoopOptions()).decide(
        work_epoch=1,
        checks=(_successful_receipt(),),
        coverage=_coverage(),
        semantic_assessment=assessment,
        require_semantic_review=True,
    )

    assert evidence.accepted is False
    assert evidence.semantic_assessment == assessment
    assert evidence.rejection_reasons == (
        "the check omits the required value",
        "answer.txt content is not proven",
    )


def test_semantic_acceptance_completes_verification_evidence() -> None:
    assessment = SemanticAssessment(
        accepted=True,
        rationale="the receipt proves the required value",
    )

    evidence = EvidenceGate(LoopOptions()).decide(
        work_epoch=1,
        checks=(_successful_receipt(),),
        coverage=_coverage(),
        semantic_assessment=assessment,
        require_semantic_review=True,
    )

    assert evidence.accepted is True
    assert evidence.semantic_assessment == assessment
    assert evidence.rejection_reasons == ()


def test_explicit_review_opt_out_keeps_mechanical_mode() -> None:
    evidence = EvidenceGate(LoopOptions(enable_completion_review=False)).decide(
        work_epoch=1,
        checks=(_successful_receipt(),),
        coverage=_coverage(),
    )

    assert evidence.accepted is True
    assert evidence.semantic_assessment is None
