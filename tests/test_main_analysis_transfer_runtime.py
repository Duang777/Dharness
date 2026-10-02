from __future__ import annotations

import ast
import hashlib
import subprocess
from pathlib import Path
from typing import Any

import pytest

from evidence_harness_mutation.main_analysis_transfer_runtime import (
    AttestedWorkspaceEnvironment,
    capture_workspace_tree,
    verify_checkout,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNTIME_MODULE = PROJECT_ROOT / "src/evidence_harness_mutation/main_analysis_transfer_runtime.py"


def _sha(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


class _FakeEnvironment:
    def __init__(
        self,
        digests: list[str],
        *,
        action_error: BaseException | None = None,
    ) -> None:
        self.config: dict[str, object] = {}
        self._digests = digests
        self._action_error = action_error
        self.actions: list[dict[str, Any]] = []

    def execute(
        self,
        action: dict[str, Any],
        cwd: str = "",
        *,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        del cwd, timeout
        command = action["command"]
        if isinstance(command, str) and command.startswith("python3 -c "):
            digest = self._digests.pop(0)
            return {
                "output": f"TRANSFER_WORKSPACE_SHA256={digest}\n",
                "returncode": 0,
                "exception_info": "",
            }
        self.actions.append(action)
        if self._action_error is not None:
            raise self._action_error
        return {"output": "ok", "returncode": 0, "exception_info": ""}

    def get_template_vars(self, **kwargs: Any) -> dict[str, Any]:
        return kwargs

    def serialize(self) -> dict[str, Any]:
        return {"info": {"fake": True}}


def test_attested_environment_captures_initial_action_and_terminal_states() -> None:
    fake = _FakeEnvironment([_sha("initial"), _sha("changed"), _sha("terminal")])
    environment = AttestedWorkspaceEnvironment(fake)

    environment.begin()
    result = environment.execute({"command": "touch file"})
    environment.finish()

    assert result["returncode"] == 0
    assert fake.actions == [{"command": "touch file"}]
    assert [
        (item.ordinal, item.stage, item.action_ordinal, item.tree_sha256)
        for item in environment.attestations
    ] == [
        (1, "initial", None, _sha("initial")),
        (2, "after_action", 1, _sha("changed")),
        (3, "terminal", None, _sha("terminal")),
    ]


def test_attested_environment_records_state_after_a_terminal_action_exception() -> None:
    class Submitted(Exception):
        pass

    fake = _FakeEnvironment(
        [_sha("initial"), _sha("submitted"), _sha("terminal")],
        action_error=Submitted("done"),
    )
    environment = AttestedWorkspaceEnvironment(fake)
    environment.begin()

    with pytest.raises(Submitted, match="done"):
        environment.execute({"command": "submit"})
    environment.finish()

    assert [item.stage for item in environment.attestations] == [
        "initial",
        "after_action",
        "terminal",
    ]


def test_workspace_digest_rejects_output_with_any_extra_lines() -> None:
    class NoisyEnvironment(_FakeEnvironment):
        def execute(
            self,
            action: dict[str, Any],
            cwd: str = "",
            *,
            timeout: int | None = None,
        ) -> dict[str, Any]:
            del action, cwd, timeout
            return {
                "output": f"noise\nTRANSFER_WORKSPACE_SHA256={_sha('tree')}\n",
                "returncode": 0,
            }

    with pytest.raises(ValueError, match="invalid record"):
        capture_workspace_tree(NoisyEnvironment([]))


def test_verify_checkout_requires_exact_clean_revision(tmp_path: Path) -> None:
    subprocess.run(("git", "init", "-q", str(tmp_path)), check=True)
    subprocess.run(
        ("git", "-C", str(tmp_path), "config", "user.email", "test@example.com"),
        check=True,
    )
    subprocess.run(
        ("git", "-C", str(tmp_path), "config", "user.name", "Test"),
        check=True,
    )
    source = tmp_path / "source.txt"
    source.write_text("fixed\n", encoding="utf-8")
    subprocess.run(("git", "-C", str(tmp_path), "add", "source.txt"), check=True)
    subprocess.run(("git", "-C", str(tmp_path), "commit", "-qm", "fixture"), check=True)
    commit = subprocess.run(
        ("git", "-C", str(tmp_path), "rev-parse", "HEAD"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    tree = subprocess.run(
        ("git", "-C", str(tmp_path), "rev-parse", "HEAD^{tree}"),
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()

    assert verify_checkout(
        tmp_path,
        expected_commit=commit,
        expected_tree=tree,
    ).model_dump() == {"commit": commit, "tree": tree}

    source.write_text("changed\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must be clean"):
        verify_checkout(
            tmp_path,
            expected_commit=commit,
            expected_tree=tree,
        )


def test_runtime_uses_dynamic_upstream_imports() -> None:
    tree = ast.parse(RUNTIME_MODULE.read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module
    )

    assert all(not name.startswith("minisweagent") for name in imported)
    assert all(not name.startswith("programbench") for name in imported)
