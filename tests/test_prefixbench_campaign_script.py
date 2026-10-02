from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import prefixbench_campaign


def test_build_writes_canonical_report_and_prints_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "campaign.json"
    report = SimpleNamespace(
        canonical_bytes=lambda: b'{"report":"fixed"}\n',
        summary=SimpleNamespace(
            tasks=28,
            projected_attempts=39,
            verified_attempts=7,
            outcomes=SimpleNamespace(
                scheduled=168,
                mutation_not_applicable=77,
                offline_invalid=0,
                oracle_equivalent=63,
                offline_violation=28,
                other_oracle_change=0,
            ),
        ),
    )
    monkeypatch.setattr(
        prefixbench_campaign,
        "build_prefixbench_development_campaign",
        lambda _root: report,
    )
    monkeypatch.setattr(prefixbench_campaign, "REPORT_PATH", output)
    monkeypatch.setattr(sys, "argv", ["prefixbench_campaign.py", "build"])

    assert prefixbench_campaign.main() == 0
    assert output.read_bytes() == b'{"report":"fixed"}\n'
    stdout = capsys.readouterr().out
    assert "tasks=28 projected_attempts=39 verified_attempts=7 scheduled=168" in stdout
    assert "offline_violation=28" in stdout
    assert "sha256=" in stdout


def test_check_returns_nonzero_for_validation_errors(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        prefixbench_campaign,
        "check_prefixbench_development_campaign",
        lambda *_args, **_kwargs: ("campaign is stale",),
    )
    monkeypatch.setattr(sys, "argv", ["prefixbench_campaign.py", "check"])

    assert prefixbench_campaign.main() == 1
    assert capsys.readouterr().out == "campaign is stale\n"


def test_cli_exposes_no_split_or_execution_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["prefixbench_campaign.py", "build", "--split", "test"],
    )

    with pytest.raises(SystemExit) as exc_info:
        prefixbench_campaign.parse_args()

    assert exc_info.value.code == 2
