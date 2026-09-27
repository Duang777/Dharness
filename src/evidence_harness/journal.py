from __future__ import annotations

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from evidence_harness.policy import is_sensitive_field, redact_sensitive
from evidence_harness.protocol import OutputExcerpt


class RunJournal:
    def __init__(self, root: Path, inline_bytes: int) -> None:
        if inline_bytes < 256:
            raise ValueError("inline_bytes must be at least 256")
        self.root = root
        self.inline_bytes = inline_bytes
        self.output_dir = root / "outputs"
        self.events_path = root / "events.jsonl"
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def append(self, event_type: str, payload: BaseModel | dict[str, Any]) -> None:
        value = payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
        event = {
            "timestamp": datetime.now(UTC).isoformat(),
            "type": event_type,
            "payload": _redact_value(value),
        }
        self.root.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=True, sort_keys=True))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())

    def capture_output(
        self,
        sequence: int,
        stream_name: str,
        raw_text: str | None,
    ) -> OutputExcerpt:
        text = redact_sensitive(raw_text or "")
        data = text.encode("utf-8", errors="replace")
        digest = hashlib.sha256(data).hexdigest()
        archive_path: str | None = None
        if data:
            filename = f"{sequence:04d}-{stream_name}-{digest[:16]}.log"
            destination = self.output_dir / filename
            temporary = destination.with_suffix(".tmp")
            temporary.write_bytes(data)
            os.replace(temporary, destination)
            archive_path = str(destination.relative_to(self.root))

        if len(data) <= self.inline_bytes:
            head = text
            tail = ""
            omitted = 0
        else:
            head_size = self.inline_bytes * 2 // 3
            tail_size = self.inline_bytes - head_size
            head = data[:head_size].decode("utf-8", errors="replace")
            tail = data[-tail_size:].decode("utf-8", errors="replace")
            omitted = len(data) - self.inline_bytes

        return OutputExcerpt(
            head=head,
            tail=tail,
            total_bytes=len(data),
            omitted_bytes=omitted,
            sha256=digest,
            archive_path=archive_path,
        )


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_sensitive(value)
    if isinstance(value, dict):
        return {
            key: (
                "[REDACTED]"
                if is_sensitive_field(key) and item is not None
                else _redact_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(item) for item in value)
    return value
