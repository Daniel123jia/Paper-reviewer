"""Deterministic P3-to-P4 admission; no new scientific judgment."""

import hashlib

from backend.p4_report_schema import EligibilityEntry, ReportSourceIdentity
from backend.p3_schema import EvidenceVerificationResult
from backend.p2c_schema import CandidateConsolidationResult


def classify_adjudication(item):
    if item.verdict == "concern_refuted" or item.report_eligibility == "not_reportable":
        return "excluded"
    if item.verdict in {"inconclusive", "blocked"}:
        return "internal_only"
    if item.report_eligibility == "requires_human_review":
        return "human_review_required"
    return "auto_draft_candidate"


def source_identity(result: EvidenceVerificationResult, raw_result: str):
    if not result.pdf_sha256 or result.pdf_identity_status != "sha256_matched":
        raise ValueError("P4 requires a verified PDF snapshot")
    return ReportSourceIdentity(document_id=result.document_id,
        snapshot_id=result.snapshot_id, pdf_sha256=result.pdf_sha256,
        source_bundle_id=result.source_bundle_id,
        understanding_run_id=result.understanding_run_id,
        professional_review_run_id=result.professional_review_run_id,
        consolidation_run_id=result.consolidation_run_id,
        evidence_verification_run_id=result.evidence_verification_run_id,
        p3_result_sha256=hashlib.sha256(raw_result.encode()).hexdigest(),
        p3_schema_version=result.schema_version,
        p3_execution_mode=result.execution_mode)


def eligibility_entries(result: EvidenceVerificationResult,
                        consolidation: CandidateConsolidationResult):
    issues = {item.consolidated_issue_id: item for item in consolidation.consolidated_issues}
    tasks = {item.verification_task_id: item for item in consolidation.verification_tasks}
    if len(result.adjudications) != len(tasks):
        raise ValueError("P3 adjudications do not cover the frozen tasks")
    entries = []
    for item in result.adjudications:
        issue = issues.get(item.consolidated_issue_id)
        task = tasks.get(item.verification_task_id)
        if not issue or not task or task.consolidated_issue_id != issue.consolidated_issue_id:
            raise ValueError("P3 issue or task does not belong to this consolidation")
        entries.append(EligibilityEntry(consolidated_issue_id=item.consolidated_issue_id,
            verification_task_id=item.verification_task_id, title=issue.title,
            scientific_question=item.revised_question or item.original_central_question,
            verdict=item.verdict, report_eligibility=item.report_eligibility,
            human_review_status=item.human_review_status,
            processing_status=classify_adjudication(item),
            source_refs=list(dict.fromkeys(item.supporting_evidence + item.original_source_refs)),
            counterevidence_refs=list(dict.fromkeys(item.counterevidence +
                                                     item.original_counterevidence_refs)),
            outstanding_checks=item.outstanding_checks,
            provisional_impact=item.provisional_impact))
    if len({item.consolidated_issue_id for item in entries}) != len(entries):
        raise ValueError("duplicate P3 adjudication")
    return entries
