import json

import pytest

from backend.p2a_schema import (ClaimEvidenceMap, PaperProfile, ReviewPlan,
                                 source_reference_index)
from backend.p2a_engine import RunBudget
from backend.p2b_context import assemble_context
from backend.p2b_engine import run_p2b, validate_understanding_snapshot


def inputs():
    bundle = {"bundle_id": "sb1", "document": {"id": "doc1", "snapshot_id": "snap1"},
              "pages": [{"page_index": 0, "status": "text_extracted", "evidence_ids": ["ev1", "ev2"]}],
              "body": [{"evidence_id": "ev1", "page_index": 0, "text": "Method and novelty",
                        "bbox": [1, 2, 3, 4]},
                       {"evidence_id": "ev2", "page_index": 0, "text": "Experiment result",
                        "bbox": [1, 5, 3, 6]}],
              "reading_units": [{"id": "ru1", "page_index": 0, "section_id": "s1",
                                 "evidence_ids": ["ev1"], "text": "Method and novelty",
                                 "reading_order_status": "geometric_candidate",
                                 "evidence_mapping_status": "exact_block"},
                                {"id": "ru2", "page_index": 0, "section_id": "s1",
                                 "evidence_ids": ["ev2"], "text": "Experiment result",
                                 "reading_order_status": "geometric_candidate",
                                 "evidence_mapping_status": "exact_block"}],
              "sections": [{"id": "s1", "title": "Method", "page_index": 0,
                            "evidence_id": "ev1"}],
              "tables": [], "formulas": [], "figures": [], "limitations": ["formula unverified"]}
    refs = source_reference_index(bundle)
    coverage = {"total_unit_count": 2, "included_unit_count": 2,
                "excluded_unit_ids": [], "source_quality_status": "needs_review",
                "limitations": ["formula unverified"]}
    metadata = {"document_id": "doc1", "source_bundle_id": "sb1", "review_run_id": "u1",
                "prompt_version": "p2a/v1", "schema_version": "1.0",
                "model_identifier": "model", "input_coverage": coverage,
                "execution_status": "limited"}
    plan = ReviewPlan.model_validate({**metadata, "paper_type": "AI algorithm research",
        "research_domain": "machine learning", "methodological_paradigm": "empirical algorithm",
        "applicable_review_dimensions": ["methods", "experiments"],
        "priority_dimensions": ["experiments"], "excluded_dimensions": [],
        "required_materials": ["methods"], "missing_materials": []})
    profile = PaperProfile.model_validate({**metadata,
        "research_problem": {"text": "Problem", "evidence_ref_ids": ["ru1"]},
        "research_motivation": None, "research_gap_claimed_by_authors": None,
        "proposed_method": {"text": "Method", "evidence_ref_ids": ["ru1"]},
        "method_components": [], "theoretical_assumptions": [], "experimental_setup": [],
        "datasets_and_metrics": [],
        "main_results": [{"text": "Result", "evidence_ref_ids": ["ru2"]}],
        "author_claimed_contributions": [], "conclusions": [], "author_stated_limitations": [],
        "evidence_refs": [refs["ru1"], refs["ru2"]],
        "unresolved_source_gaps": ["formula unverified"]})
    mapping = ClaimEvidenceMap.model_validate({**metadata,
        "contributions": [{"id": "c1", "title": "Contribution", "description": "Method",
                           "evidence_ref_ids": ["ru1"]}],
        "claims": [{"id": "cl1", "contribution_id": "c1", "statement": "Method works",
                    "evidence_ref_ids": ["ru1", "ru2"]}],
        "evidence_refs": [refs["ru1"], refs["ru2"]],
        "support_relations": [{"claim_id": "cl1", "evidence_ref_id": "ru1",
                               "relation": "direct_source", "explanation": "Author claim"}],
        "evidence_gaps": [{"claim_id": "cl1", "description": "Equation needs PDF check",
                           "status": "source_unverified"}]})
    return bundle, plan, profile, mapping


def reply(candidates=None):
    return {"content": json.dumps({"inspection_focus": ["claim support"],
        "inspected_claim_ids": ["cl1"], "inspected_source_refs": ["ru1", "ru2"],
        "limitations": ["formula not verified"], "candidates": candidates or [],
        "no_issue_explanation": "No specific issue in supplied evidence" if not candidates else None}),
        "model_identifier": "actual-model", "input_tokens": 100, "output_tokens": 50,
        "duration_ms": 10}


def candidate(source_id="ru1", title="Specific concern"):
    return {"issue_type": "claim_support", "title": title,
            "observed_fact": "Authors state a mechanism", "suspected_problem": "Evidence may not isolate it",
            "why_it_matters": "The contribution claim may be narrower", "claim_ids": ["cl1"],
            "source_refs": [{"source_id": source_id, "evidence_role": "observed_statement"}],
            "possible_counterevidence": [], "verification_questions": ["Is there an ablation?"],
            "potential_impact": "major_candidate", "verification_status": "unverified",
            "source_limitations": [], "external_verification_required": False}


