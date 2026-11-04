"""Audit reported P2-B inspection and frozen-source limits without creating new issues."""

import hashlib

from backend.p2a_schema import source_reference_index
from backend.p2b_dimensions import DIMENSIONS, configuration
from backend.p2c_schema import (ClaimCoverage, ContributionCoverage, CoverageAudit,
                                 CoverageGap, DimensionCoverage, FocusCoverage)


def _unique(values):
    return list(dict.fromkeys(values))


def _gap(bundle, gap_type, description, cause, next_action, dimensions=(), claims=(),
         contributions=(), limitations=()):
    key = "|".join((bundle["bundle_id"], gap_type, description,
                    ",".join(dimensions), ",".join(claims), ",".join(contributions)))
    identifier = hashlib.sha256(key.encode()).hexdigest()[:20]
    return CoverageGap(gap_id=f"coverage_gap_{identifier}", gap_type=gap_type,
        affected_contribution_ids=list(contributions), affected_claim_ids=list(claims),
        affected_dimension_ids=list(dimensions), description=description, cause=cause,
        source_limitations=list(limitations), next_action=next_action, status="unresolved")


def audit_coverage(bundle, plan, profile, mapping, review, inventory) -> CoverageAudit:
    """Inspection is a model report, never proof that the source or science was verified."""
    rules = {rule.id: rule for rule in DIMENSIONS}
    canonical = source_reference_index(bundle)
    claims = {item.id: item for item in mapping.claims}
    claim_gaps = {}
    for item in mapping.evidence_gaps:
        claim_gaps.setdefault(item.claim_id, []).append(item.description)
    dimensions = []
    focus = []
    gaps = []
    inspected = {claim_id: [] for claim_id in claims}
    for dimension in review.dimensions:
        reported = dimension.execution_status in {"completed", "limited"}
        dimensions.append(DimensionCoverage(
            dimension_id=dimension.dimension_id,
            execution_status=dimension.execution_status,
            applicability_status=dimension.applicability_status,
            coverage_status=dimension.coverage_status,
            candidate_count=len(dimension.candidates),
            no_issue_explanation=dimension.no_issue_explanation,
            evidence_level="model_report_only" if reported else "not_reported",
            limitations=dimension.limitations))
        if reported:
            for claim_id in dimension.inspected_claim_ids:
                inspected[claim_id].append(dimension.dimension_id)
        elif dimension.applicability_status != "not_applicable":
            gaps.append(_gap(bundle, "dimension_unfinished",
                f"{dimension.dimension_id} was not completed ({dimension.execution_status})",
                "model_run_or_source_block", "Rerun or inspect this review dimension",
                dimensions=[dimension.dimension_id], limitations=dimension.limitations))
        rule = rules[dimension.dimension_id]
        for index, name in enumerate(rule.focus):
            if dimension.applicability_status == "not_applicable":
                status, explanation = "not_applicable", dimension.applicability_reason
            elif not reported or not dimension.inspection_focus:
                status, explanation = "coverage_gap", "No completed model report for this focus"
                gaps.append(_gap(bundle, "focus_not_inspected",
                    f"{dimension.dimension_id} focus not reported: {name}",
                    "dimension_unfinished", "Inspect this applicable focus",
                    dimensions=[dimension.dimension_id], limitations=dimension.limitations))
            elif dimension.coverage_status == "sufficient":
                status, explanation = "reported_inspected", "Model reported inspection; not independently verified"
            else:
                status, explanation = "reported_partial", "Dimension reported inspection with partial source coverage; focus-specific completeness is unproven"
            focus.append(FocusCoverage(focus_id=f"{dimension.dimension_id}:{index + 1}",
                                       dimension_id=dimension.dimension_id,
                                       status=status, explanation=explanation))

    claim_records = []
    for claim in mapping.claims:
        candidate_ids = inventory.claim_index[claim.id]
        object_limits = [f"{ref_id}:{canonical[ref_id].verification_status}"
                         for ref_id in claim.evidence_ref_ids if ref_id in canonical and
                         canonical[ref_id].object_type in {"table", "formula", "figure"}]
        limits = _unique(claim_gaps.get(claim.id, []) + object_limits)
        reported_dimensions = _unique(inspected[claim.id] +
                                      [item.dimension_id for item in inventory.candidates
                                       if item.issue_id in candidate_ids])
        if not reported_dimensions:
            status = "not_reported_inspected"
            gaps.append(_gap(bundle, "claim_not_inspected", f"Claim {claim.id} has no completed inspection record",
                "no_model_report", "Inspect the claim using its original source evidence",
                claims=[claim.id], contributions=[claim.contribution_id], limitations=limits))
        elif limits:
            status = "source_limited"
        elif candidate_ids:
            status = "reported_inspected_with_issue"
        else:
            status = "reported_inspected_no_issue"
        claim_records.append(ClaimCoverage(claim_id=claim.id,
            contribution_id=claim.contribution_id,
            inspected_by_dimensions=reported_dimensions, candidate_ids=candidate_ids,
            status=status, evidence_level="model_report_only" if reported_dimensions else "not_reported",
            limitations=limits))
        if limits:
            gaps.append(_gap(bundle, "source_quality",
                f"Claim {claim.id} has unresolved source evidence: {'; '.join(limits)}",
                "p2a_evidence_gap_or_visual_object", "Verify the cited PDF region before relying on this claim",
                dimensions=reported_dimensions, claims=[claim.id],
                contributions=[claim.contribution_id], limitations=limits))

    contributions = []
    by_claim = {item.claim_id: item for item in claim_records}
    for contribution in mapping.contributions:
        related = [item for item in mapping.claims if item.contribution_id == contribution.id]
        records = [by_claim[item.id] for item in related]
        statuses = {item.status for item in records}
        if not records or "not_reported_inspected" in statuses:
            status = "not_reported_inspected"
        elif "source_limited" in statuses:
            status = "source_limited"
        elif any(item.candidate_ids for item in records):
            status = "reported_inspected_with_issue"
        else:
            status = "reported_inspected_no_issue"
        contributions.append(ContributionCoverage(
            contribution_id=contribution.id, claim_ids=[item.id for item in related],
            inspected_claim_ids=[item.claim_id for item in records if item.inspected_by_dimensions],
            candidate_ids=inventory.contribution_index[contribution.id], status=status,
            limitations=_unique(limit for item in records for limit in item.limitations)))

    source_limits = _unique(bundle.get("limitations", []) + review.input_limitations +
        profile.unresolved_source_gaps + profile.input_coverage.limitations +
        mapping.input_coverage.limitations)
    if profile.input_coverage.source_quality_status != "ready":
        source_limits.append(f"source_quality_status:{profile.input_coverage.source_quality_status}")
    source_limits = _unique(source_limits)
    if source_limits:
        gaps.append(_gap(bundle, "source_quality", "P2-B input has unresolved source limitations",
            "frozen_source_or_p2a_limit", "Review original PDF and omitted/visual regions as needed",
            dimensions=[item.dimension_id for item in review.dimensions
                        if item.applicability_status != "not_applicable"], limitations=source_limits))
    if plan.missing_materials:
        gaps.append(_gap(bundle, "required_material_missing",
            "ReviewPlan lists required materials not available in this run",
            "missing_materials", "Obtain or explicitly scope out the required material",
            limitations=plan.missing_materials))
    external = [item for item in inventory.candidates if item.external_verification_required]
    if external:
        gaps.append(_gap(bundle, "external_material", "Candidate questions require external material",
            "candidate_external_verification_flag", "Check cited literature, code or data in P3",
            dimensions=_unique(item.dimension_id for item in external),
            claims=_unique(claim_id for item in external for claim_id in item.claim_ids)))
    _, domain_limits = configuration(plan)
    if domain_limits:
        gaps.append(_gap(bundle, "specialist_rules_unavailable",
            "Only generic academic rules were available for this research domain",
            "domain_rule_configuration", "Add domain rules before claiming specialist coverage",
            limitations=domain_limits))
    return CoverageAudit(dimensions=dimensions, contributions=contributions,
        claims=claim_records, professional_focus=focus, gaps=gaps,
        limitations=source_limits)
