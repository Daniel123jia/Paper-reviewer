"""Build a small, source-bound writing plan before any report model call."""

import hashlib
import re

from backend.p2a_schema import PaperProfile
from backend.p2c_schema import CandidateConsolidationResult
from backend.p3_schema import EvidenceVerificationResult
from backend.p4_eligibility import eligibility_entries
from backend.p4_report_schema import (EvidenceReviewRecord, ReportEvidenceItem,
    ExternalEvidenceReviewRecord, ReportEvidencePack, ReportIssueGroup, ReportPlan,
    ReportSourceIdentity, ReviewerAddedIssue, ReviewerDecision)


def _terms(text):
    stop = {"the", "and", "for", "with", "from", "does", "whether", "could",
            "would", "that", "this", "their", "which", "what", "same", "paper",
            "authors", "author", "claim", "reported", "report", "result", "results"}
    return {term for term in re.findall(r"[a-z0-9]+", text.casefold())
            if len(term) > 2 and term not in stop}


def _similar_question(left, right):
    a, b = _terms(left), _terms(right)
    return bool(a and b) and len(a & b) / len(a | b) >= 0.78


def _technical_phrases(text):
    words = re.findall(r"[a-z0-9]+", text.casefold())
    generic = {"the", "and", "with", "from", "other", "does", "which", "that",
               "this", "for", "under", "rather", "than", "what", "whether",
               "could", "would", "report", "reported", "paper", "available",
               "complete", "authors", "result", "results"}
    return {tuple(words[index:index + 3]) for index in range(len(words) - 2)
            if all(word not in generic for word in words[index:index + 3])}


def _request_facets(text):
    patterns = {
        "generalization": r"\b(?:generaliz\w*|out[- ]of[- ]domain|unseen|distribution shift|transfer)\b",
        "efficiency": r"\b(?:memory|complexity|runtime|latency|throughput|scal\w*|efficien\w*)\b",
        "mathematical": r"\b(?:formula|equation|deriv\w*|proof|theorem|mathemat\w*|correctness)\b",
        "experimental_validation": r"\b(?:baseline|compar\w*|matched|fairness|same architecture|"
                                   r"training budget|ablation|component|variant|isolate|"
                                   r"contribution of)\b",
    }
    folded = text.casefold()
    return {name for name, pattern in patterns.items() if re.search(pattern, folded)}


def _same_scientific_request(left, right):
    left_facets, right_facets = _request_facets(left), _request_facets(right)
    if left_facets != right_facets:
        return False
    if _similar_question(left, right):
        return True
    return bool(left_facets and _technical_phrases(left) & _technical_phrases(right))


def _scope_clarification(question):
    return bool(re.search(r"\bclarif\w*\b", question, re.I) and
                re.search(r"\b(?:scope|limit\w*|applicab\w*|conclusion|claim)\b", question, re.I) and
                re.search(r"\b(?:this paper|evaluated|reported|studied|presented)\b", question, re.I))


def external_requirement(snapshot, entry):
    task = next((item for item in snapshot["consolidation"]["verification_tasks"]
                 if item["verification_task_id"] == entry.verification_task_id), None)
    required = bool(task and task["external_verification_required"])
    completed = required and any(item["verification_task_id"] == entry.verification_task_id and
                                 item["check_type"] == "external" and item["status"] == "completed"
                                 for item in snapshot["p3"]["tool_results"])
    external_fact = bool(re.search(
        r"\b(?:prior work|previous work|literature|citation|cited|state[- ]of[- ]the[- ]art|sota|"
        r"repository|source code|code|scripts?|released|external materials?|external data|"
        r"public dataset|benchmark)\b",
        entry.scientific_question, re.I))
    scope_only = _scope_clarification(entry.scientific_question)
    return {"required": required, "completed_in_p3": completed,
            "critical": required and not completed and external_fact and not scope_only,
            "scope_clarification": scope_only}


