import fcntl
import hashlib
import json
import os
from decimal import Decimal
from typing import Literal

from fastapi import BackgroundTasks, HTTPException, Response
from pydantic import BaseModel

from backend.auto_review_budget import AutoReviewBudget
from backend.compact_review import (
    DRAFT_PROMPT,
    VERIFY_PROMPT,
    Report,
    Verification,
    encode,
    markdown,
    source_packet,
    validate_checks,
    validate_report,
)
from backend.exporter import render_export


class Start(BaseModel):
    confirm_budget: Literal[True]


def register_compact_review(
    app, *, connect, ledger, lock_path, files_dir, bundle_for, session_for, call
):
    # The shared worker lock also serializes ordinary auto-review crash recovery.
    with connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS compact_reviews (
            document_id TEXT PRIMARY KEY, bundle_id TEXT NOT NULL, status TEXT NOT NULL,
            draft TEXT, verification TEXT, body TEXT, error TEXT)""")

    per_pass = AutoReviewBudget(
        1, 128000, 8000, 8000, Decimal("0.135"), Decimal("0.80"), Decimal("4")
    )
    total = AutoReviewBudget(
        2, 256000, 16000, 8000, Decimal("0.27"), Decimal("0.80"), Decimal("4")
    )

    def get(document_id):
        with connect() as db:
            row = db.execute(
                "SELECT * FROM compact_reviews WHERE document_id=?", (document_id,)
            ).fetchone()
        if not row:
            return {"status": "ready", "planned_limit": str(total.max_cost_usd)}
        data = dict(row)
        key = "compact-" + document_id
        data["usage"] = ledger.summary(key)
        ledger_status = ledger.task_status(key)
        if data["status"] in ("drafting", "checking") and ledger_status in (
            "interrupted",
            "unknown_usage",
        ):
            data["status"] = ledger_status
        for field in ("draft", "verification"):
            if data[field]:
                data[field] = json.loads(data[field])
        return data

    app.get("/api/documents/{document_id}/compact-review")(get)

    def run(document_id, fd, packet, session):
        key = "compact-" + document_id

        def validate_source(_):
            if (
                hashlib.sha256(
                    (files_dir / (document_id + ".pdf")).read_bytes()
                ).hexdigest()
                != document_id
            ):
                raise ValueError("source_changed")

        def generate(stage, prompt, payload, schema):
            result = call(
                session,
                session.configuration.model,
                "compact-" + stage,
                prompt + "\n" + encode(schema.model_json_schema()),
                encode(payload),
                key,
                8000,
                stage,
                source_check=validate_source,
            )
            return schema.model_validate_json(result["content"])

        try:
            blocks = {b["id"]: b for b in packet["blocks"]}
            draft = generate("draft", DRAFT_PROMPT, {"source": packet}, Report)
            validate_report(draft, blocks)
            with connect() as db:
                db.execute(
                    "UPDATE compact_reviews SET draft=?,status='checking' WHERE document_id=?",
                    (draft.model_dump_json(), document_id),
                )
            result = generate(
                "verify",
                VERIFY_PROMPT,
                {"source": packet, "draft": draft.model_dump()},
                Verification,
            )
            validate_checks(result, draft, blocks)
            body = markdown(result.report, blocks, packet["pages_without_text"])
            with connect() as db:
                db.execute(
                    "UPDATE compact_reviews SET verification=?,body=?,status='completed' WHERE document_id=?",
                    (result.model_dump_json(), body, document_id),
                )
                db.execute(
                    "UPDATE auto_review_budget_tasks SET status='completed' WHERE id=?",
                    (key,),
                )
        except Exception as exc:
            # A settlement or diagnostic write can fail after a request was sent.
            # Keep any still-dispatched request unknown, including across restart.
            for item in ledger.summary(key)["calls"]:
                if item["status"] == "dispatch_pending":
                    ledger.mark_unknown(item["id"])
            status = ledger.task_status(key)
            if status not in ("unknown_usage", "budget_exhausted"):
                status = "failed"
            reason = (
                "结构或证据核查未通过"
                if isinstance(exc, ValueError)
                else type(exc).__name__
            )
            with connect() as db:
                db.execute(
                    "UPDATE compact_reviews SET status=?,error=? WHERE document_id=?",
                    (status, reason, document_id),
                )
                db.execute(
                    "UPDATE auto_review_budget_tasks SET status=? WHERE id=? AND status='running'",
                    (status, key),
                )
        finally:
            os.close(fd)

    @app.post("/api/documents/{document_id}/compact-review/start", status_code=202)
    def start(document_id: str, payload: Start, background: BackgroundTasks):
        if get(document_id)["status"] != "ready":
            return get(document_id)
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                existing = get(document_id)
                if existing["status"] != "ready":
                    return existing
                raise HTTPException(409, "审稿工作进程正忙")
            if get(document_id)["status"] != "ready":
                return get(document_id)
            bundle = bundle_for(document_id)
            try:
                packet = source_packet(bundle)
            except ValueError as exc:
                raise HTTPException(422, "未提取到可用全文") from exc
            if (
                len(encode(packet).encode())
                + len(encode(Report.model_json_schema()).encode())
                + len(DRAFT_PROMPT.encode())
                + 64
                > per_pass.max_input_tokens
            ):
                raise HTTPException(
                    422, "完整原文超出两遍审稿的输入限制，请改用其他审稿模式"
                )
            session = session_for(timeout=180, max_retries=0)
            key = "compact-" + document_id
            with connect() as db:
                db.execute(
                    "INSERT INTO compact_reviews(document_id,bundle_id,status) VALUES (?,?,?)",
                    (document_id, bundle["bundle_id"], "drafting"),
                )
                db.execute(
                    """INSERT INTO auto_review_budget_tasks(id,budget,status,requested_model,started_at)
                              VALUES (?,?,'running',?,strftime('%s','now'))""",
                    (key, total.encode(), session.configuration.model),
                )
                db.executemany(
                    "INSERT INTO auto_review_stage_budgets(task_id,stage,budget) VALUES (?,?,?)",
                    [(key, stage, per_pass.encode()) for stage in ("draft", "verify")],
                )
            background.add_task(run, document_id, fd, packet, session)
            fd = None
            return {"status": "drafting"}
        finally:
            if fd is not None:
                os.close(fd)

    @app.get("/api/documents/{document_id}/compact-review/export")
    def export(document_id: str, format: Literal["md", "pdf"]):
        report = get(document_id)
        if report["status"] != "completed":
            raise HTTPException(409, "报告尚未完成核查")
        return Response(
            render_export(report["body"], "report", "zh", format),
            media_type="application/pdf"
            if format == "pdf"
            else "text/markdown; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="internal-DRAFT.{format}"'
            },
        )
