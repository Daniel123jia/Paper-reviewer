"""Deterministic P3 orchestration around four separately scoped model roles."""

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from backend.p2a_engine import BudgetExceeded, RunBudget
from backend.p2a_schema import source_reference_index
from backend.p3_adjudication import (JUDGMENT_PROMPT_VERSION, PROMPTS,
    SOURCE_PROMPT_VERSION, adjudicate, fallback_adjudication,
    validate_adjudication_proposal, validate_source_fact_reply)
from backend.p3_retrieval import (PROMPT as CHALLENGE_PROMPT, PROMPT_VERSION as CHALLENGE_VERSION,
                                   CounterEvidenceRetriever, validate_challenge)
from backend.p3_schema import EvidenceVerificationResult
from backend.p3_sources import EvidenceVerifier, validate_p3_inputs
from backend.p3_technical import (PROMPT as TECHNICAL_PROMPT,
    PROMPT_VERSION as TECHNICAL_VERSION, TechnicalEvidenceProposal,
    run_technical_checks, validate_technical_proposal)
from backend.review import parse_model_json


PROMPT_VERSIONS = [SOURCE_PROMPT_VERSION, CHALLENGE_VERSION,
                   TECHNICAL_VERSION, JUDGMENT_PROMPT_VERSION]


@dataclass(frozen=True)
class P3CallTrace:
    stage: str
    prompt_version: str
    requested_model: str
    actual_model: str | None
    input_tokens: int | None
    output_tokens: int | None
    duration_ms: int
    status: str
    error: str | None


def validate_p3_result(result, consolidation, bundle):
    """Require exact task ownership and reject foreign PDF/source/tool references."""
    if (result.document_id != consolidation.document_id or
            result.snapshot_id != consolidation.snapshot_id or
            result.pdf_sha256 != consolidation.pdf_sha256 or
            result.source_bundle_id != consolidation.source_bundle_id or
            result.understanding_run_id != consolidation.understanding_run_id or
            result.professional_review_run_id != consolidation.professional_review_run_id or
            result.consolidation_run_id != consolidation.consolidation_run_id):
        raise ValueError("P3 result source identity differs from P2-C")
    canonical = source_reference_index(bundle)
    tasks = {item.verification_task_id: item for item in consolidation.verification_tasks}
    issues = {item.consolidated_issue_id: item for item in consolidation.consolidated_issues}
    if {item.verification_task_id for item in result.adjudications} != set(tasks):
        raise ValueError("P3 must account for every P2-C VerificationTask")
    if not set(result.uncovered_tasks).issubset(tasks):
        raise ValueError("P3 uncovered task does not exist")
    source_ids = {item.source_id for item in result.sources}
    if not source_ids.issubset(canonical):
        raise ValueError("P3 generated a source outside the frozen bundle")
    for source in result.sources:
        ref = canonical[source.source_id]
        if (source.document_id != ref.document_id or source.snapshot_id != ref.snapshot_id or
                source.page_index != ref.page_index or source.evidence_ids != ref.evidence_ids):
            raise ValueError("P3 source does not match the frozen PDF snapshot")
        if source.source_verification_status == "human_confirmed" and \
                source.source_method != "human_pdf_review":
            raise ValueError("automated PDF extraction cannot claim human confirmation")
    observation_ids = {item.observation_id for item in result.observations}
    tool_ids = {item.tool_result_id for item in result.tool_results}
    for item in result.observations:
        if item.verification_task_id not in tasks or \
                not set(item.related_source_ref_ids).issubset(source_ids) or \
                item.document_id != result.document_id or item.snapshot_id != result.snapshot_id:
            raise ValueError("P3 observation is not tied to its frozen source")
    for item in result.tool_results:
        if item.verification_task_id not in tasks or \
                not set(item.input_refs).issubset(source_ids):
            raise ValueError("P3 tool result refers to a foreign source or task")
    for adjudication in result.adjudications:
        task = tasks[adjudication.verification_task_id]
        issue = issues[task.consolidated_issue_id]
        if (adjudication.consolidated_issue_id != issue.consolidated_issue_id or
                adjudication.original_candidate_ids != issue.source_candidate_ids or
                adjudication.original_central_question != issue.central_question or
                adjudication.original_source_refs != [ref.source_id for ref in
                    task.source_refs_to_check] or
                adjudication.original_counterevidence_refs != [ref.source_id for ref in
                    task.counterevidence_refs_to_check] or
                adjudication.counterevidence_search.verification_task_id != task.verification_task_id):
            raise ValueError("P3 adjudication changed frozen P2-C lineage")
        if {item.original_observed_fact for item in adjudication.fact_assessments} - \
                set(issue.observed_facts):
            raise ValueError("P3 fact assessment invented an original fact")
        if not set(adjudication.verified_observation_ids).issubset(observation_ids) or \
                not set(adjudication.supporting_evidence + adjudication.counterevidence +
                        adjudication.original_source_refs + adjudication.original_counterevidence_refs).issubset(canonical) or \
                not set(adjudication.executed_tool_result_ids).issubset(tool_ids):
            raise ValueError("P3 adjudication refers to an unknown observation, source or tool")
        for subcheck in adjudication.subchecks:
            if subcheck.parent_verification_task_id != task.verification_task_id or \
                    not set(subcheck.input_refs + subcheck.supporting_refs +
                            subcheck.contradicting_refs).issubset(source_ids) or \
                    not set(subcheck.tool_result_ids).issubset(tool_ids):
                raise ValueError("P3 subcheck lost parent or cited unexecuted evidence")
    EvidenceVerificationResult.model_validate(result.model_dump())


