"""Authenticated P4 projects and reviewer records, isolated from legacy drafts."""

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field

from backend.p2a_schema import source_reference_index
from backend.p2c_schema import CandidateConsolidationResult
from backend.p3_engine import validate_p3_result
from backend.p3_schema import EvidenceVerificationResult
from backend.p4_drafts_api import register_p4_draft_routes
from backend.p4_eligibility import eligibility_entries, source_identity
from backend.p4_report_schema import (EvidenceReviewRecord, ReviewerAddedIssue,
                                      ExternalEvidenceReviewRecord, ReviewerDecision)
from backend.p4_planner import external_requirement


def initialize_p4_tables(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS p4_report_projects (
            id TEXT PRIMARY KEY, document_id TEXT NOT NULL, p3_run_id TEXT NOT NULL,
            source_snapshot_json TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS p4_projects_document ON p4_report_projects(document_id);
        CREATE TABLE IF NOT EXISTS p4_reviewer_decisions (
            project_id TEXT NOT NULL, issue_id TEXT NOT NULL, record_json TEXT NOT NULL,
            PRIMARY KEY(project_id, issue_id)
        );
        CREATE TABLE IF NOT EXISTS p4_evidence_reviews (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, issue_id TEXT NOT NULL,
            record_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS p4_external_evidence_reviews (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, issue_id TEXT NOT NULL,
            record_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS p4_reviewer_added_issues (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, record_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS p4_drafts (
            id TEXT PRIMARY KEY, project_id TEXT NOT NULL, kind TEXT NOT NULL,
            language TEXT NOT NULL, revision_number INTEGER NOT NULL, body TEXT NOT NULL,
            plan_json TEXT NOT NULL, writing_json TEXT, selected_issue_ids TEXT NOT NULL,
            generation_prompt_version TEXT NOT NULL, approval_status TEXT NOT NULL,
            approved_body_sha256 TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS p4_drafts_project ON p4_drafts(project_id);
        CREATE TABLE IF NOT EXISTS p4_draft_revisions (
            draft_id TEXT NOT NULL, revision_number INTEGER NOT NULL, body TEXT NOT NULL,
            body_sha256 TEXT NOT NULL, approval_status TEXT NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY(draft_id, revision_number)
        );
        CREATE TABLE IF NOT EXISTS p4_model_calls (
            id TEXT PRIMARY KEY, draft_id TEXT, project_id TEXT NOT NULL, stage TEXT NOT NULL,
            prompt_version TEXT NOT NULL, requested_model TEXT NOT NULL,
            actual_model TEXT, input_tokens INTEGER, output_tokens INTEGER,
            duration_ms INTEGER NOT NULL, status TEXT NOT NULL, error TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS p4_calls_project ON p4_model_calls(project_id);
    """)


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(body):
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class DecisionInput(BaseModel):
    decision: Literal["approve_for_draft", "approve_for_final", "revise", "exclude",
                      "request_more_verification"]
    selected_source_refs: list[str] = Field(default_factory=list)
    reviewer_rationale: str = Field(default="", max_length=4000)
    evidence_review_record_ids: list[str] = Field(default_factory=list)
    external_evidence_review_record_ids: list[str] = Field(default_factory=list)
    external_dependency_disposition: Literal["not_assessed", "verified",
                                             "clarification_only"] = "not_assessed"
    external_uncertainty_qualification: str = Field(default="", max_length=4000)
    reviewer_edited_comment: str | None = Field(default=None, max_length=20000)
    report_order: int = Field(default=0, ge=0)


class EvidenceReviewInput(BaseModel):
    source_ref_id: str
    page_index: int = Field(ge=0)
    object_label: str = Field(min_length=1, max_length=200)
    checked_rows_columns: str = Field(default="", max_length=2000)
    checked_values_and_labels: str = Field(default="", max_length=4000)
    metric_names_and_order: str = Field(default="", max_length=2000)
    unresolved_configurations: list[str] = Field(default_factory=list)
    reviewer_note: str = Field(min_length=8, max_length=4000)
    confirmation_status: Literal["confirmed", "unresolved"]


class ExternalEvidenceReviewInput(BaseModel):
    material_type: Literal["literature", "code", "data", "other"]
    material_identifier: str = Field(min_length=3, max_length=1000)
    verification_question: str = Field(min_length=8, max_length=2000)
    verified_finding: str = Field(min_length=8, max_length=4000)
    limitations: list[str] = Field(default_factory=list)
    reviewer_note: str = Field(min_length=8, max_length=4000)
    confirmation_status: Literal["confirmed", "unresolved"]


class ReviewerIssueInput(BaseModel):
    scientific_question: str = Field(min_length=8, max_length=2000)
    evidence_basis: str = Field(min_length=8, max_length=4000)
    source_refs: list[str] = Field(default_factory=list)
    comment_body: str = Field(default="", max_length=20000)
    category: Literal["major", "minor", "clarification"] = "clarification"


def register_p4_routes(app, connect, files_dir, load_p3_inputs,
                       model_session_factory, model_completion, model_preflight):
    def project_or_404(db, project_id):
        row = db.execute("SELECT * FROM p4_report_projects WHERE id = ?", (project_id,)).fetchone()
        if not row:
            raise HTTPException(404, "P4 报告项目不存在")
        return row

    def snapshot_of(row):
        return json.loads(row["source_snapshot_json"])

    def issue_entries(snapshot):
        return eligibility_entries(EvidenceVerificationResult.model_validate(snapshot["p3"]),
                                   CandidateConsolidationResult.model_validate(snapshot["consolidation"]))

    def decision_records(db, project_id):
        return [ReviewerDecision.model_validate_json(row["record_json"]) for row in db.execute(
            "SELECT record_json FROM p4_reviewer_decisions WHERE project_id = ?", (project_id,))]

    def evidence_records(db, project_id):
        return [EvidenceReviewRecord.model_validate_json(row["record_json"]) for row in db.execute(
            "SELECT record_json FROM p4_evidence_reviews WHERE project_id = ?", (project_id,))]

    def external_evidence_records(db, project_id):
        return [ExternalEvidenceReviewRecord.model_validate_json(row["record_json"])
                for row in db.execute("SELECT record_json FROM p4_external_evidence_reviews "
                                      "WHERE project_id = ?", (project_id,))]

    def reviewer_issues(db, project_id):
        return [ReviewerAddedIssue.model_validate_json(row["record_json"]) for row in db.execute(
            "SELECT record_json FROM p4_reviewer_added_issues WHERE project_id = ?", (project_id,))]

    def project_view(db, row):
        frozen = snapshot_of(row)
        result = EvidenceVerificationResult.model_validate(frozen["p3"])
        issues = issue_entries(frozen)
        issue_views = []
        for item in issues:
            requirement = external_requirement(frozen, item)
            issue_views.append({**item.model_dump(),
                "external_verification_required": requirement["required"],
                "external_dependency_is_critical": requirement["critical"],
                "external_verification_completed_in_p3": requirement["completed_in_p3"],
                "external_scope_clarification_allowed": requirement["scope_clarification"]})
        return {"id": row["id"], "document_id": row["document_id"],
                "source_p3_run_id": row["p3_run_id"], "source": frozen["source"],
                "execution_status": result.execution_status,
                "issues": issue_views,
                "sources": [item.model_dump() for item in result.sources],
                "decisions": [item.model_dump() for item in decision_records(db, row["id"])],
                "evidence_reviews": [item.model_dump() for item in evidence_records(db, row["id"])],
                "external_evidence_reviews": [item.model_dump() for item in
                                              external_evidence_records(db, row["id"])],
                "reviewer_added": [item.model_dump() for item in reviewer_issues(db, row["id"])],
                "drafts": [{"id": item["id"], "kind": item["kind"],
                            "language": item["language"], "revision_number": item["revision_number"],
                            "approval_status": item["approval_status"]} for item in db.execute(
                    "SELECT * FROM p4_drafts WHERE project_id = ? ORDER BY rowid DESC", (row["id"],))]}

    def invalidate_approvals(db, project_id):
        db.execute("UPDATE p4_drafts SET approval_status = 'pending', approved_body_sha256 = NULL "
                   "WHERE project_id = ? AND approval_status = 'approved'", (project_id,))

    @app.get("/api/documents/{document_id}/evidence-verification-runs")
    def list_p3_runs_for_p4(document_id: str):
        with connect() as db:
            if not db.execute("SELECT 1 FROM documents WHERE id = ?", (document_id,)).fetchone():
                raise HTTPException(404, "论文不存在")
            return [dict(row) for row in db.execute(
                "SELECT id, consolidation_run_id, document_id, status, created_at, finished_at "
                "FROM evidence_verification_runs WHERE document_id = ? ORDER BY rowid DESC", (document_id,))]

    @app.get("/api/documents/{document_id}/review-report-projects")
    def list_p4_projects(document_id: str):
        with connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT id, document_id, p3_run_id, created_at FROM p4_report_projects "
                "WHERE document_id = ? ORDER BY rowid DESC", (document_id,))]

    @app.post("/api/evidence-verification-runs/{run_id}/report-projects", status_code=201)
    def create_report_project(run_id: str):
        with connect() as db:
            row = db.execute("SELECT * FROM evidence_verification_runs WHERE id = ?", (run_id,)).fetchone()
        if not row:
            raise HTTPException(404, "P3 核验运行不存在")
        if row["status"] not in {"completed", "limited"} or not row["result_json"]:
            raise HTTPException(409, "P3 核验结果尚不可用于报告")
        try:
            _, bundle, plan, profile, mapping, review, consolidation, pdf_path = \
                load_p3_inputs(row["consolidation_run_id"])
            result = EvidenceVerificationResult.model_validate_json(row["result_json"])
            if (result.evidence_verification_run_id != run_id or
                    row["document_id"] != result.document_id or
                    row["source_bundle_id"] != result.source_bundle_id or
                    row["understanding_run_id"] != result.understanding_run_id or
                    row["professional_review_run_id"] != result.professional_review_run_id or
                    row["consolidation_run_id"] != result.consolidation_run_id):
                raise ValueError("P3 stored run identity mismatch")
            validate_p3_result(result, consolidation, bundle)
            if hashlib.sha256(pdf_path.read_bytes()).hexdigest() != result.pdf_sha256:
                raise ValueError("PDF differs from P3 frozen snapshot")
            source = source_identity(result, row["result_json"])
            frozen = {"source": source.model_dump(), "bundle": bundle,
                      "plan": plan.model_dump(), "profile": profile.model_dump(),
                      "mapping": mapping.model_dump(), "review": review.model_dump(),
                      "consolidation": consolidation.model_dump(), "p3": result.model_dump()}
        except (KeyError, TypeError, ValueError, OSError) as exc:
            raise HTTPException(409, f"P4 来源快照无效：{type(exc).__name__}") from None
        project_id = uuid.uuid4().hex
        with connect() as db:
            db.execute("INSERT INTO p4_report_projects VALUES (?, ?, ?, ?, ?)",
                       (project_id, result.document_id, run_id,
                        json.dumps(frozen, ensure_ascii=False), now()))
            return project_view(db, project_or_404(db, project_id))

    @app.get("/api/review-report-projects/{project_id}")
    def get_report_project(project_id: str):
        with connect() as db:
            return project_view(db, project_or_404(db, project_id))

    @app.post("/api/review-report-projects/{project_id}/issues/{issue_id}/evidence-reviews",
              status_code=201)
    def record_evidence_review(project_id: str, issue_id: str, payload: EvidenceReviewInput):
        with connect() as db:
            frozen = snapshot_of(project_or_404(db, project_id))
            issue = next((item for item in issue_entries(frozen)
                          if item.consolidated_issue_id == issue_id), None)
            if not issue or payload.source_ref_id not in issue.source_refs + issue.counterevidence_refs:
                raise HTTPException(400, "来源不属于当前核验问题")
            source = next((item for item in frozen["p3"]["sources"]
                           if item["source_id"] == payload.source_ref_id), None)
            if not source or source["source_type"] not in {"table", "formula", "figure"} or \
                    source["page_index"] != payload.page_index:
                raise HTTPException(400, "复核对象或 PDF 页码与冻结来源不一致")
            if payload.confirmation_status == "confirmed" and source["source_type"] == "table" and \
                    not (payload.checked_rows_columns and payload.checked_values_and_labels and
                         payload.metric_names_and_order):
                raise HTTPException(400, "表格确认须记录行列、数值与指标顺序")
            record = EvidenceReviewRecord(record_id=uuid.uuid4().hex,
                report_project_id=project_id, consolidated_issue_id=issue_id,
                source_ref_id=payload.source_ref_id, page_index=payload.page_index,
                object_label=payload.object_label,
                checked_rows_columns=payload.checked_rows_columns,
                checked_values_and_labels=payload.checked_values_and_labels,
                metric_names_and_order=payload.metric_names_and_order,
                unresolved_configurations=payload.unresolved_configurations,
                reviewer_note=payload.reviewer_note,
                confirmation_status=payload.confirmation_status, reviewed_at=now())
            db.execute("INSERT INTO p4_evidence_reviews VALUES (?, ?, ?, ?)",
                       (record.record_id, project_id, issue_id, record.model_dump_json()))
            invalidate_approvals(db, project_id)
            return record.model_dump()

    @app.patch("/api/review-report-projects/{project_id}/issues/{issue_id}/decision")
    def save_reviewer_decision(project_id: str, issue_id: str, payload: DecisionInput):
        with connect() as db:
            frozen = snapshot_of(project_or_404(db, project_id))
            entry = next((item for item in issue_entries(frozen)
                          if item.consolidated_issue_id == issue_id), None)
            manual = next((item for item in reviewer_issues(db, project_id)
                           if item.issue_id == issue_id), None)
            if not entry and not manual:
                raise HTTPException(404, "报告问题不存在")
            allowed = set(entry.source_refs + entry.counterevidence_refs if entry else manual.source_refs)
            if not set(payload.selected_source_refs).issubset(allowed):
                raise HTTPException(400, "不能引用另一项问题的证据")
            records = {item.record_id: item for item in evidence_records(db, project_id)
                       if item.consolidated_issue_id == issue_id}
            if not set(payload.evidence_review_record_ids).issubset(records):
                raise HTTPException(400, "人工证据复核记录不属于本问题")
            external_records = {item.record_id: item for item in
                                external_evidence_records(db, project_id)
                                if item.consolidated_issue_id == issue_id}
            if not set(payload.external_evidence_review_record_ids).issubset(external_records):
                raise HTTPException(400, "外部证据核验记录不属于本问题")
            if payload.decision in {"approve_for_draft", "approve_for_final"}:
                if entry and entry.processing_status in {"excluded", "internal_only"}:
                    raise HTTPException(409, "P3 未核实或已反证的问题不能直接批准")
                if not payload.reviewer_rationale.strip():
                    raise HTTPException(400, "批准意见需要人工审核理由")
                if entry:
                    source_index = {item["source_id"]: item for item in frozen["p3"]["sources"]}
                    confirmed_refs = {records[record_id].source_ref_id for record_id in
                                      payload.evidence_review_record_ids if
                                      records[record_id].confirmation_status == "confirmed"}
                    usable = {source_id for source_id in payload.selected_source_refs
                              if source_id in source_index and
                              source_index[source_id]["source_verification_status"] in
                              {"pdf_text_located", "human_confirmed"}} | confirmed_refs
                    if not usable:
                        raise HTTPException(409, "批准意见需要已定位或另行确认的来源")
                    requirement = external_requirement(frozen, entry)
                    confirmed_external = [external_records[record_id] for record_id in
                        payload.external_evidence_review_record_ids
                        if external_records[record_id].confirmation_status == "confirmed"]
                    if payload.external_dependency_disposition == "verified" and not \
                            (confirmed_external or requirement["completed_in_p3"]):
                        raise HTTPException(409, "外部事实未有独立核验记录")
                    externally_verified = (requirement["completed_in_p3"] or
                        (payload.external_dependency_disposition == "verified" and
                         bool(confirmed_external)))
                    clarification_allowed = (
                        payload.external_dependency_disposition == "clarification_only" and
                        requirement["scope_clarification"] and
                        bool(payload.external_uncertainty_qualification.strip()))
                    if requirement["required"] and not requirement["completed_in_p3"] and \
                            (requirement["critical"] or entry.processing_status ==
                             "human_review_required") and not externally_verified and not \
                            clarification_allowed:
                        raise HTTPException(409, "外部证据依赖未核验，且未限定为论文自身范围的澄清问题")
                if entry and entry.processing_status == "human_review_required":
                    visual = {item["source_id"] for item in frozen["p3"]["sources"]
                              if item["source_id"] in allowed and
                              item["source_type"] in {"table", "formula", "figure"} and
                              item["source_verification_status"] == "visual_check_required"}
                    if not visual.issubset(confirmed_refs):
                        raise HTTPException(409, "关键视觉来源尚无可审计的人工复核")
            status = {"approve_for_draft": "approved_for_draft",
                      "approve_for_final": "approved_for_final", "revise": "pending",
                      "exclude": "excluded", "request_more_verification": "needs_verification"}.get(
                          payload.decision)
            if not status:
                raise HTTPException(400, "未知审核决定")
            previous = db.execute("SELECT record_json FROM p4_reviewer_decisions "
                                  "WHERE project_id = ? AND issue_id = ?", (project_id, issue_id)).fetchone()
            old = ReviewerDecision.model_validate_json(previous["record_json"]) if previous else None
            record = ReviewerDecision(decision_id=old.decision_id if old else uuid.uuid4().hex,
                report_project_id=project_id, consolidated_issue_id=issue_id,
                verification_task_id=entry.verification_task_id if entry else None,
                decision=payload.decision, reviewer_approval_status=status,
                selected_source_refs=payload.selected_source_refs,
                reviewer_rationale=payload.reviewer_rationale,
                evidence_review_record_ids=payload.evidence_review_record_ids,
                external_evidence_review_record_ids=payload.external_evidence_review_record_ids,
                external_dependency_disposition=payload.external_dependency_disposition,
                external_uncertainty_qualification=payload.external_uncertainty_qualification,
                reviewer_edited_comment=payload.reviewer_edited_comment,
                report_order=payload.report_order,
                created_at=old.created_at if old else now(), updated_at=now())
            db.execute("INSERT INTO p4_reviewer_decisions VALUES (?, ?, ?) ON CONFLICT(project_id, issue_id) "
                       "DO UPDATE SET record_json = excluded.record_json",
                       (project_id, issue_id, record.model_dump_json()))
            invalidate_approvals(db, project_id)
            return record.model_dump()

    @app.post("/api/review-report-projects/{project_id}/issues/{issue_id}/external-evidence-reviews",
              status_code=201)
    def record_external_evidence_review(project_id: str, issue_id: str,
                                        payload: ExternalEvidenceReviewInput):
        with connect() as db:
            frozen = snapshot_of(project_or_404(db, project_id))
            entry = next((item for item in issue_entries(frozen)
                          if item.consolidated_issue_id == issue_id), None)
            if not entry:
                raise HTTPException(404, "报告问题不存在")
            if not external_requirement(frozen, entry)["required"]:
                raise HTTPException(400, "当前问题未声明外部证据核验需求")
            record = ExternalEvidenceReviewRecord(record_id=uuid.uuid4().hex,
                report_project_id=project_id, consolidated_issue_id=issue_id,
                material_type=payload.material_type,
                material_identifier=payload.material_identifier,
                verification_question=payload.verification_question,
                verified_finding=payload.verified_finding,
                limitations=payload.limitations, reviewer_note=payload.reviewer_note,
                confirmation_status=payload.confirmation_status, reviewed_at=now())
            db.execute("INSERT INTO p4_external_evidence_reviews VALUES (?, ?, ?, ?)",
                       (record.record_id, project_id, issue_id, record.model_dump_json()))
            invalidate_approvals(db, project_id)
            return record.model_dump()

    @app.post("/api/review-report-projects/{project_id}/reviewer-issues", status_code=201)
    def add_reviewer_issue(project_id: str, payload: ReviewerIssueInput):
        with connect() as db:
            frozen = snapshot_of(project_or_404(db, project_id))
            canonical = source_reference_index(frozen["bundle"])
            if not set(payload.source_refs).issubset(canonical):
                raise HTTPException(400, "人工意见引用了其他论文的来源")
            if payload.category not in {"major", "minor", "clarification"}:
                raise HTTPException(400, "未知意见类别")
            item = ReviewerAddedIssue(issue_id="reviewer_" + uuid.uuid4().hex,
                report_project_id=project_id, scientific_question=payload.scientific_question,
                evidence_basis=payload.evidence_basis, source_refs=payload.source_refs,
                comment_body=payload.comment_body, category=payload.category,
                created_at=now(), updated_at=now())
            db.execute("INSERT INTO p4_reviewer_added_issues VALUES (?, ?, ?)",
                       (item.issue_id, project_id, item.model_dump_json()))
            return item.model_dump()

    register_p4_draft_routes(app, connect, model_session_factory, model_completion,
                             model_preflight, {
        "project_or_404": project_or_404, "snapshot_of": snapshot_of,
        "decision_records": decision_records, "evidence_records": evidence_records,
        "external_evidence_records": external_evidence_records,
        "reviewer_issues": reviewer_issues, "now": now, "digest": digest})
