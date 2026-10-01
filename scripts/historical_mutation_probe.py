from __future__ import annotations

import asyncio
import hashlib
import importlib
import inspect
import json
import re
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

_CASE_KINDS = {
    "review_timeout_fallback": "review_timeout",
    "reorder_check_receipts": "reorder_receipts",
    "review_receipt_order": "review_receipt_order",
    "review_quota_no_bypass": "review_quota_no_bypass",
    "failed_change_progress": "failed_change_progress",
    "max_repairs_exact": "max_repairs_exact",
    "wall_time_command_deadline": "wall_time_command_deadline",
}
_SCENARIO_KEYS = {
    "review_timeout": {
        "kind",
        "instruction",
        "options",
        "checks",
        "coverage",
        "review_error",
    },
    "reorder_receipts": {"kind", "work_epoch", "checks", "coverage"},
    "review_receipt_order": {
        "kind",
        "instruction",
        "options",
        "checks",
        "coverage",
        "receipt_marker",
    },
    "review_quota_no_bypass": {
        "kind",
        "instruction",
        "options",
        "checks",
        "coverage",
        "rejection_rationales",
    },
    "failed_change_progress": {
        "kind",
        "instruction",
        "options",
        "failed_commands",
        "failure_stderr",
        "terminal_stop_category",
    },
    "max_repairs_exact": {
        "kind",
        "instruction",
        "options",
        "checks",
        "coverage",
        "failure_stderr",
    },
    "wall_time_command_deadline": {
        "kind",
        "instruction",
        "options",
        "command",
        "model_clock_target_sec",
        "command_duration_sec",
        "terminal_stop_category",
    },
}


@dataclass(frozen=True, slots=True)
class _EnvironmentResult:
    return_code: int = 0
    stdout: str = ""
    stderr: str = ""


@dataclass(frozen=True, slots=True)
class _EnvironmentCall:
    command: str
    cwd: str | None
    timeout_sec: int | None


class _ScriptedEnvironment:
    def __init__(
        self,
        *,
        responses: dict[str, list[_EnvironmentResult]] | None = None,
        handler: Callable[[str, int | None], _EnvironmentResult] | None = None,
    ) -> None:
        self.calls: list[_EnvironmentCall] = []
        self._responses = {command: list(values) for command, values in (responses or {}).items()}
        self._handler = handler

    async def exec(
        self,
        command: str,
        cwd: str | None = None,
        timeout_sec: int | None = None,
    ) -> _EnvironmentResult:
        self.calls.append(_EnvironmentCall(command, cwd, timeout_sec))
        queued = self._responses.get(command)
        if queued:
            return queued.pop(0)
        if self._handler is not None:
            return self._handler(command, timeout_sec)
        if command.startswith("set +e\n"):
            return _EnvironmentResult(stdout="/workspace\n")
        return _EnvironmentResult()


class _ScriptedModel:
    def __init__(
        self,
        *,
        usage_type: type[Any],
        decisions: list[Any],
        reviews: list[Any] | None = None,
        review_handler: Callable[[str], Any] | None = None,
        review_error: str | None = None,
        decide_hook: Callable[[int], None] | None = None,
    ) -> None:
        self._usage_type = usage_type
        self._decisions = list(decisions)
        self._reviews = list(reviews or ())
        self._review_handler = review_handler
        self._review_error = review_error
        self._decide_hook = decide_hook
        self.decision_calls = 0
        self.review_calls = 0

    @property
    def usage(self) -> object:
        return self._usage_type(model_calls=self.decision_calls + self.review_calls)

    async def decide(self, prompt: str) -> object:
        del prompt
        self.decision_calls += 1
        if self._decide_hook is not None:
            self._decide_hook(self.decision_calls)
        if not self._decisions:
            raise AssertionError("scripted model has no executor decision left")
        return self._decisions.pop(0).model_copy(deep=True)

    async def review(self, prompt: str) -> object:
        self.review_calls += 1
        if self._review_error is not None:
            raise TimeoutError(self._review_error)
        if self._review_handler is not None:
            return self._review_handler(prompt)
        if not self._reviews:
            raise AssertionError("scripted model has no completion review left")
        return self._reviews.pop(0).model_copy(deep=True)


