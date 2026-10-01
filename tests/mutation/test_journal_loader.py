from __future__ import annotations

import hashlib
import json

import pytest

from evidence_harness_mutation import JournalLoadError, load_state_prefix

_SOURCE_COMMIT = "a" * 40


def _event(event_type: str, payload: dict[str, object]) -> str:
    return json.dumps(
        {
            "timestamp": "2026-10-01T00:00:00+00:00",
            "type": event_type,
            "payload": payload,
        },
        sort_keys=True,
    )


def _journal(*events: str) -> bytes:
    return ("\n".join(events) + "\n").encode()


def _load(data: bytes, *, through_line: int | None = None):
    return load_state_prefix(
        data,
        source_commit=_SOURCE_COMMIT,
        expected_journal_sha256=hashlib.sha256(data).hexdigest(),
        through_line=through_line,
    )


def test_prefix_uses_physical_lines_and_binds_the_complete_journal() -> None:
    data = _journal(
        _event(
            "run_started",
            {
                "journal_schema_version": 2,
                "instruction": "repair the artifact",
                "options": {"enable_completion_review": True},
            },
        ),
        _event("future_event", {"command_sequence": 99}),
        _event("run_finished", {"stop_reason": "budget_exhausted"}),
    )

    prefix = _load(data, through_line=2)

    assert prefix.through_line == 2
    assert [event.line for event in prefix.events] == [1, 2]
    assert prefix.events[1].payload == {"command_sequence": 99}
    assert prefix.source.journal_sha256 == hashlib.sha256(data).hexdigest()
    assert prefix.instruction == "repair the artifact"
    assert prefix.completion_review_enabled is True


def test_prefix_digest_includes_bytes_after_the_cutoff() -> None:
    original = _journal(
        _event(
            "run_started",
            {
                "journal_schema_version": 2,
                "instruction": "repair the artifact",
                "options": {"enable_completion_review": False},
            },
        ),
        _event("future_event", {"value": "original"}),
    )
    changed_suffix = original.replace(b"original", b"modified")

    with pytest.raises(JournalLoadError, match="source journal hash does not match"):
        load_state_prefix(
            changed_suffix,
            source_commit=_SOURCE_COMMIT,
            expected_journal_sha256=hashlib.sha256(original).hexdigest(),
            through_line=1,
        )


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (
            _journal(
                _event(
                    "run_started",
                    {
                        "journal_schema_version": 1,
                        "instruction": "repair",
                        "options": {"enable_completion_review": True},
                    },
                )
            ),
            "only journal schema version 2",
        ),
        (
            (
                _event(
                    "run_started",
                    {
                        "journal_schema_version": 2,
                        "instruction": "repair",
                        "options": {"enable_completion_review": True},
                    },
                )
                + "\n\n"
            ).encode(),
            "blank journal line",
        ),
    ],
)
def test_loader_rejects_unsupported_or_ambiguous_journals(
    data: bytes,
    message: str,
) -> None:
    with pytest.raises(JournalLoadError, match=message):
        _load(data)
