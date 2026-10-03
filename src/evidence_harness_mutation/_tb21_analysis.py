from __future__ import annotations

import copy
import hashlib
import json
import os
from collections import Counter
from fractions import Fraction
from pathlib import Path
from typing import Literal, Self

from harbor.models.trial.config import TrialConfig
from pydantic import Field, ValidationError, model_validator

from evidence_harness.collection_profile import PREFIXBENCH_V1
from evidence_harness.protocol import AgentDecision, ProducerAttestation
from evidence_harness_mutation._tb21_harbor import (
    RunSeal,
    TerminalReceipt,
    fold_task_receipts,
)
from evidence_harness_mutation._tb21_manifest import (
    BOOTSTRAP_NAMESPACE,
    CHOICE_NAMESPACE,
    OUTCOME_PATHS,
    BoundExecutable,
    PlannedTask,
    RegistryMember,
    Tb21TaskKey,
    file_binding,
    git_bytes,
    read_regular_file,
)
from evidence_harness_mutation.campaign import OfflineCampaignReport, run_offline_campaign
from evidence_harness_mutation.journal_loader import JournalLoadError, load_state_prefix
from evidence_harness_mutation.main_analysis_baselines import (
    AgentChaosView,
    BaselineSelection,
    InputRejectedOutcome,
    RandomJsonView,
    RQ2MethodResult,
    RQ2SlotKey,
    RQ2SlotOutcome,
    RQ2TaskResult,
    SchemaValidRandomView,
    StatelessEditSite,
    StatelessSemanticView,
    TargetNotReachedOutcome,
    _classify_method,
    _classify_trace,
    _encode_events,
    _replace_events,
    _schema_valid_candidates,
    _stable_audit,
    _stable_error,
    _state_aware_result,
    _wire_event,
    _WireEvent,
    build_slot_grid,
)
from evidence_harness_mutation.main_analysis_protocol import (
    RQ2_REPORT,
    MainAnalysisMethod,
    MainAnalysisOutcome,
)
from evidence_harness_mutation.main_analysis_report import (
    RQ2Analysis,
    RQ2Contrast,
    RQ2MethodSummary,
    RQ2OutcomeCounts,
    RQ2Report,
)
from evidence_harness_mutation.main_analysis_statistics import (
    ExactProbability,
    ExactValue,
    HolmInput,
    PairedBinaryTask,
    bootstrap_binary_risk_difference,
    exact_mcnemar,
    holm_adjust,
)
from evidence_harness_mutation.model import AuditReport, FrozenModel, StatePrefix
from evidence_harness_mutation.operators import MutationId
from evidence_harness_mutation.prefixbench import PrefixBenchFileBinding
from evidence_harness_mutation.tb21_sensitivity_protocol import (
    SENSITIVITY_CAMPAIGN,
    SENSITIVITY_CANONICAL,
    SENSITIVITY_METHOD_REPORT,
    SENSITIVITY_READINESS,
    SENSITIVITY_REPORT,
    SENSITIVITY_RUN_ROOT,
)

_TARGET_EVENT_TYPE = {
    MutationId.STALE_EVIDENCE_EPOCH: "verification_receipt",
    MutationId.REORDER_CHECK_RECEIPTS: "verification_receipt",
    MutationId.REVIEW_TIMEOUT_FALLBACK: "completion_review",
    MutationId.CROSS_CANDIDATE_EVIDENCE: "verification_receipt",
}
_COMPARATORS = (
    MainAnalysisMethod.RANDOM_JSON,
    MainAnalysisMethod.SCHEMA_VALID_RANDOM,
    MainAnalysisMethod.AGENTCHAOS_STYLE,
    MainAnalysisMethod.STATELESS_SEMANTIC,
)
_ALPHA = Fraction(1, 20)


class AnalysisLineage(FrozenModel):
    executable: PrefixBenchFileBinding
    executable_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    producer: ProducerAttestation


class CanonicalTask(FrozenModel):
    ordinal: int = Field(ge=1, le=61)
    key: Tb21TaskKey
    member: RegistryMember
    status: Literal["passed", "failed", "error"]
    reward: float | None
    exception_type: str | None
    result: PrefixBenchFileBinding
    config: PrefixBenchFileBinding
    journal: PrefixBenchFileBinding | None


class CanonicalReport(FrozenModel):
    schema_version: Literal[1] = 1
    report_id: Literal["prefixbench-v1-tb21-sensitivity-canonical"] = (
        "prefixbench-v1-tb21-sensitivity-canonical"
    )
    dataset: Literal["terminal-bench-2-1"] = "terminal-bench-2-1"
    lineage: AnalysisLineage
    run_seal: PrefixBenchFileBinding
    matrix: PrefixBenchFileBinding
    tasks: tuple[CanonicalTask, ...] = Field(min_length=61, max_length=61)

    @model_validator(mode="after")
    def validate_tasks(self) -> Self:
        if tuple(task.ordinal for task in self.tasks) != tuple(range(1, 62)):
            raise ValueError("TB2.1 canonical task ordinals must be contiguous")
        if len({task.key.source_identity_sha256 for task in self.tasks}) != 61:
            raise ValueError("TB2.1 canonical task identities must be unique")
        return self

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.model_dump(mode="json"))


