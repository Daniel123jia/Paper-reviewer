import json

import pytest

from backend.model_runtime import ModelConfigurationError
from backend.p2c_engine import run_p2c
from backend.p2c_schema import CandidateConsolidationResult
from backend.tests.test_p2c_normalizer import fixture_review


def grouping_model(stage, system, user):
    payload = json.loads(user)
    if stage.startswith("pair-"):
        content = {"relations": [{"left_issue_id": a, "right_issue_id": b,
                    "relation": "semantic_duplicate", "rationale": "same source check",
                    "shared_verification_need": "read the same evidence"}
                    for a, b in payload["pairs"]]}
    else:
        content = {"groups": [{"issue_ids": sorted(payload["candidates"]),
                                "rationale": "one scientific question",
                                "central_question": "Do these concerns need the same source check?"}]}
    return {"content": json.dumps(content), "model_identifier": "mock-actual",
            "input_tokens": 100, "output_tokens": 50, "duration_ms": 2}


def test_run_produces_traceable_unverified_tasks_and_valid_schema():
    bundle, plan, profile, mapping, review = fixture_review()
    result = run_p2c(bundle, plan, profile, mapping, review, "u1", "b1", "c1",
                     grouping_model, "requested", p2b_status="limited")
    assert result.execution_status == "limited"
    assert result.inventory.candidate_count == 2
    assert len(result.consolidated_issues) == len(result.verification_tasks) == 1
    issue = result.consolidated_issues[0]
    task = result.verification_tasks[0]
    assert set(issue.source_candidate_ids) == {item.issue_id for item in review.candidates}
    assert set(task.original_candidate_ids) == set(issue.source_candidate_ids)
    assert task.original_document_snapshot["snapshot_id"] == "snap1"
    assert task.source_refs_to_check == issue.original_source_refs
    assert task.central_question == issue.central_question
    assert task.verification_status == issue.verification_status == "unverified"
    assert CandidateConsolidationResult.model_validate(result.model_dump()) == result


def test_no_candidate_run_still_audits_dimensions_without_model_call():
    bundle, plan, profile, mapping, review = fixture_review()
    for dimension in review.dimensions:
        dimension.candidates = []
        dimension.no_issue_explanation = "No issue in inspected sources"
    review.candidates = []
    called = []
    result = run_p2c(bundle, plan, profile, mapping, review, "u1", "b1", "c2",
        lambda *args: called.append(args), "requested", p2b_status="limited")
    assert not called
    assert not result.consolidated_issues and not result.verification_tasks
    assert result.coverage_audit.dimensions[5].no_issue_explanation


def test_invalid_source_and_partial_failed_input_are_rejected():
    bundle, plan, profile, mapping, review = fixture_review()
    review.candidates[0].source_refs[0].source_id = "foreign"
    with pytest.raises(ValueError, match="source"):
        run_p2c(bundle, plan, profile, mapping, review, "u1", "b1", "c3",
                grouping_model, "requested", p2b_status="limited")
    review.candidates[0].source_refs[0].source_id = "ru1"
    with pytest.raises(ValueError, match="complete P2-B"):
        run_p2c(bundle, plan, profile, mapping, review, "u1", "b1", "c3",
                grouping_model, "requested", p2b_status="partial_failed")


def test_invalid_model_output_retains_all_candidates_and_flags_limit():
    bundle, plan, profile, mapping, review = fixture_review()
    result = run_p2c(bundle, plan, profile, mapping, review, "u1", "b1", "c4",
        lambda *args: {"content": "not-json", "model_identifier": "mock",
                       "input_tokens": 20, "output_tokens": 4, "duration_ms": 1},
        "requested", p2b_status="limited")
    assert result.execution_status == "limited"
    assert len(result.lineage) == result.inventory.candidate_count
    assert len(result.consolidated_issues) == result.inventory.candidate_count
    assert result.unknown_token_calls == 0


def test_unsupported_model_central_question_degrades_without_losing_tasks():
    bundle, plan, profile, mapping, review = fixture_review()
    def model(stage, system, user):
        response = grouping_model(stage, system, user)
        if stage == "reconciliation":
            payload = json.loads(response["content"])
            payload["groups"][0]["central_question"] = "Does a novel BLEU 99 result prove the claim?"
            response["content"] = json.dumps(payload)
        return response
    result = run_p2c(bundle, plan, profile, mapping, review, "u1", "b1", "c7",
                     model, "requested", p2b_status="limited")
    issue = result.consolidated_issues[0]
    assert "central_question:unresolved" in result.incomplete_items
    assert "BLEU 99" not in issue.central_question
    assert result.verification_tasks[0].central_question == issue.central_question
    assert all(question in issue.verification_questions for candidate in review.candidates
               for question in candidate.verification_questions)


def test_false_verified_result_cannot_pass_schema():
    bundle, plan, profile, mapping, review = fixture_review()
    result = run_p2c(bundle, plan, profile, mapping, review, "u1", "b1", "c5",
                     grouping_model, "requested", p2b_status="limited")
    raw = result.model_dump()
    raw["consolidated_issues"][0]["verification_status"] = "verified"
    with pytest.raises(ValueError):
        CandidateConsolidationResult.model_validate(raw)


def test_schema_rejects_lost_evidence_and_missing_pair_relation():
    bundle, plan, profile, mapping, review = fixture_review()
    result = run_p2c(bundle, plan, profile, mapping, review, "u1", "b1", "c6",
                     grouping_model, "requested", p2b_status="limited")
    raw = result.model_dump()
    raw["consolidated_issues"][0]["original_source_refs"] = []
    with pytest.raises(ValueError, match="source evidence lost"):
        CandidateConsolidationResult.model_validate(raw)
    raw = result.model_dump()
    raw["relations"] = []
    with pytest.raises(ValueError, match="relationship record is incomplete"):
        CandidateConsolidationResult.model_validate(raw)


def test_configuration_change_stops_p2c_without_grouping_retry():
    bundle, plan, profile, mapping, review = fixture_review()
    calls = []

    def changed(*args):
        calls.append(args[0])
        raise ModelConfigurationError(["model_configuration_changed"], ["OPENAI_API_KEY"])

    with pytest.raises(ModelConfigurationError):
        run_p2c(bundle, plan, profile, mapping, review, "u1", "b1", "config-change",
                changed, "requested", p2b_status="limited")
    assert len(calls) == 1
