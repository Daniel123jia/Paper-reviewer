"""Deterministic six-dimension P2-B coordinator over one frozen P2-A snapshot."""

import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from backend.p2a_engine import BudgetExceeded, RunBudget
from backend.p2a_schema import (ClaimEvidenceMap, PaperProfile, ReviewPlan,
                                 source_reference_index, validate_source_references)
from backend.p2b_context import DIMENSION_FACT_FIELDS, assemble_context
from backend.p2b_dimensions import DIMENSIONS, applicability, configuration
from backend.p2b_schema import (SCHEMA_VERSION, DimensionReview, IssueCandidate,
                                ProfessionalReviewResult)
from backend.review import parse_model_json


PROMPTS = Path(__file__).with_name("prompts") / "p2b"


@dataclass(frozen=True)
class CallTrace:
    dimension_id: str
    prompt_version: str
    source_bundle_id: str
    source_ref_ids: list[str]
    requested_model: str
    actual_model: str | None
    input_tokens: int | None
    output_tokens: int | None
    duration_ms: int
    status: str
    error: str | None


def validate_understanding_snapshot(bundle: dict, plan: ReviewPlan, profile: PaperProfile,
                                    mapping: ClaimEvidenceMap, understanding_run_id: str,
                                    run_status: str) -> None:
    if run_status != "completed":
        raise ValueError("P2-A understanding run must be completed")
    if not bundle.get("reading_units"):
        raise ValueError("frozen Source Bundle has no readable units")
    for result in (plan, profile, mapping):
        if result.review_run_id != understanding_run_id:
            raise ValueError("P2-A results belong to a different understanding run")
        if result.document_id != bundle["document"]["id"] or result.source_bundle_id != bundle["bundle_id"]:
            raise ValueError("P2-A result and frozen Source Bundle mismatch")
        if result.schema_version not in {"1.0", "1.1"}:
            raise ValueError("unsupported P2-A schema version")
    validate_source_references(profile, bundle)
    validate_source_references(mapping, bundle)


def _prompt(rule, config_name):
    dimension = (PROMPTS / f"{rule.prompt_name}.v1.txt").read_text(encoding="utf-8")
    shared = (PROMPTS / "shared-rules.v1.txt").read_text(encoding="utf-8")
    domain = (PROMPTS / f"{config_name.replace('/', '.')}.txt").read_text(encoding="utf-8")
    return "\n\n".join((shared, domain, dimension)), f"{rule.prompt_name}/v1"


def _dimension_record(rule, bundle, understanding_run_id, run_id, prompt_version,
                      model_identifier, applicability_status, applicability_reason,
                      coverage_status, limitations, execution_status, payload=None):
    payload = payload or {}
    return DimensionReview.model_validate({
        "document_id": bundle["document"]["id"], "source_bundle_id": bundle["bundle_id"],
        "understanding_run_id": understanding_run_id, "professional_review_run_id": run_id,
        "prompt_version": prompt_version, "schema_version": SCHEMA_VERSION,
        "model_identifier": model_identifier, "dimension_id": rule.id, "dimension_name": rule.name,
        "applicability_status": applicability_status, "applicability_reason": applicability_reason,
        "inspection_focus": payload.get("inspection_focus", list(rule.focus)),
        "inspected_claim_ids": payload.get("inspected_claim_ids", []),
        "inspected_source_refs": payload.get("inspected_source_refs", []),
        "coverage_status": coverage_status,
        "limitations": list(dict.fromkeys(limitations + payload.get("limitations", []))),
        "candidates": payload.get("candidates", []),
        "no_issue_explanation": payload.get("no_issue_explanation"),
        "execution_status": execution_status})


def _validated_payload(raw, rule, pack, mapping, bundle, run_id):
    if not isinstance(raw, dict) or set(raw) != {"inspection_focus", "inspected_claim_ids",
        "inspected_source_refs", "limitations", "candidates", "no_issue_explanation"}:
        raise ValueError("invalid dimension output fields")
    known_claims = {item.id for item in mapping.claims}
    allowed_refs = set(pack.source_ref_ids)
    canonical = source_reference_index(bundle)
    if not set(raw["inspected_claim_ids"]).issubset(known_claims):
        raise ValueError("unknown inspected claim")
    if not set(raw["inspected_source_refs"]).issubset(allowed_refs):
        raise ValueError("inspected source was not supplied")
    candidates = []
    for index, item in enumerate(raw["candidates"]):
        if "issue_id" in item or "dimension_id" in item:
            raise ValueError("candidate IDs and dimension are server-assigned")
        identifier = hashlib.sha256((run_id + "|" + rule.id + "|" + str(index) + "|" +
                                     item.get("title", "")).encode()).hexdigest()[:16]
        candidate = IssueCandidate.model_validate({**item, "issue_id": f"candidate_{identifier}",
                                                   "dimension_id": rule.id})
        if not set(candidate.claim_ids).issubset(known_claims):
            raise ValueError("candidate refers to unknown claim")
        for ref in candidate.source_refs + candidate.possible_counterevidence:
            if ref.source_id not in allowed_refs or ref.source_id not in canonical:
                raise ValueError("candidate source is absent from the frozen context")
        candidates.append(candidate)
    return {**raw, "candidates": candidates}


