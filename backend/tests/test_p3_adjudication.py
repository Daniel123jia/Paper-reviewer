import pytest

from backend.p3_adjudication import (AdjudicationProposal, _counter_matches_question, adjudicate,
                                     validate_adjudication_proposal)
from backend.p3_retrieval import CounterEvidenceChallenge
from backend.p3_schema import CounterEvidenceSearch, FactAssessment, ToolCheck
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


def specific_context(tmp_path):
    task, issue, search, facts, sources, observations, challenge = context(tmp_path)
    issue.title = "Key-side relative-position mechanism"
    issue.central_question = "Does the key-side relative-position mechanism have a matching ablation?"
    issue.suspected_problem = "The key-side relative-position mechanism may lack an isolated ablation"
    issue.observed_facts = ["Authors describe the key-side relative-position mechanism"]
    facts[0].original_observed_fact = issue.observed_facts[0]
    sources[0].relevant_excerpt = "Authors describe the key-side relative-position mechanism"
    observations[0].observed_statement = sources[0].relevant_excerpt
    sources[1].relevant_excerpt = "Authors report a value-side relative-position ablation"
    observations[1].observed_statement = sources[1].relevant_excerpt
    return task, issue, search, facts, sources, observations, challenge


def test_keyword_overlap_from_different_component_cannot_refute_or_support(tmp_path):
    task, issue, search, facts, sources, observations, challenge = specific_context(tmp_path)
    question = "Does a key-side relative-position ablation test the mechanism?"
    refute = proposal("concern_refuted", [check(question, "contradicted", contradict=["ru2"])])
    result = adjudicate(task, issue, refute, facts, challenge, search,
                        observations, sources, [], True)
    assert result.verdict == "inconclusive"
    assert result.subchecks[0].status == "inconclusive"
    support = proposal("concern_supported", [check(question, "supported", support=["ru2"])])
    result = adjudicate(task, issue, support, facts, challenge, search,
                        observations, sources, [], True)
    assert result.verdict == "inconclusive"
    assert result.subchecks[0].status == "inconclusive"


@pytest.mark.parametrize(("question", "counter_text"), [
    ("Does the SQuAD EM key-side ablation answer the result?",
     "A GLUE F1 key-side ablation is reported"),
    ("Does the base key-side ablation answer the result?",
     "A big key-side ablation is reported"),
])
def test_other_metric_or_configuration_is_not_matching_counterevidence(
        tmp_path, question, counter_text):
    _, _, _, _, _, observations, _ = specific_context(tmp_path)
    observations[1].observed_statement = counter_text
    assert not _counter_matches_question(question, ["ru2"], observations)


def test_unrelated_sources_cannot_pool_terms_to_fake_one_counterexample(tmp_path):
    _, _, _, _, _, observations, _ = specific_context(tmp_path)
    observations[0].observed_statement = "A SQuAD EM score is reported"
    observations[1].observed_statement = "A key-side relative-position ablation is reported"
    assert not _counter_matches_question(
        "Does the SQuAD EM key-side relative-position ablation answer this?",
        ["ru1", "ru2"], observations)


@pytest.mark.parametrize("revised", [
    "Did the key-side relative-position method improve BLEU by 99.9 points?",
    "Did the key-side relative-position method improve CIFAR-10 with EfficientNet?",
    "Did the key-side relative-position mechanism use randomized clinical trial data?",
    "Does the paper assess privacy leakage in medical records?",
])
def test_invented_or_unrelated_revised_question_is_not_accepted(tmp_path, revised):
    task, issue, search, facts, sources, observations, challenge = specific_context(tmp_path)
    narrowed = proposal("concern_narrowed", [check(
        "Does the key-side relative-position mechanism have an ablation?",
        "supported", support=["ru1"])], revised=revised)
    result = adjudicate(task, issue, narrowed, facts, challenge, search,
                        observations, sources, [], True)
    assert result.verdict == "inconclusive"
    assert result.revised_question is None
    assert result.original_central_question == issue.central_question
    assert result.subchecks


def test_visual_table_dependency_requires_human_review_without_losing_narrowed_question(tmp_path):
    task, issue, search, facts, sources, observations, challenge = specific_context(tmp_path)
    table = source("table2", "Table 2: key-side relative-position BLEU 79.1",
                   kind="table", status="visual_check_required")
    sources.append(table)
    task.counterevidence_refs_to_check.append(
        task.source_refs_to_check[0].model_copy(update={"source_id": "table2"}))
    observations.append(observation("table2", table.relevant_excerpt,
                                    task.verification_task_id))
    observations[-1].source_check_status = "visual_check_required"
    narrowed = proposal("concern_narrowed", [check(
        "Does the key-side relative-position mechanism have an ablation?",
        "supported", support=["ru1"])],
        revised="Can Table 2's key-side relative-position BLEU 79.1 be clarified?")
    result = adjudicate(task, issue, narrowed, facts, challenge, search,
                        observations, sources, [], True)
    assert result.verdict == "concern_narrowed"
    assert result.report_eligibility == "requires_human_review"
    assert result.revised_question == narrowed.revised_question