class ReadinessTask(FrozenModel):
    ordinal: int = Field(ge=1, le=61)
    key: Tb21TaskKey
    status: Literal["admitted", "unavailable"]
    journal: PrefixBenchFileBinding | None
    journal_lines: int | None = Field(default=None, ge=1)
    projected_attempts: int | None = Field(default=None, ge=0)
    reason: str | None = None

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        admitted = self.status == "admitted"
        if admitted != (
            self.journal is not None
            and self.journal_lines is not None
            and self.projected_attempts is not None
            and self.reason is None
        ):
            raise ValueError("TB2.1 readiness task fields do not match its status")
        if not admitted and not self.reason:
            raise ValueError("unavailable TB2.1 source requires a reason")
        return self


class ReadinessReport(FrozenModel):
    schema_version: Literal[1] = 1
    report_id: Literal["prefixbench-v1-tb21-sensitivity-readiness"] = (
        "prefixbench-v1-tb21-sensitivity-readiness"
    )
    dataset: Literal["terminal-bench-2-1"] = "terminal-bench-2-1"
    canonical: PrefixBenchFileBinding
    status: Literal["ready", "source_incomplete"]
    admitted_tasks: int = Field(ge=0, le=61)
    tasks: tuple[ReadinessTask, ...] = Field(min_length=61, max_length=61)

    @model_validator(mode="after")
    def validate_summary(self) -> Self:
        admitted = sum(task.status == "admitted" for task in self.tasks)
        if admitted != self.admitted_tasks:
            raise ValueError("TB2.1 readiness admitted count is stale")
        if (self.status == "ready") != (admitted == 61):
            raise ValueError("TB2.1 readiness status is stale")
        return self

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.model_dump(mode="json"))


class CampaignTask(FrozenModel):
    ordinal: int = Field(ge=1, le=61)
    key: Tb21TaskKey
    status: Literal["available", "unavailable"]
    projected_attempts: int | None = Field(default=None, ge=0)
    campaign: OfflineCampaignReport | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        available = self.status == "available"
        if available != (
            self.projected_attempts is not None
            and self.campaign is not None
            and self.reason is None
        ):
            raise ValueError("TB2.1 campaign task fields do not match its status")
        if not available and not self.reason:
            raise ValueError("unavailable TB2.1 campaign task requires a reason")
        return self


class CampaignReport(FrozenModel):
    schema_version: Literal[1] = 1
    report_id: Literal["prefixbench-v1-tb21-sensitivity-offline-campaign"] = (
        "prefixbench-v1-tb21-sensitivity-offline-campaign"
    )
    dataset: Literal["terminal-bench-2-1"] = "terminal-bench-2-1"
    readiness: PrefixBenchFileBinding
    available_tasks: int = Field(ge=0, le=61)
    tasks: tuple[CampaignTask, ...] = Field(min_length=61, max_length=61)

    @model_validator(mode="after")
    def validate_summary(self) -> Self:
        if self.available_tasks != sum(task.status == "available" for task in self.tasks):
            raise ValueError("TB2.1 campaign available count is stale")
        return self

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.model_dump(mode="json"))


class MethodReport(FrozenModel):
    schema_version: Literal[1] = 1
    report_id: Literal["prefixbench-v1-tb21-sensitivity-method-comparison"] = (
        "prefixbench-v1-tb21-sensitivity-method-comparison"
    )
    dataset: Literal["terminal-bench-2-1"] = "terminal-bench-2-1"
    role: Literal["sensitivity-only-not-main-rq"] = "sensitivity-only-not-main-rq"
    lineage: AnalysisLineage
    campaign: PrefixBenchFileBinding
    choice_namespace: Literal["thesis-tb21-sensitivity-v1"] = CHOICE_NAMESPACE
    bootstrap_namespace: Literal["thesis-tb21-sensitivity-v1/bootstrap-rq2-v1"] = (
        BOOTSTRAP_NAMESPACE
    )
    holm_family: Literal["tb21-sensitivity-rq2-v1-four-contrasts"] = (
        "tb21-sensitivity-rq2-v1-four-contrasts"
    )
    tasks: tuple[RQ2TaskResult, ...] = Field(min_length=61, max_length=61)
    analysis: RQ2Analysis

    @model_validator(mode="after")
    def validate_tasks(self) -> Self:
        if len({task.task_identity_sha256 for task in self.tasks}) != 61:
            raise ValueError("TB2.1 method task identities must be unique")
        return self

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.model_dump(mode="json"))


class SensitivityComparison(FrozenModel):
    comparator: MainAnalysisMethod
    tb20_risk_difference: ExactValue
    tb21_risk_difference: ExactValue
    direction_agreement: bool
    tb20_disposition: Literal["confirmed", "not_confirmed"]
    tb21_disposition: Literal["confirmed", "not_confirmed"]
    disposition_agreement: bool

    @model_validator(mode="after")
    def validate_agreement(self) -> Self:
        direction = _sign(self.tb20_risk_difference) == _sign(self.tb21_risk_difference)
        if self.direction_agreement != direction:
            raise ValueError("cross-version direction agreement is stale")
        if self.disposition_agreement != (self.tb20_disposition == self.tb21_disposition):
            raise ValueError("cross-version disposition agreement is stale")
        return self


