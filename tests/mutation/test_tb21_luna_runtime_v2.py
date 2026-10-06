from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from evidence_harness_mutation import tb21_luna_runtime_v2 as runtime


def test_prepare_strips_provider_environment_and_restores_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "credential-marker")
    monkeypatch.setenv("OPENAI_BASE_URL", "endpoint-marker")
    monkeypatch.setenv("OTHER_API_KEY", "other-marker")
    prepared = cast(Any, SimpleNamespace(root=tmp_path / "runtime"))

    monkeypatch.setattr(runtime.base, "_git_project_root", lambda _root: tmp_path)
    monkeypatch.setattr(runtime, "load_manifest", lambda _root: object())
    monkeypatch.setattr(runtime.base, "_load_spec", lambda _root: (object(), b"spec"))
    monkeypatch.setattr(runtime, "_read_inputs", lambda *_args: object())

    def ensure(*_args: object) -> object:
        assert "OPENAI_API_KEY" not in os.environ
        assert "OPENAI_BASE_URL" not in os.environ
        assert "OTHER_API_KEY" not in os.environ
        return prepared

    monkeypatch.setattr(runtime.base, "_ensure_runtime", ensure)

    assert runtime.prepare_luna_runtime(tmp_path, cache_root=tmp_path / "cache") is prepared
    assert os.environ["OPENAI_API_KEY"] == "credential-marker"
    assert os.environ["OPENAI_BASE_URL"] == "endpoint-marker"
    assert os.environ["OTHER_API_KEY"] == "other-marker"


def test_preflight_environment_contains_no_provider_variables(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "credential-marker")
    monkeypatch.setenv("OPENAI_BASE_URL", "endpoint-marker")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "other-marker")

    environment = runtime._preflight_environment(tmp_path)

    assert not any(runtime._is_provider_variable(key) for key in environment)


def test_collection_environment_keeps_only_allowlisted_provider_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runtime,
        "provider_environment",
        lambda _environment: (
            {
                "PATH": "/usr/bin",
                "OPENAI_API_KEY": "credential-marker",
                "OPENAI_BASE_URL": "endpoint-marker",
                "PYTHONPATH": "/untrusted",
            },
            object(),
        ),
    )

    environment = runtime._collection_environment(tmp_path)

    assert environment["OPENAI_API_KEY"] == "credential-marker"
    assert environment["OPENAI_BASE_URL"] == "endpoint-marker"
    assert "PYTHONPATH" not in environment
    assert environment["PATH"].split(os.pathsep)[0] == str(tmp_path / "venv" / "bin")


def test_frozen_preflight_invokes_the_installed_wheel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = tmp_path / "tb21"
    checkout.mkdir()
    commit = "a" * 40
    prepared = cast(
        Any,
        SimpleNamespace(
            root=tmp_path / "runtime",
            python=tmp_path / "runtime" / "venv" / "bin" / "python",
            receipt=SimpleNamespace(
                inputs=SimpleNamespace(project_commit=commit),
                runtime_id="b" * 64,
            ),
        ),
    )
    expected = f"ready_for_provider_execution=true executable_commit={commit}\n"

    monkeypatch.setattr(runtime, "_validated_paths", lambda *_args: (tmp_path, checkout))
    monkeypatch.setattr(runtime, "prepare_luna_runtime", lambda *_args, **_kwargs: prepared)

    def run(
        command: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        assert command == [
            str(prepared.python),
            "-I",
            "-m",
            "evidence_harness_mutation.tb21_luna_rq2_v2",
            "preflight",
            "--tb21-checkout",
            str(checkout),
        ]
        environment = cast(dict[str, str], kwargs["env"])
        assert not any(runtime._is_provider_variable(key) for key in environment)
        return subprocess.CompletedProcess(command, 0, expected, "")

    monkeypatch.setattr(runtime.base, "_run", run)

    result = runtime.run_frozen_preflight(tmp_path, checkout)

    assert result.runtime is prepared
    assert result.stdout == expected


@pytest.mark.parametrize("option", ["--env-file", "--model", "--seed", "--task"])
def test_runtime_cli_exposes_no_mutable_run_options(option: str) -> None:
    with pytest.raises(SystemExit) as exc_info:
        runtime._parse_args(
            [
                "collect",
                "--tb21-checkout",
                "/tmp/tb21",
                option,
                "changed",
            ]
        )

    assert exc_info.value.code == 2
