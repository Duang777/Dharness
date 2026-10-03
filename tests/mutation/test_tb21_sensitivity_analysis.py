from __future__ import annotations

import hashlib
from fractions import Fraction

import pytest
from pydantic import ValidationError

from evidence_harness_mutation._tb21_analysis import (
    ArtifactBundle,
    ArtifactEntry,
    ReadinessReport,
    ReadinessTask,
    SensitivityComparison,
    analyze_sensitivity,
    deterministic_draw,
)
from evidence_harness_mutation._tb21_manifest import Tb21TaskKey
from evidence_harness_mutation.main_analysis_baselines import RQ2SlotKey
from evidence_harness_mutation.main_analysis_protocol import MainAnalysisMethod
from evidence_harness_mutation.main_analysis_statistics import ExactValue
from evidence_harness_mutation.model import InvariantId
from evidence_harness_mutation.operators import MutationId
from evidence_harness_mutation.prefixbench import PrefixBenchFileBinding


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _slot() -> RQ2SlotKey:
    return RQ2SlotKey(
        task_identity_sha256=_sha("tb21-task"),
        attempt_ordinal=2,
        operator=MutationId.REORDER_CHECK_RECEIPTS,
        target_invariant=InvariantId.I2,
        slot_ordinal=6,
        synthetic_attempt=False,
    )


def test_choice_domain_uses_the_frozen_tb21_namespace() -> None:
    slot = _slot()
    material = b"\0".join(
        (
            b"thesis-tb21-sensitivity-v1",
            b"20261003",
            b"rq2",
            b"schema_valid_random",
            slot.task_identity_sha256.encode(),
            b"2",
            b"reorder_check_receipts",
            b"6",
            b"3",
        )
    )
    expected = int.from_bytes(hashlib.sha256(material).digest()[:8], "big") % 17

    assert (
        deterministic_draw(
            MainAnalysisMethod.SCHEMA_VALID_RANDOM,
            slot,
            3,
            17,
        )
        == expected
    )


def test_readiness_never_claims_ready_for_unavailable_sources() -> None:
    tasks = tuple(
        ReadinessTask(
            ordinal=ordinal,
            key=Tb21TaskKey(source_identity_sha256=_sha(f"source-{ordinal}")),
            status="unavailable",
            journal=None,
            reason="no schema-2 journal",
        )
        for ordinal in range(1, 62)
    )
    report = ReadinessReport(
        canonical=PrefixBenchFileBinding(
            path="evaluation/canonical.json",
            bytes=1,
            sha256=_sha("canonical"),
        ),
        status="source_incomplete",
        admitted_tasks=0,
        tasks=tasks,
    )

    assert report.status == "source_incomplete"

    with pytest.raises(ValidationError, match="readiness status is stale"):
        report.model_copy(update={"status": "ready"}, deep=True).__class__.model_validate(
            {
                **report.model_dump(mode="python"),
                "status": "ready",
            }
        )


def test_inference_rejects_an_incomplete_cohort() -> None:
    with pytest.raises(ValueError, match="exactly 61 tasks"):
        analyze_sensitivity(())


def test_artifact_bundle_cannot_skip_dependency_order() -> None:
    entries = tuple(
        ArtifactEntry(path=path, data=b"{}\n")
        for path in (
            "evaluation/prefixbench-v1-tb21-sensitivity-canonical.json",
            "evaluation/prefixbench-v1-tb21-sensitivity-readiness.json",
            "evaluation/prefixbench-v1-tb21-sensitivity-offline-campaign.json",
        )
    )
    bundle = ArtifactBundle(
        entries=entries,
        complete_sources=False,
        final_available=False,
        waiting_for=("61 admitted schema-2 journals",),
    )

    assert tuple(entry.path for entry in bundle.entries) == tuple(entry.path for entry in entries)


def test_final_comparison_reports_agreement_without_pooled_statistics() -> None:
    positive = ExactValue.from_fraction(Fraction(1, 61))
    zero = ExactValue.from_fraction(Fraction(0))

    comparison = SensitivityComparison(
        comparator=MainAnalysisMethod.RANDOM_JSON,
        tb20_risk_difference=positive,
        tb21_risk_difference=zero,
        direction_agreement=False,
        tb20_disposition="confirmed",
        tb21_disposition="not_confirmed",
        disposition_agreement=False,
    )

    assert comparison.direction_agreement is False
    assert comparison.disposition_agreement is False