class _MutableClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def set(self, value: float) -> None:
        self.now = value

    def advance(self, seconds: float) -> None:
        self.now += seconds


def main() -> int:
    try:
        request = _load_request()
        response = asyncio.run(_run_probe(request))
    except Exception as exc:
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(json.dumps(response, ensure_ascii=True, sort_keys=True))
    sys.stdout.write("\n")
    return 0


def _load_request() -> dict[str, Any]:
    try:
        value = json.loads(sys.stdin.read())
    except json.JSONDecodeError as exc:
        raise ValueError("stdin must contain one JSON request") from exc
    if not isinstance(value, dict):
        raise ValueError("probe request must be an object")
    expected_keys = {
        "schema_version",
        "suite_id",
        "case_id",
        "commit",
        "tree",
        "manifest_sha256",
        "production_source_sha256",
        "source_root",
        "scenario",
    }
    if set(value) != expected_keys:
        raise ValueError("probe request fields do not match schema version 2")
    if value["schema_version"] != 2 or value["suite_id"] != "historical-7":
        raise ValueError("unsupported probe request schema")
    case_id = value["case_id"]
    if case_id not in _CASE_KINDS:
        raise ValueError("unsupported Historical-7 case")
    for field in ("commit", "tree"):
        if not isinstance(value[field], str) or re.fullmatch(r"[0-9a-f]{40}", value[field]) is None:
            raise ValueError(f"{field} must be a full Git object id")
    for field in ("manifest_sha256", "production_source_sha256"):
        if not isinstance(value[field], str) or re.fullmatch(r"[0-9a-f]{64}", value[field]) is None:
            raise ValueError(f"{field} must be a SHA-256 digest")
    if not isinstance(value["source_root"], str) or not value["source_root"]:
        raise ValueError("source_root must be a non-empty path")
    scenario = value["scenario"]
    expected_kind = _CASE_KINDS[case_id]
    if not isinstance(scenario, dict) or scenario.get("kind") != expected_kind:
        raise ValueError("case received the wrong scenario kind")
    if set(scenario) != _SCENARIO_KEYS[expected_kind]:
        raise ValueError(f"{expected_kind} scenario fields do not match the frozen schema")
    return value


async def _run_probe(request: dict[str, Any]) -> dict[str, object]:
    source_root = Path(request["source_root"]).resolve()
    source_dir = source_root / "src"
    if not (source_dir / "evidence_harness").is_dir():
        raise ValueError("source_root has no evidence_harness package")
    source_sha256 = _production_source_sha256(source_root)
    if source_sha256 != request["production_source_sha256"]:
        raise ValueError("archived production source does not match the request")

    sys.path.insert(0, str(source_dir))
    case_id = request["case_id"]
    scenario = request["scenario"]
    if case_id == "reorder_check_receipts":
        observation = _run_reordered_receipts(
            source_root=source_root,
            source_sha256=source_sha256,
            scenario=scenario,
        )
    elif case_id == "wall_time_command_deadline":
        observation = await _run_wall_time(
            source_root=source_root,
            source_sha256=source_sha256,
            scenario=scenario,
        )
    else:
        observation = await _run_scripted_loop(
            case_id=case_id,
            source_root=source_root,
            source_sha256=source_sha256,
            scenario=scenario,
        )

    _assert_archive_imports(source_dir)
    if _production_source_sha256(source_root) != source_sha256:
        raise RuntimeError("archived production source changed during the probe")
    return {
        "schema_version": 2,
        "suite_id": request["suite_id"],
        "case_id": case_id,
        "commit": request["commit"],
        "tree": request["tree"],
        "manifest_sha256": request["manifest_sha256"],
        "production_source_sha256": source_sha256,
        "observation": observation,
    }