def _project_input(rule, pack, plan, profile, mapping):
    """Send the full claim inventory but detailed facts only for this source scope."""
    allowed = set(pack.source_ref_ids)
    plan_fields = ("paper_type", "research_domain", "methodological_paradigm",
                   "priority_dimensions", "missing_materials")
    profile_fields = DIMENSION_FACT_FIELDS[rule.id]
    profile_payload = {field: [item.model_dump() for item in value] if isinstance(value, list)
                       else value.model_dump() if value else None
                       for field in profile_fields for value in [getattr(profile, field)]}
    profile_payload["unresolved_source_gaps"] = profile.unresolved_source_gaps
    if rule.id in {"D3", "D4"}:
        profile_payload["source_conflicts"] = [item.model_dump() for item in profile.source_conflicts]
    selected = {claim.id for claim in mapping.claims if allowed.intersection(claim.evidence_ref_ids)}
    selected.update(relation.claim_id for relation in mapping.support_relations
                    if relation.evidence_ref_id in allowed)
    claims = [{**claim.model_dump(), "evidence_ref_ids":
               [ref for ref in claim.evidence_ref_ids if ref in allowed]}
              for claim in mapping.claims if claim.id in selected]
    parents = {claim.contribution_id for claim in mapping.claims if claim.id in selected}
    contributions = [{**item.model_dump(), "evidence_ref_ids":
                      [ref for ref in item.evidence_ref_ids if ref in allowed]}
                     for item in mapping.contributions if item.id in parents]
    return {"plan": {field: getattr(plan, field) for field in plan_fields},
            "profile": profile_payload,
            "claim_map": {"contributions": contributions, "claims": claims,
                          "support_relations": [{"claim_id": item.claim_id,
                                                 "evidence_ref_id": item.evidence_ref_id,
                                                 "relation": item.relation}
                                                for item in mapping.support_relations
                                                if item.claim_id in selected and
                                                item.evidence_ref_id in allowed],
                          "evidence_gaps": [item.model_dump() for item in mapping.evidence_gaps
                                            if item.claim_id in selected]},
            "claim_inventory": [{"id": item.id, "contribution_id": item.contribution_id,
                                 "statement": item.statement} for item in mapping.claims
                                if item.id not in selected]}


