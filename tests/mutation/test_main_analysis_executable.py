from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from evidence_harness_mutation import main_analysis_executable as executable

_COHORT_IDS = (
    "isona__dirble.e2dea9f",
    "clog-tool__clog-cli.7066cba",
    "simeg__eureka.df3796c",
    "johanneskaufmann__html-to-markdown.3006818",
    "tstack__lnav.ee34494",
    "ducaale__xh.4a6e44f",
    "tukaani-project__xz.1007bf0",
    "sstadick__hck.b66c751",
    "dundee__gdu.ede21d2",
    "svenstaro__genact.16f96e3",
    "nachoparker__dutree.44e877d",
    "jonas__tig.8334123",
    "cheat__cheat.b8098dc",
    "canop__rhit.ae90bcb",
    "agourlay__zip-password-finder.704700d",
    "rust-ethereum__ethabi.b1710ad",
    "rochacbruno__marmite.7d4bc2d",
    "ast-grep__ast-grep.dde0fe0",
    "cweill__gotests.2a672c5",
    "johnkerl__miller.8d85b46",
)


def _task(index: int, instance_id: str) -> executable.MiniSweCohortTask:
    return executable.MiniSweCohortTask(
        index=index,
        instance_id=instance_id,
        selection_sha256=hashlib.sha256(
            b"miniswe-transfer-v1\0" + instance_id.encode()
        ).hexdigest(),
        task_yaml_bytes=1,
        task_yaml_sha256="a" * 64,
        task_yaml_git_blob="b" * 40,
    )


def _git(root: Path, *args: str) -> None:
    subprocess.run(("git", "-C", str(root), *args), check=True, capture_output=True)


def test_cohort_model_locks_the_ranked_twenty_task_order() -> None:
    cohort = executable.MiniSweCohort(
        tasks=tuple(_task(index, task_id) for index, task_id in enumerate(_COHORT_IDS, start=1))
    )

    assert tuple(task.instance_id for task in cohort.tasks) == _COHORT_IDS

    reversed_ids = tuple(reversed(_COHORT_IDS))
    with pytest.raises(ValidationError, match="selection order"):
        executable.MiniSweCohort(
            tasks=tuple(
                _task(index, task_id) for index, task_id in enumerate(reversed_ids, start=1)
            )
        )


def test_preoutcome_gate_checks_filesystem_before_reading_inputs(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "README").write_text("fixture\n", encoding="utf-8")
    _git(tmp_path, "add", "README")
    _git(tmp_path, "commit", "-qm", "fixture")
    outcome = tmp_path / "evaluation" / "miniswe-agent-transfer-v1.json"
    outcome.parent.mkdir()
    outcome.write_text("{}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="must precede outcome artifacts"):
        executable._require_preoutcome_state(tmp_path)


def test_preoutcome_gate_rejects_deleted_all_refs_history(tmp_path: Path) -> None:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    outcome = tmp_path / "evaluation" / "miniswe-agent-transfer-v1.json"
    outcome.parent.mkdir()
    outcome.write_text("{}\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-qm", "outcome")
    outcome.unlink()
    _git(tmp_path, "add", "-u")
    _git(tmp_path, "commit", "-qm", "remove")

    with pytest.raises(ValueError, match="already exists in Git history"):
        executable._require_preoutcome_state(tmp_path)
