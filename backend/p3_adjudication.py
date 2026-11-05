"""Conservative server-side judgment over located sources and independent checks."""

import hashlib
import re
from pathlib import Path

from pydantic import Field

from backend.p2a_schema import StrictModel
from backend.p3_schema import (CounterEvidenceSearch, FactAssessment, IssueAdjudication,
                               SourceObservation, VerificationSubcheck)


SOURCE_PROMPT_VERSION = "source-observation-check/v1"
JUDGMENT_PROMPT_VERSION = "issue-adjudication/v1"
PROMPTS = Path(__file__).with_name("prompts") / "p3"


class SourceFactReply(StrictModel):
    assessments: list[FactAssessment]


class ProposedSubcheck(StrictModel):
    question: str = Field(min_length=1)
    check_type: str
    method: str
    supporting_refs: list[str]
    contradicting_refs: list[str]
    tool_result_ids: list[str]
    proposed_status: str = Field(pattern="^(supported|contradicted|inconclusive|blocked)$")
    explanation: str
    limitations: list[str]


class AdjudicationProposal(StrictModel):
    subchecks: list[ProposedSubcheck] = Field(min_length=1, max_length=12)
    proposed_verdict: str = Field(pattern="^(concern_supported|concern_refuted|concern_narrowed|inconclusive|blocked)$")
    revised_question: str | None
    verdict_rationale: str
    remaining_uncertainty: list[str]
    provisional_impact: str = Field(pattern="^(major_candidate|minor_candidate|undetermined)$")
    impact_rationale: str
    evidence_level: str = Field(default="model_interpretation", pattern="^model_interpretation$")


def validate_source_fact_reply(raw, issue, allowed_refs):
    reply = SourceFactReply.model_validate(raw)
    if {item.original_observed_fact for item in reply.assessments} != set(issue.observed_facts):
        raise ValueError("source observation omitted or invented an original fact")
    if len(reply.assessments) != len(issue.observed_facts):
        raise ValueError("source observation duplicated an original fact")
    if any(not set(item.source_ids).issubset(allowed_refs) for item in reply.assessments):
        raise ValueError("source observation cited foreign source")
    return reply.assessments


def validate_adjudication_proposal(raw, allowed_refs, tool_ids):
    proposal = AdjudicationProposal.model_validate(raw)
    if any(not set(item.supporting_refs + item.contradicting_refs).issubset(allowed_refs) or
           not set(item.tool_result_ids).issubset(tool_ids) for item in proposal.subchecks):
        raise ValueError("adjudication cited an unobserved source or unexecuted tool")
    return proposal


def _subcheck_id(task_id, index):
    return f"subcheck_{hashlib.sha256(f'{task_id}|{index}'.encode()).hexdigest()[:20]}"


def _absence_based(text):
    return bool(re.search(r"\b(?:missing|absent|no|none|not provided|not reported|lack|without)\b|缺少|未提供|没有",
                          text, re.I))


def _fact_grounded_in_pdf_text(fact, observations):
    terms = {term for term in re.findall(r"[a-z0-9]+(?:\.\d+)?", fact.original_observed_fact.casefold())
             if len(term) >= 4 or re.fullmatch(r"\d+(?:\.\d+)?", term)} - {
                 "authors", "author", "paper", "state", "states", "report", "reports",
                 "claim", "claims", "that", "with", "from", "their", "this", "which"}
    if not terms:
        return False
    actual = " ".join(item.observed_statement.casefold() for item in observations
                      if item.source_check_status == "pdf_text_located" and
                      set(item.related_source_ref_ids).intersection(fact.source_ids))
    found = {term for term in terms if re.search(r"(?<![a-z0-9])" + re.escape(term) +
                                           r"(?![a-z0-9])", actual)}
    numbers = {term for term in terms if term[0].isdigit()}
    return bool(actual) and numbers.issubset(found) and len(found) / len(terms) >= 0.5


