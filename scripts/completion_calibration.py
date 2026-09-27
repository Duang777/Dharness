from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from evidence_harness.policy import is_sensitive_field, redact_sensitive

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CANONICAL = PROJECT_ROOT / "evaluation" / "canonical-89.json"
DEFAULT_MATRIX = PROJECT_ROOT / "evaluation" / "matrix-89.json"
DEFAULT_CORPUS = PROJECT_ROOT / "evaluation" / "completion-disagreements.json"
DEFAULT_REPORT = PROJECT_ROOT / "evaluation" / "completion-calibration.json"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
ISOLATABLE_REJECTION_PATTERNS = (
    re.compile(r"^verification check '.+' appears to modify task state$"),
    re.compile(r"^verification check shell syntax cannot be inspected$"),
)
OMITTED_OUTPUT = "[OMITTED FROM COMMITTED CORPUS]"
LEGACY_DIALECT = "legacy_review_first_unversioned_v0"
ISOLATED_DIALECT = "isolated_receipt_first_v2"
MIXED_DIALECT = "mixed_legacy_v1_and_isolated_v2"


@dataclass(frozen=True, slots=True)
class CohortExpectation:
    total: int = 89
    live: int = 85
    replay: int = 4
    false_positive: int = 10
    false_negative: int = 12


DEFAULT_EXPECTATION = CohortExpectation()


@dataclass(frozen=True, slots=True)
class _Event:
    line: int
    line_sha256: str
    timestamp: str
    type: str
    payload: dict[str, Any]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build and analyze the frozen completion-disagreement corpus."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build", help="Build corpus and calibration artifacts.")
    build.add_argument("--canonical", type=Path, default=DEFAULT_CANONICAL)
    build.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    build.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    build.add_argument("--corpus-out", type=Path, default=DEFAULT_CORPUS)
    build.add_argument("--report-out", type=Path, default=DEFAULT_REPORT)

    analyze = subparsers.add_parser("analyze", help="Analyze an existing corpus.")
    analyze.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    analyze.add_argument("--report-out", type=Path)

    check = subparsers.add_parser("check", help="Check committed artifacts against sources.")
    check.add_argument("--canonical", type=Path, default=DEFAULT_CANONICAL)
    check.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    check.add_argument("--project-root", type=Path, default=PROJECT_ROOT)
    check.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    check.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    return parser.parse_args()


def build_completion_corpus(
    canonical_path: Path,
    matrix_path: Path,
    project_root: Path,
    *,
    expected: CohortExpectation = DEFAULT_EXPECTATION,
) -> dict[str, Any]:
    canonical_bytes, canonical = _read_json_object(canonical_path)
    matrix_bytes, matrix = _read_json_object(matrix_path)
    _validate_manifest_header(canonical, matrix, matrix_bytes, expected)

    matrix_tasks = matrix.get("tasks")
    canonical_tasks = canonical.get("tasks")
    assert isinstance(matrix_tasks, list)
    assert isinstance(canonical_tasks, list)

    expected_names = [
        _required_string(_object(item, "matrix task"), "name") for item in matrix_tasks
    ]
    canonical_names = [
        _required_string(_object(item, "canonical task"), "name") for item in canonical_tasks
    ]
    if canonical_names != expected_names:
        raise ValueError("canonical task order does not match the matrix")

    cases: list[dict[str, Any]] = []
    live_count = 0
    replay_count = 0
    false_positive_count = 0
    false_negative_count = 0
    source_dialects: set[str] = set()

    for row_value in canonical_tasks:
        row = _object(row_value, "canonical task")
        source = _load_source(row, project_root)
        agent_result = _object(source["result"].get("agent_result"), "agent_result")
        metadata = _object(agent_result.get("metadata"), "metadata")
        harness = _optional_object(metadata.get("evidence_harness"))
        replay = _optional_object(metadata.get("evidence_harness_replay"))

        if replay and not harness.get("stop_reason"):
            replay_count += 1
            continue
        stop_reason = harness.get("stop_reason")
        if not isinstance(stop_reason, str) or not stop_reason:
            raise ValueError(f"live task has no Harness stop reason: {source['task_name']}")
        live_count += 1

        reward = _reward(source["result"])
        externally_passed = reward == 1.0
        internally_verified = stop_reason == "verified"
        if externally_passed == internally_verified:
            continue

        if internally_verified:
            disagreement = "false_positive"
            false_positive_count += 1
        else:
            disagreement = "false_negative"
            false_negative_count += 1
        case, source_dialect = _build_case(
            row=row,
            source=source,
            dataset=_required_string(canonical, "dataset"),
            project_root=project_root,
            disagreement=disagreement,
            reward=reward,
            harness=harness,
        )
        cases.append(case)
        source_dialects.add(source_dialect)

    observed = CohortExpectation(
        total=len(canonical_tasks),
        live=live_count,
        replay=replay_count,
        false_positive=false_positive_count,
        false_negative=false_negative_count,
    )
    if observed != expected:
        raise ValueError(f"completion cohort changed: expected {expected}, observed {observed}")

    return {
        "schema_version": 1,
        "source_dialect": _combined_source_dialect(source_dialects),
        "dataset": _required_string(canonical, "dataset"),
        "sources": {
            "canonical": _file_binding(canonical_path, canonical_bytes, project_root),
            "matrix": _file_binding(matrix_path, matrix_bytes, project_root),
        },
        "selection": {
            "execution_mode": "live",
            "predicate": "(reward == 1.0) XOR (stop_reason == 'verified')",
        },
        "counts": {
            "canonical": observed.total,
            "live": observed.live,
            "replay": observed.replay,
            "cases": len(cases),
            "false_positive": observed.false_positive,
            "false_negative": observed.false_negative,
        },
        "cases": cases,
    }


