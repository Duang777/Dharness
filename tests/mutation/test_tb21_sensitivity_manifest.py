from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from evidence_harness_mutation._tb21_manifest import (
    BOOTSTRAP_NAMESPACE,
    CHOICE_NAMESPACE,
    FrozenRunPlan,
    PlannedTask,
    RandomDomains,
    RegistryMember,
    Tb21TaskKey,
    require_preoutcome_state,
)


def _git(root: Path, *args: str) -> None:
    subprocess.run(("git", "-C", str(root), *args), check=True, capture_output=True)


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


def test_run_plan_requires_contiguous_version_scoped_tasks() -> None:
    plan = FrozenRunPlan(tasks=tuple(_task(ordinal) for ordinal in range(1, 62)))

    assert len(plan.tasks) == 61
    assert plan.tasks[0].member.registry_name == "terminal-bench/task-1"

    tasks = list(plan.tasks)
    tasks[1] = tasks[1].model_copy(update={"ordinal": 3})
    with pytest.raises(ValidationError, match="ordinals must be contiguous"):
        FrozenRunPlan(tasks=tuple(tasks))


def test_random_domains_are_distinct_from_main_analysis() -> None:
    domains = RandomDomains()

    assert domains.choice_namespace == CHOICE_NAMESPACE
    assert domains.bootstrap_namespace == BOOTSTRAP_NAMESPACE
    assert domains.choice_namespace != "thesis-main-analysis-v1"
    assert domains.choice_namespace != domains.bootstrap_namespace


def test_freeze_gate_checks_broken_symlinks_as_existing(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "README").write_text("fixture\n", encoding="utf-8")
    _git(tmp_path, "add", "README")
    _git(tmp_path, "commit", "-qm", "fixture")
    outcome = tmp_path / "evaluation" / "prefixbench-v1-tb21-sensitivity-canonical.json"
    outcome.parent.mkdir()
    outcome.symlink_to(tmp_path / "missing")

    with pytest.raises(ValueError, match="must precede outcome artifacts"):
        require_preoutcome_state(tmp_path, include_executable=True)


def test_freeze_gate_rejects_deleted_outcome_in_any_ref_history(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    outcome = tmp_path / "evaluation" / "thesis-tb21-sensitivity-v1.json"
    outcome.parent.mkdir()
    outcome.write_text("{}\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "outcome")
    outcome.unlink()
    _git(tmp_path, "add", "-u")
    _git(tmp_path, "commit", "-qm", "remove outcome")

    with pytest.raises(ValueError, match="already exists in Git history"):
        require_preoutcome_state(tmp_path, include_executable=True)
