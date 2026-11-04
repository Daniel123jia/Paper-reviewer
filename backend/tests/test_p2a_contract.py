import pytest
from pydantic import ValidationError

from backend.p2a_schema import (ClaimEvidenceMap, PaperProfile, ReviewPlan,
                                 SourcedFact, validate_source_references)


def _metadata():
    return {"document_id": "doc", "source_bundle_id": "bundle", "review_run_id": "run",
            "prompt_version": "v1", "schema_version": "1.0", "model_identifier": "model-returned",
            "input_coverage": {"total_unit_count": 1, "included_unit_count": 1,
                               "excluded_unit_ids": [], "source_quality_status": "needs_review",
                               "limitations": ["formula unverified"]},
            "execution_status": "limited"}


def _bundle():
    return {"bundle_id": "bundle", "document": {"id": "doc", "snapshot_id": "snap"},
            "body": [{"evidence_id": "ev1", "page_index": 0, "bbox": [1, 2, 3, 4]}],
            "reading_units": [{"id": "ru1", "evidence_ids": ["ev1"], "page_index": 0,
                               "bbox": [1, 2, 3, 4]}],
            "sections": [], "tables": [], "formulas": [], "figures": []}


def test_versioned_contracts_bind_run_and_reject_uncited_facts():
    metadata = _metadata()
    plan = ReviewPlan.model_validate({**metadata, "paper_type": "AI algorithm research",
        "research_domain": "machine learning", "methodological_paradigm": "empirical algorithm",
        "applicable_review_dimensions": ["method"], "priority_dimensions": ["method"],
        "excluded_dimensions": [], "required_materials": ["method"], "missing_materials": []})
    assert plan.review_run_id == "run"
    with pytest.raises(ValidationError):
        SourcedFact(text="unsupported assertion", evidence_ref_ids=[])
    profile = PaperProfile.model_validate({**metadata,
        "research_problem": {"text": "Problem", "evidence_ref_ids": ["ru1"]},
        "research_motivation": None, "research_gap_claimed_by_authors": None,
        "proposed_method": None, "method_components": [], "theoretical_assumptions": [],
        "experimental_setup": [], "datasets_and_metrics": [], "main_results": [],
        "author_claimed_contributions": [], "conclusions": [],
        "author_stated_limitations": [], "evidence_refs": [],
        "unresolved_source_gaps": ["formula not checked"]})
    assert profile.research_problem.evidence_ref_ids == ["ru1"]
    assert profile.schema_version == "1.0"


def test_claim_map_references_resolve_to_snapshot_and_original_evidence():
    metadata = _metadata()
    valid = ClaimEvidenceMap.model_validate({**metadata,
        "contributions": [{"id": "c1", "title": "Contribution", "description": "Description",
                           "evidence_ref_ids": ["ru1"]}],
        "claims": [{"id": "cl1", "contribution_id": "c1", "statement": "A testable claim",
                    "evidence_ref_ids": ["ru1"]}],
        "evidence_refs": [{"id": "ru1", "object_type": "reading_unit", "object_id": "ru1",
                           "evidence_ids": ["ev1"], "document_id": "doc", "snapshot_id": "snap",
                           "source_bundle_id": "bundle", "page_index": 0,
                           "verification_status": "text_extracted_unverified"}],
        "support_relations": [{"claim_id": "cl1", "evidence_ref_id": "ru1",
                               "relation": "direct_source", "explanation": "Author states it"}],
        "evidence_gaps": []})
    validate_source_references(valid, _bundle())
    bad = valid.model_copy(deep=True)
    bad.evidence_refs[0].snapshot_id = "other"
    with pytest.raises(ValueError, match="snapshot"):
        validate_source_references(bad, _bundle())
    bad = valid.model_copy(deep=True)
    bad.claims[0].evidence_ref_ids = ["missing"]
    with pytest.raises(ValueError, match="missing"):
        validate_source_references(bad, _bundle())
    bad = valid.model_copy(deep=True)
    bad.support_relations = []
    with pytest.raises(ValueError, match="unaccounted claim"):
        validate_source_references(bad, _bundle())