def external_admission(snapshot, entry, decision, records):
    requirement = external_requirement(snapshot, entry)
    referenced = {item.record_id: item for item in records
                  if item.consolidated_issue_id == entry.consolidated_issue_id}
    confirmed = [referenced[record_id] for record_id in
                 (decision.external_evidence_review_record_ids if decision else [])
                 if record_id in referenced and referenced[record_id].confirmation_status == "confirmed"]
    if not requirement["required"]:
        return True, "not_required", "No external evidence is required for this final question.", []
    if requirement["completed_in_p3"]:
        return True, "completed_in_p3", "P3 records a completed external check.", []
    if confirmed and decision and decision.external_dependency_disposition == "verified":
        return True, "reviewer_verified", "Use only the separately reviewed external finding.", confirmed
    if decision and decision.external_dependency_disposition == "clarification_only" and \
            requirement["scope_clarification"] and \
            decision.external_uncertainty_qualification.strip():
        return True, "clarification_only", decision.external_uncertainty_qualification, []
    status = "critical_unresolved" if requirement["critical"] else "noncritical_unresolved"
    constraint = ("External support is a critical dependency and remains unverified; do not state "
                  "the external fact or comparison." if requirement["critical"] else
                  "External material remains unchecked; restrict wording to the paper's own claims "
                  "and do not imply external verification.")
    return not requirement["critical"], status, constraint, []


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
                      _same_scientific_request(group.scientific_question, entry.scientific_question) and
                      (set(issue.related_claim_ids) & set(issue_index[group.source_issue_ids[0]].related_claim_ids)
                       or _terms(group.scientific_question) == _terms(entry.scientific_question))), None)
        if match:
            match.source_issue_ids.append(entry.consolidated_issue_id)
            match.group_id = "report_group_" + hashlib.sha256(
                "|".join(sorted(match.source_issue_ids)).encode()).hexdigest()[:16]
            match.grouping_rationale = "Same claim and matching scientific request; retain every P3 source issue"
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


def _location(source):
    page = f"PDF page {source.page_index + 1}"
    if source.source_type in {"table", "figure", "formula"}:
        kind = {"table": "Table", "figure": "Figure", "formula": "Equation"}[source.source_type]
        match = re.search(rf"\b{kind}\s*\(?([0-9]+)\)?", source.relevant_excerpt, re.I)
        if match:
            return f"{kind} {match.group(1)}, {page}"
    return page


def build_report_plan(snapshot: dict, decisions: list[ReviewerDecision],
                      reviews: list[EvidenceReviewRecord], manual: list[ReviewerAddedIssue],
                      report_kind: str, language: str,
                      external_reviews: list[ExternalEvidenceReviewRecord] | None = None):
    external_reviews = external_reviews or []
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
        external_allowed, _, _, _ = external_admission(
            snapshot, entry, decision, external_reviews)
        if report_kind != "author":
            selected.append(entry)
            continue
        if decision and decision.reviewer_approval_status in {"excluded", "needs_verification"}:
            continue
        if entry.processing_status == "auto_draft_candidate" and external_allowed:
            selected.append(entry)
        elif entry.processing_status == "human_review_required" and decision and \
                decision.reviewer_approval_status in {"approved_for_draft", "approved_for_final"}:
            visual = {item.source_id for item in result.sources if
                      item.source_id in entry.source_refs + entry.counterevidence_refs and
                      item.source_type in {"table", "formula", "figure"} and
                      item.source_verification_status == "visual_check_required"}
            if all((entry.consolidated_issue_id, ref) in confirmed for ref in visual):
                requirement = external_requirement(snapshot, entry)
                if external_allowed and (not requirement["required"] or
                        requirement["completed_in_p3"] or
                        decision.external_dependency_disposition in
                        {"verified", "clarification_only"}):
                    selected.append(entry)
    selected_manual = [item for item in manual if report_kind != "author" or
                       (decision_index.get(item.issue_id) and
                        decision_index[item.issue_id].reviewer_approval_status in
                        {"approved_for_draft", "approved_for_final"})]
    groups = _groups(selected, consolidation, decision_index, selected_manual)
    plan = ReportPlan(admission_rule_version="p4.1", source=source,
        report_kind=report_kind, language=language,
        selected_issue_ids=[item.consolidated_issue_id for item in selected] +
                           [item.issue_id for item in selected_manual],
        internal_only_issue_ids=[item.consolidated_issue_id for item in entries
                                 if item.processing_status != "excluded" and item not in selected],
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
        locations = [_location(source_index[ref]) for ref in sorted(refs)
                     if ref in source_index and source_index[ref].page_index is not None]
        tools = [f"{check.check_type}: {check.result}" for check in result.tool_results if
                 check.verification_task_id == entry.verification_task_id and
                 check.status == "completed"]
        _, external_status, external_constraint, external_confirmed = external_admission(
            snapshot, entry, decision_index.get(entry.consolidated_issue_id), external_reviews)
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
            source_locations=list(dict.fromkeys(locations)), actual_tool_results=tools,
            external_verification_required=external_requirement(snapshot, entry)["required"],
            external_dependency_status=external_status,
            external_reporting_constraint=external_constraint,
            external_evidence_review_record_ids=[item.record_id for item in external_confirmed],
            external_verified_findings=[item.verified_finding for item in external_confirmed]))
    pack = ReportEvidencePack(admission_rule_version="p4.1", source=source,
        report_kind=report_kind,
        language=language, paper_facts=_paper_facts(profile), issues=evidence,
        groups=groups, reviewer_added=selected_manual,
        approval_record_ids=[decision.decision_id for decision in decisions
                             if decision.consolidated_issue_id in plan.selected_issue_ids],
        source_limitations=plan.source_limitations)
    return plan, pack
