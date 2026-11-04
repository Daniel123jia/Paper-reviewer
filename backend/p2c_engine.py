"""P2-C coordinator: inventory, conservative grouping, coverage audit, P3 handoff."""

import hashlib

from backend.p2a_engine import RunBudget
from backend.p2a_schema import source_reference_index
from backend.p2c_coverage import audit_coverage
from backend.p2c_grouping import run_semantic_grouping
from backend.p2c_normalizer import normalize_candidates
from backend.p2c_schema import CandidateConsolidationResult, VerificationTask


def _tasks(inventory, issues, review, bundle):
    canonical = source_reference_index(bundle)
    snapshot = {"document_id": inventory.document_id,
                "snapshot_id": inventory.snapshot_id,
                "pdf_sha256": inventory.pdf_sha256,
                "source_bundle_id": inventory.source_bundle_id}
    tasks = []
    for issue in issues:
        source_ids = {item.source_id for item in issue.original_source_refs +
                      issue.counterevidence_refs}
        visual = any(canonical[source_id].object_type in {"table", "formula", "figure"}
                     for source_id in source_ids)
        materials = []
        if visual:
            materials.append("Original PDF visual inspection of cited table, formula or figure")
        if issue.external_verification_required:
            materials.append("External literature, code or dataset relevant to this question")
        identifier = hashlib.sha256((issue.consolidated_issue_id + "|verification/v1").encode()).hexdigest()[:20]
        tasks.append(VerificationTask(
            verification_task_id=f"verify_{identifier}",
            consolidated_issue_id=issue.consolidated_issue_id,
            central_question=issue.central_question,
            related_claim_ids=issue.related_claim_ids,
            original_candidate_ids=issue.source_candidate_ids,
            source_refs_to_check=issue.original_source_refs,
            counterevidence_refs_to_check=issue.counterevidence_refs,
            original_document_snapshot=snapshot,
            required_checks=list(dict.fromkeys(["Confirm the observed facts against the original PDF",
                                                  *issue.verification_questions])),
            additional_materials_needed=materials,
            external_verification_required=issue.external_verification_required,
            priority_for_verification="high" if issue.potential_impact == "major_candidate"
            else "normal" if issue.potential_impact == "minor_candidate" else "undetermined",
            limitations=list(dict.fromkeys(issue.source_limitations + review.input_limitations)),
            verification_status="unverified"))
    return tasks


def validate_consolidation_result(result, bundle, mapping):
    """Check all generated refs against the exact frozen PDF snapshot."""
    canonical = source_reference_index(bundle)
    claims = {item.id: item for item in mapping.claims}
    contributions = {item.id for item in mapping.contributions}
    expected_snapshot = {"document_id": bundle["document"]["id"],
                         "snapshot_id": bundle["document"]["snapshot_id"],
                         "pdf_sha256": bundle["document"].get("sha256"),
                         "source_bundle_id": bundle["bundle_id"]}
    for issue in result.consolidated_issues:
        if not set(issue.related_claim_ids).issubset(claims):
            raise ValueError("consolidated issue cites unknown claim")
        if not set(issue.related_contribution_ids).issubset(contributions):
            raise ValueError("consolidated issue cites unknown contribution")
        if {claims[claim_id].contribution_id for claim_id in issue.related_claim_ids} != \
                set(issue.related_contribution_ids):
            raise ValueError("consolidated issue claim-contribution mismatch")
        if any(ref.source_id not in canonical for ref in
               issue.original_source_refs + issue.counterevidence_refs):
            raise ValueError("consolidated issue cites source outside frozen bundle")
    issues = {item.consolidated_issue_id: item for item in result.consolidated_issues}
    for task in result.verification_tasks:
        issue = issues[task.consolidated_issue_id]
        if (task.original_document_snapshot != expected_snapshot or
                task.source_refs_to_check != issue.original_source_refs or
                task.counterevidence_refs_to_check != issue.counterevidence_refs or
                task.original_candidate_ids != issue.source_candidate_ids):
            raise ValueError("verification task lost snapshot, lineage or evidence")
    CandidateConsolidationResult.model_validate(result.model_dump())


def run_p2c(bundle, plan, profile, mapping, review, understanding_run_id,
            professional_review_run_id, consolidation_run_id, model_call,
            requested_model, p2b_status, budget: RunBudget | None = None,
            on_inventory=None, on_batch=None, on_trace=None):
    inventory = normalize_candidates(bundle, plan, profile, mapping, review,
        understanding_run_id, professional_review_run_id, p2b_status)
    if on_inventory:
        on_inventory(inventory)
    grouping = run_semantic_grouping(inventory, mapping, model_call, requested_model,
                                     budget=budget, on_trace=on_trace, on_batch=on_batch)
    audit = audit_coverage(bundle, plan, profile, mapping, review, inventory)
    tasks = _tasks(inventory, grouping.issues, review, bundle)
    incomplete = list(dict.fromkeys(review.incomplete_items + grouping.incomplete_items))
    if p2b_status == "limited":
        incomplete.append("p2b_input_limited")
    if grouping.status == "budget_exceeded":
        status = "budget_exceeded"
    elif grouping.status != "completed" or p2b_status == "limited" or audit.limitations or audit.gaps:
        status = "limited"
    else:
        status = "completed"
    result = CandidateConsolidationResult(
        document_id=inventory.document_id, snapshot_id=inventory.snapshot_id,
        pdf_sha256=inventory.pdf_sha256, source_bundle_id=inventory.source_bundle_id,
        understanding_run_id=understanding_run_id,
        professional_review_run_id=professional_review_run_id,
        consolidation_run_id=consolidation_run_id,
        prompt_versions=grouping.prompt_versions, model_identifier=requested_model,
        inventory=inventory, relations=grouping.relations,
        consolidated_issues=grouping.issues, lineage=grouping.lineage,
        coverage_audit=audit, verification_tasks=tasks,
        incomplete_items=incomplete, call_count=grouping.call_count,
        input_tokens=grouping.input_tokens, output_tokens=grouping.output_tokens,
        unknown_token_calls=grouping.unknown_token_calls,
        duration_ms=grouping.duration_ms, retry_count=grouping.retry_count,
        execution_status=status)
    validate_consolidation_result(result, bundle, mapping)
    return result
