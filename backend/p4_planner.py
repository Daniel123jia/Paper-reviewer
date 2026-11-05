"""Build a small, source-bound writing plan before any report model call."""

import hashlib
import re

from backend.p2a_schema import PaperProfile
from backend.p2c_schema import CandidateConsolidationResult
from backend.p3_schema import EvidenceVerificationResult
from backend.p4_eligibility import eligibility_entries
from backend.p4_report_schema import (EvidenceReviewRecord, ReportEvidenceItem,
    ReportEvidencePack, ReportIssueGroup, ReportPlan, ReportSourceIdentity,
    ReviewerAddedIssue, ReviewerDecision)


def _terms(text):
    stop = {"the", "and", "for", "with", "from", "does", "whether", "could",
            "would", "that", "this", "their", "which", "what", "same", "paper",
            "authors", "author", "claim", "reported", "report", "result", "results"}
    return {term for term in re.findall(r"[a-z0-9]+", text.casefold())
            if len(term) > 2 and term not in stop}


def _similar_question(left, right):
    a, b = _terms(left), _terms(right)
    return bool(a and b) and len(a & b) / len(a | b) >= 0.78


def _category(entry, decision):
    if entry.verdict == "concern_narrowed":
        return "clarification"
    if entry.verdict == "concern_supported" and decision and \
            decision.reviewer_approval_status == "approved_for_final":
        return "major" if entry.provisional_impact == "major_candidate" else "minor"
    return "clarification"


def _groups(entries, consolidation, decisions, manual):
    issue_index = {item.consolidated_issue_id: item for item in consolidation.consolidated_issues}
    groups = []
    for entry in entries:
        issue = issue_index[entry.consolidated_issue_id]
        decision = decisions.get(entry.consolidated_issue_id)
        category = _category(entry, decision)
        match = next((group for group in groups if group.category == category and
                      _similar_question(group.scientific_question, entry.scientific_question) and
                      (set(issue.related_claim_ids) & set(issue_index[group.source_issue_ids[0]].related_claim_ids)
                       or _terms(group.scientific_question) == _terms(entry.scientific_question))), None)
        if match:
            match.source_issue_ids.append(entry.consolidated_issue_id)
            match.group_id = "report_group_" + hashlib.sha256(
                "|".join(sorted(match.source_issue_ids)).encode()).hexdigest()[:16]
            match.grouping_rationale = "Same claim and substantially matching question; keep every P3 source issue"
        else:
            groups.append(ReportIssueGroup(group_id="report_group_" + hashlib.sha256(
                entry.consolidated_issue_id.encode()).hexdigest()[:16],
                source_issue_ids=[entry.consolidated_issue_id],
                scientific_question=entry.scientific_question, category=category,
                order=decision.report_order if decision else len(groups),
                grouping_rationale="Separate scientific question"))
    for item in manual:
        decision = decisions.get(item.issue_id)
        groups.append(ReportIssueGroup(group_id="report_group_" + hashlib.sha256(
            item.issue_id.encode()).hexdigest()[:16], source_issue_ids=[item.issue_id],
            scientific_question=item.scientific_question, category=item.category,
            order=decision.report_order if decision else len(groups),
            grouping_rationale="Reviewer-added question, separate from P3"))
    priority = {"major": 0, "clarification": 1, "minor": 2}
    return sorted(groups, key=lambda group: (priority[group.category], group.order, group.group_id))


def _paper_facts(profile):
    result = []
    for name in ("research_problem", "research_motivation", "proposed_method"):
        fact = getattr(profile, name)
        if fact and fact.evidence_ref_ids:
            result.append(f"Authors report {name.replace('_', ' ')}: {fact.text} "
                          f"[Source: {', '.join(fact.evidence_ref_ids)}]")
    for fact in profile.author_claimed_contributions[:3]:
        if fact.evidence_ref_ids:
            result.append(f"Authors claim a contribution: {fact.text} "
                          f"[Source: {', '.join(fact.evidence_ref_ids)}]")
    return result


