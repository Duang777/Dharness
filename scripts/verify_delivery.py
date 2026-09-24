from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from evidence_harness.evaluation import load_matrix, render_markdown, summarize_results

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MATRIX_PATH = Path("evaluation/matrix.json")
TRIALS_DIR = Path("evaluation/trials")
RESULTS_JSON = Path("evaluation/results.json")
RESULTS_MARKDOWN = Path("evaluation/results.md")

REQUIRED_FILES = (
    Path("README.md"),
    Path("pyproject.toml"),
    MATRIX_PATH,
    RESULTS_JSON,
    RESULTS_MARKDOWN,
    Path("docs/architecture-rationale.md"),
    Path("docs/evaluation-report.md"),
    Path("docs/failure-analysis.md"),
    Path("docs/next-10-hours.md"),
    Path("docs/vibe-coding-log.md"),
)

README_HEADINGS = (
    "## 成绩",
    "## 核心亮点",
    "## 架构",
    "## 安装",
    "## 运行评测",
    "## 完整验收",
    "## 结果与限制",
    "## 交付文档",
)

VIBE_HEADINGS = (
    "## 使用工具",
    "## 5 个关键 Prompt",
    "## AI 帮了什么",
    "## AI 坑了什么",
)

SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\b[A-Fa-f0-9]{32}\.[A-Za-z0-9_-]{12,}\b"),
)
ALLOWED_TEST_SECRETS = frozenset({"sk-proj-abcdefghijklmnopqrstuvwxyz123456"})


def validate_delivery(root: Path = PROJECT_ROOT) -> list[str]:
    errors: list[str] = []
    for relative_path in REQUIRED_FILES:
        if not (root / relative_path).is_file():
            errors.append(f"missing required file: {relative_path}")
    if errors:
        return errors

    readme = (root / "README.md").read_text(encoding="utf-8")
    architecture = (root / "docs/architecture-rationale.md").read_text(encoding="utf-8")
    failures = (root / "docs/failure-analysis.md").read_text(encoding="utf-8")
    vibe_log = (root / "docs/vibe-coding-log.md").read_text(encoding="utf-8")

    for heading in README_HEADINGS:
        if heading not in readme:
            errors.append(f"README is missing heading: {heading}")
    for value in ("90%", "100%", "live", "replay"):
        if value not in readme:
            errors.append(f"README is missing result disclosure: {value}")
    errors.extend(_broken_local_links(root, readme))

    if len(architecture.split()) < 800:
        errors.append("architecture document must contain at least 800 words")
    if "```mermaid" not in architecture:
        errors.append("architecture document must contain a Mermaid flowchart")
    if len(re.findall(r"^## \d+\.", failures, flags=re.MULTILINE)) < 3:
        errors.append("failure analysis must contain at least three numbered cases")

    for heading in VIBE_HEADINGS:
        if heading not in vibe_log:
            errors.append(f"Vibe Coding log is missing heading: {heading}")
    if len(re.findall(r"^> [1-5]\.", vibe_log, flags=re.MULTILINE)) != 5:
        errors.append("Vibe Coding log must include exactly five numbered prompts")

    errors.extend(_validate_results(root))
    errors.extend(_scan_repository_secrets(root))
    return errors


def _validate_results(root: Path) -> list[str]:
    errors: list[str] = []
    matrix = load_matrix(root / MATRIX_PATH)
    summary = summarize_results(matrix, root / TRIALS_DIR)
    expected_json = json.loads((root / RESULTS_JSON).read_text(encoding="utf-8"))
    actual_json = summary.model_dump(mode="json")
    if actual_json != expected_json:
        errors.append("evaluation/results.json is stale; regenerate it from evaluation/trials")
    expected_markdown = (root / RESULTS_MARKDOWN).read_text(encoding="utf-8")
    if render_markdown(summary) != expected_markdown:
        errors.append("evaluation/results.md is stale; regenerate it from evaluation/trials")

    if summary.selected_tasks < 10:
        errors.append("evaluation must select at least 10 tasks")
    if not summary.all_tasks_attempted or summary.not_run_tasks:
        errors.append("every selected evaluation task must be attempted")
    if summary.executed_tasks != summary.selected_tasks:
        errors.append("every selected evaluation task must have a trial result")
    expected_tasks = {task.name for task in matrix.tasks}
    snapshot_tasks = {path.parent.name for path in (root / TRIALS_DIR).glob("*/result.json")}
    if snapshot_tasks != expected_tasks:
        errors.append("evaluation/trials must match the evaluation matrix exactly")
    for task in summary.tasks:
        if task.execution_mode == "live" and not task.model_name:
            errors.append(f"trial has no model provenance: {task.name}")
        if task.execution_mode == "replay" and not task.source_journal_sha256:
            errors.append(f"replay has no source journal hash: {task.name}")
        if task.execution_mode != "not_run" and not (
            task.source_result_sha256 and re.fullmatch(r"[0-9a-f]{64}", task.source_result_sha256)
        ):
            errors.append(f"trial has no source result hash: {task.name}")
    return errors


def _broken_local_links(root: Path, markdown: str) -> list[str]:
    errors: list[str] = []
    for target in re.findall(r"\[[^\]]+\]\(([^)]+)\)", markdown):
        if target.startswith(("http://", "https://", "#", "mailto:")):
            continue
        relative = target.split("#", 1)[0]
        if relative and not (root / relative).exists():
            errors.append(f"README contains broken local link: {target}")
    return errors


def _scan_repository_secrets(root: Path) -> list[str]:
    completed = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=root,
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        return ["cannot list repository files for credential scan"]

    errors: list[str] = []
    for raw_path in completed.stdout.split(b"\0"):
        if not raw_path:
            continue
        relative_path = raw_path.decode("utf-8", errors="surrogateescape")
        path = root / relative_path
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for pattern in SECRET_PATTERNS:
            matches = {match.group(0) for match in pattern.finditer(text)}
            if matches - ALLOWED_TEST_SECRETS:
                errors.append(f"possible credential in tracked file: {relative_path}")
                break
    return errors


def main() -> int:
    errors = validate_delivery()
    if errors:
        print("delivery verification failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("delivery verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
