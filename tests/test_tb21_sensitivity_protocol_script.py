from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import tb21_sensitivity_protocol


def _bundle() -> SimpleNamespace:
    return SimpleNamespace(
        matrix=SimpleNamespace(
            tasks=tuple(range(61)),
            canonical_bytes=lambda: b'{"matrix":"fixed"}\n',
        ),
        protocol=SimpleNamespace(
            protocol_id="thesis-tb21-sensitivity-v1",
            canonical_bytes=lambda: b'{"protocol":"fixed"}\n',
        ),
    )


def test_freeze_writes_the_bundle_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    matrix_path = tmp_path / "matrix.json"
    protocol_path = tmp_path / "protocol.json"
    monkeypatch.setattr(tb21_sensitivity_protocol, "MATRIX_PATH", matrix_path)
    monkeypatch.setattr(tb21_sensitivity_protocol, "PROTOCOL_PATH", protocol_path)
    monkeypatch.setattr(
        tb21_sensitivity_protocol,
        "freeze_tb21_sensitivity_protocol",
        lambda *_args: _bundle(),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "tb21_sensitivity_protocol.py",
            "freeze",
            "--tb20-checkout",
            "/tb20",
            "--tb21-checkout",
            "/tb21",
        ],
    )

    assert tb21_sensitivity_protocol.main() == 0
    assert matrix_path.read_bytes() == b'{"matrix":"fixed"}\n'
    assert protocol_path.read_bytes() == b'{"protocol":"fixed"}\n'
    assert "matrix_state=created" in capsys.readouterr().out

    assert tb21_sensitivity_protocol.main() == 0
    assert "matrix_state=unchanged" in capsys.readouterr().out


def test_freeze_refuses_all_writes_if_either_artifact_differs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    matrix_path = tmp_path / "matrix.json"
    protocol_path = tmp_path / "protocol.json"
    protocol_path.write_text('{"protocol":"old"}\n', encoding="utf-8")
    monkeypatch.setattr(tb21_sensitivity_protocol, "MATRIX_PATH", matrix_path)
    monkeypatch.setattr(tb21_sensitivity_protocol, "PROTOCOL_PATH", protocol_path)
    monkeypatch.setattr(
        tb21_sensitivity_protocol,
        "freeze_tb21_sensitivity_protocol",
        lambda *_args: _bundle(),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "tb21_sensitivity_protocol.py",
            "freeze",
            "--tb20-checkout",
            "/tb20",
            "--tb21-checkout",
            "/tb21",
        ],
    )

    assert tb21_sensitivity_protocol.main() == 2
    assert not matrix_path.exists()
    assert protocol_path.read_text(encoding="utf-8") == '{"protocol":"old"}\n'
    assert "refusing to replace" in capsys.readouterr().out


def test_preflight_reports_that_provider_execution_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = SimpleNamespace(
        ready_for_executable_freeze=True,
        ready_for_provider_execution=False,
        required_executable="experiments/prefixbench-v1/tb21-sensitivity-executable-v1.json",
        protocol=SimpleNamespace(preregistration_commit="a" * 40),
    )
    monkeypatch.setattr(
        tb21_sensitivity_protocol,
        "preflight_tb21_sensitivity_protocol",
        lambda *_args: result,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "tb21_sensitivity_protocol.py",
            "preflight",
            "--tb20-checkout",
            "/tb20",
            "--tb21-checkout",
            "/tb21",
        ],
    )

    assert tb21_sensitivity_protocol.main() == 0
    stdout = capsys.readouterr().out
    assert "ready_for_executable_freeze=true" in stdout
    assert "ready_for_provider_execution=false" in stdout


def test_check_returns_one_for_validation_errors(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        tb21_sensitivity_protocol,
        "check_tb21_sensitivity_protocol",
        lambda _root: ("protocol is stale",),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["tb21_sensitivity_protocol.py", "check"],
    )

    assert tb21_sensitivity_protocol.main() == 1
    assert capsys.readouterr().out == "protocol is stale\n"


def test_cli_exposes_no_scientific_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "tb21_sensitivity_protocol.py",
            "freeze",
            "--tb20-checkout",
            "/tb20",
            "--tb21-checkout",
            "/tb21",
            "--seed",
            "7",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        tb21_sensitivity_protocol.parse_args()

    assert exc_info.value.code == 2