def run_p3(bundle, plan, profile, mapping, review, consolidation, pdf_path,
           evidence_verification_run_id, model_call, requested_model,
           budget: RunBudget | None = None, region_dir=None, on_trace=None,
           on_task=None, on_tool=None):
    identity = validate_p3_inputs(bundle, plan, profile, mapping, review,
                                  consolidation, pdf_path)
    pdf_available = identity == "sha256_matched"
    budget = budget or RunBudget(max_calls=48, max_input_tokens=450000,
        max_output_tokens=100000, max_duration_ms=2700000)
    traces = []
    retries = 0
    exhausted = False
    started = time.perf_counter()
    source_prompt = (PROMPTS / "source-observation-check.v1.txt").read_text(encoding="utf-8")
    judgment_prompt = (PROMPTS / "issue-adjudication.v2.txt").read_text(encoding="utf-8")
    challenge_prompt = CHALLENGE_PROMPT.read_text(encoding="utf-8")
    technical_prompt = TECHNICAL_PROMPT.read_text(encoding="utf-8")

    def invoke(stage, version, system, payload, validate):
        nonlocal retries, exhausted
        user = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        for attempt in range(2):
            if attempt:
                retries += 1
            begun = time.perf_counter()
            reply = None
            try:
                reply = budget.call(stage, system,
                    user + ("\nReturn complete valid JSON only." if attempt else ""), model_call)
                if not isinstance(reply, dict) or not reply.get("content") or \
                        not reply.get("model_identifier"):
                    raise ValueError("model reply missing content or model identifier")
                value = validate(parse_model_json(reply["content"]))
                status, error = "completed", None
            except BudgetExceeded:
                exhausted = True
                return None
            except Exception as exc:
                value = None
                status = "invalid_output" if isinstance(exc, (ValueError, TypeError)) else "failed"
                error = f"{status}:{type(exc).__name__}"
            trace = P3CallTrace(stage, version, requested_model,
                reply.get("model_identifier") if isinstance(reply, dict) else None,
                reply.get("input_tokens") if isinstance(reply, dict) else None,
                reply.get("output_tokens") if isinstance(reply, dict) else None,
                reply.get("duration_ms", round((time.perf_counter() - begun) * 1000))
                if isinstance(reply, dict) else round((time.perf_counter() - begun) * 1000),
                status, error)
            traces.append(trace)
            if on_trace:
                on_trace(trace)
            if value is not None:
                return value
        return None

    issue_index = {item.consolidated_issue_id: item for item in consolidation.consolidated_issues}
    retriever = CounterEvidenceRetriever(bundle, mapping, profile)
    all_observations = []
    all_tools = []
    adjudications = []
    uncovered = []
    with EvidenceVerifier(bundle, pdf_path if pdf_available else None, region_dir) as verifier:
        for task in consolidation.verification_tasks:
            issue = issue_index[task.consolidated_issue_id]
            pack = retriever.retrieve(task, issue)
            observations = []
            def observe(source_ids):
                existing = {ref_id for item in observations for ref_id in item.related_source_ref_ids}
                for source_id in dict.fromkeys(source_ids):
                    if source_id not in existing:
                        _, observation = verifier.observe(source_id, task.verification_task_id)
                        observations.append(observation)
                        all_observations.append(observation)
                        existing.add(source_id)

            observe(ref.source_id for ref in task.source_refs_to_check +
                    task.counterevidence_refs_to_check)
            if not pdf_available or exhausted:
                reason = "Original PDF unavailable" if not pdf_available else "Model call budget exceeded"
                adjudication = fallback_adjudication(task, issue, pack.search,
                    observations, reason, pdf_available)
                uncovered.append(task.verification_task_id)
            else:
                source_ids = {ref.source_id for ref in task.source_refs_to_check +
                              task.counterevidence_refs_to_check}
                facts = invoke(f"{task.verification_task_id}:source_observation",
                    SOURCE_PROMPT_VERSION, source_prompt,
                    {"original_facts": issue.observed_facts,
                     "source_observations": [item.model_dump() for item in observations]},
                    lambda raw: validate_source_fact_reply(raw, issue, source_ids))
                challenge = None if exhausted else invoke(
                    f"{task.verification_task_id}:counterevidence",
                    CHALLENGE_VERSION, challenge_prompt,
                    {"issue": issue.model_dump(), "task": task.model_dump(),
                     "context_pack": pack.model_dump()},
                    lambda raw: validate_challenge(raw, pack))
                if challenge:
                    observe(challenge.supporting_source_ids +
                            challenge.contradicting_source_ids +
                            challenge.author_answer_source_ids)
                need_technical = (any(ref.source_id in {item["id"] for item in
                    bundle["tables"] + bundle["formulas"]} for ref in task.source_refs_to_check +
                    task.counterevidence_refs_to_check) or
                    bool(re.search(r"\b(?:table|formula|equation|metric|score|BLEU|F1|EM|speed|time|cost|complexity|numeric|percentage)\b|(?<![A-Za-z0-9])\d+(?:\.\d+)?(?![A-Za-z0-9])",
                                   issue.title + " " + issue.central_question, re.I)))
                technical = None
                if need_technical and not exhausted:
                    technical = invoke(f"{task.verification_task_id}:technical",
                        TECHNICAL_VERSION, technical_prompt,
                        {"issue": issue.model_dump(), "task": task.model_dump(),
                         "context_pack": pack.model_dump(),
                         "source_observations": [item.model_dump() for item in observations]},
                        lambda raw: validate_technical_proposal(raw, pack))
                if technical:
                    observe([item.source_id for item in technical.metric_records +
                             technical.scalar_formula_checks])
                checks = run_technical_checks(task,
                    technical or TechnicalEvidenceProposal(metric_records=[],
                        scalar_formula_checks=[], unavailable_checks=[]),
                    list(verifier.sources.values()), observations)
                all_tools.extend(checks)
                if on_tool:
                    for check in checks:
                        on_tool(check)
                if facts is None or challenge is None or (need_technical and technical is None):
                    reason = ("Source observation, counterevidence challenge or required technical "
                              "proposal was unavailable; no scientific verdict assigned")
                    adjudication = fallback_adjudication(task, issue, pack.search,
                        observations, reason, pdf_available)
                    uncovered.append(task.verification_task_id)
                else:
                    proposal = None if exhausted else invoke(
                        f"{task.verification_task_id}:adjudication",
                        JUDGMENT_PROMPT_VERSION, judgment_prompt,
                        {"issue": issue.model_dump(), "task": task.model_dump(),
                         "fact_assessments": [item.model_dump() for item in facts],
                         "challenge": challenge.model_dump(),
                         "search_audit": pack.search.model_dump(),
                         "source_observations": [item.model_dump() for item in observations],
                         "tool_results": [item.model_dump() for item in checks]},
                        lambda raw: validate_adjudication_proposal(raw,
                            {ref_id for item in observations for ref_id in
                             item.related_source_ref_ids},
                            {item.tool_result_id for item in checks}))
                    if proposal is None:
                        adjudication = fallback_adjudication(task, issue, pack.search,
                            observations, "Adjudication model result unavailable", pdf_available)
                        uncovered.append(task.verification_task_id)
                    else:
                        adjudication = adjudicate(task, issue, proposal, facts, challenge,
                            pack.search, observations, list(verifier.sources.values()),
                            checks, pdf_available)
            adjudications.append(adjudication)
            if on_task:
                on_task(adjudication)
        sources = list(verifier.sources.values())
    known_in = [item.input_tokens for item in traces if item.input_tokens is not None]
    known_out = [item.output_tokens for item in traces if item.output_tokens is not None]
    status = ("budget_exceeded" if exhausted else "limited" if
        consolidation.execution_status == "limited" or identity != "sha256_matched" or
        uncovered or any(item.execution_status != "completed" for item in adjudications) else
        "completed")
    result = EvidenceVerificationResult(document_id=consolidation.document_id,
        snapshot_id=consolidation.snapshot_id, pdf_sha256=consolidation.pdf_sha256,
        source_bundle_id=consolidation.source_bundle_id,
        understanding_run_id=consolidation.understanding_run_id,
        professional_review_run_id=consolidation.professional_review_run_id,
        consolidation_run_id=consolidation.consolidation_run_id,
        evidence_verification_run_id=evidence_verification_run_id,
        prompt_versions=PROMPT_VERSIONS, model_identifier=requested_model,
        pdf_identity_status=identity, sources=sources, observations=all_observations,
        tool_results=all_tools, adjudications=adjudications,
        uncovered_tasks=uncovered,
        unresolved_coverage_gaps=[item.gap_id for item in consolidation.coverage_audit.gaps],
        call_count=budget.calls, input_tokens=sum(known_in) if known_in else None,
        output_tokens=sum(known_out) if known_out else None,
        unknown_token_calls=budget.unknown_usage_calls,
        duration_ms=round((time.perf_counter() - started) * 1000),
        retry_count=retries, execution_status=status)
    validate_p3_result(result, consolidation, bundle)
    return result
