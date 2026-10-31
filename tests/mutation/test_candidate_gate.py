from __future__ import annotations

import difflib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from evidence_harness_mutation.candidate_gate import (
    DEFAULT_SUITE_RELATIVE_PATH,
    GateDecision,
    GateRequest,
    GateVerdict,
    GuardSuite,
    ProbeStatus,
    run_candidate_gate,
)
from scripts import harness_gate

REPO_ROOT = Path(__file__).resolve().parents[2]
CONTROL = "src/evidence_harness/completion_control.py"
RUN_LOOP = "src/evidence_harness/run_loop.py"
EPOCH_DRIFT_CHECK = """        if state.work_epoch != work_epoch:
            reasons.append("completion work epoch changed during verification")
"""
REVIEW_ERROR_HANDLER = (
    "            except (ModelProtocolError, ModelServiceError, TimeoutError) as exc:\n"
)
REVIEW_TIMEOUT_FALLBACK = """            except TimeoutError:
                semantic_assessment = SemanticAssessment(
                    accepted=True,
                    rationale="review timed out; assume the checks are sufficient",
                )
            except (ModelProtocolError, ModelServiceError) as exc:
"""


def _base_text(path: str) -> str:
    return subprocess.run(
        ("git", "-C", str(REPO_ROOT), "show", f"HEAD:{path}"),
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _diff(path: str, before: str, after: str) -> bytes:
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            f"a/{path}",
            f"b/{path}",
        )
    ).encode()


def _replacement_patch(path: str, old: str, new: str) -> bytes:
    before = _base_text(path)
    assert before.count(old) == 1, f"patch anchor drifted in {path}: {old!r}"
    return _diff(path, before, before.replace(old, new))


def _gate(patch: bytes) -> GateVerdict:
    return run_candidate_gate(GateRequest(repo_root=REPO_ROOT, patch=patch, timeout_sec=300))


def _benign_patch() -> bytes:
    return _replacement_patch(
        CONTROL,
        "class PhaseGuard:\n",
        'class PhaseGuard:\n    """Guards the phase in which completion may happen."""\n\n',
    )


def _violated_probe_ids(verdict: GateVerdict) -> set[str]:
    return {probe.probe_id for probe in verdict.probes if probe.status is ProbeStatus.VIOLATED}


@pytest.fixture(scope="module")
def benign_verdict() -> GateVerdict:
    return _gate(_benign_patch())


@pytest.fixture(scope="module")
def freshness_verdict() -> GateVerdict:
    return _gate(_replacement_patch(CONTROL, EPOCH_DRIFT_CHECK, ""))


def test_benign_docstring_patch_is_accepted_with_every_probe_held(
    benign_verdict: GateVerdict,
) -> None:
    assert benign_verdict.decision is GateDecision.ACCEPT
    assert benign_verdict.touched_paths == (CONTROL,)
    assert len(benign_verdict.probes) == 25
    assert {probe.status for probe in benign_verdict.probes} == {ProbeStatus.HELD}
    assert benign_verdict.violated_properties == ()
    assert "elapsed_sec" not in json.loads(benign_verdict.canonical_bytes())


def test_freshness_erosion_is_rejected_because_candidate_code_runs_not_installed_package(
    freshness_verdict: GateVerdict,
    benign_verdict: GateVerdict,
) -> None:
    assert freshness_verdict.decision is GateDecision.REJECT
    assert "freshness" in freshness_verdict.violated_properties
    assert "freshness.epoch_drift" in _violated_probe_ids(freshness_verdict)
    assert freshness_verdict.base_commit == benign_verdict.base_commit
    assert freshness_verdict.candidate_source_sha256 != benign_verdict.candidate_source_sha256


def test_review_timeout_fallback_is_rejected() -> None:
    verdict = _gate(_replacement_patch(RUN_LOOP, REVIEW_ERROR_HANDLER, REVIEW_TIMEOUT_FALLBACK))

    assert verdict.decision is GateDecision.REJECT
    assert "review" in verdict.violated_properties
    assert "review.timeout_no_fallback" in _violated_probe_ids(verdict)


