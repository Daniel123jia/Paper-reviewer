"""Validate one frozen P2-A/P2-B snapshot and index every original candidate."""

from backend.p2a_schema import ClaimEvidenceMap, PaperProfile, ReviewPlan, source_reference_index
from backend.p2b_engine import validate_understanding_snapshot
from backend.p2b_schema import IssueCandidate, ProfessionalReviewResult
from backend.p2c_schema import OriginalCandidateInventory


def validate_consolidation_inputs(bundle: dict, plan: ReviewPlan, profile: PaperProfile,
                                  mapping: ClaimEvidenceMap, review: ProfessionalReviewResult,
                                  understanding_run_id: str, professional_review_run_id: str,
                                  p2b_status: str) -> None:
    if p2b_status not in {"completed", "limited"} or review.execution_status != p2b_status:
        raise ValueError("complete P2-B input must have completed or limited status")
    validate_understanding_snapshot(bundle, plan, profile, mapping, understanding_run_id, "completed")
    if (review.document_id != bundle["document"]["id"] or
            review.source_bundle_id != bundle["bundle_id"] or
            review.understanding_run_id != understanding_run_id or
            review.professional_review_run_id != professional_review_run_id):
        raise ValueError("P2-B result and frozen document snapshot mismatch")
    known_claims = {item.id for item in mapping.claims}
    known_sources = source_reference_index(bundle)
    seen = set()
    for dimension in review.dimensions:
        if (dimension.document_id != review.document_id or
                dimension.source_bundle_id != review.source_bundle_id or
                dimension.understanding_run_id != understanding_run_id or
                dimension.professional_review_run_id != professional_review_run_id):
            raise ValueError("dimension belongs to a different document snapshot or run")
        if not set(dimension.inspected_claim_ids).issubset(known_claims):
            raise ValueError("dimension cites an unknown inspected claim")
        if not set(dimension.inspected_source_refs).issubset(known_sources):
            raise ValueError("dimension cites an unknown inspected source")
        for candidate in dimension.candidates:
            if candidate.dimension_id != dimension.dimension_id:
                raise ValueError("candidate dimension mismatch")
            if candidate.issue_id in seen:
                raise ValueError("duplicate original candidate ID")
            seen.add(candidate.issue_id)
            if not set(candidate.claim_ids).issubset(known_claims):
                raise ValueError("candidate cites an unknown claim")
            for ref in candidate.source_refs + candidate.possible_counterevidence:
                if ref.source_id not in known_sources:
                    raise ValueError("candidate source does not exist in frozen Source Bundle")
    # Revalidate even when an in-memory Pydantic instance was mutated after construction.
    ProfessionalReviewResult.model_validate(review.model_dump())


def normalize_candidates(bundle: dict, plan: ReviewPlan, profile: PaperProfile,
                         mapping: ClaimEvidenceMap, review: ProfessionalReviewResult,
                         understanding_run_id: str, professional_review_run_id: str,
                         p2b_status: str) -> OriginalCandidateInventory:
    validate_consolidation_inputs(bundle, plan, profile, mapping, review,
                                  understanding_run_id, professional_review_run_id, p2b_status)
    claims = {item.id: item for item in mapping.claims}
    original = [IssueCandidate.model_validate(item.model_dump()) for item in review.candidates]
    dimensions = {f"D{i}": [] for i in range(1, 7)}
    claim_index = {item.id: [] for item in mapping.claims}
    contribution_index = {item.id: [] for item in mapping.contributions}
    source_index = {}
    for candidate in original:
        issue_id = candidate.issue_id
        dimensions[candidate.dimension_id].append(issue_id)
        for claim_id in dict.fromkeys(candidate.claim_ids):
            claim_index[claim_id].append(issue_id)
        for contribution_id in dict.fromkeys(claims[claim_id].contribution_id
                                             for claim_id in candidate.claim_ids):
            contribution_index[contribution_id].append(issue_id)
        for source_id in dict.fromkeys(ref.source_id for ref in
                                       candidate.source_refs + candidate.possible_counterevidence):
            source_index.setdefault(source_id, []).append(issue_id)
    return OriginalCandidateInventory(
        document_id=review.document_id, snapshot_id=bundle["document"]["snapshot_id"],
        pdf_sha256=bundle["document"].get("sha256"), source_bundle_id=review.source_bundle_id,
        understanding_run_id=understanding_run_id,
        professional_review_run_id=professional_review_run_id,
        p2a_schema_versions={"review_plan": plan.schema_version,
                             "paper_profile": profile.schema_version,
                             "claim_evidence_map": mapping.schema_version},
        p2b_schema_version=review.schema_version,
        p2b_prompt_versions={item.dimension_id: item.prompt_version for item in review.dimensions},
        candidate_count=len(original), candidates=original,
        dimension_index=dimensions, claim_index=claim_index,
        contribution_index=contribution_index, source_index=source_index)
