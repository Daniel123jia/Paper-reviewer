"""P2-C contracts: lossless consolidation of unverified P2-B candidates."""

import itertools
from typing import Literal

from pydantic import Field, model_validator

from backend.p2a_schema import StrictModel
from backend.p2b_schema import CandidateSourceRef, IssueCandidate


SCHEMA_VERSION = "1.1"
RelationKind = Literal["exact_duplicate", "semantic_duplicate", "related_but_distinct",
                       "distinct", "uncertain_relation"]
Impact = Literal["major_candidate", "minor_candidate", "undetermined"]


class OriginalCandidateInventory(StrictModel):
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    document_id: str
    snapshot_id: str
    pdf_sha256: str | None
    source_bundle_id: str
    understanding_run_id: str
    professional_review_run_id: str
    p2a_schema_versions: dict[str, str]
    p2b_schema_version: str
    p2b_prompt_versions: dict[str, str]
    candidate_count: int = Field(ge=0)
    candidates: list[IssueCandidate]
    dimension_index: dict[str, list[str]]
    claim_index: dict[str, list[str]]
    contribution_index: dict[str, list[str]]
    source_index: dict[str, list[str]]

    @model_validator(mode="after")
    def complete_inventory(self):
        ids = [item.issue_id for item in self.candidates]
        if self.candidate_count != len(ids) or len(ids) != len(set(ids)):
            raise ValueError("duplicate or missing original candidate")
        for index in (self.dimension_index, self.claim_index, self.contribution_index,
                      self.source_index):
            if any(len(values) != len(set(values)) or not set(values).issubset(ids)
                   for values in index.values()):
                raise ValueError("candidate index contains unknown or duplicate issue ID")
        return self


class CandidateRelation(StrictModel):
    left_issue_id: str
    right_issue_id: str
    relation: RelationKind
    rationale: str = Field(min_length=1)
    shared_verification_need: str | None = None

    @model_validator(mode="after")
    def ordered_pair(self):
        if self.left_issue_id >= self.right_issue_id:
            raise ValueError("relation IDs must be distinct and sorted")
        return self


class ConsolidatedIssue(StrictModel):
    consolidated_issue_id: str
    title: str
    central_question: str
    observed_facts: list[str]
    suspected_problem: str
    scientific_significance: str
    source_candidate_ids: list[str] = Field(min_length=1)
    source_dimension_ids: list[str]
    related_claim_ids: list[str]
    related_contribution_ids: list[str]
    original_source_refs: list[CandidateSourceRef]
    counterevidence_refs: list[CandidateSourceRef]
    verification_questions: list[str]
    source_limitations: list[str]
    external_verification_required: bool
    external_flag_disagreement: bool
    original_impact_assessments: dict[str, Impact]
    potential_impact: Impact
    impact_rationale: str
    consolidation_rationale: str
    verification_status: Literal["unverified"] = "unverified"


class CandidateLineage(StrictModel):
    original_issue_id: str
    consolidated_issue_id: str
    split_rationale: str | None = None


class DimensionCoverage(StrictModel):
    dimension_id: str
    execution_status: str
    applicability_status: str
    coverage_status: str
    candidate_count: int = Field(ge=0)
    no_issue_explanation: str | None
    evidence_level: Literal["model_report_only", "not_reported"]
    limitations: list[str]


class ClaimCoverage(StrictModel):
    claim_id: str
    contribution_id: str
    inspected_by_dimensions: list[str]
    candidate_ids: list[str]
    status: Literal["reported_inspected_with_issue", "reported_inspected_no_issue",
                    "not_reported_inspected", "source_limited"]
    evidence_level: Literal["model_report_only", "not_reported"]
    limitations: list[str]


class ContributionCoverage(StrictModel):
    contribution_id: str
    claim_ids: list[str]
    inspected_claim_ids: list[str]
    candidate_ids: list[str]
    status: Literal["reported_inspected_with_issue", "reported_inspected_no_issue",
                    "not_reported_inspected", "source_limited"]
    limitations: list[str]


class FocusCoverage(StrictModel):
    focus_id: str
    dimension_id: str
    status: Literal["reported_inspected", "reported_partial", "mapping_uncertain",
                    "coverage_gap", "not_applicable"]
    explanation: str


class CoverageGap(StrictModel):
    gap_id: str
    gap_type: Literal["dimension_unfinished", "claim_not_inspected", "focus_not_inspected",
                      "source_quality", "external_material", "required_material_missing",
                      "specialist_rules_unavailable"]
    affected_contribution_ids: list[str]
    affected_claim_ids: list[str]
    affected_dimension_ids: list[str]
    description: str
    cause: str
    source_limitations: list[str]
    next_action: str
    status: Literal["unresolved"] = "unresolved"


class CoverageAudit(StrictModel):
    dimensions: list[DimensionCoverage]
    contributions: list[ContributionCoverage]
    claims: list[ClaimCoverage]
    professional_focus: list[FocusCoverage]
    gaps: list[CoverageGap]
    limitations: list[str]
    evidence_level: Literal["model_report_only"] = "model_report_only"