def build_report_plan(snapshot: dict, decisions: list[ReviewerDecision],
                      reviews: list[EvidenceReviewRecord], manual: list[ReviewerAddedIssue],
                      report_kind: str, language: str):
    source = ReportSourceIdentity.model_validate(snapshot["source"])
    result = EvidenceVerificationResult.model_validate(snapshot["p3"])
    consolidation = CandidateConsolidationResult.model_validate(snapshot["consolidation"])
    profile = PaperProfile.model_validate(snapshot["profile"])
    entries = eligibility_entries(result, consolidation)
    decision_index = {item.consolidated_issue_id: item for item in decisions}
    confirmed = {(item.consolidated_issue_id, item.source_ref_id) for item in reviews
                 if item.confirmation_status == "confirmed"}
    selected = []
    for entry in entries:
        decision = decision_index.get(entry.consolidated_issue_id)
        if report_kind != "author":
            selected.append(entry)
            continue
        if decision and decision.reviewer_approval_status in {"excluded", "needs_verification"}:
            continue
        if entry.processing_status == "auto_draft_candidate":
            selected.append(entry)
        elif entry.processing_status == "human_review_required" and decision and \
                decision.reviewer_approval_status in {"approved_for_draft", "approved_for_final"}:
            visual = {item.source_id for item in result.sources if
                      item.source_id in entry.source_refs + entry.counterevidence_refs and
                      item.source_type in {"table", "formula", "figure"} and
                      item.source_verification_status == "visual_check_required"}
            if all((entry.consolidated_issue_id, ref) in confirmed for ref in visual):
                selected.append(entry)
    selected_manual = [item for item in manual if report_kind != "author" or
                       (decision_index.get(item.issue_id) and
                        decision_index[item.issue_id].reviewer_approval_status in
                        {"approved_for_draft", "approved_for_final"})]
    groups = _groups(selected, consolidation, decision_index, selected_manual)
    plan = ReportPlan(source=source, report_kind=report_kind, language=language,
        selected_issue_ids=[item.consolidated_issue_id for item in selected] +
                           [item.issue_id for item in selected_manual],
        internal_only_issue_ids=[item.consolidated_issue_id for item in entries
                                 if item.processing_status in {"internal_only", "human_review_required"}
                                 and item not in selected],
        excluded_issue_ids=[item.consolidated_issue_id for item in entries
                            if item.processing_status == "excluded"],
        groups=groups, source_limitations=list(dict.fromkeys(
            result.unresolved_coverage_gaps + profile.unresolved_source_gaps)))
    source_index = {item.source_id: item for item in result.sources}
    observations = result.observations
    adjudications = {item.consolidated_issue_id: item for item in result.adjudications}
    evidence = []
    for entry in selected:
        item = adjudications[entry.consolidated_issue_id]
        refs = set(entry.source_refs + entry.counterevidence_refs)
        located = [ob.observed_statement for ob in observations if
                   ob.verification_task_id == entry.verification_task_id and
                   ob.source_check_status in {"pdf_text_located", "human_confirmed"} and
                   refs.intersection(ob.related_source_ref_ids)]
        confirmed_values = [review.checked_values_and_labels for review in reviews if
                            review.consolidated_issue_id == entry.consolidated_issue_id and
                            review.confirmation_status == "confirmed" and
                            review.source_ref_id in refs]
        locations = [f"{source_index[ref].source_type}, PDF page "
                     f"{source_index[ref].page_index + 1}" for ref in refs
                     if ref in source_index and source_index[ref].page_index is not None]
        tools = [f"{check.check_type}: {check.result}" for check in result.tool_results if
                 check.verification_task_id == entry.verification_task_id and
                 check.status == "completed"]
        evidence.append(ReportEvidenceItem(source_issue_id=entry.consolidated_issue_id,
            scientific_question=entry.scientific_question,
            original_question=item.original_central_question,
            revised_question=item.revised_question, verdict=item.verdict,
            report_eligibility=item.report_eligibility,
            human_review_status=item.human_review_status,
            reviewer_approval_status=(decision_index[entry.consolidated_issue_id]
                                      .reviewer_approval_status if entry.consolidated_issue_id
                                      in decision_index else "pending"),
            observed_statements=located + confirmed_values,
            source_refs=entry.source_refs, counterevidence_refs=entry.counterevidence_refs,
            author_explanations=item.author_explanations,
            outstanding_checks=item.outstanding_checks,
            evidence_limitations=item.remaining_uncertainty,
            source_locations=list(dict.fromkeys(locations)), actual_tool_results=tools))
    pack = ReportEvidencePack(source=source, report_kind=report_kind,
        language=language, paper_facts=_paper_facts(profile), issues=evidence,
        groups=groups, reviewer_added=selected_manual,
        approval_record_ids=[decision.decision_id for decision in decisions
                             if decision.consolidated_issue_id in plan.selected_issue_ids],
        source_limitations=plan.source_limitations)
    return plan, pack
