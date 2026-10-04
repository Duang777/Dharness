from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from evidence_harness_mutation.historical_cases import HistoricalReport
from evidence_harness_mutation.main_analysis_executable import (
    MainAnalysisExecutable,
    MiniSweCohort,
    load_main_analysis_executable,
)
from evidence_harness_mutation.main_analysis_protocol import (
    EXECUTABLE_PROTOCOL,
    MAIN_PROTOCOL,
    MAIN_REPORT,
    MINISWE_COHORT,
    RQ2_REPORT,
    RQ3_REPORT,
    RQ4_REPORT,
    TEST_CAMPAIGN,
    TEST_READINESS,
    MainAnalysisProtocol,
    load_main_analysis_protocol,
)
from evidence_harness_mutation.main_analysis_report import (
    AnalysisLineage,
    RQ2Report,
    RQ3Report,
    RQ4Report,
    ThesisMainAnalysisReport,
    analyze_rq2,
    analyze_rq3,
    analyze_rq4,
)
from evidence_harness_mutation.model import FrozenModel, Sha256
from evidence_harness_mutation.prefixbench import (
    PrefixBenchFileBinding,
    PrefixBenchReadinessV2,
)
from evidence_harness_mutation.prefixbench_analysis import (
    DEVELOPMENT_ANALYSIS,
    PrefixBenchDevelopmentAnalysisReport,
)
from evidence_harness_mutation.prefixbench_test_campaign import (
    PrefixBenchTestCampaignReport,
)

HISTORICAL_REPORT = Path("evaluation/historical-7.json")
TABLE_BUNDLE = Path("evaluation/thesis-tables-v1.json")
TABLE_MARKDOWN = Path("docs/thesis-tables-v1.md")


class ArtifactId(StrEnum):
    PROTOCOL = "protocol"
    EXECUTABLE = "executable"
    MINISWE_COHORT = "miniswe_cohort"
    HISTORICAL_7 = "historical_7"
    DEVELOPMENT = "development"
    TEST_READINESS = "test_readiness"
    TEST_CAMPAIGN = "test_campaign"
    RQ2 = "rq2"
    RQ3 = "rq3"
    RQ4 = "rq4"
    MAIN = "main"


_SOURCE_PATHS: dict[ArtifactId, Path] = {
    ArtifactId.PROTOCOL: MAIN_PROTOCOL,
    ArtifactId.EXECUTABLE: EXECUTABLE_PROTOCOL,
    ArtifactId.MINISWE_COHORT: MINISWE_COHORT,
    ArtifactId.HISTORICAL_7: HISTORICAL_REPORT,
    ArtifactId.DEVELOPMENT: DEVELOPMENT_ANALYSIS,
    ArtifactId.TEST_READINESS: TEST_READINESS,
    ArtifactId.TEST_CAMPAIGN: TEST_CAMPAIGN,
    ArtifactId.RQ2: RQ2_REPORT,
    ArtifactId.RQ3: RQ3_REPORT,
    ArtifactId.RQ4: RQ4_REPORT,
    ArtifactId.MAIN: MAIN_REPORT,
}
_SOURCE_ORDER = tuple(ArtifactId)
_MODEL_TYPES: dict[ArtifactId, type[BaseModel]] = {
    ArtifactId.PROTOCOL: MainAnalysisProtocol,
    ArtifactId.EXECUTABLE: MainAnalysisExecutable,
    ArtifactId.MINISWE_COHORT: MiniSweCohort,
    ArtifactId.HISTORICAL_7: HistoricalReport,
    ArtifactId.DEVELOPMENT: PrefixBenchDevelopmentAnalysisReport,
    ArtifactId.TEST_READINESS: PrefixBenchReadinessV2,
    ArtifactId.TEST_CAMPAIGN: PrefixBenchTestCampaignReport,
    ArtifactId.RQ2: RQ2Report,
    ArtifactId.RQ3: RQ3Report,
    ArtifactId.RQ4: RQ4Report,
    ArtifactId.MAIN: ThesisMainAnalysisReport,
}

_TABLE_IDS = (
    "rq1",
    "rq2",
    "rq3",
    "rq4",
    "historical_partition",
    "rq2_partition",
    "rq3_partition",
    "rq4_partition",
    "protocol_deviations",
    "source_bindings",
    "git_chronology",
    "publication_inventory",
)
_DECIMAL = re.compile(r"^-?[0-9]+\.[0-9]{12}$")
_ARRAY_INDEX = re.compile(r"^(?:0|[1-9][0-9]*)$")
_FIXED_LABELS = frozenset(
    {
        "analysis_status",
        "artifact",
        "case_pairs",
        "byte_identical_rebuild",
        "collection_complete",
        "collection_producer",
        "credentials_are_not_artifacts",
        "design_base",
        "development_analysis",
        "disposition",
        "evidence_role",
        "error_count",
        "excluded_not_an_artifact",
        "executable",
        "historical_7",
        "fixed_confirmation_rate",
        "included_canonical_artifact",
        "inferential_test",
        "main_report",
        "miniswe_cohort",
        "none_recorded",
        "not_reported_insufficient",
        "outcome_report_producer",
        "protocol",
        "publication_decision_not_recorded",
        "required_repository_artifact",
        "rq2",
        "rq2_source_campaign",
        "rq3",
        "rq3_source_rq2",
        "rq4",
        "rq4_source_cohort",
        "successful_pairs",
        "paired_success_rate",
        "test_campaign",
        "test_campaign_canonical",
        "test_campaign_matrix",
        "test_campaign_progress",
        "test_campaign_readiness",
        "test_campaign_run_config",
        "test_matrix",
        "test_protocol",
        "threshold",
        "unavailable_in_canonical_artifacts",
        "inconclusive_count",
        "vulnerable_reproduction_rate",
    }
)


class ArtifactBinding(FrozenModel):
    artifact: ArtifactId
    path: str = Field(min_length=1)
    bytes: int = Field(ge=1)
    sha256: Sha256


class CellDerivation(StrEnum):
    COPY = "copy"
    EXACT_DECIMAL_COPY = "exact_decimal_copy"
    JOIN_EXACT_DECIMAL_BOUNDS = "join_exact_decimal_bounds"
    JOIN_INTEGER_RATIO = "join_integer_ratio"
    FIXED_LABEL = "fixed_label"
    MISSING_MEMBER = "missing_member"
    HISTORICAL_VULNERABLE_RATE = "historical_vulnerable_rate"
    HISTORICAL_FIXED_RATE = "historical_fixed_rate"
    HISTORICAL_PAIRED_RATE = "historical_paired_rate"
    HISTORICAL_INCONCLUSIVE_COUNT = "historical_inconclusive_count"
    HISTORICAL_ERROR_COUNT = "historical_error_count"


class SourceRef(FrozenModel):
    artifact: ArtifactId
    pointer: str

    @field_validator("pointer")
    @classmethod
    def validate_pointer(cls, value: str) -> str:
        _pointer_tokens(value)
        return value


class EvidenceCell(FrozenModel):
    text: str
    sources: tuple[SourceRef, ...] = Field(min_length=1)
    derivation: CellDerivation
    missing_member: str | None = None

    @model_validator(mode="after")
    def validate_missing_member(self) -> Self:
        if self.derivation is CellDerivation.MISSING_MEMBER:
            if not self.missing_member or len(self.sources) != 1:
                raise ValueError("missing-member cells require one source and a member name")
        elif self.missing_member is not None:
            raise ValueError("only missing-member cells may name a missing member")
        return self


