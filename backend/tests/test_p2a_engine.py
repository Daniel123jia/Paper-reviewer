import json

import pytest

from backend.model_runtime import ModelConfigurationError
from backend.p2a_engine import BudgetExceeded, P2AError, RunBudget, chunk_reading_units, run_p2a
from backend.p2a_schema import validate_source_references


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


def _aux_reply(stage, user):
    if stage == "source-consistency":
        return _reply({"source_conflicts": []})
    if stage == "contribution-normalizer":
        data = json.loads(user)
        return _reply({"contribution_groups": [list(range(len(data["contributions"])))],
                       "claim_groups": [list(range(len(data["claims"])))],
                       "global_support": []})
    return None


def test_pipeline_uses_all_units_and_retries_invalid_source_ids():
    calls = []

    def model_call(stage, system, user):
        calls.append((stage, system, user))
        if auxiliary := _aux_reply(stage, user):
            return auxiliary
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
                                          "paper-understanding", "contribution-evidence-mapper",
                                          "contribution-normalizer"]
    assert "planning sample only" in calls[0][2]
    assert "[ru1]" in calls[1][2] and "[ru2]" in calls[1][2]
    assert profile.input_coverage.included_unit_count == 2
    assert profile.input_coverage.excluded_unit_ids == []
    assert profile.execution_status == "limited"
    assert profile.model_identifier == mapping.model_identifier == plan.model_identifier == "returned-model"
    assert mapping.evidence_refs[0].snapshot_id == "snap"
    assert {item.relation for item in mapping.support_relations} == {"direct_source", "model_interpretation"}
    assert [item.status for item in traces] == ["completed", "invalid_output", "completed", "completed", "completed"]


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
        if auxiliary := _aux_reply(stage, user):
            return auxiliary
        if stage == "paper-understanding" and "Global synthesis" not in user:
            part_number = sum(item[0] == stage for item in calls)
            return _reply({**PROFILE, "main_results": [{"text": f"Part {part_number} result",
                                                       "evidence_ref_ids": [f"ru{part_number}"]}],
                           "unresolved_source_gaps": [f"Only Part {part_number} supplied"]})
        return _reply({"paper-type-planner": PLAN, "paper-understanding": PROFILE,
                       "contribution-evidence-mapper": MAP}[stage])

    bundle = _bundle()
    plan, profile, mapping, traces = run_p2a(bundle, "run3", model_call, "model", chunk_chars=60)
    assert len([item for item in calls if item[0] == "paper-understanding"]) == 3
    assert len([item for item in calls if item[0] == "contribution-evidence-mapper"]) == 2
    assert any("Global synthesis of all parts" in user for _, user in calls)
    assert profile.input_coverage.included_unit_count == 2
    assert profile.research_problem.provenance == "model_interpretation"
    assert {item.text for item in profile.main_results} == {"Part 1 result", "Part 2 result"}
    assert "formula not verified" in profile.unresolved_source_gaps
    assert not any("Only Part" in gap for gap in profile.unresolved_source_gaps)
    assert mapping.input_coverage.included_unit_count == 2
    map_inputs = [user for stage, user in calls if stage == "contribution-evidence-mapper"]
    assert "Part 1 result" in map_inputs[0] and "Part 2 result" not in map_inputs[0]
    assert "Part 2 result" in map_inputs[1] and "Part 1 result" not in map_inputs[1]
    assert all(trace.status == "completed" for trace in traces)


def test_four_chunks_preserve_full_final_coverage_and_unique_map_ids():
    bundle = _bundle()
    for number in (3, 4):
        unit = {**bundle["reading_units"][0], "id": f"ru{number}"}
        bundle["reading_units"].append(unit)
    calls = []

    def model_call(stage, system, user):
        calls.append((stage, user))
        if auxiliary := _aux_reply(stage, user):
            return auxiliary
        return _reply({"paper-type-planner": PLAN, "paper-understanding": PROFILE,
                       "contribution-evidence-mapper": MAP}[stage])

    _, profile, mapping, _ = run_p2a(bundle, "run5", model_call, "model", chunk_chars=60)
    assert len([stage for stage, _ in calls if stage == "paper-understanding"]) == 5
    assert len([stage for stage, _ in calls if stage == "contribution-evidence-mapper"]) == 4
    assert profile.input_coverage.included_unit_count == 4
    assert mapping.input_coverage.included_unit_count == 4
    assert len(mapping.contributions) == 1
    assert len({item.id for item in mapping.contributions}) == 1