class SensitivityReport(FrozenModel):
    schema_version: Literal[1] = 1
    report_id: Literal["thesis-tb21-sensitivity-v1"] = "thesis-tb21-sensitivity-v1"
    role: Literal["sensitivity-only-no-pooled-inference"] = "sensitivity-only-no-pooled-inference"
    tb20_method_report: PrefixBenchFileBinding
    tb21_method_report: PrefixBenchFileBinding
    cross_version_test: Literal["none"] = "none"
    pooled_effect: Literal["not_computed"] = "not_computed"
    holm_family_membership: Literal["separate"] = "separate"
    comparisons: tuple[SensitivityComparison, ...] = Field(min_length=4, max_length=4)
    all_directions_agree: bool
    all_dispositions_agree: bool

    @model_validator(mode="after")
    def validate_summary(self) -> Self:
        if tuple(item.comparator for item in self.comparisons) != _COMPARATORS:
            raise ValueError("TB2.1 final comparison order has changed")
        if self.all_directions_agree != all(item.direction_agreement for item in self.comparisons):
            raise ValueError("TB2.1 final direction summary is stale")
        if self.all_dispositions_agree != all(
            item.disposition_agreement for item in self.comparisons
        ):
            raise ValueError("TB2.1 final disposition summary is stale")
        return self

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.model_dump(mode="json"))


class ArtifactEntry(FrozenModel):
    path: str = Field(min_length=1)
    data: bytes


class ArtifactBundle(FrozenModel):
    entries: tuple[ArtifactEntry, ...] = Field(min_length=3, max_length=5)
    complete_sources: bool
    final_available: bool
    waiting_for: tuple[str, ...]

    @model_validator(mode="after")
    def validate_order(self) -> Self:
        paths = tuple(entry.path for entry in self.entries)
        expected = OUTCOME_PATHS[1 : 1 + len(paths)]
        if paths != expected:
            raise ValueError("TB2.1 artifact bundle order has changed")
        if self.final_available != (len(paths) == 5):
            raise ValueError("TB2.1 final availability is stale")
        if self.complete_sources != (len(paths) >= 4):
            raise ValueError("TB2.1 source completion is stale")
        return self


def build_artifact_bundle(
    project_root: Path,
    executable: BoundExecutable,
) -> ArtifactBundle:
    run_root = project_root / SENSITIVITY_RUN_ROOT
    seal_data = read_regular_file(run_root / "run-seal.json", "TB2.1 run seal")
    seal = _parse_canonical(seal_data, RunSeal, "TB2.1 run seal")
    _validate_seal(seal, executable)

    terminals: list[TerminalReceipt] = []
    for task in executable.plan.tasks:
        state = fold_task_receipts(run_root, task)
        if state.status != "terminal" or state.terminal is None:
            raise ValueError(
                "TB2.1 canonical report requires 61 terminal tasks; "
                f"{task.member.registry_name} is {state.status}"
            )
        _validate_raw_bindings(project_root, state.terminal)
        terminals.append(state.terminal)

    lineage = AnalysisLineage(
        executable=executable.file,
        executable_commit=executable.executable_commit,
        producer=seal.producer,
    )
    canonical = CanonicalReport(
        lineage=lineage,
        run_seal=file_binding(SENSITIVITY_RUN_ROOT / "run-seal.json", seal_data),
        matrix=executable.protocol.matrix_file,
        tasks=tuple(
            CanonicalTask(
                ordinal=task.ordinal,
                key=task.key,
                member=task.member,
                status=terminal.status,
                reward=terminal.reward,
                exception_type=terminal.exception_type,
                result=terminal.result,
                config=terminal.config,
                journal=terminal.journal,
            )
            for task, terminal in zip(executable.plan.tasks, terminals, strict=True)
        ),
    )
    canonical_data = canonical.canonical_bytes()

    prefixes: dict[str, StatePrefix] = {}
    readiness_tasks: list[ReadinessTask] = []
    for task, terminal in zip(executable.plan.tasks, terminals, strict=True):
        if terminal.journal is None:
            readiness_tasks.append(
                ReadinessTask(
                    ordinal=task.ordinal,
                    key=task.key,
                    status="unavailable",
                    journal=None,
                    reason="terminal result has no bound schema-2 journal",
                )
            )
            continue
        try:
            prefix = _load_admitted_prefix(
                project_root,
                terminal,
                producer=seal.producer,
            )
        except (OSError, ValueError, JournalLoadError) as exc:
            readiness_tasks.append(
                ReadinessTask(
                    ordinal=task.ordinal,
                    key=task.key,
                    status="unavailable",
                    journal=None,
                    reason=_stable_reason(exc),
                )
            )
            continue
        projected_attempts = (
            len(
                build_slot_grid(
                    prefix,
                    task_identity_sha256=task.key.source_identity_sha256,
                )
            )
            // 4
        )
        prefixes[task.key.source_identity_sha256] = prefix
        readiness_tasks.append(
            ReadinessTask(
                ordinal=task.ordinal,
                key=task.key,
                status="admitted",
                journal=terminal.journal,
                journal_lines=prefix.through_line,
                projected_attempts=projected_attempts
                if not all(
                    slot.synthetic_attempt
                    for slot in build_slot_grid(
                        prefix,
                        task_identity_sha256=task.key.source_identity_sha256,
                    )
                )
                else 0,
            )
        )
    readiness_tuple = tuple(readiness_tasks)
    admitted = sum(task.status == "admitted" for task in readiness_tuple)
    readiness = ReadinessReport(
        canonical=file_binding(SENSITIVITY_CANONICAL, canonical_data),
        status="ready" if admitted == 61 else "source_incomplete",
        admitted_tasks=admitted,
        tasks=readiness_tuple,
    )
    readiness_data = readiness.canonical_bytes()

    campaign_tasks: list[CampaignTask] = []
    for assessment in readiness.tasks:
        selected_prefix = prefixes.get(assessment.key.source_identity_sha256)
        if assessment.status != "admitted" or selected_prefix is None:
            campaign_tasks.append(
                CampaignTask(
                    ordinal=assessment.ordinal,
                    key=assessment.key,
                    status="unavailable",
                    reason=assessment.reason or "source was not admitted",
                )
            )
            continue
        campaign_tasks.append(
            CampaignTask(
                ordinal=assessment.ordinal,
                key=assessment.key,
                status="available",
                projected_attempts=assessment.projected_attempts,
                campaign=run_offline_campaign(selected_prefix),
            )
        )
    campaign_tuple = tuple(campaign_tasks)
    campaign = CampaignReport(
        readiness=file_binding(SENSITIVITY_READINESS, readiness_data),
        available_tasks=sum(task.status == "available" for task in campaign_tuple),
        tasks=campaign_tuple,
    )
    campaign_data = campaign.canonical_bytes()
    entries = [
        ArtifactEntry(path=SENSITIVITY_CANONICAL.as_posix(), data=canonical_data),
        ArtifactEntry(path=SENSITIVITY_READINESS.as_posix(), data=readiness_data),
        ArtifactEntry(path=SENSITIVITY_CAMPAIGN.as_posix(), data=campaign_data),
    ]
    if admitted != 61:
        return ArtifactBundle(
            entries=tuple(entries),
            complete_sources=False,
            final_available=False,
            waiting_for=("61 admitted schema-2 journals",),
        )

    task_results = tuple(
        _build_method_task(
            task,
            prefixes[task.key.source_identity_sha256],
            campaign_task.campaign,
        )
        for task, campaign_task in zip(executable.plan.tasks, campaign.tasks, strict=True)
        if campaign_task.campaign is not None
    )
    if len(task_results) != 61:
        raise AssertionError("complete TB2.1 source cohort did not produce 61 method tasks")
    method = MethodReport(
        lineage=lineage,
        campaign=file_binding(SENSITIVITY_CAMPAIGN, campaign_data),
        tasks=task_results,
        analysis=analyze_sensitivity(task_results),
    )
    method_data = method.canonical_bytes()
    entries.append(ArtifactEntry(path=SENSITIVITY_METHOD_REPORT.as_posix(), data=method_data))

    reference_path = project_root / RQ2_REPORT
    if not reference_path.exists():
        return ArtifactBundle(
            entries=tuple(entries),
            complete_sources=True,
            final_available=False,
            waiting_for=(RQ2_REPORT.as_posix(),),
        )
    reference_data = read_regular_file(reference_path, "fixed TB2.0 method report")
    if git_bytes(project_root, "show", f"HEAD:{RQ2_REPORT.as_posix()}") != reference_data:
        raise ValueError("fixed TB2.0 method report differs from Git HEAD")
    reference = _parse_canonical(reference_data, RQ2Report, "fixed TB2.0 method report")
    final = _build_final(
        reference,
        method,
        reference_binding=file_binding(RQ2_REPORT, reference_data),
        method_binding=file_binding(SENSITIVITY_METHOD_REPORT, method_data),
    )
    entries.append(ArtifactEntry(path=SENSITIVITY_REPORT.as_posix(), data=final.canonical_bytes()))
    return ArtifactBundle(
        entries=tuple(entries),
        complete_sources=True,
        final_available=True,
        waiting_for=(),
    )


