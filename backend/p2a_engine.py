"""Deterministic N1–N3 pipeline over P1 reading units; no review findings."""

import json
import hashlib
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from backend.model_runtime import ModelConfigurationError
from scripts.p5c.stage_control import C1ActualBudgetExceeded
from backend.parse_quality import assess_input_readiness
from backend.p2a_schema import (ClaimEvidenceMap, PaperProfile, ReviewPlan, SourceConflict,
                                 source_reference_index, validate_source_references)
from backend.review import parse_model_json


PROMPTS = Path(__file__).with_name("prompts")
VERSIONS = {"paper-type-planner": "paper-type-planner/v1",
            "paper-understanding": "paper-understanding/v1",
            "contribution-evidence-mapper": "contribution-evidence-mapper/v2",
            "source-consistency": "source-consistency/v1",
            "contribution-normalizer": "contribution-normalizer/v2"}
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


class BudgetExceeded(P2AError):
    pass


class UnknownUsageError(P2AError):
    """A possibly dispatched request has no trustworthy supplier usage."""

    def __init__(self, stage: str, reason: str, *, actual_model: str | None = None,
                 duration_ms: int = 0):
        self.reason = reason
        self.actual_model = actual_model
        self.duration_ms = duration_ms
        super().__init__(stage, f"unknown_usage:{reason}", [])


@dataclass
class RunBudget:
    max_calls: int = 32
    max_input_tokens: int = 300000
    max_output_tokens: int = 100000
    max_duration_ms: int = 1800000
    calls: int = 0
    reserved_input_tokens: int = 0
    reserved_output_tokens: int = 0
    elapsed_ms: int = 0
    unknown_usage_calls: int = 0
    unknown_usage_latched: bool = False

    def _mark_unknown(self, estimated_input, estimated_output):
        self.reserved_input_tokens += estimated_input
        self.reserved_output_tokens += estimated_output
        self.unknown_usage_calls += 1
        self.unknown_usage_latched = True

    def call(self, stage, system, user, model_call):
        if self.unknown_usage_latched:
            raise UnknownUsageError(stage, "previous_call_unsettled")
        estimated_input = max(1, (len(system) + len(user) + 3) // 4)
        estimated_output = 4096
        if (self.calls >= self.max_calls or
                self.reserved_input_tokens + estimated_input > self.max_input_tokens or
                self.reserved_output_tokens + estimated_output > self.max_output_tokens or
                self.elapsed_ms >= self.max_duration_ms):
            raise BudgetExceeded(stage, "budget_exceeded", [])
        self.calls += 1
        started = time.perf_counter()
        try:
            reply = model_call(stage, system, user)
        except C1ActualBudgetExceeded as exc:
            self.elapsed_ms += round((time.perf_counter() - started) * 1000)
            self.reserved_input_tokens += exc.input_tokens
            self.reserved_output_tokens += exc.output_tokens
            raise BudgetExceeded(stage, "actual_budget_exceeded", []) from None
        except ModelConfigurationError:
            # The runtime session validates configuration before SDK dispatch.
            self.calls -= 1
            self.elapsed_ms += round((time.perf_counter() - started) * 1000)
            raise
        except Exception as exc:
            duration_ms = round((time.perf_counter() - started) * 1000)
            self.elapsed_ms += duration_ms
            self._mark_unknown(estimated_input, estimated_output)
            raise UnknownUsageError(stage, type(exc).__name__, duration_ms=duration_ms) from exc

        duration_ms = round((time.perf_counter() - started) * 1000)
        self.elapsed_ms += duration_ms
        try:
            input_tokens = reply.get("input_tokens") if isinstance(reply, dict) else None
            output_tokens = reply.get("output_tokens") if isinstance(reply, dict) else None
            actual_model = reply.get("model_identifier") if isinstance(reply, dict) else None
            usage_valid = (type(input_tokens) is int and input_tokens >= 0 and
                           type(output_tokens) is int and output_tokens >= 0)
        except Exception:
            input_tokens = output_tokens = actual_model = None
            usage_valid = False
        if not usage_valid:
            self._mark_unknown(estimated_input, estimated_output)
            raise UnknownUsageError(
                stage, "invalid_usage",
                actual_model=actual_model if isinstance(actual_model, str) else None,
                duration_ms=duration_ms)
        self.reserved_input_tokens += input_tokens
        self.reserved_output_tokens += output_tokens
        return reply


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
            "review_run_id": run_id, "prompt_version": VERSIONS[stage], "schema_version": "1.1",
            "model_identifier": actual_model, "input_coverage": coverage,
            "execution_status": "completed" if quality["status"] == "ready" and
                                not coverage["excluded_unit_ids"] else "limited"}