def test_planning_samples_each_available_section():
    bundle = _bundle()
    bundle["reading_units"][1]["section_id"] = "s2"
    bundle["sections"].append({"id": "s2", "title": "2 Results", "page_index": 0,
                               "evidence_id": "ev2"})
    calls = []

    def model_call(stage, system, user):
        calls.append((stage, user))
        if auxiliary := _aux_reply(stage, user):
            return auxiliary
        return _reply({"paper-type-planner": PLAN, "paper-understanding": PROFILE,
                       "contribution-evidence-mapper": MAP}[stage])

    run_p2a(bundle, "run-section-plan", model_call, "model", chunk_chars=60)
    assert "[ru1]" in calls[0][1] and "[ru2]" in calls[0][1]


def test_global_model_failure_keeps_partial_facts_and_marks_gap():
    bundle = _bundle()

    def model_call(stage, system, user):
        if auxiliary := _aux_reply(stage, user):
            return auxiliary
        if stage == "paper-understanding" and "Global synthesis" in user:
            raise RuntimeError("upstream unavailable")
        return _reply({"paper-type-planner": PLAN, "paper-understanding": PROFILE,
                       "contribution-evidence-mapper": MAP}[stage])

    _, profile, mapping, traces = run_p2a(bundle, "run6", model_call, "model", chunk_chars=60)
    assert profile.input_coverage.included_unit_count == 2
    assert profile.main_results[0].evidence_ref_ids == ["ru2"]
    assert any("Global scalar synthesis failed" in gap for gap in profile.unresolved_source_gaps)
    assert len(mapping.contributions) == 1
    assert [trace.status for trace in traces].count("failed") == 2


def test_mapping_splits_source_part_after_repeated_connection_failure():
    class APIConnectionError(RuntimeError):
        pass

    calls = []

    def model_call(stage, system, user):
        calls.append((stage, user))
        if auxiliary := _aux_reply(stage, user):
            return auxiliary
        if stage == "contribution-evidence-mapper" and "[ru1]" in user and "[ru2]" in user:
            raise APIConnectionError("relay dropped connection")
        return _reply({"paper-type-planner": PLAN, "paper-understanding": PROFILE,
                       "contribution-evidence-mapper": MAP}[stage])

    _, _, mapping, traces = run_p2a(_bundle(), "run7", model_call, "model", chunk_chars=10000)
    assert len(mapping.contributions) == 1
    assert mapping.input_coverage.included_unit_count == 2
    assert [trace.status for trace in traces].count("failed") == 2


def test_understanding_splits_source_part_after_repeated_connection_failure():
    class APIConnectionError(RuntimeError):
        pass

    def model_call(stage, system, user):
        if auxiliary := _aux_reply(stage, user):
            return auxiliary
        if (stage == "paper-understanding" and "Global synthesis" not in user and
                "[ru1]" in user and "[ru2]" in user):
            raise APIConnectionError("relay dropped connection")
        return _reply({"paper-type-planner": PLAN, "paper-understanding": PROFILE,
                       "contribution-evidence-mapper": MAP}[stage])

    _, profile, mapping, traces = run_p2a(_bundle(), "run9", model_call, "model", chunk_chars=10000)
    assert profile.input_coverage.included_unit_count == 2
    assert mapping.input_coverage.included_unit_count == 2
    assert [trace.status for trace in traces].count("failed") == 2


def test_model_error_never_enters_diagnostic_traces():
    secret = "private-credential-marker"

    def model_call(stage, system, user):
        raise RuntimeError(f"provider failure containing {secret}")

    with pytest.raises(P2AError) as failure:
        run_p2a(_bundle(), "run4", model_call, "model")
    assert secret not in str(failure.value)
    assert all(secret not in str(trace) for trace in failure.value.traces)


def test_blocked_page_stops_before_model_call():
    bundle = _bundle()
    bundle["pages"].append({"page_index": 1, "status": "needs_ocr", "evidence_ids": []})
    calls = []

    def model_call(stage, system, user):
        calls.append(stage)
        return _reply(PLAN)

    with pytest.raises(P2AError, match="source"):
        run_p2a(bundle, "run8", model_call, "model")
    assert calls == []


