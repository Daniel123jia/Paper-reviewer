"""Deterministic, auditable selection of original text for each P2-B dimension."""

import json
import re
from dataclasses import dataclass

from backend.p2a_schema import source_reference_index


TERMS = {
    "D1": ("abstract", "introduction", "contribution", "novel", "method", "related work"),
    "D2": ("method", "model", "algorithm", "theory", "formulation", "complexity"),
    "D3": ("experiment", "result", "evaluation", "ablation", "dataset", "table"),
    "D4": ("abstract", "introduction", "result", "discussion", "conclusion"),
    "D5": ("method", "implementation", "training", "reproduc", "appendix"),
    "D6": ("introduction", "related work", "background", "reference", "literature"),
}


@dataclass(frozen=True)
class ContextPack:
    dimension_id: str
    text: str
    source_ref_ids: list[str]
    claim_ids: list[str]
    selected_unit_ids: list[str]
    omitted_unit_ids: list[str]
    coverage_status: str
    limitations: list[str]


def _fact_refs(profile, dimension_id):
    fields = {
        "D1": ("research_problem", "research_gap_claimed_by_authors", "proposed_method",
               "author_claimed_contributions"),
        "D2": ("proposed_method", "method_components", "theoretical_assumptions"),
        "D3": ("experimental_setup", "datasets_and_metrics", "main_results"),
        "D4": ("research_problem", "main_results", "conclusions", "author_stated_limitations"),
        "D5": ("proposed_method", "method_components", "experimental_setup"),
        "D6": ("research_motivation", "research_gap_claimed_by_authors",
               "author_claimed_contributions"),
    }[dimension_id]
    for field in fields:
        facts = getattr(profile, field)
        for fact in facts if isinstance(facts, list) else [facts] if facts else []:
            yield from fact.evidence_ref_ids
    if dimension_id in {"D3", "D4"}:
        for conflict in profile.source_conflicts:
            yield from conflict.left_evidence_ref_ids
            yield from conflict.right_evidence_ref_ids


def _unit_line(unit):
    return (f'[{unit["id"]}] page={unit["page_index"] + 1} section={unit.get("section_id")} '
            f'evidence={",".join(unit["evidence_ids"])} '
            f'order={unit.get("reading_order_status", "unverified")}\n{unit["text"]}\n')


def assemble_context(dimension_id, bundle, plan, profile, mapping, max_chars=28000):
    """Rank anchored units, extend neighbors, and expose every omission explicitly."""
    if dimension_id not in TERMS:
        raise ValueError("unknown review dimension")
    canonical = source_reference_index(bundle)
    units = bundle["reading_units"]
    sections = {item["id"]: item["title"] for item in bundle["sections"]}
    anchors = set(_fact_refs(profile, dimension_id))
    anchors.update(ref for claim in mapping.claims for ref in claim.evidence_ref_ids)
    anchors.update(relation.evidence_ref_id for relation in mapping.support_relations)
    evidence_ids = {evidence_id for ref in anchors if ref in canonical
                    for evidence_id in canonical[ref].evidence_ids}
    terms = TERMS[dimension_id]
    scored = []
    for index, unit in enumerate(units):
        section = sections.get(unit.get("section_id"), "").casefold()
        text = unit["text"].casefold()
        score = (100 if unit["id"] in anchors or evidence_ids.intersection(unit["evidence_ids"]) else 0)
        score += 30 if any(term in section for term in terms) else 0
        score += min(3, sum(bool(re.search(r"\b" + re.escape(term) + r"\b", text)) for term in terms)) * 5
        if score:
            scored.append((score, index))
    if not scored:
        scored = [(1, index) for index in range(len(units))]
    candidates = {neighbor for _, index in scored
                  for neighbor in (index - 1, index, index + 1) if 0 <= neighbor < len(units)}
    priority = sorted(candidates, key=lambda index: (
        -next((score for score, original in scored if original == index), 0), index))
    selected = set()
    used = 0
    for index in priority:
        length = len(_unit_line(units[index]))
        if selected and used + length > max_chars:
            continue
        selected.add(index)
        used += length
    ordered = [units[index] for index in sorted(selected)]
    omitted = [unit["id"] for index, unit in enumerate(units) if index not in selected]
    selected_evidence = {source for unit in ordered for source in unit["evidence_ids"]}
    object_lines = []
    available_refs = [unit["id"] for unit in ordered]
    available_refs.extend(evidence_id for unit in ordered for evidence_id in unit["evidence_ids"]
                          if evidence_id in canonical)
    available_refs.extend(unit["section_id"] for unit in ordered
                          if unit.get("section_id") in canonical)
    limitations = list(dict.fromkeys(bundle.get("limitations", []) +
                                     profile.unresolved_source_gaps +
                                     profile.input_coverage.limitations +
                                     mapping.input_coverage.limitations))
    for kind, objects in (("table", bundle["tables"]), ("formula", bundle["formulas"]),
                          ("figure", bundle["figures"])):
        for obj in objects:
            ref = canonical.get(obj["id"])
            if not ref or not selected_evidence.intersection(ref.evidence_ids):
                continue
            content = obj.get("structured_rows") or obj.get("cells") or obj.get("text") or obj.get("caption")
            rendered = json.dumps(content, ensure_ascii=False)
            if len(rendered) > 4000:
                rendered = rendered[:4000] + " [TRUNCATED]"
                limitations.append(f"object_truncated:{obj['id']}")
            object_lines.append(f'[{obj["id"]}] type={kind} page={obj["page_index"] + 1} '
                                f'status={obj.get("usability_status", obj.get("status", "unverified"))} '
                                f'evidence={",".join(ref.evidence_ids)} content={rendered}')
            available_refs.append(obj["id"])
    if omitted:
        limitations.append(f"omitted_reading_units:{len(omitted)}")
    if not units or not ordered or any(bundle["pages"][unit["page_index"]]["status"] == "needs_ocr"
                                     for unit in ordered):
        coverage = "blocked"
    elif omitted or limitations or profile.execution_status == "limited" or mapping.execution_status == "limited":
        coverage = "partial"
    else:
        coverage = "sufficient"
    text = ("Source Bundle: " + bundle["bundle_id"] + "\n"
            + "Selected original reading units (ordered):\n" + "\n".join(_unit_line(unit) for unit in ordered)
            + "\nRelated technical objects (status is not verification):\n" + "\n".join(object_lines)
            + "\nOmitted reading unit IDs: " + json.dumps(omitted)
            + "\nSource limitations: " + json.dumps(limitations, ensure_ascii=False))
    return ContextPack(dimension_id, text, list(dict.fromkeys(available_refs)),
                       [claim.id for claim in mapping.claims],
                       [unit["id"] for unit in ordered], omitted, coverage, limitations)
