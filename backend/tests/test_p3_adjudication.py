import pytest

from backend.p3_adjudication import (AdjudicationProposal, adjudicate,
                                     validate_adjudication_proposal)
from backend.p3_retrieval import CounterEvidenceChallenge
from backend.p3_schema import CounterEvidenceSearch, FactAssessment
from backend.tests.test_p3_sources import frozen_case
from backend.tests.test_p3_technical import observation, source


def context(tmp_path):
    *_, consolidation, _ = frozen_case(tmp_path)
    task = consolidation.verification_tasks[0]
    issue = consolidation.consolidated_issues[0]
    search = CounterEvidenceSearch(verification_task_id=task.verification_task_id,
        indexed_unit_count=2, selected_source_ids=["ru1", "ru2"],
        truncated_source_ids=[], source_quality_limits=[])
    facts = [FactAssessment(original_observed_fact=fact, source_ids=["ru1"],
        alignment="paraphrase", statement_type="author_claim", explanation="Source phrase")
        for fact in issue.observed_facts]
    sources = [source("ru1", "Authors state a mechanism"),
               source("ru2", "Authors provide an ablation")]
    observations = [observation(item.source_id, item.relevant_excerpt,
                                task.verification_task_id) for item in sources]
    challenge = CounterEvidenceChallenge(supporting_source_ids=["ru1"],
        contradicting_source_ids=["ru2"], author_answer_source_ids=["ru2"],
        author_answer_assessment="The authors report the relevant ablation",
        unresolved_questions=[], source_interpretation="same method claim",
        source_limitations=[])
    return task, issue, search, facts, sources, observations, challenge


def proposal(verdict, subchecks, revised=None):
    return AdjudicationProposal.model_validate({"subchecks": subchecks,
        "proposed_verdict": verdict, "revised_question": revised,
        "verdict_rationale": "Based on the cited source and alternative evidence",
        "remaining_uncertainty": [], "provisional_impact": "major_candidate",
        "impact_rationale": "Could affect a core claim", "evidence_level": "model_interpretation"})


def check(question, status, support=(), contradict=()):
    return {"question": question, "check_type": "experimental_support",
            "method": "PDF text and author-answer comparison",
            "supporting_refs": list(support), "contradicting_refs": list(contradict),
            "tool_result_ids": [], "proposed_status": status,
            "explanation": "Located the cited statements", "limitations": []}


def test_author_ablation_can_refute_missing_experiment_candidate(tmp_path):
    task, issue, search, facts, sources, observations, challenge = context(tmp_path)
    model = proposal("concern_refuted", [check("Is there an ablation?", "contradicted",
                                                 contradict=["ru2"])])
    result = adjudicate(task, issue, model, facts, challenge, search,
                        observations, sources, [], True)
    assert result.verdict == "concern_refuted"
    assert result.report_eligibility == "not_reportable"
    assert result.original_candidate_ids == issue.source_candidate_ids
    assert "ru2" in result.counterevidence
    assert result.author_explanations


def test_mixed_issue_keeps_parent_and_independent_subcheck_results(tmp_path):
    task, issue, search, facts, sources, observations, challenge = context(tmp_path)
    model = proposal("concern_narrowed", [
        check("Does the mechanism claim need support?", "supported", support=["ru1"]),
        check("Is the component ablation missing?", "contradicted", contradict=["ru2"])],
        revised="Does the evidence isolate the mechanism beyond the existing ablation?")
    result = adjudicate(task, issue, model, facts, challenge, search,
                        observations, sources, [], True)
    assert result.verdict == "concern_narrowed"
    assert result.revised_question
    assert [item.status for item in result.subchecks] == ["supported", "contradicted"]
    assert all(item.parent_verification_task_id == task.verification_task_id
               for item in result.subchecks)


def test_pdf_text_mismatch_and_absence_claim_cannot_be_supported(tmp_path):
    task, issue, search, facts, sources, observations, challenge = context(tmp_path)
    sources[0].source_verification_status = "pdf_text_mismatch"
    model = proposal("concern_supported", [check("Is an ablation missing?", "supported",
                                                  support=["ru1"])])
    result = adjudicate(task, issue, model, facts, challenge, search,
                        observations, sources, [], True)
    assert result.verdict == "inconclusive"
    assert result.subchecks[0].status == "inconclusive"
    assert result.report_eligibility == "requires_human_review"


def test_located_unrelated_text_does_not_support_observed_fact(tmp_path):
    task, issue, search, facts, sources, observations, challenge = context(tmp_path)
    observations[0].observed_statement = "The paper discusses a dataset split."
    model = proposal("concern_supported", [check("Does the claim need support?", "supported",
                                                  support=["ru1"])])
    result = adjudicate(task, issue, model, facts, challenge, search,
                        observations, sources, [], True)
    assert result.verdict == "inconclusive"
    assert result.subchecks[0].status == "inconclusive"


def test_unrelated_counterevidence_cannot_refute_issue(tmp_path):
    task, issue, search, facts, sources, observations, challenge = context(tmp_path)
    observations[1].observed_statement = "The authors list dataset sizes."
    model = proposal("concern_refuted", [check("Is there an ablation?", "contradicted",
                                                 contradict=["ru2"])])
    result = adjudicate(task, issue, model, facts, challenge, search,
                        observations, sources, [], True)
    assert result.verdict == "inconclusive"
    assert result.subchecks[0].status == "inconclusive"


def test_missing_pdf_blocks_model_proposed_support(tmp_path):
    task, issue, search, facts, sources, observations, challenge = context(tmp_path)
    model = proposal("concern_supported", [check("Does the claim need support?", "supported",
                                                  support=["ru1"])])
    result = adjudicate(task, issue, model, facts, challenge, search,
                        observations, sources, [], False)
    assert result.verdict == "blocked"
    assert result.provisional_impact == "undetermined"


def test_model_cannot_invent_source_or_tool_result(tmp_path):
    task, issue, search, facts, sources, observations, challenge = context(tmp_path)
    raw = proposal("concern_supported", [check("Question?", "supported",
                                               support=["foreign"])]).model_dump()
    with pytest.raises(ValueError, match="unobserved"):
        validate_adjudication_proposal(raw, {"ru1", "ru2"}, set())
    raw["subchecks"][0]["supporting_refs"] = ["ru1"]
    raw["subchecks"][0]["tool_result_ids"] = ["imaginary_tool"]
    with pytest.raises(ValueError, match="unexecuted"):
        validate_adjudication_proposal(raw, {"ru1", "ru2"}, set())