async def _run_scripted_loop(
    *,
    case_id: str,
    source_root: Path,
    source_sha256: str,
    scenario: dict[str, Any],
) -> dict[str, object]:
    journal_module = importlib.import_module("evidence_harness.journal")
    protocol_module = importlib.import_module("evidence_harness.protocol")
    run_loop_module = importlib.import_module("evidence_harness.run_loop")

    review_saw_receipt_marker: list[bool] = []
    responses: dict[str, list[_EnvironmentResult]] = {}
    review_handler: Callable[[str], Any] | None = None
    review_error: str | None = None
    reviews: list[Any] = []

    if case_id == "review_timeout_fallback":
        decisions = [_finish_decision(protocol_module, scenario)]
        review_error = scenario["review_error"]
    elif case_id == "review_receipt_order":
        decisions = [_finish_decision(protocol_module, scenario)]
        check_script = scenario["checks"][0]["script"]
        marker = scenario["receipt_marker"]
        responses[check_script] = [_EnvironmentResult(stdout=f"{marker}\n")]

        def inspect_review(prompt: str) -> object:
            saw_marker = marker in prompt
            review_saw_receipt_marker.append(saw_marker)
            if saw_marker:
                return protocol_module.ReviewDecision(
                    verdict="repair",
                    rationale="the executed receipt reports the wrong value",
                    missing_requirements=("answer.txt must contain good",),
                )
            return protocol_module.ReviewDecision(
                verdict="accept",
                rationale="the proposed check covers the requested value",
            )

        review_handler = inspect_review
    elif case_id == "review_quota_no_bypass":
        decisions = [_finish_decision(protocol_module, scenario) for _ in range(3)]
        reviews = [
            protocol_module.ReviewDecision(verdict="repair", rationale=rationale)
            for rationale in scenario["rejection_rationales"]
        ]
    elif case_id == "failed_change_progress":
        decisions = [
            _execute_decision(protocol_module, command) for command in scenario["failed_commands"]
        ]
        decisions.append(_stop_decision(protocol_module, scenario["terminal_stop_category"]))
        for command in scenario["failed_commands"]:
            responses[command["script"]] = [
                _EnvironmentResult(return_code=1, stderr=scenario["failure_stderr"])
            ]
    elif case_id == "max_repairs_exact":
        decisions = [_finish_decision(protocol_module, scenario) for _ in range(2)]
        check_script = scenario["checks"][0]["script"]
        responses[check_script] = [
            _EnvironmentResult(return_code=1, stderr=scenario["failure_stderr"]),
            _EnvironmentResult(return_code=1, stderr=scenario["failure_stderr"]),
        ]
    else:
        raise ValueError(f"unsupported loop case: {case_id}")

    environment = _ScriptedEnvironment(responses=responses)
    model = _ScriptedModel(
        usage_type=protocol_module.UsageTotals,
        decisions=decisions,
        reviews=reviews,
        review_handler=review_handler,
        review_error=review_error,
    )
    report, events = await _execute_loop(
        journal_module=journal_module,
        protocol_module=protocol_module,
        run_loop_module=run_loop_module,
        scenario=scenario,
        model=model,
        environment=environment,
        clock=lambda: 100.0,
    )
    observation = {
        "kind": scenario["kind"],
        "binding": _production_binding(
            source_root=source_root,
            source_sha256=source_sha256,
            module=run_loop_module,
            entrypoint="EvidenceLoop.run",
            module_member="evidence_harness.run_loop:EvidenceLoop.run",
        ),
        "run": _loop_summary(report, model, events),
    }
    if case_id == "review_receipt_order":
        check_id = scenario["checks"][0]["id"]
        observation["call_order"] = [
            "review" if event["type"] == "completion_review" else "check"
            for event in events
            if event["type"] == "completion_review"
            or (
                event["type"] == "command_receipt"
                and event["payload"].get("command_id") == check_id
            )
        ]
        observation["review_saw_receipt_marker"] = review_saw_receipt_marker
    elif case_id == "review_quota_no_bypass":
        check_id = scenario["checks"][0]["id"]
        observation["check_calls"] = sum(
            event["type"] == "command_receipt" and event["payload"].get("command_id") == check_id
            for event in events
        )
    elif case_id == "failed_change_progress":
        expected_ids = {command["id"] for command in scenario["failed_commands"]}
        observation["failed_change_ids"] = [
            event["payload"]["command_id"]
            for event in events
            if event["type"] == "command_receipt"
            and event["payload"].get("command_id") in expected_ids
            and event["payload"].get("failure") == "nonzero"
        ]
        observation["stop_decision_consumed"] = model.decision_calls == len(decisions)
    elif case_id == "max_repairs_exact":
        check_id = scenario["checks"][0]["id"]
        observation["failed_check_calls"] = sum(
            event["type"] == "command_receipt"
            and event["payload"].get("command_id") == check_id
            and event["payload"].get("failure") == "nonzero"
            for event in events
        )

    return observation


