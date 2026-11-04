"""Pairwise semantic decisions with conservative, server-checked consolidation."""

import hashlib
import itertools
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from backend.p2a_engine import BudgetExceeded, RunBudget
from backend.p2a_schema import ClaimEvidenceMap
from backend.p2b_schema import CandidateSourceRef
from backend.p2c_schema import (CandidateLineage, CandidateRelation, ConsolidatedIssue,
                                 OriginalCandidateInventory)
from backend.review import parse_model_json


PROMPTS = Path(__file__).with_name("prompts") / "p2c"
PAIR_VERSION = "candidate-semantic-grouping/v1"
RECONCILE_VERSION = "candidate-group-reconciliation/v2"
DUPLICATE_KINDS = {"exact_duplicate", "semantic_duplicate"}


@dataclass(frozen=True)
class GroupingTrace:
    stage: str
    prompt_version: str
    requested_model: str
    actual_model: str | None
    input_tokens: int | None
    output_tokens: int | None
    duration_ms: int
    status: str
    error: str | None


@dataclass(frozen=True)
class GroupingOutcome:
    relations: list[CandidateRelation]
    issues: list[ConsolidatedIssue]
    lineage: list[CandidateLineage]
    incomplete_items: list[str]
    prompt_versions: list[str]
    status: str
    call_count: int
    input_tokens: int | None
    output_tokens: int | None
    unknown_token_calls: int
    duration_ms: int
    retry_count: int


def _unique(values):
    return list(dict.fromkeys(values))


def _source_union(refs):
    return [CandidateSourceRef(source_id=source_id, evidence_role=role)
            for source_id, role in dict.fromkeys((ref.source_id, ref.evidence_role) for ref in refs)]


def _central_question(candidates, proposed):
    original_questions = _unique(question for item in candidates
                                 for question in item.verification_questions)
    if len(candidates) == 1:
        return original_questions[0], True
    question = proposed.get("central_question")
    source = " ".join(str(value) for item in candidates for value in (
        item.title, item.observed_fact, item.suspected_problem, item.why_it_matters,
        *item.verification_questions))
    entities = re.findall(r"\b(?:\d+(?:[.,]\d+)*|[A-Z]{2,}[A-Za-z0-9]*)\b",
                          question if isinstance(question, str) else "")
    grounded = (isinstance(question, str) and len(question.strip()) >= 15 and
                question.strip().endswith(("?", "？")) and
                question not in original_questions and
                all(entity.casefold() in source.casefold() for entity in entities))
    if grounded:
        return question.strip(), True
    return " / ".join(original_questions), False


def _pair_index(inventory, relations):
    ids = sorted(item.issue_id for item in inventory.candidates)
    expected = set(itertools.combinations(ids, 2))
    actual = {(item.left_issue_id, item.right_issue_id) for item in relations}
    if len(actual) != len(relations) or actual != expected:
        raise ValueError("every original candidate pair needs one valid relation")
    return {(item.left_issue_id, item.right_issue_id): item for item in relations}


def _compatible(a, b, relation_index):
    item = relation_index[tuple(sorted((a, b)))]
    return item.relation in DUPLICATE_KINDS


def _split_into_cliques(ids, relation_index):
    groups = []
    for issue_id in sorted(ids):
        for group in groups:
            if all(_compatible(issue_id, member, relation_index) for member in group):
                group.append(issue_id)
                break
        else:
            groups.append([issue_id])
    return groups