def analyze_sensitivity(tasks: tuple[RQ2TaskResult, ...]) -> RQ2Analysis:
    if len(tasks) != 61:
        raise ValueError("TB2.1 sensitivity inference requires exactly 61 tasks")
    identities = tuple(task.task_identity_sha256 for task in tasks)
    if len(set(identities)) != 61:
        raise ValueError("TB2.1 sensitivity inference requires unique task identities")
    summaries = tuple(_method_summary(tasks, method) for method in MainAnalysisMethod)
    raw = []
    for comparator in _COMPARATORS:
        rows = tuple(
            PairedBinaryTask(
                task_identity=task.task_identity_sha256,
                treatment_detected=_task_detected(task, MainAnalysisMethod.STATE_AWARE),
                comparator_detected=_task_detected(task, comparator),
            )
            for task in tasks
        )
        seed = hashlib.sha256(
            BOOTSTRAP_NAMESPACE.encode("ascii") + b"\0" + comparator.value.encode("ascii")
        ).digest()
        raw.append(
            (
                comparator,
                exact_mcnemar(rows),
                bootstrap_binary_risk_difference(rows, seed=seed),
            )
        )
    adjusted = holm_adjust(
        tuple(
            HolmInput(comparison=comparator.value, p_value=result.p_value)
            for comparator, result, _interval in raw
        )
    )
    contrasts = tuple(
        RQ2Contrast(
            comparator=comparator,
            mcnemar=result,
            risk_difference_interval=interval,
            holm_adjusted_p_value=holm.adjusted_p_value,
            disposition=(
                "confirmed"
                if result.risk_difference.as_fraction() > 0
                and holm.adjusted_p_value.as_fraction() < _ALPHA
                else "not_confirmed"
            ),
        )
        for (comparator, result, interval), holm in zip(raw, adjusted, strict=True)
    )
    confirmed = tuple(item.comparator for item in contrasts if item.disposition == "confirmed")
    return RQ2Analysis(
        methods=summaries,
        contrasts=contrasts,
        disposition=_disposition(confirmed),
    )


