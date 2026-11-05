from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, computed_field, model_validator


ExecutionMode = Literal[
    "fresh_model_run", "recorded_model_replay", "deterministic_regression", "manual_review"
]
RunStatus = Literal[
    "queued", "running", "completed", "limited", "partial_failed", "blocked",
    "budget_exceeded", "failed", "blocked_missing_credential",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PromptRecord(StrictModel):
    path: str
    version: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class PaperBudget(StrictModel):
    max_calls: int = Field(gt=0)
    max_input_tokens: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    max_duration_seconds: int = Field(gt=0)
    max_retries: int = Field(ge=0)
    max_cost_usd: float | None = Field(default=None, gt=0)
    price_source: str | None = None


class PaperSpec(StrictModel):
    paper_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    title: str = Field(min_length=1)
    group: Literal["historical_regression", "holdout"]
    official_pdf_url: HttpUrl
    pdf_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    version: str = Field(min_length=1)
    reference_status: Literal["frozen", "pending"]
    has_appendix: bool | None = None
    has_tables: bool | None = None
    has_formulas: bool | None = None
    has_figures: bool | None = None


class EvaluationManifest(StrictModel):
    manifest_schema_version: Literal["1.0"] = "1.0"
    permitted_execution_modes: list[ExecutionMode] = [
        "fresh_model_run", "recorded_model_replay", "deterministic_regression", "manual_review"
    ]
    baseline_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    code_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    created_at: datetime
    operating_system: str
    python_version: str
    node_version: str
    schema_versions: dict[str, str]
    prompt_inventory: list[PromptRecord]
    requested_model: str
    actual_model: str | None = None
    provider_label: str
    run_parameters: dict[str, str | int | float | bool | None] = {}
    papers: list[PaperSpec]
    budgets: dict[str, PaperBudget]
    chatgpt_benchmark_status: Literal["pending", "available", "completed"] = "pending"

    @model_validator(mode="after")
    def papers_and_budgets_match(self):
        paper_ids = [item.paper_id for item in self.papers]
        if len(paper_ids) != len(set(paper_ids)):
            raise ValueError("paper_id values must be unique")
        if set(paper_ids) != set(self.budgets):
            raise ValueError("every paper requires exactly one budget")
        return self


class ModelCallSummary(StrictModel):
    stage: str
    status: Literal["completed", "failed", "unknown_usage"]
    requested_model: str
    actual_model: str | None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    duration_ms: int = Field(ge=0)
    error_type: str | None = None


class StageRun(StrictModel):
    stage: Literal["p1", "p2a", "p2b", "p2c", "p3", "p4", "manual_review", "export"]
    status: RunStatus
    execution_mode: ExecutionMode
    started_at: datetime
    finished_at: datetime | None
    document_id: str
    source_bundle_id: str | None
    input_run_id: str | None
    output_run_id: str | None
    model_calls: list[ModelCallSummary] = []
    retry_count: int = Field(default=0, ge=0)
    source_limitations: list[str] = []
    artifact_path: str | None = None
    error_type: str | None = None


class LedgerTotals(StrictModel):
    model_calls: int
    successful_calls: int
    failed_calls: int
    unknown_token_calls: int
    known_input_tokens: int
    known_output_tokens: int
    model_duration_ms: int
    retry_count: int


class RunLedger(StrictModel):
    ledger_schema_version: Literal["1.0"] = "1.0"
    evaluation_id: str
    paper_id: str
    document_id: str
    pdf_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_bundle_id: str | None
    started_at: datetime
    finished_at: datetime | None
    overall_status: RunStatus
    stages: list[StageRun]
    human_interventions: int = Field(default=0, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)
    cost_status: Literal["known", "unknown"] = "unknown"

    @model_validator(mode="after")
    def source_identity_is_stable(self):
        for stage in self.stages:
            if stage.document_id != self.document_id:
                raise ValueError("stage document_id differs from ledger document_id")
            if stage.source_bundle_id and self.source_bundle_id and \
                    stage.source_bundle_id != self.source_bundle_id:
                raise ValueError("stage source_bundle_id differs from frozen source bundle")
        return self

    @computed_field
    @property
    def totals(self) -> LedgerTotals:
        calls = [call for stage in self.stages for call in stage.model_calls]
        return LedgerTotals(
            model_calls=len(calls),
            successful_calls=sum(call.status == "completed" for call in calls),
            failed_calls=sum(call.status == "failed" for call in calls),
            unknown_token_calls=sum(call.input_tokens is None or call.output_tokens is None
                                    for call in calls),
            known_input_tokens=sum(call.input_tokens or 0 for call in calls),
            known_output_tokens=sum(call.output_tokens or 0 for call in calls),
            model_duration_ms=sum(call.duration_ms for call in calls),
            retry_count=sum(stage.retry_count for stage in self.stages),
        )