def analyze_completion_corpus(corpus: dict[str, Any]) -> dict[str, Any]:
    validate_completion_corpus(corpus)
    cases = [_object(item, "corpus case") for item in _list(corpus, "cases")]

    recorded_decisions = {
        _required_string(case, "case_id"): _required_bool(case, "recorded_internal_verified")
        for case in cases
    }
    review_required_decisions = {
        _required_string(case, "case_id"): _review_required_decision(_policy_view(case))
        for case in cases
    }

    policies = [
        _policy_report(
            name="recorded_terminal_v1",
            description="Reproduce the terminal Harness decision recorded by the source run.",
            cases=cases,
            decisions=recorded_decisions,
        ),
        _policy_report(
            name="same_attempt_review_required_v1",
            description=(
                "Accept only when the accepted verification receipt belongs to an attempt "
                "with an accepting review in the same event window."
            ),
            cases=cases,
            decisions=review_required_decisions,
        ),
    ]

    isolation_candidates: list[dict[str, Any]] = []
    for case in cases:
        view = _policy_view(case)
        verifier = _object(case.get("verifier"), "verifier")
        externally_passed = _required_bool(verifier, "passed")
        for attempt in view["attempts"]:
            reasons = [
                reason
                for rejection in attempt["rejections"]
                for reason in _string_list(rejection["payload"].get("reasons"))
            ]
            if not reasons or not all(_is_isolatable_rejection(reason) for reason in reasons):
                continue
            if not externally_passed:
                continue
            isolation_candidates.append(
                {
                    "case_id": view["case_id"],
                    "task_name": case["task_name"],
                    "attempt_ordinal": attempt["ordinal"],
                    "check_ids": [check["id"] for check in attempt["proposal"]["checks"]],
                    "rejection_reasons": reasons,
                    "attribution": (
                        "prospective_replay_required"
                        if attempt["later_change_receipts"]
                        else "same_artifact"
                    ),
                    "later_change_receipts": attempt["later_change_receipts"],
                    "required_experiment": (
                        "execute checks in a disposable writable workspace, verify the live "
                        "workspace digest is unchanged, then run receipt-first review"
                    ),
                }
            )

    strict_policy = policies[1]
    proven_matches = [
        row
        for row in strict_policy["cases"]
        if row["changed_from_recorded"] and row["matches_external"]
    ]
    candidate_case_ids = {item["case_id"] for item in isolation_candidates}
    return {
        "schema_version": 1,
        "corpus_sha256": hashlib.sha256(dump_json(corpus)).hexdigest(),
        "claim_boundary": "historical_evidence_only_no_benchmark_score_projection",
        "observed": {
            "cases": len(cases),
            "false_positive": sum(case["disagreement"] == "false_positive" for case in cases),
            "false_negative": sum(case["disagreement"] == "false_negative" for case in cases),
        },
        "policies": policies,
        "proven_control_path_corrections": {
            "count": len(proven_matches),
            "case_ids": [row["case_id"] for row in proven_matches],
            "task_names": [row["task_name"] for row in proven_matches],
            "meaning": (
                "The recorded unsafe acceptance used a no-review terminal path that the "
                "review-required controller rejects."
            ),
        },
        "isolation_experiment_candidates": isolation_candidates,
        "coverage": {
            "proven_control_path_cases": len(proven_matches),
            "counterfactual_isolation_cases": len(candidate_case_ids),
            "distinct_cases": len({row["case_id"] for row in proven_matches} | candidate_case_ids),
            "known_disagreement_cases": len(cases),
        },
        "limitations": [
            (
                "The corpus contains only disagreements, so it cannot measure overall accuracy "
                "or collateral changes on agreement cases."
            ),
            (
                "Legacy completion reviews ran before check receipts and are not evidence that "
                "the current receipt-first reviewer would accept."
            ),
            (
                "Isolation candidates identify blocked checks only. They do not predict check "
                "success, semantic-review acceptance, or external reward."
            ),
            (
                "Journal hashes bind the bytes present now; canonical collection did not attest "
                "to those journals originally."
            ),
        ],
    }


