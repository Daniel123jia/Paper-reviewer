"""Versioned bilingual report drafting from a frozen evidence pack."""

import json
from pathlib import Path

from pydantic import Field

from backend.p2a_schema import StrictModel
from backend.p4_report_schema import ReportEvidencePack, ReportPlan, ReportWritingResult
from backend.p4_validator import validate_writing
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


def write_report(plan: ReportPlan, pack: ReportEvidencePack, model_call):
    version = WRITING_VERSIONS[plan.language]
    if not plan.groups:
        return ReportWritingResult(prompt_version=version, language=plan.language,
            report_kind=plan.report_kind, general_assessment="", comments=[])
    system = _prompt("review-report-planning.v1.txt") + "\n" + \
             _prompt(f"review-comment-writing-{plan.language}.v1.txt")
    user = json.dumps({"plan": plan.model_dump(), "evidence_pack": pack.model_dump()},
                      ensure_ascii=False)
    raw = model_call("writing", version, system, user)
    writing = ReportWritingResult.model_validate(parse_model_json(raw["content"]))
    if writing.prompt_version != version:
        raise ValueError("report model returned another Prompt version")
    validate_writing(writing, plan, pack)
    consistency_user = json.dumps({"plan": plan.model_dump(), "evidence_pack": pack.model_dump(),
                                   "report": writing.model_dump()}, ensure_ascii=False)
    checked = model_call("consistency", CONSISTENCY_VERSION,
                         _prompt("review-report-consistency.v1.txt"), consistency_user)
    assessment = ConsistencyAssessment.model_validate(parse_model_json(checked["content"]))
    if not assessment.acceptable or any((assessment.unsupported_facts,
                                         assessment.state_promotions,
                                         assessment.refuted_accusations,
                                         assessment.language_or_scope_changes)):
        raise ValueError("model report consistency review flagged unsupported wording")
    return writing


def render_markdown(plan: ReportPlan, pack: ReportEvidencePack,
                    writing: ReportWritingResult):
    zh = plan.language == "zh"
    internal = plan.report_kind != "author"
    title = ("内部完整审稿报告" if internal else "面向作者的审稿意见草稿") if zh else \
            ("Internal Peer Review Report" if internal else "Review Comments for Authors — Draft")
    lines = [f"# {title}", ""]
    if writing.general_assessment:
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
            lines += [("建议作者回应：" if zh else "Suggested author response: ") +
                      comment.author_action_requested, ""]
            if internal:
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
        for limit in pack.source_limitations[:15]:
            lines += [f"- {limit}"]
        lines += [""]
    return "\n".join(lines).strip() + "\n"
