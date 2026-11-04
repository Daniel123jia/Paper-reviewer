import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path

import fitz
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel, Field
from typing import Literal

from backend.review import parse_model_json, pick_review_evidence, retrieve_counterevidence, screen_candidates, suggest_formula_links
from backend.mathcheck import source_number_matches, verify_equation_pair
from backend.experiment import audit_paired_bleu_table
from backend.algorithmcheck import check_matmul_description
from backend.drafts import compose_draft
from backend.exporter import render_export
from backend.access import AccessPolicy, COOKIE_NAME, SESSION_SECONDS
from backend.source_bundle import build_source_bundle
from backend.parse_quality import assess_parse_quality
from backend.p2a_engine import P2AError, run_p2a


MAX_UPLOAD_BYTES = 50 * 1024 * 1024


def load_local_environment(path: Path):
    load_dotenv(path, override=True)


load_local_environment(Path(__file__).resolve().parents[1] / ".env.local")


class ChatRequest(BaseModel):
    message: str


class ReviewCaseRequest(BaseModel):
    mode: Literal["first_review", "own_rereview", "new_reviewer"]
    original_document_id: str
    revised_document_id: str | None = None
    response_document_id: str | None = None


class UnderstandingRunRequest(BaseModel):
    target_journal: str | None = Field(default=None, max_length=200)
    special_requirements: str | None = Field(default=None, max_length=2000)


class EquationCheckRequest(BaseModel):
    lhs: str
    rhs: str
    assumptions: dict[str, str] = Field(default_factory=dict)
    evidence_ids: list[str]


class DraftCreateRequest(BaseModel):
    kind: Literal["report", "opinion"]
    language: Literal["zh", "en"]


class DraftUpdateRequest(BaseModel):
    body: str


class LoginRequest(BaseModel):
    access_token: str


def sse(event: str, value: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(value, ensure_ascii=False)}\n\n"


def calculate_cost(usage):
    input_rate = os.environ.get("OPENAI_INPUT_USD_PER_MILLION")
    output_rate = os.environ.get("OPENAI_OUTPUT_USD_PER_MILLION")
    if not usage or input_rate is None or output_rate is None:
        return None
    input_tokens = getattr(usage, "input_tokens", getattr(usage, "prompt_tokens", None))
    output_tokens = getattr(usage, "output_tokens", getattr(usage, "completion_tokens", None))
    if input_tokens is None or output_tokens is None:
        return None
    return round((input_tokens * float(input_rate) +
                  output_tokens * float(output_rate)) / 1_000_000, 8)


def model_base_url():
    return os.environ.get("OPENAI_BASE_URL", "https://www.yaoxing.best/v1")


def select_evidence(blocks, question):
    terms = {term.casefold() for term in re.findall(r"[A-Za-z]{4,}|[\u4e00-\u9fff]{2,}", question)}
    ranked = sorted(blocks, key=lambda row: sum(row["excerpt"].casefold().count(term) for term in terms), reverse=True)
    matched = [row for row in ranked if any(term in row["excerpt"].casefold() for term in terms)]
    prioritized = []
    equation = re.search(r"(?:\b(?:eq(?:uation)?\.?)\s*|公式\s*)\(?([0-9]+)\)?", question, re.IGNORECASE)
    if equation:
        marker = f"({equation.group(1)})"
        positions = [index for index, row in enumerate(blocks) if row["excerpt"].strip().endswith(marker)]
        if not positions:
            positions = [index for index, row in enumerate(blocks) if marker in row["excerpt"]]
        for index in positions:
            if index > 0 and blocks[index - 1]["page_index"] == blocks[index]["page_index"]:
                prioritized.append(blocks[index - 1])
            prioritized.append(blocks[index])
    return list({row["id"]: row for row in prioritized + matched}.values())[:6]


