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
MATRIX_20_PATH = Path("evaluation/matrix-20.json")
TRIALS_20_DIR = Path("evaluation/trials-20")
RESULTS_20_JSON = Path("evaluation/results-20.json")
RESULTS_20_MARKDOWN = Path("evaluation/results-20.md")

REQUIRED_FILES = (
    Path("README.md"),
    Path("pyproject.toml"),
    MATRIX_PATH,
    RESULTS_JSON,
    RESULTS_MARKDOWN,
    MATRIX_20_PATH,
    RESULTS_20_JSON,
    RESULTS_20_MARKDOWN,
    Path("docs/architecture-rationale.md"),
    Path("docs/evaluation-report.md"),
    Path("docs/failure-analysis.md"),
    Path("docs/next-10-hours.md"),
    Path("docs/ten-task-analysis.md"),
    Path("docs/expanded-ten-analysis.md"),
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
    "## 工具与职责",
    "## 五个关键 Prompt",
    "## AI 的有效贡献",
    "## AI 引入的问题与人工纠偏",
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
    return [
        *_validate_result_set(
            root,
            matrix_path=MATRIX_PATH,
            trials_dir=TRIALS_DIR,
            results_json=RESULTS_JSON,
            results_markdown=RESULTS_MARKDOWN,
            expected_task_count=10,
        ),
        *_validate_result_set(
            root,
            matrix_path=MATRIX_20_PATH,
            trials_dir=TRIALS_20_DIR,
            results_json=RESULTS_20_JSON,
            results_markdown=RESULTS_20_MARKDOWN,
            expected_task_count=20,
        ),
    ]


def _validate_result_set(
    root: Path,
    *,
    matrix_path: Path,
    trials_dir: Path,
    results_json: Path,
    results_markdown: Path,
    expected_task_count: int,
) -> list[str]:
    errors: list[str] = []
    matrix = load_matrix(root / matrix_path)
    summary = summarize_results(matrix, root / trials_dir)
    expected_json = json.loads((root / results_json).read_text(encoding="utf-8"))
    actual_json = summary.model_dump(mode="json")
    if actual_json != expected_json:
        errors.append(f"{results_json} is stale; regenerate it from {trials_dir}")
    expected_markdown = (root / results_markdown).read_text(encoding="utf-8")
    if render_markdown(summary) != expected_markdown:
        errors.append(f"{results_markdown} is stale; regenerate it from {trials_dir}")

    if summary.selected_tasks != expected_task_count:
        errors.append(f"{matrix_path} must select exactly {expected_task_count} tasks")
    if not summary.all_tasks_attempted or summary.not_run_tasks:
        errors.append(f"every task in {matrix_path} must be attempted")
    if summary.executed_tasks != summary.selected_tasks:
        errors.append(f"every task in {matrix_path} must have a trial result")
    expected_tasks = {task.name for task in matrix.tasks}
    errors.extend(_validate_snapshot_layout(root, trials_dir, expected_tasks))
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


def _validate_snapshot_layout(
    root: Path,
    trials_dir: Path,
    expected_tasks: set[str],
) -> list[str]:
    errors: list[str] = []
    snapshot_tasks: set[str] = set()
    for snapshot_path in sorted((root / trials_dir).rglob("result.json")):
        relative_path = snapshot_path.relative_to(root / trials_dir)
        if len(relative_path.parts) != 2:
            errors.append(f"{trials_dir} contains invalid trial path: {relative_path}")
            continue
        directory_task = relative_path.parts[0]
        snapshot_tasks.add(directory_task)
        try:
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            errors.append(f"{trials_dir} contains unreadable snapshot: {relative_path}")
            continue
        if not isinstance(snapshot, dict) or snapshot.get("task_name") != directory_task:
            errors.append(f"{trials_dir} snapshot task does not match path: {relative_path}")
    if snapshot_tasks != expected_tasks:
        errors.append(f"{trials_dir} must match the expected task set exactly")
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
