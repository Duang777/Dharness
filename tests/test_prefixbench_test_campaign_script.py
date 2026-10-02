from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import prefixbench_test_campaign


def test_freeze_writes_protocol_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "protocol.json"
    protocol = SimpleNamespace(
        protocol_id="prefixbench-v1-test-offline-mutation-v1",
        task_count=61,
        canonical_bytes=lambda: b'{"protocol":"fixed"}\n',
    )
    monkeypatch.setattr(
        prefixbench_test_campaign,
        "freeze_prefixbench_test_protocol",
        lambda _root: protocol,
    )
    monkeypatch.setattr(prefixbench_test_campaign, "PROTOCOL_PATH", output)
    monkeypatch.setattr(sys, "argv", ["prefixbench_test_campaign.py", "freeze"])

    assert prefixbench_test_campaign.main() == 0
    assert output.read_bytes() == b'{"protocol":"fixed"}\n'
    assert "state=created" in capsys.readouterr().out

    assert prefixbench_test_campaign.main() == 0
    assert "state=unchanged" in capsys.readouterr().out


def test_freeze_refuses_to_replace_protocol(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "protocol.json"
    output.write_text('{"protocol":"old"}\n', encoding="utf-8")
    protocol = SimpleNamespace(
        protocol_id="prefixbench-v1-test-offline-mutation-v1",
        task_count=61,
        canonical_bytes=lambda: b'{"protocol":"new"}\n',
    )
    monkeypatch.setattr(
        prefixbench_test_campaign,
        "freeze_prefixbench_test_protocol",
        lambda _root: protocol,
    )
    monkeypatch.setattr(prefixbench_test_campaign, "PROTOCOL_PATH", output)
    monkeypatch.setattr(sys, "argv", ["prefixbench_test_campaign.py", "freeze"])

    assert prefixbench_test_campaign.main() == 2
    assert output.read_text(encoding="utf-8") == '{"protocol":"old"}\n'
    assert "refusing to replace" in capsys.readouterr().out


def test_preflight_prints_frozen_identity(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = SimpleNamespace(
        ready_for_collection=True,
        task_count=61,
        collection_run_root="runs/terminal-bench-2/prefixbench-v1-test-20261002",
        runtime_source=SimpleNamespace(sha256="a" * 64),
        protocol=SimpleNamespace(
            preregistration_commit="b" * 40,
            file=SimpleNamespace(sha256="c" * 64),
        ),
    )
    monkeypatch.setattr(
        prefixbench_test_campaign,
        "preflight_prefixbench_test_campaign",
        lambda _root: result,
    )
    monkeypatch.setattr(sys, "argv", ["prefixbench_test_campaign.py", "preflight"])

    assert prefixbench_test_campaign.main() == 0
    stdout = capsys.readouterr().out
    assert "ready_for_collection=true tasks=61" in stdout
    assert f"producer_commit={'b' * 40}" in stdout


def test_build_writes_canonical_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "campaign.json"
    report = SimpleNamespace(
        canonical_bytes=lambda: b'{"campaign":"fixed"}\n',
        summary=SimpleNamespace(
            tasks=61,
            projected_attempts=82,
            verified_attempts=17,
            outcomes=SimpleNamespace(
                scheduled=400,
                mutation_not_applicable=100,
                offline_invalid=1,
                oracle_equivalent=200,
                offline_violation=90,
                other_oracle_change=9,
            ),
        ),
    )
    monkeypatch.setattr(
        prefixbench_test_campaign,
        "build_prefixbench_test_campaign",
        lambda _root: report,
    )
    monkeypatch.setattr(prefixbench_test_campaign, "REPORT_PATH", output)
    monkeypatch.setattr(sys, "argv", ["prefixbench_test_campaign.py", "build"])

    assert prefixbench_test_campaign.main() == 0
    assert output.read_bytes() == b'{"campaign":"fixed"}\n'
    stdout = capsys.readouterr().out
    assert "tasks=61 projected_attempts=82 verified_attempts=17 scheduled=400" in stdout
    assert "offline_violation=90" in stdout


def test_check_returns_one_for_validation_errors(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        prefixbench_test_campaign,
        "check_prefixbench_test_campaign",
        lambda *_args, **_kwargs: ("campaign is stale",),
    )
    monkeypatch.setattr(sys, "argv", ["prefixbench_test_campaign.py", "check"])

    assert prefixbench_test_campaign.main() == 1
    assert capsys.readouterr().out == "campaign is stale\n"


def test_cli_exposes_no_campaign_policy_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["prefixbench_test_campaign.py", "build", "--retry-status", "error"],
    )

    with pytest.raises(SystemExit) as exc_info:
        prefixbench_test_campaign.parse_args()

    assert exc_info.value.code == 2
