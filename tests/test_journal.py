import json

from evidence_harness.journal import RunJournal


def test_output_is_redacted_archived_and_truncated(tmp_path) -> None:
    journal = RunJournal(tmp_path, inline_bytes=256)
    raw = "API_KEY=top-secret\n" + ("x" * 600) + "\npassword: swordfish"

    excerpt = journal.capture_output(1, "stdout", raw)

    assert "top-secret" not in excerpt.head
    assert "swordfish" not in excerpt.tail
    assert excerpt.total_bytes > excerpt.omitted_bytes > 0
    assert excerpt.archive_path is not None
    archived = (tmp_path / excerpt.archive_path).read_text()
    assert "[REDACTED]" in archived
    assert "top-secret" not in archived


def test_journal_writes_one_json_event_per_line(tmp_path) -> None:
    journal = RunJournal(tmp_path, inline_bytes=256)
    journal.append("state", {"phase": "thinking"})
    journal.append("state", {"phase": "verifying"})

    events = [
        json.loads(line)
        for line in journal.events_path.read_text(encoding="utf-8").splitlines()
    ]
    assert [event["payload"]["phase"] for event in events] == [
        "thinking",
        "verifying",
    ]


def test_journal_redacts_secrets_in_structured_events(tmp_path) -> None:
    journal = RunJournal(tmp_path, inline_bytes=256)
    bearer = "Bearer abcdefghijklmnopqrstuvwxyz"
    provider_key = "sk-proj-abcdefghijklmnopqrstuvwxyz123456"
    jwt = (
        "eyJhbGciOiJIUzI1NiJ9."
        "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
        "abcdefghijklmnopqrstuvwxyz"
    )

    journal.append(
        "decision",
        {
            "script": f"curl -H 'Authorization: {bearer}' https://example.invalid",
            "nested": [provider_key, {"token": jwt}],
        },
    )

    encoded = journal.events_path.read_text(encoding="utf-8")
    assert bearer not in encoded
    assert provider_key not in encoded
    assert jwt not in encoded
    assert encoded.count("[REDACTED]") >= 3
