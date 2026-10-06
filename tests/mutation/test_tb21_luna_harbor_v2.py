from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from evidence_harness_mutation import _tb21_luna_harbor_v2 as harbor
from evidence_harness_mutation._tb21_manifest import (
    PlannedTask,
    RegistryMember,
    Tb21TaskKey,
)
from evidence_harness_mutation.prefixbench import PrefixBenchFileBinding
from evidence_harness_mutation.tb21_luna_rq2_protocol_v2 import LUNA_RUN_ROOT


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _binding(path: str, data: bytes) -> PrefixBenchFileBinding:
    return PrefixBenchFileBinding(
        path=path,
        bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def _task() -> PlannedTask:
    return PlannedTask(
        ordinal=1,
        key=Tb21TaskKey(source_identity_sha256=_sha("source")),
        member=RegistryMember(
            registry_name="terminal-bench/example",
            package_digest="sha256:" + _sha("package"),
        ),
        task_tree=hashlib.sha1(b"tree").hexdigest(),
        invocation_sha256=_sha("invocation"),
    )


def _terminal(result: bytes, config: bytes) -> harbor.TerminalReceipt:
    return harbor.TerminalReceipt(
        task=Tb21TaskKey(source_identity_sha256=_sha("source")),
        member=RegistryMember(
            registry_name="terminal-bench/example",
            package_digest="sha256:" + _sha("package"),
        ),
        ordinal=1,
        attempt=1,
        command_sha256=_sha("command"),
        status="passed",
        reward=1.0,
        exception_type=None,
        result=_binding("result.json", result),
        config=_binding("config.json", config),
        journal=None,
    )


def _attempt(root: Path, task: PlannedTask) -> Path:
    return (
        root
        / "tasks"
        / f"{task.ordinal:03d}-{task.key.source_identity_sha256[:12]}"
        / "attempt-001"
    )


def _write_launch(attempt: Path, task: PlannedTask) -> None:
    intent = harbor.IntentReceipt(
        task=task.key,
        member=task.member,
        ordinal=task.ordinal,
        attempt=1,
        invocation_sha256=task.invocation_sha256,
        command_sha256=_sha("command"),
    )
    harbor.write_receipt(attempt / "intent.json", intent)
    harbor.write_receipt(
        attempt / "launch.json",
        harbor.LaunchReceipt(
            task=task.key,
            attempt=1,
            command_sha256=intent.command_sha256,
            pid=999_999_999,
            process_group_id=999_999_999,
            started_at="2026-10-07T00:00:00+00:00",
        ),
    )
    (attempt / "process.lease").write_text("", encoding="ascii")


def test_provider_environment_is_allowlisted_and_secretless(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = "https://example.invalid/v1"
    monkeypatch.setattr(
        harbor,
        "PROVIDER_ENDPOINT_SHA256",
        hashlib.sha256(endpoint.encode()).hexdigest(),
    )
    environment = {
        "PATH": "/usr/bin",
        "OPENAI_API_KEY": "test-key-value",
        "OPENAI_BASE_URL": endpoint,
        "OTHER_API_KEY": "remove-me",
        "ANTHROPIC_API_KEY": "remove-me-too",
    }

    child, attestation = harbor.provider_environment(environment)
    serialized = attestation.canonical_bytes()

    assert child["OPENAI_API_KEY"] == "test-key-value"
    assert child["OPENAI_BASE_URL"] == endpoint
    assert "OTHER_API_KEY" not in child
    assert "ANTHROPIC_API_KEY" not in child
    assert b"test-key-value" not in serialized
    assert endpoint.encode() not in serialized
    assert attestation.values == "omitted"


def test_harbor_child_uses_only_the_executable_project_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint = "https://example.invalid/v1"
    (tmp_path / "src").mkdir()
    monkeypatch.setattr(
        harbor,
        "PROVIDER_ENDPOINT_SHA256",
        hashlib.sha256(endpoint.encode()).hexdigest(),
    )

    child, _attestation = harbor._harbor_child_environment(
        tmp_path,
        {
            "PATH": "/usr/bin",
            "PYTHONHOME": "/untrusted/home",
            "PYTHONPATH": "/untrusted/source",
            "OPENAI_API_KEY": "test-key-value",
            "OPENAI_BASE_URL": endpoint,
        },
    )

    assert child["PYTHONPATH"] == (tmp_path / "src").resolve().as_posix()
    assert "PYTHONHOME" not in child
    assert child["PYTHONNOUSERSITE"] == "1"


def test_live_and_recovery_ignore_the_job_aggregate_result(tmp_path: Path) -> None:
    task = _task()
    run_root = tmp_path / LUNA_RUN_ROOT
    attempt = _attempt(run_root, task)
    trial = attempt / "harbor" / "run" / "trial"
    trial.mkdir(parents=True)
    _write_launch(attempt, task)
    model = "openai/modelhub/gpt-5.6-luna"
    config = {
        "agent": {"model_name": model},
        "task": {
            "name": task.member.registry_name,
            "ref": task.member.package_digest,
        },
    }
    result = {
        "task_id": {
            "org": "terminal-bench",
            "name": "example",
            "ref": task.member.package_digest,
        },
        "config": config,
        "agent_info": {
            "model_info": {
                "provider": "openai",
                "name": "modelhub/gpt-5.6-luna",
            }
        },
        "agent_result": {"model_usage": {model: {"input_tokens": 1}}},
        "verifier_result": {"rewards": {"reward": 1}},
    }
    (attempt / "harbor" / "run" / "result.json").write_text(
        '{"aggregate": true}\n',
        encoding="utf-8",
    )
    (trial / "config.json").write_text(json.dumps(config), encoding="utf-8")
    (trial / "result.json").write_text(json.dumps(result), encoding="utf-8")

    live = harbor._terminal_from_luna_harbor(
        attempt,
        task,
        1,
        _sha("command"),
        project_root=tmp_path,
    )
    recovered = harbor.fold_luna_task_receipts(tmp_path, run_root, task)

    assert live is not None
    assert live.result.path.endswith("/run/trial/result.json")
    assert recovered.status == "terminal"
    assert recovered.terminal == live
    assert (attempt / "model-proof.json").is_file()


def test_multiple_trial_results_are_rejected_even_with_one_aggregate(tmp_path: Path) -> None:
    attempt = tmp_path / "attempt"
    job = attempt / "harbor" / "run"
    for name in ("trial-a", "trial-b"):
        trial = job / name
        trial.mkdir(parents=True)
        (trial / "result.json").write_text("{}\n", encoding="utf-8")
    (job / "result.json").write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="multiple Harbor trial results"):
        harbor._trial_result_path(attempt)


def test_model_proof_requires_all_harbor_model_locations(tmp_path: Path) -> None:
    model = "openai/modelhub/gpt-5.6-luna"
    config = json.dumps({"agent": {"model_name": model}}).encode()
    result = json.dumps(
        {
            "config": {"agent": {"model_name": model}},
            "agent_info": {
                "model_info": {
                    "provider": "openai",
                    "name": "modelhub/gpt-5.6-luna",
                }
            },
            "agent_result": {"model_usage": {model: {"input_tokens": 1}}},
        }
    ).encode()
    (tmp_path / "config.json").write_bytes(config)
    (tmp_path / "result.json").write_bytes(result)

    proof = harbor._model_proof(tmp_path, _terminal(result, config))

    assert proof.saved_config_model == model
    assert proof.result_config_model == model
    assert proof.model_usage_keys == (model,)

    changed = json.loads(result)
    changed["agent_info"]["model_info"]["name"] = "modelhub/gpt-5.6-terra"
    changed_data = json.dumps(changed).encode()
    (tmp_path / "result.json").write_bytes(changed_data)
    with pytest.raises(ValueError, match="does not prove"):
        harbor._model_proof(tmp_path, _terminal(changed_data, config))


def test_persisted_provider_value_is_rejected(tmp_path: Path) -> None:
    logs = tmp_path / "harbor" / "run"
    logs.mkdir(parents=True)
    (logs / "job.log").write_bytes(b"prefix credential-marker suffix")

    with pytest.raises(ValueError, match="persisted"):
        harbor._reject_persisted_provider_values(
            tmp_path,
            ("credential-marker", "endpoint-marker"),
        )
