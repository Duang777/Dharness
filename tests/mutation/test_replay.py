from __future__ import annotations

import hashlib
import json

import pytest

from evidence_harness.protocol import ActionKind, AgentDecision, ReviewDecision
from evidence_harness_mutation import ReplayContractError, ReplayGateway, load_state_prefix


def _event(event_type: str, payload: dict[str, object]) -> str:
    return json.dumps(
        {
            "timestamp": "2026-10-01T00:00:00+00:00",
            "type": event_type,
            "payload": payload,
        },
        sort_keys=True,
    )


def _prefix():
    first = AgentDecision(
        action=ActionKind.REPLAN,
        rationale="inspect a different subsystem",
        plan=("inspect",),
    )
    review = ReviewDecision(verdict="accept", rationale="the evidence is sufficient")
    second = AgentDecision(
        action=ActionKind.STOP,
        rationale="the task is blocked",
        stop_category="blocked",
    )
    data = (
        "\n".join(
            (
                _event(
                    "run_started",
                    {
                        "journal_schema_version": 2,
                        "instruction": "repair the artifact",
                        "options": {"enable_completion_review": True},
                    },
                ),
                _event(
                    "model_decision",
                    {"role": "executor", "decision": first.model_dump(mode="json")},
                ),
                _event("agent_decision", first.model_dump(mode="json")),
                _event("completion_review", review.model_dump(mode="json")),
                _event("agent_decision", second.model_dump(mode="json")),
            )
        )
        + "\n"
    ).encode()
    return load_state_prefix(
        data,
        source_commit="b" * 40,
        expected_journal_sha256=hashlib.sha256(data).hexdigest(),
    )


async def test_replay_gateway_preserves_model_call_order_and_usage() -> None:
    gateway = ReplayGateway.from_prefix(_prefix())

    with pytest.raises(ReplayContractError, match=r"expected replay decide.*got review"):
        await gateway.review("wrong call kind")
    first = await gateway.decide("ignored")
    assert gateway.consumed_lines == (3,)
    assert gateway.usage.model_calls == 1
    with pytest.raises(ReplayContractError, match="unconsumed review"):
        gateway.assert_exhausted()

    review = await gateway.review("ignored")
    second = await gateway.decide("ignored")
    gateway.assert_exhausted()

    assert first.action is ActionKind.REPLAN
    assert review.verdict == "accept"
    assert second.action is ActionKind.STOP
    assert gateway.usage.model_calls == 3
    with pytest.raises(ReplayContractError, match="no recorded decide"):
        await gateway.decide("exhausted")


async def test_replay_gateways_are_independent_and_return_copies() -> None:
    prefix = _prefix()
    first_gateway = ReplayGateway.from_prefix(prefix)
    second_gateway = ReplayGateway.from_prefix(prefix)

    first = await first_gateway.decide("ignored")
    first.rationale = "mutated by caller"
    second = await second_gateway.decide("ignored")
    usage = first_gateway.usage
    usage.model_calls = 99

    assert second.rationale == "inspect a different subsystem"
    assert first_gateway.usage.model_calls == 1
    assert second_gateway.usage.model_calls == 1
