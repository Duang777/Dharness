from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CANONICAL = PROJECT_ROOT / "evaluation" / "canonical-89.json"
DEFAULT_STOP_REASONS = frozenset({"verified", "budget_exhausted"})


@dataclass(frozen=True)
class TraceMetrics:
    task: str
    stop_reason: str
    turns_used: int
    environment_calls_used: int
    elapsed_sec: float
    turns_remaining: int
    environment_calls_remaining: int
    wall_time_remaining_sec: float
    finish_attempts: int
    first_finish_turn: int | None
    turns_remaining_at_first_finish: int | None
    environment_calls_remaining_at_first_finish: int | None
    wall_time_remaining_at_first_finish_sec: float | None
    completion_reviews: int
    review_repairs: int
    completion_rejections: int
    long_commands: int
    truncated_receipts: int
    duplicate_plan_items: int
    changed_state_in_final_five_decisions: bool


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize control-loop behavior in canonical failed trials."
    )
    parser.add_argument("--canonical", type=Path, default=DEFAULT_CANONICAL)
    parser.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    parser.add_argument(
        "--stop-reason",
        action="append",
        dest="stop_reasons",
        help="Include this internal stop reason. Defaults to verified and budget_exhausted.",
    )
    parser.add_argument("--json-out", type=Path)
    return parser.parse_args()


def analyze_failure_traces(
    canonical_path: Path,
    project_root: Path,
    stop_reasons: frozenset[str] = DEFAULT_STOP_REASONS,
) -> dict[str, Any]:
    canonical = _read_object(canonical_path)
    tasks = canonical.get("tasks")
    if not isinstance(tasks, list):
        raise ValueError(f"canonical manifest has no task list: {canonical_path}")

    rows: list[TraceMetrics] = []
    for task in tasks:
        if not isinstance(task, dict) or task.get("reward") != 0:
            continue
        result_path_value = task.get("result_path")
        if not isinstance(result_path_value, str):
            raise ValueError(f"canonical task has no result_path: {task!r}")
        result_path = Path(result_path_value)
        if not result_path.is_absolute():
            result_path = project_root / result_path
        result = _read_object(result_path)
        harness = _harness_metadata(result)
        if harness is None:
            continue
        stop_reason = harness.get("stop_reason")
        if not isinstance(stop_reason, str) or stop_reason not in stop_reasons:
            continue

        task_name = task.get("name")
        if not isinstance(task_name, str):
            raise ValueError(f"canonical task has no name: {task!r}")
        journal_path = result_path.parent / "agent" / "evidence-harness" / "events.jsonl"
        rows.append(_analyze_journal(task_name, stop_reason, harness, journal_path))

    serialized_rows = [asdict(row) for row in rows]
    groups = {
        reason: _summarize_group([row for row in rows if row.stop_reason == reason])
        for reason in sorted(stop_reasons)
    }
    return {
        "schema_version": 1,
        "canonical": _display_path(canonical_path, project_root),
        "groups": groups,
        "tasks": serialized_rows,
    }


def _analyze_journal(
    task: str,
    stop_reason: str,
    harness: dict[str, Any],
    journal_path: Path,
) -> TraceMetrics:
    events = _read_events(journal_path)
    started = _event(events, "run_started")
    finished = _event(events, "run_finished")
    options = _object(started["payload"].get("options"), f"{journal_path}: run options")

    decisions = [event for event in events if event.get("type") == "agent_decision"]
    command_receipts = [event for event in events if event.get("type") == "command_receipt"]
    finish_positions = [
        index
        for index, event in enumerate(decisions, start=1)
        if _payload(event).get("action") == "finish"
    ]
    first_finish_turn = finish_positions[0] if finish_positions else None
    first_finish_event = decisions[first_finish_turn - 1] if first_finish_turn else None
    calls_at_first_finish = (
        sum(event["timestamp"] < first_finish_event["timestamp"] for event in command_receipts)
        if first_finish_event
        else None
    )

    max_turns = _int(options, "max_turns")
    max_calls = _int(options, "max_environment_calls")
    max_wall = float(_number(options, "max_wall_time_sec"))
    turns_used = _int(harness, "turns_used")
    calls_used = _int(harness, "environment_calls_used")
    start_time = _timestamp(started, journal_path)
    finish_time = _timestamp(finished, journal_path)
    elapsed_sec = max(0.0, (finish_time - start_time).total_seconds())
    first_finish_elapsed = (
        max(0.0, (_timestamp(first_finish_event, journal_path) - start_time).total_seconds())
        if first_finish_event
        else None
    )

    review_events = [event for event in events if event.get("type") == "completion_review"]
    plan_items = [item for event in decisions for item in _string_list(_payload(event).get("plan"))]
    plan_counts = Counter(plan_items)

    return TraceMetrics(
        task=task,
        stop_reason=stop_reason,
        turns_used=turns_used,
        environment_calls_used=calls_used,
        elapsed_sec=round(elapsed_sec, 3),
        turns_remaining=max(0, max_turns - turns_used),
        environment_calls_remaining=max(0, max_calls - calls_used),
        wall_time_remaining_sec=round(max(0.0, max_wall - elapsed_sec), 3),
        finish_attempts=len(finish_positions),
        first_finish_turn=first_finish_turn,
        turns_remaining_at_first_finish=(
            max(0, max_turns - first_finish_turn) if first_finish_turn else None
        ),
        environment_calls_remaining_at_first_finish=(
            max(0, max_calls - calls_at_first_finish) if calls_at_first_finish is not None else None
        ),
        wall_time_remaining_at_first_finish_sec=(
            round(max(0.0, max_wall - first_finish_elapsed), 3)
            if first_finish_elapsed is not None
            else None
        ),
        completion_reviews=len(review_events),
        review_repairs=sum(_payload(event).get("verdict") == "repair" for event in review_events),
        completion_rejections=sum(event.get("type") == "completion_rejected" for event in events),
        long_commands=sum(
            float(_payload(event).get("duration_sec", 0)) >= 30 for event in command_receipts
        ),
        truncated_receipts=sum(_receipt_is_truncated(event) for event in command_receipts),
        duplicate_plan_items=sum(count - 1 for count in plan_counts.values() if count > 1),
        changed_state_in_final_five_decisions=any(
            command.get("mode") == "change"
            for event in decisions[-5:]
            for command in _commands(_payload(event))
        ),
    )