def run_p2b(bundle: dict, plan: ReviewPlan, profile: PaperProfile, mapping: ClaimEvidenceMap,
            understanding_run_id: str, run_id: str, model_call: Callable, requested_model: str,
            target_journal: str | None = None, special_requirements: str | None = None,
            budget: RunBudget | None = None, on_dimension: Callable | None = None,
            on_trace: Callable | None = None, max_context_chars: int = 28000):
    validate_understanding_snapshot(bundle, plan, profile, mapping, understanding_run_id, "completed")
    budget = budget or RunBudget(max_calls=18, max_input_tokens=180000,
                                 max_output_tokens=50000, max_duration_ms=1800000)
    config_name, domain_limits = configuration(plan)
    input_limits = list(dict.fromkeys(domain_limits + bundle.get("limitations", []) +
                                      profile.unresolved_source_gaps +
                                      profile.input_coverage.limitations +
                                      mapping.input_coverage.limitations))
    if any(item.execution_status == "limited" for item in (plan, profile, mapping)):
        input_limits.append("p2a_limited")
    dimensions = []
    traces = []
    budget_exhausted = False
    started = time.perf_counter()

    def record(dimension):
        dimensions.append(dimension)
        if on_dimension:
            on_dimension(dimension)

    for rule in DIMENSIONS:
        status, reason = applicability(rule, plan)
        system, version = _prompt(rule, config_name)
        if status == "not_applicable":
            record(_dimension_record(rule, bundle, understanding_run_id, run_id, version,
                None, status, reason, "blocked", input_limits, "skipped",
                {"no_issue_explanation": reason}))
            continue
        pack = assemble_context(rule.id, bundle, plan, profile, mapping, max_context_chars)
        limits = list(dict.fromkeys(input_limits + pack.limitations))
        if budget_exhausted or pack.coverage_status == "blocked":
            state = "budget_exceeded" if budget_exhausted else "limited"
            record(_dimension_record(rule, bundle, understanding_run_id, run_id, version,
                None, status, reason, pack.coverage_status, limits, state,
                {"no_issue_explanation": "Review not performed: budget exhausted" if budget_exhausted
                 else "Review not performed: source material blocked"}))
            continue
        user = json.dumps({"configuration": config_name,
            **_project_input(rule, pack, plan, profile, mapping),
            "target_journal": target_journal, "special_requirements": special_requirements,
            "context": pack.text, "allowed_source_ids": pack.source_ref_ids,
            "coverage_status": pack.coverage_status}, ensure_ascii=False, separators=(",", ":"))
        result = None
        last_error = None
        for attempt in range(2):
            reply = None
            begun = time.perf_counter()
            try:
                reply = budget.call(rule.id, system, user + ("\nReturn valid JSON only." if attempt else ""),
                                    model_call)
                if not reply.get("content") or not reply.get("model_identifier"):
                    raise ValueError("model response missing content or identifier")
                raw = parse_model_json(reply["content"])
                payload = _validated_payload(raw, rule, pack, mapping, bundle, run_id)
                state = "limited" if pack.coverage_status != "sufficient" or input_limits else "completed"
                result = _dimension_record(rule, bundle, understanding_run_id, run_id, version,
                    reply["model_identifier"], status, reason, pack.coverage_status,
                    limits, state, payload)
                trace_status, last_error = "completed", None
            except BudgetExceeded:
                budget_exhausted = True
                last_error = "budget_exceeded"
                break
            except Exception as exc:
                trace_status = "invalid_output" if isinstance(exc, (ValueError, TypeError)) else "failed"
                last_error = f"{trace_status}:{type(exc).__name__}"
            trace = CallTrace(rule.id, version, bundle["bundle_id"], pack.source_ref_ids,
                              requested_model, reply.get("model_identifier") if isinstance(reply, dict) else None,
                              reply.get("input_tokens") if isinstance(reply, dict) else None,
                              reply.get("output_tokens") if isinstance(reply, dict) else None,
                              reply.get("duration_ms", round((time.perf_counter() - begun) * 1000))
                              if isinstance(reply, dict) else round((time.perf_counter() - begun) * 1000),
                              trace_status, last_error)
            traces.append(trace)
            if on_trace:
                on_trace(trace)
            if result:
                break
        if result is None:
            result = _dimension_record(rule, bundle, understanding_run_id, run_id, version,
                None, status, reason, pack.coverage_status, limits + ([last_error] if last_error else []),
                "budget_exceeded" if budget_exhausted else "failed")
        record(result)
    failures = [item for item in dimensions if item.execution_status == "failed"]
    if budget_exhausted:
        overall = "budget_exceeded"
    elif failures:
        overall = "failed" if len(failures) == len([item for item in dimensions if
                                                   item.applicability_status != "not_applicable"]) else "partial_failed"
    elif input_limits or any(item.execution_status == "limited" for item in dimensions):
        overall = "limited"
    else:
        overall = "completed"
    incomplete = [f"{item.dimension_id}:{item.execution_status}" for item in dimensions
                  if item.execution_status not in {"completed", "skipped"}]
    known_inputs = [item.input_tokens for item in traces if item.input_tokens is not None]
    known_outputs = [item.output_tokens for item in traces if item.output_tokens is not None]
    return ProfessionalReviewResult.model_validate({
        "document_id": bundle["document"]["id"], "source_bundle_id": bundle["bundle_id"],
        "understanding_run_id": understanding_run_id, "professional_review_run_id": run_id,
        "model_identifier": requested_model, "dimensions": dimensions,
        "candidates": [candidate for item in dimensions for candidate in item.candidates],
        "incomplete_items": incomplete, "input_limitations": input_limits,
        "call_count": budget.calls, "input_tokens": sum(known_inputs) if known_inputs else None,
        "output_tokens": sum(known_outputs) if known_outputs else None,
        "unknown_token_calls": budget.unknown_usage_calls,
        "duration_ms": round((time.perf_counter() - started) * 1000),
        "execution_status": overall})