def validate_completion_corpus(corpus: dict[str, Any]) -> None:
    if corpus.get("schema_version") != 1:
        raise ValueError("unsupported completion corpus schema")
    if corpus.get("source_dialect") not in {
        LEGACY_DIALECT,
        ISOLATED_DIALECT,
        MIXED_DIALECT,
    }:
        raise ValueError("unsupported completion corpus dialect")
    if _sanitize_for_export(corpus) != corpus:
        raise ValueError("completion corpus contains unsanitized values")

    counts = _object(corpus.get("counts"), "corpus counts")
    cases = [_object(item, "corpus case") for item in _list(corpus, "cases")]
    if counts.get("cases") != len(cases):
        raise ValueError("corpus case count does not match cases")
    expected_false_positive = sum(case.get("disagreement") == "false_positive" for case in cases)
    expected_false_negative = sum(case.get("disagreement") == "false_negative" for case in cases)
    if counts.get("false_positive") != expected_false_positive:
        raise ValueError("corpus false-positive count does not match cases")
    if counts.get("false_negative") != expected_false_negative:
        raise ValueError("corpus false-negative count does not match cases")

    case_ids: set[str] = set()
    indices: list[int] = []
    for case in cases:
        case_id = _required_string(case, "case_id")
        if not SHA256_PATTERN.fullmatch(case_id):
            raise ValueError(f"invalid case id: {case_id}")
        if case_id in case_ids:
            raise ValueError(f"duplicate case id: {case_id}")
        case_ids.add(case_id)
        index = _required_int(case, "canonical_index")
        indices.append(index)

        verifier = _object(case.get("verifier"), "verifier")
        externally_passed = _required_bool(verifier, "passed")
        if externally_passed != (verifier.get("reward") == 1.0):
            raise ValueError(f"verifier pass label disagrees with reward: {case['task_name']}")
        internally_verified = _required_bool(case, "recorded_internal_verified")
        expected_disagreement = "false_negative" if externally_passed else "false_positive"
        if case.get("disagreement") != expected_disagreement:
            raise ValueError(f"wrong disagreement kind: {case['task_name']}")
        if externally_passed == internally_verified:
            raise ValueError(f"case is not a disagreement: {case['task_name']}")

        source = _object(case.get("sources"), "case sources")
        for name in ("result", "config", "journal"):
            binding = _object(source.get(name), f"{name} binding")
            digest = _required_string(binding, "sha256")
            if not SHA256_PATTERN.fullmatch(digest):
                raise ValueError(f"invalid {name} hash: {case['task_name']}")
        expected_case_id = _case_id(
            _required_string(corpus, "dataset"),
            _required_string(case, "task_name"),
            _required_string(_object(source["result"], "result binding"), "sha256"),
            _required_string(_object(source["journal"], "journal binding"), "sha256"),
        )
        if case_id != expected_case_id:
            raise ValueError(f"case id does not match source bindings: {case['task_name']}")

        attempts = [_object(item, "completion attempt") for item in _list(case, "attempts")]
        if not attempts:
            raise ValueError(f"disagreement case has no finish attempt: {case['task_name']}")
        ordinals = [_required_int(attempt, "ordinal") for attempt in attempts]
        if ordinals != list(range(1, len(attempts) + 1)):
            raise ValueError(f"attempt ordinals are not contiguous: {case['task_name']}")

    if indices != sorted(indices):
        raise ValueError("corpus cases are not in canonical order")


def check_artifacts(
    *,
    canonical_path: Path,
    matrix_path: Path,
    project_root: Path,
    corpus_path: Path,
    report_path: Path,
    expected: CohortExpectation = DEFAULT_EXPECTATION,
) -> list[str]:
    errors: list[str] = []
    try:
        _, corpus = _read_json_object(corpus_path)
        validate_completion_corpus(corpus)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [f"invalid completion corpus: {exc}"]

    try:
        expected_report = analyze_completion_corpus(corpus)
        actual_report_bytes, actual_report = _read_json_object(report_path)
        if actual_report != expected_report or actual_report_bytes != dump_json(expected_report):
            errors.append("completion calibration report is stale")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        errors.append(f"invalid completion calibration report: {exc}")

    try:
        canonical_bytes, canonical = _read_json_object(canonical_path)
        matrix_bytes, matrix = _read_json_object(matrix_path)
        _validate_manifest_header(canonical, matrix, matrix_bytes, expected)
        source = _object(corpus.get("sources"), "corpus sources")
        _validate_file_binding(
            _object(source.get("canonical"), "canonical binding"),
            canonical_path,
            canonical_bytes,
            project_root,
        )
        _validate_file_binding(
            _object(source.get("matrix"), "matrix binding"),
            matrix_path,
            matrix_bytes,
            project_root,
        )
        _validate_corpus_canonical_bindings(corpus, canonical)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        errors.append(f"completion corpus does not match canonical sources: {exc}")
        return errors

    rows = [_object(item, "canonical task") for item in _list(canonical, "tasks")]
    source_paths = [_resolve_source_path(row, project_root) for row in rows]
    existing = sum(path.is_file() for path in source_paths)
    if existing not in {0, len(source_paths)}:
        errors.append("only part of the canonical raw result set is available")
    elif existing == len(source_paths):
        try:
            rebuilt = build_completion_corpus(
                canonical_path,
                matrix_path,
                project_root,
                expected=expected,
            )
            if dump_json(rebuilt) != dump_json(corpus):
                errors.append("completion corpus is stale")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"cannot rebuild completion corpus: {exc}")
    return errors


