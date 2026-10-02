from __future__ import annotations

import hashlib
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
MATRIX_89_PATH = Path("evaluation/matrix-89.json")
MATRIX_89_ERRORS_PATH = Path("evaluation/matrix-89-errors.json")
CANONICAL_89_PATH = Path("evaluation/canonical-89.json")
TRIALS_89_DIR = Path("evaluation/trials-89")
RESULTS_89_JSON = Path("evaluation/results-89.json")
RESULTS_89_MARKDOWN = Path("evaluation/results-89.md")

REQUIRED_FILES = (
    Path("README.md"),
    Path("pyproject.toml"),
    MATRIX_PATH,
    RESULTS_JSON,
    RESULTS_MARKDOWN,
    MATRIX_20_PATH,
    RESULTS_20_JSON,
    RESULTS_20_MARKDOWN,
    MATRIX_89_PATH,
    MATRIX_89_ERRORS_PATH,
    CANONICAL_89_PATH,
    RESULTS_89_JSON,
    RESULTS_89_MARKDOWN,
    Path("evaluation/matrix-prefixbench-development.json"),
    Path("evaluation/matrix-prefixbench-test.json"),
    Path("evaluation/prefixbench-readiness.json"),
    Path("evaluation/prefixbench-v1-development-canonical.json"),
    Path("evaluation/prefixbench-v1-development-offline-analysis.json"),
    Path("evaluation/prefixbench-v1-development-offline-campaign.json"),
    Path("evaluation/prefixbench-v1-development-readiness.json"),
    Path("evaluation/completion-calibration.json"),
    Path("evaluation/completion-disagreements.json"),
    Path("evaluation/completion-isolation-experiments.json"),
    Path("evaluation/completion-isolation-support.json"),
    Path("docs/architecture-rationale.md"),
    Path("docs/completion-calibration-design.md"),
    Path("docs/completion-calibration-research.md"),
    Path("docs/completion-isolation-experiments.md"),
    Path("docs/completion-isolation-support.md"),
    Path("docs/evaluation-report.md"),
    Path("docs/failure-analysis.md"),
    Path("docs/isolated-verification-design.md"),
    Path("docs/isolated-verification-runtime-research.md"),
    Path("docs/next-10-hours.md"),
    Path("docs/prefixbench-collection-design.md"),
    Path("docs/prefixbench-design.md"),
    Path("docs/prefixbench-analysis-design.md"),
    Path("docs/prefixbench-mutation-campaign-design.md"),
    Path("docs/terminal-agent-isolation-research.md"),
    Path("docs/ten-task-analysis.md"),
    Path("docs/expanded-ten-analysis.md"),
    Path("docs/vibe-coding-log.md"),
    Path("scripts/completion_calibration.py"),
    Path("scripts/completion_isolation_census.py"),
    Path("scripts/completion_isolation_experiments.py"),
    Path("scripts/prefixbench.py"),
    Path("scripts/prefixbench_analysis.py"),
    Path("scripts/prefixbench_campaign.py"),
    Path("experiments/prefixbench-v1/mutation-protocol-v1.json"),
    *(
        Path("evaluation/completion-isolation-trials") / task / relative
        for task in ("crack-7z-hash", "fix-ocaml-gc", "write-compressor")
        for relative in (
            Path("result.json"),
            Path("source-events.jsonl"),
            Path("agent/completion-isolation-experiment/replay/events.jsonl"),
            Path("agent/completion-isolation-experiment/completion/events.jsonl"),
        )
    ),
)

README_HEADINGS = (
    "## 成绩",
    "## 核心亮点",
    "## 工程方法与知识覆盖",
    "## 架构",
    "## 安装",
    "## 运行评测",
    "## 完整验收",
    "## 结果与限制",
    "## 交付文档",
)

