import json

import pytest

from backend.p2a_engine import RunBudget
from backend.p2c_engine import run_p2c
from backend.p3_engine import run_p3, validate_p3_result
from backend.p3_schema import ToolCheck
from backend.tests.test_p3_sources import frozen_case


def model(stage, system, user):
    payload = json.loads(user)
    source_id = "ru1"
    if stage.endswith(":source_observation"):
        source_id = payload["source_observations"][0]["related_source_ref_ids"][0]
        content = {"assessments": [{"original_observed_fact": fact,
            "source_ids": [source_id], "alignment": "paraphrase",
            "statement_type": "author_claim", "explanation": "possible paraphrase",
            "evidence_level": "model_interpretation"}
            for fact in payload["original_facts"]]}
    elif stage.endswith(":counterevidence"):
        source_id = payload["task"]["source_refs_to_check"][0]["source_id"]
        content = {"supporting_source_ids": [source_id],
            "contradicting_source_ids": [], "author_answer_source_ids": [],
            "author_answer_assessment": "Not established from selected material",
            "unresolved_questions": [], "source_interpretation": "Limited",
            "source_limitations": [], "evidence_level": "model_interpretation"}
    elif stage.endswith(":technical"):
        content = {"metric_records": [], "scalar_formula_checks": [],
                   "unavailable_checks": [], "evidence_level": "model_proposal"}
    else:
        source_id = payload["task"]["source_refs_to_check"][0]["source_id"]
        content = {"subchecks": [{"question": "Does the source support the concern?",
            "check_type": "source_support", "method": "PDF and counterevidence check",
            "supporting_refs": [source_id], "contradicting_refs": [],
            "tool_result_ids": [], "proposed_status": "supported",
            "explanation": "Model interpretation", "limitations": []}],
            "proposed_verdict": "concern_supported", "revised_question": None,
            "verdict_rationale": "Possibly worth asking", "remaining_uncertainty": [],
            "provisional_impact": "major_candidate", "impact_rationale": "Method claim",
            "evidence_level": "model_interpretation"}
    return {"content": json.dumps(content), "model_identifier": "actual-mock",
            "input_tokens": 100, "output_tokens": 40, "duration_ms": 2}


def distinct_grouping(stage, system, user):
    payload = json.loads(user)
    if stage.startswith("pair-"):
        a, b = payload["pairs"][0]
        content = {"relations": [{"left_issue_id": a, "right_issue_id": b,
            "relation": "distinct", "rationale": "different concerns"}]}
    else:
        content = {"groups": [{"issue_ids": [issue_id],
            "rationale": "separate concern", "central_question":
            item["verification_questions"][0]}
            for issue_id, item in payload["candidates"].items()]}
    return {"content": json.dumps(content), "model_identifier": "mock",
            "input_tokens": 10, "output_tokens": 10, "duration_ms": 1}


def test_p3_covers_every_task_without_promoting_model_interpretation(tmp_path):
    bundle, plan, profile, mapping, review, consolidation, pdf_path = frozen_case(tmp_path)
    traces = []
    result = run_p3(bundle, plan, profile, mapping, review, consolidation, pdf_path,
                    "v1", model, "requested", on_trace=traces.append)
    assert result.pdf_identity_status == "sha256_matched"
    assert len(result.adjudications) == len(consolidation.verification_tasks)
    assert result.call_count == len(traces) == 3
    assert result.adjudications[0].verdict == "inconclusive"
    assert result.adjudications[0].original_candidate_ids == \
        consolidation.consolidated_issues[0].source_candidate_ids
    assert result.adjudications[0].human_review_status == "pending"
    validate_p3_result(result, consolidation, bundle)


def test_missing_pdf_blocks_all_tasks_and_makes_no_model_calls(tmp_path):
    bundle, plan, profile, mapping, review, consolidation, _ = frozen_case(tmp_path)
    result = run_p3(bundle, plan, profile, mapping, review, consolidation,
                    tmp_path / "not-here.pdf", "v2",
                    lambda *args: (_ for _ in ()).throw(AssertionError("model must not run")),
                    "requested")
    assert result.pdf_identity_status == "pdf_unavailable"
    assert result.call_count == 0
    assert all(item.verdict == "blocked" for item in result.adjudications)
    assert result.uncovered_tasks == [item.verification_task_id for item in
                                      consolidation.verification_tasks]


