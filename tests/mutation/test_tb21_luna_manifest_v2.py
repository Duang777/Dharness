from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from evidence_harness_mutation._tb21_luna_manifest_v2 import (
    IMPLEMENTATION_PATHS,
    IMPORTED_KERNEL_PATHS,
    MODEL,
    OPERATIONAL_INPUT_PATHS,
    ExecutableManifest,
    FrozenRunPlan,
    PlannedTask,
    RandomDomains,
    RegistryMember,
    Tb21TaskKey,
    invocation_command,
    require_preoutcome_state,
)
from evidence_harness_mutation.tb21_luna_rq2_protocol_v2 import LUNA_EXECUTABLE

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(("git", "-C", str(root), *arguments), check=True, capture_output=True)


def _task(ordinal: int) -> PlannedTask:
    identity = hashlib.sha256(f"source-{ordinal}".encode()).hexdigest()
    return PlannedTask(
        ordinal=ordinal,
        key=Tb21TaskKey(source_identity_sha256=identity),
        member=RegistryMember(
            registry_name=f"terminal-bench/task-{ordinal}",
            package_digest="sha256:" + hashlib.sha256(f"package-{ordinal}".encode()).hexdigest(),
        ),
        task_tree=hashlib.sha1(f"tree-{ordinal}".encode()).hexdigest(),
        invocation_sha256=hashlib.sha256(f"invocation-{ordinal}".encode()).hexdigest(),
    )


def test_executable_bytes_are_canonical_and_bind_every_source_group() -> None:
    data = (PROJECT_ROOT / LUNA_EXECUTABLE).read_bytes()
    manifest = ExecutableManifest.model_validate_json(data)

    assert data == manifest.canonical_bytes()
    assert tuple(item.path for item in manifest.implementation_sources.files) == (
        IMPLEMENTATION_PATHS
    )
    assert tuple(item.path for item in manifest.imported_kernel_sources.files) == (
        IMPORTED_KERNEL_PATHS
    )
    assert tuple(item.path for item in manifest.operational_inputs.files) == (
        OPERATIONAL_INPUT_PATHS
    )


def test_run_plan_and_invocation_are_luna_scoped() -> None:
    plan = FrozenRunPlan(tasks=tuple(_task(ordinal) for ordinal in range(1, 62)))
    command = invocation_command(plan.tasks[0])

    assert len(plan.tasks) == 61
    assert command[command.index("--model") + 1] == MODEL
    assert "--env-file" not in command
    assert "gpt-5.6-terra" not in command

    tasks = list(plan.tasks)
    tasks[1] = tasks[1].model_copy(update={"ordinal": 3})
    with pytest.raises(ValidationError, match="ordinals must be contiguous"):
        FrozenRunPlan(tasks=tuple(tasks))


def test_random_domains_are_independent_from_terra() -> None:
    domains = RandomDomains()

    assert domains.choice_namespace == "thesis-tb21-luna-rq2-replication-v1"
    assert domains.bootstrap_namespace.endswith("/bootstrap-rq2-v1")
    assert "sensitivity" not in domains.choice_namespace


def test_freeze_gate_rejects_outcome_history(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    outcome = tmp_path / "evaluation" / "thesis-tb21-luna-rq2-replication-v2.json"
    outcome.parent.mkdir()
    outcome.write_text("{}\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "outcome")
    outcome.unlink()
    _git(tmp_path, "add", "-u")
    _git(tmp_path, "commit", "-qm", "remove")

    with pytest.raises(ValueError, match="already exists in Git history"):
        require_preoutcome_state(tmp_path, include_executable=True)
