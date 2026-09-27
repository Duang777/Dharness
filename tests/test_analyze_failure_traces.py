from __future__ import annotations

import json
from pathlib import Path

from scripts.analyze_failure_traces import analyze_failure_traces


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _event(timestamp: str, event_type: str, payload: dict[str, object]) -> dict[str, object]:
    return {"timestamp": timestamp, "type": event_type, "payload": payload}


def test_analyze_failure_traces_measures_completion_and_budget_behavior(
    tmp_path: Path,
) -> None:
    result_path = tmp_path / "runs" / "task-a" / "result.json"
    journal_path = result_path.parent / "agent" / "evidence-harness" / "events.jsonl"
    _write_json(
        tmp_path / "evaluation" / "canonical.json",
        {
            "tasks": [
                {
                    "name": "task-a",
                    "reward": 0,
                    "result_path": str(result_path.relative_to(tmp_path)),
                },
                {
                    "name": "task-b",
                    "reward": 1,
                    "result_path": "unused.json",
                },
                {
                    "name": "task-replay",
                    "reward": 0,
                    "result_path": "runs/task-replay/result.json",
                },
            ]
        },
    )
    _write_json(
        tmp_path / "runs" / "task-replay" / "result.json",
        {"agent_result": {"metadata": {"replay": {"commands": 3}}}},
    )
    _write_json(
        result_path,
        {
            "agent_result": {
                "metadata": {
                    "evidence_harness": {
                        "stop_reason": "verified",
                        "turns_used": 3,
                        "environment_calls_used": 3,
                    }
                }
            }
        },
    )
    events = [
        _event(
            "2026-09-27T00:00:00+00:00",
            "run_started",
            {
                "options": {
                    "max_turns": 40,
                    "max_environment_calls": 80,
                    "max_wall_time_sec": 1800,
                }
            },
        ),
        _event(
            "2026-09-27T00:00:01+00:00",
            "command_receipt",
            {
                "duration_sec": 31,
                "stdout": {"omitted_bytes": 2},
                "stderr": {"omitted_bytes": 0},
            },
        ),
        _event(
            "2026-09-27T00:00:02+00:00",
            "agent_decision",
            {
                "action": "execute",
                "plan": ["inspect"],
                "commands": [{"mode": "change"}],
            },
        ),
        _event(
            "2026-09-27T00:00:02.500000+00:00",
            "command_receipt",
            {
                "duration_sec": 1,
                "stdout": {"omitted_bytes": 0},
                "stderr": {"omitted_bytes": 0},
            },
        ),
        _event(
            "2026-09-27T00:00:03+00:00",
            "agent_decision",
            {"action": "finish", "plan": ["inspect"], "commands": []},
        ),
        _event(
            "2026-09-27T00:00:03.100000+00:00",
            "completion_review",
            {"verdict": "repair"},
        ),
        _event(
            "2026-09-27T00:00:03.200000+00:00",
            "completion_rejected",
            {"reasons": ["weak"]},
        ),
        _event(
            "2026-09-27T00:00:04+00:00",
            "agent_decision",
            {"action": "finish", "plan": [], "commands": []},
        ),
        _event(
            "2026-09-27T00:00:04.100000+00:00",
            "completion_review",
            {"verdict": "accept"},
        ),
        _event(
            "2026-09-27T00:00:05+00:00",
            "command_receipt",
            {
                "duration_sec": 1,
                "stdout": {"omitted_bytes": 0},
                "stderr": {"omitted_bytes": 0},
            },
        ),
        _event(
            "2026-09-27T00:00:10+00:00",
            "run_finished",
            {"stop_reason": "verified"},
        ),
    ]
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    journal_path.write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )

    report = analyze_failure_traces(
        tmp_path / "evaluation" / "canonical.json",
        tmp_path,
    )

    row = report["tasks"][0]
    assert row["finish_attempts"] == 2
    assert row["first_finish_turn"] == 2
    assert row["turns_remaining_at_first_finish"] == 38
    assert row["environment_calls_remaining_at_first_finish"] == 78
    assert row["wall_time_remaining_at_first_finish_sec"] == 1797
    assert row["completion_reviews"] == 2
    assert row["review_repairs"] == 1
    assert row["completion_rejections"] == 1
    assert row["long_commands"] == 1
    assert row["truncated_receipts"] == 1
    assert row["duplicate_plan_items"] == 1
    assert row["changed_state_in_final_five_decisions"] is True
    assert report["groups"]["verified"]["tasks"] == 1
    assert report["groups"]["budget_exhausted"]["tasks"] == 0