def test_invalid_json_and_budget_limit_preserve_task_lineage(tmp_path):
    bundle, plan, profile, mapping, review, consolidation, pdf_path = frozen_case(tmp_path)
    bad = run_p3(bundle, plan, profile, mapping, review, consolidation,
        pdf_path, "v3", lambda *args: {"content": "not-json",
        "model_identifier": "mock", "input_tokens": None,
        "output_tokens": None, "duration_ms": 1}, "requested")
    assert bad.execution_status == "limited"
    assert bad.unknown_token_calls == bad.call_count > 0
    assert len(bad.adjudications) == len(consolidation.verification_tasks)
    assert bad.uncovered_tasks
    limited = run_p3(bundle, plan, profile, mapping, review, consolidation,
        pdf_path, "v4", model, "requested", budget=RunBudget(max_calls=0))
    assert limited.execution_status == "budget_exceeded"
    assert limited.call_count == 0
    assert len(limited.adjudications) == len(consolidation.verification_tasks)


def test_one_model_failure_does_not_discard_other_completed_tasks(tmp_path):
    bundle, plan, profile, mapping, review, _, pdf_path = frozen_case(tmp_path)
    consolidation = run_p2c(bundle, plan, profile, mapping, review,
        "u1", "b1", "c2", distinct_grouping, "mock", "limited")
    assert len(consolidation.verification_tasks) == 2
    failed_id = consolidation.verification_tasks[0].verification_task_id
    def partial_model(stage, system, user):
        if stage.startswith(failed_id):
            return {"content": "invalid JSON", "model_identifier": "mock",
                    "input_tokens": 2, "output_tokens": 2, "duration_ms": 1}
        return model(stage, system, user)
    result = run_p3(bundle, plan, profile, mapping, review, consolidation,
                    pdf_path, "v5", partial_model, "requested")
    assert len(result.adjudications) == 2
    assert result.adjudications[0].verification_task_id == failed_id
    assert result.adjudications[1].verification_task_id != failed_id
    assert result.uncovered_tasks == [failed_id]
    assert result.adjudications[1].fact_assessments


def test_result_rejects_observation_and_tool_borrowed_from_another_task(tmp_path):
    bundle, plan, profile, mapping, review, _, pdf_path = frozen_case(tmp_path)
    consolidation = run_p2c(bundle, plan, profile, mapping, review,
        "u1", "b1", "c2", distinct_grouping, "mock", "limited")
    result = run_p3(bundle, plan, profile, mapping, review, consolidation,
                    pdf_path, "v6", model, "requested")
    assert len(result.adjudications) == 2
    first_id = result.adjudications[0].verification_task_id
    second = result.adjudications[1]
    first_observation = next(item for item in result.observations
                             if item.verification_task_id == first_id)
    borrowed_observation = result.model_copy(deep=True)
    borrowed_observation.adjudications[1].verified_observation_ids.append(
        first_observation.observation_id)
    with pytest.raises(ValueError, match="Observation|observation"):
        validate_p3_result(borrowed_observation, consolidation, bundle)

    borrowed_tool = result.model_copy(deep=True)
    check = ToolCheck(tool_result_id="tool_from_first", verification_task_id=first_id,
        check_type="numeric", method="literal check", input_refs=[], input_values={},
        result={"value": "1"}, status="completed", limitations=[])
    borrowed_tool.tool_results.append(check)
    borrowed_tool.adjudications[1].executed_tool_result_ids.append(check.tool_result_id)
    with pytest.raises(ValueError, match="Tool|tool"):
        validate_p3_result(borrowed_tool, consolidation, bundle)

    borrowed_subcheck_tool = result.model_copy(deep=True)
    borrowed_subcheck_tool.tool_results.append(check)
    borrowed_subcheck_tool.adjudications[1].subchecks[0].tool_result_ids.append(check.tool_result_id)
    with pytest.raises(ValueError, match="Tool|tool"):
        validate_p3_result(borrowed_subcheck_tool, consolidation, bundle)
