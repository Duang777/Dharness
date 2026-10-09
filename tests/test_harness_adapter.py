from __future__ import annotations

import ast
from dataclasses import replace
from pathlib import Path

from evidence_harness.completion_contract import CompletionContract, TaskRequirement
from evidence_harness.completion_control import CompletionController
from evidence_harness.harness_adapter import (
    CandidateIdentity,
    CandidateSnapshot,
    CompletionCounters,
    CompletionPolicy,
    CompletionProposal,
    CompletionState,
    CompletionTrace,
    CompletionTransactionView,
    CurrentCandidate,
)
from evidence_harness.protocol import (
    CheckKind,
    LoopOptions,
    RequirementCoverage,
    RunPhase,
    VerificationCheck,
)
from evidence_harness.stub_harness_adapter import StubHarnessAdapter


def _view(*, checks: tuple[VerificationCheck, ...] | None = None) -> CompletionTransactionView:
    proposed_checks = checks or (
        VerificationCheck(
            id="answer",
            kind=CheckKind.BEHAVIOR,
            script="check answer.txt",
            proves="REQ-1",
        ),
    )
    options = LoopOptions(enable_completion_review=False)
    contract = CompletionContract.create(
        requirements=(
            TaskRequirement(
                id="REQ-1",
                statement="answer.txt contains ready",
                evidence_kinds=(CheckKind.BEHAVIOR,),
            ),
        ),
        options=options,
    )
    return CompletionTransactionView(
        proposal=CompletionProposal(
            checks=proposed_checks,
            coverage=(
                RequirementCoverage(
                    requirement="REQ-1",
                    check_ids=tuple(check.id for check in proposed_checks),
                ),
            ),
            candidate=CurrentCandidate(),
            summary="answer.txt is ready",
        ),
        contract=contract,
        policy=CompletionPolicy(
            review_required=False,
            max_check_timeout_sec=options.max_command_timeout_sec,
        ),
        trace=CompletionTrace(),
        state=CompletionState(
            phase=RunPhase.FINALIZING,
            work_epoch=3,
            next_completion_attempt=1,
            candidate_digest=None,
            deadline_monotonic=100,
            counters=CompletionCounters(
                turns=1,
                environment_calls=0,
                repairs=0,
                recoveries=0,
                completion_reviews=0,
            ),
        ),
    )


def _verifying_state(
    view: CompletionTransactionView,
    *,
    candidate_digest: str,
) -> CompletionState:
    return replace(
        view.state,
        phase=RunPhase.VERIFYING,
        next_completion_attempt=view.state.next_completion_attempt + 1,
        candidate_digest=candidate_digest,
    )


def test_adapter_returns_a_defensive_copy_of_the_completion_view() -> None:
    view = _view()
    adapter = StubHarnessAdapter(
        view=view,
        candidate_files={"answer.txt": b"ready\n"},
        check_runners={"answer": lambda files: True},
    )

    adapter.view.proposal.checks[0].script = "changed"
    view.proposal.checks[0].script = "also changed"

    assert adapter.view.proposal.checks[0].script == "check answer.txt"


async def test_stub_adapter_completes_without_dharness_runtime() -> None:
    view = _view()
    adapter = StubHarnessAdapter(
        view=view,
        candidate_files={"answer.txt": b"ready\n"},
        check_runners={
            "answer": lambda files: files["answer.txt"] == b"ready\n",
        },
    )
    controller = CompletionController(adapter)

    admission = controller.admit(now=1)
    run = await controller.verify(admission)
    state = _verifying_state(
        view,
        candidate_digest=run.snapshot.candidate_digest,
    )
    acceptance = controller.evaluate_accept(run, state=state, now=2)
    completion = controller.evaluate_complete(
        acceptance,
        assessment=None,
        state=state,
    )

    assert admission.accepted
    assert completion.accepted
    assert completion.receipt.isolation is not None
    assert completion.receipt.isolation.backend == "memory-snapshot-v1"
    assert run.snapshot.identity.algorithm == "sha256"
    assert run.snapshot.identity.value.startswith("sha256:")
    assert controller.authorizes(completion.permit, state)
    assert (
        controller.authorizes(
            completion.permit,
            replace(state, candidate_digest="9" * 64),
        )
        is False
    )
    assert adapter.isolated_run_count == 1


async def test_stub_gives_each_check_a_private_candidate_copy() -> None:
    checks = (
        VerificationCheck(
            id="mutate",
            kind=CheckKind.BEHAVIOR,
            script="mutate private copy",
            proves="REQ-1",
        ),
        VerificationCheck(
            id="observe",
            kind=CheckKind.BEHAVIOR,
            script="observe clean copy",
            proves="REQ-1",
        ),
    )
    view = _view(checks=checks)

    def mutate(files: dict[str, bytes]) -> bool:
        files["answer.txt"] = b"changed\n"
        return True

    adapter = StubHarnessAdapter(
        view=view,
        candidate_files={"answer.txt": b"ready\n"},
        check_runners={
            "mutate": mutate,
            "observe": lambda files: files["answer.txt"] == b"ready\n",
        },
    )

    run = await adapter.run_checks_isolated()

    assert [receipt.succeeded for receipt in run.receipts] == [True, True]
    assert len({record.child_id_sha256 for record in run.isolation.checks}) == 2
    assert all(record.disposed for record in run.isolation.checks)


async def test_controller_rejects_snapshot_binding_mismatches() -> None:
    view = _view()
    controller = CompletionController(
        StubHarnessAdapter(
            view=view,
            candidate_files={"answer.txt": b"ready\n"},
            check_runners={"answer": lambda files: True},
        )
    )
    run = await controller.verify(controller.admit(now=1))
    run = replace(
        run,
        snapshot=CandidateSnapshot(
            identity=CandidateIdentity(algorithm="sha256", value="sha256:different"),
            candidate_digest=run.snapshot.candidate_digest,
            attempt_id=2,
            work_epoch=4,
        ),
    )

    acceptance = controller.evaluate_accept(
        run,
        state=_verifying_state(
            view,
            candidate_digest=run.snapshot.candidate_digest,
        ),
        now=2,
    )

    assert acceptance.accepted is False
    assert acceptance.evidence.reasons == (
        "candidate snapshot attempt does not match",
        "candidate snapshot work epoch does not match",
        "candidate snapshot identity does not match isolation evidence",
    )


async def test_controller_rejects_completion_state_candidate_drift() -> None:
    view = _view()
    controller = CompletionController(
        StubHarnessAdapter(
            view=view,
            candidate_files={"answer.txt": b"ready\n"},
            check_runners={"answer": lambda files: True},
        )
    )
    run = await controller.verify(controller.admit(now=1))

    acceptance = controller.evaluate_accept(
        run,
        state=_verifying_state(
            view,
            candidate_digest="9" * 64,
        ),
        now=2,
    )

    assert acceptance.accepted is False
    assert acceptance.evidence.reasons == ("completion state candidate digest does not match",)


def test_portable_completion_modules_do_not_import_dharness_runtime() -> None:
    package = Path(__file__).parents[1] / "src" / "evidence_harness"
    modules = (
        package / "harness_adapter.py",
        package / "completion_control.py",
        package / "stub_harness_adapter.py",
    )
    banned = {
        "evidence_harness.completion_isolation",
        "evidence_harness.dharness_adapter",
        "evidence_harness.docker_completion_isolation",
        "evidence_harness.harbor_agent",
        "evidence_harness.journal",
        "evidence_harness.run_loop",
        "evidence_harness.shell",
    }

    imported: set[str] = set()
    for module in modules:
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported.add(node.module)

    assert imported.isdisjoint(banned)