class TableColumn(FrozenModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    heading: str = Field(min_length=1)


class TableRow(FrozenModel):
    row_id: str = Field(min_length=1)
    cells: tuple[EvidenceCell, ...] = Field(min_length=1)


class TablePanel(FrozenModel):
    panel_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    title: str = Field(min_length=1)
    columns: tuple[TableColumn, ...] = Field(min_length=1)
    rows: tuple[TableRow, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_grid(self) -> Self:
        keys = tuple(column.key for column in self.columns)
        if len(keys) != len(set(keys)):
            raise ValueError("table column keys must be unique")
        row_ids = tuple(row.row_id for row in self.rows)
        if len(row_ids) != len(set(row_ids)):
            raise ValueError("table row IDs must be unique")
        if any(len(row.cells) != len(self.columns) for row in self.rows):
            raise ValueError("table rows must match the column count")
        return self


class ThesisTable(FrozenModel):
    table_id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    title: str = Field(min_length=1)
    section: Literal["body", "appendix"]
    panels: tuple[TablePanel, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_panels(self) -> Self:
        panel_ids = tuple(panel.panel_id for panel in self.panels)
        if len(panel_ids) != len(set(panel_ids)):
            raise ValueError("table panel IDs must be unique")
        return self


class ReproducibilityEntry(FrozenModel):
    cell_pointer: str
    sources: tuple[SourceRef, ...] = Field(min_length=1)

    @field_validator("cell_pointer")
    @classmethod
    def validate_pointer(cls, value: str) -> str:
        _pointer_tokens(value)
        return value


class ThesisTableBundle(FrozenModel):
    schema_version: Literal[1] = 1
    bundle_id: Literal["thesis-tables-v1"] = "thesis-tables-v1"
    source_artifacts: tuple[ArtifactBinding, ...] = Field(
        min_length=len(_SOURCE_ORDER),
        max_length=len(_SOURCE_ORDER),
    )
    tables: tuple[ThesisTable, ...] = Field(
        min_length=len(_TABLE_IDS),
        max_length=len(_TABLE_IDS),
    )
    reproducibility_index: tuple[ReproducibilityEntry, ...]
    rebuild_scope: Literal[
        "canonical-projection-only-no-model-environment-campaign-or-reducer-execution"
    ] = "canonical-projection-only-no-model-environment-campaign-or-reducer-execution"

    @model_validator(mode="after")
    def validate_structure(self) -> Self:
        if tuple(item.artifact for item in self.source_artifacts) != _SOURCE_ORDER:
            raise ValueError("table source artifact order has changed")
        if tuple(table.table_id for table in self.tables) != _TABLE_IDS:
            raise ValueError("thesis table order has changed")
        expected = _reproducibility_index(self.tables)
        if self.reproducibility_index != expected:
            raise ValueError("table reproducibility index is stale")
        known = {item.artifact for item in self.source_artifacts}
        if any(
            source.artifact not in known
            for entry in self.reproducibility_index
            for source in entry.sources
        ):
            raise ValueError("table cell references an unknown source artifact")
        return self

    def canonical_bytes(self) -> bytes:
        return _canonical_json(self.model_dump(mode="json"))


class ThesisTablesBuildResult(FrozenModel):
    bundle_state: Literal["created", "unchanged"]
    markdown_state: Literal["created", "unchanged"]
    bundle_sha256: Sha256
    markdown_sha256: Sha256


@dataclass(frozen=True, slots=True)
class _LoadedArtifact:
    binding: ArtifactBinding
    data: bytes
    document: dict[str, Any]
    model: BaseModel


@dataclass(frozen=True, slots=True)
class _CanonicalInputs:
    artifacts: dict[ArtifactId, _LoadedArtifact]

    def artifact(self, artifact: ArtifactId) -> _LoadedArtifact:
        return self.artifacts[artifact]

    def document(self, artifact: ArtifactId) -> dict[str, Any]:
        return self.artifact(artifact).document

    def typed[T: BaseModel](self, artifact: ArtifactId, model: type[T]) -> T:
        value = self.artifact(artifact).model
        if not isinstance(value, model):
            raise TypeError(f"{artifact.value} did not load as {model.__name__}")
        return value


def build_thesis_tables(project_root: Path) -> ThesisTablesBuildResult:
    root = _project_root(project_root)
    inputs = _load_inputs(root)
    bundle = _project_bundle(inputs)
    _validate_traceability(bundle, inputs)
    bundle_bytes = bundle.canonical_bytes()
    markdown_bytes = render_thesis_tables(bundle).encode()
    states = _write_outputs_once(
        (
            (root / TABLE_BUNDLE, bundle_bytes),
            (root / TABLE_MARKDOWN, markdown_bytes),
        )
    )
    return ThesisTablesBuildResult(
        bundle_state=states[0],
        markdown_state=states[1],
        bundle_sha256=_sha256(bundle_bytes),
        markdown_sha256=_sha256(markdown_bytes),
    )


def check_thesis_tables(project_root: Path) -> tuple[str, ...]:
    root = _project_root(project_root)
    try:
        inputs = _load_inputs(root)
        expected = _project_bundle(inputs)
        _validate_traceability(expected, inputs)
        bundle_data = _read_regular_file(root, TABLE_BUNDLE, "thesis table bundle")
        try:
            actual = ThesisTableBundle.model_validate_json(bundle_data)
        except ValidationError as exc:
            raise ValueError("invalid thesis table bundle") from exc
        if bundle_data != actual.canonical_bytes():
            raise ValueError("thesis table bundle is not canonical JSON")
        _validate_traceability(actual, inputs)
        if actual != expected:
            raise ValueError("thesis table bundle is stale")
        markdown_data = _read_regular_file(root, TABLE_MARKDOWN, "thesis table Markdown")
        if markdown_data != render_thesis_tables(expected).encode():
            raise ValueError("thesis table Markdown is stale")
    except (OSError, TypeError, ValueError) as exc:
        return (f"invalid thesis table artifacts: {exc}",)
    return ()


def render_thesis_tables(bundle: ThesisTableBundle) -> str:
    lines = [
        "# Thesis main-analysis tables",
        "",
        "These tables are a deterministic projection of committed canonical JSON artifacts.",
        "Rebuilding them does not rerun the model, environment, mutation campaign, or reducers.",
        "",
        "## Source artifacts",
        "",
    ]
    for source in bundle.source_artifacts:
        lines.append(
            f"- `{source.artifact.value}`: `{source.path}` "
            f"({source.bytes} bytes, `{source.sha256}`)"
        )
    lines.append("")
    for table in bundle.tables:
        lines.extend((f"## {table.title}", ""))
        for panel in table.panels:
            if len(table.panels) > 1:
                lines.extend((f"### {panel.title}", ""))
            lines.append("| " + " | ".join(column.heading for column in panel.columns) + " |")
            lines.append("|" + "|".join("---" for _ in panel.columns) + "|")
            for row in panel.rows:
                lines.append("| " + " | ".join(_render_cell(cell) for cell in row.cells) + " |")
            lines.append("")
    return "\n".join(lines)


def _load_inputs(project_root: Path) -> _CanonicalInputs:
    artifacts: dict[ArtifactId, _LoadedArtifact] = {}
    for artifact in _SOURCE_ORDER:
        artifacts[artifact] = _load_artifact(
            project_root,
            artifact,
            _SOURCE_PATHS[artifact],
            _MODEL_TYPES[artifact],
        )
    inputs = _CanonicalInputs(artifacts)
    _validate_input_relationships(project_root, inputs)
    return inputs


def _load_artifact(
    project_root: Path,
    artifact: ArtifactId,
    relative: Path,
    model_type: type[BaseModel],
) -> _LoadedArtifact:
    data = _read_regular_file(project_root, relative, artifact.value)
    try:
        model = model_type.model_validate_json(data)
    except ValidationError as exc:
        raise ValueError(f"invalid {artifact.value} artifact") from exc
    canonical = _model_canonical_bytes(model)
    if data != canonical:
        raise ValueError(f"{artifact.value} artifact is not canonical JSON")
    try:
        document = json.loads(data)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{artifact.value} artifact is not valid JSON") from exc
    if not isinstance(document, dict):
        raise ValueError(f"{artifact.value} artifact must be a JSON object")
    return _LoadedArtifact(
        binding=ArtifactBinding(
            artifact=artifact,
            path=relative.as_posix(),
            bytes=len(data),
            sha256=_sha256(data),
        ),
        data=data,
        document=document,
        model=model,
    )


def _validate_input_relationships(project_root: Path, inputs: _CanonicalInputs) -> None:
    protocol = inputs.typed(ArtifactId.PROTOCOL, MainAnalysisProtocol)
    executable = inputs.typed(ArtifactId.EXECUTABLE, MainAnalysisExecutable)
    cohort = inputs.typed(ArtifactId.MINISWE_COHORT, MiniSweCohort)
    historical = inputs.typed(ArtifactId.HISTORICAL_7, HistoricalReport)
    development = inputs.typed(
        ArtifactId.DEVELOPMENT,
        PrefixBenchDevelopmentAnalysisReport,
    )
    campaign = inputs.typed(ArtifactId.TEST_CAMPAIGN, PrefixBenchTestCampaignReport)
    rq2 = inputs.typed(ArtifactId.RQ2, RQ2Report)
    rq3 = inputs.typed(ArtifactId.RQ3, RQ3Report)
    rq4 = inputs.typed(ArtifactId.RQ4, RQ4Report)
    main = inputs.typed(ArtifactId.MAIN, ThesisMainAnalysisReport)
    bound_protocol = load_main_analysis_protocol(project_root)
    bound_executable = load_main_analysis_executable(project_root)

    if protocol != bound_protocol.spec:
        raise ValueError("table protocol differs from the committed protocol")
    if executable != bound_executable.spec:
        raise ValueError("table executable differs from the committed executable")
    _require_binding(
        executable.protocol, inputs.artifact(ArtifactId.PROTOCOL), "executable protocol"
    )
    if executable.cohort != cohort:
        raise ValueError("executable cohort differs from the cohort artifact")
    _require_binding(rq2.source_campaign, inputs.artifact(ArtifactId.TEST_CAMPAIGN), "RQ2 campaign")
    _require_binding(rq3.source_rq2, inputs.artifact(ArtifactId.RQ2), "RQ3 RQ2 source")
    _require_binding(rq4.cohort, inputs.artifact(ArtifactId.MINISWE_COHORT), "RQ4 cohort")
    _require_binding(
        campaign.sources.readiness,
        inputs.artifact(ArtifactId.TEST_READINESS),
        "test campaign readiness",
    )

    main_bindings = {
        ArtifactId.HISTORICAL_7: main.source_bindings.historical_7,
        ArtifactId.DEVELOPMENT: main.source_bindings.development_analysis,
        ArtifactId.RQ2: main.source_bindings.rq2,
        ArtifactId.RQ3: main.source_bindings.rq3,
        ArtifactId.RQ4: main.source_bindings.rq4,
    }
    for artifact, binding in main_bindings.items():
        _require_binding(binding, inputs.artifact(artifact), f"main {artifact.value} source")

    lineages = (rq2.lineage, rq3.lineage, rq4.lineage, main.lineage)
    if any(lineage != main.lineage for lineage in lineages):
        raise ValueError("main-analysis report lineages do not match")
    _validate_lineage(main.lineage, inputs, executable)
    if (
        main.lineage.protocol_commit != bound_protocol.preregistration_commit
        or main.lineage.executable_commit != bound_executable.executable_commit
    ):
        raise ValueError("analysis lineage commits do not match committed artifacts")
    _validate_partition_analyses(rq2, rq3, rq4)
    _require_git_ancestor(
        project_root,
        main.lineage.protocol_commit,
        main.lineage.executable_commit,
        "protocol commit does not precede the executable commit",
    )
    _require_git_ancestor(
        project_root,
        main.lineage.executable_commit,
        campaign.producer.commit,
        "executable commit does not precede the held-out producer",
    )

    if (main.rq2, main.rq3, main.rq4) != (rq2.analysis, rq3.analysis, rq4.analysis):
        raise ValueError("main report analyses differ from the detailed reports")
    if main.rq1.successful_pairs != _historical_successful_pairs(historical):
        raise ValueError("main RQ1 result differs from Historical-7")
    if main.development_context.status != "descriptive_not_evaluated":
        raise ValueError("development evidence escaped its descriptive boundary")
    if development.claims.test_split_generalization != "not_evaluated":
        raise ValueError("development analysis claims test-split generalization")
    if protocol.rq_order != ("RQ1", "RQ2", "RQ3", "RQ4"):
        raise ValueError("main-analysis RQ order has changed")


def _validate_lineage(
    lineage: AnalysisLineage,
    inputs: _CanonicalInputs,
    executable: MainAnalysisExecutable,
) -> None:
    if lineage.protocol_sha256 != inputs.artifact(ArtifactId.PROTOCOL).binding.sha256:
        raise ValueError("analysis lineage protocol digest is stale")
    if lineage.executable_sha256 != inputs.artifact(ArtifactId.EXECUTABLE).binding.sha256:
        raise ValueError("analysis lineage executable digest is stale")
    if lineage.protocol_commit != executable.protocol_commit:
        raise ValueError("analysis lineage protocol commit is stale")


def _validate_partition_analyses(
    rq2: RQ2Report,
    rq3: RQ3Report,
    rq4: RQ4Report,
) -> None:
    rebuilt = (
        analyze_rq2(rq2.tasks),
        analyze_rq3(rq3.comparisons),
        analyze_rq4(rq4.tasks),
    )
    if rebuilt != (rq2.analysis, rq3.analysis, rq4.analysis):
        raise ValueError("detailed report analysis does not match its partition rows")


def _project_bundle(inputs: _CanonicalInputs) -> ThesisTableBundle:
    tables = (
        _rq1_table(inputs),
        _rq2_table(inputs),
        _rq3_table(inputs),
        _rq4_table(inputs),
        _historical_partition(inputs),
        _rq2_partition(inputs),
        _rq3_partition(inputs),
        _rq4_partition(inputs),
        _deviation_table(inputs),
        _source_binding_table(inputs),
        _chronology_table(inputs),
        _publication_table(inputs),
    )
    return ThesisTableBundle(
        source_artifacts=tuple(inputs.artifact(item).binding for item in _SOURCE_ORDER),
        tables=tables,
        reproducibility_index=_reproducibility_index(tables),
    )


def _rq1_table(inputs: _CanonicalInputs) -> ThesisTable:
    main_source = ArtifactId.MAIN
    summary_rows = tuple(
        _row(
            key,
            _fixed(label, main_source, pointer),
            _copy(inputs, main_source, pointer),
        )
        for key, label, pointer in (
            ("evidence_role", "evidence_role", "/rq1/evidence_role"),
            ("case_pairs", "case_pairs", "/rq1/case_pairs"),
            ("successful_pairs", "successful_pairs", "/rq1/successful_pairs"),
            ("disposition", "disposition", "/rq1/disposition"),
            ("inferential_test", "inferential_test", "/rq1/inferential_test"),
        )
    )
    threshold = _row(
        "threshold",
        _fixed("threshold", ArtifactId.PROTOCOL, "/rq1/engineering_threshold"),
        _copy(inputs, ArtifactId.PROTOCOL, "/rq1/engineering_threshold"),
    )
    historical_rows = (
        _row(
            "vulnerable-reproduction-rate",
            _fixed(
                "vulnerable_reproduction_rate",
                ArtifactId.HISTORICAL_7,
                "/results",
            ),
            _historical_metric(
                inputs,
                CellDerivation.HISTORICAL_VULNERABLE_RATE,
            ),
        ),
        _row(
            "fixed-confirmation-rate",
            _fixed(
                "fixed_confirmation_rate",
                ArtifactId.HISTORICAL_7,
                "/results",
            ),
            _historical_metric(
                inputs,
                CellDerivation.HISTORICAL_FIXED_RATE,
            ),
        ),
        _row(
            "paired-success-rate",
            _fixed(
                "paired_success_rate",
                ArtifactId.HISTORICAL_7,
                "/results",
            ),
            _historical_metric(
                inputs,
                CellDerivation.HISTORICAL_PAIRED_RATE,
            ),
        ),
        _row(
            "inconclusive-count",
            _fixed(
                "inconclusive_count",
                ArtifactId.HISTORICAL_7,
                "/results",
            ),
            _historical_metric(
                inputs,
                CellDerivation.HISTORICAL_INCONCLUSIVE_COUNT,
            ),
        ),
        _row(
            "error-count",
            _fixed("error_count", ArtifactId.HISTORICAL_7, "/results"),
            _historical_metric(
                inputs,
                CellDerivation.HISTORICAL_ERROR_COUNT,
            ),
        ),
        _row(
            "byte-identical-rebuild",
            _fixed(
                "byte_identical_rebuild",
                ArtifactId.HISTORICAL_7,
                "",
            ),
            _missing(
                inputs,
                ArtifactId.HISTORICAL_7,
                "",
                "byte_identical_rebuild",
            ),
        ),
    )
    return _table(
        "rq1",
        "RQ1 Historical-7 replay",
        "body",
        _panel(
            "result",
            "Result",
            ("Metric", "Value"),
            *summary_rows[:3],
            threshold,
            *historical_rows,
            *summary_rows[3:],
        ),
    )


def _rq2_table(inputs: _CanonicalInputs) -> ThesisTable:
    source = ArtifactId.RQ2
    analysis = _object_at(inputs, source, "/analysis")
    methods = _array_member(analysis, "methods")
    method_rows = []
    for index, _method in enumerate(methods):
        base = f"/analysis/methods/{index}"
        method_rows.append(
            _row(
                f"method-{index + 1}",
                _copy(inputs, source, f"{base}/method"),
                _copy(inputs, source, f"{base}/tasks"),
                _copy(inputs, source, f"{base}/scheduled_slots"),
                _copy(inputs, source, f"{base}/outcomes/target_violation"),
                _copy(inputs, source, f"{base}/tasks_with_target_violation"),
                _exact(inputs, source, f"{base}/input_acceptance_rate/decimal"),
                _exact(inputs, source, f"{base}/target_reach_rate/decimal"),
                _exact(inputs, source, f"{base}/target_violation_rate/decimal"),
                _exact(inputs, source, f"{base}/oracle_equivalent_rate/decimal"),
                _exact(inputs, source, f"{base}/other_oracle_change_rate/decimal"),
                _copy(inputs, source, f"{base}/distinct_target_invariants"),
                _exact(
                    inputs,
                    source,
                    f"{base}/target_violations_per_100_slots/decimal",
                ),
            )
        )
    contrasts = _array_member(analysis, "contrasts")
    contrast_rows = []
    for index, _contrast in enumerate(contrasts):
        base = f"/analysis/contrasts/{index}"
        contrast_rows.append(
            _row(
                f"contrast-{index + 1}",
                _copy(inputs, source, f"{base}/comparator"),
                _copy(inputs, source, f"{base}/mcnemar/tasks"),
                _exact(inputs, source, f"{base}/mcnemar/risk_difference/decimal"),
                _interval(
                    inputs,
                    source,
                    f"{base}/risk_difference_interval/lower/decimal",
                    f"{base}/risk_difference_interval/upper/decimal",
                ),
                _exact(inputs, source, f"{base}/mcnemar/p_value/decimal"),
                _exact(inputs, source, f"{base}/holm_adjusted_p_value/decimal"),
                _copy(inputs, source, f"{base}/disposition"),
            )
        )
    disposition = _row(
        "overall-disposition",
        _fixed("disposition", source, "/analysis/disposition"),
        _copy(inputs, source, "/analysis/disposition"),
    )
    return _table(
        "rq2",
        "RQ2 method comparison",
        "body",
        _panel(
            "methods",
            "Method summaries",
            (
                "Method",
                "Tasks",
                "Slots",
                "Target violations",
                "Tasks detected",
                "Input acceptance",
                "Target reach",
                "Violation rate",
                "Oracle equivalent",
                "Other oracle change",
                "Distinct invariants",
                "Violations per 100 slots",
            ),
            *method_rows,
        ),
        _panel(
            "contrasts",
            "Pre-registered contrasts",
            (
                "Comparator",
                "Tasks",
                "Risk difference",
                "95% interval",
                "Raw p",
                "Holm p",
                "Disposition",
            ),
            *contrast_rows,
        ),
        _panel("decision", "Decision", ("Metric", "Value"), disposition),
    )


def _rq3_table(inputs: _CanonicalInputs) -> ThesisTable:
    source = ArtifactId.RQ3
    analysis = _object_at(inputs, source, "/analysis")
    reducers = _array_member(analysis, "reducers")
    reducer_rows = []
    for index, _reducer in enumerate(reducers):
        base = f"/analysis/reducers/{index}"
        reducer_rows.append(
            _row(
                f"reducer-{index + 1}",
                _copy(inputs, source, f"{base}/reducer"),
                _copy(inputs, source, f"{base}/cases"),
                _copy(inputs, source, f"{base}/tasks"),
                _copy(inputs, source, f"{base}/before_bytes"),
                _copy(inputs, source, f"{base}/after_bytes"),
                _exact(inputs, source, f"{base}/pooled_retained_fraction/decimal"),
                _copy(inputs, source, f"{base}/failed_retained"),
                _copy(inputs, source, f"{base}/candidates_evaluated"),
                _copy(inputs, source, f"{base}/audits_executed"),
                _copy(inputs, source, f"{base}/accepted_reductions"),
            )
        )
    if not reducer_rows:
        reducer_rows.append(
            _row(
                "insufficient",
                _fixed("not_reported_insufficient", source, "/analysis/status"),
                _copy(inputs, source, "/analysis/cases"),
                _copy(inputs, source, "/analysis/tasks"),
                _fixed("not_reported_insufficient", source, "/analysis/status"),
                _fixed("not_reported_insufficient", source, "/analysis/status"),
                _fixed("not_reported_insufficient", source, "/analysis/status"),
                _fixed("not_reported_insufficient", source, "/analysis/status"),
                _fixed("not_reported_insufficient", source, "/analysis/status"),
                _fixed("not_reported_insufficient", source, "/analysis/status"),
                _fixed("not_reported_insufficient", source, "/analysis/status"),
            )
        )
    contrasts = _array_member(analysis, "contrasts")
    contrast_rows = []
    for index, _contrast in enumerate(contrasts):
        base = f"/analysis/contrasts/{index}"
        contrast_rows.append(
            _row(
                f"contrast-{index + 1}",
                _copy(inputs, source, f"{base}/comparator"),
                _copy(inputs, source, f"{base}/wilcoxon/pairs"),
                _exact(inputs, source, f"{base}/wilcoxon/rank_biserial/decimal"),
                _interval(
                    inputs,
                    source,
                    f"{base}/retained_difference_interval/lower/decimal",
                    f"{base}/retained_difference_interval/upper/decimal",
                ),
                _exact(inputs, source, f"{base}/wilcoxon/p_value/decimal"),
                _exact(inputs, source, f"{base}/holm_adjusted_p_value/decimal"),
                _copy(inputs, source, f"{base}/disposition"),
            )
        )
    if not contrast_rows:
        contrast_rows.append(
            _row(
                "insufficient",
                _fixed("not_reported_insufficient", source, "/analysis/status"),
                _fixed("not_reported_insufficient", source, "/analysis/status"),
                _fixed("not_reported_insufficient", source, "/analysis/status"),
                _fixed("not_reported_insufficient", source, "/analysis/status"),
                _fixed("not_reported_insufficient", source, "/analysis/status"),
                _fixed("not_reported_insufficient", source, "/analysis/status"),
                _copy(inputs, source, "/analysis/status"),
            )
        )
    decision = _row(
        "decision",
        _fixed("analysis_status", source, "/analysis/status"),
        _copy(inputs, source, "/analysis/status"),
        _fixed("disposition", source, "/analysis/disposition"),
        _copy(inputs, source, "/analysis/disposition"),
    )
    return _table(
        "rq3",
        "RQ3 reducer comparison",
        "body",
        _panel(
            "reducers",
            "Reducer summaries",
            (
                "Reducer",
                "Cases",
                "Tasks",
                "Before bytes",
                "After bytes",
                "Retained fraction",
                "Failed retained",
                "Candidates evaluated",
                "Audits executed",
                "Accepted reductions",
            ),
            *reducer_rows,
        ),
        _panel(
            "contrasts",
            "Pre-registered contrasts",
            (
                "Comparator",
                "Pairs",
                "Rank-biserial",
                "95% interval",
                "Raw p",
                "Holm p",
                "Disposition",
            ),
            *contrast_rows,
        ),
        _panel("decision", "Decision", ("Metric", "Value", "Metric 2", "Value 2"), decision),
    )


def _rq4_table(inputs: _CanonicalInputs) -> ThesisTable:
    source = ArtifactId.RQ4
    families = _array_at(inputs, source, "/analysis/families")
    rows = []
    for index, _family in enumerate(families):
        base = f"/analysis/families/{index}"
        rows.append(
            _row(
                f"family-{index + 1}",
                _copy(inputs, source, f"{base}/family"),
                _copy(inputs, source, f"{base}/mapping"),
                _copy(inputs, source, f"{base}/scheduled"),
                _copy(inputs, source, f"{base}/executable"),
                _copy(inputs, source, f"{base}/adapter_rejected"),
                _copy(inputs, source, f"{base}/preexisting_violation"),
                _copy(inputs, source, f"{base}/not_applicable"),
                _copy(inputs, source, f"{base}/applicable"),
                _copy(inputs, source, f"{base}/target_violation"),
                _copy(inputs, source, f"{base}/other_oracle_change"),
                _copy(inputs, source, f"{base}/deterministic_replay"),
                _copy(inputs, source, f"{base}/disposition"),
            )
        )
    decision = _row(
        "decision",
        _fixed("collection_complete", source, "/analysis/collection_complete"),
        _copy(inputs, source, "/analysis/collection_complete"),
        _fixed("disposition", source, "/analysis/disposition"),
        _copy(inputs, source, "/analysis/disposition"),
    )
    return _table(
        "rq4",
        "RQ4 cross-harness transfer",
        "body",
        _panel(
            "families",
            "Family summaries",
            (
                "Family",
                "Mapping",
                "Scheduled",
                "Executable",
                "Adapter rejected",
                "Preexisting violation",
                "Not applicable",
                "Applicable",
                "Target violations",
                "Other changes",
                "Deterministic replay",
                "Disposition",
            ),
            *rows,
        ),
        _panel("decision", "Decision", ("Metric", "Value", "Metric 2", "Value 2"), decision),
    )


def _historical_partition(inputs: _CanonicalInputs) -> ThesisTable:
    source = ArtifactId.HISTORICAL_7
    results = _array_at(inputs, source, "/results")
    rows = []
    for index, value in enumerate(results):
        result = _object(value, "Historical-7 result")
        base = f"/results/{index}"
        observed_pointer = (
            f"{base}/observed_outcome" if "observed_outcome" in result else f"{base}/kind"
        )
        matched_pointer = (
            f"{base}/matches_expected" if "matches_expected" in result else f"{base}/kind"
        )
        rows.append(
            _row(
                f"cell-{index + 1}",
                _copy(inputs, source, f"{base}/case_id"),
                _copy(inputs, source, f"{base}/revision_role"),
                _copy(inputs, source, f"{base}/expected_outcome"),
                _copy(inputs, source, observed_pointer),
                _copy(inputs, source, matched_pointer),
                _copy(inputs, source, f"{base}/commit"),
            )
        )
    return _table(
        "historical_partition",
        "Appendix: Historical-7 cells",
        "appendix",
        _panel(
            "cells",
            "Historical cells",
            ("Case", "Revision", "Expected", "Observed", "Matched", "Commit"),
            *rows,
        ),
    )


def _rq2_partition(inputs: _CanonicalInputs) -> ThesisTable:
    source = ArtifactId.RQ2
    tasks = _array_at(inputs, source, "/tasks")
    rows = []
    for task_index, task_value in enumerate(tasks):
        task = _object(task_value, "RQ2 task")
        methods = _array_member(task, "methods")
        for method_index, method_value in enumerate(methods):
            method = _object(method_value, "RQ2 method")
            outcomes = _array_member(method, "outcomes")
            for slot_index, _outcome in enumerate(outcomes):
                task_base = f"/tasks/{task_index}"
                method_base = f"{task_base}/methods/{method_index}"
                outcome_base = f"{method_base}/outcomes/{slot_index}"
                slot_base = f"{outcome_base}/slot"
                rows.append(
                    _row(
                        f"task-{task_index + 1}-method-{method_index + 1}-slot-{slot_index + 1}",
                        _copy(inputs, source, f"{task_base}/task_name"),
                        _copy(inputs, source, f"{method_base}/method"),
                        _copy(inputs, source, f"{slot_base}/slot_ordinal"),
                        _copy(inputs, source, f"{slot_base}/attempt_ordinal"),
                        _copy(inputs, source, f"{slot_base}/operator"),
                        _copy(inputs, source, f"{slot_base}/target_invariant"),
                        _copy(inputs, source, f"{slot_base}/synthetic_attempt"),
                        _copy(inputs, source, f"{outcome_base}/outcome"),
                    )
                )
    return _table(
        "rq2_partition",
        "Appendix: RQ2 outcome partition",
        "appendix",
        _panel(
            "slots",
            "Every scheduled method slot",
            (
                "Task",
                "Method",
                "Slot",
                "Attempt",
                "Operator",
                "Invariant",
                "Synthetic",
                "Outcome",
            ),
            *rows,
        ),
    )


def _rq3_partition(inputs: _CanonicalInputs) -> ThesisTable:
    source = ArtifactId.RQ3
    comparisons = _array_at(inputs, source, "/comparisons")
    rows = []
    for comparison_index, comparison_value in enumerate(comparisons):
        comparison = _object(comparison_value, "RQ3 comparison")
        results = _array_member(comparison, "results")
        for reducer_index, _result in enumerate(results):
            comparison_base = f"/comparisons/{comparison_index}"
            result_base = f"{comparison_base}/results/{reducer_index}"
            rows.append(
                _row(
                    f"case-{comparison_index + 1}-reducer-{reducer_index + 1}",
                    _copy(inputs, source, f"{comparison_base}/task_identity_sha256"),
                    _copy(inputs, source, f"{comparison_base}/case_ordinal"),
                    _copy(inputs, source, f"{result_base}/reducer"),
                    _copy(inputs, source, f"{result_base}/status"),
                    _copy(
                        inputs,
                        source,
                        f"{result_base}/metrics/before/canonical_json_bytes",
                    ),
                    _copy(
                        inputs,
                        source,
                        f"{result_base}/metrics/after/canonical_json_bytes",
                    ),
                    _copy(
                        inputs,
                        source,
                        f"{result_base}/metrics/before/event_count",
                    ),
                    _copy(
                        inputs,
                        source,
                        f"{result_base}/metrics/after/event_count",
                    ),
                    _copy(
                        inputs,
                        source,
                        f"{result_base}/metrics/before/payload_member_count",
                    ),
                    _copy(
                        inputs,
                        source,
                        f"{result_base}/metrics/after/payload_member_count",
                    ),
                    _copy(
                        inputs,
                        source,
                        f"{result_base}/metrics/candidates_evaluated",
                    ),
                    _copy(
                        inputs,
                        source,
                        f"{result_base}/metrics/audits_executed",
                    ),
                    _copy(
                        inputs,
                        source,
                        f"{result_base}/metrics/accepted_reductions",
                    ),
                    _copy(
                        inputs,
                        source,
                        f"{result_base}/metrics/rejections/malformed",
                    ),
                    _copy(
                        inputs,
                        source,
                        f"{result_base}/metrics/rejections/not_applicable",
                    ),
                    _copy(
                        inputs,
                        source,
                        f"{result_base}/metrics/rejections/no_violation",
                    ),
                    _copy(
                        inputs,
                        source,
                        f"{result_base}/metrics/rejections/cause_changed",
                    ),
                    _copy(
                        inputs,
                        source,
                        f"{result_base}/metrics/rejections/nondeterministic",
                    ),
                    _copy(inputs, source, f"{result_base}/failure_reason"),
                )
            )
    if not rows:
        rows.append(
            _row(
                "empty-population",
                _fixed("not_reported_insufficient", source, "/analysis/status"),
                _fixed("not_reported_insufficient", source, "/analysis/status"),
                _fixed("not_reported_insufficient", source, "/analysis/status"),
                _copy(inputs, source, "/analysis/status"),
                _fixed("not_reported_insufficient", source, "/comparisons"),
                _fixed("not_reported_insufficient", source, "/comparisons"),
                _fixed("not_reported_insufficient", source, "/comparisons"),
                _fixed("not_reported_insufficient", source, "/comparisons"),
                _fixed("not_reported_insufficient", source, "/comparisons"),
                _fixed("not_reported_insufficient", source, "/comparisons"),
                _fixed("not_reported_insufficient", source, "/comparisons"),
                _fixed("not_reported_insufficient", source, "/comparisons"),
                _fixed("not_reported_insufficient", source, "/comparisons"),
                _fixed("not_reported_insufficient", source, "/comparisons"),
                _fixed("not_reported_insufficient", source, "/comparisons"),
                _fixed("not_reported_insufficient", source, "/comparisons"),
                _fixed("not_reported_insufficient", source, "/comparisons"),
                _fixed("not_reported_insufficient", source, "/comparisons"),
                _fixed("not_reported_insufficient", source, "/comparisons"),
            )
        )
    return _table(
        "rq3_partition",
        "Appendix: RQ3 reducer outcomes",
        "appendix",
        _panel(
            "cases",
            "Every reducer result",
            (
                "Task identity",
                "Case",
                "Reducer",
                "Status",
                "Before bytes",
                "After bytes",
                "Before events",
                "After events",
                "Before payload members",
                "After payload members",
                "Candidates evaluated",
                "Audits executed",
                "Accepted reductions",
                "Rejected malformed",
                "Rejected not applicable",
                "Rejected no violation",
                "Rejected cause changed",
                "Rejected nondeterministic",
                "Failure reason",
            ),
            *rows,
        ),
    )


def _rq4_partition(inputs: _CanonicalInputs) -> ThesisTable:
    source = ArtifactId.RQ4
    tasks = _array_at(inputs, source, "/tasks")
    rows = []
    for task_index, task_value in enumerate(tasks):
        task = _object(task_value, "RQ4 task")
        families = _array_member(task, "families")
        for family_index, _family in enumerate(families):
            task_base = f"/tasks/{task_index}"
            family_base = f"{task_base}/families/{family_index}"
            rows.append(
                _row(
                    f"task-{task_index + 1}-family-{family_index + 1}",
                    _copy(inputs, source, f"{task_base}/index"),
                    _copy(inputs, source, f"{task_base}/instance_id"),
                    _copy(inputs, source, f"{family_base}/family"),
                    _copy(inputs, source, f"{family_base}/status"),
                    _copy(inputs, source, f"{family_base}/deterministic_replay"),
                )
            )
    return _table(
        "rq4_partition",
        "Appendix: RQ4 task-family outcomes",
        "appendix",
        _panel(
            "task_families",
            "Every task-family result",
            ("Index", "Instance", "Family", "Status", "Deterministic replay"),
            *rows,
        ),
    )


def _deviation_table(inputs: _CanonicalInputs) -> ThesisTable:
    source = ArtifactId.MAIN
    deviations = _array_at(inputs, source, "/deviations")
    rows = [
        _row(
            f"deviation-{index + 1}",
            _copy(inputs, source, f"/deviations/{index}"),
        )
        for index, _deviation in enumerate(deviations)
    ]
    if not rows:
        rows.append(
            _row(
                "none-recorded",
                _fixed("none_recorded", source, "/deviations"),
            )
        )
    return _table(
        "protocol_deviations",
        "Appendix: Protocol deviations",
        "appendix",
        _panel("deviations", "Recorded deviations", ("Deviation",), *rows),
    )


def _source_binding_table(inputs: _CanonicalInputs) -> ThesisTable:
    entries = (
        ("historical_7", ArtifactId.MAIN, "/source_bindings/historical_7"),
        ("development_analysis", ArtifactId.MAIN, "/source_bindings/development_analysis"),
        ("rq2", ArtifactId.MAIN, "/source_bindings/rq2"),
        ("rq3", ArtifactId.MAIN, "/source_bindings/rq3"),
        ("rq4", ArtifactId.MAIN, "/source_bindings/rq4"),
        ("rq2_source_campaign", ArtifactId.RQ2, "/source_campaign"),
        ("rq3_source_rq2", ArtifactId.RQ3, "/source_rq2"),
        ("rq4_source_cohort", ArtifactId.RQ4, "/cohort"),
        ("test_campaign_matrix", ArtifactId.TEST_CAMPAIGN, "/sources/matrix"),
        ("test_campaign_canonical", ArtifactId.TEST_CAMPAIGN, "/sources/canonical"),
        ("test_campaign_readiness", ArtifactId.TEST_CAMPAIGN, "/sources/readiness"),
        (
            "test_campaign_run_config",
            ArtifactId.TEST_CAMPAIGN,
            "/sources/collection/run_config",
        ),
        ("test_campaign_progress", ArtifactId.TEST_CAMPAIGN, "/sources/collection/progress"),
        ("protocol", ArtifactId.EXECUTABLE, "/protocol"),
    )
    rows = tuple(
        _row(
            f"binding-{index + 1}",
            _fixed(label, source, base),
            _copy(inputs, source, f"{base}/path"),
            _copy(inputs, source, f"{base}/bytes"),
            _copy(inputs, source, f"{base}/sha256"),
        )
        for index, (label, source, base) in enumerate(entries)
    )
    return _table(
        "source_bindings",
        "Appendix: Source bindings",
        "appendix",
        _panel("bindings", "Bound artifacts", ("Role", "Path", "Bytes", "SHA-256"), *rows),
    )


def _chronology_table(inputs: _CanonicalInputs) -> ThesisTable:
    rows = (
        _row(
            "design-base",
            _fixed("design_base", ArtifactId.PROTOCOL, "/chronology/design_base_commit"),
            _copy(inputs, ArtifactId.PROTOCOL, "/chronology/design_base_commit"),
        ),
        _row(
            "test-matrix",
            _fixed("test_matrix", ArtifactId.PROTOCOL, "/chronology/test_matrix_commit"),
            _copy(inputs, ArtifactId.PROTOCOL, "/chronology/test_matrix_commit"),
        ),
        _row(
            "test-protocol",
            _fixed("test_protocol", ArtifactId.PROTOCOL, "/chronology/test_protocol_commit"),
            _copy(inputs, ArtifactId.PROTOCOL, "/chronology/test_protocol_commit"),
        ),
        _row(
            "main-protocol",
            _fixed("protocol", ArtifactId.MAIN, "/lineage/protocol_commit"),
            _copy(inputs, ArtifactId.MAIN, "/lineage/protocol_commit"),
        ),
        _row(
            "executable",
            _fixed("executable", ArtifactId.MAIN, "/lineage/executable_commit"),
            _copy(inputs, ArtifactId.MAIN, "/lineage/executable_commit"),
        ),
        _row(
            "collection-producer",
            _fixed("collection_producer", ArtifactId.TEST_CAMPAIGN, "/producer/commit"),
            _copy(inputs, ArtifactId.TEST_CAMPAIGN, "/producer/commit"),
        ),
        _row(
            "outcome-report-producer",
            _fixed("outcome_report_producer", ArtifactId.RQ2, ""),
            _missing(inputs, ArtifactId.RQ2, "", "producer_commit"),
        ),
    )
    return _table(
        "git_chronology",
        "Appendix: Git chronology",
        "appendix",
        _panel("commits", "Recorded commits", ("Role", "Commit"), *rows),
    )


def _publication_table(inputs: _CanonicalInputs) -> ThesisTable:
    required = {
        "protocol",
        "executable_protocol",
        "miniswe_cohort",
        "test_canonical",
        "test_readiness",
        "test_campaign",
        "rq2_report",
        "rq3_report",
        "rq4_report",
        "main_report",
    }
    path_keys = (
        "protocol",
        "executable_protocol",
        "miniswe_cohort",
        "test_run_root",
        "test_canonical",
        "test_readiness",
        "test_campaign",
        "rq2_report",
        "rq3_report",
        "rq4_report",
        "main_report",
        "tb21_report",
        "auxiliary_report",
    )
    rows = []
    for index, key in enumerate(path_keys):
        pointer = f"/paths/{key}"
        status = (
            "required_repository_artifact"
            if key in required
            else "publication_decision_not_recorded"
        )
        rows.append(
            _row(
                f"artifact-{index + 1}",
                _copy(inputs, ArtifactId.PROTOCOL, pointer),
                _fixed(status, ArtifactId.PROTOCOL, pointer),
            )
        )
    credential_pointer = "/protocol/spec/provider_credentials"
    rows.append(
        _row(
            "provider-credentials",
            _copy(inputs, ArtifactId.TEST_CAMPAIGN, credential_pointer),
            _fixed(
                "excluded_not_an_artifact",
                ArtifactId.TEST_CAMPAIGN,
                credential_pointer,
            ),
        )
    )
    return _table(
        "publication_inventory",
        "Appendix: Publication inventory",
        "appendix",
        _panel("artifacts", "Artifact decisions", ("Artifact", "Status"), *rows),
    )


def _validate_traceability(bundle: ThesisTableBundle, inputs: _CanonicalInputs) -> None:
    expected_bindings = tuple(inputs.artifact(item).binding for item in _SOURCE_ORDER)
    if bundle.source_artifacts != expected_bindings:
        raise ValueError("table source bindings are stale")
    for table in bundle.tables:
        for panel in table.panels:
            for row in panel.rows:
                for cell in row.cells:
                    _validate_cell(cell, inputs)


def _validate_cell(cell: EvidenceCell, inputs: _CanonicalInputs) -> None:
    values = tuple(
        _resolve_pointer(inputs.document(source.artifact), source.pointer)
        for source in cell.sources
    )
    if cell.derivation is CellDerivation.COPY:
        if len(values) != 1 or cell.text != _scalar_text(values[0]):
            raise ValueError("copy cell does not match its source")
        return
    if cell.derivation is CellDerivation.EXACT_DECIMAL_COPY:
        if (
            len(values) != 1
            or not isinstance(values[0], str)
            or _DECIMAL.fullmatch(values[0]) is None
            or cell.text != values[0]
        ):
            raise ValueError("exact-decimal cell does not match its source")
        return
    if cell.derivation is CellDerivation.JOIN_EXACT_DECIMAL_BOUNDS:
        if (
            len(values) != 2
            or any(not isinstance(value, str) for value in values)
            or any(_DECIMAL.fullmatch(str(value)) is None for value in values)
            or cell.text != f"[{values[0]}, {values[1]}]"
        ):
            raise ValueError("interval cell does not match its sources")
        return
    if cell.derivation is CellDerivation.JOIN_INTEGER_RATIO:
        if (
            len(values) != 2
            or any(not isinstance(value, int) or isinstance(value, bool) for value in values)
            or cell.text != f"{values[0]}/{values[1]}"
        ):
            raise ValueError("integer-ratio cell does not match its sources")
        return
    historical_derivations = {
        CellDerivation.HISTORICAL_VULNERABLE_RATE,
        CellDerivation.HISTORICAL_FIXED_RATE,
        CellDerivation.HISTORICAL_PAIRED_RATE,
        CellDerivation.HISTORICAL_INCONCLUSIVE_COUNT,
        CellDerivation.HISTORICAL_ERROR_COUNT,
    }
    if cell.derivation in historical_derivations:
        if len(values) != 1 or not isinstance(values[0], list):
            raise ValueError("historical metric must reference the result array")
        if cell.text != _historical_metric_text(values[0], cell.derivation):
            raise ValueError("historical metric does not match its result rows")
        return
    if cell.derivation is CellDerivation.FIXED_LABEL:
        if cell.text not in _FIXED_LABELS:
            raise ValueError(f"unregistered fixed label: {cell.text}")
        return
    if len(values) != 1 or not isinstance(values[0], dict):
        raise ValueError("missing-member cell must reference an object")
    if cell.missing_member in values[0]:
        raise ValueError("missing-member cell references an existing member")
    if cell.text != "unavailable_in_canonical_artifacts":
        raise ValueError("missing-member cell has an invalid display value")


def _copy(
    inputs: _CanonicalInputs,
    artifact: ArtifactId,
    pointer: str,
) -> EvidenceCell:
    value = _resolve_pointer(inputs.document(artifact), pointer)
    return EvidenceCell(
        text=_scalar_text(value),
        sources=(SourceRef(artifact=artifact, pointer=pointer),),
        derivation=CellDerivation.COPY,
    )


def _exact(
    inputs: _CanonicalInputs,
    artifact: ArtifactId,
    pointer: str,
) -> EvidenceCell:
    value = _resolve_pointer(inputs.document(artifact), pointer)
    if not isinstance(value, str) or _DECIMAL.fullmatch(value) is None:
        raise ValueError(f"exact decimal source is invalid: {artifact.value}#{pointer}")
    return EvidenceCell(
        text=value,
        sources=(SourceRef(artifact=artifact, pointer=pointer),),
        derivation=CellDerivation.EXACT_DECIMAL_COPY,
    )


def _interval(
    inputs: _CanonicalInputs,
    artifact: ArtifactId,
    lower: str,
    upper: str,
) -> EvidenceCell:
    low = _resolve_pointer(inputs.document(artifact), lower)
    high = _resolve_pointer(inputs.document(artifact), upper)
    if not isinstance(low, str) or not isinstance(high, str):
        raise ValueError("interval bounds must be strings")
    return EvidenceCell(
        text=f"[{low}, {high}]",
        sources=(
            SourceRef(artifact=artifact, pointer=lower),
            SourceRef(artifact=artifact, pointer=upper),
        ),
        derivation=CellDerivation.JOIN_EXACT_DECIMAL_BOUNDS,
    )


def _fixed(
    text: str,
    artifact: ArtifactId,
    pointer: str,
) -> EvidenceCell:
    if text not in _FIXED_LABELS:
        raise ValueError(f"unregistered fixed label: {text}")
    return EvidenceCell(
        text=text,
        sources=(SourceRef(artifact=artifact, pointer=pointer),),
        derivation=CellDerivation.FIXED_LABEL,
    )


def _missing(
    inputs: _CanonicalInputs,
    artifact: ArtifactId,
    pointer: str,
    member: str,
) -> EvidenceCell:
    value = _resolve_pointer(inputs.document(artifact), pointer)
    if not isinstance(value, dict) or member in value:
        raise ValueError("missing-member source does not prove absence")
    return EvidenceCell(
        text="unavailable_in_canonical_artifacts",
        sources=(SourceRef(artifact=artifact, pointer=pointer),),
        derivation=CellDerivation.MISSING_MEMBER,
        missing_member=member,
    )


def _historical_metric(
    inputs: _CanonicalInputs,
    derivation: CellDerivation,
) -> EvidenceCell:
    results = _array_at(inputs, ArtifactId.HISTORICAL_7, "/results")
    return EvidenceCell(
        text=_historical_metric_text(results, derivation),
        sources=(SourceRef(artifact=ArtifactId.HISTORICAL_7, pointer="/results"),),
        derivation=derivation,
    )


def _historical_metric_text(
    values: list[Any],
    derivation: CellDerivation,
) -> str:
    results = tuple(_object(value, "Historical-7 result") for value in values)
    vulnerable = tuple(row for row in results if row.get("revision_role") == "vulnerable")
    fixed = tuple(row for row in results if row.get("revision_role") == "fixed")
    by_case: dict[str, dict[str, dict[str, Any]]] = {}
    for row in results:
        case_id = row.get("case_id")
        role = row.get("revision_role")
        if not isinstance(case_id, str) or not isinstance(role, str):
            raise ValueError("Historical-7 result identity is invalid")
        by_case.setdefault(case_id, {})[role] = row

    if derivation is CellDerivation.HISTORICAL_VULNERABLE_RATE:
        matched = sum(
            row.get("kind") == "completed" and row.get("observed_outcome") == "survived"
            for row in vulnerable
        )
        return f"{matched}/{len(vulnerable)}"
    if derivation is CellDerivation.HISTORICAL_FIXED_RATE:
        matched = sum(
            row.get("kind") == "completed" and row.get("observed_outcome") == "killed"
            for row in fixed
        )
        return f"{matched}/{len(fixed)}"
    if derivation is CellDerivation.HISTORICAL_PAIRED_RATE:
        matched = 0
        for revisions in by_case.values():
            vulnerable_row = revisions.get("vulnerable", {})
            fixed_row = revisions.get("fixed", {})
            matched += (
                vulnerable_row.get("kind") == "completed"
                and vulnerable_row.get("observed_outcome") == "survived"
                and fixed_row.get("kind") == "completed"
                and fixed_row.get("observed_outcome") == "killed"
            )
        return f"{matched}/{len(by_case)}"
    if derivation is CellDerivation.HISTORICAL_INCONCLUSIVE_COUNT:
        return str(
            sum(
                row.get("kind") == "completed" and row.get("observed_outcome") == "inconclusive"
                for row in results
            )
        )
    if derivation is CellDerivation.HISTORICAL_ERROR_COUNT:
        return str(sum(row.get("kind") == "error" for row in results))
    raise ValueError("unsupported historical metric derivation")


def _row(row_id: str, *cells: EvidenceCell) -> TableRow:
    return TableRow(row_id=row_id, cells=cells)


def _panel(
    panel_id: str,
    title: str,
    headings: tuple[str, ...],
    *rows: TableRow,
) -> TablePanel:
    return TablePanel(
        panel_id=panel_id,
        title=title,
        columns=tuple(
            TableColumn(
                key=_column_key(heading),
                heading=heading,
            )
            for heading in headings
        ),
        rows=rows,
    )


def _column_key(heading: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "_", heading.lower()).strip("_")
    return key if not key[:1].isdigit() else f"value_{key}"


def _table(
    table_id: str,
    title: str,
    section: Literal["body", "appendix"],
    *panels: TablePanel,
) -> ThesisTable:
    return ThesisTable(table_id=table_id, title=title, section=section, panels=panels)


def _reproducibility_index(
    tables: tuple[ThesisTable, ...],
) -> tuple[ReproducibilityEntry, ...]:
    entries = []
    for table_index, table in enumerate(tables):
        for panel_index, panel in enumerate(table.panels):
            for row_index, row in enumerate(panel.rows):
                for cell_index, cell in enumerate(row.cells):
                    entries.append(
                        ReproducibilityEntry(
                            cell_pointer=(
                                f"/tables/{table_index}/panels/{panel_index}/rows/"
                                f"{row_index}/cells/{cell_index}"
                            ),
                            sources=cell.sources,
                        )
                    )
    return tuple(entries)


def _render_cell(cell: EvidenceCell) -> str:
    text = _markdown_cell(cell.text)
    refs = ", ".join(f"{source.artifact.value}#{source.pointer or '/'}" for source in cell.sources)
    return f"{text}<br><sub>`{_markdown_cell(refs)}`</sub>"


def _markdown_cell(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")


def _resolve_pointer(document: Any, pointer: str) -> Any:
    current = document
    for token in _pointer_tokens(pointer):
        if isinstance(current, dict):
            if token not in current:
                raise ValueError(f"JSON pointer member does not exist: {pointer}")
            current = current[token]
            continue
        if isinstance(current, list):
            if token == "-" or _ARRAY_INDEX.fullmatch(token) is None:
                raise ValueError(f"JSON pointer has an invalid array index: {pointer}")
            index = int(token)
            if index >= len(current):
                raise ValueError(f"JSON pointer array index is out of range: {pointer}")
            current = current[index]
            continue
        raise ValueError(f"JSON pointer traverses a scalar: {pointer}")
    return current


def _pointer_tokens(pointer: str) -> tuple[str, ...]:
    if pointer == "":
        return ()
    if not pointer.startswith("/"):
        raise ValueError("JSON pointer must be empty or start with '/'")
    return tuple(_decode_pointer_token(token) for token in pointer[1:].split("/"))


def _decode_pointer_token(token: str) -> str:
    output: list[str] = []
    index = 0
    while index < len(token):
        character = token[index]
        if character != "~":
            output.append(character)
            index += 1
            continue
        if index + 1 >= len(token) or token[index + 1] not in {"0", "1"}:
            raise ValueError("JSON pointer contains an invalid escape")
        output.append("~" if token[index + 1] == "0" else "/")
        index += 2
    return "".join(output)


def _object_at(
    inputs: _CanonicalInputs,
    artifact: ArtifactId,
    pointer: str,
) -> dict[str, Any]:
    return _object(_resolve_pointer(inputs.document(artifact), pointer), pointer)


def _array_at(
    inputs: _CanonicalInputs,
    artifact: ArtifactId,
    pointer: str,
) -> list[Any]:
    value = _resolve_pointer(inputs.document(artifact), pointer)
    if not isinstance(value, list):
        raise ValueError(f"{artifact.value}#{pointer} must be an array")
    return value


def _array_member(value: dict[str, Any], member: str) -> list[Any]:
    result = value.get(member)
    if not isinstance(result, list):
        raise ValueError(f"{member} must be an array")
    return result


def _object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _scalar_text(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str | int):
        return str(value)
    raise ValueError("table cells can only copy JSON scalar values")


def _require_binding(
    expected: PrefixBenchFileBinding,
    actual: _LoadedArtifact,
    label: str,
) -> None:
    if (expected.path, expected.bytes, expected.sha256) != (
        actual.binding.path,
        actual.binding.bytes,
        actual.binding.sha256,
    ):
        raise ValueError(f"{label} binding is stale")


def _historical_successful_pairs(report: HistoricalReport) -> int:
    by_case: dict[str, dict[str, bool]] = {}
    for result in report.results:
        by_case.setdefault(str(result.case_id), {})[str(result.revision_role)] = bool(
            getattr(result, "matches_expected", False)
        )
    return sum(
        revisions.get("vulnerable", False) and revisions.get("fixed", False)
        for revisions in by_case.values()
    )


def _require_git_ancestor(
    project_root: Path,
    ancestor: str,
    descendant: str,
    message: str,
) -> None:
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=project_root,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        return
    if result.returncode == 1:
        raise ValueError(message)
    detail = result.stderr.strip() or result.stdout.strip() or "git ancestry check failed"
    raise ValueError(f"{message}: {detail}")


def _write_outputs_once(
    outputs: tuple[tuple[Path, bytes], ...],
) -> tuple[Literal["created", "unchanged"], ...]:
    states: list[Literal["created", "unchanged"]] = []
    for path, data in outputs:
        if path.exists() or path.is_symlink():
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"output must be a regular file: {path}")
            if path.read_bytes() != data:
                raise ValueError(f"refusing to replace a different output: {path}")
            states.append("unchanged")
        else:
            states.append("created")
    for index, ((path, data), state) in enumerate(zip(outputs, states, strict=True)):
        if state == "created":
            states[index] = _write_atomic(path, data)
    return tuple(states)


def _write_atomic(
    path: Path,
    data: bytes,
) -> Literal["created", "unchanged"]:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as stream:
            temporary = Path(stream.name)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"output must be a regular file: {path}") from None
            if path.read_bytes() != data:
                raise ValueError(f"refusing to replace a different output: {path}") from None
            return "unchanged"
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return "created"
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _read_regular_file(project_root: Path, relative: Path, label: str) -> bytes:
    root = project_root.resolve()
    path = (root / relative).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} path escapes the project") from exc
    unresolved = root / relative
    if unresolved.is_symlink() or not unresolved.is_file():
        raise ValueError(f"{label} must be a regular file: {relative}")
    return unresolved.read_bytes()


def _model_canonical_bytes(model: BaseModel) -> bytes:
    canonical = getattr(model, "canonical_bytes", None)
    if callable(canonical):
        value = canonical()
        if not isinstance(value, bytes):
            raise TypeError("canonical_bytes() must return bytes")
        return value
    return _canonical_json(model.model_dump(mode="json"))


def _project_root(project_root: Path) -> Path:
    root = project_root.resolve()
    if not root.is_dir():
        raise ValueError(f"project root is not a directory: {root}")
    return root


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


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