def deterministic_draw(
    method: MainAnalysisMethod,
    slot: RQ2SlotKey,
    draw_ordinal: int,
    choices: int,
) -> int:
    if choices < 1:
        raise ValueError("deterministic choice requires a nonempty candidate set")
    fields = (
        CHOICE_NAMESPACE,
        "20261003",
        "rq2",
        method.value,
        slot.task_identity_sha256,
        str(slot.attempt_ordinal),
        slot.operator.value,
        str(slot.slot_ordinal),
        str(draw_ordinal),
    )
    material = b"\0".join(value.encode("ascii") for value in fields)
    return int.from_bytes(hashlib.sha256(material).digest()[:8], "big") % choices


def _build_method_task(
    task: PlannedTask,
    prefix: StatePrefix,
    campaign: OfflineCampaignReport | None,
) -> RQ2TaskResult:
    if campaign is None:
        raise ValueError("TB2.1 method task requires an available campaign")
    slots = build_slot_grid(prefix, task_identity_sha256=task.key.source_identity_sha256)
    baseline, baseline_error = _baseline(prefix)
    document = _encode_events(prefix.events)
    random_view = RandomJsonView(canonical_event_bytes=document)
    schema_view = SchemaValidRandomView(candidates=_schema_valid_candidates(prefix))
    agent_view = AgentChaosView(
        canonical_agent_response_bytes=tuple(
            _compact_json(event.payload)
            for event in prefix.events
            if event.event_type == "agent_decision"
        )
    )
    stateless_view = StatelessSemanticView(
        ordered_event_kinds=tuple(event.event_type for event in prefix.events),
        allowed_edit_sites=tuple(
            StatelessEditSite(
                event_ordinal=ordinal,
                event_kind=event.event_type,
                canonical_event_bytes=_wire_event(event).canonical_bytes(),
            )
            for ordinal, event in enumerate(prefix.events, start=1)
            if event.event_type in set(_TARGET_EVENT_TYPE.values())
        ),
    )
    random_selections = tuple(_select_random(random_view, slot) for slot in slots)
    schema_selections = tuple(_select_schema(schema_view, slot) for slot in slots)
    agent_selections = tuple(_select_agent(agent_view, slot) for slot in slots)
    stateless_selections = tuple(_select_stateless(stateless_view, slot) for slot in slots)
    methods = (
        _state_aware_result(slots, campaign.cases),
        _classify_method(
            prefix,
            slots,
            MainAnalysisMethod.RANDOM_JSON,
            random_selections,
            baseline,
            baseline_error,
        ),
        _classify_method(
            prefix,
            slots,
            MainAnalysisMethod.SCHEMA_VALID_RANDOM,
            schema_selections,
            baseline,
            baseline_error,
        ),
        _classify_agent(
            prefix,
            slots,
            agent_selections,
            baseline,
            baseline_error,
        ),
        _classify_stateless_method(
            prefix,
            slots,
            stateless_view,
            stateless_selections,
            baseline,
            baseline_error,
        ),
    )
    return RQ2TaskResult(
        task_name=task.member.registry_name,
        task_identity_sha256=task.key.source_identity_sha256,
        slots=slots,
        methods=methods,
    )


def _baseline(prefix: StatePrefix) -> tuple[AuditReport | None, str | None]:
    try:
        return _stable_audit(prefix), None
    except (ValidationError, ValueError) as exc:
        return None, _stable_error(exc)


def _select_random(view: RandomJsonView, slot: RQ2SlotKey) -> BaselineSelection:
    data = view.canonical_event_bytes
    operation = deterministic_draw(MainAnalysisMethod.RANDOM_JSON, slot, 1, 4)
    position = deterministic_draw(MainAnalysisMethod.RANDOM_JSON, slot, 2, len(data))
    if operation == 0:
        candidate = data[:position] + data[position + 1 :]
    elif operation == 1:
        candidate = data[:position] + data[position : position + 1] + data[position:]
    elif operation == 2:
        replacement = b"0" if data[position : position + 1] != b"0" else b"1"
        candidate = data[:position] + replacement + data[position + 1 :]
    else:
        candidate = data[:position]
    return BaselineSelection(candidate_event_bytes=candidate)


def _select_schema(
    view: SchemaValidRandomView,
    slot: RQ2SlotKey,
) -> BaselineSelection | None:
    if not view.candidates:
        return None
    index = deterministic_draw(
        MainAnalysisMethod.SCHEMA_VALID_RANDOM,
        slot,
        1,
        len(view.candidates),
    )
    return BaselineSelection(candidate_event_bytes=view.candidates[index].canonical_event_bytes)


