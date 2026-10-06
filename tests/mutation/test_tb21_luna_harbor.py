from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from evidence_harness_mutation import _tb21_luna_harbor as harbor
from evidence_harness_mutation._tb21_manifest import RegistryMember, Tb21TaskKey
from evidence_harness_mutation.prefixbench import PrefixBenchFileBinding


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def _binding(path: str, data: bytes) -> PrefixBenchFileBinding:
    return PrefixBenchFileBinding(
        path=path,
        bytes=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
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