def dump_json(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode()


def _validate_manifest_header(
    canonical: dict[str, Any],
    matrix: dict[str, Any],
    matrix_bytes: bytes,
    expected: CohortExpectation,
) -> None:
    if canonical.get("schema_version") != 1:
        raise ValueError("unsupported canonical manifest")
    if matrix.get("schema_version") != 1:
        raise ValueError("unsupported matrix")
    if canonical.get("dataset") != matrix.get("dataset"):
        raise ValueError("canonical dataset does not match the matrix")
    if canonical.get("matrix_sha256") != hashlib.sha256(matrix_bytes).hexdigest():
        raise ValueError("canonical matrix hash does not match the matrix")
    rows = canonical.get("tasks")
    if not isinstance(rows, list) or len(rows) != expected.total:
        raise ValueError(f"canonical manifest must contain {expected.total} tasks")


def _validate_corpus_canonical_bindings(
    corpus: dict[str, Any],
    canonical: dict[str, Any],
) -> None:
    if corpus.get("dataset") != canonical.get("dataset"):
        raise ValueError("corpus dataset does not match canonical manifest")

    counts = _object(corpus.get("counts"), "corpus counts")
    rows = [_object(item, "canonical task") for item in _list(canonical, "tasks")]
    if counts.get("canonical") != len(rows):
        raise ValueError("corpus canonical count does not match canonical manifest")

    rows_by_index: dict[int, dict[str, Any]] = {}
    for row in rows:
        index = _required_int(row, "index")
        if index in rows_by_index:
            raise ValueError(f"duplicate canonical index: {index}")
        rows_by_index[index] = row

    for case_value in _list(corpus, "cases"):
        case = _object(case_value, "corpus case")
        index = _required_int(case, "canonical_index")
        canonical_row = rows_by_index.get(index)
        if canonical_row is None:
            raise ValueError(f"case index is absent from canonical manifest: {index}")
        task_name = _required_string(case, "task_name")
        if task_name != canonical_row.get("name"):
            raise ValueError(f"case task does not match canonical manifest: {task_name}")

        sources = _object(case.get("sources"), "case sources")
        result_binding = _object(sources.get("result"), "result binding")
        config_binding = _object(sources.get("config"), "config binding")
        journal_binding = _object(sources.get("journal"), "journal binding")
        result_path = Path(_required_string(canonical_row, "result_path"))
        expected_bindings = {
            "result path": (result_binding.get("path"), str(result_path)),
            "result hash": (
                result_binding.get("sha256"),
                canonical_row.get("result_sha256"),
            ),
            "config path": (
                config_binding.get("path"),
                str(result_path.parent / "config.json"),
            ),
            "config hash": (
                config_binding.get("sha256"),
                canonical_row.get("config_sha256"),
            ),
            "journal path": (
                journal_binding.get("path"),
                str(result_path.parent / "agent" / "evidence-harness" / "events.jsonl"),
            ),
            "task checksum": (
                sources.get("task_checksum"),
                canonical_row.get("task_checksum"),
            ),
            "task git URL": (
                sources.get("task_git_url"),
                canonical_row.get("task_git_url"),
            ),
            "task git commit": (
                sources.get("task_git_commit_id"),
                canonical_row.get("task_git_commit_id"),
            ),
        }
        for label, (actual, expected) in expected_bindings.items():
            if actual != expected:
                raise ValueError(f"{label} does not match canonical manifest: {task_name}")

        verifier = _object(case.get("verifier"), "verifier")
        expected_verifier = {
            "reward": canonical_row.get("reward"),
            "status": canonical_row.get("status"),
            "exception_type": canonical_row.get("exception_type"),
        }
        for field, expected_value in expected_verifier.items():
            if verifier.get(field) != expected_value:
                raise ValueError(f"verifier {field} does not match canonical manifest: {task_name}")


def _load_source(row: dict[str, Any], project_root: Path) -> dict[str, Any]:
    task_name = _required_string(row, "name")
    result_path = _resolve_source_path(row, project_root)
    result_bytes, result = _read_json_object(result_path)
    expected_result_sha256 = _required_string(row, "result_sha256")
    if hashlib.sha256(result_bytes).hexdigest() != expected_result_sha256:
        raise ValueError(f"canonical result hash does not match: {task_name}")

    config_path = result_path.parent / "config.json"
    config_bytes, config = _read_json_object(config_path)
    expected_config_sha256 = _required_string(row, "config_sha256")
    if hashlib.sha256(config_bytes).hexdigest() != expected_config_sha256:
        raise ValueError(f"canonical config hash does not match: {task_name}")

    result_task_name = _task_name(result)
    if result_task_name != task_name:
        raise ValueError(f"canonical result task does not match: {task_name}")
    result_config = _object(result.get("config"), "result config")
    for section, field in (
        ("agent", "name"),
        ("task", "path"),
        ("task", "git_url"),
        ("task", "git_commit_id"),
    ):
        left = _object(result_config.get(section), f"result config {section}").get(field)
        right = _object(config.get(section), f"config {section}").get(field)
        if left != right:
            raise ValueError(f"result config does not match config.json: {task_name}")
    for field in ("job_id", "trial_name"):
        if result_config.get(field) != config.get(field):
            raise ValueError(f"result config does not match config.json: {task_name}")

    task_id = _optional_object(result.get("task_id"))
    task_config = _object(result_config.get("task"), "result task config")
    provenance = {
        "task_checksum": result.get("task_checksum"),
        "task_git_url": task_id.get("git_url") or task_config.get("git_url"),
        "task_git_commit_id": task_id.get("git_commit_id") or task_config.get("git_commit_id"),
    }
    for field, actual in provenance.items():
        if row.get(field) != actual:
            raise ValueError(f"canonical {field} does not match result: {task_name}")

    reward = _reward(result)
    if row.get("reward") != reward:
        raise ValueError(f"canonical reward does not match result: {task_name}")
    status = _result_status(result, reward)
    if row.get("status") != status:
        raise ValueError(f"canonical status does not match result: {task_name}")

    return {
        "task_name": task_name,
        "result_path": result_path,
        "result_bytes": result_bytes,
        "result": result,
        "config_path": config_path,
        "config_bytes": config_bytes,
    }


def _build_case(
    *,
    row: dict[str, Any],
    source: dict[str, Any],
    dataset: str,
    project_root: Path,
    disagreement: str,
    reward: float,
    harness: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    result_path = Path(source["result_path"])
    journal_path = result_path.parent / "agent" / "evidence-harness" / "events.jsonl"
    journal_bytes = journal_path.read_bytes()
    events = _parse_events(journal_bytes, journal_path)
    started = _single_event(events, "run_started", journal_path)
    finished = _single_event(events, "run_finished", journal_path)
    if finished.line != events[-1].line:
        raise ValueError(f"run_finished is not the final journal event: {journal_path}")
    journal_schema_version, source_dialect = _journal_dialect(started, journal_path)
    attempts = _build_attempts(
        events,
        journal_path,
        journal_schema_version=journal_schema_version,
    )

    stop_reason = _required_string(harness, "stop_reason")
    if finished.payload.get("stop_reason") != stop_reason:
        raise ValueError(f"run_finished stop reason does not match result: {source['task_name']}")
    if stop_reason == "verified" and (not attempts or attempts[-1]["outcome"] != "accepted"):
        raise ValueError(f"verified run has no accepted terminal attempt: {source['task_name']}")

    result_metadata = {
        field: harness.get(field)
        for field in (
            "phase",
            "turns_used",
            "environment_calls_used",
            "repairs_used",
            "recoveries_used",
            "work_epoch",
            "stop_reason",
            "failure_category",
            "latest_evidence_accepted",
        )
    }
    result_hash = hashlib.sha256(source["result_bytes"]).hexdigest()
    journal_hash = hashlib.sha256(journal_bytes).hexdigest()
    case_id = _case_id(dataset, source["task_name"], result_hash, journal_hash)
    case = {
        "case_id": case_id,
        "canonical_index": _required_int(row, "index"),
        "task_name": source["task_name"],
        "disagreement": disagreement,
        "recorded_internal_verified": stop_reason == "verified",
        "instruction": _required_string(started.payload, "instruction"),
        "run_options": _object(started.payload.get("options"), "run options"),
        "attempts": attempts,
        "terminal": {
            "event": _event_ref(finished),
            "journal_payload": finished.payload,
            "result_metadata": result_metadata,
        },
        "verifier": {
            "reward": reward,
            "passed": reward == 1.0,
            "status": _required_string(row, "status"),
            "exception_type": row.get("exception_type"),
        },
        "sources": {
            "result": _file_binding(
                Path(source["result_path"]),
                source["result_bytes"],
                project_root,
            ),
            "config": _file_binding(
                Path(source["config_path"]),
                source["config_bytes"],
                project_root,
            ),
            "journal": _file_binding(journal_path, journal_bytes, project_root),
            "task_checksum": row.get("task_checksum"),
            "task_git_url": row.get("task_git_url"),
            "task_git_commit_id": row.get("task_git_commit_id"),
        },
    }
    return _object(_sanitize_for_export(case), "sanitized corpus case"), source_dialect


def _build_attempts(
    events: list[_Event],
    journal_path: Path,
    *,
    journal_schema_version: int,
) -> list[dict[str, Any]]:
    if journal_schema_version == 1:
        return _build_attempts_for_dialect(
            events,
            journal_path,
            receipt_first=False,
        )
    if journal_schema_version == 2:
        return _build_attempts_for_dialect(
            events,
            journal_path,
            receipt_first=True,
        )
    raise ValueError(f"unsupported journal schema {journal_schema_version}: {journal_path}")


def _build_attempts_for_dialect(
    events: list[_Event],
    journal_path: Path,
    *,
    receipt_first: bool,
) -> list[dict[str, Any]]:
    decision_positions = [
        index
        for index, event in enumerate(events)
        if event.type == "agent_decision" and event.payload.get("action") == "finish"
    ]
    attempts: list[dict[str, Any]] = []
    owned_completion_lines: set[int] = set()
    for ordinal, start in enumerate(decision_positions, start=1):
        end = next(
            (
                index
                for index in range(start + 1, len(events))
                if events[index].type == "agent_decision"
            ),
            len(events),
        )
        segment = events[start + 1 : end]
        proposal = events[start].payload
        checks = [_object(item, "proposed check") for item in _list(proposal, "checks")]
        coverage = [_object(item, "requirement coverage") for item in _list(proposal, "coverage")]
        check_ids = [_required_string(check, "id") for check in checks]
        if len(check_ids) != len(set(check_ids)):
            raise ValueError(
                f"duplicate completion check id at {journal_path}:{events[start].line}"
            )
        check_receipt_events = [event for event in segment if event.type == "command_receipt"]
        if len(check_receipt_events) > len(checks):
            raise ValueError(f"too many check receipts at {journal_path}:{events[start].line}")
        for expected_check, receipt_event in zip(checks, check_receipt_events, strict=False):
            _validate_check_receipt(expected_check, receipt_event, journal_path)

        reviews = [
            event
            for event in segment
            if event.type in {"completion_review", "completion_review_error"}
        ]
        if len(reviews) > 1:
            raise ValueError(f"multiple completion reviews at {journal_path}:{events[start].line}")
        if reviews and check_receipt_events:
            review_line = reviews[0].line
            if not receipt_first and review_line > check_receipt_events[0].line:
                raise ValueError(
                    f"receipt-first events do not match the legacy dialect at "
                    f"{journal_path}:{events[start].line}"
                )
            if receipt_first and review_line < check_receipt_events[-1].line:
                raise ValueError(
                    f"review-first events do not match journal schema 2 at "
                    f"{journal_path}:{events[start].line}"
                )
        verification_events = [event for event in segment if event.type == "verification_receipt"]
        if len(verification_events) > 1:
            raise ValueError(
                f"multiple verification receipts at {journal_path}:{events[start].line}"
            )
        verification = _normalized_event(verification_events[0]) if verification_events else None
        if verification_events:
            receipt_checks = _list(verification_events[0].payload, "checks")
            expected_receipts = [event.payload for event in check_receipt_events]
            if receipt_checks != expected_receipts:
                raise ValueError(
                    f"verification receipt does not match check receipts at "
                    f"{journal_path}:{verification_events[0].line}"
                )
            if _list(verification_events[0].payload, "coverage") != coverage:
                raise ValueError(
                    f"verification receipt coverage does not match proposal at "
                    f"{journal_path}:{verification_events[0].line}"
                )
        if receipt_first:
            _validate_schema2_isolation(
                segment=segment,
                check_receipts=check_receipt_events,
                verification_events=verification_events,
                journal_path=journal_path,
                proposal_line=events[start].line,
            )

        rejection_events = [event for event in segment if event.type == "completion_rejected"]
        owned_completion_lines.update(event.line for event in reviews)
        owned_completion_lines.update(event.line for event in verification_events)
        owned_completion_lines.update(event.line for event in rejection_events)
        if receipt_first:
            owned_completion_lines.update(
                event.line for event in segment if event.type.startswith("completion_")
            )
        run_finished = [event for event in segment if event.type == "run_finished"]
        accepted = bool(
            verification_events
            and verification_events[0].payload.get("accepted") is True
            and run_finished
            and run_finished[0].payload.get("stop_reason") == "verified"
        )
        if accepted:
            outcome = "accepted"
        elif rejection_events or (
            verification_events and verification_events[0].payload.get("accepted") is False
        ):
            outcome = "rejected"
        else:
            outcome = "interrupted"

        prior_receipts = [event for event in events[:start] if event.type == "command_receipt"][-6:]
        later_change_receipts = [
            _event_ref(event)
            for event in events[end:]
            if event.type == "command_receipt" and event.payload.get("mode") == "change"
        ]
        attempts.append(
            {
                "ordinal": ordinal,
                "proposal_event": _event_ref(events[start]),
                "proposal": {
                    "rationale": proposal.get("rationale"),
                    "summary": proposal.get("summary"),
                    "checks": checks,
                    "coverage": coverage,
                },
                "supporting_receipts": [_normalized_event(event) for event in prior_receipts],
                "review": _normalized_event(reviews[0]) if reviews else None,
                "check_receipts": [_normalized_event(event) for event in check_receipt_events],
                "verification_receipt": verification,
                "rejections": [_normalized_event(event) for event in rejection_events],
                "later_change_receipts": later_change_receipts,
                "outcome": outcome,
            }
        )
    completion_events = set()
    for event in events:
        if (
            event.type == "verification_receipt"
            or event.type
            in {
                "completion_review",
                "completion_review_error",
                "completion_rejected",
            }
            or (receipt_first and event.type.startswith("completion_"))
        ):
            completion_events.add(event.line)
    if completion_events != owned_completion_lines:
        orphaned = sorted(completion_events - owned_completion_lines)
        raise ValueError(f"orphaned completion events in {journal_path}: {orphaned}")
    return attempts


def _validate_schema2_isolation(
    *,
    segment: list[_Event],
    check_receipts: list[_Event],
    verification_events: list[_Event],
    journal_path: Path,
    proposal_line: int,
) -> None:
    isolation_started = [event for event in segment if event.type == "completion_isolation_started"]
    isolation_failed = [event for event in segment if event.type == "completion_isolation_failed"]
    if len(isolation_started) > 1 or len(isolation_failed) > 1:
        raise ValueError(f"multiple isolation lifecycle events at {journal_path}:{proposal_line}")
    if check_receipts or verification_events:
        if len(isolation_started) != 1:
            raise ValueError(
                f"journal schema 2 completion evidence has no isolation start at "
                f"{journal_path}:{proposal_line}"
            )
        if check_receipts and isolation_started[0].line > check_receipts[0].line:
            raise ValueError(
                f"journal schema 2 isolation starts after its receipt at "
                f"{journal_path}:{proposal_line}"
            )
    if verification_events:
        isolation = _object(
            verification_events[0].payload.get("isolation"),
            "verification isolation",
        )
        attempt_id = _required_int(isolation, "attempt_id")
        if isolation_started[0].payload.get("attempt_id") != attempt_id:
            raise ValueError(
                f"journal schema 2 isolation attempt does not match at "
                f"{journal_path}:{proposal_line}"
            )
    if isolation_failed and verification_events:
        raise ValueError(
            f"failed isolation has a verification receipt at {journal_path}:{proposal_line}"
        )


def _journal_dialect(started: _Event, journal_path: Path) -> tuple[int, str]:
    raw_version = started.payload.get("journal_schema_version")
    if raw_version is None or raw_version == 1:
        return 1, LEGACY_DIALECT
    if raw_version == 2:
        return 2, ISOLATED_DIALECT
    raise ValueError(f"unsupported journal schema {raw_version}: {journal_path}")


def _combined_source_dialect(source_dialects: set[str]) -> str:
    if source_dialects == {LEGACY_DIALECT}:
        return LEGACY_DIALECT
    if source_dialects == {ISOLATED_DIALECT}:
        return ISOLATED_DIALECT
    if source_dialects == {LEGACY_DIALECT, ISOLATED_DIALECT}:
        return MIXED_DIALECT
    raise ValueError("completion corpus contains no supported journal dialect")


def _validate_check_receipt(
    check: dict[str, Any],
    event: _Event,
    journal_path: Path,
) -> None:
    receipt = event.payload
    expected = {
        "command_id": check.get("id"),
        "script": check.get("script"),
        "purpose": check.get("proves"),
        "cwd": check.get("cwd"),
        "mode": "observe",
    }
    actual = {field: receipt.get(field) for field in expected}
    if actual != expected:
        raise ValueError(f"check receipt does not match proposal at {journal_path}:{event.line}")


def _policy_view(case: dict[str, Any]) -> dict[str, Any]:
    attempts = []
    for attempt_value in _list(case, "attempts"):
        attempt = _object(attempt_value, "completion attempt")
        attempts.append(
            {
                "ordinal": _required_int(attempt, "ordinal"),
                "proposal": _object(attempt.get("proposal"), "proposal"),
                "review": attempt.get("review"),
                "check_receipts": _list(attempt, "check_receipts"),
                "verification_receipt": attempt.get("verification_receipt"),
                "rejections": _list(attempt, "rejections"),
                "later_change_receipts": _list(attempt, "later_change_receipts"),
            }
        )
    return {
        "case_id": _required_string(case, "case_id"),
        "instruction": _required_string(case, "instruction"),
        "run_options": _object(case.get("run_options"), "run options"),
        "attempts": attempts,
    }


def _review_required_decision(view: dict[str, Any]) -> bool:
    for attempt in view["attempts"]:
        verification = attempt["verification_receipt"]
        if not isinstance(verification, dict):
            continue
        payload = _object(verification.get("payload"), "verification receipt payload")
        if payload.get("accepted") is not True:
            continue
        review = attempt["review"]
        if not isinstance(review, dict) or review.get("type") != "completion_review":
            return False
        payload = _object(review.get("payload"), "completion review payload")
        return payload.get("verdict") == "accept"
    return False


def _policy_report(
    *,
    name: str,
    description: str,
    cases: list[dict[str, Any]],
    decisions: dict[str, bool],
) -> dict[str, Any]:
    rows = []
    for case in cases:
        case_id = _required_string(case, "case_id")
        decision = decisions[case_id]
        external = _required_bool(_object(case.get("verifier"), "verifier"), "passed")
        recorded = _required_bool(case, "recorded_internal_verified")
        rows.append(
            {
                "case_id": case_id,
                "task_name": case["task_name"],
                "decision": "accept" if decision else "reject",
                "matches_external": decision == external,
                "changed_from_recorded": decision != recorded,
            }
        )
    return {
        "name": name,
        "description": description,
        "decisions": {
            "accept": sum(row["decision"] == "accept" for row in rows),
            "reject": sum(row["decision"] == "reject" for row in rows),
            "matches_external": sum(row["matches_external"] for row in rows),
            "conflicts_external": sum(not row["matches_external"] for row in rows),
            "changed_from_recorded": sum(row["changed_from_recorded"] for row in rows),
        },
        "cases": rows,
    }


def _is_isolatable_rejection(reason: str) -> bool:
    return any(pattern.fullmatch(reason) for pattern in ISOLATABLE_REJECTION_PATTERNS)


def _parse_events(data: bytes, path: Path) -> list[_Event]:
    events: list[_Event] = []
    for line_number, raw_line in enumerate(data.splitlines(keepends=True), start=1):
        if not raw_line.strip():
            raise ValueError(f"blank journal line: {path}:{line_number}")
        try:
            value = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid journal event: {path}:{line_number}") from exc
        event = _object(value, f"journal event {path}:{line_number}")
        events.append(
            _Event(
                line=line_number,
                line_sha256=hashlib.sha256(raw_line).hexdigest(),
                timestamp=_required_string(event, "timestamp"),
                type=_required_string(event, "type"),
                payload=_object(event.get("payload"), "event payload"),
            )
        )
    if not events:
        raise ValueError(f"journal is empty: {path}")
    return events


def _single_event(events: list[_Event], event_type: str, path: Path) -> _Event:
    matches = [event for event in events if event.type == event_type]
    if len(matches) != 1:
        raise ValueError(f"journal must contain one {event_type} event: {path}")
    return matches[0]


def _normalized_event(event: _Event) -> dict[str, Any]:
    return {"event": _event_ref(event), "type": event.type, "payload": event.payload}


def _event_ref(event: _Event) -> dict[str, Any]:
    return {
        "line": event.line,
        "timestamp": event.timestamp,
        "line_sha256": event.line_sha256,
    }


def _read_json_object(path: Path) -> tuple[bytes, dict[str, Any]]:
    data = path.read_bytes()
    value = json.loads(data)
    return data, _object(value, str(path))


def _resolve_source_path(row: dict[str, Any], project_root: Path) -> Path:
    raw = _required_string(row, "result_path")
    relative = Path(raw)
    if relative.is_absolute():
        raise ValueError(f"canonical result path is absolute: {raw}")
    resolved_root = project_root.resolve()
    resolved = (resolved_root / relative).resolve()
    if not resolved.is_relative_to(resolved_root):
        raise ValueError(f"canonical result path escapes the project: {raw}")
    if relative.parts[:2] != ("runs", "terminal-bench-2"):
        raise ValueError(f"canonical result path is outside the run store: {raw}")
    return resolved


def _file_binding(path: Path, data: bytes, project_root: Path) -> dict[str, Any]:
    resolved = path.resolve()
    root = project_root.resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"source path is outside the project: {path}")
    return {
        "path": str(resolved.relative_to(root)),
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def _validate_file_binding(
    binding: dict[str, Any],
    path: Path,
    data: bytes,
    project_root: Path,
) -> None:
    expected = _file_binding(path, data, project_root)
    if binding != expected:
        raise ValueError(f"source binding is stale: {expected['path']}")


def _case_id(dataset: str, task_name: str, result_sha256: str, journal_sha256: str) -> str:
    material = "\0".join((dataset, task_name, result_sha256, journal_sha256)).encode()
    return hashlib.sha256(material).hexdigest()


def _task_name(result: dict[str, Any]) -> str | None:
    config_task = _optional_object(_optional_object(result.get("config")).get("task"))
    task_id = _optional_object(result.get("task_id"))
    for value in (config_task.get("name"), task_id.get("name"), result.get("task_name")):
        if isinstance(value, str) and value:
            return value.rsplit("/", 1)[-1]
    path = config_task.get("path")
    return Path(path).name if isinstance(path, str) and path else None


def _reward(result: dict[str, Any]) -> float:
    verifier = _object(result.get("verifier_result"), "verifier_result")
    rewards = _object(verifier.get("rewards"), "verifier rewards")
    reward = rewards.get("reward")
    if not isinstance(reward, int | float) or isinstance(reward, bool):
        raise ValueError("verifier reward is missing or non-numeric")
    return float(reward)


def _result_status(result: dict[str, Any], reward: float) -> str:
    exception = _optional_object(result.get("exception_info"))
    exception_type = exception.get("exception_type") or exception.get("type")
    if exception_type:
        return "error"
    return "passed" if reward == 1.0 else "failed"


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _optional_object(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: dict[str, Any], key: str) -> list[Any]:
    items = value.get(key)
    if not isinstance(items, list):
        raise ValueError(f"{key} must be a list")
    return items


def _required_string(value: dict[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise ValueError(f"{key} must be a non-empty string")
    return item


def _required_int(value: dict[str, Any], key: str) -> int:
    item = value.get(key)
    if not isinstance(item, int) or isinstance(item, bool):
        raise ValueError(f"{key} must be an integer")
    return item


def _required_bool(value: dict[str, Any], key: str) -> bool:
    item = value.get(key)
    if not isinstance(item, bool):
        raise ValueError(f"{key} must be a boolean")
    return item


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        return []
    return value


def _sanitize_for_export(value: object, *, parent_key: str | None = None) -> object:
    if isinstance(value, str):
        return redact_sensitive(value)
    if isinstance(value, dict):
        sanitized: dict[str, object] = {}
        for key, item in value.items():
            if is_sensitive_field(key) and item is not None:
                sanitized[key] = "[REDACTED]"
            else:
                sanitized[key] = _sanitize_for_export(item, parent_key=key)
        if parent_key in {"stdout", "stderr"}:
            for excerpt_field in ("head", "tail"):
                if sanitized.get(excerpt_field):
                    sanitized[excerpt_field] = OMITTED_OUTPUT
        return sanitized
    if isinstance(value, list):
        return [_sanitize_for_export(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_for_export(item) for item in value]
    return value


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    args = parse_args()
    try:
        if args.command == "build":
            corpus = build_completion_corpus(
                args.canonical,
                args.matrix,
                args.project_root,
            )
            report = analyze_completion_corpus(corpus)
            corpus_bytes = dump_json(corpus)
            report_bytes = dump_json(report)
            _write_atomic(args.corpus_out, corpus_bytes)
            _write_atomic(args.report_out, report_bytes)
            print(
                f"built {corpus['counts']['cases']} cases "
                f"(false_positive={corpus['counts']['false_positive']}, "
                f"false_negative={corpus['counts']['false_negative']})"
            )
            print(f"corpus_sha256={hashlib.sha256(corpus_bytes).hexdigest()}")
            return 0
        if args.command == "analyze":
            _, corpus = _read_json_object(args.corpus)
            report = analyze_completion_corpus(corpus)
            data = dump_json(report)
            if args.report_out is None:
                print(data.decode(), end="")
            else:
                _write_atomic(args.report_out, data)
            return 0

        errors = check_artifacts(
            canonical_path=args.canonical,
            matrix_path=args.matrix,
            project_root=args.project_root,
            corpus_path=args.corpus,
            report_path=args.report,
        )
        if errors:
            for error in errors:
                print(error)
            return 1
        print("completion calibration artifacts verified")
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"completion calibration failed: {exc}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
