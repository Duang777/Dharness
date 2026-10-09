from __future__ import annotations

import hashlib

import pytest
from conftest import FakeCompletionIsolation, FakeEnvironment

from evidence_harness.completion_contract import CompletionContract
from evidence_harness.completion_isolation import (
    CompletionIsolationError,
    IsolationFailureKind,
)
from evidence_harness.dharness_adapter import DharnessAdapter
from evidence_harness.harness_adapter import HarnessAdapterError
from evidence_harness.protocol import (
    ActionKind,
    AgentDecision,
    CheckKind,
    CommandMode,
    CommandReceipt,
    LoopOptions,
    OutputExcerpt,
    RequirementCoverage,
    RunPhase,
    RunState,
    VerificationCheck,
)

_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()


def _decision() -> AgentDecision:
    return AgentDecision(
        action=ActionKind.FINISH,
        rationale="verify the candidate",
        summary="done",
        checks=(
            VerificationCheck(
                id="answer",
                kind=CheckKind.ARTIFACT,
                script="test -s answer.txt",
                proves="REQ-1",
            ),
        ),
        coverage=(
            RequirementCoverage(
                requirement="REQ-1",
                check_ids=("answer",),
            ),
        ),
    )


def _state() -> RunState:
    return RunState(
        instruction="Create answer.txt",
        phase=RunPhase.FINALIZING,
        started_monotonic=0,
        deadline_monotonic=100,
        turn_count=2,
        work_epoch=4,
    )


def _receipt(check: VerificationCheck, state: RunState) -> CommandReceipt:
    output = OutputExcerpt(
        head="",
        tail="",
        total_bytes=0,
        omitted_bytes=0,
        sha256=_EMPTY_SHA256,
    )
    return CommandReceipt(
        sequence=state.next_sequence,
        command_id=check.id,
        script=check.script,
        purpose=check.proves,
        cwd=check.cwd,
        mode=CommandMode.OBSERVE,
        work_epoch=state.work_epoch,
        return_code=0,
        duration_sec=0,
        stdout=output,
        stderr=output,
        command_fingerprint="a" * 64,
        observation_fingerprint="b" * 64,
    )


async def _unused_execute(check, environment, deadline):
    raise AssertionError((check, environment, deadline))


async def test_dharness_adapter_delegates_one_atomic_isolation_run() -> None:
    options = LoopOptions(enable_completion_review=False)
    state = _state()
    decision = _decision()
    isolation = FakeCompletionIsolation(FakeEnvironment())

    async def execute(check, environment, deadline):
        del environment, deadline
        return _receipt(check, state)

    adapter = DharnessAdapter.capture(
        contract=CompletionContract.from_instruction(state.instruction, options),
        options=options,
        state=state,
        decision=decision,
        isolation=isolation,
        execute_check=execute,
    )

    run = await adapter.run_checks_isolated()

    assert len(isolation.requests) == 1
    assert isolation.requests[0].attempt_id == 1
    assert isolation.requests[0].work_epoch == 4
    assert isolation.requests[0].checks == decision.checks
    assert run.snapshot.identity.algorithm == "docker-image-id"
    assert run.snapshot.identity.value == "sha256:test-candidate"
    assert tuple(receipt.command_id for receipt in run.receipts) == ("answer",)
    assert run.isolation.checks[0].receipt_sequence == run.receipts[0].sequence
    assert (
        run.isolation.checks[0].receipt_observation_sha256
        == run.receipts[0].observation_fingerprint
    )


def test_dharness_adapter_freezes_trace_and_state_at_capture() -> None:
    options = LoopOptions(enable_completion_review=False)
    state = _state()
    prior = _receipt(_decision().checks[0], state)
    state.observations.append(prior)
    adapter = DharnessAdapter.capture(
        contract=CompletionContract.from_instruction(state.instruction, options),
        options=options,
        state=state,
        decision=_decision(),
        isolation=FakeCompletionIsolation(FakeEnvironment()),
        execute_check=_unused_execute,
    )

    state.work_epoch = 9
    state.observations.clear()

    assert adapter.view.state.work_epoch == 4
    assert adapter.view.trace.receipts == (prior,)


async def test_dharness_adapter_translates_isolation_errors() -> None:
    options = LoopOptions(enable_completion_review=False)
    state = _state()
    adapter = DharnessAdapter.capture(
        contract=CompletionContract.from_instruction(state.instruction, options),
        options=options,
        state=state,
        decision=_decision(),
        isolation=FakeCompletionIsolation(
            FakeEnvironment(),
            failure=CompletionIsolationError(
                IsolationFailureKind.CLEANUP,
                "cleanup failed",
                attempt_id=1,
            ),
        ),
        execute_check=_unused_execute,
    )

    with pytest.raises(HarnessAdapterError) as caught:
        await adapter.run_checks_isolated()

    assert caught.value.kind == IsolationFailureKind.CLEANUP.value
    assert caught.value.attempt_id == 1
