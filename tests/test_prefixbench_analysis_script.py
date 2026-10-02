from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import prefixbench_analysis


def test_build_writes_canonical_analysis_and_prints_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "analysis.json"
    report = SimpleNamespace(
        canonical_bytes=lambda: b'{"analysis":"fixed"}\n',
        tasks=SimpleNamespace(
            tasks=28,
            projected_attempts=39,
            verified_attempts_of_projected=SimpleNamespace(count=7),
        ),
        cases=SimpleNamespace(
            scheduled_cases=168,
            applicable_cases=SimpleNamespace(count=91),
            mutation_not_applicable_cases=SimpleNamespace(count=77),
            offline_invalid_cases=SimpleNamespace(count=0),
            oracle_equivalent_cases=SimpleNamespace(count=63),
            offline_violations=SimpleNamespace(count=28),
            other_oracle_changes=SimpleNamespace(count=0),
        ),
        reduction=SimpleNamespace(
            events=SimpleNamespace(before=1_996, after=230),
            recursive_payload_members=SimpleNamespace(before=35_582, after=6_465),
            compact_canonical_json_bytes=SimpleNamespace(
                before=4_044_814,
                after=716_196,
            ),
        ),
    )
    monkeypatch.setattr(
        prefixbench_analysis,
        "build_prefixbench_development_analysis",
        lambda _root: report,
    )
    monkeypatch.setattr(prefixbench_analysis, "REPORT_PATH", output)
    monkeypatch.setattr(sys, "argv", ["prefixbench_analysis.py", "build"])

    assert prefixbench_analysis.main() == 0
    assert output.read_bytes() == b'{"analysis":"fixed"}\n'
    stdout = capsys.readouterr().out
    assert "tasks=28 projected_attempts=39 verified_attempts=7 scheduled=168" in stdout
    assert "applicable=91 mutation_not_applicable=77" in stdout
    assert "offline_violation=28" in stdout
    assert "reduced_events=1996->230" in stdout
    assert "sha256=" in stdout


def test_check_returns_nonzero_for_validation_errors(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        prefixbench_analysis,
        "check_prefixbench_development_analysis",
        lambda *_args, **_kwargs: ("analysis is stale",),
    )
    monkeypatch.setattr(sys, "argv", ["prefixbench_analysis.py", "check"])

    assert prefixbench_analysis.main() == 1
    assert capsys.readouterr().out == "analysis is stale\n"


@pytest.mark.parametrize(
    "option",
    (
        ("--split", "test"),
        ("--campaign", "other.json"),
        ("--model", "model"),
        ("--output", "other.json"),
    ),
)
def test_cli_exposes_no_selection_or_execution_options(
    option: tuple[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["prefixbench_analysis.py", "build", *option],
    )

    with pytest.raises(SystemExit) as exc_info:
        prefixbench_analysis.parse_args()

    assert exc_info.value.code == 2
