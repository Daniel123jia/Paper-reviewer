"""Versioned bilingual report drafting from a frozen evidence pack."""

import json
import re
from pathlib import Path

from pydantic import Field

from backend.p2a_schema import StrictModel
from backend.p4_report_schema import ReportEvidencePack, ReportPlan, ReportWritingResult
from backend.p4_validator import numeric_claims, validate_bilingual_pair, validate_writing
from backend.review import parse_model_json


PROMPTS = Path(__file__).with_name("prompts") / "p4"
PLANNING_VERSION = "review-report-planning/v1"
CONSISTENCY_VERSION = "review-report-consistency/v1"
WRITING_VERSIONS = {"zh": "review-comment-writing-zh/v1",
                    "en": "review-comment-writing-en/v1"}


class ConsistencyAssessment(StrictModel):
    acceptable: bool
    unsupported_facts: list[str] = Field(default_factory=list)
    state_promotions: list[str] = Field(default_factory=list)
    refuted_accusations: list[str] = Field(default_factory=list)
    language_or_scope_changes: list[str] = Field(default_factory=list)


def _prompt(filename):
    return (PROMPTS / filename).read_text(encoding="utf-8")


def _model_chunk(plan, pack, groups):
    """Send bounded issue detail while leaving the frozen audit pack unchanged."""
    ids = {issue_id for group in groups for issue_id in group.source_issue_ids}
    chunk_plan = plan.model_copy(deep=True)
    chunk_plan.groups = groups
    chunk_plan.selected_issue_ids = [item for item in plan.selected_issue_ids if item in ids]
    chunk_pack = pack.model_copy(deep=True)
    chunk_pack.groups = groups
    chunk_pack.issues = [item for item in chunk_pack.issues if item.source_issue_id in ids]
    chunk_pack.reviewer_added = [item for item in chunk_pack.reviewer_added if item.issue_id in ids]
    if plan.report_kind != "author":
        for item in chunk_pack.issues:
            item.actual_tool_results = item.actual_tool_results[:5]
            item.evidence_limitations = item.evidence_limitations[:6]
            item.outstanding_checks = item.outstanding_checks[:6]
        chunk_pack.source_limitations = chunk_pack.source_limitations[:10]
    return chunk_plan, chunk_pack