def _counter_matches_question(question, refs, observations):
    terms = {term for term in re.findall(r"[a-z0-9]{4,}|\d+(?:\.\d+)?", question.casefold())
             if term not in {"does", "there", "this", "that", "with", "from", "have",
                             "been", "were", "would", "could", "paper", "claim", "about"}}
    actual = " ".join(item.observed_statement.casefold() for item in observations
                      if item.source_check_status == "pdf_text_located" and
                      set(item.related_source_ref_ids).intersection(refs))
    return bool(actual and terms and any(re.search(r"(?<![a-z0-9])" + re.escape(term) +
                                                 r"(?![a-z0-9])", actual) for term in terms))


def fallback_adjudication(task, issue, search: CounterEvidenceSearch,
                         observations: list[SourceObservation], reason, pdf_available):
    question = task.central_question
    subcheck = VerificationSubcheck(subcheck_id=_subcheck_id(task.verification_task_id, 1),
        parent_verification_task_id=task.verification_task_id, question=question,
        check_type="source_and_counterevidence", input_refs=[ref.source_id for ref in
            task.source_refs_to_check + task.counterevidence_refs_to_check],
        method="No reliable model adjudication; preserve original question",
        actual_observations=[], supporting_refs=[], contradicting_refs=[],
        tool_result_ids=[], status="blocked" if not pdf_available else "inconclusive",
        limitations=[reason])
    return IssueAdjudication(consolidated_issue_id=issue.consolidated_issue_id,
        verification_task_id=task.verification_task_id,
        original_candidate_ids=issue.source_candidate_ids,
        original_central_question=issue.central_question,
        original_source_refs=[ref.source_id for ref in task.source_refs_to_check],
        original_counterevidence_refs=[ref.source_id for ref in task.counterevidence_refs_to_check],
        revised_question=None,
        fact_assessments=[], counterevidence_search=search, subchecks=[subcheck],
        verified_observation_ids=[item.observation_id for item in observations
                                  if item.source_check_status == "pdf_text_located"],
        supporting_evidence=[], counterevidence=[], author_explanations=[],
        executed_tool_result_ids=[], outstanding_checks=task.required_checks,
        verdict="blocked" if not pdf_available else "inconclusive",
        verdict_rationale=reason, remaining_uncertainty=[reason],
        provisional_impact="undetermined", impact_rationale="No grounded impact assessment",
        report_eligibility="requires_human_review", human_review_status="pending",
        execution_status="blocked" if not pdf_available else "limited")


