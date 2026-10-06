from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import tb21_luna_rq2_protocol


def _protocol() -> SimpleNamespace:
    return SimpleNamespace(
        protocol_id="thesis-tb21-luna-rq2-replication-v1",
        analysis=SimpleNamespace(tasks=61),
        canonical_bytes=lambda: b'{"protocol":"luna"}\n',
    )


def test_freeze_writes_the_protocol_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "protocol.json"
    monkeypatch.setattr(tb21_luna_rq2_protocol, "PROTOCOL_PATH", path)
    monkeypatch.setattr(
        tb21_luna_rq2_protocol,
        "freeze_luna_rq2_protocol",
        lambda _root: _protocol(),
    )
    monkeypatch.setattr(sys, "argv", ["tb21_luna_rq2_protocol.py", "freeze"])

    assert tb21_luna_rq2_protocol.main() == 0
    assert path.read_bytes() == b'{"protocol":"luna"}\n'
    assert "state=created" in capsys.readouterr().out

    assert tb21_luna_rq2_protocol.main() == 0
    assert "state=unchanged" in capsys.readouterr().out


def test_freeze_refuses_to_replace_different_protocol(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "protocol.json"
    path.write_text('{"protocol":"other"}\n', encoding="utf-8")
    monkeypatch.setattr(tb21_luna_rq2_protocol, "PROTOCOL_PATH", path)
    monkeypatch.setattr(
        tb21_luna_rq2_protocol,
        "freeze_luna_rq2_protocol",
        lambda _root: _protocol(),
    )
    monkeypatch.setattr(sys, "argv", ["tb21_luna_rq2_protocol.py", "freeze"])

    assert tb21_luna_rq2_protocol.main() == 2
    assert path.read_text(encoding="utf-8") == '{"protocol":"other"}\n'
    assert "refusing to replace" in capsys.readouterr().out


def test_preflight_reports_provider_execution_blocked(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = SimpleNamespace(
        ready_for_executable_freeze=True,
        ready_for_provider_execution=False,
        required_executable=(
            "experiments/prefixbench-v1/"
            "tb21-luna-rq2-replication-executable-v1.json"
        ),
        protocol=SimpleNamespace(preregistration_commit="a" * 40),
    )
    monkeypatch.setattr(
        tb21_luna_rq2_protocol,
        "preflight_luna_rq2_protocol",
        lambda _root: result,
    )
    monkeypatch.setattr(sys, "argv", ["tb21_luna_rq2_protocol.py", "preflight"])

    assert tb21_luna_rq2_protocol.main() == 0
    stdout = capsys.readouterr().out
    assert "ready_for_executable_freeze=true" in stdout
    assert "ready_for_provider_execution=false" in stdout


def test_cli_exposes_no_scientific_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["tb21_luna_rq2_protocol.py", "freeze", "--seed", "7"],
    )

    with pytest.raises(SystemExit) as exc_info:
        tb21_luna_rq2_protocol.parse_args()

    assert exc_info.value.code == 2