def write_report(plan: ReportPlan, pack: ReportEvidencePack, model_call,
                 bilingual_reference: ReportWritingResult | None = None):
    version = WRITING_VERSIONS[plan.language]
    if not plan.groups:
        return ReportWritingResult(prompt_version=version, language=plan.language,
            report_kind=plan.report_kind, general_assessment="", comments=[])
    system = _prompt("review-report-planning.v1.txt") + "\n" + \
             _prompt(f"review-comment-writing-{plan.language}.v1.txt")
    reference = {item.report_comment_id: item for item in bilingual_reference.comments} \
        if bilingual_reference else {}
    group_batches = ([[group] for group in plan.groups] if plan.report_kind != "author"
                     else [plan.groups])
    comments = []
    general = ""
    for number, groups in enumerate(group_batches, 1):
        chunk_plan, chunk_pack = _model_chunk(plan, pack, groups)
        alignment = {group.group_id: {
            "evidence_refs": reference[group.group_id].evidence_refs,
            "numeric_claims": sorted(numeric_claims(reference[group.group_id].comment_body)),
        } for group in groups if group.group_id in reference}
        full_issues = {item.source_issue_id: item for item in pack.issues}
        model_limits = [{"source_issue_id": item.source_issue_id,
            "tool_results_omitted": len(full_issues[item.source_issue_id].actual_tool_results) -
                                    len(item.actual_tool_results),
            "source_limitations_omitted": len(full_issues[item.source_issue_id].evidence_limitations) -
                                          len(item.evidence_limitations),
            "outstanding_checks_omitted": len(full_issues[item.source_issue_id].outstanding_checks) -
                                          len(item.outstanding_checks)}
            for item in chunk_pack.issues]
        user = json.dumps({"plan": chunk_plan.model_dump(),
                           "evidence_pack": chunk_pack.model_dump(),
                           "model_input_limitations": model_limits,
                           "bilingual_alignment": alignment,
                           "general_assessment_numeric_claims": sorted(
                               numeric_claims(bilingual_reference.general_assessment))
                               if bilingual_reference else []}, ensure_ascii=False)
        for attempt in range(2):
            repair = ("\nThe preceding attempt failed strict schema, evidence, bilingual, or "
                      "consistency checks. Return corrected JSON using only the frozen "
                      "evidence and exact alignment values. Remove unsupported claims."
                      if attempt else "")
            try:
                raw = model_call(f"writing-{number}-attempt-{attempt + 1}", version,
                                 system + repair, user)
                partial = ReportWritingResult.model_validate(parse_model_json(raw["content"]))
                if partial.prompt_version != version:
                    raise ValueError("report model returned another Prompt version")
                validate_writing(partial, chunk_plan, chunk_pack)
                if bilingual_reference:
                    subset = bilingual_reference.model_copy(deep=True)
                    subset.comments = [reference[group.group_id] for group in groups
                                       if group.group_id in reference]
                    validate_bilingual_pair(partial, subset)
                consistency_user = json.dumps({"plan": chunk_plan.model_dump(),
                    "evidence_pack": chunk_pack.model_dump(), "report": partial.model_dump()},
                    ensure_ascii=False)
                checked = model_call(f"consistency-{number}-attempt-{attempt + 1}",
                    CONSISTENCY_VERSION, _prompt("review-report-consistency.v1.txt"),
                    consistency_user)
                assessment = ConsistencyAssessment.model_validate(
                    parse_model_json(checked["content"]))
                if not assessment.acceptable or any((assessment.unsupported_facts,
                                                     assessment.state_promotions,
                                                     assessment.refuted_accusations,
                                                     assessment.language_or_scope_changes)):
                    raise ValueError("model report consistency review flagged unsupported wording")
                break
            except ValueError:
                if attempt:
                    raise
        if not general:
            general = partial.general_assessment
        comments.extend(partial.comments)
    writing = ReportWritingResult(prompt_version=version, language=plan.language,
        report_kind=plan.report_kind, general_assessment=general, comments=comments)
    validate_writing(writing, plan, pack)
    return writing