def test_global_merge_unifies_paraphrases_and_removes_fragment_only_gap():
    bundle = _bundle()

    def model_call(stage, system, user):
        if stage == "contribution-normalizer":
            data = json.loads(user)
            assert len(data["contributions"]) == 3
            return _reply({"contribution_groups": [[0, 1, 2]],
                           "claim_groups": [[0, 1]], "global_support": [],
                           "resolved_gaps": [{"gap_index": 0, "evidence_ref_id": "ru2"}]})
        if stage == "contribution-evidence-mapper":
            local_id = "ru1" if "[ru1]" in user else "ru2"
            supported = local_id == "ru2"
            return _reply({"contributions": [{"id": "c1", "title": "Method",
                "description": "A method for the same research contribution",
                "evidence_ref_ids": [local_id]}],
                "claims": [{"id": "cl1", "contribution_id": "c1",
                            "statement": "The method handles the relation",
                            "evidence_ref_ids": [local_id]}],
                "support_relations": ([{"claim_id": "cl1", "evidence_ref_id": local_id,
                                       "relation": "direct_source", "explanation": "Result"}]
                                      if supported else []),
                "evidence_gaps": ([] if supported else [{"claim_id": "cl1",
                    "description": "Not in this part", "status": "support_not_found"}])})
        return _reply({"paper-type-planner": PLAN, "paper-understanding": PROFILE}[stage])

    _, profile, mapping, _ = run_p2a(bundle, "merge-run", model_call, "model", chunk_chars=60)
    assert len(mapping.contributions) == len(mapping.claims) == 1
    assert set(mapping.contributions[0].evidence_ref_ids) == {"ru1", "ru2"}
    assert set(mapping.claims[0].evidence_ref_ids) == {"ru1", "ru2"}
    assert mapping.evidence_gaps == []
    assert mapping.support_relations[0].claim_id == mapping.claims[0].id
    validate_source_references(profile, bundle)
    validate_source_references(mapping, bundle)


def _merged_gap_case(gaps_by_part, relations_by_part, resolved_gaps=None):
    bundle = _bundle()

    def model_call(stage, system, user):
        if stage == "contribution-normalizer":
            data = json.loads(user)
            assert len(data["evidence_gaps"]) == sum(len(gaps) for gaps in gaps_by_part)
            return _reply({"contribution_groups": [[0, 1, 2]],
                           "claim_groups": [[0, 1]], "global_support": [],
                           "resolved_gaps": resolved_gaps or []})
        if stage == "contribution-evidence-mapper":
            index = 0 if "[ru1]" in user else 1
            ref = f"ru{index + 1}"
            return _reply({"contributions": [{"id": "c1", "title": "Method",
                "description": "Method", "evidence_ref_ids": [ref]}],
                "claims": [{"id": "cl1", "contribution_id": "c1",
                            "statement": "Claim", "evidence_ref_ids": [ref]}],
                "support_relations": [{"claim_id": "cl1", "evidence_ref_id": ref,
                    "relation": "direct_source", "explanation": "Text supports the claim"}]
                    if relations_by_part[index] else [],
                "evidence_gaps": [{"claim_id": "cl1", **gap} for gap in gaps_by_part[index]]})
        return _reply({"paper-type-planner": PLAN, "paper-understanding": PROFILE}[stage])

    _, _, mapping, _ = run_p2a(bundle, "gap-run", model_call, "model", chunk_chars=60)
    validate_source_references(mapping, bundle)
    return mapping


def test_direct_text_support_does_not_erase_unverified_formula_gap():
    mapping = _merged_gap_case(
        [[{"description": "Equation 4 transcription remains unverified",
           "status": "source_unverified"}], []],
        [False, True])
    assert len(mapping.support_relations) == 1
    assert [(gap.status, gap.description) for gap in mapping.evidence_gaps] == [
        ("source_unverified", "Equation 4 transcription remains unverified")]


def test_duplicate_unresolved_gap_is_kept_once_after_claim_merge():
    gap = {"description": "Table 2 row alignment needs PDF verification",
           "status": "source_unverified"}
    mapping = _merged_gap_case([[gap], [gap]], [False, False])
    assert len(mapping.claims) == 1
    assert [(item.status, item.description) for item in mapping.evidence_gaps] == [
        ("source_unverified", gap["description"])]