def adjudicate(task, issue, proposal, facts, challenge, search, observations, sources,
               tools, pdf_available):
    source_index = {item.source_id: item for item in sources}
    tool_index = {item.tool_result_id: item for item in tools}
    located = {item.source_id for item in sources
               if item.source_verification_status in {"pdf_text_located", "human_confirmed"}}
    fact_aligned = any(item.alignment in {"direct", "paraphrase"} and
                       set(item.source_ids).intersection(located) and
                       _fact_grounded_in_pdf_text(item, observations) for item in facts)
    subchecks = []
    for index, item in enumerate(proposal.subchecks, start=1):
        status = item.proposed_status
        if not pdf_available:
            status = "blocked"
        elif status == "supported" and (not set(item.supporting_refs).intersection(located) or
                                        not fact_aligned):
            status = "inconclusive"
        elif status == "contradicted" and (not set(item.contradicting_refs).intersection(located) or
            not _counter_matches_question(item.question, item.contradicting_refs, observations)):
            status = "inconclusive"
        elif status == "supported" and _absence_based(item.question) and not search.absence_confirmed:
            status = "inconclusive"
        subchecks.append(VerificationSubcheck(subcheck_id=_subcheck_id(task.verification_task_id, index),
            parent_verification_task_id=task.verification_task_id,
            question=item.question, check_type=item.check_type,
            input_refs=list(dict.fromkeys(item.supporting_refs + item.contradicting_refs)),
            method=item.method, actual_observations=[item.explanation],
            supporting_refs=item.supporting_refs, contradicting_refs=item.contradicting_refs,
            tool_result_ids=item.tool_result_ids, status=status,
            limitations=item.limitations + (["Model status reduced by source/coverage gate"]
                                            if status != item.proposed_status else [])))
    statuses = {item.status for item in subchecks}
    verdict = proposal.proposed_verdict
    if not pdf_available:
        verdict = "blocked"
    elif verdict == "concern_refuted" and (statuses != {"contradicted"} or
            not any(item.contradicting_refs for item in subchecks)):
        verdict = "inconclusive"
    elif verdict == "concern_supported" and ("supported" not in statuses or
            "contradicted" in statuses or _absence_based(issue.suspected_problem + " " +
            issue.central_question) and not search.absence_confirmed):
        verdict = "inconclusive"
    elif verdict == "concern_narrowed" and (not proposal.revised_question or
            proposal.revised_question == issue.central_question or
            "supported" not in statuses):
        verdict = "inconclusive"
    elif verdict == "blocked" and located:
        verdict = "inconclusive"
    if verdict == "concern_refuted":
        eligibility = "not_reportable"
    elif verdict in {"concern_supported", "concern_narrowed"} and \
            all(source_index[ref].source_verification_status == "pdf_text_located"
                for subcheck in subchecks if subcheck.status == "supported"
                for ref in subcheck.supporting_refs) and \
            not any(tool_index[tool_id].status == "blocked" for tool_id in
                    (tool_id for subcheck in subchecks for tool_id in subcheck.tool_result_ids)):
        eligibility = "eligible_with_qualification"
    else:
        eligibility = "requires_human_review"
    if verdict in {"inconclusive", "blocked"}:
        impact = "undetermined"
    else:
        impact = proposal.provisional_impact
    supporting = list(dict.fromkeys(ref for item in subchecks for ref in item.supporting_refs))
    contradicting = list(dict.fromkeys(ref for item in subchecks for ref in item.contradicting_refs))
    explanations = ([challenge.author_answer_assessment]
        if challenge.author_answer_source_ids and
        set(challenge.author_answer_source_ids).intersection(located) else [])
    uncertainty = list(dict.fromkeys(proposal.remaining_uncertainty +
        challenge.source_limitations + search.source_quality_limits +
        [limit for item in sources for limit in item.limitations] +
        [limit for item in tools for limit in item.limitations]))
    outstanding = list(dict.fromkeys(challenge.unresolved_questions +
        [item.question for item in subchecks if item.status in {"inconclusive", "blocked"}] +
        [item for item in task.required_checks if _absence_based(item) and
         not search.absence_confirmed]))
    if verdict != proposal.proposed_verdict:
        uncertainty.append("Server source/coverage gate reduced the model-proposed verdict")
    return IssueAdjudication(consolidated_issue_id=issue.consolidated_issue_id,
        verification_task_id=task.verification_task_id,
        original_candidate_ids=issue.source_candidate_ids,
        original_central_question=issue.central_question,
        original_source_refs=[ref.source_id for ref in task.source_refs_to_check],
        original_counterevidence_refs=[ref.source_id for ref in task.counterevidence_refs_to_check],
        revised_question=proposal.revised_question if verdict == "concern_narrowed" else None,
        fact_assessments=facts, counterevidence_search=search,
        subchecks=subchecks,
        verified_observation_ids=[item.observation_id for item in observations
                                  if item.source_check_status == "pdf_text_located"],
        supporting_evidence=supporting, counterevidence=contradicting,
        author_explanations=explanations,
        executed_tool_result_ids=list(dict.fromkeys(tool_id for item in subchecks
                                                      for tool_id in item.tool_result_ids)),
        outstanding_checks=outstanding,
        verdict=verdict, verdict_rationale=proposal.verdict_rationale +
            ("; server source/coverage gate reduced the proposed verdict"
             if verdict != proposal.proposed_verdict else ""),
        remaining_uncertainty=uncertainty,
        provisional_impact=impact, impact_rationale=proposal.impact_rationale,
        report_eligibility=eligibility, human_review_status="pending",
        execution_status="completed" if verdict in {"concern_supported", "concern_refuted",
            "concern_narrowed"} and not outstanding else "limited")
