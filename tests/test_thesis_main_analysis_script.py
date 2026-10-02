from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import thesis_main_analysis


def test_freeze_executable_writes_cohort_and_manifest_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    cohort = SimpleNamespace(
        tasks=tuple(range(20)),
        canonical_bytes=lambda: b'{"cohort":"fixed"}\n',
    )
    executable = SimpleNamespace(
        executable_id="thesis-main-analysis-executable-v1",
        canonical_bytes=lambda: b'{"executable":"fixed"}\n',
    )
    monkeypatch.setattr(thesis_main_analysis, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        thesis_main_analysis,
        "freeze_main_analysis_executable",
        lambda *_args, **_kwargs: (cohort, executable),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "thesis_main_analysis.py",
            "freeze-executable",
            "--mini-swe-checkout",
            "/mini",
            "--programbench-checkout",
            "/programbench",
        ],
    )

    assert thesis_main_analysis.main() == 0
    assert (
        tmp_path / "experiments/prefixbench-v1/miniswe-cohort-v1.json"
    ).read_bytes() == b'{"cohort":"fixed"}\n'
    assert (
        tmp_path / "experiments/prefixbench-v1/main-analysis-executable-v1.json"
    ).read_bytes() == b'{"executable":"fixed"}\n'
    assert "state=created" in capsys.readouterr().out

    assert thesis_main_analysis.main() == 0
    assert "state=unchanged" in capsys.readouterr().out


def test_build_dispatch_writes_only_the_fixed_report_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    report = SimpleNamespace(
        report_id="prefixbench-v1-test-method-comparison",
        canonical_bytes=lambda: b'{"report":"rq2"}\n',
    )
    monkeypatch.setattr(thesis_main_analysis, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(
        thesis_main_analysis,
        "build_rq2_report",
        lambda _root: report,
    )
    monkeypatch.setattr(sys, "argv", ["thesis_main_analysis.py", "build-rq2"])

    assert thesis_main_analysis.main() == 0
    assert (
        tmp_path / "evaluation/prefixbench-v1-test-method-comparison.json"
    ).read_bytes() == b'{"report":"rq2"}\n'
    assert "report=prefixbench-v1-test-method-comparison" in capsys.readouterr().out


def test_check_returns_one_for_validation_errors(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        thesis_main_analysis,
        "check_rq3_report",
        lambda _root: ("RQ3 report is stale",),
    )
    monkeypatch.setattr(sys, "argv", ["thesis_main_analysis.py", "check-rq3"])

    assert thesis_main_analysis.main() == 1
    assert capsys.readouterr().out == "RQ3 report is stale\n"


@pytest.mark.parametrize(
    "option",
    [
        "--seed",
        "--task",
        "--method",
        "--operator",
        "--reducer",
        "--bootstrap-resamples",
        "--output",
    ],
)
def test_cli_exposes_no_scientific_or_output_options(
    option: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["thesis_main_analysis.py", "build-rq2", option, "changed"],
    )

    with pytest.raises(SystemExit) as exc_info:
        thesis_main_analysis.parse_args()

    assert exc_info.value.code == 2


def test_transfer_collection_requires_an_env_file_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "thesis_main_analysis.py",
            "transfer-collect",
            "--mini-swe-checkout",
            "/mini",
            "--programbench-checkout",
            "/programbench",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        thesis_main_analysis.parse_args()

    assert exc_info.value.code == 2
