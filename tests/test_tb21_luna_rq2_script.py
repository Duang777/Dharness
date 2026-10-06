from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import tb21_luna_rq2


def test_freeze_writes_manifest_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest = SimpleNamespace(
        executable_id="thesis-tb21-luna-rq2-replication-executable-v1",
        harbor=SimpleNamespace(expected_member_count=61),
        canonical_bytes=lambda: b'{"executable":"luna"}\n',
    )
    monkeypatch.setattr(tb21_luna_rq2, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(tb21_luna_rq2, "freeze", lambda _root: manifest)
    monkeypatch.setattr(sys, "argv", ["tb21_luna_rq2.py", "freeze"])

    assert tb21_luna_rq2.main() == 0
    assert (
        tmp_path / "experiments/prefixbench-v1/tb21-luna-rq2-replication-executable-v1.json"
    ).read_bytes() == b'{"executable":"luna"}\n'
    assert "state=created" in capsys.readouterr().out

    assert tb21_luna_rq2.main() == 0
    assert "state=unchanged" in capsys.readouterr().out


def test_collection_uses_no_credential_file_option(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "tb21_luna_rq2.py",
            "collect",
            "--tb21-checkout",
            "/tmp/tb21",
            "--env-file",
            "/tmp/credentials",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        tb21_luna_rq2.parse_args()

    assert exc_info.value.code == 2


@pytest.mark.parametrize(
    "option",
    [
        "--seed",
        "--task",
        "--model",
        "--method",
        "--bootstrap-resamples",
        "--output",
        "--start-at",
    ],
)
def test_cli_exposes_no_scientific_or_resume_options(
    option: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["tb21_luna_rq2.py", "build", option, "changed"],
    )

    with pytest.raises(SystemExit) as exc_info:
        tb21_luna_rq2.parse_args()

    assert exc_info.value.code == 2


def test_blocked_preflight_has_a_distinct_exit_code(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = SimpleNamespace(
        ready_for_provider_execution=False,
        executable_commit="a" * 40,
        blockers=(SimpleNamespace(code="selector_resolution_failed", message="blocked"),),
    )
    monkeypatch.setattr(tb21_luna_rq2, "preflight", lambda *_args, **_kwargs: result)
    monkeypatch.setattr(
        sys,
        "argv",
        ["tb21_luna_rq2.py", "preflight", "--tb21-checkout", "/tmp/tb21"],
    )

    assert tb21_luna_rq2.main() == 1
    assert "ready_for_provider_execution=false" in capsys.readouterr().out
