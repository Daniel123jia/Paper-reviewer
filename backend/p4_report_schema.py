"""P4 report contracts. Approval is separate from P3 scientific verification."""

from typing import Literal

from pydantic import Field, model_validator

from backend.p2a_schema import StrictModel


SCHEMA_VERSION = "1.1"
EligibilityStatus = Literal["auto_draft_candidate", "human_review_required",
                            "internal_only", "excluded"]
ApprovalStatus = Literal["pending", "approved_for_draft", "approved_for_final",
                         "needs_verification", "excluded"]
ReportKind = Literal["internal", "author", "editor"]
Language = Literal["zh", "en"]


class ReportSourceIdentity(StrictModel):
    document_id: str
    snapshot_id: str
    pdf_sha256: str
    source_bundle_id: str
    understanding_run_id: str
    professional_review_run_id: str
    consolidation_run_id: str
    evidence_verification_run_id: str
    p3_result_sha256: str
    p3_schema_version: str
    p3_execution_mode: str


class EligibilityEntry(StrictModel):
    consolidated_issue_id: str
    verification_task_id: str
    title: str
    scientific_question: str
    verdict: str
    report_eligibility: str
    human_review_status: str
    processing_status: EligibilityStatus
    source_refs: list[str]
    counterevidence_refs: list[str]
    outstanding_checks: list[str]
    provisional_impact: str


class ReviewerDecision(StrictModel):
    decision_id: str
    report_project_id: str
    consolidated_issue_id: str
    verification_task_id: str | None
    decision: Literal["approve_for_draft", "approve_for_final", "revise", "exclude",
                      "request_more_verification"]
    reviewer_approval_status: ApprovalStatus
    selected_source_refs: list[str]
    reviewer_rationale: str
    evidence_review_record_ids: list[str]
    external_evidence_review_record_ids: list[str] = Field(default_factory=list)
    external_dependency_disposition: Literal["not_assessed", "verified",
                                             "clarification_only"] = "not_assessed"
    external_uncertainty_qualification: str = ""
    reviewer_edited_comment: str | None = None
    report_order: int = Field(default=0, ge=0)
    created_at: str
    updated_at: str


class EvidenceReviewRecord(StrictModel):
    record_id: str
    report_project_id: str
    consolidated_issue_id: str
    source_ref_id: str
    page_index: int = Field(ge=0)
    object_label: str
    checked_rows_columns: str
    checked_values_and_labels: str
    metric_names_and_order: str
    unresolved_configurations: list[str]
    reviewer_note: str
    confirmation_status: Literal["confirmed", "unresolved"]
    reviewed_at: str


class ExternalEvidenceReviewRecord(StrictModel):
    record_id: str
    report_project_id: str
    consolidated_issue_id: str
    material_type: Literal["literature", "code", "data", "other"]
    material_identifier: str
    verification_question: str
    verified_finding: str
    limitations: list[str]
    reviewer_note: str
    confirmation_status: Literal["confirmed", "unresolved"]
    reviewed_at: str


class ReviewerAddedIssue(StrictModel):
    issue_id: str
    report_project_id: str
    origin: Literal["reviewer_added"] = "reviewer_added"
    scientific_question: str = Field(min_length=8)
    evidence_basis: str = Field(min_length=8)
    source_refs: list[str]
    comment_body: str
    category: Literal["major", "minor", "clarification"] = "clarification"
    created_at: str
    updated_at: str


class ReportIssueGroup(StrictModel):
    group_id: str
    source_issue_ids: list[str] = Field(min_length=1)
    scientific_question: str
    category: Literal["major", "minor", "clarification"]
    order: int = Field(ge=0)
    grouping_rationale: str


class ReportEvidenceItem(StrictModel):
    source_issue_id: str
    scientific_question: str
    original_question: str
    revised_question: str | None
    verdict: str
    report_eligibility: str
    human_review_status: str
    reviewer_approval_status: ApprovalStatus
    observed_statements: list[str]
    source_refs: list[str]
    counterevidence_refs: list[str]
    author_explanations: list[str]
    outstanding_checks: list[str]
    evidence_limitations: list[str]
    source_locations: list[str]
    actual_tool_results: list[str]
    external_verification_required: bool = False
    external_dependency_status: Literal["not_required", "completed_in_p3",
                                        "reviewer_verified", "clarification_only",
                                        "critical_unresolved", "noncritical_unresolved"] = "not_required"
    external_reporting_constraint: str = ""
    external_evidence_review_record_ids: list[str] = Field(default_factory=list)
    external_verified_findings: list[str] = Field(default_factory=list)


class ReportEvidencePack(StrictModel):
    schema_version: Literal["1.0", "1.1"] = SCHEMA_VERSION
    admission_rule_version: Literal["p4.0", "p4.1"] | None = None
    source: ReportSourceIdentity
    report_kind: ReportKind
    language: Language
    paper_facts: list[str]
    issues: list[ReportEvidenceItem]
    groups: list[ReportIssueGroup]
    reviewer_added: list[ReviewerAddedIssue]
    approval_record_ids: list[str]
    source_limitations: list[str]


class ReportPlan(StrictModel):
    schema_version: Literal["1.0", "1.1"] = SCHEMA_VERSION
    admission_rule_version: Literal["p4.0", "p4.1"] | None = None
    source: ReportSourceIdentity
    report_kind: ReportKind
    language: Language
    selected_issue_ids: list[str]
    internal_only_issue_ids: list[str]
    excluded_issue_ids: list[str]
    groups: list[ReportIssueGroup]
    source_limitations: list[str]


class ReportComment(StrictModel):
    report_comment_id: str
    source_issue_ids: list[str] = Field(min_length=1)
    source: Literal["p3", "reviewer_added"]
    title_zh: str = Field(min_length=1)
    title_en: str = Field(min_length=1)
    comment_body: str = Field(min_length=20)
    scientific_question: str = Field(min_length=8)
    evidence_refs: list[str]
    author_action_requested: str = Field(min_length=8)
    comment_category: Literal["major", "minor", "clarification"]
    uncertainty_qualification: str
    reviewer_approval_status: ApprovalStatus = "pending"


class ReportWritingResult(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    prompt_version: str
    language: Language
    report_kind: ReportKind
    general_assessment: str
    comments: list[ReportComment]

    @model_validator(mode="after")
    def unique_comments(self):
        ids = [item.report_comment_id for item in self.comments]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate report comment ID")
        return self
