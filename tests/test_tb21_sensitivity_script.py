from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import tb21_sensitivity


def test_freeze_writes_manifest_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    manifest = SimpleNamespace(
        executable_id="thesis-tb21-sensitivity-executable-v1",
        harbor=SimpleNamespace(expected_member_count=61),
        canonical_bytes=lambda: b'{"executable":"fixed"}\n',
    )
    monkeypatch.setattr(tb21_sensitivity, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(tb21_sensitivity, "freeze", lambda _root: manifest)
    monkeypatch.setattr(sys, "argv", ["tb21_sensitivity.py", "freeze"])

    assert tb21_sensitivity.main() == 0
    assert (
        tmp_path / "experiments/prefixbench-v1/tb21-sensitivity-executable-v1.json"
    ).read_bytes() == b'{"executable":"fixed"}\n'
    assert "state=created" in capsys.readouterr().out

    assert tb21_sensitivity.main() == 0
    assert "state=unchanged" in capsys.readouterr().out


def test_blocked_preflight_has_a_distinct_exit_code(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = SimpleNamespace(
        ready_for_provider_execution=False,
        executable_commit="a" * 40,
        blockers=(
            SimpleNamespace(
                code="repo_dataset_toml_not_resolved",
                message="unsupported manifest",
                harbor_version="0.23.0",
                observed_registry_path="tasks/dataset.toml/registry.json",
            ),
        ),
    )
    monkeypatch.setattr(tb21_sensitivity, "preflight", lambda *_args, **_kwargs: result)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "tb21_sensitivity.py",
            "preflight",
            "--tb21-checkout",
            "/tmp/tb21",
        ],
    )

    assert tb21_sensitivity.main() == 1
    output = capsys.readouterr().out
    assert "ready_for_provider_execution=false" in output
    assert "blocker=repo_dataset_toml_not_resolved" in output


def test_bundle_writer_validates_every_target_before_writing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tb21_sensitivity, "PROJECT_ROOT", tmp_path)
    second = tmp_path / "evaluation" / "second.json"
    second.parent.mkdir(parents=True)
    second.write_bytes(b"different\n")

    with pytest.raises(ValueError, match="refusing to replace"):
        tb21_sensitivity._write_bundle(
            (
                (Path("evaluation/first.json"), b"first\n"),
                (Path("evaluation/second.json"), b"second\n"),
            )
        )

    assert not (tmp_path / "evaluation" / "first.json").exists()


@pytest.mark.parametrize(
    "option",
    [
        "--seed",
        "--task",
        "--method",
        "--operator",
        "--bootstrap-resamples",
        "--output",
        "--start-at",
    ],
)
def test_cli_exposes_no_scientific_or_resume_selection_options(
    option: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["tb21_sensitivity.py", "build", option, "changed"],
    )

    with pytest.raises(SystemExit) as exc_info:
        tb21_sensitivity.parse_args()

    assert exc_info.value.code == 2


def test_collection_requires_both_checkout_and_env_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["tb21_sensitivity.py", "collect", "--tb21-checkout", "/tmp/tb21"],
    )

    with pytest.raises(SystemExit) as exc_info:
        tb21_sensitivity.parse_args()

    assert exc_info.value.code == 2
