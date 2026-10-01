from __future__ import annotations

import hashlib
import json

from pydantic import ValidationError

from evidence_harness_mutation.model import JournalBinding, RecordedEvent, StatePrefix


class JournalLoadError(ValueError):
    pass


def load_state_prefix(
    journal_bytes: bytes,
    *,
    source_commit: str,
    expected_journal_sha256: str,
    through_line: int | None = None,
) -> StatePrefix:
    actual_sha256 = hashlib.sha256(journal_bytes).hexdigest()
    try:
        source = JournalBinding(
            source_commit=source_commit,
            journal_sha256=expected_journal_sha256,
        )
    except ValidationError as exc:
        raise JournalLoadError(f"invalid source binding: {exc}") from exc
    if source.journal_sha256 != actual_sha256:
        raise JournalLoadError(
            f"source journal hash does not match: {actual_sha256} != {source.journal_sha256}"
        )

    events = _parse_events(journal_bytes)
    starts = [event for event in events if event.event_type == "run_started"]
    if len(starts) != 1 or starts[0].line != 1:
        raise JournalLoadError("schema-2 journal must contain one leading run_started event")

    start_payload = starts[0].payload
    if start_payload.get("journal_schema_version") != 2:
        raise JournalLoadError("only journal schema version 2 is supported")
    instruction = start_payload.get("instruction")
    if not isinstance(instruction, str) or not instruction.strip():
        raise JournalLoadError("run_started instruction must be a non-empty string")
    options = start_payload.get("options")
    if not isinstance(options, dict):
        raise JournalLoadError("run_started options must be an object")
    review_enabled = options.get("enable_completion_review")
    if not isinstance(review_enabled, bool):
        raise JournalLoadError("run_started options.enable_completion_review must be a boolean")

    selected_line = len(events) if through_line is None else through_line
    if (
        not isinstance(selected_line, int)
        or isinstance(selected_line, bool)
        or selected_line < 1
        or selected_line > len(events)
    ):
        raise JournalLoadError(
            f"through_line must select an existing journal line, got {selected_line!r}"
        )

    return StatePrefix(
        source=source,
        through_line=selected_line,
        instruction=instruction,
        completion_review_enabled=review_enabled,
        events=tuple(events[:selected_line]),
    )


def _parse_events(journal_bytes: bytes) -> list[RecordedEvent]:
    events: list[RecordedEvent] = []
    for line, raw_line in enumerate(journal_bytes.splitlines(keepends=True), start=1):
        if not raw_line.strip():
            raise JournalLoadError(f"blank journal line: {line}")
        try:
            text = raw_line.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise JournalLoadError(f"journal line {line} is not valid UTF-8") from exc
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise JournalLoadError(f"invalid journal JSON on line {line}") from exc
        if not isinstance(value, dict):
            raise JournalLoadError(f"journal event on line {line} must be an object")
        timestamp = value.get("timestamp")
        event_type = value.get("type")
        payload = value.get("payload")
        if not isinstance(timestamp, str) or not timestamp:
            raise JournalLoadError(f"journal event on line {line} has no timestamp")
        if not isinstance(event_type, str) or not event_type:
            raise JournalLoadError(f"journal event on line {line} has no type")
        if not isinstance(payload, dict):
            raise JournalLoadError(f"journal event payload on line {line} must be an object")
        try:
            events.append(
                RecordedEvent(
                    line=line,
                    line_sha256=hashlib.sha256(raw_line).hexdigest(),
                    timestamp=timestamp,
                    event_type=event_type,
                    payload=payload,
                )
            )
        except ValidationError as exc:
            raise JournalLoadError(f"invalid journal event on line {line}: {exc}") from exc
    if not events:
        raise JournalLoadError("journal is empty")
    return events