VIBE_HEADINGS = (
    "## 工具与职责",
    "## 使用的 Skills",
    "## 本轮接触的新知识",
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
    results_89 = json.loads((root / RESULTS_89_JSON).read_text(encoding="utf-8"))

    for heading in README_HEADINGS:
        if heading not in readme:
            errors.append(f"README is missing heading: {heading}")
    result_disclosures = (
        (f"| 已执行并评分 | {results_89['executed_tasks']} / {results_89['selected_tasks']} |"),
        f"| Harbor reward 1.0 | {results_89['passed_tasks']} |",
        f"| Harbor reward 0 | {results_89['failed_tasks']} |",
        (
            "| Attempted / scored pass rate | "
            f"{results_89['pass_rate']:.1%} / {results_89['scored_pass_rate']:.1%} |"
        ),
        (
            "| Execution / scored coverage | "
            f"{results_89['execution_coverage']:.0%} / "
            f"{results_89['scored_coverage']:.0%} |"
        ),
        "live",
        "replay",
    )
    for value in result_disclosures:
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
    errors.extend(
        _validate_canonical_manifest(
            root,
            manifest_path=CANONICAL_89_PATH,
            matrix_path=MATRIX_89_PATH,
            results_json=RESULTS_89_JSON,
        )
    )
    errors.extend(
        _validate_frozen_provenance(
            root,
            TRIALS_89_DIR,
            manifest_path=CANONICAL_89_PATH,
        )
    )
    errors.extend(
        _validate_zero_error_retry_matrix(
            root,
            retry_matrix_path=MATRIX_89_ERRORS_PATH,
            results_json=RESULTS_89_JSON,
        )
    )
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
        *_validate_result_set(
            root,
            matrix_path=MATRIX_89_PATH,
            trials_dir=TRIALS_89_DIR,
            results_json=RESULTS_89_JSON,
            results_markdown=RESULTS_89_MARKDOWN,
            expected_task_count=89,
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


def _validate_canonical_manifest(
    root: Path,
    *,
    manifest_path: Path,
    matrix_path: Path,
    results_json: Path,
) -> list[str]:
    manifest = json.loads((root / manifest_path).read_text(encoding="utf-8"))
    matrix = load_matrix(root / matrix_path)
    summary = json.loads((root / results_json).read_text(encoding="utf-8"))
    errors: list[str] = []

    if manifest.get("schema_version") != 1:
        errors.append(f"{manifest_path} has an unsupported schema version")
    if manifest.get("dataset") != matrix.dataset:
        errors.append(f"{manifest_path} dataset does not match {matrix_path}")
    matrix_sha256 = hashlib.sha256((root / matrix_path).read_bytes()).hexdigest()
    if manifest.get("matrix_sha256") != matrix_sha256:
        errors.append(f"{manifest_path} matrix hash does not match {matrix_path}")

    rows = manifest.get("tasks")
    summary_rows = summary.get("tasks")
    if not isinstance(rows, list) or not isinstance(summary_rows, list):
        return [*errors, f"{manifest_path} and {results_json} must contain task lists"]

    expected_names = [task.name for task in matrix.tasks]
    names = [row.get("name") for row in rows if isinstance(row, dict)]
    if names != expected_names:
        errors.append(f"{manifest_path} task order does not match {matrix_path}")
    if len(rows) != len(names):
        errors.append(f"{manifest_path} contains a non-object task row")

    canonical_outcomes = [
        (
            row.get("name"),
            row.get("status"),
            row.get("reward"),
            row.get("result_sha256"),
        )
        for row in rows
        if isinstance(row, dict)
    ]
    summary_outcomes = [
        (
            row.get("name"),
            row.get("status"),
            row.get("reward"),
            row.get("source_result_sha256"),
        )
        for row in summary_rows
        if isinstance(row, dict)
    ]
    if canonical_outcomes != summary_outcomes:
        errors.append(f"{manifest_path} outcomes do not match {results_json}")

    counts = manifest.get("counts")
    expected_counts = {
        "completed": summary.get("executed_tasks"),
        "passed": summary.get("passed_tasks"),
        "failed": summary.get("failed_tasks"),
        "error": summary.get("errored_tasks"),
    }
    if counts != expected_counts:
        errors.append(f"{manifest_path} counts do not match {results_json}")

    for row in rows:
        if not isinstance(row, dict):
            continue
        result_path = row.get("result_path")
        if not isinstance(result_path, str) or not result_path:
            errors.append(f"{manifest_path} task has no result path: {row.get('name')}")
            continue
        result_path_object = Path(result_path)
        source_path: Path | None = None
        if result_path_object.is_absolute():
            errors.append(f"{manifest_path} contains an absolute result path: {row.get('name')}")
        elif not (root / result_path_object).resolve().is_relative_to(root.resolve()):
            errors.append(f"{manifest_path} result path escapes the repository: {row.get('name')}")
        elif result_path_object.parts[:2] != ("runs", "terminal-bench-2"):
            errors.append(
                f"{manifest_path} result path is outside the run store: {row.get('name')}"
            )
        else:
            source_path = root / result_path_object
        result_sha256 = row.get("result_sha256")
        if not isinstance(result_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", result_sha256):
            errors.append(f"{manifest_path} task has no valid result hash: {row.get('name')}")
        elif source_path is not None and source_path.is_file():
            actual_sha256 = hashlib.sha256(source_path.read_bytes()).hexdigest()
            if actual_sha256 != result_sha256:
                errors.append(
                    f"{manifest_path} source result hash does not match: {row.get('name')}"
                )
        for field in ("config_sha256", "task_checksum"):
            value = row.get(field)
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                errors.append(f"{manifest_path} task has no valid {field}: {row.get('name')}")
        task_commit = row.get("task_git_commit_id")
        if not isinstance(task_commit, str) or not re.fullmatch(r"[0-9a-f]{40}", task_commit):
            errors.append(f"{manifest_path} task has no valid upstream commit: {row.get('name')}")
        task_repository = row.get("task_git_url")
        if not isinstance(task_repository, str) or not task_repository.startswith("https://"):
            errors.append(
                f"{manifest_path} task has no valid upstream repository: {row.get('name')}"
            )
    return errors


def _validate_frozen_provenance(
    root: Path,
    trials_dir: Path,
    *,
    manifest_path: Path | None = None,
) -> list[str]:
    errors: list[str] = []
    git_commits: set[str] = set()
    git_urls: set[str] = set()
    manifest_rows: dict[str, dict[str, object]] = {}
    if manifest_path is not None:
        manifest = json.loads((root / manifest_path).read_text(encoding="utf-8"))
        rows = manifest.get("tasks")
        if isinstance(rows, list):
            manifest_rows = {
                str(row.get("name")): row
                for row in rows
                if isinstance(row, dict) and isinstance(row.get("name"), str)
            }
    for snapshot_path in sorted((root / trials_dir).glob("*/result.json")):
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        task_name = snapshot.get("task_name")
        provenance = snapshot.get("snapshot")
        task = snapshot.get("task")
        config = snapshot.get("config")
        if not isinstance(provenance, dict) or provenance.get("schema_version") != 2:
            errors.append(f"{trials_dir} snapshot has unsupported provenance: {task_name}")
            continue
        for field in ("source_result_sha256", "source_config_sha256"):
            value = provenance.get(field)
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                errors.append(f"{trials_dir} snapshot has no valid {field}: {task_name}")
        if not isinstance(task, dict):
            errors.append(f"{trials_dir} snapshot has no task provenance: {task_name}")
            continue
        checksum = task.get("checksum")
        git_commit = task.get("git_commit_id")
        git_url = task.get("git_url")
        if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-f]{64}", checksum):
            errors.append(f"{trials_dir} snapshot has no valid task checksum: {task_name}")
        if not isinstance(git_commit, str) or not re.fullmatch(r"[0-9a-f]{40}", git_commit):
            errors.append(f"{trials_dir} snapshot has no valid task commit: {task_name}")
        else:
            git_commits.add(git_commit)
        if not isinstance(git_url, str) or not git_url.startswith("https://"):
            errors.append(f"{trials_dir} snapshot has no valid task repository: {task_name}")
        else:
            git_urls.add(git_url)
        if not isinstance(config, dict):
            errors.append(f"{trials_dir} snapshot has no sanitized configuration: {task_name}")
        replay = _mapping(
            _mapping(_mapping(snapshot.get("agent_result")).get("metadata")).get(
                "evidence_harness_replay"
            )
        )
        if replay and replay.get("replay_mode") not in {
            "recorded_receipts",
            "recorded_receipts_v1",
        }:
            errors.append(f"{trials_dir} snapshot used legacy replay: {task_name}")

        manifest_row = manifest_rows.get(str(task_name))
        if manifest_row is not None:
            manifest_provenance = {
                "config_sha256": manifest_row.get("config_sha256"),
                "task_checksum": manifest_row.get("task_checksum"),
                "task_git_url": manifest_row.get("task_git_url"),
                "task_git_commit_id": manifest_row.get("task_git_commit_id"),
            }
            snapshot_provenance = {
                "config_sha256": provenance.get("source_config_sha256"),
                "task_checksum": task.get("checksum"),
                "task_git_url": task.get("git_url"),
                "task_git_commit_id": task.get("git_commit_id"),
            }
            if manifest_provenance != snapshot_provenance:
                errors.append(
                    f"{trials_dir} snapshot provenance does not match manifest: {task_name}"
                )
        result_path = manifest_row.get("result_path") if manifest_row is not None else None
        if not isinstance(result_path, str):
            continue
        relative_result_path = Path(result_path)
        if (
            relative_result_path.is_absolute()
            or not (root / relative_result_path).resolve().is_relative_to(root.resolve())
            or relative_result_path.parts[:2] != ("runs", "terminal-bench-2")
        ):
            continue
        source_path = root / relative_result_path
        if not source_path.is_file():
            continue
        try:
            source_bytes = source_path.read_bytes()
            source = json.loads(source_bytes)
            source_config_bytes = (source_path.parent / "config.json").read_bytes()
        except (OSError, json.JSONDecodeError):
            errors.append(f"{trials_dir} cannot read local source provenance: {task_name}")
            continue
        if not isinstance(source, dict):
            errors.append(f"{trials_dir} local source is not an object: {task_name}")
            continue
        source_task_id = _mapping(source.get("task_id"))
        source_task_config = _mapping(_mapping(source.get("config")).get("task"))
        expected_task = {
            "source": source.get("source") or source_task_config.get("source"),
            "checksum": source.get("task_checksum"),
            "git_url": source_task_id.get("git_url") or source_task_config.get("git_url"),
            "git_commit_id": (
                source_task_id.get("git_commit_id") or source_task_config.get("git_commit_id")
            ),
        }
        if provenance.get("source_result_sha256") != hashlib.sha256(source_bytes).hexdigest():
            errors.append(f"{trials_dir} result hash does not match local source: {task_name}")
        if (
            provenance.get("source_config_sha256")
            != hashlib.sha256(source_config_bytes).hexdigest()
        ):
            errors.append(f"{trials_dir} config hash does not match local source: {task_name}")
        if task != expected_task:
            errors.append(f"{trials_dir} snapshot does not match local source: {task_name}")
    if len(git_commits) != 1:
        errors.append(f"{trials_dir} snapshots must use one task commit")
    if len(git_urls) != 1:
        errors.append(f"{trials_dir} snapshots must use one task repository")
    return errors


def _validate_zero_error_retry_matrix(
    root: Path,
    *,
    retry_matrix_path: Path,
    results_json: Path,
) -> list[str]:
    retry_matrix = json.loads((root / retry_matrix_path).read_text(encoding="utf-8"))
    summary = json.loads((root / results_json).read_text(encoding="utf-8"))
    errors: list[str] = []
    if retry_matrix.get("schema_version") != 1:
        errors.append(f"{retry_matrix_path} has an unsupported schema version")
    if retry_matrix.get("dataset") != summary.get("dataset"):
        errors.append(f"{retry_matrix_path} dataset does not match {results_json}")
    tasks = retry_matrix.get("tasks")
    if summary.get("errored_tasks") == 0 and tasks != []:
        errors.append(f"{retry_matrix_path} must be empty when canonical error count is zero")
    if summary.get("errored_tasks") != 0 and not tasks:
        errors.append(f"{retry_matrix_path} omits canonical error tasks")
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


def _mapping(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


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