def _referenced_ids(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in {"evidence_ref_ids", "left_evidence_ref_ids", "right_evidence_ref_ids"}:
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
    system = (PROMPTS / f"{stage}.{VERSIONS[stage].split('/')[-1]}.txt").read_text(encoding="utf-8")
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
        except ModelConfigurationError:
            raise
        except UnknownUsageError as exc:
            trace = CallTrace(stage, VERSIONS[stage], requested_model, exc.actual_model,
                              None, None, exc.duration_ms, "unknown_usage",
                              f"unknown_usage:{exc.reason}")
            traces.append(trace)
            exc.traces = list(traces)
            if on_trace:
                on_trace(trace)
            raise
        except BudgetExceeded:
            raise
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


def _aux_json(stage, payload, requested_model, model_call, traces, on_trace):
    system = (PROMPTS / f"{stage}.{VERSIONS[stage].split('/')[-1]}.txt").read_text(encoding="utf-8")
    for attempt in range(2):
        started = time.perf_counter()
        reply = None
        try:
            reply = model_call(stage, system, json.dumps(payload, ensure_ascii=False))
            result = parse_model_json(reply["content"])
            if not isinstance(result, dict) or not reply.get("model_identifier"):
                raise ValueError("auxiliary response is not a JSON object with model identifier")
            trace = CallTrace(stage, VERSIONS[stage], requested_model, reply["model_identifier"],
                              reply.get("input_tokens"), reply.get("output_tokens"),
                              reply.get("duration_ms", 0), "completed")
            traces.append(trace)
            if on_trace:
                on_trace(trace)
            return result
        except ModelConfigurationError:
            raise
        except UnknownUsageError as exc:
            trace = CallTrace(stage, VERSIONS[stage], requested_model, exc.actual_model,
                              None, None, exc.duration_ms, "unknown_usage",
                              f"unknown_usage:{exc.reason}")
            traces.append(trace)
            exc.traces = list(traces)
            if on_trace:
                on_trace(trace)
            raise
        except BudgetExceeded:
            raise
        except Exception as exc:
            code = f"{type(exc).__name__}"
            trace = CallTrace(stage, VERSIONS[stage], requested_model,
                              reply.get("model_identifier") if isinstance(reply, dict) else None,
                              reply.get("input_tokens") if isinstance(reply, dict) else None,
                              reply.get("output_tokens") if isinstance(reply, dict) else None,
                              reply.get("duration_ms", 0) if isinstance(reply, dict) else
                              round((time.perf_counter() - started) * 1000), "failed", code)
            traces.append(trace)
            if on_trace:
                on_trace(trace)
            if attempt == 1:
                raise P2AError(stage, code, traces) from exc


def _check_source_conflicts(profile, bundle, requested_model, model_call, traces, on_trace):
    numeric_units = [unit for unit in bundle["reading_units"]
                     if len(re.findall(r"(?<!\w)\d+(?:\.\d+)?(?!\w)", unit["text"])) >= 2]
    if not numeric_units:
        return profile
    result = _aux_json("source-consistency",
                       {"result_facts": [fact.model_dump() for fact in profile.main_results],
                        "source_excerpts": [{"id": unit["id"], "section_id": unit.get("section_id"),
                                             "text": unit["text"][:1200]} for unit in numeric_units]},
                       requested_model, model_call, traces, on_trace)
    conflicts = [SourceConflict.model_validate(item) for item in result["source_conflicts"]]
    known = source_reference_index(bundle)
    for conflict in conflicts:
        for ref in conflict.left_evidence_ref_ids + conflict.right_evidence_ref_ids:
            if ref not in known:
                raise P2AError("source-consistency", "invalid_source_reference", traces)
    payload = profile.model_dump(exclude=RESULT_METADATA)
    payload["source_conflicts"] = [item.model_dump() for item in conflicts]
    updated = PaperProfile.model_validate({**_with_references(payload, bundle),
                                           **profile.model_dump(include=RESULT_METADATA - {"evidence_refs"})})
    validate_source_references(updated, bundle)
    return updated


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
    except UnknownUsageError:
        raise
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


def _merge_claim_maps(partials, profile, bundle, run_id, quality, requested_model,
                      model_call, traces, on_trace):
    anchors = profile.author_claimed_contributions
    contributions = [{"text": fact.text, "evidence_ref_ids": fact.evidence_ref_ids,
                      "anchor": True} for fact in anchors]
    contribution_index = {}
    claims = []
    claim_index = {}
    for part_index, mapping in enumerate(partials):
        for item in mapping.contributions:
            contribution_index[(part_index, item.id)] = len(contributions)
            contributions.append({"text": item.description or item.title,
                                  "evidence_ref_ids": item.evidence_ref_ids, "anchor": False})
        for item in mapping.claims:
            claim_index[(part_index, item.id)] = len(claims)
            claims.append({"statement": item.statement,
                           "contribution_index": contribution_index[(part_index, item.contribution_id)],
                           "evidence_ref_ids": item.evidence_ref_ids})
    profile_facts = [fact.model_dump() for field in ("method_components", "experimental_setup",
                     "main_results", "author_claimed_contributions") for fact in getattr(profile, field)]
    local_gaps = [{"claim_index": claim_index[(part_index, gap.claim_id)],
                   "part_index": part_index, "status": gap.status, "description": gap.description}
                  for part_index, mapping in enumerate(partials) for gap in mapping.evidence_gaps]
    local_relations = [{"claim_index": claim_index[(part_index, relation.claim_id)],
                        "part_index": part_index, "evidence_ref_id": relation.evidence_ref_id,
                        "relation": relation.relation, "explanation": relation.explanation}
                       for part_index, mapping in enumerate(partials)
                       for relation in mapping.support_relations]
    grouping = _aux_json("contribution-normalizer",
                         {"contributions": contributions, "claims": claims,
                          "profile_facts": profile_facts, "evidence_gaps": local_gaps,
                          "support_relations": local_relations},
                         requested_model, model_call, traces, on_trace)

    def partition(groups, count, label):
        if (not isinstance(groups, list) or any(not isinstance(group, list) or not group for group in groups)
                or sorted(index for group in groups for index in group) != list(range(count))):
            raise P2AError("contribution-normalizer", f"invalid_{label}_partition", traces)
        return groups

    excluded = grouping.get("excluded_contribution_indices", [])
    if (not isinstance(excluded, list) or any(not isinstance(index, int) for index in excluded)
            or len(set(excluded)) != len(excluded)):
        raise P2AError("contribution-normalizer", "invalid_excluded_contributions", traces)
    contribution_groups = grouping.get("contribution_groups")
    if (not isinstance(contribution_groups, list) or
            any(not isinstance(group, list) or not group for group in contribution_groups) or
            any(not isinstance(index, int) for group in contribution_groups for index in group) or
            sorted([index for group in contribution_groups for index in group] + excluded)
            != list(range(len(contributions)))):
        raise P2AError("contribution-normalizer", "invalid_contribution_partition", traces)
    claim_groups = partition(grouping.get("claim_groups"), len(claims), "claim")
    payload = {"contributions": [], "claims": [], "support_relations": [], "evidence_gaps": []}
    contribution_ids = {}
    for group in contribution_groups:
        members = [contributions[index] for index in group]
        anchor = next((item for item in members if item["anchor"]), members[0])
        refs = list(dict.fromkeys(ref for item in members for ref in item["evidence_ref_ids"]))
        identity = anchor["text"].casefold().strip()
        cid = "contrib_" + hashlib.sha256(identity.encode()).hexdigest()[:16]
        if any(item["id"] == cid for item in payload["contributions"]):
            raise P2AError("contribution-normalizer", "duplicate_contribution_identity", traces)
        payload["contributions"].append({"id": cid, "title": anchor["text"],
                                         "description": anchor["text"], "evidence_ref_ids": refs})
        contribution_ids.update({index: cid for index in group})
    merged_claim_ids = {}
    for group in claim_groups:
        by_parent = {}
        for index in group:
            parent = contribution_ids.get(claims[index]["contribution_index"])
            if parent is None:
                continue
            by_parent.setdefault(parent, []).append(index)
        for parent, members in by_parent.items():
            selected = min((claims[index] for index in members),
                           key=lambda item: item["statement"].casefold())
            claim_id = "claim_" + hashlib.sha256((parent + "|" +
                        selected["statement"].casefold().strip()).encode()).hexdigest()[:16]
            refs = list(dict.fromkeys(ref for index in members for ref in claims[index]["evidence_ref_ids"]))
            existing = next((item for item in payload["claims"] if item["id"] == claim_id), None)
            if existing:
                existing["evidence_ref_ids"] = list(dict.fromkeys(existing["evidence_ref_ids"] + refs))
            else:
                payload["claims"].append({"id": claim_id, "contribution_id": parent,
                                          "statement": selected["statement"], "evidence_ref_ids": refs})
            merged_claim_ids.update({index: claim_id for index in members})
    relation_keys = set()
    for part_index, mapping in enumerate(partials):
        for relation in mapping.support_relations:
            claim_id = merged_claim_ids.get(claim_index[(part_index, relation.claim_id)])
            if claim_id is None:
                continue
            key = (claim_id, relation.evidence_ref_id, relation.relation)
            if key not in relation_keys:
                relation_keys.add(key)
                payload["support_relations"].append({**relation.model_dump(), "claim_id": claim_id})
    profile_refs = {ref for fact in profile_facts for ref in fact["evidence_ref_ids"]}
    canonical_refs = source_reference_index(bundle)
    rejected_global_support = {}
    global_support = grouping.get("global_support", [])
    if not isinstance(global_support, list):
        raise P2AError("contribution-normalizer", "invalid_global_support_structure", traces)
    for candidate in global_support:
        if (not isinstance(candidate, dict)
                or "claim_index" not in candidate
                or not isinstance(candidate.get("evidence_ref_ids"), list)
                or not candidate["evidence_ref_ids"]
                or any(not isinstance(ref, str) or not ref
                       or any(character.isspace() for character in ref)
                       for ref in candidate["evidence_ref_ids"])):
            raise P2AError("contribution-normalizer", "invalid_global_support_structure", traces)
        index = candidate["claim_index"]
        if type(index) is not int or index not in claim_index.values():
            raise P2AError("contribution-normalizer", "invalid_global_support_claim", traces)
        claim_id = merged_claim_ids.get(index)
        if claim_id is None:
            continue
        for ref in candidate["evidence_ref_ids"]:
            if ref not in canonical_refs or ref not in profile_refs:
                reason = ("not_in_source_bundle" if ref not in canonical_refs
                          else "not_in_profile_facts")
                reference_hash = hashlib.sha256(ref.encode()).hexdigest()[:16]
                rejected_global_support[(claim_id, reason, reference_hash)] = {
                    "claim_id": claim_id,
                    "status": "source_unverified",
                    "description": (
                        "Rejected optional global_support lead "
                        f"(reason={reason}; ref_sha256={reference_hash}); "
                        "the source was not added and no evidence gap was resolved."
                    ),
                }
                continue
            key = (claim_id, ref, "unverified")
            if key not in relation_keys:
                relation_keys.add(key)
                payload["support_relations"].append({"claim_id": claim_id, "evidence_ref_id": ref,
                                                     "relation": "unverified",
                                                     "explanation": "N2 global source lead; verify direct support."})
    resolved = set()
    for decision in grouping.get("resolved_gaps", []):
        gap_index, evidence_ref_id = decision.get("gap_index"), decision.get("evidence_ref_id")
        if not isinstance(gap_index, int) or not 0 <= gap_index < len(local_gaps):
            continue
        gap = local_gaps[gap_index]
        claim_id = merged_claim_ids.get(gap["claim_index"])
        if gap["status"] != "support_not_found" or claim_id is None:
            continue
        if any(relation["part_index"] != gap["part_index"] and
               merged_claim_ids.get(relation["claim_index"]) == claim_id and
               relation["evidence_ref_id"] == evidence_ref_id and
               relation["relation"] == "direct_source" for relation in local_relations):
            resolved.add(gap_index)
    deduplicated = {}
    for index, gap in enumerate(local_gaps):
        if index in resolved:
            continue
        claim_id = merged_claim_ids.get(gap["claim_index"])
        if claim_id is None:
            continue
        key = (claim_id, re.sub(r"\W+", " ", gap["description"].casefold()).strip())
        if key not in deduplicated:
            deduplicated[key] = {"claim_id": claim_id, "status": "source_unverified",
                                 "description": gap["description"]}
    payload["evidence_gaps"].extend(deduplicated.values())
    payload["evidence_gaps"].extend(rejected_global_support.values())
    for claim in payload["claims"]:
        if any(gap["claim_id"] == claim["id"] for gap in payload["evidence_gaps"]):
            continue
        related = [item for item in payload["support_relations"] if item["claim_id"] == claim["id"]]
        if any(item["relation"] != "unverified" for item in related):
            continue
        status = "source_unverified" if related else "support_not_found"
        description = ("Global evidence exists but requires source verification." if related else
                       "No support lead was found in the supplied Source Bundle or N2 facts; verify the PDF before concluding absence.")
        payload["evidence_gaps"].append({"claim_id": claim["id"], "status": status,
                                         "description": description})
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

    payload = {field: (getattr(profile, field).model_dump() if getattr(profile, field) and
                       local(getattr(profile, field)) else None)
               for field in SCALAR_FACTS}
    for field in LIST_FACTS:
        payload[field] = [fact.model_dump() for fact in getattr(profile, field)
                          if field == "author_claimed_contributions" or local(fact)]
    return json.dumps(payload, ensure_ascii=False)


def run_p2a(bundle: dict, run_id: str, model_call: Callable, requested_model: str,
            target_journal: str | None = None, special_requirements: str | None = None,
            chunk_chars: int = 12000, on_result: Callable | None = None,
            on_trace: Callable | None = None, budget: RunBudget | None = None,
            on_n3_checkpoint: Callable | None = None):
    """Run N1–N3; caller persists results and trace metadata in private storage."""
    units = bundle["reading_units"]
    if not units:
        raise P2AError("source", "no readable reading units", [])
    quality = assess_input_readiness(bundle, "section_summary")
    if quality["status"] == "blocked":
        raise P2AError("source", "required source scope is blocked", [])
    budget = budget or RunBudget()
    raw_model_call = model_call
    model_call = lambda stage, system, user: budget.call(stage, system, user, raw_model_call)
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
        return [_invoke(
            "paper-understanding", profile_context + f"\nPart {label}. "
            "Other parts will be processed separately; do not call them missing.\n" +
            _source_text(part), bundle, run_id, part, quality, requested_model,
            model_call, traces, PaperProfile, on_trace)]

    for index, part in enumerate(chunks):
        partial_profiles.extend(profile_part(part, f"{index + 1}/{len(chunks)}"))
    profile = _global_profile(partial_profiles, profile_context, bundle, run_id, quality,
                              requested_model, model_call, traces, on_trace)
    profile = _check_source_conflicts(profile, bundle, requested_model, model_call, traces, on_trace)
    if on_result:
        on_result("paper_profile", profile)
    # Local import avoids coupling the versioned continuation contract to N1/N2.
    from backend.p2a_resume import run_p2a_n3

    mapping, n3_traces = run_p2a_n3(
        bundle,
        profile,
        run_id,
        raw_model_call,
        requested_model,
        target_journal,
        special_requirements,
        chunk_chars=chunk_chars,
        budget=budget,
        on_checkpoint=on_n3_checkpoint,
        on_result=on_result,
        on_trace=on_trace,
    )
    traces.extend(n3_traces)
    return plan, profile, mapping, traces
