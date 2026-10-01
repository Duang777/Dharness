from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import Field, model_validator

from evidence_harness_mutation.model import FrozenModel

Sha256 = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
GitObjectId = Annotated[str, Field(pattern=r"^[0-9a-f]{40}$")]


class RevisionRole(StrEnum):
    VULNERABLE = "vulnerable"
    FIXED = "fixed"


class ProductionEntrypoint(StrEnum):
    EVIDENCE_LOOP = "EvidenceLoop.run"
    EVIDENCE_GATE = "EvidenceGate.decide"


class MutationOutcome(StrEnum):
    SURVIVED = "survived"
    KILLED = "killed"
    INCONCLUSIVE = "inconclusive"


class HistoricalCaseId(StrEnum):
    REVIEW_TIMEOUT_FALLBACK = "review_timeout_fallback"
    REORDER_CHECK_RECEIPTS = "reorder_check_receipts"
    REVIEW_RECEIPT_ORDER = "review_receipt_order"
    REVIEW_QUOTA_NO_BYPASS = "review_quota_no_bypass"
    FAILED_CHANGE_PROGRESS = "failed_change_progress"
    MAX_REPAIRS_EXACT = "max_repairs_exact"
    WALL_TIME_COMMAND_DEADLINE = "wall_time_command_deadline"


class HistoricalPropertyId(StrEnum):
    REVIEW_TIMEOUT_FAILS_CLOSED = "review_timeout_fails_closed"
    RECEIPTS_MATCH_PROPOSAL_ORDER = "receipts_match_proposal_order"
    REVIEW_SEES_EXECUTED_RECEIPTS = "review_sees_executed_receipts"
    REVIEW_QUOTA_FAILS_CLOSED = "review_quota_fails_closed"
    FAILED_CHANGES_DO_NOT_COUNT_AS_PROGRESS = "failed_changes_do_not_count_as_progress"
    REPAIR_BUDGET_COUNTS_GRANTED_REPAIRS = "repair_budget_counts_granted_repairs"
    WORK_PRESERVES_FINALIZATION_TIME = "work_preserves_finalization_time"


class HistoricalProperty(FrozenModel):
    id: HistoricalPropertyId
    statement: str = Field(min_length=1)


class RevisionSpec(FrozenModel):
    role: RevisionRole
    commit: GitObjectId
    tree: GitObjectId
    expected_outcome: Literal[MutationOutcome.SURVIVED, MutationOutcome.KILLED]


class CheckSpec(FrozenModel):
    id: str = Field(min_length=1)
    kind: Literal["artifact", "behavior", "build", "data", "service"]
    script: str = Field(min_length=1)
    proves: str = Field(min_length=1)
    cwd: str | None
    timeout_sec: int = Field(ge=1, le=900)


class CoverageSpec(FrozenModel):
    requirement: str = Field(min_length=1)
    check_ids: tuple[str, ...] = Field(min_length=1)


class ChangeCommandSpec(FrozenModel):
    id: str = Field(min_length=1)
    script: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    cwd: str | None
    timeout_sec: int = Field(ge=1, le=900)


class LoopOptionsSpec(FrozenModel):
    max_turns: int = Field(ge=1)
    max_environment_calls: int = Field(ge=1)
    max_repairs: int = Field(ge=0)
    max_recoveries: int = Field(ge=0)
    max_completion_reviews: int = Field(ge=0)
    max_wall_time_sec: int = Field(ge=1)
    max_model_call_timeout_sec: int = Field(ge=1)
    max_command_timeout_sec: int = Field(ge=1, le=900)
    verification_environment_reserve: int = Field(ge=0)
    enable_completion_review: bool

    @model_validator(mode="after")
    def validate_reserve(self) -> Self:
        if self.verification_environment_reserve >= self.max_environment_calls:
            raise ValueError(
                "verification_environment_reserve must be smaller than max_environment_calls"
            )
        return self


