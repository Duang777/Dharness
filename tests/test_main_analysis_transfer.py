from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path

import pytest

from evidence_harness_mutation.main_analysis_transfer import (
    TransferCaseStatus,
    TransferFamily,
    TransferInvariantStatus,
    adapt_miniswe_trajectory,
    audit_transfer_trace,
    evaluate_transfer_family,
    transfer_family_applicable,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
TRANSFER_MODULE = PROJECT_ROOT / "src/evidence_harness_mutation/main_analysis_transfer.py"


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _trajectory(
    *,
    trees: tuple[str, ...] = ("initial", "first", "second"),
    commands: tuple[str, ...] = ("printf first > file", "printf second >> file"),
) -> bytes:
    assert len(trees) == len(commands) + 1
    messages: list[dict[str, object]] = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "task"},
    ]
    attestations: list[dict[str, object]] = [
        {
            "ordinal": 1,
            "stage": "initial",
            "action_ordinal": None,
            "tree_sha256": _sha(trees[0]),
        }
    ]
    for action_ordinal, command in enumerate(commands, start=1):
        call_id = f"call-{action_ordinal}"
        messages.extend(
            (
                {
                    "role": "assistant",
                    "content": f"decision {action_ordinal}",
                    "extra": {
                        "actions": [
                            {
                                "command": command,
                                "tool_call_id": call_id,
                            }
                        ]
                    },
                },
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": f"result {action_ordinal}",
                    "extra": {
                        "returncode": 0,
                        "exception_info": "",
                    },
                },
            )
        )
        attestations.append(
            {
                "ordinal": action_ordinal + 1,
                "stage": "after_action",
                "action_ordinal": action_ordinal,
                "tree_sha256": _sha(trees[action_ordinal]),
            }
        )
    messages.append(
        {
            "role": "exit",
            "content": "done",
            "extra": {
                "exit_status": "Submitted",
                "submission": "done",
            },
        }
    )
    attestations.append(
        {
            "ordinal": len(commands) + 2,
            "stage": "terminal",
            "action_ordinal": None,
            "tree_sha256": _sha(trees[-1]),
        }
    )
    return (
        json.dumps(
            {
                "trajectory_format": "mini-swe-agent-1.1",
                "instance_id": "example__task.1234567",
                "info": {
                    "exit_status": "Submitted",
                    "submission": "done",
                },
                "messages": messages,
                "workspace_attestations": attestations,
            },
            sort_keys=True,
        )
        + "\n"
    ).encode()


def test_adapter_builds_independent_control_chain_from_explicit_attestations() -> None:
    trace = adapt_miniswe_trajectory(_trajectory())

    assert trace.instance_id == "example__task.1234567"
    assert len(trace.decisions) == 2
    assert [step.action.ordinal for step in trace.steps] == [1, 2]
    assert [step.observation.action_ordinal for step in trace.steps] == [1, 2]
    assert trace.steps[0].action.prior_tree_sha256 == _sha("initial")
    assert trace.steps[0].candidate.tree_sha256 == _sha("first")
    assert trace.terminal.bound_tree_sha256 == _sha("second")

    audit = audit_transfer_trace(trace)
    assert [result.status for result in audit.results] == [
        TransferInvariantStatus.PASS,
        TransferInvariantStatus.PASS,
        TransferInvariantStatus.UNSUPPORTED_BY_DESIGN,
        TransferInvariantStatus.PASS,
    ]


@pytest.mark.parametrize("failure", ["missing", "duplicate", "reordered"])
def test_adapter_rejects_invalid_workspace_attestation_sequences(failure: str) -> None:
    payload = json.loads(_trajectory())
    attestations = payload["workspace_attestations"]
    if failure == "missing":
        del attestations[1]
    elif failure == "duplicate":
        attestations[2]["ordinal"] = attestations[1]["ordinal"]
    else:
        attestations[1], attestations[2] = attestations[2], attestations[1]

    with pytest.raises(ValueError, match="workspace attestation"):
        adapt_miniswe_trajectory(json.dumps(payload).encode())


def test_adapter_rejects_a_tool_observation_bound_to_another_action() -> None:
    payload = json.loads(_trajectory())
    payload["messages"][3]["tool_call_id"] = "another-call"

    with pytest.raises(ValueError, match="does not match its action"):
        adapt_miniswe_trajectory(json.dumps(payload).encode())


def test_i1_applicability_uses_digests_not_command_text() -> None:
    trace = adapt_miniswe_trajectory(
        _trajectory(
            trees=("same", "same", "same"),
            commands=("rm -rf apparent-change", "touch apparent-change"),
        )
    )

    assert not transfer_family_applicable(trace, TransferFamily.I1)
    assert (
        evaluate_transfer_family(trace, TransferFamily.I1).status
        is TransferCaseStatus.NOT_APPLICABLE
    )


@pytest.mark.parametrize(
    "family",
    [TransferFamily.I1, TransferFamily.I2, TransferFamily.I4],
)
def test_mapped_family_mutations_create_deterministic_target_violations(
    family: TransferFamily,
) -> None:
    trace = adapt_miniswe_trajectory(_trajectory())

    result = evaluate_transfer_family(trace, family)

    assert result.status is TransferCaseStatus.TARGET_VIOLATION
    assert result.deterministic_replay
    assert result.mutated_audit is not None
    assert result.mutated_audit.result(family).status is TransferInvariantStatus.FAIL


def test_i3_is_fixed_as_unsupported_by_design() -> None:
    trace = adapt_miniswe_trajectory(_trajectory())

    result = evaluate_transfer_family(trace, TransferFamily.I3)

    assert result.status is TransferCaseStatus.NOT_APPLICABLE
    assert (
        result.source_audit.result(TransferFamily.I3).status
        is TransferInvariantStatus.UNSUPPORTED_BY_DESIGN
    )


def test_terminal_action_may_use_the_exit_message_as_its_observation() -> None:
    payload = json.loads(
        _trajectory(
            trees=("initial", "submitted"),
            commands=("echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT",),
        )
    )
    del payload["messages"][-2]

    trace = adapt_miniswe_trajectory(json.dumps(payload).encode())

    assert len(trace.steps) == 1
    assert trace.steps[0].observation.terminal_observation
    assert trace.steps[0].observation.source_message_ordinal == len(payload["messages"])


def test_transfer_adapter_imports_only_stdlib_and_pydantic() -> None:
    tree = ast.parse(TRANSFER_MODULE.read_text(encoding="utf-8"))
    roots = {
        alias.name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    roots.update(
        node.module.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    )

    assert roots <= {
        "__future__",
        "enum",
        "hashlib",
        "json",
        "pydantic",
        "typing",
    }
    source = TRANSFER_MODULE.read_text(encoding="utf-8")
    assert "evidence_harness_mutation" not in source
    assert "work_epoch" not in source
    assert "attempt_id" not in source