def test_snapshot_rejects_mixed_bundle_and_run():
    bundle, plan, profile, mapping = inputs()
    validate_understanding_snapshot(bundle, plan, profile, mapping, "u1", "completed")
    with pytest.raises(ValueError, match="understanding run"):
        validate_understanding_snapshot(bundle, plan, profile, mapping, "other", "completed")
    with pytest.raises(ValueError, match="completed"):
        validate_understanding_snapshot(bundle, plan, profile, mapping, "u1", "running")
    profile.evidence_refs[0].snapshot_id = "foreign"
    with pytest.raises(ValueError, match="snapshot"):
        validate_understanding_snapshot(bundle, plan, profile, mapping, "u1", "completed")


def test_snapshot_accepts_p2a_schema_11_and_rejects_foreign_mapping():
    bundle, plan, profile, mapping = inputs()
    for item in (plan, profile, mapping):
        item.schema_version = "1.1"
    validate_understanding_snapshot(bundle, plan, profile, mapping, "u1", "completed")
    mapping.source_bundle_id = "other"
    with pytest.raises(ValueError, match="Source Bundle"):
        validate_understanding_snapshot(bundle, plan, profile, mapping, "u1", "completed")


def test_context_keeps_source_text_neighbors_and_visible_limits():
    bundle, plan, profile, mapping = inputs()
    pack = assemble_context("D2", bundle, plan, profile, mapping, max_chars=1000)
    assert {"ru1", "ru2"}.issubset(set(pack.source_ref_ids))
    assert {"ev1", "ev2", "s1"}.issubset(set(pack.source_ref_ids))
    assert "Method and novelty" in pack.text and "Experiment result" in pack.text
    assert pack.omitted_unit_ids == []
    limited = assemble_context("D2", bundle, plan, profile, mapping, max_chars=10)
    assert limited.omitted_unit_ids
    assert limited.coverage_status == "partial"


def test_six_dimensions_and_multiple_candidates_are_preserved():
    bundle, plan, profile, mapping = inputs()
    calls = []
    def model(stage, system, user):
        calls.append(stage)
        return reply([candidate(title=f"Concern {i}") for i in range(4)] if stage == "D3" else [])
    result = run_p2b(bundle, plan, profile, mapping, "u1", "p1", model, "requested")
    assert calls == ["D1", "D2", "D3", "D4", "D5", "D6"]
    assert len(result.dimensions) == 6
    assert len(result.candidates) == 4
    assert result.dimensions[0].no_issue_explanation
    assert len({item.issue_id for item in result.candidates}) == 4
    assert all(item.verification_status == "unverified" for item in result.candidates)


def test_invalid_candidate_source_fails_one_dimension_without_losing_others():
    bundle, plan, profile, mapping = inputs()
    def model(stage, system, user):
        return reply([candidate(source_id="foreign")]) if stage == "D2" else reply()
    result = run_p2b(bundle, plan, profile, mapping, "u1", "p2", model, "requested")
    assert result.execution_status == "partial_failed"
    assert result.dimensions[1].execution_status == "failed"
    assert result.dimensions[0].execution_status in {"completed", "limited"}
    assert result.dimensions[-1].execution_status in {"completed", "limited"}
    assert not result.candidates


def test_theoretical_paper_skips_experiment_dimension_with_reason():
    bundle, plan, profile, mapping = inputs()
    plan.methodological_paradigm = "pure theory"
    plan.excluded_dimensions = ["D3"]
    result = run_p2b(bundle, plan, profile, mapping, "u1", "p3",
                     lambda stage, system, user: reply(), "requested")
    assert result.dimensions[2].applicability_status == "not_applicable"
    assert result.dimensions[2].applicability_reason
    assert result.dimensions[2].candidates == []


def test_budget_exhaustion_retains_prior_dimension_and_unknown_usage():
    bundle, plan, profile, mapping = inputs()
    def model(stage, system, user):
        response = reply()
        response["input_tokens"] = None
        response["output_tokens"] = None
        return response
    result = run_p2b(bundle, plan, profile, mapping, "u1", "p4", model, "requested",
                     budget=RunBudget(max_calls=1))
    assert result.execution_status == "budget_exceeded"
    assert result.dimensions[0].execution_status == "limited"
    assert result.dimensions[1].execution_status == "budget_exceeded"
    assert result.call_count == 1 and result.unknown_token_calls == 1
    assert result.input_tokens is None and result.output_tokens is None


def test_unreadable_source_blocks_reviews_without_inventing_defect():
    bundle, plan, profile, mapping = inputs()
    bundle["pages"][0]["status"] = "needs_ocr"
    calls = []
    result = run_p2b(bundle, plan, profile, mapping, "u1", "p5",
                     lambda stage, system, user: calls.append(stage), "requested")
    assert not calls and not result.candidates
    assert all(item.coverage_status == "blocked" for item in result.dimensions)
    assert result.execution_status == "limited"
