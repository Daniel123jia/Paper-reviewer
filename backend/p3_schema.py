"""P3 evidence verification contracts; P2-C remains an immutable input."""

from typing import Literal

from pydantic import Field, model_validator

from backend.p2a_schema import StrictModel


SCHEMA_VERSION = "1.0"
Verdict = Literal["concern_supported", "concern_refuted", "concern_narrowed",
                  "inconclusive", "blocked"]
SourceStatus = Literal["pdf_text_located", "pdf_text_mismatch", "visual_check_required",
                       "pdf_unavailable", "external_check_unavailable", "human_confirmed"]


class VerificationSource(StrictModel):
    source_id: str
    source_type: Literal["reading_unit", "evidence", "section", "table", "formula",
                         "figure", "external"]
    document_id: str | None
    snapshot_id: str | None
    page_index: int | None = Field(default=None, ge=0)
    bbox: list[float] | None = None
    evidence_ids: list[str]
    source_method: Literal["frozen_bundle_plus_pdf_text", "frozen_bundle_only",
                           "external_document", "human_pdf_review"]
    source_verification_status: SourceStatus
    relevant_excerpt: str
    region_image_sha256: str | None = None
    limitations: list[str]


class SourceObservation(StrictModel):
    observation_id: str
    verification_task_id: str
    related_source_ref_ids: list[str] = Field(min_length=1)
    document_id: str
    snapshot_id: str
    page_index: int | None = Field(default=None, ge=0)
    bbox: list[float] | None = None
    original_excerpt: str
    observed_statement: str
    observation_type: Literal["source_text", "experimental_result", "author_claim",
                              "cited_prior_work", "model_interpretation", "ambiguous"]
    source_check_method: Literal["pdf_text_extraction", "frozen_bundle_only",
                                 "model_interpretation", "human_pdf_review"]
    source_check_status: SourceStatus
    limitations: list[str]


class ToolCheck(StrictModel):
    tool_result_id: str
    verification_task_id: str
    check_type: Literal["numeric", "table", "scalar_formula", "pdf_region", "external"]
    method: str
    input_refs: list[str]
    input_values: dict[str, str | float | int | None]
    result: dict
    status: Literal["completed", "inconclusive", "blocked"]
    limitations: list[str]


class VerificationSubcheck(StrictModel):
    subcheck_id: str
    parent_verification_task_id: str
    question: str = Field(min_length=1)
    check_type: str
    input_refs: list[str]
    method: str
    actual_observations: list[str]
    supporting_refs: list[str]
    contradicting_refs: list[str]
    tool_result_ids: list[str]
    status: Literal["supported", "contradicted", "inconclusive", "blocked"]
    limitations: list[str]


class IssueAdjudication(StrictModel):
    consolidated_issue_id: str
    verification_task_id: str
    original_candidate_ids: list[str] = Field(min_length=1)
    original_central_question: str
    revised_question: str | None
    subchecks: list[VerificationSubcheck] = Field(min_length=1)
    verified_observation_ids: list[str]
    supporting_evidence: list[str]
    counterevidence: list[str]
    author_explanations: list[str]
    executed_tool_result_ids: list[str]
    outstanding_checks: list[str]
    verdict: Verdict
    verdict_rationale: str
    remaining_uncertainty: list[str]
    provisional_impact: Literal["major_candidate", "minor_candidate", "undetermined"]
    impact_rationale: str
    report_eligibility: Literal["eligible_with_qualification", "requires_human_review",
                                "not_reportable"]
    human_review_status: Literal["pending", "confirmed", "rejected"] = "pending"
    execution_status: Literal["completed", "limited", "blocked", "failed"]

    @model_validator(mode="after")
    def reportability_is_conservative(self):
        if self.verdict == "concern_refuted" and self.report_eligibility != "not_reportable":
            raise ValueError("refuted issue cannot be reportable")
        if self.verdict in {"inconclusive", "blocked"} and \
                self.report_eligibility == "eligible_with_qualification":
            raise ValueError("unresolved issue cannot enter a report")
        if self.verdict == "concern_narrowed" and not self.revised_question:
            raise ValueError("narrowed issue needs a revised question")
        return self


class EvidenceVerificationResult(StrictModel):
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    document_id: str
    snapshot_id: str
    pdf_sha256: str | None
    source_bundle_id: str
    understanding_run_id: str
    professional_review_run_id: str
    consolidation_run_id: str
    evidence_verification_run_id: str
    prompt_versions: list[str]
    model_identifier: str
    pdf_identity_status: Literal["sha256_matched", "pdf_unavailable"]
    sources: list[VerificationSource]
    observations: list[SourceObservation]
    tool_results: list[ToolCheck]
    adjudications: list[IssueAdjudication]
    uncovered_tasks: list[str]
    unresolved_coverage_gaps: list[str]
    call_count: int = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    unknown_token_calls: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    retry_count: int = Field(ge=0)
    execution_status: Literal["completed", "limited", "partial_failed", "budget_exceeded",
                              "failed"]

    @model_validator(mode="after")
    def unique_records(self):
        for values in (self.sources, self.observations, self.tool_results,
                       self.adjudications):
            field = ("source_id" if values and isinstance(values[0], VerificationSource) else
                     "observation_id" if values and isinstance(values[0], SourceObservation) else
                     "tool_result_id" if values and isinstance(values[0], ToolCheck) else
                     "verification_task_id")
            ids = [getattr(item, field) for item in values]
            if len(ids) != len(set(ids)):
                raise ValueError(f"duplicate {field}")
        return self