def create_app(data_dir: Path) -> FastAPI:
    access = AccessPolicy()
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    files_dir = data_dir / "files"
    files_dir.mkdir(exist_ok=True)
    database = data_dir / "review.db"

    def connect():
        connection = sqlite3.connect(database)
        connection.row_factory = sqlite3.Row
        return connection

    with connect() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS documents (
                id TEXT PRIMARY KEY, filename TEXT NOT NULL, sha256 TEXT NOT NULL,
                snapshot_id TEXT NOT NULL, page_count INTEGER NOT NULL,
                sections TEXT NOT NULL, pages TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS evidence (
                id TEXT PRIMARY KEY, document_id TEXT NOT NULL, page_index INTEGER NOT NULL,
                excerpt TEXT NOT NULL, bbox TEXT NOT NULL, precision TEXT NOT NULL,
                FOREIGN KEY(document_id) REFERENCES documents(id)
            );
            CREATE INDEX IF NOT EXISTS evidence_document ON evidence(document_id);
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY, document_id TEXT NOT NULL, created_at TEXT NOT NULL,
                FOREIGN KEY(document_id) REFERENCES documents(id)
            );
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, role TEXT NOT NULL,
                content TEXT NOT NULL, citations TEXT NOT NULL, created_at TEXT NOT NULL,
                FOREIGN KEY(conversation_id) REFERENCES conversations(id)
            );
            CREATE INDEX IF NOT EXISTS messages_conversation ON messages(conversation_id);
            CREATE TABLE IF NOT EXISTS model_calls (
                id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, model TEXT NOT NULL,
                input_tokens INTEGER, output_tokens INTEGER, cost_usd REAL,
                duration_ms INTEGER NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS review_cases (
                id TEXT PRIMARY KEY, mode TEXT NOT NULL, original_document_id TEXT NOT NULL,
                revised_document_id TEXT, response_document_id TEXT, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS review_runs (
                id TEXT PRIMARY KEY, case_id TEXT NOT NULL, status TEXT NOT NULL,
                error TEXT, created_at TEXT NOT NULL, finished_at TEXT
            );
            CREATE TABLE IF NOT EXISTS review_findings (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL, title TEXT NOT NULL, concern TEXT NOT NULL,
                evidence TEXT NOT NULL, evidence_level TEXT NOT NULL,
                verification_status TEXT NOT NULL, reviewer_decision TEXT NOT NULL,
                counterargument TEXT NOT NULL, support_assessment TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS review_model_calls (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL, stage TEXT NOT NULL, model TEXT NOT NULL,
                input_tokens INTEGER, output_tokens INTEGER, cost_usd REAL,
                duration_ms INTEGER NOT NULL, status TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS equation_checks (
                id TEXT PRIMARY KEY, finding_id TEXT NOT NULL, lhs TEXT NOT NULL, rhs TEXT NOT NULL,
                assumptions TEXT NOT NULL, evidence_ids TEXT NOT NULL, result TEXT NOT NULL,
                transcription_status TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS cross_formula_checks (
                id TEXT PRIMARY KEY, case_id TEXT NOT NULL, lhs TEXT NOT NULL, rhs TEXT NOT NULL,
                assumptions TEXT NOT NULL, evidence_ids TEXT NOT NULL, result TEXT NOT NULL,
                source_status TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS experiment_audits (
                id TEXT PRIMARY KEY, case_id TEXT NOT NULL, table_evidence_id TEXT NOT NULL,
                claim_evidence_id TEXT NOT NULL, result TEXT NOT NULL,
                source_status TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS algorithm_audits (
                id TEXT PRIMARY KEY, case_id TEXT NOT NULL, result TEXT NOT NULL,
                source_status TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS finding_verifications (
                id TEXT PRIMARY KEY, finding_id TEXT NOT NULL, status TEXT NOT NULL,
                result TEXT, error TEXT, created_at TEXT NOT NULL, finished_at TEXT
            );
            CREATE TABLE IF NOT EXISTS review_drafts (
                id TEXT PRIMARY KEY, case_id TEXT NOT NULL, kind TEXT NOT NULL,
                language TEXT NOT NULL, body TEXT NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL, UNIQUE(case_id, kind, language)
            );
            CREATE TABLE IF NOT EXISTS draft_model_calls (
                id TEXT PRIMARY KEY, case_id TEXT NOT NULL, draft_id TEXT,
                model TEXT NOT NULL, input_tokens INTEGER, output_tokens INTEGER,
                cost_usd REAL, duration_ms INTEGER NOT NULL, status TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS paper_understanding_runs (
                id TEXT PRIMARY KEY, document_id TEXT NOT NULL, source_bundle_id TEXT,
                source_bundle_json TEXT,
                status TEXT NOT NULL, error TEXT, target_journal TEXT, special_requirements TEXT,
                review_plan TEXT, paper_profile TEXT, claim_evidence_map TEXT,
                created_at TEXT NOT NULL, finished_at TEXT
            );
            CREATE TABLE IF NOT EXISTS paper_understanding_model_calls (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL, stage TEXT NOT NULL,
                prompt_version TEXT NOT NULL, requested_model TEXT NOT NULL, actual_model TEXT,
                input_tokens INTEGER, output_tokens INTEGER, duration_ms INTEGER NOT NULL,
                status TEXT NOT NULL, error TEXT
            );
            CREATE INDEX IF NOT EXISTS p2a_calls_run ON paper_understanding_model_calls(run_id);
        """)
        columns = {row[1] for row in db.execute("PRAGMA table_info(review_findings)")}
        if "support_assessment" not in columns:
            db.execute("ALTER TABLE review_findings ADD COLUMN support_assessment TEXT NOT NULL DEFAULT ''")
        understanding_columns = {row[1] for row in db.execute("PRAGMA table_info(paper_understanding_runs)")}
        if "source_bundle_json" not in understanding_columns:
            db.execute("ALTER TABLE paper_understanding_runs ADD COLUMN source_bundle_json TEXT")
        db.execute("UPDATE review_runs SET status = 'failed', error = '服务重启中断，请重新运行分析', "
                   "finished_at = datetime('now') WHERE status IN ('queued', 'running')")
        db.execute("UPDATE finding_verifications SET status = 'failed', error = '服务重启中断，请重新运行复核', "
                   "finished_at = datetime('now') WHERE status IN ('queued', 'running')")
        db.execute("UPDATE paper_understanding_runs SET status = 'failed', error = 'interrupted_by_restart', "
                   "finished_at = datetime('now') WHERE status IN ('queued', 'running')")

    app = FastAPI(title="AI 论文深度审稿工作台")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def guard_api(request: Request, call_next):
        if (request.url.path.startswith("/api/") and
                request.url.path not in {"/api/auth/status", "/api/auth/login"} and
                not access.valid(request.cookies.get(COOKIE_NAME))):
            return JSONResponse({"detail": "请先登录审稿工作台"}, status_code=401)
        return await call_next(request)

    @app.get("/api/auth/status")
    def auth_status(request: Request):
        return {"required": access.required, "authenticated": access.valid(request.cookies.get(COOKIE_NAME))}

    @app.post("/api/auth/login")
    def auth_login(payload: LoginRequest):
        if not access.matches(payload.access_token):
            raise HTTPException(401, "访问口令无效")
        response = JSONResponse({"authenticated": True})
        response.set_cookie(COOKIE_NAME, access.issue(), max_age=SESSION_SECONDS,
                            httponly=True, secure=access.secure_cookie, samesite="strict", path="/api")
        return response

    @app.post("/api/auth/logout")
    def auth_logout():
        response = JSONResponse({"authenticated": False})
        response.delete_cookie(COOKIE_NAME, path="/api")
        return response

    @app.get("/api/usage/summary")
    def usage_summary():
        with connect() as db:
            rows = [dict(row) for row in db.execute("""
                SELECT 'chat' AS category, model, input_tokens, output_tokens, cost_usd, duration_ms, status FROM model_calls
                UNION ALL
                SELECT 'review', model, input_tokens, output_tokens, cost_usd, duration_ms, status FROM review_model_calls
                UNION ALL
                SELECT 'draft', model, input_tokens, output_tokens, cost_usd, duration_ms, status FROM draft_model_calls
                UNION ALL
                SELECT 'understanding', COALESCE(actual_model, requested_model), input_tokens,
                       output_tokens, NULL, duration_ms, status FROM paper_understanding_model_calls
            """)]

        def summarize(items):
            return {"calls": len(items), "input_tokens": sum(row["input_tokens"] or 0 for row in items),
                    "output_tokens": sum(row["output_tokens"] or 0 for row in items),
                    "duration_ms": sum(row["duration_ms"] for row in items),
                    "known_cost_usd": round(sum(row["cost_usd"] or 0 for row in items), 8),
                    "unpriced_calls": sum(row["cost_usd"] is None for row in items),
                    "failed_calls": sum(row["status"] != "completed" for row in items)}

        categories = sorted({row["category"] for row in rows})
        models = sorted({row["model"] for row in rows})
        return {"total": summarize(rows),
                "by_category": [{"category": category, **summarize([row for row in rows if row["category"] == category])}
                                for category in categories],
                "by_model": [{"model": model, **summarize([row for row in rows if row["model"] == model])}
                             for model in models]}

    def document_or_404(db, document_id):
        row = db.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "论文不存在")
        return row

    def document_json(row):
        return {
            "id": row["id"], "filename": row["filename"], "sha256": row["sha256"],
            "snapshot_id": row["snapshot_id"], "page_count": row["page_count"],
            "sections": json.loads(row["sections"]), "pages": json.loads(row["pages"]),
        }

    def review_case_json(db, row):
        runs = [dict(item) for item in db.execute("SELECT * FROM review_runs WHERE case_id = ? ORDER BY rowid DESC",
                                                   (row["id"],)).fetchall()]
        latest = runs[0] if runs else None
        findings = []
        calls = []
        if latest:
            findings = [{**dict(item), "evidence": json.loads(item["evidence"])} for item in
                        db.execute("SELECT * FROM review_findings WHERE run_id = ? ORDER BY rowid", (latest["id"],))]
            calls = [dict(item) for item in db.execute("SELECT * FROM review_model_calls WHERE run_id = ? ORDER BY rowid",
                                                      (latest["id"],))]
        return {**dict(row), "latest_run": latest, "findings": findings, "model_calls": calls}

    def section_from_excerpt(excerpt, page_index, evidence_id):
        heading = " ".join(excerpt.splitlines())
        numbered = re.fullmatch(r"(\d+(?:\.\d+)*)\s+([A-Z][^\n]{2,70})", heading)
        plain = heading.lower() in ("abstract", "introduction", "related work", "methods",
                                    "experiments", "conclusions", "references")
        if len(heading) >= 90 or not (numbered or plain):
            return None
        return {"title": heading, "page_index": page_index,
                "level": numbered.group(1).count(".") + 1 if numbered else 1,
                "evidence_id": evidence_id}

    @app.get("/api/documents")
    def list_documents():
        with connect() as db:
            rows = db.execute("SELECT * FROM documents ORDER BY rowid DESC").fetchall()
            return [document_json(row) for row in rows]

    @app.post("/api/documents", status_code=201)
    async def upload_document(file: UploadFile = File(...)):
        raw = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(raw) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "PDF 超过 50 MB")
        if not raw.startswith(b"%PDF-"):
            raise HTTPException(400, "文件不是有效 PDF")
        sha256 = hashlib.sha256(raw).hexdigest()
        with connect() as db:
            existing = db.execute("SELECT * FROM documents WHERE sha256 = ? LIMIT 1", (sha256,)).fetchone()
            if existing:
                old_sections = json.loads(existing["sections"])
                rows = db.execute("SELECT id, page_index, excerpt FROM evidence WHERE document_id = ? ORDER BY page_index, rowid",
                                  (existing["id"],)).fetchall()
                recovered = [section for row in rows
                             if (section := section_from_excerpt(row["excerpt"], row["page_index"], row["id"]))]
                if len(recovered) > len(old_sections):
                    db.execute("UPDATE documents SET sections = ? WHERE id = ?",
                               (json.dumps(recovered), existing["id"]))
                    existing = document_or_404(db, existing["id"])
                return document_json(existing)
        try:
            pdf = fitz.open(stream=raw, filetype="pdf")
            if pdf.needs_pass or not pdf.page_count:
                raise ValueError("PDF 已加密或没有页面")
            document_id = sha256
            snapshot_id = uuid.uuid4().hex
            sections = []
            pages = []
            evidence = []
            toc = pdf.get_toc()
            for level, title, page_number in toc:
                if 1 <= page_number <= pdf.page_count:
                    sections.append({"title": title, "page_index": page_number - 1, "level": level})
            for page_index, page in enumerate(pdf):
                blocks = page.get_text("blocks", sort=True)
                pages.append({"page_index": page_index, "width": page.rect.width,
                              "height": page.rect.height, "rotation": page.rotation,
                              "needs_ocr": not any(str(block[4]).strip() for block in blocks if block[6] == 0)})
                for block_index, block in enumerate(blocks):
                    if block[6] != 0 or not str(block[4]).strip():
                        continue
                    excerpt = str(block[4]).strip()
                    evidence_id = f"ev_{snapshot_id[:12]}_{page_index}_{block_index}"
                    evidence.append((evidence_id, document_id, page_index, excerpt,
                                     json.dumps(list(block[:4])), "block"))
                    if not toc and (section := section_from_excerpt(excerpt, page_index, evidence_id)):
                        sections.append(section)
            pdf.close()
        except (fitz.FileDataError, ValueError, RuntimeError) as exc:
            raise HTTPException(400, f"PDF 无法解析：{exc}") from exc

        path = files_dir / f"{document_id}.pdf"
        path.write_bytes(raw)
        try:
            with connect() as db:
                db.execute("INSERT OR IGNORE INTO documents VALUES (?, ?, ?, ?, ?, ?, ?)",
                           (document_id, Path(file.filename or "paper.pdf").name, sha256,
                            snapshot_id, len(pages), json.dumps(sections), json.dumps(pages)))
                if db.execute("SELECT snapshot_id FROM documents WHERE id = ?", (document_id,)).fetchone()[0] == snapshot_id:
                    db.executemany("INSERT INTO evidence VALUES (?, ?, ?, ?, ?, ?)", evidence)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        with connect() as db:
            return document_json(document_or_404(db, document_id))

    @app.get("/api/documents/{document_id}")
    def get_document(document_id: str):
        with connect() as db:
            return document_json(document_or_404(db, document_id))

    @app.get("/api/documents/{document_id}/pdf")
    def get_pdf(document_id: str):
        with connect() as db:
            document_or_404(db, document_id)
        return FileResponse(files_dir / f"{document_id}.pdf", media_type="application/pdf")

    def source_bundle_for_document(document_id: str):
        with connect() as db:
            document = document_or_404(db, document_id)
            rows = db.execute("SELECT * FROM evidence WHERE document_id = ? ORDER BY page_index, rowid",
                              (document_id,)).fetchall()
            return build_source_bundle(document, rows, files_dir / f"{document_id}.pdf")

    @app.get("/api/documents/{document_id}/source-bundle")
    def get_source_bundle(document_id: str):
        return source_bundle_for_document(document_id)

    @app.get("/api/documents/{document_id}/parse-quality")
    def get_parse_quality(document_id: str):
        return assess_parse_quality(source_bundle_for_document(document_id))

    def run_understanding(document_id: str, run_id: str, request: UnderstandingRunRequest):
        try:
            with connect() as db:
                db.execute("UPDATE paper_understanding_runs SET status = 'running' WHERE id = ?", (run_id,))
            bundle = source_bundle_for_document(document_id)
            with connect() as db:
                db.execute("UPDATE paper_understanding_runs SET source_bundle_id = ?, source_bundle_json = ? "
                           "WHERE id = ?", (bundle["bundle_id"], json.dumps(bundle), run_id))
            model = os.environ.get("OPENAI_MODEL", "gpt-6.1-sol")
            client = OpenAI(base_url=model_base_url(), timeout=180, max_retries=0)

            def model_call(stage, system, user):
                started = time.perf_counter()
                stream = client.chat.completions.create(
                    model=model, messages=[{"role": "system", "content": system},
                                           {"role": "user", "content": user}],
                    stream=True, stream_options={"include_usage": True})
                pieces = []
                actual_model = None
                usage = None
                for chunk in stream:
                    actual_model = chunk.model or actual_model
                    usage = chunk.usage or usage
                    if chunk.choices and chunk.choices[0].delta.content:
                        pieces.append(chunk.choices[0].delta.content)
                return {"content": "".join(pieces),
                        "model_identifier": actual_model,
                        "input_tokens": getattr(usage, "prompt_tokens", None),
                        "output_tokens": getattr(usage, "completion_tokens", None),
                        "duration_ms": round((time.perf_counter() - started) * 1000)}

            def persist_result(column, result):
                if column not in {"review_plan", "paper_profile", "claim_evidence_map"}:
                    raise ValueError("unknown understanding stage")
                with connect() as db:
                    db.execute(f"UPDATE paper_understanding_runs SET {column} = ? WHERE id = ?",
                               (result.model_dump_json(), run_id))

            def persist_trace(trace):
                with connect() as db:
                    db.execute("INSERT INTO paper_understanding_model_calls VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                               (uuid.uuid4().hex, run_id, trace.stage, trace.prompt_version,
                                trace.requested_model, trace.actual_model, trace.input_tokens,
                                trace.output_tokens, trace.duration_ms, trace.status, trace.error))

            run_p2a(
                bundle, run_id, model_call, model, request.target_journal,
                request.special_requirements, on_result=persist_result, on_trace=persist_trace)
            with connect() as db:
                db.execute("UPDATE paper_understanding_runs SET status = 'completed', "
                           "finished_at = datetime('now') WHERE id = ?", (run_id,))
        except P2AError as exc:
            with connect() as db:
                db.execute("UPDATE paper_understanding_runs SET status = 'failed', error = ?, "
                           "finished_at = datetime('now') WHERE id = ?",
                           (str(exc), run_id))
        except Exception as exc:
            with connect() as db:
                db.execute("UPDATE paper_understanding_runs SET status = 'failed', error = ?, "
                           "finished_at = datetime('now') WHERE id = ?",
                           (f"source: {type(exc).__name__}", run_id))

    @app.post("/api/documents/{document_id}/understanding-runs", status_code=202)
    def create_understanding_run(document_id: str, payload: UnderstandingRunRequest,
                                 background_tasks: BackgroundTasks):
        if not os.environ.get("OPENAI_API_KEY"):
            raise HTTPException(503, "后端尚未配置 OPENAI_API_KEY")
        with connect() as db:
            document_or_404(db, document_id)
            active = db.execute("SELECT 1 FROM paper_understanding_runs WHERE document_id = ? "
                                "AND status IN ('queued', 'running')", (document_id,)).fetchone()
            if active:
                raise HTTPException(409, "该论文已有正在运行的理解任务")
            run_id = uuid.uuid4().hex
            db.execute("INSERT INTO paper_understanding_runs "
                       "(id, document_id, status, target_journal, special_requirements, created_at) "
                       "VALUES (?, ?, 'queued', ?, ?, datetime('now'))",
                       (run_id, document_id, payload.target_journal, payload.special_requirements))
        background_tasks.add_task(run_understanding, document_id, run_id, payload)
        return {"id": run_id, "document_id": document_id, "status": "queued"}

    @app.get("/api/understanding-runs/{run_id}")
    def get_understanding_run(run_id: str):
        with connect() as db:
            row = db.execute("SELECT * FROM paper_understanding_runs WHERE id = ?", (run_id,)).fetchone()
            if not row:
                raise HTTPException(404, "论文理解任务不存在")
            calls = [dict(item) for item in db.execute(
                "SELECT * FROM paper_understanding_model_calls WHERE run_id = ? ORDER BY rowid", (run_id,))]
        result = dict(row)
        result.pop("source_bundle_json")
        for key in ("review_plan", "paper_profile", "claim_evidence_map"):
            result[key] = json.loads(result[key]) if result[key] else None
        result["model_calls"] = calls
        return result

    @app.get("/api/understanding-runs/{run_id}/source-bundle")
    def get_understanding_source_bundle(run_id: str):
        with connect() as db:
            row = db.execute("SELECT source_bundle_json FROM paper_understanding_runs WHERE id = ?", (run_id,)).fetchone()
        if not row or not row["source_bundle_json"]:
            raise HTTPException(404, "该任务尚无 Source Bundle 快照")
        return json.loads(row["source_bundle_json"])

    @app.post("/api/review-cases", status_code=201)
    def create_review_case(payload: ReviewCaseRequest):
        if payload.mode == "first_review" and (payload.revised_document_id or payload.response_document_id):
            raise HTTPException(400, "一审只能关联原稿")
        if payload.mode == "own_rereview" and (not payload.revised_document_id or not payload.response_document_id):
            raise HTTPException(400, "本人复审需要修订稿和作者回复")
        if payload.mode == "new_reviewer" and not payload.revised_document_id:
            raise HTTPException(400, "新增审稿人二审需要修订稿")
        document_ids = [item for item in (payload.original_document_id, payload.revised_document_id,
                                           payload.response_document_id) if item]
        if len(set(document_ids)) != len(document_ids):
            raise HTTPException(400, "不同材料不能使用同一份 PDF")
        with connect() as db:
            for document_id in document_ids:
                document_or_404(db, document_id)
            case_id = uuid.uuid4().hex
            db.execute("INSERT INTO review_cases VALUES (?, ?, ?, ?, ?, datetime('now'))",
                       (case_id, payload.mode, payload.original_document_id,
                        payload.revised_document_id, payload.response_document_id))
            row = db.execute("SELECT * FROM review_cases WHERE id = ?", (case_id,)).fetchone()
            return review_case_json(db, row)

    @app.get("/api/review-cases")
    def list_review_cases():
        with connect() as db:
            rows = db.execute("SELECT * FROM review_cases ORDER BY rowid DESC").fetchall()
            return [review_case_json(db, row) for row in rows]

    @app.get("/api/review-cases/{case_id}")
    def get_review_case(case_id: str):
        with connect() as db:
            row = db.execute("SELECT * FROM review_cases WHERE id = ?", (case_id,)).fetchone()
            if not row:
                raise HTTPException(404, "审稿任务不存在")
            return review_case_json(db, row)

    @app.get("/api/review-cases/{case_id}/formula-links")
    def list_formula_links(case_id: str):
        with connect() as db:
            case = db.execute("SELECT * FROM review_cases WHERE id = ?", (case_id,)).fetchone()
            if not case:
                raise HTTPException(404, "审稿任务不存在")
            document_ids = [case[key] for key in ("original_document_id", "revised_document_id",
                                                  "response_document_id") if case[key]]
            placeholders = ",".join("?" for _ in document_ids)
            blocks = db.execute(f"SELECT id, document_id, page_index, excerpt FROM evidence "
                                f"WHERE document_id IN ({placeholders}) ORDER BY document_id, page_index, rowid",
                                document_ids).fetchall()
        return [{"from_equation": item["from_equation"], "to_equation": item["to_equation"],
                 "left": {"evidence_id": item["left"]["id"], "document_id": item["left"]["document_id"],
                          "page_index": item["left"]["page_index"], "excerpt": item["left"]["excerpt"]},
                 "right": {"evidence_id": item["right"]["id"], "document_id": item["right"]["document_id"],
                           "page_index": item["right"]["page_index"], "excerpt": item["right"]["excerpt"]},
                 "relation": {"evidence_id": item["relation"]["id"], "document_id": item["relation"]["document_id"],
                              "page_index": item["relation"]["page_index"], "excerpt": item["relation"]["excerpt"]}}
                for item in suggest_formula_links(blocks)]

    def review_model_call(run_id, stage, model, messages):
        started = time.perf_counter()
        usage = None
        status = "failed"
        try:
            client = OpenAI(base_url=model_base_url(), timeout=120, max_retries=1)
            completion = client.chat.completions.create(model=model, messages=messages)
            usage = completion.usage
            content = completion.choices[0].message.content or ""
            if not content:
                raise RuntimeError("模型未返回审稿内容")
            parsed = parse_model_json(content)
            status = "completed"
            return parsed
        finally:
            with connect() as db:
                db.execute("INSERT INTO review_model_calls VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                           (uuid.uuid4().hex, run_id, stage, model,
                            getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None),
                            calculate_cost(usage), round((time.perf_counter() - started) * 1000), status))

    def run_review(case_id, run_id):
        try:
            with connect() as db:
                db.execute("UPDATE review_runs SET status = 'running' WHERE id = ?", (run_id,))
                case = db.execute("SELECT * FROM review_cases WHERE id = ?", (case_id,)).fetchone()
                document_ids = [item for item in (case["original_document_id"], case["revised_document_id"],
                                                   case["response_document_id"]) if item]
                placeholders = ",".join("?" for _ in document_ids)
                blocks = db.execute(f"SELECT id, document_id, page_index, excerpt FROM evidence "
                                    f"WHERE document_id IN ({placeholders}) ORDER BY document_id, page_index, rowid",
                                    document_ids).fetchall()
            selected = pick_review_evidence(blocks)
            if not selected:
                raise ValueError("材料没有可提取文字；扫描 PDF 需要 OCR")
            available = {row["id"]: row for row in selected}
            roles = {case["original_document_id"]: "原稿"}
            if case["revised_document_id"]:
                roles[case["revised_document_id"]] = "修订稿"
            if case["response_document_id"]:
                roles[case["response_document_id"]] = "作者回复"
            source = "\n\n".join(f'[{row["id"]}] {roles[row["document_id"]]}，PDF 第 {row["page_index"] + 1} 页：'
                                  f'{row["excerpt"][:900]}' for row in selected)
            mode_note = {"first_review": "首次审稿：只评价原稿。",
                         "own_rereview": "本人复审：核对修订稿与作者回复是否回应原稿疑点。",
                         "new_reviewer": "新增审稿人二审：独立检查修订稿，可参考原稿与作者回复。"}[case["mode"]]
            model = os.environ.get("OPENAI_MODEL", "gpt-6.1-sol")
            proposal = review_model_call(run_id, "propose", model, [
                {"role": "system", "content": "你是谨慎的科研论文审稿员。只根据给出的原文片段提出 0 到 3 个具体候选问题；"
                 "如果证据不足，返回空数组。不可把公式疑点判定为数学错误。只输出 JSON，格式："
                 '{"candidates":[{"title":"...","concern":"...","evidence_ids":["ev_..."]}]}。'
                 "每条问题必须指明实际支持它的原文证据 ID。"},
                {"role": "user", "content": f"{mode_note}\n以下是抽样原文，覆盖不同页面但并非全文：\n{source}"},
            ])
            candidates = proposal.get("candidates", [])
            if not isinstance(candidates, list):
                raise ValueError("模型返回的候选问题格式无效")
            candidates = candidates[:3]
            checks = []
            if candidates:
                challenge = review_model_call(run_id, "challenge", model, [
                    {"role": "system", "content": "你是独立的审稿反证员。逐条寻找候选问题可能被原文解释或反驳的地方；"
                     "检查引用片段是否真的支持担忧。只输出 JSON，格式："
                     '{"checks":[{"index":0,"verdict":"supported|not_supported|uncertain",'
                     '"support_assessment":"引用原文如何支持或不能支持这个具体判断",'
                     '"counterargument":"具体反证、替代解释或未检查到的限制"}]}。'
                     "仅凭这些片段不能证明数学错误，遇到公式、算法和实验推断要说明仍需独立验证。"},
                    {"role": "user", "content": f"候选问题：{json.dumps(candidates, ensure_ascii=False)}\n\n原文片段：\n{source}"},
                ])
                checks = challenge.get("checks", [])
                if not isinstance(checks, list):
                    raise ValueError("模型返回的反证格式无效")
            findings = screen_candidates(candidates, checks, available)
            with connect() as db:
                for finding in findings:
                    db.execute("INSERT INTO review_findings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                               (uuid.uuid4().hex, run_id, finding["title"], finding["concern"],
                                json.dumps(finding["evidence"]), finding["evidence_level"],
                                finding["verification_status"], finding["reviewer_decision"],
                                finding["counterargument"], finding["support_assessment"]))
                db.execute("UPDATE review_runs SET status = 'completed', finished_at = datetime('now') WHERE id = ?",
                           (run_id,))
        except Exception as exc:
            with connect() as db:
                db.execute("UPDATE review_runs SET status = 'failed', error = ?, finished_at = datetime('now') WHERE id = ?",
                           (f"审稿分析失败：{type(exc).__name__}", run_id))

    @app.post("/api/review-cases/{case_id}/analyze", status_code=202)
    def analyze_review_case(case_id: str, background_tasks: BackgroundTasks):
        if not os.environ.get("OPENAI_API_KEY"):
            raise HTTPException(503, "后端尚未配置 OPENAI_API_KEY")
        with connect() as db:
            if not db.execute("SELECT 1 FROM review_cases WHERE id = ?", (case_id,)).fetchone():
                raise HTTPException(404, "审稿任务不存在")
            running = db.execute("SELECT 1 FROM review_runs WHERE case_id = ? AND status IN ('queued', 'running')",
                                 (case_id,)).fetchone()
            if running:
                raise HTTPException(409, "该任务已有正在运行的分析")
            run_id = uuid.uuid4().hex
            db.execute("INSERT INTO review_runs VALUES (?, ?, 'queued', NULL, datetime('now'), NULL)",
                       (run_id, case_id))
        background_tasks.add_task(run_review, case_id, run_id)
        return {"id": run_id, "case_id": case_id, "status": "queued"}

    def finding_verification_json(row):
        return {**dict(row), "result": json.loads(row["result"]) if row["result"] else None}

    def run_finding_verification(verification_id: str, finding_id: str):
        try:
            with connect() as db:
                db.execute("UPDATE finding_verifications SET status = 'running' WHERE id = ?", (verification_id,))
                finding = db.execute("SELECT * FROM review_findings WHERE id = ?", (finding_id,)).fetchone()
                run = db.execute("SELECT * FROM review_runs WHERE id = ?", (finding["run_id"],)).fetchone()
                case = db.execute("SELECT * FROM review_cases WHERE id = ?", (run["case_id"],)).fetchone()
                document_ids = [case[key] for key in ("original_document_id", "revised_document_id",
                                                      "response_document_id") if case[key]]
                placeholders = ",".join("?" for _ in document_ids)
                blocks = db.execute(f"SELECT id, document_id, page_index, excerpt FROM evidence "
                                    f"WHERE document_id IN ({placeholders}) ORDER BY document_id, page_index, rowid",
                                    document_ids).fetchall()
            cited_ids = {item["evidence_id"] for item in json.loads(finding["evidence"])}
            selected = retrieve_counterevidence(blocks, cited_ids)
            if not selected:
                raise ValueError("候选问题没有可复核的原文证据")
            roles = {case["original_document_id"]: "原稿"}
            if case["revised_document_id"]:
                roles[case["revised_document_id"]] = "修订稿"
            if case["response_document_id"]:
                roles[case["response_document_id"]] = "作者回复"
            source = "\n\n".join(f'[{row["id"]}] {roles[row["document_id"]]}第 {row["page_index"] + 1} 页：'
                                  f'{row["excerpt"][:800]}' for row in selected)
            model = os.environ.get("OPENAI_MODEL", "gpt-6.1-sol")
            response = review_model_call(finding["run_id"], "deep_challenge", model, [
                {"role": "system", "content": "你是独立的科研审稿复核员。对一个候选问题在更广的原文段落中寻找最强反证和合理解释，"
                 "并检查证据是否真正支持具体判断。可选择 verdict 为 supported_clarification、not_supported 或 uncertain；"
                 "不能把未程序验证的数学或算法疑点判为已证实错误。只输出 JSON："
                 '{"verdict":"...","support_assessment":"...","rebuttal":"...",'
                 '"missing_checks":"...","evidence_ids":["ev_..."]}。'
                 "引用只能使用提供的 ID；找不到可靠反证时明确说明。"},
                {"role": "user", "content": f'候选问题：{finding["title"]}\n{finding["concern"]}\n'
                 f'原有反证：{finding["counterargument"]}\n\n扩大检索到的原文（共 {len(blocks)} 个文字块中选 {len(selected)} 个）：\n{source}'},
            ])
            valid_ids = {row["id"] for row in selected}
            source_by_id = {row["id"]: row for row in selected}
            raw_ids = response.get("evidence_ids", [])
            evidence_ids = list(dict.fromkeys(item for item in raw_ids if isinstance(item, str) and item in valid_ids)) if isinstance(raw_ids, list) else []
            verdict = response.get("verdict")
            if verdict not in {"supported_clarification", "not_supported", "uncertain"} or not evidence_ids:
                verdict = "uncertain"
            result = {"verdict": verdict,
                      "support_assessment": str(response.get("support_assessment", ""))[:3000],
                      "rebuttal": str(response.get("rebuttal", ""))[:3000],
                      "missing_checks": str(response.get("missing_checks", ""))[:2000],
                      "evidence_ids": evidence_ids,
                      "evidence": [{"evidence_id": item, "document_id": source_by_id[item]["document_id"],
                                    "page_index": source_by_id[item]["page_index"]} for item in evidence_ids],
                      "scope": {"selected_blocks": len(selected), "total_blocks": len(blocks)},
                      "limitation": "本步骤是扩展原文检索后的模型反证，不构成数学或实验事实的独立程序证明。"}
            with connect() as db:
                db.execute("UPDATE finding_verifications SET status = 'completed', result = ?, finished_at = datetime('now') WHERE id = ?",
                           (json.dumps(result, ensure_ascii=False), verification_id))
        except Exception as exc:
            with connect() as db:
                db.execute("UPDATE finding_verifications SET status = 'failed', error = ?, finished_at = datetime('now') WHERE id = ?",
                           (f"深化复核失败：{type(exc).__name__}", verification_id))

    @app.post("/api/review-findings/{finding_id}/deep-verifications", status_code=202)
    def start_finding_verification(finding_id: str, background_tasks: BackgroundTasks):
        if not os.environ.get("OPENAI_API_KEY"):
            raise HTTPException(503, "后端尚未配置 OPENAI_API_KEY")
        with connect() as db:
            if not db.execute("SELECT 1 FROM review_findings WHERE id = ?", (finding_id,)).fetchone():
                raise HTTPException(404, "候选问题不存在")
            running = db.execute("SELECT 1 FROM finding_verifications WHERE finding_id = ? AND status IN ('queued', 'running')",
                                 (finding_id,)).fetchone()
            if running:
                raise HTTPException(409, "该候选问题已有正在运行的深化复核")
            verification_id = uuid.uuid4().hex
            db.execute("INSERT INTO finding_verifications VALUES (?, ?, 'queued', NULL, NULL, datetime('now'), NULL)",
                       (verification_id, finding_id))
        background_tasks.add_task(run_finding_verification, verification_id, finding_id)
        return {"id": verification_id, "finding_id": finding_id, "status": "queued"}

    @app.get("/api/review-findings/{finding_id}/deep-verifications")
    def list_finding_verifications(finding_id: str):
        with connect() as db:
            if not db.execute("SELECT 1 FROM review_findings WHERE id = ?", (finding_id,)).fetchone():
                raise HTTPException(404, "候选问题不存在")
            rows = db.execute("SELECT * FROM finding_verifications WHERE finding_id = ? ORDER BY rowid DESC",
                              (finding_id,)).fetchall()
            return [finding_verification_json(row) for row in rows]

    def equation_check_json(row):
        return {**dict(row), "assumptions": json.loads(row["assumptions"]),
                "evidence_ids": json.loads(row["evidence_ids"]), "result": json.loads(row["result"])}

    def cross_formula_check_json(db, row):
        ids = json.loads(row["evidence_ids"])
        sources = []
        for evidence_id in ids:
            source = db.execute("SELECT id, document_id, page_index, excerpt FROM evidence WHERE id = ?",
                                (evidence_id,)).fetchone()
            sources.append({"evidence_id": source["id"], "document_id": source["document_id"],
                            "page_index": source["page_index"], "excerpt": source["excerpt"]})
        return {**dict(row), "assumptions": json.loads(row["assumptions"]),
                "evidence_ids": ids, "result": json.loads(row["result"]), "sources": sources}

    @app.post("/api/review-cases/{case_id}/cross-formula-checks", status_code=201)
    def create_cross_formula_check(case_id: str, payload: EquationCheckRequest):
        with connect() as db:
            case = db.execute("SELECT * FROM review_cases WHERE id = ?", (case_id,)).fetchone()
            if not case:
                raise HTTPException(404, "审稿任务不存在")
            ids = payload.evidence_ids
            if len(ids) != 2 or len(set(ids)) != 2:
                raise HTTPException(400, "跨公式核查需要两处不同的原文证据")
            allowed = {case[key] for key in ("original_document_id", "revised_document_id",
                                             "response_document_id") if case[key]}
            sources = db.execute("SELECT id, document_id, excerpt FROM evidence WHERE id IN (?, ?)", ids).fetchall()
            if len(sources) != 2 or any(source["document_id"] not in allowed for source in sources):
                raise HTTPException(400, "证据必须来自当前审稿任务的原文材料")
            try:
                result = verify_equation_pair(payload.lhs, payload.rhs, payload.assumptions)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            source_by_id = {source["id"]: source for source in sources}
            result["source_number_matches"] = source_number_matches(
                payload.lhs, payload.rhs, source_by_id[ids[0]]["excerpt"], source_by_id[ids[1]]["excerpt"])
            check_id = uuid.uuid4().hex
            db.execute("INSERT INTO cross_formula_checks VALUES (?, ?, ?, ?, ?, ?, ?, 'unconfirmed', datetime('now'))",
                       (check_id, case_id, payload.lhs, payload.rhs,
                        json.dumps(payload.assumptions), json.dumps(ids), json.dumps(result)))
            row = db.execute("SELECT * FROM cross_formula_checks WHERE id = ?", (check_id,)).fetchone()
            return cross_formula_check_json(db, row)

    @app.get("/api/review-cases/{case_id}/cross-formula-checks")
    def list_cross_formula_checks(case_id: str):
        with connect() as db:
            if not db.execute("SELECT 1 FROM review_cases WHERE id = ?", (case_id,)).fetchone():
                raise HTTPException(404, "审稿任务不存在")
            rows = db.execute("SELECT * FROM cross_formula_checks WHERE case_id = ? ORDER BY rowid DESC",
                              (case_id,)).fetchall()
            return [cross_formula_check_json(db, row) for row in rows]

    def experiment_audit_json(db, row):
        sources = []
        for key in ("table_evidence_id", "claim_evidence_id"):
            source = db.execute("SELECT id, document_id, page_index, excerpt FROM evidence WHERE id = ?",
                                (row[key],)).fetchone()
            sources.append({"evidence_id": source["id"], "document_id": source["document_id"],
                            "page_index": source["page_index"], "excerpt": source["excerpt"]})
        return {**dict(row), "result": json.loads(row["result"]),
                "table_source": sources[0], "claim_source": sources[1]}

    @app.post("/api/review-cases/{case_id}/experiment-audits", status_code=201)
    def create_experiment_audit(case_id: str):
        with connect() as db:
            case = db.execute("SELECT * FROM review_cases WHERE id = ?", (case_id,)).fetchone()
            if not case:
                raise HTTPException(404, "审稿任务不存在")
            document_id = case["revised_document_id"] or case["original_document_id"]
            blocks = db.execute("SELECT id, excerpt FROM evidence WHERE document_id = ? ORDER BY page_index, rowid",
                                (document_id,)).fetchall()
            tables = [row for row in blocks if "EN-DE BLEU" in row["excerpt"] and "EN-FR BLEU" in row["excerpt"]]
            claims = [row for row in blocks if "For English-to-German" in row["excerpt"]
                      and "For English-to-French" in row["excerpt"]]
            for table in tables:
                for claim in claims:
                    result = audit_paired_bleu_table(table["excerpt"], claim["excerpt"])
                    if result["status"] == "inconclusive":
                        continue
                    audit_id = hashlib.sha256(f'{case_id}:{table["id"]}:{claim["id"]}:paired-bleu-v1'.encode()).hexdigest()[:32]
                    db.execute("INSERT OR IGNORE INTO experiment_audits VALUES (?, ?, ?, ?, ?, 'extracted_text_only', datetime('now'))",
                               (audit_id, case_id, table["id"], claim["id"], json.dumps(result)))
                    row = db.execute("SELECT * FROM experiment_audits WHERE id = ?", (audit_id,)).fetchone()
                    return experiment_audit_json(db, row)
            raise HTTPException(422, "当前论文未找到可明确解析的成对 BLEU 表格与文字结论")

    @app.get("/api/review-cases/{case_id}/experiment-audits")
    def list_experiment_audits(case_id: str):
        with connect() as db:
            if not db.execute("SELECT 1 FROM review_cases WHERE id = ?", (case_id,)).fetchone():
                raise HTTPException(404, "审稿任务不存在")
            rows = db.execute("SELECT * FROM experiment_audits WHERE case_id = ? ORDER BY rowid DESC",
                              (case_id,)).fetchall()
            return [experiment_audit_json(db, row) for row in rows]

    def algorithm_audit_json(row):
        return {**dict(row), "result": json.loads(row["result"])}

    @app.post("/api/review-cases/{case_id}/algorithm-audits", status_code=201)
    def create_algorithm_audit(case_id: str):
        with connect() as db:
            case = db.execute("SELECT * FROM review_cases WHERE id = ?", (case_id,)).fetchone()
            if not case:
                raise HTTPException(404, "审稿任务不存在")
            document_id = case["revised_document_id"] or case["original_document_id"]
            blocks = db.execute("SELECT id, document_id, page_index, excerpt FROM evidence WHERE document_id = ? ORDER BY page_index, rowid",
                                (document_id,)).fetchall()
            checks = []
            for block in blocks:
                parsed = check_matmul_description(block["excerpt"])
                if parsed:
                    checks.append({**parsed, "source": {"evidence_id": block["id"],
                                   "document_id": block["document_id"], "page_index": block["page_index"],
                                   "excerpt": block["excerpt"]}})
            if not checks:
                raise HTTPException(422, "当前论文未找到可明确解析的并行矩阵乘法描述")
            totals = {item["total_output_elements"] for item in checks}
            status = ("shape_mismatch" if any(item["status"] == "shape_mismatch" for item in checks)
                      else "shape_consistent" if len(checks) >= 2 and len(totals) == 1 else "inconclusive")
            result = {"status": status, "checks": checks,
                      "limitation": "仅验证解析文字中的矩阵内维度和输出元素数；不证明算法可运行、训练内存或整体空间复杂度。"}
            audit_id = hashlib.sha256((case_id + ":" + ":".join(item["source"]["evidence_id"] for item in checks)
                                       + ":explicit-matmul-v1").encode()).hexdigest()[:32]
            db.execute("INSERT OR IGNORE INTO algorithm_audits VALUES (?, ?, ?, 'extracted_text_only', datetime('now'))",
                       (audit_id, case_id, json.dumps(result)))
            row = db.execute("SELECT * FROM algorithm_audits WHERE id = ?", (audit_id,)).fetchone()
            return algorithm_audit_json(row)

    @app.get("/api/review-cases/{case_id}/algorithm-audits")
    def list_algorithm_audits(case_id: str):
        with connect() as db:
            if not db.execute("SELECT 1 FROM review_cases WHERE id = ?", (case_id,)).fetchone():
                raise HTTPException(404, "审稿任务不存在")
            rows = db.execute("SELECT * FROM algorithm_audits WHERE case_id = ? ORDER BY rowid DESC",
                              (case_id,)).fetchall()
            return [algorithm_audit_json(row) for row in rows]

    @app.post("/api/review-findings/{finding_id}/equation-checks", status_code=201)
    def create_equation_check(finding_id: str, payload: EquationCheckRequest):
        with connect() as db:
            finding = db.execute("SELECT * FROM review_findings WHERE id = ?", (finding_id,)).fetchone()
            if not finding:
                raise HTTPException(404, "候选问题不存在")
            available = {item["evidence_id"] for item in json.loads(finding["evidence"])}
            if not payload.evidence_ids or not set(payload.evidence_ids).issubset(available):
                raise HTTPException(400, "公式核查必须引用当前问题的原文证据")
            try:
                result = verify_equation_pair(payload.lhs, payload.rhs, payload.assumptions)
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
            check_id = uuid.uuid4().hex
            db.execute("INSERT INTO equation_checks VALUES (?, ?, ?, ?, ?, ?, ?, 'unconfirmed', datetime('now'))",
                       (check_id, finding_id, payload.lhs, payload.rhs,
                        json.dumps(payload.assumptions), json.dumps(payload.evidence_ids), json.dumps(result)))
            return equation_check_json(db.execute("SELECT * FROM equation_checks WHERE id = ?", (check_id,)).fetchone())

    @app.get("/api/review-findings/{finding_id}/equation-checks")
    def list_equation_checks(finding_id: str):
        with connect() as db:
            if not db.execute("SELECT 1 FROM review_findings WHERE id = ?", (finding_id,)).fetchone():
                raise HTTPException(404, "候选问题不存在")
            rows = db.execute("SELECT * FROM equation_checks WHERE finding_id = ? ORDER BY rowid", (finding_id,))
            return [equation_check_json(row) for row in rows]

    @app.post("/api/documents/{document_id}/conversations", status_code=201)
    def create_conversation(document_id: str):
        with connect() as db:
            document_or_404(db, document_id)
            conversation_id = uuid.uuid4().hex
            db.execute("INSERT INTO conversations VALUES (?, ?, datetime('now'))", (conversation_id, document_id))
            return {"id": conversation_id, "document_id": document_id}

    @app.get("/api/documents/{document_id}/conversations")
    def list_conversations(document_id: str):
        with connect() as db:
            document_or_404(db, document_id)
            rows = db.execute("SELECT * FROM conversations WHERE document_id = ? ORDER BY created_at DESC, rowid DESC",
                              (document_id,)).fetchall()
            return [dict(row) for row in rows]

    @app.get("/api/conversations/{conversation_id}")
    def get_conversation(conversation_id: str):
        with connect() as db:
            row = db.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
            if not row:
                raise HTTPException(404, "对话不存在")
            messages = db.execute("SELECT * FROM messages WHERE conversation_id = ? ORDER BY rowid",
                                  (conversation_id,)).fetchall()
            return {**dict(row), "messages": [{**dict(message), "citations": json.loads(message["citations"])}
                                             for message in messages]}

    @app.get("/api/conversations/{conversation_id}/usage")
    def conversation_usage(conversation_id: str):
        with connect() as db:
            if not db.execute("SELECT 1 FROM conversations WHERE id = ?", (conversation_id,)).fetchone():
                raise HTTPException(404, "对话不存在")
            rows = db.execute("SELECT * FROM model_calls WHERE conversation_id = ? ORDER BY rowid",
                              (conversation_id,)).fetchall()
            return [dict(row) for row in rows]

    @app.post("/api/conversations/{conversation_id}/messages/stream")
    def chat(conversation_id: str, payload: ChatRequest):
        question = payload.message.strip()
        if not question or len(question) > 4000:
            raise HTTPException(400, "问题长度必须在 1 到 4000 字符之间")
        if not os.environ.get("OPENAI_API_KEY"):
            raise HTTPException(503, "后端尚未配置 OPENAI_API_KEY")
        with connect() as db:
            conversation = db.execute("SELECT * FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
            if not conversation:
                raise HTTPException(404, "对话不存在")
            document = document_or_404(db, conversation["document_id"])
            previous = db.execute("SELECT role, content FROM messages WHERE conversation_id = ? ORDER BY rowid DESC LIMIT 8",
                                  (conversation_id,)).fetchall()
            blocks = db.execute("SELECT id, page_index, excerpt FROM evidence WHERE document_id = ? ORDER BY page_index, rowid",
                                (document["id"],)).fetchall()

        chosen = select_evidence(blocks, question)
        available = {row["id"]: row for row in chosen}
        context = "\n\n".join(f'[{row["id"]}] PDF 第 {row["page_index"] + 1} 页：{row["excerpt"][:1100]}'
                              for row in chosen)
        instructions = ("你是论文阅读助手。只根据提供的论文原文证据回答。每个事实性判断在句末引用对应证据 ID，"
                        "格式为【ev_...】。没有足够证据时明确说无法从当前材料确认。证据 ID 只表示原文位置，"
                        "不能证明技术判断成立。可用 Markdown 和 LaTeX 排版。不要声称已经独立验证公式。\n\n"
                        f"当前论文 SHA-256：{document['sha256']}\n可用证据：\n{context or '没有检索到相关证据。'}")
        history = [{"role": row["role"], "content": row["content"]} for row in reversed(previous)]
        model = os.environ.get("OPENAI_MODEL", "gpt-6.1-sol")

        def generate():
            started = time.perf_counter()
            answer = []
            usage = None
            status = "failed"
            try:
                yield sse("sources", {"evidence": [{"evidence_id": row["id"], "page_index": row["page_index"],
                                                    "excerpt": row["excerpt"][:400]} for row in chosen]})
                client = OpenAI(base_url=model_base_url(), timeout=90, max_retries=1)
                messages = ([{"role": "system", "content": instructions}] + history +
                            [{"role": "user", "content": question}])
                stream = client.chat.completions.create(model=model, messages=messages,
                                                        stream=True, stream_options={"include_usage": True})
                for chunk in stream:
                    if chunk.usage:
                        usage = chunk.usage
                    if chunk.choices and chunk.choices[0].delta.content:
                        delta = chunk.choices[0].delta.content
                        answer.append(delta)
                        yield sse("delta", {"text": delta})
                content = "".join(answer)
                if not content:
                    raise RuntimeError("模型没有返回回答")
                cited_ids = list(dict.fromkeys(re.findall(r"【(ev_[A-Za-z0-9_]+)】", content)))
                citations = [{"evidence_id": item_id, "page_index": available[item_id]["page_index"]}
                             for item_id in cited_ids if item_id in available]
                status = "completed"
                with connect() as db:
                    db.execute("INSERT INTO messages VALUES (?, ?, 'user', ?, '[]', datetime('now'))",
                               (uuid.uuid4().hex, conversation_id, question))
                    db.execute("INSERT INTO messages VALUES (?, ?, 'assistant', ?, ?, datetime('now'))",
                               (uuid.uuid4().hex, conversation_id, content, json.dumps(citations)))
                yield sse("done", {"citations": citations})
            except Exception as exc:
                yield sse("error", {"message": f"模型调用失败：{type(exc).__name__}"})
            finally:
                with connect() as db:
                    db.execute("INSERT INTO model_calls VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
                               (uuid.uuid4().hex, conversation_id, model,
                                getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None),
                                calculate_cost(usage), round((time.perf_counter() - started) * 1000), status))

        return StreamingResponse(generate(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    @app.get("/api/documents/{document_id}/evidence/{evidence_id}")
    def get_evidence(document_id: str, evidence_id: str):
        with connect() as db:
            document_or_404(db, document_id)
            row = db.execute("SELECT * FROM evidence WHERE document_id = ? AND id = ?",
                             (document_id, evidence_id)).fetchone()
            if row is None:
                raise HTTPException(404, "证据不存在")
            return {"evidence_id": row["id"], "page_index": row["page_index"],
                    "excerpt": row["excerpt"], "bbox": json.loads(row["bbox"]),
                    "precision": row["precision"]}

    @app.get("/api/documents/{document_id}/search")
    def search(document_id: str, q: str):
        query = q.strip()
        if not query or len(query) > 200:
            raise HTTPException(400, "搜索词长度必须在 1 到 200 字符之间")
        with connect() as db:
            document_or_404(db, document_id)
            rows = db.execute("SELECT * FROM evidence WHERE document_id = ? ORDER BY page_index, rowid",
                              (document_id,)).fetchall()
        matches = []
        with fitz.open(files_dir / f"{document_id}.pdf") as pdf:
            page_hits = {page_index: [list(rect) for rect in page.search_for(query)]
                         for page_index, page in enumerate(pdf)}
        for row in rows:
            if query.casefold() in row["excerpt"].casefold():
                block_box = fitz.Rect(json.loads(row["bbox"]))
                hits = [hit for hit in page_hits[row["page_index"]]
                        if block_box.intersects(fitz.Rect(hit))]
                matches.append({"evidence_id": row["id"], "page_index": row["page_index"],
                                "excerpt": row["excerpt"], "bbox": hits[0] if hits else list(block_box),
                                "precision": "match" if hits else "block"})
        return matches[:100]

    def draft_json(db, row):
        calls = db.execute("SELECT * FROM draft_model_calls WHERE draft_id = ? ORDER BY rowid", (row["id"],)).fetchall()
        return {**dict(row), "model_calls": [dict(call) for call in calls]}

    def draft_material(db, case_id):
        case = db.execute("SELECT * FROM review_cases WHERE id = ?", (case_id,)).fetchone()
        if not case:
            raise HTTPException(404, "审稿任务不存在")
        paper_id = case["revised_document_id"] or case["original_document_id"]
        paper = document_or_404(db, paper_id)
        run = db.execute("SELECT id FROM review_runs WHERE case_id = ? AND status = 'completed' ORDER BY rowid DESC LIMIT 1",
                         (case_id,)).fetchone()
        findings = []
        if run:
            for row in db.execute("SELECT * FROM review_findings WHERE run_id = ? ORDER BY rowid", (run["id"],)):
                item = dict(row)
                item["evidence"] = []
                for anchor in json.loads(row["evidence"]):
                    source = db.execute("SELECT excerpt FROM evidence WHERE id = ? AND document_id = ?",
                                        (anchor["evidence_id"], anchor["document_id"])).fetchone()
                    if source:
                        item["evidence"].append({**anchor, "excerpt": source["excerpt"]})
                verification = db.execute("SELECT result FROM finding_verifications WHERE finding_id = ? "
                                          "AND status = 'completed' ORDER BY rowid DESC LIMIT 1", (row["id"],)).fetchone()
                item["deep"] = json.loads(verification["result"]) if verification else None
                findings.append(item)
        formulas = [cross_formula_check_json(db, row) for row in db.execute(
            "SELECT * FROM cross_formula_checks WHERE case_id = ? ORDER BY rowid", (case_id,))]
        experiments = [experiment_audit_json(db, row) for row in db.execute(
            "SELECT * FROM experiment_audits WHERE case_id = ? ORDER BY rowid", (case_id,))]
        algorithms = [algorithm_audit_json(row) for row in db.execute(
            "SELECT * FROM algorithm_audits WHERE case_id = ? ORDER BY rowid", (case_id,))]
        return case, paper, findings, formulas, experiments, algorithms

    @app.post("/api/review-cases/{case_id}/drafts", status_code=201)
    def create_draft(case_id: str, payload: DraftCreateRequest):
        with connect() as db:
            existing = db.execute("SELECT * FROM review_drafts WHERE case_id = ? AND kind = ? AND language = ?",
                                  (case_id, payload.kind, payload.language)).fetchone()
            if existing:
                return draft_json(db, existing)
            case, paper, findings, formulas, experiments, algorithms = draft_material(db, case_id)
        body = compose_draft(payload.kind, paper, case["mode"], findings, formulas, experiments, algorithms)
        draft_id = uuid.uuid4().hex
        if payload.language == "en":
            if not os.environ.get("OPENAI_API_KEY"):
                raise HTTPException(503, "英文草稿需要后端配置 OPENAI_API_KEY")
            model = os.environ.get("OPENAI_MODEL", "gpt-6.1-sol")
            started = time.perf_counter()
            usage = None
            status = "failed"
            try:
                client = OpenAI(base_url=model_base_url(), timeout=120, max_retries=1)
                completion = client.chat.completions.create(model=model, messages=[
                    {"role": "system", "content": "把中文审稿 Markdown 准确翻译为正式学术英语。保留全部 Evidence ID、数字、表格、LaTeX 公式和章节结构；"
                     "不要新增论文事实、错误判断或引文。只输出翻译后的 Markdown。"},
                    {"role": "user", "content": body},
                ])
                usage = completion.usage
                translated = (completion.choices[0].message.content or "").strip()
                if translated.startswith("```markdown") and translated.endswith("```"):
                    translated = translated[len("```markdown"):-3].strip()
                source_ids = set(re.findall(r"ev_[A-Za-z0-9_]+", body))
                if not translated or set(re.findall(r"ev_[A-Za-z0-9_]+", translated)) != source_ids:
                    raise ValueError("英文草稿未完整保留原文证据 ID")
                body = translated
                status = "completed"
            except Exception as exc:
                raise HTTPException(502, f"英文草稿生成失败：{type(exc).__name__}") from exc
            finally:
                with connect() as db:
                    db.execute("INSERT INTO draft_model_calls VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                               (uuid.uuid4().hex, case_id, draft_id if status == "completed" else None, model,
                                getattr(usage, "prompt_tokens", None), getattr(usage, "completion_tokens", None),
                                calculate_cost(usage), round((time.perf_counter() - started) * 1000), status))
        with connect() as db:
            db.execute("INSERT INTO review_drafts VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'))",
                       (draft_id, case_id, payload.kind, payload.language, body))
            row = db.execute("SELECT * FROM review_drafts WHERE id = ?", (draft_id,)).fetchone()
            return draft_json(db, row)

    @app.get("/api/review-cases/{case_id}/drafts")
    def list_drafts(case_id: str):
        with connect() as db:
            if not db.execute("SELECT 1 FROM review_cases WHERE id = ?", (case_id,)).fetchone():
                raise HTTPException(404, "审稿任务不存在")
            rows = db.execute("SELECT * FROM review_drafts WHERE case_id = ? ORDER BY rowid DESC", (case_id,)).fetchall()
            return [draft_json(db, row) for row in rows]

    @app.get("/api/review-drafts/{draft_id}")
    def get_draft(draft_id: str):
        with connect() as db:
            row = db.execute("SELECT * FROM review_drafts WHERE id = ?", (draft_id,)).fetchone()
            if not row:
                raise HTTPException(404, "审稿草稿不存在")
            return draft_json(db, row)

    @app.put("/api/review-drafts/{draft_id}")
    def update_draft(draft_id: str, payload: DraftUpdateRequest):
        body = payload.body
        if not body.strip() or len(body) > 100_000:
            raise HTTPException(400, "审稿正文长度必须在 1 到 100000 字符之间")
        if re.search(r"!\[[^]]*\]\(|<\s*img\b", body, re.IGNORECASE):
            raise HTTPException(400, "当前导出不支持外部图片")
        with connect() as db:
            if not db.execute("SELECT 1 FROM review_drafts WHERE id = ?", (draft_id,)).fetchone():
                raise HTTPException(404, "审稿草稿不存在")
            db.execute("UPDATE review_drafts SET body = ?, updated_at = datetime('now') WHERE id = ?", (body, draft_id))
            row = db.execute("SELECT * FROM review_drafts WHERE id = ?", (draft_id,)).fetchone()
            return draft_json(db, row)

    @app.get("/api/review-drafts/{draft_id}/export")
    def export_draft(draft_id: str, format: Literal["md", "docx", "pdf"]):
        with connect() as db:
            row = db.execute("SELECT * FROM review_drafts WHERE id = ?", (draft_id,)).fetchone()
            if not row:
                raise HTTPException(404, "审稿草稿不存在")
            draft = dict(row)
        try:
            data = render_export(draft["body"], draft["kind"], draft["language"], format)
        except Exception as exc:
            raise HTTPException(500, f"文档导出失败：{type(exc).__name__}") from exc
        name = f'{"review_report" if draft["kind"] == "report" else "review_opinion"}_{draft["language"]}.{format}'
        media = {"md": "text/markdown; charset=utf-8",
                 "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                 "pdf": "application/pdf"}[format]
        return Response(data, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'})

    return app


app = create_app(Path(os.environ.get("PAPER_REVIEW_DATA_DIR", Path(__file__).resolve().parents[1] / "data")))
