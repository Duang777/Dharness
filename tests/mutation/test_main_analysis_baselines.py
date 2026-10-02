from __future__ import annotations

from collections.abc import Callable

import pytest
from pydantic import ValidationError

from evidence_harness_mutation import main_analysis_baselines as baselines
from evidence_harness_mutation import run_offline_campaign
from evidence_harness_mutation.campaign import OfflineCampaignCase
from evidence_harness_mutation.main_analysis_baselines import (
    AgentChaosView,
    RandomJsonView,
    RQ2MethodResult,
    RQ2TaskResult,
    SchemaValidRandomView,
    StatelessSemanticView,
    build_rq2_task,
    build_slot_grid,
)
from evidence_harness_mutation.main_analysis_protocol import (
    MainAnalysisMethod,
    MainAnalysisOutcome,
)
from mutation.support import load_valid_prefix

_TASK_IDENTITY = "a" * 64


def test_slot_grid_is_attempt_major_operator_minor() -> None:
    prefix = load_valid_prefix()

    slots = build_slot_grid(prefix, task_identity_sha256=_TASK_IDENTITY)

    assert [
        (
            slot.slot_ordinal,
            slot.attempt_ordinal,
            slot.operator.value,
            slot.target_invariant.value,
            slot.synthetic_attempt,
        )
        for slot in slots
    ] == [
        (1, 1, "stale_evidence_epoch", "I1", False),
        (2, 1, "reorder_check_receipts", "I2", False),
        (3, 1, "review_timeout_fallback", "I3", False),
        (4, 1, "cross_candidate_evidence", "I4", False),
    ]


def test_slot_grid_keeps_four_synthetic_slots_without_attempts() -> None:
    prefix = load_valid_prefix(through_line=1)

    slots = build_slot_grid(prefix, task_identity_sha256=_TASK_IDENTITY)

    assert len(slots) == 4
    assert {slot.attempt_ordinal for slot in slots} == {1}
    assert all(slot.synthetic_attempt for slot in slots)


def test_baseline_selector_views_exclude_state_aware_fields() -> None:
    forbidden = {
        "attempt",
        "audit",
        "phase",
        "project_root",
        "source_line",
        "state_aware_target",
    }

    assert not forbidden & set(RandomJsonView.model_fields)
    assert not forbidden & set(SchemaValidRandomView.model_fields)
    assert not forbidden & set(AgentChaosView.model_fields)
    assert not forbidden & set(StatelessSemanticView.model_fields)


def test_builder_runs_all_baseline_selectors_before_loading_state_aware_campaign(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prefix = load_valid_prefix()
    observed: set[MainAnalysisMethod] = set()
    wrapped: tuple[
        tuple[str, MainAnalysisMethod, Callable[..., object]],
        ...,
    ] = (
        (
            "select_random_json",
            MainAnalysisMethod.RANDOM_JSON,
            baselines.select_random_json,
        ),
        (
            "select_schema_valid_random",
            MainAnalysisMethod.SCHEMA_VALID_RANDOM,
            baselines.select_schema_valid_random,
        ),
        (
            "select_agentchaos_style",
            MainAnalysisMethod.AGENTCHAOS_STYLE,
            baselines.select_agentchaos_style,
        ),
        (
            "select_stateless_semantic",
            MainAnalysisMethod.STATELESS_SEMANTIC,
            baselines.select_stateless_semantic,
        ),
    )
    for name, method, original in wrapped:

        def record(
            *args: object,
            _method: MainAnalysisMethod = method,
            _original: Callable[..., object] = original,
            **kwargs: object,
        ) -> object:
            observed.add(_method)
            return _original(*args, **kwargs)

        monkeypatch.setattr(baselines, name, record)

    def load_cases() -> tuple[OfflineCampaignCase, ...]:
        assert observed == set(MainAnalysisMethod) - {MainAnalysisMethod.STATE_AWARE}
        return run_offline_campaign(prefix).cases

    result = build_rq2_task(
        task_name="synthetic",
        task_identity_sha256=_TASK_IDENTITY,
        prefix=prefix,
        load_state_aware_cases=load_cases,
    )

    assert tuple(method.method for method in result.methods) == tuple(MainAnalysisMethod)
    assert all(len(method.outcomes) == 4 for method in result.methods)
    assert {outcome.outcome for outcome in result.methods[0].outcomes} == {
        MainAnalysisOutcome.TARGET_VIOLATION
    }


def test_schema_valid_random_candidates_cross_the_typed_input_boundary() -> None:
    prefix = load_valid_prefix()

    result = build_rq2_task(
        task_name="synthetic",
        task_identity_sha256=_TASK_IDENTITY,
        prefix=prefix,
        load_state_aware_cases=lambda: run_offline_campaign(prefix).cases,
    )
    schema = next(
        method
        for method in result.methods
        if method.method is MainAnalysisMethod.SCHEMA_VALID_RANDOM
    )

    assert all(
        outcome.outcome is not MainAnalysisOutcome.INPUT_REJECTED for outcome in schema.outcomes
    )


def test_agentchaos_rotation_does_not_redraw_after_rejection() -> None:
    prefix = load_valid_prefix()

    first = build_rq2_task(
        task_name="synthetic",
        task_identity_sha256=_TASK_IDENTITY,
        prefix=prefix,
        load_state_aware_cases=lambda: run_offline_campaign(prefix).cases,
    )
    second = build_rq2_task(
        task_name="synthetic",
        task_identity_sha256=_TASK_IDENTITY,
        prefix=prefix,
        load_state_aware_cases=lambda: run_offline_campaign(prefix).cases,
    )
    first_agentchaos = next(
        method for method in first.methods if method.method is MainAnalysisMethod.AGENTCHAOS_STYLE
    )
    second_agentchaos = next(
        method for method in second.methods if method.method is MainAnalysisMethod.AGENTCHAOS_STYLE
    )

    assert first_agentchaos == second_agentchaos
    assert all(
        outcome.outcome is MainAnalysisOutcome.INPUT_REJECTED
        for outcome in first_agentchaos.outcomes
    )


def test_task_result_rejects_an_unequal_method_grid() -> None:
    prefix = load_valid_prefix()
    result = build_rq2_task(
        task_name="synthetic",
        task_identity_sha256=_TASK_IDENTITY,
        prefix=prefix,
        load_state_aware_cases=lambda: run_offline_campaign(prefix).cases,
    )
    changed = result.methods[1].model_copy(
        update={"outcomes": tuple(reversed(result.methods[1].outcomes))}
    )

    with pytest.raises(ValidationError, match="same slot grid"):
        RQ2TaskResult(
            task_name=result.task_name,
            task_identity_sha256=result.task_identity_sha256,
            slots=result.slots,
            methods=(result.methods[0], changed, *result.methods[2:]),
        )


def test_method_result_rejects_outcome_from_another_method() -> None:
    prefix = load_valid_prefix()
    result = build_rq2_task(
        task_name="synthetic",
        task_identity_sha256=_TASK_IDENTITY,
        prefix=prefix,
        load_state_aware_cases=lambda: run_offline_campaign(prefix).cases,
    )

    with pytest.raises(ValidationError, match="contains another method"):
        RQ2MethodResult(
            method=MainAnalysisMethod.RANDOM_JSON,
            outcomes=result.methods[0].outcomes,
        )
