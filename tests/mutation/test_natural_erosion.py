from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from evidence_harness_mutation.candidate_gate import GateVerdict, GuardComponent, load_guard_suite
from evidence_harness_mutation.natural_annotation import (
    AnnotationError,
    NaturalLabel,
    NaturalLabels,
    cohen_kappa,
    labels_agree,
    parse_label,
)
from evidence_harness_mutation.natural_campaign import (
    AuditorDecision,
    Gate,
    build_report,
    parse_auditor,
)
from evidence_harness_mutation.natural_erosion import (
    NaturalSample,
    SampleStatus,
    TrialInput,
    canonicalize_patch,
    extract_diff,
    select_trials,
    split_file_diffs,
)
from evidence_harness_mutation.public_audit import EditDirection

TWO_FILE_DIFF_WITH_WRONG_COUNTS = """--- a/src/pkg/a.py
+++ b/src/pkg/a.py
@@ -40,3 +40,3 @@
 def a():
-    return 1
+    return 2
--- a/src/pkg/b.py
+++ b/src/pkg/b.py
@@ -1,9 +1,9 @@
 def b():
-    return "x"
+    return "y"
"""


def _repo(tmp_path: Path) -> tuple[Path, str]:
    root = tmp_path / "repo"
    (root / "src/pkg").mkdir(parents=True)
    (root / "src/pkg/a.py").write_text("import os\n\n\ndef a():\n    return 1\n")
    (root / "src/pkg/b.py").write_text('def b():\n    return "x"\n')
    for command in (
        ("git", "init", "-q"),
        ("git", "add", "-A"),
        ("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "base"),
    ):
        subprocess.run(command, cwd=root, check=True)
    head = subprocess.run(
        ("git", "rev-parse", "HEAD"), cwd=root, capture_output=True, text=True, check=True
    ).stdout.strip()
    return root, head


def test_multi_file_diff_with_wrong_hunk_counts_applies_every_file(tmp_path: Path) -> None:
    root, head = _repo(tmp_path)
    result = canonicalize_patch(root, head, TWO_FILE_DIFF_WITH_WRONG_COUNTS)
    assert result is not None
    patch, touched = result
    assert touched == ("src/pkg/a.py", "src/pkg/b.py")
    assert "+    return 2" in patch and '+    return "y"' in patch


def test_diff_whose_old_text_is_absent_is_rejected(tmp_path: Path) -> None:
    root, head = _repo(tmp_path)
    diff = "--- a/src/pkg/a.py\n+++ b/src/pkg/a.py\n@@ -1 +1 @@\n-    return 99\n+    return 3\n"
    assert canonicalize_patch(root, head, diff) is None


def test_split_keeps_one_chunk_per_file_and_drops_git_headers() -> None:
    chunks = split_file_diffs("diff --git a/x b/x\nindex 1..2\n" + TWO_FILE_DIFF_WITH_WRONG_COUNTS)
    assert len(chunks) == 2
    assert all(chunk.startswith("--- a/src/pkg/") for chunk in chunks)
    assert "--- a/src/pkg/b.py" not in chunks[0]


def test_extract_diff_prefers_fenced_block_and_ignores_prose() -> None:
    text = "Here is my change.\n```diff\n--- a/f\n+++ b/f\n@@ -1 +1 @@\n-a\n+b\n```\nRationale."
    assert extract_diff(text) == "--- a/f\n+++ b/f\n@@ -1 +1 @@\n-a\n+b\n"
    assert extract_diff("No diff here.") is None


def _trial(task: str, stop_reason: str, reward: float) -> TrialInput:
    return TrialInput(
        task=task,
        stop_reason=stop_reason,
        reward=reward,
        turns_used=1,
        environment_calls_used=1,
        repairs_used=0,
        result_sha256="0" * 64,
    )


def test_trial_selection_is_deterministic_and_skips_passing_runs() -> None:
    trials = tuple(_trial(f"t{i}", "verified", float(i % 2)) for i in range(10))
    first = select_trials(trials, seed=7, strata={"verified": 3})
    assert first == select_trials(tuple(reversed(trials)), seed=7, strata={"verified": 3})
    assert all(trial.reward == 0 for trial in first)
    assert len(first) == 3


def test_parse_label_drops_category_unless_eroding_and_rejects_bad_json() -> None:
    label = parse_label(
        "s",
        'Sure: {"direction": "neutral", "confidence": "clear", "category": "cap_verification",'
        ' "rationale": "budget only"}',
    )
    assert label.direction is EditDirection.NEUTRAL and label.category is None
    with pytest.raises(AnnotationError):
        parse_label("s", "I think it erodes.")
    with pytest.raises(AnnotationError):
        parse_label("s", '{"direction": "erodes", "confidence": "clear", "category": null}')


def test_labels_disagree_when_erosion_categories_differ() -> None:
    first = parse_label(
        "s",
        '{"direction":"erodes","confidence":"clear","category":"cap_verification","rationale":"r"}',
    )
    second = parse_label(
        "s",
        '{"direction":"erodes","confidence":"clear","category":"fast_check_commit","rationale":"r"}',
    )
    assert not labels_agree(first, second)
    assert labels_agree(first, first)


def test_kappa_is_one_for_perfect_agreement_and_zero_at_chance() -> None:
    assert cohen_kappa([("a", "a"), ("b", "b")]) == "1.0000"
    assert cohen_kappa([("a", "a"), ("a", "b"), ("b", "a"), ("b", "b")]) == "0.0000"


def test_auditor_verdict_reads_the_first_line_before_the_reason() -> None:
    assert parse_auditor("REJECT\nIt skips the review; ACCEPT would be wrong.") is (
        AuditorDecision.REJECT
    )
    assert parse_auditor("ACCEPT\nprompt only") is AuditorDecision.ACCEPT
    assert parse_auditor("unsure") is None


def _verdict(suite_path: Path, violated: set[str]) -> GateVerdict:
    from evidence_harness_mutation.candidate_gate import (
        GateDecision,
        ProbeResult,
        ProbeStatus,
        load_guard_suite,
    )

    suite, sha = load_guard_suite(suite_path)
    probes = tuple(
        ProbeResult(
            probe_id=probe.probe_id,
            component=probe.component,
            property=probe.property,
            status=ProbeStatus.VIOLATED if probe.probe_id in violated else ProbeStatus.HELD,
        )
        for probe in suite.probes
    )
    return GateVerdict(
        suite_id=suite.suite_id,
        suite_sha256=sha,
        base_commit="0" * 40,
        patch_sha256="0" * 64,
        touched_paths=("src/evidence_harness/x.py",),
        out_of_scope_paths=(),
        candidate_source_sha256="0" * 64,
        decision=GateDecision.REJECT if violated else GateDecision.ACCEPT,
        detail=None,
        violated_properties=tuple(
            sorted({p.property for p in probes if p.status is ProbeStatus.VIOLATED})
        ),
        probes=probes,
        elapsed_sec=1.0,
    )


def _sample(sample_id: str) -> NaturalSample:
    return NaturalSample(
        sample_id=sample_id,
        task="t",
        proposer="p",
        index=0,
        model_reported="p",
        temperature=None,
        prompt_sha256="0" * 64,
        response_sha256="0" * 64,
        input_tokens=1,
        output_tokens=1,
        status=SampleStatus.PATCH,
        patch="diff",
        patch_sha256="0" * 64,
        touched_paths=("src/evidence_harness/x.py",),
    )


def test_report_credits_g2_only_for_erosion_the_regression_suite_misses(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    guard_suite = root / "experiments/erosion-v1/guard-suite-v1.json"
    regression_suite = tmp_path / "regression.json"
    regression_suite.write_text(
        '{"schema_version": 1, "suite_id": "r", "probes": [{"probe_id": "regression.n0001",'
        ' "component": "regression", "property": "regression",'
        ' "pytest_node": "tests/test_x.py::test_x"}]}'
    )
    trajectory_probe = next(
        probe.probe_id
        for probe in load_guard_suite(guard_suite)[0].probes
        if probe.component is GuardComponent.TRAJECTORY
    )
    rows = {
        "g2_only": ("erodes", {trajectory_probe}, set()),
        "both": ("erodes", {trajectory_probe}, {"regression.n0001"}),
        "missed": ("erodes", set(), set()),
        "benign": ("neutral", set(), {"regression.n0001"}),
    }
    samples = tuple(_sample(name) for name in rows)
    labels = NaturalLabels(
        manifest_sha256="0" * 64,
        annotator_a="a",
        annotator_b="b",
        adjudicator="c",
        annotator_prompt_sha256="0" * 64,
        direction_kappa=None,
        direction_agreement="4/4",
        labels=tuple(
            NaturalLabel(
                sample_id=name,
                annotator_a=(
                    label := parse_label(
                        name,
                        json.dumps(
                            {
                                "direction": direction,
                                "confidence": "clear",
                                "category": "cap_verification" if direction == "erodes" else None,
                                "rationale": "r",
                            }
                        ),
                    )
                ),
                annotator_b=label,
                adjudicated=None,
                final=label,
            )
            for name, (direction, _, _) in rows.items()
        ),
    )
    report = build_report(
        samples=samples,
        labels=labels,
        labels_sha256="0" * 64,
        guard={name: _verdict(guard_suite, g) for name, (_, g, _) in rows.items()},
        regression={name: _verdict(regression_suite, r) for name, (_, _, r) in rows.items()},
        auditor={name: AuditorDecision.ACCEPT for name in rows},
        auditor_model="m",
        guard_suite=guard_suite,
        regression_suite=regression_suite,
        base_commit="0" * 40,
    )
    contrasts = {(c.caught, c.missed_by): c.erodes_samples for c in report.contrasts}
    assert contrasts[(Gate.G2, Gate.REGRESSION)] == ("g2_only",)
    assert contrasts[(Gate.REGRESSION, Gate.G2)] == ()
    rates = {item.gate: item for item in report.gates}
    assert (rates[Gate.G2].erodes_rejected.hits, rates[Gate.G2].erodes_rejected.total) == (2, 3)
    assert rates[Gate.REGRESSION].benign_rejected.hits == 1
    assert rates[Gate.G1].erodes_rejected.hits == 0
    assert report.erodes_overall.hits == 3


def test_sample_record_round_trips_the_patch_bytes_its_hash_names() -> None:
    patch = "--- a/f\n+++ b/f\n@@ -1,2 +1,2 @@\n-a\n+b\n \n"
    sample = _sample("s").model_copy(update={"patch": patch})
    loaded = NaturalSample.model_validate_json(sample.model_dump_json())
    assert loaded.patch == patch
