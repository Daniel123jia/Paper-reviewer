"""Conservative server-side judgment over located sources and independent checks."""

import hashlib
import re
from pathlib import Path

from pydantic import Field, field_validator

from backend.p2a_schema import StrictModel
from backend.p3_schema import (CounterEvidenceSearch, FactAssessment, IssueAdjudication,
                               SourceObservation, VerificationSubcheck)


SOURCE_PROMPT_VERSION = "source-observation-check/v1"
JUDGMENT_PROMPT_VERSION = "issue-adjudication/v2"
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

    @field_validator("proposed_status", mode="before")
    @classmethod
    def normalize_issue_level_alias(cls, value):
        # The model sometimes applies the whole-issue vocabulary to a subcheck.
        # A partial/narrowed issue does not establish a supported subcheck.
        return {"concern_supported": "supported", "concern_refuted": "contradicted",
                "concern_narrowed": "inconclusive"}.get(value, value) if isinstance(value, str) else value


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
    # A single shared word such as "ablation" is not evidence of the same test.
    anchors = _topic_terms(question)
    if len(anchors) < 2:
        return False
    for item in observations:
        if item.source_check_status not in {"pdf_text_located", "human_confirmed"} or \
                not set(item.related_source_ref_ids).intersection(refs):
            continue
        actual = item.observed_statement
        actual_terms = _topic_terms(actual)
        if len(anchors & actual_terms) < max(2, (len(anchors) + 2) // 3):
            continue
        if not _named_terms(question).issubset(set(re.findall(r"[a-z0-9]+(?:\.\d+)?",
                                                         actual.casefold()))):
            continue
        if not {term for term in anchors if term in {"key", "value", "base", "big",
                                                      "train", "test", "left", "right"}}.issubset(actual_terms):
            continue
        normalized = " ".join(re.findall(r"[a-z0-9]+", actual.casefold()))
        if all(" ".join(re.findall(r"[a-z0-9]+", compound.casefold())) in normalized
               for compound in re.findall(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+", question)
               if set(compound.casefold().split("-")) &
               {"key", "value", "base", "big", "left", "right"}):
            return True
    return False


_GENERIC_TERMS = {
    "about", "ablation", "ablations", "answer", "answers", "author", "authors",
    "baseline", "baselines", "claim", "claims", "compare", "comparison", "concern",
    "condition", "conditions", "could", "dataset", "datasets", "does", "effect",
    "evidence", "experiment", "experiments", "explain", "explanation", "figure",
    "given", "have", "isolate", "isolated", "issue", "method", "methods", "model",
    "models", "paper", "reported", "report", "result", "results", "same", "setting",
    "settings", "show", "shows", "source", "study", "support", "supported", "table",
    "test", "tested", "there", "these", "this", "those", "using", "verified",
    "verify", "what", "when", "where", "whether", "which", "with", "would",
    "clarify", "clarified", "should", "can", "did", "the", "and", "for", "from",
    "into", "its", "not", "are", "was", "were", "has", "had", "how", "that",
    "than", "their", "then", "under", "only", "each", "also", "need", "needs",
    "mechanism", "component", "components", "improve", "improved", "points",
    "missing", "absent", "lacking", "lack", "without", "provided", "present",
    "clarification", "empirical", "located", "independently", "pdf", "text",
    "require", "requires", "make", "whose", "directly", "passages", "material",
    "searched", "other", "narrower", "broad", "basis", "warrant", "warrants",
    "describe", "described", "specify", "specified", "uniquely", "establish",
    "established", "contain", "contains", "found", "available", "complete",
    "reported", "reporting", "full", "actual", "original", "potential",
    "scores", "score", "numerical", "numeric", "outside", "inside", "remaining",
}


def _topic_terms(text):
    return {_root_term(term) for term in re.findall(r"[a-z0-9]+(?:\.\d+)?", text.casefold())
            if (len(term) >= 3 or any(char.isdigit() for char in term)) and
            term not in _GENERIC_TERMS}


def _root_term(term):
    if term.endswith("ization"):
        return term[:-5]
    if term.endswith("ize"):
        return term[:-1]
    if term.endswith("s") and len(term) > 4 and not term.endswith("ss"):
        return term[:-1]
    return term


def _named_terms(text):
    return {term.casefold() for term in re.findall(r"\b[A-Za-z][A-Za-z0-9]*\b", text)
            if len(term) >= 2 and term.casefold() not in _GENERIC_TERMS and
            (any(char.isdigit() for char in term) or
             (any(char.isupper() for char in term[1:]) and any(char.islower() for char in term)) or
             (term.isupper() and len(term) >= 2))}


def _question_matches_issue(question, issue):
    issue_text = " ".join([issue.title, issue.central_question,
                           issue.suspected_problem, *issue.observed_facts])
    return len(_topic_terms(question) & _topic_terms(issue_text)) >= 2


def _revised_question_grounded(revised, issue, subchecks, observations):
    if not revised or not _question_matches_issue(revised, issue):
        return False
    supported = [item for item in subchecks if item.status == "supported"]
    if not supported or not any(len(_topic_terms(revised) & _topic_terms(item.question)) >= 2
                                for item in supported):
        return False
    source_text = " ".join(item.observed_statement for item in observations
        if item.source_check_status in {"pdf_text_located", "human_confirmed",
                                        "visual_check_required"})
    known = _topic_terms(" ".join([issue.title, issue.central_question,
        issue.suspected_problem, *issue.observed_facts, source_text]))
    revision_terms = _topic_terms(revised)
    if len(revision_terms - known) * 2 >= len(revision_terms):
        return False
    observed_text = " ".join(item.observed_statement.casefold() for item in observations)
    numbers = re.findall(r"(?<![a-z0-9])\d+(?:\.\d+)?(?![a-z0-9])", revised.casefold())
    if any(not re.search(r"(?<![a-z0-9])" + re.escape(number) + r"(?![a-z0-9])",
                         observed_text) for number in numbers):
        return False
    if not _named_terms(revised).issubset(set(re.findall(r"[a-z0-9]+(?:\.\d+)?",
                                                    observed_text))):
        return False
    return True


def _critical_visual_dependency(question, task, sources, subchecks):
    relevant = {ref.source_id for ref in task.source_refs_to_check +
                task.counterevidence_refs_to_check}
    relevant.update(ref for item in subchecks for ref in
                    item.supporting_refs + item.contradicting_refs)
    numbers = set(re.findall(r"(?<![a-z0-9])\d+(?:\.\d+)?(?![a-z0-9])",
                             question.casefold()))
    for source in sources:
        if source.source_id not in relevant or source.source_type not in {
                "table", "formula", "figure"} or \
                source.source_verification_status != "visual_check_required":
            continue
        label = re.search(r"\b(?:table|figure|fig\.?|equation|eq\.?)\s*(\d+)\b",
                          source.relevant_excerpt, re.I)
        if label and re.search(r"\b(?:table|figure|fig\.?|equation|eq\.?)\s*" +
                               re.escape(label.group(1)) + r"\b", question, re.I):
            return True
        if numbers and re.search(r"\b(?:table|row|column|formula|equation|figure|score|value)\b",
                                 question, re.I):
            return True
    return False


def _critical_external_dependency(question, task, tools):
    if not task.external_verification_required:
        return False
    external = [item for item in tools if item.check_type == "external"]
    if external and all(item.status == "completed" for item in external):
        return False
    return bool(re.search(r"\b(?:prior|literature|citation|cited|novelty|contrast|"
                          r"comparison|outperform|state.of.the.art|sota)\b", question, re.I))


def _server_rationale(verdict, proposed_verdict, issue, revised_question, subchecks,
                      observations, search, outstanding, author_answer_refs):
    """Describe scoped questions and executed sources without trusting model tool claims."""
    located = sum(item.source_check_status == "pdf_text_located" for item in observations)
    located_refs = {ref for item in observations
        if item.source_check_status in {"pdf_text_located", "human_confirmed"}
        for ref in item.related_source_ref_ids}
    def describe(status, ref_field):
        matches = [item for item in subchecks if item.status == status]
        return "; ".join(f"{item.question} [Source Ref: " +
                         ", ".join(ref for ref in getattr(item, ref_field)
                                   if ref in located_refs) + "]"
                         for item in matches[:3]) or "none"
    answer_refs = set(author_answer_refs)
    answers = [item for item in observations if item.source_check_status == "pdf_text_located"
        and answer_refs.intersection(item.related_source_ref_ids)]
    answer_text = "; ".join(f"{item.related_source_ref_ids[0]}: " +
        " ".join(item.observed_statement.split())[:140] for item in answers[:2]) or "none located"
    pending = "; ".join(outstanding[:3]) or "none recorded"
    return (f"Original scientific question: {issue.central_question} "
            f"Final verdict: {verdict}; model proposed {proposed_verdict}. "
            f"Supported subchecks: {describe('supported', 'supporting_refs')}. "
            f"Contradicted subchecks: {describe('contradicted', 'contradicting_refs')}. "
            f"Revised question: {revised_question or 'none'}. "
            f"Potential author answers in located PDF text: {answer_text}. "
            f"Key pending checks: {pending}. "
            f"Audit: {located} PDF-text observations located; "
            f"{search.indexed_unit_count} available reading units indexed. "
            "Source location and model interpretation do not prove author error.")


def _subcheck_source_observations(refs, observations):
    selected = set(refs)
    return [f"{item.source_check_status} {item.related_source_ref_ids[0]}: "
            f"{item.observed_statement[:400]}" for item in observations
            if item.source_check_status == "pdf_text_located" and
            selected.intersection(item.related_source_ref_ids)]


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
                                        not fact_aligned or
                                        not _question_matches_issue(item.question, issue) or
                                        not _counter_matches_question(item.question,
                                            item.supporting_refs, observations) or
                                        not set(item.supporting_refs).intersection(
                                            challenge.supporting_source_ids)):
            status = "inconclusive"
        elif status == "contradicted" and (not set(item.contradicting_refs).intersection(located) or
            not _question_matches_issue(item.question, issue) or
            not _counter_matches_question(item.question, item.contradicting_refs, observations) or
            not set(item.contradicting_refs).intersection(
                challenge.contradicting_source_ids + challenge.author_answer_source_ids)):
            status = "inconclusive"
        elif status == "supported" and _absence_based(item.question) and not search.absence_confirmed:
            status = "inconclusive"
        subchecks.append(VerificationSubcheck(subcheck_id=_subcheck_id(task.verification_task_id, index),
            parent_verification_task_id=task.verification_task_id,
            question=item.question, check_type=item.check_type,
            input_refs=list(dict.fromkeys(item.supporting_refs + item.contradicting_refs)),
            method="PDF text location and selected full-text context; model assessment subject to server gates",
            actual_observations=_subcheck_source_observations(
                item.supporting_refs + item.contradicting_refs, observations),
            supporting_refs=item.supporting_refs, contradicting_refs=item.contradicting_refs,
            tool_result_ids=item.tool_result_ids, status=status,
            limitations=[f"Model interpretation (not independent fact proof): {item.explanation}"] +
                [f"Model-reported limitation: {limit}" for limit in item.limitations] +
                (["Model status reduced by source/coverage gate"]
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
    elif verdict == "concern_narrowed" and (proposal.revised_question == issue.central_question or
            not _revised_question_grounded(proposal.revised_question, issue,
                                           subchecks, observations)):
        verdict = "inconclusive"
    elif verdict == "blocked" and located:
        verdict = "inconclusive"
    if verdict == "concern_refuted":
        eligibility = "not_reportable"
    elif verdict in {"concern_supported", "concern_narrowed"} and \
            all(source_index[ref].source_verification_status == "pdf_text_located"
                for subcheck in subchecks if subcheck.status == "supported"
                for ref in subcheck.supporting_refs) and \
            not _critical_visual_dependency(proposal.revised_question if
                verdict == "concern_narrowed" else issue.central_question,
                task, sources, subchecks) and \
            not _critical_external_dependency(proposal.revised_question if
                verdict == "concern_narrowed" else issue.central_question,
                task, tools) and \
            not any(tool_index[tool_id].status != "completed" for tool_id in
                    (tool_id for subcheck in subchecks if subcheck.status == "supported"
                     for tool_id in subcheck.tool_result_ids)):
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
        verdict=verdict, verdict_rationale=_server_rationale(verdict,
            proposal.proposed_verdict, issue,
            proposal.revised_question if verdict == "concern_narrowed" else None,
            subchecks, observations, search, outstanding,
            challenge.author_answer_source_ids),
        remaining_uncertainty=uncertainty,
        provisional_impact=impact, impact_rationale=proposal.impact_rationale,
        report_eligibility=eligibility, human_review_status="pending",
        execution_status="completed" if verdict in {"concern_supported", "concern_refuted",
            "concern_narrowed"} and not outstanding else "limited")
