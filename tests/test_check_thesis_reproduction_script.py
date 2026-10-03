from __future__ import annotations

import json
import sys

import pytest

from evidence_harness_mutation.thesis_reproduction import (
    RawInputDeclaration,
    RawInputGroup,
    RawInputMode,
    ReproductionState,
    ThesisReproductionResult,
)
from scripts import check_thesis_reproduction


def _result(state: ReproductionState) -> ThesisReproductionResult:
    raw_inputs = (
        tuple(
            RawInputDeclaration(
                group=group,
                mode=RawInputMode.ARTIFACT_ONLY,
                expected_files={
                    RawInputGroup.HELD_OUT_COLLECTION: 185,
                    RawInputGroup.RQ2_METHOD_COMPARISON: 61,
                    RawInputGroup.RQ3_REDUCER_COMPARISON: 61,
                    RawInputGroup.CROSS_HARNESS_COLLECTION: 20,
                }[group],
                present_files=0,
            )
            for group in RawInputGroup
        )
        if state is not ReproductionState.PARTIAL_INVALID
        else ()
    )
    return ThesisReproductionResult(
        state=state,
        protocol_commit="a" * 40 if state is not ReproductionState.PARTIAL_INVALID else None,
        executable_commit="b" * 40 if state is not ReproductionState.PARTIAL_INVALID else None,
        raw_inputs=raw_inputs,
        errors=("invalid package",) if state is ReproductionState.PARTIAL_INVALID else (),
    )


@pytest.mark.parametrize(
    ("state", "exit_code"),
    [
        (ReproductionState.PRE_COLLECTION, 0),
        (ReproductionState.COMPLETE, 0),
        (ReproductionState.PARTIAL_INVALID, 1),
    ],
)
def test_cli_prints_canonical_result_and_maps_state_to_exit_code(
    state: ReproductionState,
    exit_code: int,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = _result(state)
    monkeypatch.setattr(
        check_thesis_reproduction,
        "check_thesis_reproduction",
        lambda _root: result,
    )
    monkeypatch.setattr(sys, "argv", ["check_thesis_reproduction.py"])

    assert check_thesis_reproduction.main() == exit_code
    output = capsys.readouterr().out.encode()
    assert output == result.canonical_bytes()
    assert json.loads(output)["state"] == state.value


@pytest.mark.parametrize(
    "argument",
    [
        "--root",
        "--mode",
        "--output",
        "--env-file",
        "--provider",
        "check",
    ],
)
def test_cli_rejects_all_arguments(
    argument: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["check_thesis_reproduction.py", argument],
    )
    monkeypatch.setattr(
        check_thesis_reproduction,
        "check_thesis_reproduction",
        lambda _root: pytest.fail("checker ran after a CLI usage error"),
    )

    with pytest.raises(SystemExit) as exc_info:
        check_thesis_reproduction.parse_args()

    assert exc_info.value.code == 2
