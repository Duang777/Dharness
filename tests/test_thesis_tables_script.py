from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from scripts import thesis_tables


def test_build_prints_both_fixed_output_hashes(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        thesis_tables,
        "build_thesis_tables",
        lambda _root: SimpleNamespace(
            bundle_state="created",
            markdown_state="created",
            bundle_sha256="a" * 64,
            markdown_sha256="b" * 64,
        ),
    )
    monkeypatch.setattr(sys, "argv", ["thesis_tables.py", "build"])

    assert thesis_tables.main() == 0
    assert capsys.readouterr().out == (
        "bundle_state=created markdown_state=created\n"
        f"bundle_sha256={'a' * 64}\n"
        f"markdown_sha256={'b' * 64}\n"
    )


def test_check_returns_one_for_validation_errors(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        thesis_tables,
        "check_thesis_tables",
        lambda _root: ("table bundle is stale",),
    )
    monkeypatch.setattr(sys, "argv", ["thesis_tables.py", "check"])

    assert thesis_tables.main() == 1
    assert capsys.readouterr().out == "table bundle is stale\n"


@pytest.mark.parametrize(
    "option",
    [
        "--input",
        "--output",
        "--rq",
        "--filter",
        "--precision",
        "--model",
        "--env-file",
    ],
)
def test_cli_exposes_no_path_or_scientific_options(
    option: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["thesis_tables.py", "build", option, "changed"],
    )

    with pytest.raises(SystemExit) as exc_info:
        thesis_tables.parse_args()

    assert exc_info.value.code == 2