class VerificationTask(StrictModel):
    verification_task_id: str
    consolidated_issue_id: str
    central_question: str
    related_claim_ids: list[str]
    original_candidate_ids: list[str]
    source_refs_to_check: list[CandidateSourceRef]
    counterevidence_refs_to_check: list[CandidateSourceRef]
    original_document_snapshot: dict[str, str | None]
    required_checks: list[str]
    additional_materials_needed: list[str]
    external_verification_required: bool
    priority_for_verification: Literal["high", "normal", "undetermined"]
    limitations: list[str]
    verification_status: Literal["unverified"] = "unverified"


class CandidateConsolidationResult(StrictModel):
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    document_id: str
    snapshot_id: str
    pdf_sha256: str | None
    source_bundle_id: str
    understanding_run_id: str
    professional_review_run_id: str
    consolidation_run_id: str
    prompt_versions: list[str]
    model_identifier: str
    inventory: OriginalCandidateInventory
    relations: list[CandidateRelation]
    consolidated_issues: list[ConsolidatedIssue]
    lineage: list[CandidateLineage]
    coverage_audit: CoverageAudit
    verification_tasks: list[VerificationTask]
    incomplete_items: list[str]
    call_count: int = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    unknown_token_calls: int = Field(ge=0)
    duration_ms: int = Field(ge=0)
    retry_count: int = Field(ge=0)
    execution_status: Literal["completed", "limited", "partial_failed", "budget_exceeded", "failed"]

    @model_validator(mode="after")
    def lineage_and_evidence_complete(self):
        original = {item.issue_id: item for item in self.inventory.candidates}
        expected_pairs = set(itertools.combinations(sorted(original), 2))
        actual_pairs = {(item.left_issue_id, item.right_issue_id) for item in self.relations}
        if len(actual_pairs) != len(self.relations) or actual_pairs != expected_pairs:
            raise ValueError("candidate relationship record is incomplete")
        issue_ids = [item.consolidated_issue_id for item in self.consolidated_issues]
        if len(set(issue_ids)) != len(issue_ids):
            raise ValueError("duplicate consolidated issue ID")
        if len(self.lineage) != len(original) or {item.original_issue_id for item in self.lineage} != set(original):
            raise ValueError("every original candidate needs one lineage record")
        if len({item.original_issue_id for item in self.lineage}) != len(self.lineage):
            raise ValueError("original candidate counted more than once")
        assigned = {item.original_issue_id: item.consolidated_issue_id for item in self.lineage}
        if set(assigned.values()) != set(issue_ids):
            raise ValueError("lineage does not cover consolidated issues")
        for issue in self.consolidated_issues:
            members = issue.source_candidate_ids
            if len(set(members)) != len(members) or not set(members).issubset(original):
                raise ValueError("consolidated issue has unknown or duplicate source candidate")
            if {item_id for item_id in members if assigned[item_id] == issue.consolidated_issue_id} != set(members):
                raise ValueError("consolidated issue and lineage mismatch")
            source_refs = {(ref.source_id, ref.evidence_role) for item_id in members
                           for ref in original[item_id].source_refs}
            counter_refs = {(ref.source_id, ref.evidence_role) for item_id in members
                            for ref in original[item_id].possible_counterevidence}
            if source_refs != {(ref.source_id, ref.evidence_role) for ref in issue.original_source_refs}:
                raise ValueError("original source evidence lost during consolidation")
            if counter_refs != {(ref.source_id, ref.evidence_role) for ref in issue.counterevidence_refs}:
                raise ValueError("counterevidence lost during consolidation")
            if not {question for item_id in members for question in original[item_id].verification_questions}.issubset(
                    issue.verification_questions):
                raise ValueError("verification question lost during consolidation")
            if not {limit for item_id in members for limit in original[item_id].source_limitations}.issubset(
                    issue.source_limitations):
                raise ValueError("source limitation lost during consolidation")
            if issue.original_impact_assessments != {item_id: original[item_id].potential_impact
                                                     for item_id in members}:
                raise ValueError("original impact assessment lost")
            if set(issue.observed_facts) != {original[item_id].observed_fact for item_id in members}:
                raise ValueError("original observed facts changed during consolidation")
            if set(issue.verification_questions) != {question for item_id in members
                                                     for question in original[item_id].verification_questions}:
                raise ValueError("original verification questions changed")
            if set(issue.source_limitations) != {limit for item_id in members
                                                 for limit in original[item_id].source_limitations}:
                raise ValueError("original source limitations changed")
            if set(issue.source_dimension_ids) != {original[item_id].dimension_id for item_id in members}:
                raise ValueError("source dimension lineage changed")
            if set(issue.related_claim_ids) != {claim_id for item_id in members
                                                for claim_id in original[item_id].claim_ids}:
                raise ValueError("claim lineage changed")
            flags = {original[item_id].external_verification_required for item_id in members}
            if issue.external_verification_required != (True in flags) or \
                    issue.external_flag_disagreement != (len(flags) > 1):
                raise ValueError("external-verification disagreement lost")
            if any(original[item_id].suspected_problem not in issue.suspected_problem or
                   original[item_id].why_it_matters not in issue.scientific_significance
                   for item_id in members):
                raise ValueError("original scientific concern lost")
        tasks = {item.consolidated_issue_id for item in self.verification_tasks}
        if len(tasks) != len(self.verification_tasks) or tasks != set(issue_ids):
            raise ValueError("each consolidated issue needs one verification task")
        return self