async def _run_wall_time(
    *,
    source_root: Path,
    source_sha256: str,
    scenario: dict[str, Any],
) -> dict[str, object]:
    journal_module = importlib.import_module("evidence_harness.journal")
    protocol_module = importlib.import_module("evidence_harness.protocol")
    run_loop_module = importlib.import_module("evidence_harness.run_loop")

    clock = _MutableClock()
    command = scenario["command"]
    decisions = [
        _execute_decision(protocol_module, command),
        _stop_decision(protocol_module, scenario["terminal_stop_category"]),
    ]

    def advance_after_first_decision(call_number: int) -> None:
        if call_number == 1:
            clock.set(float(scenario["model_clock_target_sec"]))

    def execute_with_duration(script: str, timeout_sec: int | None) -> _EnvironmentResult:
        if script.startswith("set +e\n"):
            return _EnvironmentResult(stdout="/workspace\n")
        if script != command["script"] or timeout_sec is None:
            return _EnvironmentResult()
        duration = float(scenario["command_duration_sec"])
        if timeout_sec < duration:
            clock.advance(float(timeout_sec))
            raise TimeoutError
        clock.advance(duration)
        return _EnvironmentResult()

    model = _ScriptedModel(
        usage_type=protocol_module.UsageTotals,
        decisions=decisions,
        decide_hook=advance_after_first_decision,
    )
    environment = _ScriptedEnvironment(handler=execute_with_duration)
    report, events = await _execute_loop(
        journal_module=journal_module,
        protocol_module=protocol_module,
        run_loop_module=run_loop_module,
        scenario=scenario,
        model=model,
        environment=environment,
        clock=clock,
    )
    work_call = next(
        (call for call in environment.calls if call.command == command["script"]),
        None,
    )
    work_receipt = next(
        (
            event["payload"]
            for event in events
            if event["type"] == "command_receipt"
            and event["payload"].get("command_id") == command["id"]
        ),
        None,
    )
    finalization_triggers = tuple(
        trigger
        for event in events
        if event["type"] == "finalization_started"
        for trigger in event["payload"].get("triggers", ())
    )
    return {
        "kind": scenario["kind"],
        "binding": _production_binding(
            source_root=source_root,
            source_sha256=source_sha256,
            module=run_loop_module,
            entrypoint="EvidenceLoop.run",
            module_member="evidence_harness.run_loop:EvidenceLoop.run",
        ),
        "run": _loop_summary(report, model, events),
        "work_command_timeout_sec": work_call.timeout_sec if work_call is not None else None,
        "work_command_return_code": (
            work_receipt.get("return_code") if work_receipt is not None else None
        ),
        "work_command_failure": (work_receipt.get("failure") if work_receipt is not None else None),
        "final_clock_sec": clock.now,
        "finalization_triggers": finalization_triggers,
    }


