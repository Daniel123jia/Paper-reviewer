import json

import pytest

from backend.p2a_engine import P2AError, chunk_reading_units, run_p2a


def _bundle():
    return {"bundle_id": "bundle", "document": {"id": "doc", "snapshot_id": "snap"},
            "pages": [{"page_index": 0, "status": "text_extracted", "evidence_ids": ["ev1", "ev2"]}],
            "body": [{"evidence_id": "ev1", "page_index": 0, "text": "Method text", "bbox": [1, 2, 3, 4]},
                     {"evidence_id": "ev2", "page_index": 0, "text": "Result text", "bbox": [1, 5, 3, 6]}],
            "reading_units": [{"id": "ru1", "page_index": 0, "section_id": "s1",
                               "evidence_ids": ["ev1"], "text": "Method text", "bbox": [1, 2, 3, 4],
                               "reading_order_status": "geometric_candidate",
                               "evidence_mapping_status": "exact_block"},
                              {"id": "ru2", "page_index": 0, "section_id": "s1",
                               "evidence_ids": ["ev2"], "text": "Result text", "bbox": [1, 5, 3, 6],
                               "reading_order_status": "geometric_candidate",
                               "evidence_mapping_status": "exact_block"}],
            "sections": [{"id": "s1", "title": "1 Method", "page_index": 0, "evidence_id": "ev1"}],
            "tables": [], "formulas": [], "figures": [], "limitations": ["formula not verified"]}


PLAN = {"paper_type": "AI algorithm research", "research_domain": "machine learning",
        "methodological_paradigm": "empirical algorithm", "applicable_review_dimensions": ["method"],
        "priority_dimensions": ["method"], "excluded_dimensions": [],
        "required_materials": ["method"], "missing_materials": []}
PROFILE = {"research_problem": {"text": "Problem", "evidence_ref_ids": ["ru1"]},
           "research_motivation": None, "research_gap_claimed_by_authors": None,
           "proposed_method": {"text": "Method", "evidence_ref_ids": ["ru1"]},
           "method_components": [], "theoretical_assumptions": [],
           "experimental_setup": [], "datasets_and_metrics": [],
           "main_results": [{"text": "Result", "evidence_ref_ids": ["ru2"]}],
           "author_claimed_contributions": [{"text": "Contribution", "evidence_ref_ids": ["ru1"]}],
           "conclusions": [], "author_stated_limitations": [],
           "unresolved_source_gaps": ["formula not verified"]}
MAP = {"contributions": [{"id": "c1", "title": "Contribution", "description": "Description",
                         "evidence_ref_ids": ["ru1"]}],
       "claims": [{"id": "cl1", "contribution_id": "c1", "statement": "Testable claim",
                   "evidence_ref_ids": ["ru1", "ru2"]}],
       "support_relations": [{"claim_id": "cl1", "evidence_ref_id": "ru1",
                              "relation": "direct_source", "explanation": "Stated by authors"},
                             {"claim_id": "cl1", "evidence_ref_id": "ru2",
                              "relation": "model_interpretation", "explanation": "Relates result"}],
       "evidence_gaps": []}


def _reply(payload):
    return {"content": json.dumps(payload), "model_identifier": "returned-model",
            "input_tokens": 100, "output_tokens": 30, "duration_ms": 12}


def test_pipeline_uses_all_units_and_retries_invalid_source_ids():
    calls = []

    def model_call(stage, system, user):
        calls.append((stage, system, user))
        if stage == "paper-type-planner":
            return _reply(PLAN)
        if stage == "paper-understanding" and sum(item[0] == stage for item in calls) == 1:
            return _reply({**PROFILE, "research_problem": {"text": "invented", "evidence_ref_ids": ["missing"]}})
        if stage == "paper-understanding":
            return _reply(PROFILE)
        return _reply(MAP)

    plan, profile, mapping, traces = run_p2a(_bundle(), "run1", model_call,
                                             requested_model="model-requested", chunk_chars=10000)

    assert [item[0] for item in calls] == ["paper-type-planner", "paper-understanding",
                                          "paper-understanding", "contribution-evidence-mapper"]
    assert "[ru1]" in calls[1][2] and "[ru2]" in calls[1][2]
    assert profile.input_coverage.included_unit_count == 2
    assert profile.input_coverage.excluded_unit_ids == []
    assert profile.execution_status == "limited"
    assert profile.model_identifier == mapping.model_identifier == plan.model_identifier == "returned-model"
    assert mapping.evidence_refs[0].snapshot_id == "snap"
    assert {item.relation for item in mapping.support_relations} == {"direct_source", "model_interpretation"}
    assert [item.status for item in traces] == ["completed", "invalid_output", "completed", "completed"]


def test_chunking_preserves_every_unit_and_failure_is_explicit():
    bundle = _bundle()
    chunks = chunk_reading_units(bundle["reading_units"], max_chars=30)
    assert len(chunks) == 2
    assert [unit["id"] for chunk in chunks for unit in chunk] == ["ru1", "ru2"]

    def broken(stage, system, user):
        if stage == "paper-type-planner":
            return _reply(PLAN)
        raise RuntimeError("upstream unavailable")

    with pytest.raises(P2AError, match="paper-understanding") as failure:
        run_p2a(bundle, "run2", broken, requested_model="model", chunk_chars=10000)
    assert len(failure.value.traces) == 3
    assert failure.value.traces[-1].status == "failed"


def test_long_input_uses_global_synthesis_and_preserves_coverage():
    calls = []

    def model_call(stage, system, user):
        calls.append((stage, user))
        return _reply({"paper-type-planner": PLAN, "paper-understanding": PROFILE,
                       "contribution-evidence-mapper": MAP}[stage])

    bundle = _bundle()
    plan, profile, mapping, traces = run_p2a(bundle, "run3", model_call, "model", chunk_chars=60)
    assert len([item for item in calls if item[0] == "paper-understanding"]) == 3
    assert len([item for item in calls if item[0] == "contribution-evidence-mapper"]) == 3
    assert any("Global synthesis of both parts" in user for _, user in calls)
    assert profile.input_coverage.included_unit_count == 2
    assert mapping.input_coverage.included_unit_count == 2
    assert all(trace.status == "completed" for trace in traces)


def test_four_chunks_merge_hierarchically_with_full_final_coverage():
    bundle = _bundle()
    for number in (3, 4):
        unit = {**bundle["reading_units"][0], "id": f"ru{number}"}
        bundle["reading_units"].append(unit)
    calls = []

    def model_call(stage, system, user):
        calls.append((stage, user))
        return _reply({"paper-type-planner": PLAN, "paper-understanding": PROFILE,
                       "contribution-evidence-mapper": MAP}[stage])

    _, profile, mapping, _ = run_p2a(bundle, "run5", model_call, "model", chunk_chars=60)
    assert len([stage for stage, _ in calls if stage == "paper-understanding"]) == 7
    assert len([stage for stage, _ in calls if stage == "contribution-evidence-mapper"]) == 7
    assert profile.input_coverage.included_unit_count == 4
    assert mapping.input_coverage.included_unit_count == 4


def test_model_error_never_enters_diagnostic_traces():
    secret = "sk-super-secret-marker"

    def model_call(stage, system, user):
        raise RuntimeError(f"provider failure containing {secret}")

    with pytest.raises(P2AError) as failure:
        run_p2a(_bundle(), "run4", model_call, "model")
    assert secret not in str(failure.value)
    assert all(secret not in str(trace) for trace in failure.value.traces)