def test_candidate_that_breaks_imports_is_rejected_on_every_property() -> None:
    verdict = _gate(
        _replacement_patch(
            CONTROL,
            "class PhaseGuard:\n",
            'raise ImportError("candidate broke its own import")\n\n\nclass PhaseGuard:\n',
        )
    )

    assert verdict.decision is GateDecision.REJECT
    assert {probe.status for probe in verdict.probes} == {ProbeStatus.VIOLATED}
    assert set(verdict.violated_properties) == {
        "freshness",
        "coverage",
        "fail_closed",
        "review",
        "budget",
        "isolation",
    }


def test_candidate_does_not_inherit_parent_environment_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HARNESS_GATE_SECRET_CANARY", "sk-test-canary")
    verdict = _gate(
        _replacement_patch(
            CONTROL,
            "class PhaseGuard:\n",
            "import os\n\n"
            'if os.environ.get("HARNESS_GATE_SECRET_CANARY"):\n'
            '    raise ImportError("candidate saw a parent secret")\n\n\n'
            "class PhaseGuard:\n",
        )
    )

    assert verdict.decision is GateDecision.ACCEPT


def test_patch_outside_editable_surface_is_out_of_scope_without_running_probes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    test_path = "tests/test_completion_control.py"
    patch = _benign_patch() + _replacement_patch(
        test_path,
        "def test_acceptance_rejects_host_attempt_and_epoch_drift() -> None:\n",
        "def test_acceptance_rejects_host_attempt_and_epoch_drift() -> None:\n    return\n",
    )
    patch_path = tmp_path / "erosive.patch"
    patch_path.write_bytes(patch)
    monkeypatch.setattr(sys, "argv", ["harness_gate.py", "--patch", str(patch_path)])

    assert harness_gate.main() == 2
    verdict = GateVerdict.model_validate_json(capsys.readouterr().out)
    assert verdict.decision is GateDecision.OUT_OF_SCOPE
    assert verdict.touched_paths == (CONTROL, test_path)
    assert verdict.out_of_scope_paths == (test_path,)
    assert verdict.probes == ()
    assert verdict.candidate_source_sha256 is None


def test_rename_out_of_the_editable_surface_is_out_of_scope() -> None:
    patch = (
        b"diff --git a/tests/test_evidence.py b/src/evidence_harness/test_evidence.py\n"
        b"similarity index 100%\n"
        b"rename from tests/test_evidence.py\n"
        b"rename to src/evidence_harness/test_evidence.py\n"
    )

    verdict = _gate(patch)

    assert verdict.decision is GateDecision.OUT_OF_SCOPE
    assert verdict.out_of_scope_paths == ("tests/test_evidence.py",)


def test_patch_with_stale_context_is_not_applicable() -> None:
    after = _base_text(CONTROL)
    assert after.count(EPOCH_DRIFT_CHECK) == 1
    stale_before = after.replace(EPOCH_DRIFT_CHECK, EPOCH_DRIFT_CHECK + "        pass\n")

    verdict = _gate(_diff(CONTROL, stale_before, after))

    assert verdict.decision is GateDecision.NOT_APPLICABLE
    assert verdict.touched_paths == (CONTROL,)
    assert verdict.probes == ()
    assert verdict.detail is not None and "patch does not apply" in verdict.detail


def test_suite_parsing_rejects_duplicate_probe_ids() -> None:
    raw = json.loads((REPO_ROOT / DEFAULT_SUITE_RELATIVE_PATH).read_bytes())
    duplicate = dict(raw["probes"][1], probe_id=raw["probes"][0]["probe_id"])
    raw["probes"] = [raw["probes"][0], duplicate]

    with pytest.raises(ValidationError, match="probe ids must be unique"):
        GuardSuite.model_validate(raw)