def consolidate_from_relations(inventory: OriginalCandidateInventory,
                               mapping: ClaimEvidenceMap, relations: list[CandidateRelation],
                               proposed_groups: list[dict] | None = None):
    """Never merge beyond a pairwise duplicate clique, even if the model proposes one."""
    pair_index = _pair_index(inventory, relations)
    original = {item.issue_id: item for item in inventory.candidates}
    if proposed_groups is None:
        proposed_groups = [{"issue_ids": group, "rationale": "Pairwise duplicate relations"}
                           for group in _split_into_cliques(original, pair_index)]
    listed = [issue_id for group in proposed_groups for issue_id in group["issue_ids"]]
    if len(listed) != len(original) or set(listed) != set(original) or len(set(listed)) != len(listed):
        raise ValueError("reconciliation groups must partition all original candidates")
    claims = {item.id: item for item in mapping.claims}
    issues = []
    lineage = []
    for proposal in proposed_groups:
        if not proposal["issue_ids"] or not proposal.get("rationale"):
            raise ValueError("reconciliation group requires members and rationale")
        split = _split_into_cliques(proposal["issue_ids"], pair_index)
        for members in split:
            candidates = [original[issue_id] for issue_id in members]
            central_question, _ = _central_question(candidates, proposal if len(split) == 1 else {})
            identifier = hashlib.sha256((inventory.document_id + "|" +
                inventory.professional_review_run_id + "|" + "|".join(members)).encode()).hexdigest()[:20]
            consolidated_id = f"consolidated_{identifier}"
            claim_ids = _unique(claim_id for item in candidates for claim_id in item.claim_ids)
            impact = {item.issue_id: item.potential_impact for item in candidates}
            impact_values = set(impact.values())
            flags = {item.external_verification_required for item in candidates}
            issue = ConsolidatedIssue(
                consolidated_issue_id=consolidated_id,
                title=candidates[0].title,
                central_question=central_question,
                observed_facts=_unique(item.observed_fact for item in candidates),
                suspected_problem=" / ".join(_unique(item.suspected_problem for item in candidates)),
                scientific_significance=" / ".join(_unique(item.why_it_matters for item in candidates)),
                source_candidate_ids=members,
                source_dimension_ids=sorted({item.dimension_id for item in candidates}),
                related_claim_ids=claim_ids,
                related_contribution_ids=_unique(claims[claim_id].contribution_id for claim_id in claim_ids),
                original_source_refs=_source_union(ref for item in candidates for ref in item.source_refs),
                counterevidence_refs=_source_union(ref for item in candidates
                                                    for ref in item.possible_counterevidence),
                verification_questions=_unique(question for item in candidates
                                               for question in item.verification_questions),
                source_limitations=_unique(limit for item in candidates for limit in item.source_limitations),
                external_verification_required=True in flags,
                external_flag_disagreement=len(flags) > 1,
                original_impact_assessments=impact,
                potential_impact=next(iter(impact_values)) if len(impact_values) == 1 else "undetermined",
                impact_rationale="Original impact assessments differ; P3 must assess severity"
                if len(impact_values) > 1 else "Original candidates agree; severity remains unverified",
                consolidation_rationale=proposal["rationale"] +
                ("; split after pairwise global consistency check" if len(split) > 1 else ""),
                verification_status="unverified")
            issues.append(issue)
            lineage.extend(CandidateLineage(original_issue_id=issue_id,
                                            consolidated_issue_id=consolidated_id)
                           for issue_id in members)
    return issues, lineage


def _validated_pair_reply(raw, pairs):
    if not isinstance(raw, dict) or set(raw) != {"relations"} or not isinstance(raw["relations"], list):
        raise ValueError("pair reply requires relations")
    result = [CandidateRelation.model_validate(item) for item in raw["relations"]]
    expected = set(pairs)
    actual = {(item.left_issue_id, item.right_issue_id) for item in result}
    if len(result) != len(expected) or actual != expected:
        raise ValueError("pair reply omitted or invented a candidate pair")
    return result


def _validated_group_reply(raw, ids):
    if not isinstance(raw, dict) or set(raw) != {"groups"} or not isinstance(raw["groups"], list):
        raise ValueError("reconciliation reply requires groups")
    groups = raw["groups"]
    if any(not isinstance(group, dict) or set(group) != {"issue_ids", "rationale", "central_question"} or
           not isinstance(group["issue_ids"], list) or not group["issue_ids"] or
           not isinstance(group["rationale"], str) or not group["rationale"].strip() or
           not isinstance(group["central_question"], str) or not group["central_question"].strip()
           for group in groups):
        raise ValueError("invalid reconciliation group")
    listed = [item for group in groups for item in group["issue_ids"]]
    if len(listed) != len(ids) or set(listed) != set(ids) or len(set(listed)) != len(listed):
        raise ValueError("reconciliation omitted or duplicated candidates")
    return groups


