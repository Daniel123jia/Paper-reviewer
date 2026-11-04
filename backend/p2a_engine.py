"""Deterministic N1–N3 pipeline over P1 reading units; no review findings."""

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from backend.parse_quality import assess_input_readiness
from backend.p2a_schema import (ClaimEvidenceMap, PaperProfile, ReviewPlan,
                                 source_reference_index, validate_source_references)
from backend.review import parse_model_json


PROMPTS = Path(__file__).with_name("prompts")
VERSIONS = {"paper-type-planner": "paper-type-planner/v1",
            "paper-understanding": "paper-understanding/v1",
            "contribution-evidence-mapper": "contribution-evidence-mapper/v1"}
RESULT_METADATA = {"document_id", "source_bundle_id", "review_run_id", "prompt_version",
                   "schema_version", "model_identifier", "input_coverage", "execution_status",
                   "evidence_refs"}
SCALAR_FACTS = ("research_problem", "research_motivation",
                "research_gap_claimed_by_authors", "proposed_method")
LIST_FACTS = ("method_components", "theoretical_assumptions", "experimental_setup",
              "datasets_and_metrics", "main_results", "author_claimed_contributions",
              "conclusions", "author_stated_limitations")


@dataclass
class CallTrace:
    stage: str
    prompt_version: str
    requested_model: str
    actual_model: str | None
    input_tokens: int | None
    output_tokens: int | None
    duration_ms: int
    status: str
    error: str | None = None


class P2AError(RuntimeError):
    def __init__(self, stage: str, message: str, traces: list[CallTrace]):
        super().__init__(f"{stage}: {message}")
        self.stage = stage
        self.traces = traces


def chunk_reading_units(units: list[dict], max_chars: int = 12000) -> list[list[dict]]:
    chunks = []
    current = []
    size = 0
    for unit in units:
        length = len(_unit_line(unit))
        if current and size + length > max_chars:
            chunks.append(current)
            current = []
            size = 0
        current.append(unit)
        size += length
    if current:
        chunks.append(current)
    return chunks


def _planning_units(bundle, chunks):
    if len(chunks) == 1:
        return chunks[0]
    units = bundle["reading_units"]
    selected = set()
    for section in bundle["sections"]:
        candidates = [unit for unit in units if unit.get("section_id") == section["id"] and
                      unit["text"].strip() != section["title"].strip()]
        selected.update(unit["id"] for unit in candidates[:2])
    selected.update(unit["id"] for unit in units if unit.get("section_id") is None and
                    unit["page_index"] == 0)
    if not selected:
        selected.update(unit["id"] for unit in chunks[0])
    return [unit for unit in units if unit["id"] in selected]


def _unit_line(unit):
    detail = f' table={unit["table_id"]}' if unit.get("table_id") else ""
    return (f'[{unit["id"]}] page={unit["page_index"] + 1} section={unit.get("section_id")}'
            f' evidence={",".join(unit["evidence_ids"])} order={unit.get("reading_order_status")}{detail}\n'
            f'{unit["text"]}\n')


def _source_text(units):
    return "\n".join(_unit_line(unit) for unit in units)


def _content_json(result):
    return json.dumps(result.model_dump(exclude=RESULT_METADATA), ensure_ascii=False)


def _object_catalog(bundle):
    return "\n".join(
        f'[{item["id"]}] type={kind} page={item["page_index"] + 1} '
        f'status={item.get("usability_status", item.get("status", "unverified"))} '
        f'evidence={",".join(item.get("evidence_ids") or [item.get("caption_evidence_id", "")])}'
        for kind, items in (("table", bundle["tables"]), ("formula", bundle["formulas"]),
                            ("figure", bundle["figures"])) for item in items)


def _coverage(bundle, included_units, quality):
    included = {unit["id"] for unit in included_units}
    return {"total_unit_count": len(bundle["reading_units"]),
            "included_unit_count": len(included),
            "excluded_unit_ids": [unit["id"] for unit in bundle["reading_units"] if unit["id"] not in included],
            "source_quality_status": quality["status"],
            "limitations": list(dict.fromkeys(bundle.get("limitations", []) + quality["reasons"]))}


