from __future__ import annotations

from evidence_harness_mutation import MutationId, MutationRequest, apply_mutation
from evidence_harness_mutation.main_analysis_baselines import build_rq2_task
from evidence_harness_mutation.main_analysis_protocol import (
    MainAnalysisMethod,
    MainAnalysisReducer,
)
from evidence_harness_mutation.main_analysis_reducers import (
    ReductionInput,
    compare_reducers,
)
from evidence_harness_mutation.main_analysis_report import (
    analyze_rq2,
    analyze_rq3,
    analyze_rq4,
    rq4_rejected_task,
)
from evidence_harness_mutation.main_analysis_transfer import TransferFamily
from evidence_harness_mutation.prefixbench import PrefixBenchFileBinding
from mutation.support import load_valid_prefix


def _binding(path: str) -> PrefixBenchFileBinding:
    return PrefixBenchFileBinding(path=path, bytes=1, sha256="a" * 64)


def test_rq2_analysis_uses_task_level_pairs_and_protocol_order() -> None:
    prefix = load_valid_prefix()
    tasks = tuple(
        build_rq2_task(
            task_name=f"task-{ordinal}",
            task_identity_sha256=f"{ordinal:064x}",
            prefix=prefix,
            load_state_aware_cases=lambda: (
                __import__("evidence_harness_mutation").run_offline_campaign(prefix).cases
            ),
        )
        for ordinal in range(1, 9)
    )

    analysis = analyze_rq2(tasks)

    assert tuple(summary.method for summary in analysis.methods) == tuple(MainAnalysisMethod)
    assert tuple(contrast.comparator for contrast in analysis.contrasts) == (
        MainAnalysisMethod.RANDOM_JSON,
        MainAnalysisMethod.SCHEMA_VALID_RANDOM,
        MainAnalysisMethod.AGENTCHAOS_STYLE,
        MainAnalysisMethod.STATELESS_SEMANTIC,
    )
    assert all(summary.tasks == 8 for summary in analysis.methods)
    assert all(
        summary.outcomes.scheduled == summary.scheduled_slots for summary in analysis.methods
    )


def test_rq3_analysis_omits_inference_below_the_frozen_minimum() -> None:
    prefix = load_valid_prefix()
    mutation = apply_mutation(
        prefix,
        MutationRequest(operator=MutationId.STALE_EVIDENCE_EPOCH),
    )
    comparison = compare_reducers(
        ReductionInput(
            task_identity_sha256="b" * 64,
            case_ordinal=1,
            mutation=mutation,
        )
    )

    analysis = analyze_rq3((comparison,))

    assert analysis.status == "insufficient_cases"
    assert analysis.contrasts == ()
    assert tuple(summary.reducer for summary in analysis.reducers) == tuple(MainAnalysisReducer)


def test_rq3_empty_population_is_a_valid_insufficient_result() -> None:
    analysis = analyze_rq3(())

    assert analysis.cases == 0
    assert analysis.tasks == 0
    assert analysis.reducers == ()
    assert analysis.contrasts == ()


def test_rq4_summary_keeps_all_four_families_and_adapter_rejections() -> None:
    tasks = tuple(
        rq4_rejected_task(
            index=ordinal,
            instance_id=f"task-{ordinal}",
            trajectory=_binding(
                f"runs/programbench/miniswe-transfer-v1/task-{ordinal}/task-{ordinal}.traj.json"
            ),
            error="ValueError: synthetic rejection",
        )
        for ordinal in range(1, 21)
    )

    analysis = analyze_rq4(tasks)

    assert analysis.collection_complete
    assert analysis.disposition == "transfer_not_confirmed"
    assert tuple(summary.family for summary in analysis.families) == tuple(TransferFamily)
    assert analysis.families[0].adapter_rejected == 20
    assert analysis.families[2].mapping == "unsupported_by_design"
    assert analysis.families[2].scheduled == 20


def test_rq4_missing_trajectory_marks_the_collection_incomplete() -> None:
    tasks = tuple(
        rq4_rejected_task(
            index=ordinal,
            instance_id=f"task-{ordinal}",
            trajectory=(
                None
                if ordinal == 20
                else _binding(
                    f"runs/programbench/miniswe-transfer-v1/task-{ordinal}/task-{ordinal}.traj.json"
                )
            ),
            error="missing trajectory",
        )
        for ordinal in range(1, 21)
    )

    assert analyze_rq4(tasks).disposition == "incomplete"
