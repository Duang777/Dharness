from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from evidence_harness_mutation.candidate_gate import GateRequest, ProbeStatus, run_candidate_gate

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = PROJECT_ROOT / "experiments/erosion-v1/regression-suite-v1.json"
BASE_COMMIT = "c2f95328b284b9a9addbff8cc6af7e12cbb1c34f"
EXCLUDED = (
    "tests/guard",
    "tests/mutation/test_candidate_gate.py",
    "tests/mutation/test_erosion_campaign.py",
    "tests/mutation/test_public_audit.py",
    "tests/mutation/test_natural_erosion.py",
)


def main() -> int:
    """Write the pre-gate repository test suite as a gate suite for the regression baseline.

    Tests that already fail on the unmodified base inside the gate sandbox cannot tell a
    candidate apart from the base, so they are dropped; the suite must accept an empty patch.
    """
    completed = subprocess.run(
        (
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-p",
            "no:cacheprovider",
            "tests",
            *(f"--ignore={path}" for path in EXCLUDED),
        ),
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode:
        print(completed.stdout[-2000:], completed.stderr[-2000:], file=sys.stderr)
        return 1
    nodes = sorted(
        {
            re.sub(r"\[.*\]$", "", line.strip())
            for line in completed.stdout.splitlines()
            if line.startswith("tests/") and "::" in line
        }
    )
    write_suite(nodes)
    verdict = run_candidate_gate(
        GateRequest(repo_root=PROJECT_ROOT, patch=b"", base=BASE_COMMIT, suite_path=OUTPUT)
    )
    by_id = {f"regression.n{index:04d}": node for index, node in enumerate(nodes, start=1)}
    failing = {by_id[p.probe_id] for p in verdict.probes if p.status is ProbeStatus.VIOLATED}
    kept = [node for node in nodes if node not in failing]
    write_suite(kept)
    print(f"collected {len(nodes)}, dropped {len(failing)} failing on the base, kept {len(kept)}")
    for node in sorted(failing):
        print(f"  dropped {node}")
    return 0


def write_suite(nodes: list[str]) -> None:
    suite = {
        "schema_version": 1,
        "suite_id": "regression-suite-v1",
        "probes": [
            {
                "probe_id": f"regression.n{index:04d}",
                "component": "regression",
                "property": "regression",
                "pytest_node": node,
            }
            for index, node in enumerate(nodes, start=1)
        ],
    }
    OUTPUT.write_text(json.dumps(suite, indent=2) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
