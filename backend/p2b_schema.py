"""P2-B candidate-review contracts. No candidate is a verified finding."""

from typing import Literal

from pydantic import Field, model_validator

from backend.p2a_schema import StrictModel


SCHEMA_VERSION = "1.0"


class CandidateSourceRef(StrictModel):
    source_id: str = Field(min_length=1)
    evidence_role: Literal["observed_statement", "supporting_result", "counterevidence",
                           "context", "source_conflict"]


class IssueCandidate(StrictModel):
    issue_id: str = Field(min_length=1)
    dimension_id: Literal["D1", "D2", "D3", "D4", "D5", "D6"]
    issue_type: str = Field(min_length=1)
    title: str = Field(min_length=1)
    observed_fact: str = Field(min_length=1)
    suspected_problem: str = Field(min_length=1)
    why_it_matters: str = Field(min_length=1)
    claim_ids: list[str]
    source_refs: list[CandidateSourceRef]
    possible_counterevidence: list[CandidateSourceRef]
    verification_questions: list[str] = Field(min_length=1)
    potential_impact: Literal["major_candidate", "minor_candidate", "undetermined"]
    verification_status: Literal["unverified"] = "unverified"
    source_limitations: list[str]
    external_verification_required: bool

    @model_validator(mode="after")
    def requires_located_observation(self):
        if not self.source_refs:
            raise ValueError("candidate requires an observed source reference")
        return self


class DimensionReview(StrictModel):
    document_id: str
    source_bundle_id: str
    understanding_run_id: str
    professional_review_run_id: str
    prompt_version: str
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    model_identifier: str | None
    dimension_id: Literal["D1", "D2", "D3", "D4", "D5", "D6"]
    dimension_name: str
    applicability_status: Literal["applicable", "partially_applicable", "not_applicable"]
    applicability_reason: str
    inspection_focus: list[str]
    inspected_claim_ids: list[str]
    inspected_source_refs: list[str]
    coverage_status: Literal["sufficient", "partial", "blocked"]
    limitations: list[str]
    candidates: list[IssueCandidate]
    no_issue_explanation: str | None
    execution_status: Literal["completed", "limited", "failed", "skipped", "budget_exceeded"]

    @model_validator(mode="after")
    def coherent_state(self):
        if self.applicability_status == "not_applicable" and self.candidates:
            raise ValueError("inapplicable dimension cannot have candidates")
        if self.execution_status in {"completed", "limited"} and not self.candidates and not self.no_issue_explanation:
            raise ValueError("completed dimension without candidates requires explanation")
        return self


class ProfessionalReviewResult(StrictModel):
    document_id: str
    source_bundle_id: str
    understanding_run_id: str
    professional_review_run_id: str
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    model_identifier: str
    dimensions: list[DimensionReview]
    candidates: list[IssueCandidate]
    incomplete_items: list[str]
    input_limitations: list[str]
    call_count: int = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    unknown_token_calls: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    execution_status: Literal["completed", "limited", "partial_failed", "budget_exceeded", "failed"]

    @model_validator(mode="after")
    def six_dimensions_and_candidates(self):
        if [item.dimension_id for item in self.dimensions] != [f"D{i}" for i in range(1, 7)]:
            raise ValueError("result must record all six dimensions in order")
        expected = [candidate for dimension in self.dimensions for candidate in dimension.candidates]
        if self.candidates != expected:
            raise ValueError("candidate list must match dimension records")
        if len({item.issue_id for item in self.candidates}) != len(self.candidates):
            raise ValueError("duplicate candidate ID")
        return self