def render_markdown(plan: ReportPlan, pack: ReportEvidencePack,
                    writing: ReportWritingResult):
    zh = plan.language == "zh"
    internal = plan.report_kind != "author"
    if plan.report_kind == "author":
        title = "面向作者的审稿意见" if zh else "Review Comments for Authors"
    elif plan.report_kind == "editor":
        title = "编辑保密说明" if zh else "Confidential Comments to the Editor"
    else:
        title = "内部完整审稿报告" if zh else "Internal Peer Review Report"
    lines = [f"# {title}", ""]
    if internal:
        total = len(plan.selected_issue_ids)
        grouped = len(plan.groups)
        eligible = sum(issue.report_eligibility == "eligible_with_qualification"
                       for issue in pack.issues)
        lines += ["## 报告范围" if zh else "## Review Scope", "",
            (f"本报告按 {grouped} 个审查主题保留 {total} 项 P3 核验问题，其中 {eligible} 项具备限定性自动草稿资格。"
             "以下意见保留原始判定与待核验事项，仅供审稿人内部审阅，不代表已确认的论文错误。"
             if zh else f"This internal report retains {total} P3 verification issues in {grouped} "
             f"review topics. {eligible} have qualified automatic draft eligibility. "
             "All original verdicts and pending checks remain visible; these are not confirmed paper errors."), ""]
    elif writing.general_assessment:
        lines += ["## 总体评价" if zh else "## General Assessment", "",
                  writing.general_assessment, ""]
    if not plan.groups and not internal:
        lines += [("目前没有自动准入的正式审稿问题。待人工复核的问题保留在内部报告中；"
                   "审稿人可以在核实来源后补充意见。" if zh else
                   "There are currently no automatically admitted comments for authors. "
                   "Items requiring human review remain in the internal report."), ""]
    by_id = {item.report_comment_id: item for item in writing.comments}
    pack_issues = {item.source_issue_id: item for item in pack.issues}
    labels = {"major": "主要意见" if zh else "Major Comments",
              "minor": "次要意见" if zh else "Minor Comments",
              "clarification": "需要澄清的问题" if zh else "Points Requiring Clarification"}
    for category in ("major", "clarification", "minor"):
        selected = [group for group in plan.groups if group.category == category]
        if not selected:
            continue
        lines += [f"## {labels[category]}", ""]
        for index, group in enumerate(selected, 1):
            comment = by_id[group.group_id]
            lines += [f"### {index}. {comment.title_zh if zh else comment.title_en}", "",
                      comment.comment_body, ""]
            locations = list(dict.fromkeys(location for issue_id in group.source_issue_ids
                for location in (pack_issues[issue_id].source_locations
                                 if issue_id in pack_issues else [])))
            if locations:
                lines += [("原文位置：" if zh else "Paper locations: ") +
                          "; ".join(locations) + ".", ""]
            if internal:
                lines += [("建议作者回应：" if zh else "Suggested author response: ") +
                          comment.author_action_requested, ""]
                for issue_id in group.source_issue_ids:
                    issue = pack_issues.get(issue_id)
                    if issue:
                        lines += [f"- P3: {issue.verdict}; report_eligibility: "
                                  f"{issue.report_eligibility}; human_review_status: "
                                  f"{issue.human_review_status}; reviewer_approval_status: "
                                  f"{issue.reviewer_approval_status}"]
                        if issue.outstanding_checks:
                            lines += ["- " + ("未完成检查：" if zh else "Pending checks: ") +
                                      "; ".join(issue.outstanding_checks[:3])]
                lines += [""]
    if internal:
        lines += ["## 核验限制与来源" if zh else "## Verification Limits and Sources", ""]
        for issue in pack.issues:
            lines += [f"- {issue.source_issue_id}: {issue.verdict} / "
                      f"{issue.report_eligibility}; {issue.scientific_question}"]
            if issue.outstanding_checks:
                lines += ["  - " + ("待检查：" if zh else "Pending: ") +
                          "; ".join(issue.outstanding_checks[:3])]
        opaque_gaps = [limit for limit in pack.source_limitations
                       if limit.startswith("coverage_gap_")]
        if opaque_gaps:
            lines += [(f"- {len(opaque_gaps)} 项解析覆盖疑点保留在冻结来源中，需按相关原文逐项复核。"
                       if zh else f"- {len(opaque_gaps)} parse coverage gaps remain in the "
                       "frozen source and require source-specific review.")]
        table_flags = [item for item in pack.source_limitations
                       if re.search(r"\btable_(?:ev_|p\d)", item)]
        figure_flags = [item for item in pack.source_limitations
                        if re.search(r"\bfigure_(?:ev_|p\d)", item)]
        if table_flags:
            lines += [(f"- {len(table_flags)} 项表格结构限制保留在冻结来源中。" if zh else
                       f"- {len(table_flags)} table-structure limitation records remain in the frozen source.")]
        if figure_flags:
            lines += [(f"- {len(figure_flags)} 项图像或图注限制保留在冻结来源中。" if zh else
                       f"- {len(figure_flags)} figure/caption limitation records remain in the frozen source.")]
        for limit in [item for item in pack.source_limitations
                      if item not in opaque_gaps + table_flags + figure_flags][:8]:
            lines += [f"- {limit}"]
        lines += [""]
    return "\n".join(lines).strip() + "\n"
