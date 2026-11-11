"""P4 draft revisions, explicit approval, and exports through the existing renderer."""

import json
import re
import time
import uuid
from typing import Literal

from fastapi import HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from backend.exporter import render_export
from backend.model_runtime import ModelConfigurationError
from backend.p4_budget import (P4BudgetConfigurationError, P4BudgetController,
                               P4BudgetExceededError, P4BudgetStateError,
                               P4BudgetStore, P4UsageError)
from backend.p4_planner import build_report_plan
from backend.p4_report_schema import ReportEvidencePack, ReportPlan, ReportWritingResult
from backend.p4_validator import (numeric_claims, validate_author_body,
                                  validate_bilingual_pair)
from backend.p4_writer import render_markdown, write_report, WRITING_VERSIONS
from scripts.p5c.stage_control import C1ControlError


class CreateDraftInput(BaseModel):
    kind: Literal["internal", "author", "editor"]
    language: Literal["zh", "en"]


class UpdateDraftInput(BaseModel):
    body: str = Field(min_length=1, max_length=100000)
    expected_revision: int = Field(ge=1)


class ApproveDraftInput(BaseModel):
    expected_revision: int = Field(ge=1)
    confirm_content: bool


def register_p4_draft_routes(app, connect, model_session_factory, model_completion,
                             model_preflight, model_session_preflight,
                             budget_configuration_factory, helpers, c1_control=None):
    project_or_404 = helpers["project_or_404"]
    snapshot_of = helpers["snapshot_of"]
    decision_records = helpers["decision_records"]
    evidence_records = helpers["evidence_records"]
    external_evidence_records = helpers["external_evidence_records"]
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

    def call_model(project_id, draft_id, generation_run_id, session, controller, store):
        def invoke(stage, version, system, user):
            requested = session.configuration.model
            messages = [{"role": "system", "content": system},
                        {"role": "user", "content": user}]
            try:
                if c1_control:
                    model_session_preflight(session, requested, c1_run_id=generation_run_id)
                else:
                    model_session_preflight(session, requested)
            except (ModelConfigurationError, C1ControlError):
                store.fail_run(generation_run_id, "configuration_changed",
                               "model_configuration_changed")
                raise
            model_call_id = uuid.uuid4().hex
            try:
                reservation = controller.plan_request(
                    model_call_id=model_call_id, stage=stage, prompt_version=version,
                    messages=messages,
                    required_followup_requests=1 if stage.startswith("writing-") else 0,
                )
            except P4BudgetExceededError as exc:
                store.fail_run(generation_run_id, "budget_exhausted", exc.blocker)
                raise
            store.reserve_request(generation_run_id, reservation, requested)
            controller.apply_reserved(reservation)
            store.mark_dispatch_pending(generation_run_id, model_call_id)
            started = time.perf_counter()
            try:
                response = model_completion(
                    session,
                    model=requested,
                    messages=messages,
                    max_tokens=reservation.max_tokens,
                    timeout=reservation.remaining_duration_ms / 1000,
                )
            except ModelConfigurationError:
                duration_ms = round((time.perf_counter() - started) * 1000)
                store.fail_not_sent(generation_run_id, model_call_id,
                                    "ModelConfigurationError", duration_ms)
                raise
            except Exception as exc:
                duration_ms = round((time.perf_counter() - started) * 1000)
                store.fail_usage_unknown(generation_run_id, model_call_id,
                                         type(exc).__name__, duration_ms)
                raise
            duration_ms = round((time.perf_counter() - started) * 1000)
            try:
                actual = response.model or None
                usage = response.usage
            except Exception:
                store.fail_usage_unknown(
                    generation_run_id, model_call_id,
                    "p4_response_usage_unavailable", duration_ms,
                )
                raise P4UsageError("p4_response_usage_unavailable") from None
            try:
                settlement = controller.validate_usage(reservation, usage)
            except P4UsageError as exc:
                if exc.settlement is None:
                    if exc.blocker in {"p4_usage_missing", "p4_usage_unavailable"}:
                        store.fail_usage_unknown(generation_run_id, model_call_id,
                                                 exc.blocker, duration_ms)
                    else:
                        store.fail_usage_invalid(generation_run_id, model_call_id,
                                                 exc.blocker, duration_ms)
                else:
                    store.settle_terminal_usage(
                        generation_run_id, model_call_id, actual_model=actual,
                        settlement=exc.settlement, duration_ms=duration_ms,
                        reason=exc.blocker, run_state="usage_invalid",
                    )
                raise
            except P4BudgetExceededError as exc:
                if exc.settlement is None:
                    store.fail_usage_invalid(generation_run_id, model_call_id,
                                             exc.blocker, duration_ms,
                                             run_state="budget_exhausted")
                else:
                    store.settle_terminal_usage(
                        generation_run_id, model_call_id, actual_model=actual,
                        settlement=exc.settlement, duration_ms=duration_ms,
                        reason=exc.blocker, run_state="budget_exhausted",
                    )
                raise
            if c1_control and actual != requested:
                store.settle_terminal_usage(
                    generation_run_id, model_call_id, actual_model=actual,
                    settlement=settlement, duration_ms=duration_ms,
                    reason="c1_response_model_mismatch", run_state="usage_invalid",
                )
                raise P4UsageError("c1_response_model_mismatch")
            store.settle_valid_usage(
                generation_run_id, model_call_id, actual_model=actual,
                input_tokens=settlement.input_tokens,
                output_tokens=settlement.output_tokens,
                total_tokens=settlement.total_tokens,
                duration_ms=duration_ms,
                usage_cost_usd=settlement.usage_cost_usd,
            )
            controller.apply_settlement(settlement)
            content = response.choices[0].message.content
            return {"content": content, "model_identifier": actual,
                    "input_tokens": settlement.input_tokens,
                    "output_tokens": settlement.output_tokens}
        return invoke

    def frozen_plan(db, project_id, kind, language):
        row = project_or_404(db, project_id)
        frozen = snapshot_of(row)
        decisions = decision_records(db, project_id)
        reviews = evidence_records(db, project_id)
        external_reviews = external_evidence_records(db, project_id)
        manual = reviewer_issues(db, project_id)
        return build_report_plan(frozen, decisions, reviews, manual, kind, language,
                                 external_reviews), decisions

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
    def create_p4_draft(project_id: str, payload: CreateDraftInput,
                        http_request: Request):
        c1_claimed = None
        if c1_control:
            if (payload.kind, payload.language) != ("internal", "zh"):
                raise HTTPException(409, detail={"code": "c1_report_scope_invalid"})
            try:
                c1_claimed = c1_control.claim(
                    http_request.headers.get("X-P5C-Stage-Authorization"), "p4", project_id)
            except C1ControlError as exc:
                raise HTTPException(409, detail={"code": exc.code}) from None
            if not c1_claimed[1]:
                with connect() as db:
                    previous = db.execute("SELECT draft_id, state, project_id, kind, language "
                                          "FROM p4_generation_runs WHERE id=?",
                                          (c1_claimed[0],)).fetchone()
                    if (previous and previous["state"] == "completed"
                            and previous["project_id"] == project_id
                            and (previous["kind"], previous["language"]) == ("internal", "zh")
                            and previous["draft_id"]):
                        draft = db.execute("SELECT * FROM p4_drafts WHERE id=? AND project_id=? "
                                           "AND kind='internal' AND language='zh'",
                                           (previous["draft_id"], project_id)).fetchone()
                        if draft:
                            result = draft_view(db, draft)
                            result["generation_run_id"] = c1_claimed[0]
                            return result
                return {"generation_run_id": c1_claimed[0],
                        "status": c1_control.store.get_run(c1_claimed[0])["status"]}
        try:
            with connect() as db:
                (plan, pack), _ = frozen_plan(db, project_id, payload.kind, payload.language)
        except Exception:
            if c1_control and c1_claimed:
                c1_control.store.finish_stage(c1_claimed[0], completed=False)
            raise
        draft_id = uuid.uuid4().hex
        generation_run_id = c1_claimed[0] if c1_claimed else uuid.uuid4().hex
        store = P4BudgetStore(connect, now)

        def terminate_run_safely(reason):
            if not plan.groups:
                return
            try:
                store.fail_run(generation_run_id, "interrupted", reason)
            except P4BudgetStateError:
                pass

        try:
            controller = session = None
            if c1_control and not plan.groups:
                budget_configuration = budget_configuration_factory()
                c1_control.check_p4_budget(generation_run_id, budget_configuration)
                store.create_run(generation_run_id, draft_id, project_id,
                                 payload.kind, payload.language, budget_configuration)
            if plan.groups:
                try:
                    model_preflight()
                    budget_configuration = budget_configuration_factory()
                    if c1_control:
                        c1_control.check_p4_budget(generation_run_id, budget_configuration)
                except HTTPException as exc:
                    code = (exc.detail.get("code") if isinstance(exc.detail, dict)
                            else "model_configuration_invalid")
                    store.create_configuration_failed_run(
                        generation_run_id, draft_id, project_id, payload.kind,
                        payload.language, code,
                    )
                    detail = (dict(exc.detail) if isinstance(exc.detail, dict)
                              else {"code": code})
                    detail["generation_run_id"] = generation_run_id
                    raise HTTPException(exc.status_code, detail=detail) from None
                except P4BudgetConfigurationError as exc:
                    store.create_configuration_failed_run(
                        generation_run_id, draft_id, project_id, payload.kind,
                        payload.language, exc.code,
                    )
                    raise
                store.create_run(
                    generation_run_id, draft_id, project_id, payload.kind,
                    payload.language, budget_configuration,
                )
                controller = P4BudgetController(budget_configuration)
                try:
                    batch_count = (1 if payload.kind == "author" else len(plan.groups))
                    controller.ensure_minimum_success_path(batch_count)
                except P4BudgetExceededError as exc:
                    store.fail_run(generation_run_id, "budget_exhausted", exc.blocker)
                    raise
                try:
                    session = model_session_factory()
                except HTTPException as exc:
                    store.fail_run(generation_run_id, "configuration_changed",
                                   "model_configuration_invalid")
                    detail = (dict(exc.detail) if isinstance(exc.detail, dict)
                              else {"code": "model_configuration_invalid"})
                    detail["generation_run_id"] = generation_run_id
                    raise HTTPException(exc.status_code, detail=detail) from None
                except ModelConfigurationError:
                    store.fail_run(generation_run_id, "configuration_changed",
                                   "model_configuration_invalid")
                    raise
            with connect() as db:
                other = db.execute("SELECT writing_json FROM p4_drafts WHERE project_id = ? "
                    "AND kind = ? AND language != ? AND selected_issue_ids = ? "
                    "ORDER BY rowid DESC LIMIT 1", (project_id, payload.kind, payload.language,
                    json.dumps(plan.selected_issue_ids))).fetchone()
            counterpart = (ReportWritingResult.model_validate_json(other["writing_json"])
                           if other and other["writing_json"] else None)
            model_call = (call_model(project_id, draft_id, generation_run_id,
                                     session, controller, store)
                          if plan.groups else None)
            writing = write_report(
                plan, pack, model_call, counterpart,
                (lambda partial: store.save_validated_partial(
                    generation_run_id, partial)) if plan.groups else None,
            )
            body = render_markdown(plan, pack, writing)
            if payload.kind == "author":
                validate_author_body(body, pack)
                with connect() as db:
                    reject_rejected_issue_text(body, snapshot_of(
                        project_or_404(db, project_id)), plan.selected_issue_ids)
            if counterpart:
                validate_bilingual_pair(writing, counterpart)
            if plan.groups:
                try:
                    controller.ensure_within_duration()
                except P4BudgetExceededError as exc:
                    store.fail_run(generation_run_id, "budget_exhausted", exc.blocker)
                    raise
            with connect() as db:
                stamp = now()
                db.execute("INSERT INTO p4_drafts VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?, ?, "
                    "'pending', NULL, ?, ?)", (draft_id, project_id, payload.kind,
                    payload.language, body,
                    json.dumps({"plan": plan.model_dump(), "pack": pack.model_dump()}, ensure_ascii=False),
                    writing.model_dump_json(), json.dumps(plan.selected_issue_ids),
                    writing.prompt_version, stamp, stamp))
                db.execute("INSERT INTO p4_draft_revisions VALUES (?, 1, ?, ?, 'pending', ?)",
                           (draft_id, body, digest(body), stamp))
                if plan.groups or c1_control:
                    store.complete_run_in_transaction(db, generation_run_id)
                result = draft_view(db, draft_or_404(db, draft_id))
                if c1_control:
                    result["generation_run_id"] = generation_run_id
            return result
        except P4BudgetConfigurationError as exc:
            detail = exc.safe_detail()
            detail["generation_run_id"] = generation_run_id
            raise HTTPException(status_code=503, detail=detail) from None
        except P4BudgetExceededError as exc:
            detail = exc.safe_detail()
            detail["generation_run_id"] = generation_run_id
            raise HTTPException(status_code=409, detail=detail) from None
        except (P4UsageError, P4BudgetStateError) as exc:
            if isinstance(exc, P4BudgetStateError):
                terminate_run_safely(exc.blocker)
            detail = exc.safe_detail()
            detail["generation_run_id"] = generation_run_id
            raise HTTPException(status_code=502, detail=detail) from None
        except ModelConfigurationError as exc:
            detail = exc.safe_detail()
            detail["generation_run_id"] = generation_run_id
            raise HTTPException(status_code=503, detail=detail) from None
        except C1ControlError as exc:
            terminate_run_safely(exc.code)
            raise HTTPException(status_code=409, detail={"code": exc.code,
                                                        "generation_run_id": generation_run_id}) from None
        except HTTPException:
            raise
        except (ValueError, KeyError, TypeError) as exc:
            terminate_run_safely("report_content_validation_failed")
            if c1_control:
                raise HTTPException(422, detail={"code": "report_content_validation_failed",
                                                  "generation_run_id": generation_run_id}) from None
            raise HTTPException(422, f"P4 写作校验失败：{type(exc).__name__}: {exc}") from None
        except Exception as exc:
            terminate_run_safely("model_request_failed")
            raise HTTPException(502, f"P4 模型调用失败：{type(exc).__name__}") from None
        finally:
            if c1_control and c1_claimed:
                with connect() as db:
                    state = db.execute("SELECT state, unknown_usage_calls, cost_complete FROM p4_generation_runs "
                                       "WHERE id=?", (generation_run_id,)).fetchone()
                if state and (state["unknown_usage_calls"] or not state["cost_complete"]):
                    c1_control.store.mark_stage_unknown(generation_run_id)
                else:
                    c1_control.store.finish_stage(
                        generation_run_id, completed=bool(state and state["state"] == "completed"))

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
