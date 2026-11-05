import json
import sqlite3

import pytest

from backend.p4_planner import build_report_plan
from backend.p4_report_schema import ReportComment, ReportWritingResult
from backend.p4_validator import numeric_claims, validate_bilingual_pair, validate_writing
from backend.tests.test_p4_api import seeded_p4_client


def case(tmp_path):
    client, _ = seeded_p4_client(tmp_path, reportable=True)
    project = client.post("/api/evidence-verification-runs/v1/report-projects").json()
    with sqlite3.connect(tmp_path / "review.db") as db:
        frozen = json.loads(db.execute("SELECT source_snapshot_json FROM p4_report_projects "
                                       "WHERE id = ?", (project["id"],)).fetchone()[0])
    plan, pack = build_report_plan(frozen, [], [], [], "author", "en")
    group = plan.groups[0]
    comment = ReportComment(report_comment_id=group.group_id,
        source_issue_ids=group.source_issue_ids, source="p3",
        title_zh="方法主张范围", title_en="Scope of the method claim",
        comment_body="The paper describes the method and novelty claim in PDF page 1. "
                     "Could the authors clarify which part of the method is new and how the claim is bounded?",
        scientific_question=group.scientific_question, evidence_refs=pack.issues[0].source_refs[:1],
        author_action_requested="Clarify the method's specific novelty scope.",
        comment_category="clarification", uncertainty_qualification="A clarification, not a proven error")
    writing = ReportWritingResult(prompt_version="review-comment-writing-en/v1",
        language="en", report_kind="author",
        general_assessment="The authors describe a method and its novelty claim.", comments=[comment])
    return plan, pack, writing


def test_writer_must_keep_frozen_issue_and_source(tmp_path):
    plan, pack, writing = case(tmp_path)
    validate_writing(writing, plan, pack)
    wrong = writing.model_copy(deep=True)
    wrong.comments[0].evidence_refs = ["foreign-paper-source"]
    with pytest.raises(ValueError, match="another issue"):
        validate_writing(wrong, plan, pack)
    wrong = writing.model_copy(deep=True)
    wrong.comments[0].scientific_question = "A different scientific accusation"
    with pytest.raises(ValueError, match="frozen scientific question"):
        validate_writing(wrong, plan, pack)


def test_new_numbers_template_claims_and_unapproved_model_output_are_rejected(tmp_path):
    plan, pack, writing = case(tmp_path)
    wrong = writing.model_copy(deep=True)
    wrong.comments[0].comment_body += " The model improved by 91.7 points."
    with pytest.raises(ValueError, match="unsupported numbers"):
        validate_writing(wrong, plan, pack)
    wrong = writing.model_copy(deep=True)
    wrong.comments[0].comment_body = "More experiments are needed. " * 4
    with pytest.raises(ValueError, match="generic"):
        validate_writing(wrong, plan, pack)
    wrong = writing.model_copy(deep=True)
    wrong.comments[0].reviewer_approval_status = "approved_for_final"
    with pytest.raises(ValueError, match="model cannot approve"):
        validate_writing(wrong, plan, pack)


def test_bilingual_versions_cannot_change_numbers_or_evidence(tmp_path):
    _, _, english = case(tmp_path)
    chinese = english.model_copy(deep=True)
    chinese.language = "zh"
    chinese.comments[0].comment_body = "论文在第 1 页描述了方法与创新主张。建议作者明确方法中哪些部分是新的，并说明该主张的适用范围。"
    validate_bilingual_pair(chinese, english)
    chinese.comments[0].comment_body += "准确率达到 92.3。"
    with pytest.raises(ValueError, match="changed evidence, numbers"):
        validate_bilingual_pair(chinese, english)


def test_author_text_cannot_leak_internal_identifiers_or_decisions(tmp_path):
    plan, pack, writing = case(tmp_path)
    wrong = writing.model_copy(deep=True)
    wrong.general_assessment += " Source bundle ru_ev_123."
    with pytest.raises(ValueError, match="internal source"):
        validate_writing(wrong, plan, pack)
    wrong = writing.model_copy(deep=True)
    wrong.comments[0].comment_body += " We recommend rejection."
    with pytest.raises(ValueError, match="publication decision"):
        validate_writing(wrong, plan, pack)


def test_internal_report_cannot_invent_a_numeric_result(tmp_path):
    client, _ = seeded_p4_client(tmp_path, reportable=True)
    project = client.post("/api/evidence-verification-runs/v1/report-projects").json()
    with sqlite3.connect(tmp_path / "review.db") as db:
        frozen = json.loads(db.execute("SELECT source_snapshot_json FROM p4_report_projects "
            "WHERE id = ?", (project["id"],)).fetchone()[0])
    plan, pack = build_report_plan(frozen, [], [], [], "internal", "en")
    group = plan.groups[0]
    comment = ReportComment(report_comment_id=group.group_id,
        source_issue_ids=group.source_issue_ids, source="p3",
        title_zh="方法问题", title_en="Method question",
        comment_body="The method requires clarification of its novelty claim. The fabricated accuracy is 91.7 percent.",
        scientific_question=group.scientific_question, evidence_refs=[],
        author_action_requested="Clarify the method and the evidence supporting the claim.",
        comment_category=group.category, uncertainty_qualification="Unverified")
    result = ReportWritingResult(prompt_version="review-comment-writing-en/v1",
        language="en", report_kind="internal", general_assessment="Authors describe a method.",
        comments=[comment])
    with pytest.raises(ValueError, match="unsupported numbers"):
        validate_writing(result, plan, pack)


def test_numbered_comment_heading_is_not_a_scientific_numeric_claim():
    assert numeric_claims("### 1. Relative position scope\nTable 3 reports 79.1 EM.") == {"79.1"}
    assert numeric_claims("所提供材料定位于 PDF 第 3–4 页，速度下降 7%。") == {"7"}
    assert numeric_claims("PDF pages 3–4 and Tables 2–3 report 7% slower steps.") == {"7"}


def test_clarification_only_report_cannot_turn_unchecked_external_fact_into_assertion(tmp_path):
    plan, pack, writing = case(tmp_path)
    pack.issues[0].external_verification_required = True
    pack.issues[0].external_dependency_status = "clarification_only"
    pack.issues[0].external_reporting_constraint = (
        "External literature was not checked; retain this only as a request for clarification.")
    wrong = writing.model_copy(deep=True)
    wrong.comments[0].comment_body = (
        "Prior work has already proven that the method improves out-of-domain accuracy. "
        "The authors should therefore revise the scope of the claim and explain the discrepancy.")
    wrong.comments[0].uncertainty_qualification = "External literature was not checked."
    with pytest.raises(ValueError, match="unchecked external fact"):
        validate_writing(wrong, plan, pack)
