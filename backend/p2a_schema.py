"""Versioned contracts for P2-A paper understanding, separate from review findings."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InputCoverage(StrictModel):
    total_unit_count: int = Field(ge=0)
    included_unit_count: int = Field(ge=0)
    excluded_unit_ids: list[str]
    source_quality_status: Literal["ready", "needs_review", "blocked"]
    limitations: list[str]

    @model_validator(mode="after")
    def counts_consistent(self):
        if self.included_unit_count + len(self.excluded_unit_ids) != self.total_unit_count:
            raise ValueError("input coverage counts do not match")
        return self


class ResultMetadata(StrictModel):
    document_id: str = Field(min_length=1)
    source_bundle_id: str = Field(min_length=1)
    review_run_id: str = Field(min_length=1)
    prompt_version: str = Field(min_length=1)
    schema_version: Literal["1.0"]
    model_identifier: str = Field(min_length=1)
    input_coverage: InputCoverage
    execution_status: Literal["completed", "limited"]


class EvidenceReference(StrictModel):
    id: str
    object_type: Literal["reading_unit", "evidence", "section", "table", "formula", "figure"]
    object_id: str
    evidence_ids: list[str] = Field(min_length=1)
    document_id: str
    snapshot_id: str
    source_bundle_id: str
    page_index: int = Field(ge=0)
    verification_status: Literal["text_extracted_unverified", "structure_unverified",
                                 "visual_review_required"]


class SourcedFact(StrictModel):
    text: str = Field(min_length=1)
    evidence_ref_ids: list[str] = Field(min_length=1)
    provenance: Literal["direct_source", "model_interpretation"] = "direct_source"


class ReviewPlan(ResultMetadata):
    paper_type: str
    research_domain: str
    methodological_paradigm: str
    applicable_review_dimensions: list[str]
    priority_dimensions: list[str]
    excluded_dimensions: list[str]
    required_materials: list[str]
    missing_materials: list[str]


class PaperProfile(ResultMetadata):
    research_problem: SourcedFact | None
    research_motivation: SourcedFact | None
    research_gap_claimed_by_authors: SourcedFact | None
    proposed_method: SourcedFact | None
    method_components: list[SourcedFact]
    theoretical_assumptions: list[SourcedFact]
    experimental_setup: list[SourcedFact]
    datasets_and_metrics: list[SourcedFact]
    main_results: list[SourcedFact]
    author_claimed_contributions: list[SourcedFact]
    conclusions: list[SourcedFact]
    author_stated_limitations: list[SourcedFact]
    evidence_refs: list[EvidenceReference]
    unresolved_source_gaps: list[str]


class Contribution(StrictModel):
    id: str
    title: str
    description: str
    evidence_ref_ids: list[str] = Field(min_length=1)


class Claim(StrictModel):
    id: str
    contribution_id: str
    statement: str
    evidence_ref_ids: list[str]


class SupportRelation(StrictModel):
    claim_id: str
    evidence_ref_id: str
    relation: Literal["direct_source", "model_interpretation", "unverified"]
    explanation: str


class EvidenceGap(StrictModel):
    claim_id: str
    description: str
    status: Literal["support_not_found", "source_unverified"]


class ClaimEvidenceMap(ResultMetadata):
    contributions: list[Contribution]
    claims: list[Claim]
    evidence_refs: list[EvidenceReference]
    support_relations: list[SupportRelation]
    evidence_gaps: list[EvidenceGap]


def source_reference_index(bundle: dict) -> dict[str, EvidenceReference]:
    """Derive references only from real bundle objects and their original Evidence IDs."""
    document = bundle["document"]
    known_evidence = {item["evidence_id"] for item in bundle["body"]}
    references = {}
    groups = (("reading_unit", bundle["reading_units"]),
              ("evidence", bundle["body"]), ("section", bundle["sections"]),
              ("table", bundle["tables"]), ("formula", bundle["formulas"]),
              ("figure", bundle["figures"]))
    for object_type, objects in groups:
        for item in objects:
            object_id = item["evidence_id"] if object_type == "evidence" else item["id"]
            evidence_ids = (item.get("evidence_ids") or
                            [item[key] for key in ("evidence_id", "caption_evidence_id") if item.get(key)])
            if not evidence_ids or not set(evidence_ids).issubset(known_evidence):
                continue
            status = ("visual_review_required" if object_type == "figure" else
                      "structure_unverified" if object_type in {"table", "formula", "section"} else
                      "text_extracted_unverified")
            references[object_id] = EvidenceReference(
                id=object_id, object_type=object_type, object_id=object_id,
                evidence_ids=evidence_ids, document_id=document["id"],
                snapshot_id=document["snapshot_id"], source_bundle_id=bundle["bundle_id"],
                page_index=item["page_index"], verification_status=status)
    return references


def _fact_refs(profile: PaperProfile):
    for field in ("research_problem", "research_motivation", "research_gap_claimed_by_authors",
                  "proposed_method"):
        fact = getattr(profile, field)
        if fact:
            yield from fact.evidence_ref_ids
    for field in ("method_components", "theoretical_assumptions", "experimental_setup",
                  "datasets_and_metrics", "main_results", "author_claimed_contributions",
                  "conclusions", "author_stated_limitations"):
        for fact in getattr(profile, field):
            yield from fact.evidence_ref_ids


def validate_source_references(result: PaperProfile | ClaimEvidenceMap, bundle: dict) -> None:
    if result.document_id != bundle["document"]["id"] or result.source_bundle_id != bundle["bundle_id"]:
        raise ValueError("result belongs to another document or Source Bundle")
    canonical = source_reference_index(bundle)
    cited = {item.id: item for item in result.evidence_refs}
    if len(cited) != len(result.evidence_refs):
        raise ValueError("duplicate evidence references")
    for reference in result.evidence_refs:
        expected = canonical.get(reference.id)
        if not expected:
            raise ValueError(f"missing source object: {reference.id}")
        if reference.snapshot_id != expected.snapshot_id:
            raise ValueError(f"snapshot mismatch: {reference.id}")
        if reference != expected:
            raise ValueError(f"source mapping mismatch: {reference.id}")
    required = list(_fact_refs(result)) if isinstance(result, PaperProfile) else []
    if isinstance(result, ClaimEvidenceMap):
        contributions = {item.id for item in result.contributions}
        claims = {item.id for item in result.claims}
        if len(contributions) != len(result.contributions) or len(claims) != len(result.claims):
            raise ValueError("duplicate contribution or claim ID")
        for item in result.contributions:
            required.extend(item.evidence_ref_ids)
        for item in result.claims:
            if item.contribution_id not in contributions:
                raise ValueError(f"missing contribution: {item.contribution_id}")
            required.extend(item.evidence_ref_ids)
        for relation in result.support_relations:
            if relation.claim_id not in claims:
                raise ValueError(f"missing claim: {relation.claim_id}")
            required.append(relation.evidence_ref_id)
        for gap in result.evidence_gaps:
            if gap.claim_id not in claims:
                raise ValueError(f"missing claim: {gap.claim_id}")
    for reference_id in required:
        if reference_id not in cited:
            raise ValueError(f"missing evidence reference: {reference_id}")