def _select_agent(
    view: AgentChaosView,
    slot: RQ2SlotKey,
) -> BaselineSelection | None:
    if not view.canonical_agent_response_bytes:
        return None
    index = deterministic_draw(
        MainAnalysisMethod.AGENTCHAOS_STYLE,
        slot,
        1,
        len(view.canonical_agent_response_bytes),
    )
    response = view.canonical_agent_response_bytes[index]
    operation = (slot.slot_ordinal - 1) % 4
    if operation == 0:
        candidate = b""
    elif operation == 1:
        candidate = response[: len(response) // 2]
    else:
        try:
            value = json.loads(response)
        except json.JSONDecodeError:
            value = None
        if not isinstance(value, dict):
            candidate = b"null"
        elif operation == 2:
            value.pop("action", None)
            candidate = _compact_json(value)
        else:
            value["action"] = 7
            candidate = _compact_json(value)
    return BaselineSelection(candidate_event_bytes=candidate)


def _select_stateless(
    view: StatelessSemanticView,
    slot: RQ2SlotKey,
) -> BaselineSelection | None:
    matching = tuple(
        site
        for site in view.allowed_edit_sites
        if site.event_kind == _TARGET_EVENT_TYPE[slot.operator]
    )
    if not matching:
        return None
    site = matching[
        deterministic_draw(
            MainAnalysisMethod.STATELESS_SEMANTIC,
            slot,
            1,
            len(matching),
        )
    ]
    try:
        wire = _WireEvent.model_validate_json(site.canonical_event_bytes)
        payload = copy.deepcopy(wire.payload)
        event_type = wire.type
        if slot.operator is MutationId.STALE_EVIDENCE_EPOCH:
            epoch = payload.get("work_epoch")
            if not isinstance(epoch, int) or isinstance(epoch, bool):
                return None
            payload["work_epoch"] = max(0, epoch - 1)
            checks = payload.get("checks")
            if isinstance(checks, list):
                for check in checks:
                    if isinstance(check, dict):
                        check["work_epoch"] = max(0, epoch - 1)
            isolation = payload.get("isolation")
            if isinstance(isolation, dict):
                isolation["work_epoch"] = max(0, epoch - 1)
        elif slot.operator is MutationId.REORDER_CHECK_RECEIPTS:
            checks = payload.get("checks")
            if not isinstance(checks, list) or len(checks) < 2:
                return None
            payload["checks"] = list(reversed(checks))
        elif slot.operator is MutationId.REVIEW_TIMEOUT_FALLBACK:
            event_type = "completion_review_error"
            payload = {
                "error": "injected completion review timeout",
                "error_type": "TimeoutError",
            }
        else:
            isolation = payload.get("isolation")
            if not isinstance(isolation, dict):
                return None
            candidate_id = isolation.get("candidate_image_id")
            if not isinstance(candidate_id, str):
                return None
            replacement = (
                "sha256:"
                + hashlib.sha256(f"{candidate_id}:{slot.operator.value}".encode()).hexdigest()
            )
            isolation["candidate_image_id"] = replacement
            checks = isolation.get("checks")
            if isinstance(checks, list):
                for check in checks:
                    if isinstance(check, dict):
                        check["started_from_image_id"] = replacement
        return BaselineSelection(
            candidate_event_bytes=wire.model_copy(
                update={"type": event_type, "payload": payload}
            ).canonical_bytes()
        )
    except (ValidationError, ValueError, TypeError):
        return None


def _classify_agent(
    prefix: StatePrefix,
    slots: tuple[RQ2SlotKey, ...],
    selections: tuple[BaselineSelection | None, ...],
    baseline: AuditReport | None,
    baseline_error: str | None,
) -> RQ2MethodResult:
    indices = tuple(
        index for index, event in enumerate(prefix.events) if event.event_type == "agent_decision"
    )
    outcomes: list[RQ2SlotOutcome] = []
    for slot, selection in zip(slots, selections, strict=True):
        if selection is None or not indices:
            outcomes.append(
                TargetNotReachedOutcome(
                    slot=slot,
                    method=MainAnalysisMethod.AGENTCHAOS_STYLE,
                    outcome=MainAnalysisOutcome.TARGET_NOT_REACHED,
                    reason="no agent response is available",
                )
            )
            continue
        selected = deterministic_draw(
            MainAnalysisMethod.AGENTCHAOS_STYLE,
            slot,
            1,
            len(indices),
        )
        try:
            payload = AgentDecision.model_validate_json(selection.candidate_event_bytes).model_dump(
                mode="json"
            )
        except ValidationError as exc:
            outcomes.append(
                InputRejectedOutcome(
                    slot=slot,
                    method=MainAnalysisMethod.AGENTCHAOS_STYLE,
                    outcome=MainAnalysisOutcome.INPUT_REJECTED,
                    reason=_stable_error(exc),
                )
            )
            continue
        events = list(prefix.events)
        event = events[indices[selected]]
        events[indices[selected]] = event.model_copy(update={"payload": payload})
        outcomes.append(
            _classify_trace(
                prefix,
                _replace_events(prefix, tuple(events)),
                slot,
                MainAnalysisMethod.AGENTCHAOS_STYLE,
                baseline,
                baseline_error,
            )
        )
    return RQ2MethodResult(
        method=MainAnalysisMethod.AGENTCHAOS_STYLE,
        outcomes=tuple(outcomes),
    )


def _classify_stateless_method(
    prefix: StatePrefix,
    slots: tuple[RQ2SlotKey, ...],
    view: StatelessSemanticView,
    selections: tuple[BaselineSelection | None, ...],
    baseline: AuditReport | None,
    baseline_error: str | None,
) -> RQ2MethodResult:
    outcomes: list[RQ2SlotOutcome] = []
    for slot, selection in zip(slots, selections, strict=True):
        matching = tuple(
            site
            for site in view.allowed_edit_sites
            if site.event_kind == _TARGET_EVENT_TYPE[slot.operator]
        )
        if selection is None or not matching:
            outcomes.append(
                TargetNotReachedOutcome(
                    slot=slot,
                    method=MainAnalysisMethod.STATELESS_SEMANTIC,
                    outcome=MainAnalysisOutcome.TARGET_NOT_REACHED,
                    reason="no stateless semantic edit site is available",
                )
            )
            continue
        site = matching[
            deterministic_draw(
                MainAnalysisMethod.STATELESS_SEMANTIC,
                slot,
                1,
                len(matching),
            )
        ]
        try:
            wire = _WireEvent.model_validate_json(selection.candidate_event_bytes)
            events = list(prefix.events)
            original = events[site.event_ordinal - 1]
            events[site.event_ordinal - 1] = original.model_copy(
                update={"event_type": wire.type, "payload": wire.payload}
            )
            candidate = _replace_events(prefix, tuple(events))
        except (ValidationError, ValueError, IndexError) as exc:
            outcomes.append(
                InputRejectedOutcome(
                    slot=slot,
                    method=MainAnalysisMethod.STATELESS_SEMANTIC,
                    outcome=MainAnalysisOutcome.INPUT_REJECTED,
                    reason=_stable_error(exc),
                )
            )
            continue
        outcomes.append(
            _classify_trace(
                prefix,
                candidate,
                slot,
                MainAnalysisMethod.STATELESS_SEMANTIC,
                baseline,
                baseline_error,
            )
        )
    return RQ2MethodResult(
        method=MainAnalysisMethod.STATELESS_SEMANTIC,
        outcomes=tuple(outcomes),
    )


def _method_summary(
    tasks: tuple[RQ2TaskResult, ...],
    method: MainAnalysisMethod,
) -> RQ2MethodSummary:
    task_outcomes = tuple(_method_outcomes(task, method) for task in tasks)
    outcomes = tuple(outcome for row in task_outcomes for outcome in row)
    counts = Counter(outcome.outcome for outcome in outcomes)
    scheduled = len(outcomes)
    accepted = scheduled - counts[MainAnalysisOutcome.INPUT_REJECTED]
    reached = accepted - counts[MainAnalysisOutcome.TARGET_NOT_REACHED]
    violations = counts[MainAnalysisOutcome.TARGET_VIOLATION]
    return RQ2MethodSummary(
        method=method,
        tasks=len(tasks),
        scheduled_slots=scheduled,
        outcomes=RQ2OutcomeCounts(
            **{outcome.value: counts[outcome] for outcome in MainAnalysisOutcome}
        ),
        input_acceptance_rate=ExactProbability.from_fraction(Fraction(accepted, scheduled)),
        target_reach_rate=ExactProbability.from_fraction(Fraction(reached, scheduled)),
        target_violation_rate=ExactProbability.from_fraction(Fraction(violations, scheduled)),
        oracle_equivalent_rate=ExactProbability.from_fraction(
            Fraction(counts[MainAnalysisOutcome.ORACLE_EQUIVALENT], scheduled)
        ),
        other_oracle_change_rate=ExactProbability.from_fraction(
            Fraction(counts[MainAnalysisOutcome.OTHER_ORACLE_CHANGE], scheduled)
        ),
        distinct_target_invariants=len(
            {
                outcome.slot.target_invariant
                for outcome in outcomes
                if outcome.outcome is MainAnalysisOutcome.TARGET_VIOLATION
            }
        ),
        target_violations_per_100_slots=ExactValue.from_fraction(
            Fraction(violations * 100, scheduled)
        ),
        tasks_with_target_violation=sum(
            any(outcome.outcome is MainAnalysisOutcome.TARGET_VIOLATION for outcome in row)
            for row in task_outcomes
        ),
    )


def _task_detected(task: RQ2TaskResult, method: MainAnalysisMethod) -> bool:
    return any(
        outcome.outcome is MainAnalysisOutcome.TARGET_VIOLATION
        for outcome in _method_outcomes(task, method)
    )


def _method_outcomes(
    task: RQ2TaskResult,
    method: MainAnalysisMethod,
) -> tuple[RQ2SlotOutcome, ...]:
    return next(item.outcomes for item in task.methods if item.method is method)


def _disposition(
    confirmed: tuple[MainAnalysisMethod, ...],
) -> Literal[
    "broad_superiority_confirmed",
    "nearest_baseline_advantage_confirmed",
    "broad_superiority_not_confirmed",
]:
    if confirmed == _COMPARATORS:
        return "broad_superiority_confirmed"
    if confirmed == (MainAnalysisMethod.STATELESS_SEMANTIC,):
        return "nearest_baseline_advantage_confirmed"
    return "broad_superiority_not_confirmed"


def _build_final(
    reference: RQ2Report,
    sensitivity: MethodReport,
    *,
    reference_binding: PrefixBenchFileBinding,
    method_binding: PrefixBenchFileBinding,
) -> SensitivityReport:
    reference_by_method = {
        contrast.comparator: contrast for contrast in reference.analysis.contrasts
    }
    sensitivity_by_method = {
        contrast.comparator: contrast for contrast in sensitivity.analysis.contrasts
    }
    comparisons = tuple(
        SensitivityComparison(
            comparator=method,
            tb20_risk_difference=reference_by_method[method].mcnemar.risk_difference,
            tb21_risk_difference=sensitivity_by_method[method].mcnemar.risk_difference,
            direction_agreement=(
                _sign(reference_by_method[method].mcnemar.risk_difference)
                == _sign(sensitivity_by_method[method].mcnemar.risk_difference)
            ),
            tb20_disposition=reference_by_method[method].disposition,
            tb21_disposition=sensitivity_by_method[method].disposition,
            disposition_agreement=(
                reference_by_method[method].disposition == sensitivity_by_method[method].disposition
            ),
        )
        for method in _COMPARATORS
    )
    return SensitivityReport(
        tb20_method_report=reference_binding,
        tb21_method_report=method_binding,
        comparisons=comparisons,
        all_directions_agree=all(item.direction_agreement for item in comparisons),
        all_dispositions_agree=all(item.disposition_agreement for item in comparisons),
    )


def validate_artifact_prefix(project_root: Path, bundle: ArtifactBundle) -> tuple[str, ...]:
    errors: list[str] = []
    expected = {entry.path: entry.data for entry in bundle.entries}
    for path in OUTCOME_PATHS[1:]:
        target = project_root / path
        if path in expected:
            if not os.path.lexists(target):
                errors.append(f"missing TB2.1 artifact: {path}")
            elif target.is_symlink() or not target.is_file():
                errors.append(f"TB2.1 artifact is not a regular file: {path}")
            elif target.read_bytes() != expected[path]:
                errors.append(f"stale TB2.1 artifact: {path}")
        elif os.path.lexists(target):
            errors.append(f"unexpected TB2.1 artifact before dependencies are ready: {path}")
    return tuple(errors)


def _validate_seal(seal: RunSeal, executable: BoundExecutable) -> None:
    if (
        seal.executable != executable.file
        or seal.executable_commit != executable.executable_commit
        or seal.protocol != executable.protocol.protocol_file
        or seal.matrix != executable.protocol.matrix_file
        or seal.run_plan_sha256 != executable.spec.run_plan_sha256
    ):
        raise ValueError("TB2.1 run seal does not match the executable")


def _validate_raw_bindings(project_root: Path, terminal: TerminalReceipt) -> None:
    for binding in (terminal.result, terminal.config, terminal.journal):
        if binding is not None:
            _read_bound_file(project_root, binding)


def _load_admitted_prefix(
    project_root: Path,
    terminal: TerminalReceipt,
    *,
    producer: ProducerAttestation,
) -> StatePrefix:
    if terminal.journal is None:
        raise ValueError("terminal result has no journal")
    result_data = _read_bound_file(project_root, terminal.result)
    config_data = _read_bound_file(project_root, terminal.config)
    journal_data = _read_bound_file(project_root, terminal.journal)
    try:
        result = _json_object(result_data, "TB2.1 result")
        saved_config = TrialConfig.model_validate_json(config_data).model_dump(mode="json")
        result_config = TrialConfig.model_validate(result.get("config")).model_dump(mode="json")
    except ValidationError as exc:
        raise ValueError("TB2.1 trial config is invalid") from exc
    if saved_config != result_config:
        raise ValueError("TB2.1 result config differs from config.json")
    metadata = _mapping(_mapping(result.get("agent_result")).get("metadata"))
    evidence = _mapping(metadata.get("evidence_harness"))
    if not isinstance(evidence.get("stop_reason"), str) or not evidence["stop_reason"]:
        raise ValueError("TB2.1 result is not a live Evidence Harness run")
    agent = _mapping(saved_config.get("agent"))
    if agent.get("name") != "evidence_harness.harbor_agent:EvidenceHarnessAgent":
        raise ValueError("TB2.1 trial config uses the wrong agent")
    kwargs = _mapping(agent.get("kwargs"))
    PREFIXBENCH_V1.validate_effective_options(kwargs)
    expected_provenance = {
        "prefixbench_profile": PREFIXBENCH_V1.name,
        "producer_commit": producer.commit,
        "producer_tree": producer.tree,
        "producer_source_sha256": producer.source_sha256,
    }
    if any(kwargs.get(key) != value for key, value in expected_provenance.items()):
        raise ValueError("TB2.1 trial config provenance differs from the run seal")

    prefix = load_state_prefix(
        journal_data,
        source_commit=producer.commit,
        expected_journal_sha256=terminal.journal.sha256,
    )
    started = prefix.events[0].payload
    if started.get("prefixbench_profile") != PREFIXBENCH_V1.name:
        raise ValueError("TB2.1 journal profile differs from the frozen profile")
    if started.get("options") != dict(PREFIXBENCH_V1.controlled_agent_options):
        raise ValueError("TB2.1 journal options differ from the frozen profile")
    try:
        journal_producer = ProducerAttestation.model_validate(started.get("producer"))
    except ValidationError as exc:
        raise ValueError("TB2.1 journal producer is invalid") from exc
    if journal_producer != producer:
        raise ValueError("TB2.1 journal producer differs from the run seal")
    return prefix


def _read_bound_file(project_root: Path, binding: PrefixBenchFileBinding) -> bytes:
    candidate = project_root / binding.path
    if candidate.is_symlink():
        raise ValueError(f"bound TB2.1 source cannot be a symlink: {binding.path}")
    path = candidate.resolve()
    try:
        path.relative_to(project_root.resolve())
    except ValueError as exc:
        raise ValueError(f"bound TB2.1 source is outside the project: {binding.path}") from exc
    data = read_regular_file(path, f"bound TB2.1 source {binding.path}")
    if file_binding(Path(binding.path), data) != binding:
        raise ValueError(f"bound TB2.1 source is stale: {binding.path}")
    return data


def _json_object(data: bytes, label: str) -> dict[str, object]:
    try:
        value = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is not valid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be a JSON object")
    return value


def _mapping(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _parse_canonical[T: FrozenModel](data: bytes, model: type[T], label: str) -> T:
    try:
        value = model.model_validate_json(data)
    except ValidationError as exc:
        raise ValueError(f"invalid {label}") from exc
    canonical = getattr(value, "canonical_bytes", None)
    expected = (
        canonical() if callable(canonical) else _canonical_json(value.model_dump(mode="json"))
    )
    if data != expected:
        raise ValueError(f"{label} is not canonical JSON")
    return value


def _sign(value: ExactValue) -> int:
    fraction = value.as_fraction()
    return (fraction > 0) - (fraction < 0)


def _stable_reason(error: Exception) -> str:
    return f"{type(error).__name__}: {' '.join(str(error).split())}"[:1_000]


def _compact_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()


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
