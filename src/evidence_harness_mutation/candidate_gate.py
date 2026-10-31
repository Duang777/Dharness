from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import time
from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, Self
from xml.etree import ElementTree

from pydantic import Field, ValidationError, model_validator

from evidence_harness_mutation.historical import (
    HistoricalInfrastructureError,
    _bind_production_source,
)
from evidence_harness_mutation.model import FrozenModel, Sha256

DEFAULT_SUITE_RELATIVE_PATH = Path("experiments/erosion-v1/guard-suite-v1.json")
DEFAULT_TIMEOUT_SEC = 600.0
_EDITABLE_SURFACE = ("src", "evidence_harness")
_ORIGIN_PACKAGES = ("evidence_harness", "evidence_harness_mutation")
_INHERITED_ENVIRONMENT = frozenset({"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE", "TZ"})
_ORIGIN_PROBE = (
    "import importlib.util, json, sys\n"
    "print(json.dumps({name: getattr(importlib.util.find_spec(name), 'origin', None)"
    " for name in sys.argv[1:]}))\n"
)

GitObjectId = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]


class CandidateGateError(RuntimeError):
    pass


class GuardProperty(StrEnum):
    FRESHNESS = "freshness"
    COVERAGE = "coverage"
    FAIL_CLOSED = "fail_closed"
    REVIEW = "review"
    BUDGET = "budget"
    ISOLATION = "isolation"