def _metadata(bundle, run_id, stage, actual_model, included_units, quality):
    coverage = _coverage(bundle, included_units, quality)
    return {"document_id": bundle["document"]["id"], "source_bundle_id": bundle["bundle_id"],
            "review_run_id": run_id, "prompt_version": VERSIONS[stage], "schema_version": "1.0",
            "model_identifier": actual_model, "input_coverage": coverage,
            "execution_status": "completed" if quality["status"] == "ready" and
                                not coverage["excluded_unit_ids"] else "limited"}


def _referenced_ids(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key == "evidence_ref_ids":
                yield from child
            elif key == "evidence_ref_id":
                yield child
            else:
                yield from _referenced_ids(child)
    elif isinstance(value, list):
        for child in value:
            yield from _referenced_ids(child)


def _with_references(payload, bundle):
    if "evidence_refs" in payload:
        raise ValueError("model must cite source IDs; canonical references are resolved by the server")
    index = source_reference_index(bundle)
    cited = list(dict.fromkeys(_referenced_ids(payload)))
    missing = [reference_id for reference_id in cited if reference_id not in index]
    if missing:
        raise ValueError(f"missing source object: {', '.join(missing[:5])}")
    return {**payload, "evidence_refs": [index[reference_id].model_dump() for reference_id in cited]}


def _invoke(stage: str, user_text: str, bundle: dict, run_id: str, included_units: list[dict],
            quality: dict, requested_model: str, model_call: Callable, traces: list[CallTrace],
            result_type, on_trace: Callable | None = None):
    system = (PROMPTS / f"{stage}.v1.txt").read_text(encoding="utf-8")
    for attempt in range(2):
        reply = None
        started = time.perf_counter()
        try:
            retry_instruction = ("\nPrevious response failed output validation. Return exactly the required "
                                 "JSON fields and cite only source IDs shown in the input.") if attempt else ""
            reply = model_call(stage, system, user_text + retry_instruction)
            content = reply["content"]
            actual = reply["model_identifier"]
            if not actual or not content:
                raise ValueError("model response has no model identifier or content")
            payload = parse_model_json(content)
            if not isinstance(payload, dict):
                raise ValueError("model response must be a JSON object")
            if result_type is not ReviewPlan:
                payload = _with_references(payload, bundle)
            result = result_type.model_validate({**payload, **_metadata(
                bundle, run_id, stage, actual, included_units, quality)})
            if result_type is not ReviewPlan:
                validate_source_references(result, bundle)
            trace = CallTrace(stage, VERSIONS[stage], requested_model, actual,
                              reply.get("input_tokens"), reply.get("output_tokens"),
                              reply.get("duration_ms", 0), "completed")
            traces.append(trace)
            if on_trace:
                on_trace(trace)
            return result
        except Exception as exc:
            error_code = (f"invalid_output:{type(exc).__name__}" if
                          isinstance(exc, (ValueError, TypeError)) else
                          f"model_call_failed:{type(exc).__name__}")
            trace = CallTrace(stage, VERSIONS[stage], requested_model,
                              reply.get("model_identifier") if isinstance(reply, dict) else None,
                              reply.get("input_tokens") if isinstance(reply, dict) else None,
                              reply.get("output_tokens") if isinstance(reply, dict) else None,
                              reply.get("duration_ms", 0) if isinstance(reply, dict) else
                              round((time.perf_counter() - started) * 1000),
                              "invalid_output" if error_code.startswith("invalid_output") else "failed",
                              error_code)
            traces.append(trace)
            if on_trace:
                on_trace(trace)
            if attempt == 1:
                raise P2AError(stage, error_code, traces) from exc


def _global_profile(partials, context, bundle, run_id, quality, requested_model,
                    model_call, traces, on_trace):
    if len(partials) == 1:
        return partials[0]
    summaries = [{**{field: (getattr(profile, field).model_dump() if getattr(profile, field)
                             else None) for field in SCALAR_FACTS},
                  "selected_facts": {field: [fact.model_dump() for fact in getattr(profile, field)[:2]]
                                     for field in ("method_components", "main_results",
                                                   "author_claimed_contributions", "conclusions")},
                  "source_gaps": profile.unresolved_source_gaps[:4]}
                 for profile in partials]
    fallback = False
    try:
        global_result = _invoke(
            "paper-understanding", context +
            "\nGlobal synthesis of all parts. Reconcile only the first four scalar facts "
            "and unresolved source gaps. Remove gaps caused only by chunk partitioning. "
            "Return empty arrays for fact lists; the server preserves all part-level facts. "
            "Cite the provided source IDs.\n" + json.dumps(summaries, ensure_ascii=False),
            bundle, run_id, bundle["reading_units"], quality, requested_model,
            model_call, traces, PaperProfile, on_trace)
    except P2AError:
        global_result = partials[0]
        fallback = True
    payload = global_result.model_dump(exclude=RESULT_METADATA)
    if not fallback:
        for field in SCALAR_FACTS:
            if payload[field]:
                payload[field]["provenance"] = "model_interpretation"
    for field in LIST_FACTS:
        unique = {}
        for profile in partials:
            for fact in getattr(profile, field):
                key = (fact.text.casefold().strip(), fact.provenance)
                if key not in unique:
                    unique[key] = fact.model_copy(deep=True)
                else:
                    unique[key].evidence_ref_ids = list(dict.fromkeys(
                        unique[key].evidence_ref_ids + fact.evidence_ref_ids))
        payload[field] = [fact.model_dump() for fact in unique.values()]
    gaps = ([gap for profile in partials for gap in profile.unresolved_source_gaps]
            if fallback else list(global_result.unresolved_source_gaps))
    gaps.extend(f"Source quality: {reason}" for reason in quality["reasons"])
    for kind, objects in (("table", bundle["tables"]), ("formula", bundle["formulas"]),
                          ("figure", bundle["figures"])):
        gaps.extend(f"{kind} {item['id']}: {item.get('usability_status', 'unverified')}"
                    for item in objects if item.get("usability_status") != "verified")
    if fallback:
        gaps.append("Global scalar synthesis failed; scalar fields use the first part only.")
    payload["unresolved_source_gaps"] = list(dict.fromkeys(gaps))
    metadata = _metadata(bundle, run_id, "paper-understanding", global_result.model_identifier,
                         bundle["reading_units"], quality)
    if fallback:
        metadata["execution_status"] = "limited"
    result = PaperProfile.model_validate({**_with_references(payload, bundle),
                                          **metadata})
    validate_source_references(result, bundle)
    return result


def _merge_claim_maps(partials, bundle, run_id, quality):
    if len(partials) == 1:
        return partials[0]
    payload = {"contributions": [], "claims": [], "support_relations": [], "evidence_gaps": []}
    for index, mapping in enumerate(partials, start=1):
        contribution_ids = {item.id: f"part{index}_{item.id}" for item in mapping.contributions}
        claim_ids = {item.id: f"part{index}_{item.id}" for item in mapping.claims}
        payload["contributions"].extend({**item.model_dump(), "id": contribution_ids[item.id]}
                                        for item in mapping.contributions)
        payload["claims"].extend({**item.model_dump(), "id": claim_ids[item.id],
                                  "contribution_id": contribution_ids[item.contribution_id]}
                                 for item in mapping.claims)
        payload["support_relations"].extend({**item.model_dump(),
                                             "claim_id": claim_ids[item.claim_id]}
                                            for item in mapping.support_relations)
        payload["evidence_gaps"].extend({**item.model_dump(), "claim_id": claim_ids[item.claim_id]}
                                        for item in mapping.evidence_gaps)
    result = ClaimEvidenceMap.model_validate({**_with_references(payload, bundle),
                                              **_metadata(bundle, run_id,
                                                          "contribution-evidence-mapper",
                                                          partials[-1].model_identifier,
                                                          bundle["reading_units"], quality)})
    validate_source_references(result, bundle)
    return result


def _profile_context_for_part(profile, part):
    """Use every profile fact in its source part, plus global contribution context."""
    unit_ids = {unit["id"] for unit in part}
    evidence_ids = {source_id for unit in part for source_id in unit["evidence_ids"]}
    references = {item.id: item for item in profile.evidence_refs}

    def local(fact):
        return any(source_id in unit_ids or source_id in evidence_ids or
                   evidence_ids.intersection(references[source_id].evidence_ids)
                   for source_id in fact.evidence_ref_ids if source_id in references)

    payload = {field: (getattr(profile, field).model_dump() if getattr(profile, field) else None)
               for field in SCALAR_FACTS}
    for field in LIST_FACTS:
        payload[field] = [fact.model_dump() for fact in getattr(profile, field)
                          if field == "author_claimed_contributions" or local(fact)]
    return json.dumps(payload, ensure_ascii=False)


def run_p2a(bundle: dict, run_id: str, model_call: Callable, requested_model: str,
            target_journal: str | None = None, special_requirements: str | None = None,
            chunk_chars: int = 12000, on_result: Callable | None = None,
            on_trace: Callable | None = None):
    """Run N1–N3; caller persists results and trace metadata in private storage."""
    units = bundle["reading_units"]
    if not units:
        raise P2AError("source", "no readable reading units", [])
    quality = assess_input_readiness(bundle, "section_summary")
    if quality["status"] == "blocked":
        raise P2AError("source", "required source scope is blocked", [])
    chunks = chunk_reading_units(units, chunk_chars)
    plan_units = _planning_units(bundle, chunks)
    traces = []
    context = (f'Document {bundle["document"]["id"]}; Source Bundle {bundle["bundle_id"]}.\n'
               f'Quality: {json.dumps(quality, ensure_ascii=False)}\n'
               f'Journal: {target_journal or "not supplied"}\n'
               f'User requirements: {special_requirements or "not supplied"}\n'
               f'Sections: {json.dumps([item["title"] for item in bundle["sections"]], ensure_ascii=False)}\n'
               f'Technical object status:\n{_object_catalog(bundle)}\n')
    section_counts = {item["id"]: sum(unit["section_id"] == item["id"] for unit in units)
                      for item in bundle["sections"]}
    planning_context = (context + "\nThese reading units are a planning sample only; N2 will read all "
                        "available units. Do not call an unshown section missing. "
                        f"Full Source Bundle unit counts by section: {json.dumps(section_counts)}\n")
    plan = _invoke("paper-type-planner", planning_context + "\nReading units:\n" + _source_text(plan_units),
                   bundle, run_id, plan_units, quality, requested_model, model_call, traces,
                   ReviewPlan, on_trace)
    if on_result:
        on_result("review_plan", plan)
    profile_context = context + "\nReview plan:\n" + _content_json(plan)
    partial_profiles = []

    def profile_part(part, label):
        try:
            return [_invoke(
                "paper-understanding", profile_context + f"\nPart {label}. "
                "Other parts will be processed separately; do not call them missing.\n" +
                _source_text(part), bundle, run_id, part, quality, requested_model,
                model_call, traces, PaperProfile, on_trace)]
        except P2AError as exc:
            if len(part) < 2 or not any(code in str(exc) for code in
                                         ("APIConnectionError", "APITimeoutError")):
                raise
            middle = len(part) // 2
            return profile_part(part[:middle], label + "a") + profile_part(part[middle:], label + "b")

    for index, part in enumerate(chunks):
        partial_profiles.extend(profile_part(part, f"{index + 1}/{len(chunks)}"))
    profile = _global_profile(partial_profiles, profile_context, bundle, run_id, quality,
                              requested_model, model_call, traces, on_trace)
    if on_result:
        on_result("paper_profile", profile)
    map_context = context
    map_chunks = chunk_reading_units(units, min(chunk_chars, 6000))
    partial_maps = []

    def map_part(part, label):
        try:
            return [_invoke(
                "contribution-evidence-mapper", map_context +
                f"\nPart {label}. Map claims from these reading units; "
                "the PaperProfile facts below are projected by their source IDs.\n" +
                _profile_context_for_part(profile, part) + "\nReading units:\n" +
                _source_text(part), bundle, run_id, part, quality, requested_model,
                model_call, traces, ClaimEvidenceMap, on_trace)]
        except P2AError as exc:
            if len(part) < 2 or not any(code in str(exc) for code in
                                         ("APIConnectionError", "APITimeoutError")):
                raise
            middle = len(part) // 2
            return map_part(part[:middle], label + "a") + map_part(part[middle:], label + "b")

    for index, part in enumerate(map_chunks):
        partial_maps.extend(map_part(part, f"{index + 1}/{len(map_chunks)}"))
    mapping = _merge_claim_maps(partial_maps, bundle, run_id, quality)
    if any(gap.startswith("Global scalar synthesis failed") for gap in profile.unresolved_source_gaps):
        mapping.execution_status = "limited"
        mapping.input_coverage.limitations.append("upstream_global_synthesis_failed")
    if on_result:
        on_result("claim_evidence_map", mapping)
    return plan, profile, mapping, traces