def _run_reordered_receipts(
    *,
    source_root: Path,
    source_sha256: str,
    scenario: dict[str, Any],
) -> dict[str, object]:
    evidence_module = importlib.import_module("evidence_harness.evidence")
    protocol_module = importlib.import_module("evidence_harness.protocol")

    checks = tuple(
        protocol_module.VerificationCheck.model_validate(value) for value in scenario["checks"]
    )
    coverage = tuple(
        protocol_module.RequirementCoverage.model_validate(value) for value in scenario["coverage"]
    )
    gate = evidence_module.EvidenceGate(protocol_module.LoopOptions(enable_completion_review=False))
    proposal_rejections = gate.validate_proposal(checks, coverage)
    output = protocol_module.OutputExcerpt(
        head="",
        tail="",
        total_bytes=0,
        omitted_bytes=0,
        sha256=hashlib.sha256(b"").hexdigest(),
    )
    canonical_receipts = tuple(
        protocol_module.CommandReceipt(
            sequence=index,
            command_id=check.id,
            script=check.script,
            purpose=check.proves,
            cwd=check.cwd,
            mode=protocol_module.CommandMode.OBSERVE,
            work_epoch=scenario["work_epoch"],
            return_code=0,
            duration_sec=0,
            stdout=output,
            stderr=output,
            command_fingerprint=f"{index:064x}",
            observation_fingerprint=f"{index + 10:064x}",
        )
        for index, check in enumerate(checks, start=1)
    )
    supplied_receipts = tuple(reversed(canonical_receipts))
    kwargs: dict[str, object] = {
        "work_epoch": scenario["work_epoch"],
        "checks": supplied_receipts,
        "coverage": coverage,
    }
    parameters = inspect.signature(evidence_module.EvidenceGate.decide).parameters
    if "expected_check_ids" in parameters:
        kwargs["expected_check_ids"] = tuple(check.id for check in checks)
    receipt = gate.decide(**kwargs)
    return {
        "kind": scenario["kind"],
        "binding": _production_binding(
            source_root=source_root,
            source_sha256=source_sha256,
            module=evidence_module,
            entrypoint="EvidenceGate.decide",
            module_member="evidence_harness.evidence:EvidenceGate.decide",
        ),
        "proposal_rejections": list(proposal_rejections),
        "proposed_check_ids": [check.id for check in checks],
        "supplied_receipt_ids": [item.command_id for item in supplied_receipts],
        "accepted": receipt.accepted,
        "rejection_reasons": list(receipt.rejection_reasons),
    }


async def _execute_loop(
    *,
    journal_module: ModuleType,
    protocol_module: ModuleType,
    run_loop_module: ModuleType,
    scenario: dict[str, Any],
    model: _ScriptedModel,
    environment: _ScriptedEnvironment,
    clock: Callable[[], float],
) -> tuple[Any, list[dict[str, Any]]]:
    options = protocol_module.LoopOptions(**scenario["options"])
    with tempfile.TemporaryDirectory(prefix="historical-loop-") as temporary:
        journal = journal_module.RunJournal(Path(temporary), inline_bytes=512)
        loop = run_loop_module.EvidenceLoop(
            model=model,
            journal=journal,
            options=options,
            clock=clock,
        )
        report = await loop.run(scenario["instruction"], environment)
        events = [
            json.loads(line)
            for line in journal.events_path.read_text(encoding="utf-8").splitlines()
        ]
    return report, events


def _finish_decision(protocol_module: ModuleType, scenario: dict[str, Any]) -> object:
    return protocol_module.AgentDecision.model_validate(
        {
            "action": "finish",
            "rationale": "the artifact is ready for fresh verification",
            "checks": scenario["checks"],
            "coverage": scenario["coverage"],
            "summary": "verified the requested artifact",
        }
    )