class GateDecision(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"
    OUT_OF_SCOPE = "out_of_scope"
    NOT_APPLICABLE = "not_applicable"
    INFRA_ERROR = "infra_error"


class ProbeStatus(StrEnum):
    HELD = "held"
    VIOLATED = "violated"


class GuardProbe(FrozenModel):
    probe_id: str = Field(pattern=r"^[a-z_]+\.[a-z0-9_]+$")
    property: GuardProperty
    pytest_node: str = Field(pattern=r"^tests/[A-Za-z0-9_/]+\.py(::[A-Za-z0-9_]+)+$")

    @model_validator(mode="after")
    def validate_namespace(self) -> Self:
        if self.probe_id.split(".", 1)[0] != self.property:
            raise ValueError(f"probe {self.probe_id} must be namespaced by its property")
        return self


class GuardSuite(FrozenModel):
    schema_version: Literal[1]
    suite_id: str = Field(min_length=1)
    probes: tuple[GuardProbe, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique(self) -> Self:
        if len({probe.probe_id for probe in self.probes}) != len(self.probes):
            raise ValueError("guard suite probe ids must be unique")
        if len({probe.pytest_node for probe in self.probes}) != len(self.probes):
            raise ValueError("guard suite pytest nodes must be unique")
        return self


class ProbeResult(FrozenModel):
    probe_id: str
    property: GuardProperty
    status: ProbeStatus


class GateVerdict(FrozenModel):
    """``elapsed_sec`` is wall-clock timing and is excluded from ``canonical_bytes``."""

    schema_version: Literal[1] = 1
    suite_id: str
    suite_sha256: Sha256
    base_commit: GitObjectId
    patch_sha256: Sha256
    touched_paths: tuple[str, ...]
    out_of_scope_paths: tuple[str, ...]
    candidate_source_sha256: Sha256 | None
    decision: GateDecision
    detail: str | None
    violated_properties: tuple[GuardProperty, ...]
    probes: tuple[ProbeResult, ...]
    elapsed_sec: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_decision(self) -> Self:
        if self.touched_paths != tuple(sorted(set(self.touched_paths))):
            raise ValueError("touched paths must be sorted and unique")
        if not set(self.out_of_scope_paths) <= set(self.touched_paths):
            raise ValueError("out-of-scope paths must be touched paths")
        if (self.decision is GateDecision.OUT_OF_SCOPE) != bool(self.out_of_scope_paths):
            raise ValueError("out_of_scope must be decided exactly when paths leave the surface")
        ran = self.decision in (GateDecision.ACCEPT, GateDecision.REJECT)
        if ran != bool(self.probes) or (ran and self.candidate_source_sha256 is None):
            raise ValueError("only accept and reject carry probe results")
        failed = self.decision in (GateDecision.NOT_APPLICABLE, GateDecision.INFRA_ERROR)
        if failed != (self.detail is not None):
            raise ValueError("detail is required exactly for not_applicable and infra_error")
        violated = sorted(
            {probe.property for probe in self.probes if probe.status is ProbeStatus.VIOLATED}
        )
        if self.violated_properties != tuple(violated):
            raise ValueError("violated properties must summarize the violated probes")
        if ran and (self.decision is GateDecision.REJECT) != bool(violated):
            raise ValueError("reject must be decided exactly when a probe is violated")
        return self

    def report_bytes(self) -> bytes:
        return _canonical_json(self.model_dump(mode="json"))

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.model_dump(mode="json", exclude={"elapsed_sec"}))


@dataclass(frozen=True, slots=True)
class GateRequest:
    repo_root: Path
    patch: bytes
    base: str = "HEAD"
    suite_path: Path | None = None
    timeout_sec: float = DEFAULT_TIMEOUT_SEC


@dataclass(frozen=True, slots=True)
class _Evaluation:
    decision: GateDecision
    touched_paths: tuple[str, ...] = ()
    out_of_scope_paths: tuple[str, ...] = ()
    candidate_source_sha256: str | None = None
    detail: str | None = None
    probes: tuple[ProbeResult, ...] = ()


class _GateStop(Exception):
    def __init__(
        self,
        decision: Literal[GateDecision.NOT_APPLICABLE, GateDecision.INFRA_ERROR],
        detail: str,
    ) -> None:
        super().__init__(detail)
        self.decision = decision
        self.detail = detail


def load_guard_suite(path: Path) -> tuple[GuardSuite, str]:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise CandidateGateError(f"cannot read guard suite: {exc}") from exc
    try:
        suite = GuardSuite.model_validate_json(data)
    except ValidationError as exc:
        raise CandidateGateError(f"invalid guard suite: {exc}") from exc
    return suite, hashlib.sha256(data).hexdigest()


def run_candidate_gate(request: GateRequest) -> GateVerdict:
    started = time.monotonic()
    repo_root = request.repo_root.resolve()
    suite, suite_sha256 = load_guard_suite(
        request.suite_path or repo_root / DEFAULT_SUITE_RELATIVE_PATH
    )
    completed = _git(repo_root, "rev-parse", "--verify", f"{request.base}^{{commit}}")
    if completed.returncode:
        raise CandidateGateError(f"cannot resolve base {request.base!r} to a commit")
    base_commit = completed.stdout.decode().strip()
    with tempfile.TemporaryDirectory(prefix="harness-gate-") as workspace:
        evaluation = _evaluate(
            repo_root=repo_root,
            base_commit=base_commit,
            patch=request.patch,
            suite=suite,
            workspace=Path(workspace).resolve(),
            timeout_sec=request.timeout_sec,
        )
    return GateVerdict(
        suite_id=suite.suite_id,
        suite_sha256=suite_sha256,
        base_commit=base_commit,
        patch_sha256=hashlib.sha256(request.patch).hexdigest(),
        touched_paths=evaluation.touched_paths,
        out_of_scope_paths=evaluation.out_of_scope_paths,
        candidate_source_sha256=evaluation.candidate_source_sha256,
        decision=evaluation.decision,
        detail=evaluation.detail,
        violated_properties=tuple(
            sorted(
                {
                    probe.property
                    for probe in evaluation.probes
                    if probe.status is ProbeStatus.VIOLATED
                }
            )
        ),
        probes=evaluation.probes,
        elapsed_sec=time.monotonic() - started,
    )


def _evaluate(
    *,
    repo_root: Path,
    base_commit: str,
    patch: bytes,
    suite: GuardSuite,
    workspace: Path,
    timeout_sec: float,
) -> _Evaluation:
    try:
        touched_paths = _touched_paths(patch, workspace)
    except _GateStop as stop:
        return _Evaluation(decision=stop.decision, detail=stop.detail)
    out_of_scope_paths = tuple(path for path in touched_paths if not _in_editable_surface(path))
    if out_of_scope_paths:
        return _Evaluation(
            touched_paths=touched_paths,
            out_of_scope_paths=out_of_scope_paths,
            decision=GateDecision.OUT_OF_SCOPE,
        )
    candidate_root = workspace / "candidate"
    candidate_source_sha256: str | None = None
    try:
        _materialize_candidate(repo_root, base_commit, patch, workspace, candidate_root)
        try:
            candidate_source_sha256 = _bind_production_source(candidate_root).sha256
        except HistoricalInfrastructureError as exc:
            raise _GateStop(GateDecision.INFRA_ERROR, str(exc)) from exc
        environment = _candidate_environment(candidate_root)
        _check_module_origin(candidate_root, environment, timeout_sec)
        probes = _run_probes(suite, candidate_root, workspace, environment, timeout_sec)
    except _GateStop as stop:
        return _Evaluation(
            touched_paths=touched_paths,
            candidate_source_sha256=candidate_source_sha256,
            decision=stop.decision,
            detail=stop.detail,
        )
    violated = any(probe.status is ProbeStatus.VIOLATED for probe in probes)
    return _Evaluation(
        touched_paths=touched_paths,
        candidate_source_sha256=candidate_source_sha256,
        decision=GateDecision.REJECT if violated else GateDecision.ACCEPT,
        probes=probes,
    )


def _touched_paths(patch: bytes, workspace: Path) -> tuple[str, ...]:
    completed = _git_apply(workspace, patch, "--numstat", "-z")
    if completed.returncode:
        raise _GateStop(GateDecision.NOT_APPLICABLE, _git_error(completed, "cannot parse patch"))
    paths = {
        record.split(b"\t", 2)[2].decode("utf-8", "surrogateescape")
        for record in completed.stdout.split(b"\0")
        if record
    }
    # numstat reports only the destination of a rename; the source is touched too.
    rename_prefix = b"rename from "
    paths.update(
        line.removeprefix(rename_prefix).decode("utf-8", "surrogateescape")
        for line in patch.splitlines()
        if line.startswith(rename_prefix)
    )
    return tuple(sorted(paths))


def _in_editable_surface(path: str) -> bool:
    parts = PurePosixPath(path).parts
    return parts[:2] == _EDITABLE_SURFACE and len(parts) > 2 and ".." not in parts


def _materialize_candidate(
    repo_root: Path,
    base_commit: str,
    patch: bytes,
    workspace: Path,
    candidate_root: Path,
) -> None:
    archive_path = workspace / "base.tar"
    archived = _git(repo_root, "archive", "--format=tar", f"--output={archive_path}", base_commit)
    if archived.returncode:
        raise _GateStop(GateDecision.INFRA_ERROR, _git_error(archived, "cannot archive base"))
    candidate_root.mkdir()
    try:
        with tarfile.open(archive_path, mode="r:") as archive:
            archive.extractall(candidate_root, filter="data")
    except (OSError, tarfile.TarError) as exc:
        raise _GateStop(GateDecision.INFRA_ERROR, f"cannot extract base archive: {exc}") from exc
    # git apply is atomic, so a failed apply leaves the base tree untouched.
    applied = _git_apply(candidate_root, patch)
    if applied.returncode:
        raise _GateStop(GateDecision.NOT_APPLICABLE, _git_error(applied, "patch does not apply"))


def _candidate_environment(candidate_root: Path) -> dict[str, str]:
    # Candidate code is proposer-written, so it must not see provider credentials.
    environment = {
        name: value for name, value in os.environ.items() if name in _INHERITED_ENVIRONMENT
    }
    environment["PYTHONPATH"] = str(candidate_root / "src")
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


def _check_module_origin(
    candidate_root: Path,
    environment: dict[str, str],
    timeout_sec: float,
) -> None:
    completed = _run_candidate_python(
        ("-c", _ORIGIN_PROBE, *_ORIGIN_PACKAGES),
        candidate_root,
        environment,
        timeout_sec,
        label="module origin check",
    )
    if completed.returncode:
        raise _GateStop(GateDecision.INFRA_ERROR, "module origin check failed")
    try:
        origins = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise _GateStop(GateDecision.INFRA_ERROR, "module origin check was unreadable") from exc
    candidate_source = (candidate_root / "src").resolve()
    for name in _ORIGIN_PACKAGES:
        origin = origins.get(name)
        if not isinstance(origin, str) or not Path(origin).resolve().is_relative_to(
            candidate_source
        ):
            raise _GateStop(GateDecision.INFRA_ERROR, f"{name} does not import from the candidate")


def _run_probes(
    suite: GuardSuite,
    candidate_root: Path,
    workspace: Path,
    environment: dict[str, str],
    timeout_sec: float,
) -> tuple[ProbeResult, ...]:
    junit_path = workspace / "junit.xml"
    # Module paths, not node ids: pytest turns an unimportable module named by node id
    # into a usage error that also skips every other module.
    modules = tuple(dict.fromkeys(probe.pytest_node.split("::", 1)[0] for probe in suite.probes))
    completed = _run_candidate_python(
        (
            "-m",
            "pytest",
            *modules,
            "-p",
            "no:cacheprovider",
            "--continue-on-collection-errors",
            "-o",
            "junit_family=xunit2",
            f"--junitxml={junit_path}",
            "-q",
        ),
        candidate_root,
        environment,
        timeout_sec,
        label="pytest",
    )
    if completed.returncode not in (0, 1):
        raise _GateStop(GateDecision.INFRA_ERROR, f"pytest exited with code {completed.returncode}")
    try:
        outcomes = _junit_outcomes(junit_path.read_bytes())
    except (OSError, ElementTree.ParseError) as exc:
        raise _GateStop(GateDecision.INFRA_ERROR, f"cannot read junit report: {exc}") from exc
    return tuple(_probe_result(probe, outcomes) for probe in suite.probes)


@dataclass(frozen=True, slots=True)
class _JunitOutcomes:
    passed_by_case: dict[tuple[str, str], list[bool]]
    collection_errors: frozenset[str]


def _junit_outcomes(data: bytes) -> _JunitOutcomes:
    passed_by_case: defaultdict[tuple[str, str], list[bool]] = defaultdict(list)
    collection_errors: set[str] = set()
    for case in ElementTree.fromstring(data).iter("testcase"):
        classname = case.get("classname", "")
        name = case.get("name", "")
        passed = not any(child.tag in ("failure", "error", "skipped") for child in case)
        if not classname and not passed:
            collection_errors.add(name)
        else:
            passed_by_case[(classname, name)].append(passed)
    return _JunitOutcomes(dict(passed_by_case), frozenset(collection_errors))


def _probe_result(probe: GuardProbe, outcomes: _JunitOutcomes) -> ProbeResult:
    module_path, *scopes, test_name = probe.pytest_node.split("::")
    module = module_path.removesuffix(".py").replace("/", ".")
    if module in outcomes.collection_errors:
        status = ProbeStatus.VIOLATED
    else:
        classname = ".".join((module, *scopes))
        passed = [
            result
            for (case_class, case_name), results in outcomes.passed_by_case.items()
            if case_class == classname
            and (case_name == test_name or case_name.startswith(f"{test_name}["))
            for result in results
        ]
        if not passed:
            raise _GateStop(
                GateDecision.INFRA_ERROR,
                f"suite node missing from junit report: {probe.pytest_node}",
            )
        status = ProbeStatus.HELD if all(passed) else ProbeStatus.VIOLATED
    return ProbeResult(probe_id=probe.probe_id, property=probe.property, status=status)


def _run_candidate_python(
    arguments: tuple[str, ...],
    candidate_root: Path,
    environment: dict[str, str],
    timeout_sec: float,
    *,
    label: str,
) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            (sys.executable, *arguments),
            cwd=candidate_root,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise _GateStop(
            GateDecision.INFRA_ERROR, f"{label} timed out after {timeout_sec:g} seconds"
        ) from exc
    except OSError as exc:
        raise _GateStop(GateDecision.INFRA_ERROR, f"cannot start {label}: {exc}") from exc


def _git(repo_root: Path, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(
            ("git", "-C", str(repo_root), *arguments),
            capture_output=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CandidateGateError(f"cannot run git {arguments[0]}: {exc}") from exc


def _git_apply(cwd: Path, patch: bytes, *arguments: str) -> subprocess.CompletedProcess[bytes]:
    environment = os.environ.copy()
    # Keep git from discovering an enclosing repository, where apply would resolve
    # paths against that repository's root instead of cwd.
    environment["GIT_CEILING_DIRECTORIES"] = str(cwd.parent)
    try:
        return subprocess.run(
            ("git", "apply", "--allow-empty", *arguments),
            cwd=cwd,
            env=environment,
            input=patch,
            capture_output=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise _GateStop(GateDecision.INFRA_ERROR, f"cannot run git apply: {exc}") from exc


def _git_error(completed: subprocess.CompletedProcess[bytes], summary: str) -> str:
    message = " ".join(completed.stderr.decode("utf-8", "replace").split())
    return f"{summary}: {message[:1_000]}" if message else summary


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=True,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode()