def test_distinct_contributions_remain_distinct_after_global_grouping():
    bundle = _bundle()
    order = {"reverse": False}
    profile = {**PROFILE, "author_claimed_contributions": [
        {"text": "New architecture", "evidence_ref_ids": ["ru1"]},
        {"text": "New training objective", "evidence_ref_ids": ["ru2"]}]}

    def model_call(stage, system, user):
        if stage == "contribution-normalizer":
            groups = [[0, 2], [1, 3]]
            claims = [[0], [1]]
            if order["reverse"]:
                groups.reverse()
                claims.reverse()
            return _reply({"contribution_groups": groups,
                           "claim_groups": claims, "global_support": []})
        if stage == "contribution-evidence-mapper":
            first = "[ru1]" in user
            ref = "ru1" if first else "ru2"
            name = "architecture" if first else "training objective"
            return _reply({"contributions": [{"id": "c1", "title": name,
                "description": name, "evidence_ref_ids": [ref]}],
                "claims": [{"id": "cl1", "contribution_id": "c1",
                            "statement": name, "evidence_ref_ids": [ref]}],
                "support_relations": [{"claim_id": "cl1", "evidence_ref_id": ref,
                                       "relation": "direct_source", "explanation": "Source"}],
                "evidence_gaps": []})
        return _reply({"paper-type-planner": PLAN, "paper-understanding": profile}[stage])

    _, result_profile, mapping, _ = run_p2a(bundle, "distinct-run", model_call, "model", chunk_chars=60)
    assert len(mapping.contributions) == len(mapping.claims) == 2
    assert len({item.id for item in mapping.contributions}) == 2
    validate_source_references(result_profile, bundle)
    validate_source_references(mapping, bundle)
    order["reverse"] = True
    _, _, reordered, _ = run_p2a(bundle, "distinct-run-again", model_call, "model", chunk_chars=60)
    assert {item.id for item in reordered.contributions} == {item.id for item in mapping.contributions}
    assert {item.id for item in reordered.claims} == {item.id for item in mapping.claims}


def test_n2_global_source_lead_reclassifies_local_missing_support():
    bundle = _bundle()

    def model_call(stage, system, user):
        if stage == "contribution-normalizer":
            data = json.loads(user)
            assert any(fact["evidence_ref_ids"] == ["ru2"] for fact in data["profile_facts"])
            return _reply({"contribution_groups": [[0, 1, 2]], "claim_groups": [[0, 1]],
                           "global_support": [{"claim_index": 0, "evidence_ref_ids": ["ru2"]}]})
        if stage == "contribution-evidence-mapper":
            ref = "ru1" if "[ru1]" in user else "ru2"
            return _reply({"contributions": [{"id": "c1", "title": "Method",
                 "description": "Method", "evidence_ref_ids": [ref]}],
                 "claims": [{"id": "cl1", "contribution_id": "c1",
                             "statement": "Claim", "evidence_ref_ids": [ref]}],
                 "support_relations": [],
                 "evidence_gaps": [{"claim_id": "cl1", "description": "Not in this part",
                                    "status": "support_not_found"}]})
        return _reply({"paper-type-planner": PLAN, "paper-understanding": PROFILE}[stage])

    _, _, mapping, _ = run_p2a(bundle, "global-lead", model_call, "model", chunk_chars=60)
    assert len(mapping.claims) == 1
    assert mapping.evidence_gaps[0].status == "source_unverified"
    assert mapping.support_relations[0].evidence_ref_id == "ru2"
    assert mapping.support_relations[0].relation == "unverified"
    validate_source_references(mapping, bundle)