def _execute_decision(protocol_module: ModuleType, command: dict[str, Any]) -> object:
    return protocol_module.AgentDecision.model_validate(
        {
            "action": "execute",
            "rationale": command["purpose"],
            "commands": [{**command, "mode": "change"}],
        }
    )


def _stop_decision(protocol_module: ModuleType, category: str) -> object:
    return protocol_module.AgentDecision.model_validate(
        {
            "action": "stop",
            "rationale": "the task cannot proceed",
            "stop_category": category,
            "summary": "stopped after the scripted attempts",
        }
    )


def _loop_summary(
    report: Any,
    model: _ScriptedModel,
    events: list[dict[str, Any]],
) -> dict[str, object]:
    evidence = report.latest_evidence
    verification_events = [event for event in events if event["type"] == "verification_receipt"]
    return {
        "stop_reason": _enum_value(report.stop_reason),
        "failure_category": report.failure_category,
        "evidence_accepted": evidence.accepted if evidence is not None else None,
        "rejection_reasons": (list(evidence.rejection_reasons) if evidence is not None else []),
        "decision_calls": model.decision_calls,
        "review_calls": model.review_calls,
        "environment_calls": report.environment_calls_used,
        "turns_used": report.turns_used,
        "repairs_used": report.repairs_used,
        "recoveries_used": report.recoveries_used,
        "review_error_count": sum(event["type"] == "completion_review_error" for event in events),
        "verification_attempts": len(verification_events),
        "accepted_verification_count": sum(
            event["payload"].get("accepted") is True for event in verification_events
        ),
        "completion_rejection_count": sum(
            event["type"] == "completion_rejected" for event in events
        ),
    }


def _production_binding(
    *,
    source_root: Path,
    source_sha256: str,
    module: ModuleType,
    entrypoint: str,
    module_member: str,
) -> dict[str, str]:
    module_file = _module_file(module)
    try:
        relative_source = module_file.relative_to(source_root)
    except ValueError as exc:
        raise RuntimeError(f"production module escaped archive: {module.__name__}") from exc
    return {
        "entrypoint": entrypoint,
        "module_member": module_member,
        "source_path": relative_source.as_posix(),
        "source_sha256": _sha256(module_file),
        "production_source_sha256": source_sha256,
    }


def _production_source_sha256(source_root: Path) -> str:
    relative_paths = [
        Path("pyproject.toml"),
        Path("uv.lock"),
        *sorted(
            (
                path.relative_to(source_root)
                for path in (source_root / "src" / "evidence_harness").rglob("*.py")
            ),
            key=lambda path: path.as_posix(),
        ),
    ]
    digest = hashlib.sha256()
    for relative_path in relative_paths:
        path = source_root / relative_path
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"production source is not a regular file: {relative_path.as_posix()}")
        content = path.read_bytes()
        encoded_path = relative_path.as_posix().encode()
        digest.update(len(encoded_path).to_bytes(8, "big"))
        digest.update(encoded_path)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def _assert_archive_imports(source_dir: Path) -> None:
    for name, module in tuple(sys.modules.items()):
        if name != "evidence_harness" and not name.startswith("evidence_harness."):
            continue
        if module is None or getattr(module, "__file__", None) is None:
            continue
        module_file = _module_file(module)
        try:
            module_file.relative_to(source_dir)
        except ValueError as exc:
            raise RuntimeError(f"module {name} was imported outside the archive") from exc


def _module_file(module: ModuleType) -> Path:
    filename = getattr(module, "__file__", None)
    if not isinstance(filename, str):
        raise RuntimeError(f"module has no source file: {module.__name__}")
    return Path(filename).resolve()


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ValueError(f"cannot hash required file {path.name}") from exc


def _enum_value(value: object) -> str:
    raw = getattr(value, "value", value)
    if not isinstance(raw, str):
        raise TypeError("production enum value is not a string")
    return raw


if __name__ == "__main__":
    raise SystemExit(main())
