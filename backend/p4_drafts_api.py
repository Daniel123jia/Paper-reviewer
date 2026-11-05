"""P4 draft revisions, explicit approval, and exports through the existing renderer."""

import json
import os
import re
import time
import uuid
from typing import Literal

from fastapi import HTTPException
from fastapi.responses import Response
from openai import APIConnectionError, APITimeoutError
from pydantic import BaseModel, Field

from backend.exporter import render_export
from backend.p4_planner import build_report_plan
from backend.p4_report_schema import ReportEvidencePack, ReportPlan, ReportWritingResult
from backend.p4_validator import (numeric_claims, validate_author_body,
                                  validate_bilingual_pair)
from backend.p4_writer import render_markdown, write_report, WRITING_VERSIONS


class CreateDraftInput(BaseModel):
    kind: Literal["internal", "author", "editor"]
    language: Literal["zh", "en"]


class UpdateDraftInput(BaseModel):
    body: str = Field(min_length=1, max_length=100000)
    expected_revision: int = Field(ge=1)


class ApproveDraftInput(BaseModel):
    expected_revision: int = Field(ge=1)
    confirm_content: bool


def register_p4_draft_routes(app, connect, model_factory, helpers):
    project_or_404 = helpers["project_or_404"]
    snapshot_of = helpers["snapshot_of"]
    decision_records = helpers["decision_records"]
    evidence_records = helpers["evidence_records"]
    reviewer_issues = helpers["reviewer_issues"]
    now = helpers["now"]
    digest = helpers["digest"]

    def draft_or_404(db, draft_id):
        row = db.execute("SELECT * FROM p4_drafts WHERE id = ?", (draft_id,)).fetchone()
        if not row:
            raise HTTPException(404, "P4 报告草稿不存在")
        return row

    def draft_view(db, row):
        result = dict(row)
        result["selected_issue_ids"] = json.loads(result["selected_issue_ids"])
        result["plan"] = json.loads(result.pop("plan_json"))["plan"]
        result["writing"] = json.loads(result.pop("writing_json")) if result["writing_json"] else None
        result["revisions"] = [dict(item) for item in db.execute(
            "SELECT revision_number, body_sha256, approval_status, created_at "
            "FROM p4_draft_revisions WHERE draft_id = ? ORDER BY revision_number", (row["id"],))]
        result["model_calls"] = [dict(item) for item in db.execute(
            "SELECT stage, prompt_version, requested_model, actual_model, input_tokens, "
            "output_tokens, duration_ms, status, error FROM p4_model_calls "
            "WHERE draft_id = ? ORDER BY rowid", (row["id"],))]
        return result

    def call_model(project_id, draft_id):
        requested = os.environ.get("OPENAI_MODEL", "gpt-6.1-sol")
        client = None

        def invoke(stage, version, system, user):
            nonlocal client
            for attempt in range(2):
                started = time.perf_counter()
                actual = None
                input_tokens = output_tokens = None
                error = None
                try:
                    if client is None:
                        client = model_factory()
                    response = client.chat.completions.create(model=requested,
                        messages=[{"role": "system", "content": system},
                                  {"role": "user", "content": user}])
                    actual = response.model or None
                    usage = response.usage
                    input_tokens = getattr(usage, "prompt_tokens", None)
                    output_tokens = getattr(usage, "completion_tokens", None)
                    content = response.choices[0].message.content
                    if not content:
                        raise ValueError("empty model response")
                    return {"content": content, "model_identifier": actual,
                            "input_tokens": input_tokens, "output_tokens": output_tokens}
                except (APIConnectionError, APITimeoutError) as exc:
                    error = type(exc).__name__
                    if attempt:
                        raise
                except Exception as exc:
                    error = type(exc).__name__
                    raise
                finally:
                    with connect() as db:
                        db.execute("INSERT INTO p4_model_calls VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (uuid.uuid4().hex, draft_id, project_id, stage, version, requested,
                             actual, input_tokens, output_tokens,
                             round((time.perf_counter() - started) * 1000),
                             "failed" if error else "completed", error, now()))
        return invoke

    def frozen_plan(db, project_id, kind, language):
        row = project_or_404(db, project_id)
        frozen = snapshot_of(row)
        decisions = decision_records(db, project_id)
        reviews = evidence_records(db, project_id)
        manual = reviewer_issues(db, project_id)
        return build_report_plan(frozen, decisions, reviews, manual, kind, language), decisions

    def reject_rejected_issue_text(body, snapshot, selected):
        for issue in snapshot["p3"]["adjudications"]:
            question = issue["original_central_question"].strip()
            if issue["consolidated_issue_id"] not in selected and len(question) > 35 and \
                    question.casefold() in body.casefold():
                raise ValueError("unselected P3 accusation appeared in author report")
            if issue["consolidated_issue_id"] in selected and issue["verdict"] == "concern_narrowed":
                if issue["revised_question"] != question and len(question) > 35 and \
                        question.casefold() in body.casefold():
                    raise ValueError("original accusation replaced the narrowed question")
                for subcheck in issue["subchecks"]:
                    defeated = subcheck["question"].strip()
                    if subcheck["status"] == "contradicted" and len(defeated) > 35 and \
                            defeated.casefold() in body.casefold():
                        raise ValueError("refuted subcheck reappeared in author report")

    @app.post("/api/review-report-projects/{project_id}/drafts", status_code=201)
    def create_p4_draft(project_id: str, payload: CreateDraftInput):
        with connect() as db:
            (plan, pack), _ = frozen_plan(db, project_id, payload.kind, payload.language)
        if plan.groups and not os.environ.get("OPENAI_API_KEY"):
            raise HTTPException(503, "后端尚未配置报告写作模型")
        draft_id = uuid.uuid4().hex
        try:
            with connect() as db:
                other = db.execute("SELECT writing_json FROM p4_drafts WHERE project_id = ? "
                    "AND kind = ? AND language != ? AND selected_issue_ids = ? "
                    "ORDER BY rowid DESC LIMIT 1", (project_id, payload.kind, payload.language,
                    json.dumps(plan.selected_issue_ids))).fetchone()
            counterpart = (ReportWritingResult.model_validate_json(other["writing_json"])
                           if other and other["writing_json"] else None)
            writing = write_report(plan, pack, call_model(project_id, draft_id), counterpart)
            body = render_markdown(plan, pack, writing)
            if payload.kind == "author":
                validate_author_body(body, pack)
                with connect() as db:
                    reject_rejected_issue_text(body, snapshot_of(
                        project_or_404(db, project_id)), plan.selected_issue_ids)
            with connect() as db:
                if counterpart:
                    validate_bilingual_pair(writing, counterpart)
                stamp = now()
                db.execute("INSERT INTO p4_drafts VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, "
                    "'pending', NULL, ?, ?)", (draft_id, project_id, payload.kind,
                    payload.language, body,
                    json.dumps({"plan": plan.model_dump(), "pack": pack.model_dump()}, ensure_ascii=False),
                    writing.model_dump_json(), json.dumps(plan.selected_issue_ids),
                    writing.prompt_version, stamp, stamp))
                db.execute("INSERT INTO p4_draft_revisions VALUES (?, 1, ?, ?, 'pending', ?)",
                           (draft_id, body, digest(body), stamp))
                return draft_view(db, draft_or_404(db, draft_id))
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(422, f"P4 写作校验失败：{type(exc).__name__}: {exc}") from None
        except Exception as exc:
            raise HTTPException(502, f"P4 模型调用失败：{type(exc).__name__}") from None

    @app.get("/api/review-report-drafts/{draft_id}")
    def get_p4_draft(draft_id: str):
        with connect() as db:
            return draft_view(db, draft_or_404(db, draft_id))

    @app.put("/api/review-report-drafts/{draft_id}")
    def update_p4_draft(draft_id: str, payload: UpdateDraftInput):
        body = payload.body
        if not body.strip() or re.search(r"!\[[^]]*\]\(|<\s*img\b", body, re.I):
            raise HTTPException(400, "报告正文为空或包含不支持的外部图片")
        with connect() as db:
            row = draft_or_404(db, draft_id)
            if payload.expected_revision != row["revision_number"]:
                raise HTTPException(409, "报告已被另一次修改更新，请重新载入")
            if body == row["body"]:
                return draft_view(db, row)
            frozen = snapshot_of(project_or_404(db, row["project_id"]))
            stored = json.loads(row["plan_json"])
            plan = ReportPlan.model_validate(stored["plan"])
            pack = ReportEvidencePack.model_validate(stored["pack"])
            if row["kind"] == "author":
                try:
                    if not plan.selected_issue_ids:
                        raise ValueError("请先添加有来源依据的人工意见，再重新生成作者草稿")
                    if len(re.findall(r"(?m)^###\s+", body)) != len(plan.groups):
                        raise ValueError("编辑不能新增或删除结构化意见；请更新问题决定并重新生成")
                    validate_author_body(body, pack)
                    reject_rejected_issue_text(body, frozen, json.loads(row["selected_issue_ids"]))
                except ValueError as exc:
                    raise HTTPException(400, str(exc)) from None
            revision = row["revision_number"] + 1
            stamp = now()
            db.execute("UPDATE p4_drafts SET body = ?, revision_number = ?, approval_status = "
                       "'pending', approved_body_sha256 = NULL, updated_at = ? WHERE id = ?",
                       (body, revision, stamp, draft_id))
            db.execute("INSERT INTO p4_draft_revisions VALUES (?, ?, ?, ?, 'pending', ?)",
                       (draft_id, revision, body, digest(body), stamp))
            return draft_view(db, draft_or_404(db, draft_id))

    @app.post("/api/review-report-drafts/{draft_id}/approve")
    def approve_p4_draft(draft_id: str, payload: ApproveDraftInput):
        if not payload.confirm_content:
            raise HTTPException(400, "请明确确认当前正文")
        with connect() as db:
            row = draft_or_404(db, draft_id)
            if row["revision_number"] != payload.expected_revision:
                raise HTTPException(409, "待审核版本已变化")
            stored = json.loads(row["plan_json"])
            plan = ReportPlan.model_validate(stored["plan"])
            pack = ReportEvidencePack.model_validate(stored["pack"])
            (current, _), decisions = frozen_plan(db, row["project_id"], row["kind"], row["language"])
            if current.selected_issue_ids != plan.selected_issue_ids or \
                    [group.model_dump() for group in current.groups] != \
                    [group.model_dump() for group in plan.groups]:
                raise HTTPException(409, "审核决定或来源已变化，请重新生成草稿")
            if row["kind"] == "author":
                approved = {item.consolidated_issue_id for item in decisions
                            if item.reviewer_approval_status == "approved_for_final"}
                if not set(plan.selected_issue_ids).issubset(approved):
                    raise HTTPException(409, "所有作者可见意见须逐项批准进入最终稿")
                try:
                    validate_author_body(row["body"], pack)
                    reject_rejected_issue_text(row["body"], snapshot_of(
                        project_or_404(db, row["project_id"])), plan.selected_issue_ids)
                except ValueError as exc:
                    raise HTTPException(400, str(exc)) from None
                other = db.execute("SELECT body FROM p4_drafts WHERE project_id = ? AND "
                    "kind = 'author' AND language != ? AND selected_issue_ids = ? "
                    "ORDER BY rowid DESC LIMIT 1", (row["project_id"], row["language"],
                    json.dumps(plan.selected_issue_ids))).fetchone()
                if other and numeric_claims(row["body"]) != numeric_claims(other["body"]):
                    raise HTTPException(409, "中英文报告的关键数值不一致")
            stamp = now()
            db.execute("UPDATE p4_drafts SET approval_status = 'approved', "
                       "approved_body_sha256 = ?, updated_at = ? WHERE id = ?",
                       (digest(row["body"]), stamp, draft_id))
            db.execute("UPDATE p4_draft_revisions SET approval_status = 'approved' "
                       "WHERE draft_id = ? AND revision_number = ?",
                       (draft_id, row["revision_number"]))
            return draft_view(db, draft_or_404(db, draft_id))

    @app.get("/api/review-report-drafts/{draft_id}/export")
    def export_p4_draft(draft_id: str, format: Literal["md", "docx", "pdf"],
                        final: bool = False):
        with connect() as db:
            row = draft_or_404(db, draft_id)
            if final and (row["approval_status"] != "approved" or
                          row["approved_body_sha256"] != digest(row["body"])):
                raise HTTPException(409, "当前版本未获审核批准，不能导出正式稿")
            body = row["body"]
            if not final:
                notice = ("# 待审核草稿 / DRAFT\n\n此版本尚未完成审稿人最终审核。\n\n" if
                          row["language"] == "zh" else
                          "# DRAFT — NOT APPROVED\n\nThis version has not received final reviewer approval.\n\n")
                body = notice + body
            kind = "opinion" if row["kind"] == "author" else "report"
        try:
            data = render_export(body, kind, row["language"], format)
        except Exception as exc:
            raise HTTPException(500, f"文档导出失败：{type(exc).__name__}") from None
        media = {"md": "text/markdown; charset=utf-8",
                 "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                 "pdf": "application/pdf"}[format]
        name = f'p4_{row["kind"]}_{row["language"]}_{"final" if final else "draft"}.{format}'
        return Response(data, media_type=media,
                        headers={"Content-Disposition": f'attachment; filename="{name}"'})