def _summarize_group(rows: list[TraceMetrics]) -> dict[str, Any]:
    first_finish_rows = [row for row in rows if row.first_finish_turn is not None]
    return {
        "tasks": len(rows),
        "finish_attempts": sum(row.finish_attempts for row in rows),
        "completion_reviews": sum(row.completion_reviews for row in rows),
        "review_repairs": sum(row.review_repairs for row in rows),
        "completion_rejections": sum(row.completion_rejections for row in rows),
        "long_commands": sum(row.long_commands for row in rows),
        "truncated_receipts": sum(row.truncated_receipts for row in rows),
        "tasks_with_duplicate_plan_items": sum(row.duplicate_plan_items > 0 for row in rows),
        "tasks_that_reached_finish": len(first_finish_rows),
        "tasks_changed_in_final_five_decisions": sum(
            row.changed_state_in_final_five_decisions for row in rows
        ),
        "median_turns_remaining": _median(row.turns_remaining for row in rows),
        "median_environment_calls_remaining": _median(
            row.environment_calls_remaining for row in rows
        ),
        "median_wall_time_remaining_sec": _median(row.wall_time_remaining_sec for row in rows),
        "median_first_finish_turn": _median(row.first_finish_turn for row in first_finish_rows),
        "median_turns_remaining_at_first_finish": _median(
            row.turns_remaining_at_first_finish for row in first_finish_rows
        ),
        "median_environment_calls_remaining_at_first_finish": _median(
            row.environment_calls_remaining_at_first_finish for row in first_finish_rows
        ),
        "median_wall_time_remaining_at_first_finish_sec": _median(
            row.wall_time_remaining_at_first_finish_sec for row in first_finish_rows
        ),
    }


def _read_events(path: Path) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"cannot read journal: {path}") from exc
    for line_number, line in enumerate(lines, start=1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid journal event: {path}:{line_number}") from exc
        if not isinstance(event, dict):
            raise ValueError(f"journal event is not an object: {path}:{line_number}")
        events.append(event)
    return events


def _read_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON object: {path}") from exc
    return _object(value, str(path))


def _event(events: list[dict[str, Any]], event_type: str) -> dict[str, Any]:
    matches = [event for event in events if event.get("type") == event_type]
    if len(matches) != 1:
        raise ValueError(f"expected one {event_type} event, found {len(matches)}")
    return matches[0]


def _payload(event: dict[str, Any]) -> dict[str, Any]:
    return _object(event.get("payload"), "journal event payload")


def _commands(payload: dict[str, Any]) -> list[dict[str, Any]]:
    commands = payload.get("commands")
    if not isinstance(commands, list):
        return []
    return [command for command in commands if isinstance(command, dict)]


def _receipt_is_truncated(event: dict[str, Any]) -> bool:
    payload = _payload(event)
    return any(
        isinstance(excerpt, dict) and int(excerpt.get("omitted_bytes", 0)) > 0
        for excerpt in (payload.get("stdout"), payload.get("stderr"))
    )


def _timestamp(event: dict[str, Any], path: Path) -> datetime:
    value = event.get("timestamp")
    if not isinstance(value, str):
        raise ValueError(f"journal event has no timestamp: {path}")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid journal timestamp in {path}: {value}") from exc


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object for {label}")
    return value


def _harness_metadata(result: dict[str, Any]) -> dict[str, Any] | None:
    agent_result = result.get("agent_result")
    if not isinstance(agent_result, dict):
        return None
    metadata = agent_result.get("metadata")
    if not isinstance(metadata, dict):
        return None
    harness = metadata.get("evidence_harness")
    return harness if isinstance(harness, dict) else None


def _int(value: dict[str, Any], key: str) -> int:
    result = value.get(key)
    if not isinstance(result, int):
        raise ValueError(f"expected integer {key}")
    return result


def _number(value: dict[str, Any], key: str) -> int | float:
    result = value.get(key)
    if not isinstance(result, int | float):
        raise ValueError(f"expected number {key}")
    return result


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str)]


def _median(values: Any) -> int | float | None:
    materialized = list(values)
    if not materialized:
        return None
    return median(materialized)


def _display_path(path: Path, project_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(project_root.resolve()))
    except ValueError:
        return str(path)


def main() -> int:
    args = parse_args()
    stop_reasons = frozenset(args.stop_reasons) if args.stop_reasons else DEFAULT_STOP_REASONS
    report = analyze_failure_traces(args.canonical, args.project_root, stop_reasons)
    output = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.json_out is None:
        print(output, end="")
    else:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(output, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