def test_secondary_unavailable_check_does_not_block_grounded_clarification(tmp_path):
    task, issue, search, facts, sources, observations, challenge = specific_context(tmp_path)
    blocked = ToolCheck(tool_result_id="external1", verification_task_id=task.verification_task_id,
        check_type="external", method="not supplied", input_refs=[], input_values={},
        result={"status": "external_check_unavailable"}, status="blocked", limitations=[])
    narrowed = proposal("concern_narrowed", [check(
        "Does the key-side relative-position mechanism have an ablation?",
        "supported", support=["ru1"])],
        revised="Could the key-side relative-position mechanism be clarified?")
    result = adjudicate(task, issue, narrowed, facts, challenge, search,
                        observations, sources, [blocked], True)
    assert result.verdict == "concern_narrowed"
    assert result.report_eligibility == "eligible_with_qualification"


def test_unchecked_external_comparison_is_a_critical_reporting_dependency(tmp_path):
    task, issue, search, facts, sources, observations, challenge = specific_context(tmp_path)
    task.external_verification_required = True
    sources[0].relevant_excerpt += " and contrast it with prior representations"
    observations[0].observed_statement = sources[0].relevant_excerpt
    blocked = ToolCheck(tool_result_id="external2", verification_task_id=task.verification_task_id,
        check_type="external", method="not supplied", input_refs=[], input_values={},
        result={"status": "external_check_unavailable"}, status="blocked", limitations=[])
    narrowed = proposal("concern_narrowed", [check(
        "Does the key-side relative-position mechanism have support?",
        "supported", support=["ru1"])],
        revised="Could the key-side relative-position contrast with prior representations be clarified?")
    result = adjudicate(task, issue, narrowed, facts, challenge, search,
                        observations, sources, [blocked], True)
    assert result.verdict == "concern_narrowed"
    assert result.report_eligibility == "requires_human_review"

    completed_other = ToolCheck(tool_result_id="external3",
        verification_task_id=task.verification_task_id, check_type="external",
        method="different source checked", input_refs=[], input_values={},
        result={"status": "checked"}, status="completed", limitations=[])
    partially_checked = adjudicate(task, issue, narrowed, facts, challenge, search,
                                   observations, sources, [blocked, completed_other], True)
    assert partially_checked.report_eligibility == "requires_human_review"


def test_issue_level_status_aliases_do_not_make_narrowed_subcheck_supported():
    parsed = proposal("concern_narrowed", [
        check("supported aspect", "concern_supported"),
        check("refuted aspect", "concern_refuted"),
        check("partially answered aspect", "concern_narrowed")],
        revised="Which aspect remains untested?")
    assert [item.proposed_status for item in parsed.subchecks] == [
        "supported", "contradicted", "inconclusive"]


def test_final_rationale_uses_executed_records_instead_of_false_model_tool_claim(tmp_path):
    task, issue, search, facts, sources, observations, challenge = context(tmp_path)
    model = proposal("inconclusive", [check("Is the mechanism established?", "inconclusive")])
    model.verdict_rationale = "Original PDF and full-text search were not executed."
    result = adjudicate(task, issue, model, facts, challenge, search,
                        observations, sources, [], True)
    assert "2 PDF-text observations located" in result.verdict_rationale
    assert "2 available reading units indexed" in result.verdict_rationale
    assert "were not executed" not in result.verdict_rationale
    assert result.subchecks[0].actual_observations == []
    assert result.subchecks[0].limitations[0].startswith("Model interpretation")


def test_author_ablation_can_refute_missing_experiment_candidate(tmp_path):
    task, issue, search, facts, sources, observations, challenge = specific_context(tmp_path)
    sources[1].relevant_excerpt = "Authors report a key-side relative-position ablation"
    observations[1].observed_statement = sources[1].relevant_excerpt
    model = proposal("concern_refuted", [check("Is there a key-side relative-position ablation?", "contradicted",
                                                 contradict=["ru2"])])
    result = adjudicate(task, issue, model, facts, challenge, search,
                        observations, sources, [], True)
    assert result.verdict == "concern_refuted"
    assert result.report_eligibility == "not_reportable"
    assert result.original_candidate_ids == issue.source_candidate_ids
    assert "ru2" in result.counterevidence
    assert result.author_explanations


def test_mixed_issue_keeps_parent_and_independent_subcheck_results(tmp_path):
    task, issue, search, facts, sources, observations, challenge = specific_context(tmp_path)
    sources[1].relevant_excerpt = "Authors report a key-side relative-position ablation"
    observations[1].observed_statement = sources[1].relevant_excerpt
    model = proposal("concern_narrowed", [
        check("Does the key-side relative-position mechanism have support?", "supported", support=["ru1"]),
        check("Is the key-side relative-position ablation missing?", "contradicted", contradict=["ru2"])],
        revised="Could the key-side relative-position mechanism ablation be clarified?")
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
