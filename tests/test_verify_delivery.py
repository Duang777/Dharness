from __future__ import annotations

from scripts.verify_delivery import PROJECT_ROOT, validate_delivery


def test_repository_delivery_is_complete() -> None:
    assert validate_delivery(PROJECT_ROOT) == []