class ReviewTimeoutScenario(FrozenModel):
    kind: Literal["review_timeout"]
    instruction: str = Field(min_length=1)
    options: LoopOptionsSpec
    checks: tuple[CheckSpec]
    coverage: tuple[CoverageSpec, ...] = Field(min_length=1)
    review_error: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_coverage(self) -> Self:
        _validate_scenario_coverage(self.checks, self.coverage)
        return self


class ReorderReceiptsScenario(FrozenModel):
    kind: Literal["reorder_receipts"]
    work_epoch: int = Field(ge=0)
    checks: tuple[CheckSpec, CheckSpec]
    coverage: tuple[CoverageSpec, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_coverage(self) -> Self:
        _validate_scenario_coverage(self.checks, self.coverage)
        return self


class ReviewReceiptOrderScenario(FrozenModel):
    kind: Literal["review_receipt_order"]
    instruction: str = Field(min_length=1)
    options: LoopOptionsSpec
    checks: tuple[CheckSpec]
    coverage: tuple[CoverageSpec, ...] = Field(min_length=1)
    receipt_marker: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_coverage(self) -> Self:
        _validate_scenario_coverage(self.checks, self.coverage)
        return self


class ReviewQuotaScenario(FrozenModel):
    kind: Literal["review_quota_no_bypass"]
    instruction: str = Field(min_length=1)
    options: LoopOptionsSpec
    checks: tuple[CheckSpec]
    coverage: tuple[CoverageSpec, ...] = Field(min_length=1)
    rejection_rationales: tuple[str, str]

    @model_validator(mode="after")
    def validate_coverage(self) -> Self:
        _validate_scenario_coverage(self.checks, self.coverage)
        return self


class FailedChangeScenario(FrozenModel):
    kind: Literal["failed_change_progress"]
    instruction: str = Field(min_length=1)
    options: LoopOptionsSpec
    failed_commands: tuple[ChangeCommandSpec, ChangeCommandSpec, ChangeCommandSpec]
    failure_stderr: str = Field(min_length=1)
    terminal_stop_category: Literal["blocked", "unsafe", "impossible"]


class MaxRepairsScenario(FrozenModel):
    kind: Literal["max_repairs_exact"]
    instruction: str = Field(min_length=1)
    options: LoopOptionsSpec
    checks: tuple[CheckSpec]
    coverage: tuple[CoverageSpec, ...] = Field(min_length=1)
    failure_stderr: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_coverage(self) -> Self:
        _validate_scenario_coverage(self.checks, self.coverage)
        if self.options.max_repairs != 1:
            raise ValueError("Historical-7 max-repairs scenario must use max_repairs=1")
        return self


class WallTimeScenario(FrozenModel):
    kind: Literal["wall_time_command_deadline"]
    instruction: str = Field(min_length=1)
    options: LoopOptionsSpec
    command: ChangeCommandSpec
    model_clock_target_sec: float = Field(ge=0)
    command_duration_sec: float = Field(gt=0)
    terminal_stop_category: Literal["blocked", "unsafe", "impossible"]

    @model_validator(mode="after")
    def validate_clock(self) -> Self:
        if self.model_clock_target_sec >= self.options.max_wall_time_sec:
            raise ValueError("model clock target must precede the run deadline")
        return self


HistoricalScenario = Annotated[
    ReviewTimeoutScenario
    | ReorderReceiptsScenario
    | ReviewReceiptOrderScenario
    | ReviewQuotaScenario
    | FailedChangeScenario
    | MaxRepairsScenario
    | WallTimeScenario,
    Field(discriminator="kind"),
]


class HistoricalCaseBase(FrozenModel):
    property: HistoricalProperty
    revisions: tuple[RevisionSpec, RevisionSpec]


class ReviewTimeoutCase(HistoricalCaseBase):
    case_id: Literal[HistoricalCaseId.REVIEW_TIMEOUT_FALLBACK]
    entrypoint: Literal[ProductionEntrypoint.EVIDENCE_LOOP]
    scenario: ReviewTimeoutScenario


class ReorderReceiptsCase(HistoricalCaseBase):
    case_id: Literal[HistoricalCaseId.REORDER_CHECK_RECEIPTS]
    entrypoint: Literal[ProductionEntrypoint.EVIDENCE_GATE]
    scenario: ReorderReceiptsScenario


class ReviewReceiptOrderCase(HistoricalCaseBase):
    case_id: Literal[HistoricalCaseId.REVIEW_RECEIPT_ORDER]
    entrypoint: Literal[ProductionEntrypoint.EVIDENCE_LOOP]
    scenario: ReviewReceiptOrderScenario


class ReviewQuotaCase(HistoricalCaseBase):
    case_id: Literal[HistoricalCaseId.REVIEW_QUOTA_NO_BYPASS]
    entrypoint: Literal[ProductionEntrypoint.EVIDENCE_LOOP]
    scenario: ReviewQuotaScenario


class FailedChangeCase(HistoricalCaseBase):
    case_id: Literal[HistoricalCaseId.FAILED_CHANGE_PROGRESS]
    entrypoint: Literal[ProductionEntrypoint.EVIDENCE_LOOP]
    scenario: FailedChangeScenario


class MaxRepairsCase(HistoricalCaseBase):
    case_id: Literal[HistoricalCaseId.MAX_REPAIRS_EXACT]
    entrypoint: Literal[ProductionEntrypoint.EVIDENCE_LOOP]
    scenario: MaxRepairsScenario


class WallTimeCase(HistoricalCaseBase):
    case_id: Literal[HistoricalCaseId.WALL_TIME_COMMAND_DEADLINE]
    entrypoint: Literal[ProductionEntrypoint.EVIDENCE_LOOP]
    scenario: WallTimeScenario


HistoricalCase = Annotated[
    ReviewTimeoutCase
    | ReorderReceiptsCase
    | ReviewReceiptOrderCase
    | ReviewQuotaCase
    | FailedChangeCase
    | MaxRepairsCase
    | WallTimeCase,
    Field(discriminator="case_id"),
]

_VULNERABLE_BA2 = (
    "vulnerable",
    "ba2cbaebb1ceb97b024c549ee3e00790bd7e5f78",
    "5f30ae8405443ab41774b770491c5a37bd7c0f7c",
    "survived",
)
_FIXED_5F64 = (
    "fixed",
    "5f64d669a3bd8078dba075717d044fbc29694cd5",
    "ab3ab8e3856eace4b273278fcc6d3ae5ad7dcc03",
    "killed",
)
_EXPECTED_REVISIONS = {
    HistoricalCaseId.REVIEW_TIMEOUT_FALLBACK: (_VULNERABLE_BA2, _FIXED_5F64),
    HistoricalCaseId.REORDER_CHECK_RECEIPTS: (
        (
            "vulnerable",
            "6f74c19f74b68dd7d79d64c4c205274ae2cd1788",
            "283427564a6972ca2c3019e811e3a75541e214f5",
            "survived",
        ),
        (
            "fixed",
            "289bea40d10e0e94b98b1247617d06a12fc46f52",
            "a87bee24f82ca14f4cf49c2582b5d71553b1eb99",
            "killed",
        ),
    ),
    HistoricalCaseId.REVIEW_RECEIPT_ORDER: (_VULNERABLE_BA2, _FIXED_5F64),
    HistoricalCaseId.REVIEW_QUOTA_NO_BYPASS: (_VULNERABLE_BA2, _FIXED_5F64),
    HistoricalCaseId.FAILED_CHANGE_PROGRESS: (_VULNERABLE_BA2, _FIXED_5F64),
    HistoricalCaseId.MAX_REPAIRS_EXACT: (_VULNERABLE_BA2, _FIXED_5F64),
    HistoricalCaseId.WALL_TIME_COMMAND_DEADLINE: (
        (
            "vulnerable",
            "5f64d669a3bd8078dba075717d044fbc29694cd5",
            "ab3ab8e3856eace4b273278fcc6d3ae5ad7dcc03",
            "survived",
        ),
        (
            "fixed",
            "3a3b83f8315304d2fd39130c524fa2d41e4807d5",
            "98ef1b63b7d368ffed6d0391178c4a8a2134d373",
            "killed",
        ),
    ),
}
_EXPECTED_PROPERTIES = {
    HistoricalCaseId.REVIEW_TIMEOUT_FALLBACK: (
        HistoricalPropertyId.REVIEW_TIMEOUT_FAILS_CLOSED,
        "A review timeout cannot produce verified when completion review is required.",
    ),
    HistoricalCaseId.REORDER_CHECK_RECEIPTS: (
        HistoricalPropertyId.RECEIPTS_MATCH_PROPOSAL_ORDER,
        "Accepted receipts must be complete, successful, and in proposal order.",
    ),
    HistoricalCaseId.REVIEW_RECEIPT_ORDER: (
        HistoricalPropertyId.REVIEW_SEES_EXECUTED_RECEIPTS,
        "The completion reviewer must evaluate executed receipts before acceptance.",
    ),
    HistoricalCaseId.REVIEW_QUOTA_NO_BYPASS: (
        HistoricalPropertyId.REVIEW_QUOTA_FAILS_CLOSED,
        "Exhausting the review quota cannot bypass required semantic review.",
    ),
    HistoricalCaseId.FAILED_CHANGE_PROGRESS: (
        HistoricalPropertyId.FAILED_CHANGES_DO_NOT_COUNT_AS_PROGRESS,
        "A failed change command cannot count as productive progress.",
    ),
    HistoricalCaseId.MAX_REPAIRS_EXACT: (
        HistoricalPropertyId.REPAIR_BUDGET_COUNTS_GRANTED_REPAIRS,
        "max_repairs=N grants exactly N repair cycles.",
    ),
    HistoricalCaseId.WALL_TIME_COMMAND_DEADLINE: (
        HistoricalPropertyId.WORK_PRESERVES_FINALIZATION_TIME,
        "Ordinary work commands cannot consume reserved finalization wall time.",
    ),
}
EXPECTED_CASE_IDS = tuple(HistoricalCaseId)


class HistoricalManifest(FrozenModel):
    schema_version: Literal[2]
    suite_id: Literal["historical-7"]
    uv_lock_sha256: Sha256
    cases: tuple[HistoricalCase, ...] = Field(min_length=7, max_length=7)

    @model_validator(mode="after")
    def validate_frozen_scope(self) -> Self:
        case_ids = tuple(case.case_id for case in self.cases)
        if case_ids != EXPECTED_CASE_IDS:
            raise ValueError("Historical-7 cases do not match the frozen order")
        for case in self.cases:
            expected_property = _EXPECTED_PROPERTIES[case.case_id]
            if (case.property.id, case.property.statement) != expected_property:
                raise ValueError(f"Historical-7 property changed for {case.case_id}")
            revisions = tuple(
                (
                    revision.role.value,
                    revision.commit,
                    revision.tree,
                    revision.expected_outcome.value,
                )
                for revision in case.revisions
            )
            if revisions != _EXPECTED_REVISIONS[case.case_id]:
                raise ValueError(f"Historical-7 revision matrix changed for {case.case_id}")
        return self


class FileBinding(FrozenModel):
    path: str = Field(min_length=1)
    sha256: Sha256


class SourceSetBinding(FrozenModel):
    algorithm: Literal["sha256-length-framed-path-content-v1"]
    sha256: Sha256
    files: tuple[FileBinding, ...] = Field(min_length=3)


class ArchiveBinding(FrozenModel):
    commit: GitObjectId
    tree: GitObjectId
    archive_commit: GitObjectId
    archive_sha256: Sha256
    source_set: SourceSetBinding


class ControlBinding(FrozenModel):
    manifest: FileBinding
    worker: FileBinding
    coordinator: FileBinding
    schema_module: FileBinding


class ProductionBinding(FrozenModel):
    entrypoint: ProductionEntrypoint
    module_member: str = Field(min_length=1)
    source_path: str = Field(min_length=1)
    source_sha256: Sha256
    production_source_sha256: Sha256


class LoopSummary(FrozenModel):
    stop_reason: str = Field(min_length=1)
    failure_category: str | None
    evidence_accepted: bool | None
    rejection_reasons: tuple[str, ...]
    decision_calls: int = Field(ge=0)
    review_calls: int = Field(ge=0)
    environment_calls: int = Field(ge=0)
    turns_used: int = Field(ge=0)
    repairs_used: int = Field(ge=0)
    recoveries_used: int = Field(ge=0)
    review_error_count: int = Field(ge=0)
    verification_attempts: int = Field(ge=0)
    accepted_verification_count: int = Field(ge=0)
    completion_rejection_count: int = Field(ge=0)


class LoopObservation(FrozenModel):
    binding: ProductionBinding
    run: LoopSummary


class ReviewTimeoutObservation(LoopObservation):
    kind: Literal["review_timeout"]


class ReorderReceiptsObservation(FrozenModel):
    kind: Literal["reorder_receipts"]
    binding: ProductionBinding
    proposal_rejections: tuple[str, ...]
    proposed_check_ids: tuple[str, ...]
    supplied_receipt_ids: tuple[str, ...]
    accepted: bool
    rejection_reasons: tuple[str, ...]


class ReviewReceiptOrderObservation(LoopObservation):
    kind: Literal["review_receipt_order"]
    call_order: tuple[Literal["check", "review"], ...]
    review_saw_receipt_marker: tuple[bool, ...]


class ReviewQuotaObservation(LoopObservation):
    kind: Literal["review_quota_no_bypass"]
    check_calls: int = Field(ge=0)


class FailedChangeObservation(LoopObservation):
    kind: Literal["failed_change_progress"]
    failed_change_ids: tuple[str, ...]
    stop_decision_consumed: bool


class MaxRepairsObservation(LoopObservation):
    kind: Literal["max_repairs_exact"]
    failed_check_calls: int = Field(ge=0)


class WallTimeObservation(LoopObservation):
    kind: Literal["wall_time_command_deadline"]
    work_command_timeout_sec: int | None
    work_command_return_code: int | None
    work_command_failure: str | None
    final_clock_sec: float = Field(ge=0)
    finalization_triggers: tuple[str, ...]


ProductionObservation = Annotated[
    ReviewTimeoutObservation
    | ReorderReceiptsObservation
    | ReviewReceiptOrderObservation
    | ReviewQuotaObservation
    | FailedChangeObservation
    | MaxRepairsObservation
    | WallTimeObservation,
    Field(discriminator="kind"),
]


class ProbeRequest(FrozenModel):
    schema_version: Literal[2]
    suite_id: Literal["historical-7"]
    case_id: HistoricalCaseId
    commit: GitObjectId
    tree: GitObjectId
    manifest_sha256: Sha256
    production_source_sha256: Sha256
    source_root: str = Field(min_length=1)
    scenario: HistoricalScenario


class ProbeResponse(FrozenModel):
    schema_version: Literal[2]
    suite_id: Literal["historical-7"]
    case_id: HistoricalCaseId
    commit: GitObjectId
    tree: GitObjectId
    manifest_sha256: Sha256
    production_source_sha256: Sha256
    observation: ProductionObservation


class CompletedCaseResult(FrozenModel):
    kind: Literal["completed"]
    case_id: HistoricalCaseId
    property_id: HistoricalPropertyId
    revision_role: RevisionRole
    commit: GitObjectId
    tree: GitObjectId
    archive_source_sha256: Sha256
    expected_outcome: Literal[MutationOutcome.SURVIVED, MutationOutcome.KILLED]
    observed_outcome: MutationOutcome
    matches_expected: bool
    observation: ProductionObservation

    @model_validator(mode="after")
    def validate_match(self) -> Self:
        if self.matches_expected != (self.observed_outcome == self.expected_outcome):
            raise ValueError("matches_expected does not match the observed outcome")
        if self.observation.binding.production_source_sha256 != self.archive_source_sha256:
            raise ValueError("observation source binding does not match the archive")
        return self


class ErrorCaseResult(FrozenModel):
    kind: Literal["error"]
    case_id: HistoricalCaseId
    property_id: HistoricalPropertyId
    revision_role: RevisionRole
    commit: GitObjectId
    tree: GitObjectId
    expected_outcome: Literal[MutationOutcome.SURVIVED, MutationOutcome.KILLED]
    archive_source_sha256: Sha256 | None = None
    error: str = Field(min_length=1)


HistoricalCaseResult = Annotated[
    CompletedCaseResult | ErrorCaseResult,
    Field(discriminator="kind"),
]


class HistoricalReport(FrozenModel):
    schema_version: Literal[2]
    suite_id: Literal["historical-7"]
    uv_lock_sha256: Sha256
    control: ControlBinding
    archives: tuple[ArchiveBinding, ...] = Field(max_length=5)
    results: tuple[HistoricalCaseResult, ...] = Field(min_length=14, max_length=14)
    verdict: Literal["passed", "failed", "error"]

    @model_validator(mode="after")
    def validate_report(self) -> Self:
        cells = tuple((result.case_id, result.revision_role) for result in self.results)
        if cells != expected_cell_keys():
            raise ValueError("Historical-7 report does not contain the frozen fourteen cells")
        expected_archive_keys = _expected_archive_keys()
        archive_keys = tuple((archive.commit, archive.tree) for archive in self.archives)
        if len(set(archive_keys)) != len(archive_keys):
            raise ValueError("Historical-7 report contains duplicate archives")
        if any(key not in expected_archive_keys for key in archive_keys):
            raise ValueError("Historical-7 report does not contain the frozen archives")
        if (
            not any(result.kind == "error" for result in self.results)
            and archive_keys != expected_archive_keys
        ):
            raise ValueError("successful Historical-7 report requires all frozen archives")
        source_by_revision = {
            (archive.commit, archive.tree): archive.source_set.sha256 for archive in self.archives
        }
        for result in self.results:
            if (
                result.kind == "completed"
                and result.archive_source_sha256 != source_by_revision[(result.commit, result.tree)]
            ):
                raise ValueError("cell source binding does not match the report archive")
        expected_verdict = (
            "error"
            if any(result.kind == "error" for result in self.results)
            else "passed"
            if all(
                result.matches_expected
                for result in self.results
                if isinstance(result, CompletedCaseResult)
            )
            else "failed"
        )
        if self.verdict != expected_verdict:
            raise ValueError("report verdict does not match its cell results")
        return self


def expected_cell_keys() -> tuple[tuple[HistoricalCaseId, RevisionRole], ...]:
    return tuple(
        (case_id, role)
        for case_id in EXPECTED_CASE_IDS
        for role in (RevisionRole.VULNERABLE, RevisionRole.FIXED)
    )


def _expected_archive_keys() -> tuple[tuple[str, str], ...]:
    unique: dict[tuple[str, str], None] = {}
    for case_id in EXPECTED_CASE_IDS:
        for _, commit, tree, _ in _EXPECTED_REVISIONS[case_id]:
            unique.setdefault((commit, tree), None)
    return tuple(unique)


def _validate_scenario_coverage(
    checks: tuple[CheckSpec, ...],
    coverage: tuple[CoverageSpec, ...],
) -> None:
    check_ids = tuple(check.id for check in checks)
    if len(check_ids) != len(set(check_ids)):
        raise ValueError("scenario check ids must be unique")
    covered_ids = tuple(check_id for item in coverage for check_id in item.check_ids)
    unknown_ids = set(covered_ids) - set(check_ids)
    if unknown_ids:
        raise ValueError(f"scenario coverage references unknown checks: {sorted(unknown_ids)}")
    if set(covered_ids) != set(check_ids):
        raise ValueError("scenario coverage must use every check")
