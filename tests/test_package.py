from __future__ import annotations

import subprocess
import sys

import pytest


def test_evaluation_import_does_not_load_model_stack() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            ("import sys; import evidence_harness.evaluation; assert 'litellm' not in sys.modules"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_top_level_agent_export_remains_available() -> None:
    import evidence_harness
    from evidence_harness.harbor_agent import EvidenceHarnessAgent

    assert evidence_harness.EvidenceHarnessAgent is EvidenceHarnessAgent
    missing_name = "missing"
    with pytest.raises(AttributeError, match="missing"):
        getattr(evidence_harness, missing_name)