def run_semantic_grouping(inventory: OriginalCandidateInventory, mapping: ClaimEvidenceMap,
                          model_call: Callable, requested_model: str,
                          budget: RunBudget | None = None, on_trace: Callable | None = None,
                          on_batch: Callable | None = None, max_pairs_per_call: int = 20):
    if max_pairs_per_call < 1:
        raise ValueError("pair batch size must be positive")
    budget = budget or RunBudget(max_calls=16, max_input_tokens=200000,
                                 max_output_tokens=50000, max_duration_ms=1800000)
    pair_prompt = (PROMPTS / "candidate-semantic-grouping.v1.txt").read_text(encoding="utf-8")
    reconciliation_prompt = (PROMPTS / "candidate-group-reconciliation.v2.txt").read_text(encoding="utf-8")
    ids = sorted(item.issue_id for item in inventory.candidates)
    all_pairs = list(itertools.combinations(ids, 2))
    catalog = {item.issue_id: item.model_dump() for item in inventory.candidates}
    known_claims = {item.id: item.statement for item in mapping.claims
                    if item.id in {claim for candidate in inventory.candidates for claim in candidate.claim_ids}}
    traces = []
    incomplete = []
    retries = 0
    exhausted = False
    started = time.perf_counter()

    def invoke(stage, version, system, payload, validate):
        nonlocal retries, exhausted
        user = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        for attempt in range(2):
            if attempt:
                retries += 1
            begun = time.perf_counter()
            reply = None
            try:
                reply = budget.call(stage, system, user + ("\nReturn complete valid JSON only." if attempt else ""),
                                    model_call)
                if not isinstance(reply, dict) or not reply.get("content") or not reply.get("model_identifier"):
                    raise ValueError("model response missing content or identifier")
                result = validate(parse_model_json(reply["content"]))
                status, error = "completed", None
            except BudgetExceeded:
                exhausted = True
                return None
            except Exception as exc:
                status = "invalid_output" if isinstance(exc, (ValueError, TypeError)) else "failed"
                error = f"{status}:{type(exc).__name__}"
                result = None
            trace = GroupingTrace(stage, version, requested_model,
                reply.get("model_identifier") if isinstance(reply, dict) else None,
                reply.get("input_tokens") if isinstance(reply, dict) else None,
                reply.get("output_tokens") if isinstance(reply, dict) else None,
                reply.get("duration_ms", round((time.perf_counter() - begun) * 1000))
                if isinstance(reply, dict) else round((time.perf_counter() - begun) * 1000),
                status, error)
            traces.append(trace)
            if on_trace:
                on_trace(trace)
            if result is not None:
                return result
        return None

    relations = []
    for batch_number, offset in enumerate(range(0, len(all_pairs), max_pairs_per_call), start=1):
        pairs = all_pairs[offset:offset + max_pairs_per_call]
        batch_ids = {issue_id for pair in pairs for issue_id in pair}
        payload = {"candidates": {issue_id: catalog[issue_id] for issue_id in sorted(batch_ids)},
                   "claims": known_claims, "pairs": pairs}
        result = None if exhausted else invoke(f"pair-{batch_number}", PAIR_VERSION, pair_prompt,
                                                 payload, lambda raw: _validated_pair_reply(raw, pairs))
        if result is None:
            incomplete.append(f"pair_batch_{batch_number}:unresolved")
            result = [CandidateRelation(left_issue_id=a, right_issue_id=b,
                relation="uncertain_relation", rationale="Model relation unavailable; keep separate")
                for a, b in pairs]
        relations.extend(result)
        if on_batch:
            on_batch(batch_number, result)

    if ids:
        suggested = [{"issue_ids": group, "rationale": "Pairwise duplicate relations"}
                     for group in _split_into_cliques(ids, _pair_index(inventory, relations))]
        reconciliation_input = {"candidates": catalog,
                                "relations": [item.model_dump() for item in relations],
                                "suggested_groups": suggested}
        proposed = None if exhausted else invoke("reconciliation", RECONCILE_VERSION,
            reconciliation_prompt, reconciliation_input,
            lambda raw: _validated_group_reply(raw, ids))
        if proposed is None:
            incomplete.append("reconciliation:unresolved")
            proposed = suggested
        issues, lineage = consolidate_from_relations(inventory, mapping, relations, proposed)
        originals = {item.issue_id: item for item in inventory.candidates}
        if any(len(group["issue_ids"]) > 1 and not _central_question(
                [originals[issue_id] for issue_id in group["issue_ids"]], group)[1]
                for group in proposed):
            incomplete.append("central_question:unresolved")
        if len(issues) > len(proposed):
            incomplete.append("reconciliation:global_consistency_split")
    else:
        issues, lineage = [], []
    if exhausted:
        status = "budget_exceeded"
    elif incomplete:
        status = "limited"
    else:
        status = "completed"
    known_inputs = [item.input_tokens for item in traces if item.input_tokens is not None]
    known_outputs = [item.output_tokens for item in traces if item.output_tokens is not None]
    return GroupingOutcome(relations, issues, lineage, incomplete,
        [PAIR_VERSION, RECONCILE_VERSION], status, budget.calls,
        sum(known_inputs) if known_inputs else None,
        sum(known_outputs) if known_outputs else None, budget.unknown_usage_calls,
        round((time.perf_counter() - started) * 1000), retries)
