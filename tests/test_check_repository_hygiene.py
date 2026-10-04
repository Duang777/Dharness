from __future__ import annotations

from pathlib import Path

from scripts.check_repository_hygiene import find_violations
from scripts.sanitize_repository_artifacts import sanitize_json


def test_find_violations_reports_private_repository_metadata(tmp_path: Path) -> None:
    source = tmp_path / "artifact.md"
    source.write_text(
        "\n".join(
            (
                "https://internal." + "bytedance.net/v1",
                "/" + "Users/example/project",
                "/" + "absolute/path/config.env",
                "/" + "tmp/credentials.env",
            )
        ),
        encoding="utf-8",
    )

    violations = find_violations((source,), tmp_path)

    assert {(item.line, item.kind) for item in violations} == {
        (1, "internal service URL"),
        (2, "local macOS home path"),
        (3, "placeholder absolute path"),
        (4, "temporary credential file"),
    }


def test_find_violations_allows_relative_paths_and_public_urls(tmp_path: Path) -> None:
    source = tmp_path / "artifact.txt"
    source.write_text(
        "tools/runtime\nhttps://github.com/example/project\n$PROVIDER_ENV_FILE\n",
        encoding="utf-8",
    )

    assert find_violations((source,), tmp_path) == ()


def test_sanitize_json_removes_internal_routing_and_local_root(tmp_path: Path) -> None:
    source = {
        "api_base": "https://internal." + "bytedance.net/v1",
        "path": str(tmp_path / "evaluation/result.json"),
        "uri": "file://" + str(tmp_path / "runs/trial"),
        "model": "provider/model",
    }

    sanitized, changed = sanitize_json(source, tmp_path)

    assert changed is True
    assert sanitized == {
        "path": "evaluation/result.json",
        "uri": "runs/trial",
        "model": "provider/model",
    }
