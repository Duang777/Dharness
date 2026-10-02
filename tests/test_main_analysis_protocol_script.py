from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import main_analysis_protocol


def test_freeze_writes_protocol_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "protocol.json"
    frozen = SimpleNamespace(
        protocol_id="thesis-main-analysis-v1",
        canonical_bytes=lambda: b'{"protocol":"fixed"}\n',
    )
    monkeypatch.setattr(
        main_analysis_protocol,
        "freeze_main_analysis_protocol",
        lambda _root: frozen,
    )
    monkeypatch.setattr(main_analysis_protocol, "PROTOCOL_PATH", output)
    monkeypatch.setattr(sys, "argv", ["main_analysis_protocol.py", "freeze"])

    assert main_analysis_protocol.main() == 0
    assert output.read_bytes() == b'{"protocol":"fixed"}\n'
    assert "state=created" in capsys.readouterr().out

    assert main_analysis_protocol.main() == 0
    assert "state=unchanged" in capsys.readouterr().out


def test_freeze_refuses_to_replace_a_different_protocol(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    output = tmp_path / "protocol.json"
    output.write_text('{"protocol":"old"}\n', encoding="utf-8")
    frozen = SimpleNamespace(
        protocol_id="thesis-main-analysis-v1",
        canonical_bytes=lambda: b'{"protocol":"new"}\n',
    )
    monkeypatch.setattr(
        main_analysis_protocol,
        "freeze_main_analysis_protocol",
        lambda _root: frozen,
    )
    monkeypatch.setattr(main_analysis_protocol, "PROTOCOL_PATH", output)
    monkeypatch.setattr(sys, "argv", ["main_analysis_protocol.py", "freeze"])

    assert main_analysis_protocol.main() == 2
    assert output.read_text(encoding="utf-8") == '{"protocol":"old"}\n'
    assert "refusing to replace" in capsys.readouterr().out


def test_preflight_prints_protocol_identity(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = SimpleNamespace(
        ready_for_held_out_collection=True,
        protocol=SimpleNamespace(
            preregistration_commit="a" * 40,
            file=SimpleNamespace(sha256="b" * 64),
        ),
        protected_source_sha256="c" * 64,
        protocol_source_sha256="d" * 64,
    )
    monkeypatch.setattr(
        main_analysis_protocol,
        "preflight_main_analysis_protocol",
        lambda _root: result,
    )
    monkeypatch.setattr(sys, "argv", ["main_analysis_protocol.py", "preflight"])

    assert main_analysis_protocol.main() == 0
    stdout = capsys.readouterr().out
    assert "ready_for_held_out_collection=true" in stdout
    assert f"protocol_commit={'a' * 40}" in stdout
    assert f"protocol_sha256={'b' * 64}" in stdout


def test_check_returns_one_for_validation_errors(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        main_analysis_protocol,
        "check_main_analysis_protocol",
        lambda _root: ("protocol is stale",),
    )
    monkeypatch.setattr(sys, "argv", ["main_analysis_protocol.py", "check"])

    assert main_analysis_protocol.main() == 1
    assert capsys.readouterr().out == "protocol is stale\n"


def test_cli_exposes_no_scientific_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["main_analysis_protocol.py", "freeze", "--seed", "7"],
    )

    with pytest.raises(SystemExit) as exc_info:
        main_analysis_protocol.parse_args()

    assert exc_info.value.code == 2
