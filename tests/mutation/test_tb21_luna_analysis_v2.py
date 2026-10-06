from __future__ import annotations

import hashlib

import pytest
from pydantic import ValidationError

from evidence_harness_mutation._tb21_luna_analysis_v2 import (
    ArtifactBundle,
    ArtifactEntry,
    LunaRq2Report,
    ReadinessReport,
    ReadinessTask,
    analyze_luna_rq2,
    deterministic_draw,
)
from evidence_harness_mutation._tb21_manifest import Tb21TaskKey
from evidence_harness_mutation.main_analysis_baselines import RQ2SlotKey
from evidence_harness_mutation.main_analysis_protocol import MainAnalysisMethod
from evidence_harness_mutation.model import InvariantId
from evidence_harness_mutation.operators import MutationId
from evidence_harness_mutation.prefixbench import PrefixBenchFileBinding


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _slot() -> RQ2SlotKey:
    return RQ2SlotKey(
        task_identity_sha256=_sha("luna-task"),
        attempt_ordinal=2,
        operator=MutationId.REORDER_CHECK_RECEIPTS,
        target_invariant=InvariantId.I2,
        slot_ordinal=6,
        synthetic_attempt=False,
    )


def test_choice_domain_uses_the_frozen_luna_namespace() -> None:
    slot = _slot()
    material = b"\0".join(
        (
            b"thesis-tb21-luna-rq2-replication-v1",
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

    with pytest.raises(ValidationError, match="readiness status is stale"):
        ReadinessReport.model_validate(
            {
                **report.model_dump(mode="python"),
                "status": "ready",
            }
        )


def test_final_report_schema_forbids_external_inference() -> None:
    fields = LunaRq2Report.model_fields

    assert fields["model"].default == "openai/modelhub/gpt-5.6-luna"
    assert fields["terra_outcomes"].default == "forbidden-input"
    assert fields["tb20_outcomes"].default == "forbidden-input"
    assert fields["cross_model_inference"].default == "none"
    assert fields["cross_version_inference"].default == "none"


def test_inference_rejects_an_incomplete_cohort() -> None:
    with pytest.raises(ValueError, match="exactly 61 tasks"):
        analyze_luna_rq2(())


def test_artifact_bundle_has_one_luna_only_final_report() -> None:
    entries = tuple(
        ArtifactEntry(path=path, data=b"{}\n")
        for path in (
            "evaluation/prefixbench-v1-tb21-luna-rq2-canonical-v2.json",
            "evaluation/prefixbench-v1-tb21-luna-rq2-readiness-v2.json",
            "evaluation/prefixbench-v1-tb21-luna-rq2-offline-campaign-v2.json",
        )
    )
    bundle = ArtifactBundle(
        entries=entries,
        complete_sources=False,
        final_available=False,
        waiting_for=("61 admitted schema-2 journals",),
    )

    assert len(bundle.entries) == 3
    assert all("sensitivity" not in entry.path for entry in bundle.entries)