def test_ancillary_release_statement_is_excluded_only_by_model_decision():
    bundle = _bundle()
    profile = {**PROFILE, "author_claimed_contributions": [
        {"text": "New method", "evidence_ref_ids": ["ru1"]},
        {"text": "Code is available", "evidence_ref_ids": ["ru2"]}]}

    def model_call(stage, system, user):
        if stage == "contribution-normalizer":
            return _reply({"contribution_groups": [[0, 2]], "excluded_contribution_indices": [1, 3],
                           "claim_groups": [[0], [1]], "global_support": []})
        if stage == "contribution-evidence-mapper":
            first = "[ru1]" in user
            ref = "ru1" if first else "ru2"
            title = "New method" if first else "Code is available"
            return _reply({"contributions": [{"id": "c1", "title": title,
                 "description": title, "evidence_ref_ids": [ref]}],
                 "claims": [{"id": "cl1", "contribution_id": "c1",
                             "statement": title, "evidence_ref_ids": [ref]}],
                 "support_relations": [{"claim_id": "cl1", "evidence_ref_id": ref,
                                        "relation": "direct_source", "explanation": "Source"}],
                 "evidence_gaps": []})
        return _reply({"paper-type-planner": PLAN, "paper-understanding": profile}[stage])

    _, result_profile, mapping, _ = run_p2a(bundle, "exclude-run", model_call, "model", chunk_chars=60)
    assert [item.title for item in mapping.contributions] == ["New method"]
    assert len(mapping.claims) == 1
    assert len(result_profile.author_claimed_contributions) == 2
    validate_source_references(mapping, bundle)


@pytest.mark.parametrize("same_configuration", [True, False])
def test_numeric_discrepancy_requires_same_experiment_context(same_configuration):
    bundle = _bundle()
    bundle["reading_units"][0]["text"] = "Model A, dataset X, EM 79.1 and F1 86.9."
    other_model = "A" if same_configuration else "B"
    bundle["reading_units"][1]["text"] = f"Model {other_model}, dataset X, EM 70.4 and F1 79.8."

    def model_call(stage, system, user):
        if stage == "source-consistency":
            excerpts = json.loads(user)["source_excerpts"]
            assert {item["id"] for item in excerpts} == {"ru1", "ru2"}
            conflict = {"status": "unresolved_source_conflict", "topic": "SQuAD scores",
                        "configuration": "Model A on dataset X, EM and F1",
                        "left_statement": "EM 79.1 / F1 86.9",
                        "right_statement": "EM 70.4 / F1 79.8",
                        "left_evidence_ref_ids": ["ru1"], "right_evidence_ref_ids": ["ru2"],
                        "verification_question": "Check model and dataset configuration."}
            return _reply({"source_conflicts": [conflict] if same_configuration else []})
        if auxiliary := _aux_reply(stage, user):
            return auxiliary
        return _reply({"paper-type-planner": PLAN, "paper-understanding": PROFILE,
                       "contribution-evidence-mapper": MAP}[stage])

    _, profile, mapping, _ = run_p2a(bundle, "conflict-run", model_call, "model", chunk_chars=10000)
    assert len(profile.source_conflicts) == int(same_configuration)
    if same_configuration:
        assert profile.source_conflicts[0].left_evidence_ref_ids == ["ru1"]
        assert profile.source_conflicts[0].right_evidence_ref_ids == ["ru2"]
    validate_source_references(profile, bundle)
    validate_source_references(mapping, bundle)


def test_budget_stops_before_unaffordable_call_and_tracks_unknown_usage():
    budget = RunBudget(max_calls=1)
    calls = []

    def model_call(stage, system, user):
        calls.append(stage)
        return _reply(PLAN)

    with pytest.raises(BudgetExceeded, match="budget_exceeded"):
        run_p2a(_bundle(), "budget-run", model_call, "model", budget=budget)
    assert calls == ["paper-type-planner"]
    assert budget.calls == 1
    assert budget.reserved_input_tokens > 0


def test_failed_call_reserves_unknown_tokens_in_budget():
    budget = RunBudget(max_calls=1)
    def unavailable(stage, system, user):
        raise RuntimeError("provider disconnected")
    with pytest.raises(RuntimeError):
        budget.call("paper-understanding", "system", "source text", unavailable)
    assert budget.unknown_usage_calls == 1
    assert budget.reserved_input_tokens > 0
    assert budget.reserved_output_tokens == 4096


def test_configuration_change_stops_p2a_without_content_retry():
    calls = []

    def changed(*args):
        calls.append(args[0])
        raise ModelConfigurationError(["model_configuration_changed"], ["OPENAI_MODEL"])

    with pytest.raises(ModelConfigurationError):
        run_p2a(_bundle(), "configuration-change", changed, "model")
    assert calls == ["paper-type-planner"]
