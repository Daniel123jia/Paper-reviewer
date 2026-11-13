import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from dataclasses import replace
from pathlib import Path

import fitz
from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from dotenv import dotenv_values
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
from backend.p2a_engine import BudgetExceeded, P2AError, RunBudget, VERSIONS, run_p2a
from backend.p2a_resume import (N3_SCHEMA_VERSION, StoredN3Checkpoint,
                                build_n3_partition_descriptors,
                                checkpoint_content_rebinds_exactly,
                                checkpoint_result_hash, inherit_n3_checkpoint, run_p2a_n3,
                                validate_checkpoint, validate_inherited_understanding_assets)
from backend.p2a_schema import (ClaimEvidenceMap, PaperProfile, ReviewPlan,
                                validate_source_references)
from backend.p2b_engine import run_p2b, validate_understanding_snapshot
from backend.p2b_schema import ProfessionalReviewResult
from backend.p2c_engine import run_p2c
from backend.p2c_normalizer import validate_consolidation_inputs
from backend.p2c_schema import CandidateConsolidationResult
from backend.p3_engine import run_p3
from backend.p3_sources import validate_p3_inputs
from backend.p4_api import initialize_p4_tables, register_p4_routes
from backend.p4_budget import (P4_BUDGET_ENV_KEYS, P4_WORKER_ENV_KEYS,
                               recover_p4_generation_runs,
                               require_p4_budget_configuration,
                               require_p4_single_worker)
from backend.model_runtime import (REQUIRED_MODEL_ENV_KEYS,
                                   ModelConfigurationError,
                                   create_model_session,
                                   create_validated_chat_completion,
                                   require_model_runtime_config,
                                   validate_model_session_configuration)
from backend.p5c_runtime import C1Runtime
from scripts.p5c.stage_control import C1ControlError, GuardedChatClient


MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MODEL_ENVIRONMENT_FILE = Path(__file__).resolve().parents[1] / ".env.local"


def load_local_environment(path: Path):
    try:
        values = dotenv_values(path)
    except Exception:
        # Runtime model preflight reports a stable, redacted configuration
        # error.  Importing the application must not expose parser details.
        return
    for key, value in values.items():
        if key not in (REQUIRED_MODEL_ENV_KEYS + P4_BUDGET_ENV_KEYS
                       + P4_WORKER_ENV_KEYS) and not key.startswith("P5C_") and value is not None:
            os.environ[key] = value


load_local_environment(MODEL_ENVIRONMENT_FILE)


def runtime_model_configuration():
    return require_model_runtime_config(MODEL_ENVIRONMENT_FILE)


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


class ProfessionalReviewRunRequest(BaseModel):
    review_requirements: str | None = Field(default=None, max_length=2000)


class ConsolidationRunRequest(BaseModel):
    retry: bool = False


class EvidenceVerificationRunRequest(BaseModel):
    retry: bool = False


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
    require_p4_single_worker(environment_file=MODEL_ENVIRONMENT_FILE)
    access = AccessPolicy()
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    files_dir = data_dir / "files"
    files_dir.mkdir(exist_ok=True)
    database = data_dir / "review.db"
    c1 = (C1Runtime(data_dir, root=Path(__file__).resolve().parents[1],
                    environment_file=MODEL_ENVIRONMENT_FILE)
          if os.environ.get("P5C_CONTROLLED_MODE") == "1" else None)
    if c1 and os.environ.get("PAPER_REVIEW_WORKER_COUNT") != "1":
        raise C1ControlError("single_worker_declaration_required")

    def connect():
        connection = sqlite3.connect(database)
        connection.row_factory = sqlite3.Row
        return connection

    def model_configuration_or_503():
        try:
            return runtime_model_configuration()
        except ModelConfigurationError as exc:
            raise HTTPException(status_code=503, detail=exc.safe_detail()) from None

    def model_session(*, timeout, max_retries, c1_run_id=None, allow_c1_p4=False):
        if c1 and (max_retries != 0 or (c1_run_id is None and not allow_c1_p4)):
            raise C1ControlError("c1_model_request_not_authorized")
        configuration = runtime_model_configuration()
        session = create_model_session(
            configuration,
            timeout=timeout,
            max_retries=max_retries,
            client_factory=OpenAI,
        )
        if c1_run_id is not None:
            if c1 is None:
                raise C1ControlError("c1_mode_not_enabled")
            identity = c1.identity()
            view = c1.store.get_run(c1_run_id)
            if view["status"] != "dispatch_pending":
                raise C1ControlError("c1_stage_not_dispatchable")
            session = replace(session, client=GuardedChatClient(
                session.client, c1.store, c1_run_id, identity, c1.identity))
        return session

    def model_session_or_503(*, timeout, max_retries, allow_c1_p4=False):
        try:
            return model_session(timeout=timeout, max_retries=max_retries,
                                 allow_c1_p4=allow_c1_p4)
        except ModelConfigurationError as exc:
            raise HTTPException(status_code=503, detail=exc.safe_detail()) from None
        except C1ControlError as exc:
            raise HTTPException(status_code=409, detail={"code": exc.code}) from None

    def model_completion(session, **request):
        return create_validated_chat_completion(
            session, MODEL_ENVIRONMENT_FILE, **request
        )

    def model_session_preflight(session, requested_model, c1_run_id=None):
        validate_model_session_configuration(
            session, MODEL_ENVIRONMENT_FILE, requested_model=requested_model
        )
        if c1 and c1_run_id:
            c1.store.assert_run_identity(c1_run_id, c1.identity())

    def c1_claim(request: Request, stage: str, upstream_run_id: str):
        if c1 is None:
            return None
        try:
            return c1.claim(request.headers.get("X-P5C-Stage-Authorization"),
                            stage, upstream_run_id)
        except C1ControlError as exc:
            raise HTTPException(409, detail={"code": exc.code}) from None

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
            CREATE TABLE IF NOT EXISTS paper_understanding_run_lineage (
                run_id TEXT PRIMARY KEY, parent_run_id TEXT NOT NULL,
                plan_source_run_id TEXT NOT NULL, profile_source_run_id TEXT NOT NULL,
                document_id TEXT NOT NULL, source_bundle_id TEXT NOT NULL,
                source_snapshot_id TEXT NOT NULL, pdf_sha256 TEXT NOT NULL,
                plan_prompt_version TEXT NOT NULL, plan_schema_version TEXT NOT NULL,
                profile_prompt_version TEXT NOT NULL, profile_schema_version TEXT NOT NULL,
                n3_prompt_version TEXT NOT NULL, n3_schema_version TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS p2a_lineage_parent
                ON paper_understanding_run_lineage(parent_run_id);
            CREATE TABLE IF NOT EXISTS paper_understanding_n3_checkpoints (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
                origin_checkpoint_id TEXT, origin_run_id TEXT,
                generation_mode TEXT NOT NULL,
                partition_id TEXT NOT NULL, partition_index INTEGER NOT NULL,
                partition_count INTEGER NOT NULL, unit_ids_json TEXT NOT NULL,
                input_fingerprint TEXT NOT NULL,
                document_id TEXT NOT NULL, source_bundle_id TEXT NOT NULL,
                source_snapshot_id TEXT NOT NULL, pdf_sha256 TEXT NOT NULL,
                prompt_version TEXT NOT NULL, schema_version TEXT NOT NULL,
                result_json TEXT NOT NULL, result_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(run_id, partition_id)
            );
            CREATE INDEX IF NOT EXISTS p2a_n3_checkpoint_run
                ON paper_understanding_n3_checkpoints(run_id, partition_index);
            CREATE TABLE IF NOT EXISTS professional_review_runs (
                id TEXT PRIMARY KEY, understanding_run_id TEXT NOT NULL,
                document_id TEXT NOT NULL, source_bundle_id TEXT NOT NULL,
                review_requirements TEXT, status TEXT NOT NULL, error TEXT, result_json TEXT,
                created_at TEXT NOT NULL, finished_at TEXT
            );
            CREATE TABLE IF NOT EXISTS professional_review_dimensions (
                run_id TEXT NOT NULL, dimension_id TEXT NOT NULL, result_json TEXT NOT NULL,
                PRIMARY KEY (run_id, dimension_id)
            );
            CREATE TABLE IF NOT EXISTS professional_review_model_calls (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL, dimension_id TEXT NOT NULL,
                prompt_version TEXT NOT NULL, source_bundle_id TEXT NOT NULL,
                source_ref_ids TEXT NOT NULL, requested_model TEXT NOT NULL,
                actual_model TEXT, input_tokens INTEGER, output_tokens INTEGER,
                duration_ms INTEGER NOT NULL, status TEXT NOT NULL, error TEXT
            );
            CREATE INDEX IF NOT EXISTS p2b_calls_run ON professional_review_model_calls(run_id);
            CREATE TABLE IF NOT EXISTS candidate_consolidation_runs (
                id TEXT PRIMARY KEY, professional_review_run_id TEXT NOT NULL,
                understanding_run_id TEXT NOT NULL, document_id TEXT NOT NULL,
                source_bundle_id TEXT NOT NULL, status TEXT NOT NULL, error TEXT,
                inventory_json TEXT, result_json TEXT,
                created_at TEXT NOT NULL, finished_at TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS p2c_one_active_per_p2b
                ON candidate_consolidation_runs(professional_review_run_id)
                WHERE status IN ('queued', 'running');
            CREATE TABLE IF NOT EXISTS candidate_consolidation_relation_batches (
                run_id TEXT NOT NULL, batch_number INTEGER NOT NULL, relations_json TEXT NOT NULL,
                PRIMARY KEY (run_id, batch_number)
            );
            CREATE TABLE IF NOT EXISTS candidate_consolidation_model_calls (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL, stage TEXT NOT NULL,
                prompt_version TEXT NOT NULL, requested_model TEXT NOT NULL,
                actual_model TEXT, input_tokens INTEGER, output_tokens INTEGER,
                duration_ms INTEGER NOT NULL, status TEXT NOT NULL, error TEXT
            );
            CREATE INDEX IF NOT EXISTS p2c_calls_run ON candidate_consolidation_model_calls(run_id);
            CREATE TABLE IF NOT EXISTS evidence_verification_runs (
                id TEXT PRIMARY KEY, consolidation_run_id TEXT NOT NULL,
                professional_review_run_id TEXT NOT NULL, understanding_run_id TEXT NOT NULL,
                document_id TEXT NOT NULL, source_bundle_id TEXT NOT NULL,
                status TEXT NOT NULL, error TEXT, result_json TEXT,
                created_at TEXT NOT NULL, finished_at TEXT
            );
            CREATE UNIQUE INDEX IF NOT EXISTS p3_one_active_per_p2c
                ON evidence_verification_runs(consolidation_run_id)
                WHERE status IN ('queued', 'running');
            CREATE TABLE IF NOT EXISTS evidence_verification_tasks (
                run_id TEXT NOT NULL, task_id TEXT NOT NULL, status TEXT NOT NULL,
                adjudication_json TEXT NOT NULL, updated_at TEXT NOT NULL,
                PRIMARY KEY (run_id, task_id)
            );
            CREATE TABLE IF NOT EXISTS evidence_verification_model_calls (
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL, stage TEXT NOT NULL,
                prompt_version TEXT NOT NULL, requested_model TEXT NOT NULL,
                actual_model TEXT, input_tokens INTEGER, output_tokens INTEGER,
                duration_ms INTEGER NOT NULL, status TEXT NOT NULL, error TEXT
            );
            CREATE INDEX IF NOT EXISTS p3_calls_run ON evidence_verification_model_calls(run_id);
            CREATE TABLE IF NOT EXISTS evidence_verification_tool_results (
                run_id TEXT NOT NULL, tool_result_id TEXT NOT NULL, task_id TEXT NOT NULL,
                result_json TEXT NOT NULL, PRIMARY KEY (run_id, tool_result_id)
            );
        """)
        initialize_p4_tables(db)
        recover_p4_generation_runs(
            db, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        )
        columns = {row[1] for row in db.execute("PRAGMA table_info(review_findings)")}
        if "support_assessment" not in columns:
            db.execute("ALTER TABLE review_findings ADD COLUMN support_assessment TEXT NOT NULL DEFAULT ''")
        understanding_columns = {row[1] for row in db.execute("PRAGMA table_info(paper_understanding_runs)")}
        if "source_bundle_json" not in understanding_columns:
            db.execute("ALTER TABLE paper_understanding_runs ADD COLUMN source_bundle_json TEXT")
        professional_review_columns = {
            row[1] for row in db.execute("PRAGMA table_info(professional_review_runs)")
        }
        if "review_requirements" not in professional_review_columns:
            db.execute("ALTER TABLE professional_review_runs ADD COLUMN review_requirements TEXT")
        db.execute("UPDATE review_runs SET status = 'failed', error = '服务重启中断，请重新运行分析', "
                   "finished_at = datetime('now') WHERE status IN ('queued', 'running')")
        db.execute("UPDATE finding_verifications SET status = 'failed', error = '服务重启中断，请重新运行复核', "
                   "finished_at = datetime('now') WHERE status IN ('queued', 'running')")
        db.execute("UPDATE paper_understanding_runs SET status = 'failed', error = 'interrupted_by_restart', "
                   "finished_at = datetime('now') WHERE status IN ('queued', 'running')")
        db.execute("UPDATE professional_review_runs SET status = CASE WHEN EXISTS "
                   "(SELECT 1 FROM professional_review_dimensions d WHERE d.run_id = professional_review_runs.id) "
                   "THEN 'partial_failed' ELSE 'failed' END, error = 'interrupted_by_restart', "
                   "finished_at = datetime('now') WHERE status IN ('queued', 'running')")
        db.execute("UPDATE candidate_consolidation_runs SET status = CASE WHEN inventory_json IS NOT NULL "
                   "OR EXISTS (SELECT 1 FROM candidate_consolidation_relation_batches b "
                   "WHERE b.run_id = candidate_consolidation_runs.id) THEN 'partial_failed' "
                   "ELSE 'failed' END, error = 'interrupted_by_restart', "
                   "finished_at = datetime('now') WHERE status IN ('queued', 'running')")
        db.execute("UPDATE evidence_verification_runs SET status = CASE WHEN EXISTS "
                   "(SELECT 1 FROM evidence_verification_tasks t "
                   "WHERE t.run_id = evidence_verification_runs.id) THEN 'partial_failed' "
                   "ELSE 'failed' END, error = 'interrupted_by_restart', "
                   "finished_at = datetime('now') WHERE status IN ('queued', 'running')")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS p2a_one_active_per_document "
                   "ON paper_understanding_runs(document_id) "
                   "WHERE status IN ('queued', 'running')")
        db.execute("CREATE UNIQUE INDEX IF NOT EXISTS p2a_one_continuation_per_parent "
                   "ON paper_understanding_run_lineage(parent_run_id)")

    if c1:
        with connect() as db:
            uncertain_p4 = [row["id"] for row in db.execute(
                "SELECT id FROM p4_generation_runs WHERE cost_complete=0 OR "
                "unknown_usage_calls>0")]
        for run_id in uncertain_p4:
            c1.store.mark_stage_unknown(run_id)
        for stage_row in c1.store.list_stage_states():
            if stage_row["status"] != "interrupted":
                continue
            table = {"p2a": "paper_understanding_runs",
                     "p2b": "professional_review_runs",
                     "p2c": "candidate_consolidation_runs",
                     "p3": "evidence_verification_runs",
                     "p4": "p4_generation_runs"}[stage_row["stage"]]
            column = "state" if stage_row["stage"] == "p4" else "status"
            with connect() as db:
                completed = db.execute(f"SELECT {column} FROM {table} WHERE id=?",
                                       (stage_row["run_id"],)).fetchone()
            if completed and completed[column] in {"completed", "limited"}:
                c1.store.recover_verified_completion(stage_row["run_id"])

    app = FastAPI(title="AI 论文深度审稿工作台")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["GET", "POST", "PUT", "PATCH"],
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

    @app.get("/api/p5c/stage-runs/{run_id}")
    def get_p5c_stage_run(run_id: str):
        if c1 is None:
            raise HTTPException(404, detail={"code": "c1_mode_not_enabled"})
        try:
            result = c1.store.get_run(run_id)
        except C1ControlError:
            raise HTTPException(404, detail={"code": "c1_run_missing"}) from None
        stage_tables = {
            "p2a": "paper_understanding_runs",
            "p2b": "professional_review_runs",
            "p2c": "candidate_consolidation_runs",
            "p3": "evidence_verification_runs",
        }
        if result["stage"] in stage_tables:
            with connect() as db:
                row = db.execute(f"SELECT status FROM {stage_tables[result['stage']]} WHERE id=?",
                                 (run_id,)).fetchone()
            result["production_run_status"] = row["status"] if row else None
        if result["stage"] == "p4":
            with connect() as db:
                row = db.execute("SELECT state, request_count, input_tokens, output_tokens, "
                                 "unknown_usage_calls, planned_cost_usd, usage_cost_usd, "
                                 "cost_complete, provider_billed_cost_usd "
                                 "FROM p4_generation_runs WHERE id=?", (run_id,)).fetchone()
                calls = [dict(item) for item in db.execute(
                    "SELECT b.model_call_id, b.request_index, b.dispatch_state, b.usage_status, "
                    "b.estimated_input_tokens, b.reserved_output_tokens, b.planned_cost_usd, "
                    "c.input_tokens, c.output_tokens, b.actual_total_tokens, b.usage_cost_usd "
                    "FROM p4_model_call_budget b LEFT JOIN p4_model_calls c "
                    "ON c.id=b.model_call_id WHERE b.generation_run_id=? "
                    "ORDER BY b.request_index", (run_id,))]
            if row:
                result["p4_budget_audit"] = dict(row)
                result["model_calls"] = calls
                result["provider_request_count"] = row["request_count"]
                result["known_input_tokens"] = row["input_tokens"]
                result["known_output_tokens"] = row["output_tokens"]
                result["unknown_usage_calls"] = row["unknown_usage_calls"] + sum(
                    call["dispatch_state"] == "dispatch_pending" for call in calls)
                result["cost_complete"] = bool(row["cost_complete"]) and not any(
                    call["dispatch_state"] == "dispatch_pending" for call in calls)
                if not result["cost_complete"]:
                    result["status"] = "unknown_usage"
                result["planned_cost_usd"] = row["planned_cost_usd"]
                result["known_usage_cost_usd"] = row["usage_cost_usd"]
                result["provider_billed_cost_usd"] = row["provider_billed_cost_usd"]
        return result

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
                    "unknown_token_calls": sum(row["input_tokens"] is None or row["output_tokens"] is None
                                               for row in items),
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

    def p2a_budget():
        return RunBudget(
            max_calls=int(os.environ.get("P2A_MAX_CALLS", "32")),
            max_input_tokens=int(os.environ.get("P2A_MAX_INPUT_TOKENS", "300000")),
            max_output_tokens=int(os.environ.get("P2A_MAX_OUTPUT_TOKENS", "100000")),
            max_duration_ms=int(os.environ.get("P2A_MAX_DURATION_MS", "1800000")),
        )

    def streamed_understanding_call(session, model, stage, system, user):
        started = time.perf_counter()
        stream = model_completion(
            session,
            model=model,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
            stream=True,
            stream_options={"include_usage": True},
        )
        pieces = []
        actual_model = None
        usage = None
        for chunk in stream:
            actual_model = chunk.model or actual_model
            usage = chunk.usage or usage
            if chunk.choices and chunk.choices[0].delta.content:
                pieces.append(chunk.choices[0].delta.content)
        return {
            "content": "".join(pieces),
            "model_identifier": actual_model,
            "input_tokens": getattr(usage, "prompt_tokens", None),
            "output_tokens": getattr(usage, "completion_tokens", None),
            "duration_ms": round((time.perf_counter() - started) * 1000),
        }

    def persist_understanding_trace(run_id, trace):
        with connect() as db:
            db.execute(
                "INSERT INTO paper_understanding_model_calls VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (uuid.uuid4().hex, run_id, trace.stage, trace.prompt_version,
                 trace.requested_model, trace.actual_model, trace.input_tokens,
                 trace.output_tokens, trace.duration_ms, trace.status, trace.error),
            )

    def checkpoint_from_row(row):
        return StoredN3Checkpoint(
            checkpoint_id=row["id"],
            generating_run_id=row["run_id"],
            origin_checkpoint_id=row["origin_checkpoint_id"],
            origin_run_id=row["origin_run_id"],
            generation_mode=row["generation_mode"],
            partition_id=row["partition_id"],
            partition_index=row["partition_index"],
            partition_count=row["partition_count"],
            unit_ids=tuple(json.loads(row["unit_ids_json"])),
            input_fingerprint=row["input_fingerprint"],
            prompt_version=row["prompt_version"],
            schema_version=row["schema_version"],
            result_hash=row["result_hash"],
            result=ClaimEvidenceMap.model_validate_json(row["result_json"]),
        )

    def persist_n3_checkpoint(run_id, bundle, checkpoint, connection=None):
        if checkpoint.generating_run_id != run_id:
            raise ValueError("checkpoint run mismatch")
        if checkpoint.result_hash != checkpoint_result_hash(checkpoint.result):
            raise ValueError("checkpoint result hash mismatch")
        if (checkpoint.result.document_id != bundle["document"]["id"] or
                checkpoint.result.source_bundle_id != bundle["bundle_id"]):
            raise ValueError("checkpoint source mismatch")
        validate_source_references(checkpoint.result, bundle)
        values = (
            checkpoint.checkpoint_id, run_id, checkpoint.origin_checkpoint_id,
            checkpoint.origin_run_id, checkpoint.generation_mode,
            checkpoint.partition_id, checkpoint.partition_index,
            checkpoint.partition_count, json.dumps(checkpoint.unit_ids),
            checkpoint.input_fingerprint, bundle["document"]["id"],
            bundle["bundle_id"], bundle["document"]["snapshot_id"],
            bundle["document"].get("sha256", ""), checkpoint.prompt_version,
            checkpoint.schema_version, checkpoint.result.model_dump_json(),
            checkpoint.result_hash,
        )
        def write(db):
            existing = db.execute(
                "SELECT * FROM paper_understanding_n3_checkpoints "
                "WHERE run_id = ? AND partition_id = ?",
                (run_id, checkpoint.partition_id),
            ).fetchone()
            if existing:
                current = checkpoint_from_row(existing)
                if (current.checkpoint_id != checkpoint.checkpoint_id or
                        current.input_fingerprint != checkpoint.input_fingerprint or
                        current.result_hash != checkpoint.result_hash or
                        current.result.model_dump(mode="json") !=
                        checkpoint.result.model_dump(mode="json")):
                    raise ValueError("checkpoint duplicate mismatch")
                return
            db.execute(
                "INSERT INTO paper_understanding_n3_checkpoints "
                "(id, run_id, origin_checkpoint_id, origin_run_id, generation_mode, "
                "partition_id, partition_index, partition_count, unit_ids_json, "
                "input_fingerprint, document_id, source_bundle_id, source_snapshot_id, "
                "pdf_sha256, prompt_version, schema_version, result_json, result_hash, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
                values,
            )
        if connection is not None:
            write(connection)
        else:
            with connect() as db:
                write(db)

    def validate_understanding_lineage_chain(db, run_id, frozen_bundle, document):
        if frozen_bundle["document"]["id"] != document["id"]:
            raise ValueError("understanding lineage document mismatch")
        chain = []
        seen = set()
        current_run_id = run_id
        while True:
            if current_run_id in seen:
                raise ValueError("understanding lineage cycle")
            seen.add(current_run_id)
            row = db.execute(
                "SELECT * FROM paper_understanding_runs WHERE id = ?", (current_run_id,)
            ).fetchone()
            if not row or not row["source_bundle_json"] or not row["review_plan"] or not row["paper_profile"]:
                raise ValueError("understanding lineage run missing")
            run_bundle = json.loads(row["source_bundle_json"])
            if (run_bundle != frozen_bundle or row["document_id"] != document["id"] or
                    row["source_bundle_id"] != frozen_bundle["bundle_id"] or
                    run_bundle["document"]["snapshot_id"] != document["snapshot_id"] or
                    run_bundle["document"].get("sha256") != document["sha256"]):
                raise ValueError("understanding lineage source mismatch")
            plan = ReviewPlan.model_validate_json(row["review_plan"])
            profile = PaperProfile.model_validate_json(row["paper_profile"])
            validate_inherited_understanding_assets(
                frozen_bundle, current_run_id, plan, profile)
            lineage = db.execute(
                "SELECT * FROM paper_understanding_run_lineage WHERE run_id = ?",
                (current_run_id,),
            ).fetchone()
            if lineage and (
                lineage["document_id"] != document["id"]
                or lineage["source_bundle_id"] != frozen_bundle["bundle_id"]
                or lineage["source_snapshot_id"] != document["snapshot_id"]
                or lineage["pdf_sha256"] != document["sha256"]
                or lineage["plan_prompt_version"] != plan.prompt_version
                or lineage["plan_schema_version"] != plan.schema_version
                or lineage["profile_prompt_version"] != profile.prompt_version
                or lineage["profile_schema_version"] != profile.schema_version
                or lineage["n3_prompt_version"] != VERSIONS["contribution-evidence-mapper"]
                or lineage["n3_schema_version"] != N3_SCHEMA_VERSION
            ):
                raise ValueError("understanding lineage metadata mismatch")
            chain.append((current_run_id, plan, profile, lineage))
            if not lineage:
                break
            current_run_id = lineage["parent_run_id"]

        chain_index = {item[0]: item for item in chain}
        for index, (current_id, plan, profile, lineage) in enumerate(chain):
            if not lineage:
                continue
            ancestors = {item[0] for item in chain[index + 1:]}
            plan_source_id = lineage["plan_source_run_id"]
            profile_source_id = lineage["profile_source_run_id"]
            if plan_source_id not in ancestors or profile_source_id not in ancestors:
                raise ValueError("understanding lineage source is not an ancestor")
            source_plan = chain_index[plan_source_id][1].model_copy(
                update={"review_run_id": current_id}, deep=True)
            source_profile = chain_index[profile_source_id][2].model_copy(
                update={"review_run_id": current_id}, deep=True)
            if (source_plan.model_dump(mode="json") != plan.model_dump(mode="json") or
                    source_profile.model_dump(mode="json") != profile.model_dump(mode="json")):
                raise ValueError("inherited understanding content mismatch")
        return chain[0][3]

    def checkpoint_row_matches_bundle(row, bundle):
        return (
            row["document_id"] == bundle["document"]["id"]
            and row["source_bundle_id"] == bundle["bundle_id"]
            and row["source_snapshot_id"] == bundle["document"]["snapshot_id"]
            and row["pdf_sha256"] == bundle["document"].get("sha256", "")
        )

    def validate_checkpoint_chain(db, row, descriptor, bundle):
        seen = set()
        current_row = row
        while True:
            checkpoint_id = current_row["id"]
            if checkpoint_id in seen:
                raise ValueError("checkpoint origin cycle")
            seen.add(checkpoint_id)
            if not checkpoint_row_matches_bundle(current_row, bundle):
                raise ValueError("checkpoint frozen source mismatch")
            current = checkpoint_from_row(current_row)
            validate_checkpoint(
                current, descriptor, bundle, current.generating_run_id)
            if current.generation_mode == "generated":
                return checkpoint_from_row(row)
            lineage = db.execute(
                "SELECT parent_run_id FROM paper_understanding_run_lineage WHERE run_id = ?",
                (current.generating_run_id,),
            ).fetchone()
            if not lineage or lineage["parent_run_id"] != current.origin_run_id:
                raise ValueError("checkpoint origin run is not the lineage parent")
            origin_row = db.execute(
                "SELECT * FROM paper_understanding_n3_checkpoints WHERE id = ? AND run_id = ?",
                (current.origin_checkpoint_id, current.origin_run_id),
            ).fetchone()
            if not origin_row or origin_row["input_fingerprint"] != current.input_fingerprint:
                raise ValueError("checkpoint origin mismatch")
            origin = checkpoint_from_row(origin_row)
            validate_checkpoint(origin, descriptor, bundle, origin.generating_run_id)
            if not checkpoint_content_rebinds_exactly(origin, current):
                raise ValueError("checkpoint inherited content mismatch")
            current_row = origin_row

    def load_run_checkpoints(db, run_id, bundle, descriptor_index):
        rows = db.execute(
            "SELECT * FROM paper_understanding_n3_checkpoints "
            "WHERE run_id = ? ORDER BY partition_index", (run_id,)
        ).fetchall()
        checkpoints = []
        for row in rows:
            descriptor = descriptor_index.get(row["partition_id"])
            if descriptor is None:
                raise ValueError("checkpoint partition mismatch")
            checkpoints.append(validate_checkpoint_chain(db, row, descriptor, bundle))
        return checkpoints

    def run_understanding(document_id: str, run_id: str, request: UnderstandingRunRequest):
        try:
            with connect() as db:
                db.execute("UPDATE paper_understanding_runs SET status = 'running' WHERE id = ?", (run_id,))
            bundle = source_bundle_for_document(document_id)
            with connect() as db:
                db.execute("UPDATE paper_understanding_runs SET source_bundle_id = ?, source_bundle_json = ? "
                           "WHERE id = ?", (bundle["bundle_id"], json.dumps(bundle), run_id))
            session = model_session(timeout=180, max_retries=0, c1_run_id=run_id if c1 else None)
            model = session.configuration.model

            def model_call(stage, system, user):
                return streamed_understanding_call(session, model, stage, system, user)

            def persist_result(column, result):
                if column not in {"review_plan", "paper_profile", "claim_evidence_map"}:
                    raise ValueError("unknown understanding stage")
                with connect() as db:
                    db.execute(f"UPDATE paper_understanding_runs SET {column} = ? WHERE id = ?",
                               (result.model_dump_json(), run_id))

            run_p2a(
                bundle, run_id, model_call, model, request.target_journal,
                request.special_requirements, on_result=persist_result,
                on_trace=lambda trace: persist_understanding_trace(run_id, trace),
                on_n3_checkpoint=lambda checkpoint: persist_n3_checkpoint(
                    run_id, bundle, checkpoint),
                budget=c1.p2_budget(run_id) if c1 else p2a_budget())
            with connect() as db:
                db.execute("UPDATE paper_understanding_runs SET status = 'completed', "
                           "finished_at = datetime('now') WHERE id = ?", (run_id,))
        except BudgetExceeded as exc:
            with connect() as db:
                db.execute("UPDATE paper_understanding_runs SET status = 'budget_exceeded', error = ?, "
                           "finished_at = datetime('now') WHERE id = ?",
                           (f"{exc.stage}:budget_exceeded" if c1 else str(exc), run_id))
        except P2AError as exc:
            with connect() as db:
                db.execute("UPDATE paper_understanding_runs SET status = 'failed', error = ?, "
                           "finished_at = datetime('now') WHERE id = ?",
                           (f"{exc.stage}:{type(exc).__name__}" if c1 else str(exc), run_id))
        except Exception as exc:
            with connect() as db:
                db.execute("UPDATE paper_understanding_runs SET status = 'failed', error = ?, "
                           "finished_at = datetime('now') WHERE id = ?",
                           (f"source: {type(exc).__name__}", run_id))
        finally:
            if c1:
                with connect() as db:
                    state = db.execute("SELECT status FROM paper_understanding_runs WHERE id=?",
                                       (run_id,)).fetchone()
                c1.store.finish_stage(run_id, completed=bool(state and state["status"] == "completed"))

    def run_n3_continuation(document_id: str, run_id: str):
        try:
            with connect() as db:
                row = db.execute(
                    "SELECT * FROM paper_understanding_runs WHERE id = ?", (run_id,)
                ).fetchone()
                lineage = db.execute(
                    "SELECT * FROM paper_understanding_run_lineage WHERE run_id = ?", (run_id,)
                ).fetchone()
                document = db.execute(
                    "SELECT id, snapshot_id, sha256 FROM documents WHERE id = ?", (document_id,)
                ).fetchone()
                if not row or not lineage or not document:
                    raise ValueError("continuation state missing")
                bundle = json.loads(row["source_bundle_json"])
                if (row["status"] != "queued" or row["source_bundle_id"] != bundle["bundle_id"] or
                        lineage["document_id"] != document["id"] or
                        lineage["source_bundle_id"] != bundle["bundle_id"] or
                        lineage["source_snapshot_id"] != document["snapshot_id"] or
                        lineage["pdf_sha256"] != document["sha256"] or
                        bundle["document"]["snapshot_id"] != document["snapshot_id"] or
                        bundle["document"].get("sha256") != document["sha256"]):
                    raise ValueError("continuation frozen source mismatch")
                plan = ReviewPlan.model_validate_json(row["review_plan"])
                profile = PaperProfile.model_validate_json(row["paper_profile"])
                validate_inherited_understanding_assets(bundle, run_id, plan, profile)
                validate_understanding_lineage_chain(db, run_id, bundle, document)
                descriptors = build_n3_partition_descriptors(
                    bundle, profile, row["target_journal"], row["special_requirements"])
                by_partition = {item.partition_id: item for item in descriptors}
                checkpoints = load_run_checkpoints(
                    db, run_id, bundle, by_partition)
                db.execute(
                    "UPDATE paper_understanding_runs SET status = 'running' WHERE id = ?",
                    (run_id,),
                )

            session = model_session(timeout=180, max_retries=0, c1_run_id=run_id if c1 else None)
            model = session.configuration.model
            checkpoint_index = {item.input_fingerprint: item for item in checkpoints}

            mapping, _ = run_p2a_n3(
                bundle,
                profile,
                run_id,
                lambda stage, system, user: streamed_understanding_call(
                    session, model, stage, system, user),
                model,
                row["target_journal"],
                row["special_requirements"],
                budget=p2a_budget(),
                checkpoint_loader=lambda descriptor: checkpoint_index.get(
                    descriptor.input_fingerprint),
                on_checkpoint=lambda checkpoint: persist_n3_checkpoint(
                    run_id, bundle, checkpoint),
                on_trace=lambda trace: persist_understanding_trace(run_id, trace),
            )
            validate_understanding_snapshot(
                bundle, plan, profile, mapping, run_id, "completed")
            with connect() as db:
                db.execute(
                    "UPDATE paper_understanding_runs SET claim_evidence_map = ?, status = 'completed', "
                    "error = NULL, finished_at = datetime('now') WHERE id = ?",
                    (mapping.model_dump_json(), run_id),
                )
        except BudgetExceeded as exc:
            with connect() as db:
                db.execute(
                    "UPDATE paper_understanding_runs SET status = 'budget_exceeded', error = ?, "
                    "finished_at = datetime('now') WHERE id = ?", (str(exc), run_id))
        except P2AError as exc:
            with connect() as db:
                db.execute(
                    "UPDATE paper_understanding_runs SET status = 'failed', error = ?, "
                    "finished_at = datetime('now') WHERE id = ?", (str(exc), run_id))
        except Exception as exc:
            with connect() as db:
                db.execute(
                    "UPDATE paper_understanding_runs SET status = 'failed', error = ?, "
                    "finished_at = datetime('now') WHERE id = ?",
                    (f"resume: {type(exc).__name__}", run_id),
                )

    @app.post("/api/documents/{document_id}/understanding-runs", status_code=202)
    def create_understanding_run(document_id: str, payload: UnderstandingRunRequest,
                                 background_tasks: BackgroundTasks, http_request: Request):
        model_configuration_or_503()
        c1_claimed = c1_claim(http_request, "p2a", document_id)
        if c1_claimed and not c1_claimed[1]:
            return {"id": c1_claimed[0], "document_id": document_id,
                    "status": c1.store.get_run(c1_claimed[0])["status"]}
        try:
            with connect() as db:
                db.execute("BEGIN IMMEDIATE")
                document_or_404(db, document_id)
                active = db.execute("SELECT 1 FROM paper_understanding_runs WHERE document_id = ? "
                                    "AND status IN ('queued', 'running')", (document_id,)).fetchone()
                if active:
                    raise HTTPException(409, "该论文已有正在运行的理解任务")
                run_id = c1_claimed[0] if c1_claimed else uuid.uuid4().hex
                db.execute("INSERT INTO paper_understanding_runs "
                           "(id, document_id, status, target_journal, special_requirements, created_at) "
                           "VALUES (?, ?, 'queued', ?, ?, datetime('now'))",
                           (run_id, document_id, payload.target_journal, payload.special_requirements))
        except HTTPException:
            raise
        except sqlite3.IntegrityError:
            raise HTTPException(409, "该论文已有正在运行的理解任务") from None
        background_tasks.add_task(run_understanding, document_id, run_id, payload)
        return {"id": run_id, "document_id": document_id, "status": "queued"}

    @app.post("/api/understanding-runs/{parent_run_id}/n3-resume-runs", status_code=202)
    def create_n3_resume_run(parent_run_id: str, background_tasks: BackgroundTasks):
        if c1:
            raise HTTPException(409, detail={"code": "c1_resume_not_authorized"})
        model_configuration_or_503()
        try:
            with connect() as db:
                db.execute("BEGIN IMMEDIATE")
                parent = db.execute(
                    "SELECT * FROM paper_understanding_runs WHERE id = ?", (parent_run_id,)
                ).fetchone()
                if not parent:
                    raise HTTPException(404, "论文理解任务不存在")
                if (parent["status"] not in {"failed", "budget_exceeded"} or
                        not parent["source_bundle_json"] or not parent["review_plan"] or
                        not parent["paper_profile"] or parent["claim_evidence_map"]):
                    raise HTTPException(409, "父任务不满足N3续跑条件")
                document = document_or_404(db, parent["document_id"])
                if db.execute(
                    "SELECT 1 FROM paper_understanding_runs WHERE document_id = ? "
                    "AND status IN ('queued', 'running')", (parent["document_id"],)
                ).fetchone():
                    raise HTTPException(409, "该论文已有正在运行的理解任务")

                bundle = json.loads(parent["source_bundle_json"])
                if (parent["source_bundle_id"] != bundle["bundle_id"] or
                        bundle["document"]["id"] != document["id"] or
                        bundle["document"]["snapshot_id"] != document["snapshot_id"] or
                        bundle["document"].get("sha256") != document["sha256"]):
                    raise ValueError("parent frozen source mismatch")
                source_plan = ReviewPlan.model_validate_json(parent["review_plan"])
                source_profile = PaperProfile.model_validate_json(parent["paper_profile"])
                validate_inherited_understanding_assets(
                    bundle, parent_run_id, source_plan, source_profile)
                parent_lineage = validate_understanding_lineage_chain(
                    db, parent_run_id, bundle, document)

                run_id = uuid.uuid4().hex
                plan = source_plan.model_copy(update={"review_run_id": run_id}, deep=True)
                profile = source_profile.model_copy(update={"review_run_id": run_id}, deep=True)
                validate_inherited_understanding_assets(bundle, run_id, plan, profile)
                descriptors = build_n3_partition_descriptors(
                    bundle, profile, parent["target_journal"], parent["special_requirements"])
                descriptor_index = {item.partition_id: item for item in descriptors}
                inherited = []
                for checkpoint in load_run_checkpoints(
                        db, parent_run_id, bundle, descriptor_index):
                    descriptor = descriptor_index[checkpoint.partition_id]
                    inherited.append(inherit_n3_checkpoint(
                        checkpoint, descriptor, bundle, run_id))

                db.execute(
                    "INSERT INTO paper_understanding_runs "
                    "(id, document_id, source_bundle_id, source_bundle_json, status, error, "
                    "target_journal, special_requirements, review_plan, paper_profile, "
                    "claim_evidence_map, created_at, finished_at) "
                    "VALUES (?, ?, ?, ?, 'queued', NULL, ?, ?, ?, ?, NULL, datetime('now'), NULL)",
                    (run_id, parent["document_id"], bundle["bundle_id"],
                     parent["source_bundle_json"], parent["target_journal"],
                     parent["special_requirements"], plan.model_dump_json(),
                     profile.model_dump_json()),
                )
                db.execute(
                    "INSERT INTO paper_understanding_run_lineage VALUES "
                    "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
                    (run_id, parent_run_id,
                     parent_lineage["plan_source_run_id"] if parent_lineage else parent_run_id,
                     parent_lineage["profile_source_run_id"] if parent_lineage else parent_run_id,
                     parent["document_id"], bundle["bundle_id"],
                     bundle["document"]["snapshot_id"], document["sha256"],
                     source_plan.prompt_version, source_plan.schema_version,
                     source_profile.prompt_version, source_profile.schema_version,
                     VERSIONS["contribution-evidence-mapper"], N3_SCHEMA_VERSION),
                )
                for checkpoint in inherited:
                    persist_n3_checkpoint(run_id, bundle, checkpoint, connection=db)
        except HTTPException:
            raise
        except (KeyError, TypeError, ValueError, sqlite3.DatabaseError):
            raise HTTPException(409, "父任务续跑资产无效") from None
        background_tasks.add_task(run_n3_continuation, parent["document_id"], run_id)
        return {"id": run_id, "document_id": parent["document_id"],
                "parent_run_id": parent_run_id, "status": "queued"}

    @app.get("/api/understanding-runs/{run_id}")
    def get_understanding_run(run_id: str):
        with connect() as db:
            row = db.execute("SELECT * FROM paper_understanding_runs WHERE id = ?", (run_id,)).fetchone()
            if not row:
                raise HTTPException(404, "论文理解任务不存在")
            calls = [dict(item) for item in db.execute(
                "SELECT * FROM paper_understanding_model_calls WHERE run_id = ? ORDER BY rowid", (run_id,))]
            lineage_row = db.execute(
                "SELECT * FROM paper_understanding_run_lineage WHERE run_id = ?", (run_id,)
            ).fetchone()
            checkpoint_rows = db.execute(
                "SELECT id, origin_checkpoint_id, origin_run_id, generation_mode, "
                "partition_id, partition_index, partition_count, input_fingerprint, "
                "prompt_version, schema_version, result_hash, created_at "
                "FROM paper_understanding_n3_checkpoints WHERE run_id = ? "
                "ORDER BY partition_index", (run_id,)
            ).fetchall()
        result = dict(row)
        result.pop("source_bundle_json")
        for key in ("review_plan", "paper_profile", "claim_evidence_map"):
            result[key] = json.loads(result[key]) if result[key] else None
        result["model_calls"] = calls
        result["lineage"] = dict(lineage_row) if lineage_row else None
        result["n3_checkpoints"] = [dict(item) for item in checkpoint_rows]
        return result

    @app.get("/api/understanding-runs/{run_id}/source-bundle")
    def get_understanding_source_bundle(run_id: str):
        with connect() as db:
            row = db.execute("SELECT source_bundle_json FROM paper_understanding_runs WHERE id = ?", (run_id,)).fetchone()
        if not row or not row["source_bundle_json"]:
            raise HTTPException(404, "该任务尚无 Source Bundle 快照")
        return json.loads(row["source_bundle_json"])

    def load_professional_inputs(understanding_run_id):
        with connect() as db:
            row = db.execute("SELECT * FROM paper_understanding_runs WHERE id = ?",
                             (understanding_run_id,)).fetchone()
            if not row:
                raise HTTPException(404, "论文理解任务不存在")
            if row["status"] != "completed" or not all(row[key] for key in
                    ("source_bundle_json", "review_plan", "paper_profile", "claim_evidence_map")):
                raise HTTPException(409, "论文理解任务尚未完整完成")
            document = db.execute("SELECT id, snapshot_id, sha256 FROM documents WHERE id = ?",
                                  (row["document_id"],)).fetchone()
        try:
            bundle = json.loads(row["source_bundle_json"])
            if row["source_bundle_id"] != bundle["bundle_id"] or not document or \
                    bundle["document"]["id"] != document["id"] or \
                    bundle["document"]["snapshot_id"] != document["snapshot_id"] or \
                    (bundle["document"].get("sha256") and
                     bundle["document"]["sha256"] != document["sha256"]):
                raise ValueError("frozen snapshot metadata mismatch")
            plan = ReviewPlan.model_validate_json(row["review_plan"])
            profile = PaperProfile.model_validate_json(row["paper_profile"])
            mapping = ClaimEvidenceMap.model_validate_json(row["claim_evidence_map"])
            validate_understanding_snapshot(bundle, plan, profile, mapping,
                                            understanding_run_id, row["status"])
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(409, f"论文理解快照无效：{type(exc).__name__}") from None
        return row, bundle, plan, profile, mapping

    def run_professional_review(understanding_run_id, run_id):
        try:
            row, bundle, plan, profile, mapping = load_professional_inputs(understanding_run_id)
            with connect() as db:
                review_run = db.execute(
                    "SELECT review_requirements FROM professional_review_runs WHERE id = ?",
                    (run_id,),
                ).fetchone()
                if not review_run:
                    raise RuntimeError("professional review run missing")
                db.execute("UPDATE professional_review_runs SET status = 'running' WHERE id = ?", (run_id,))
            session = model_session(timeout=300, max_retries=0, c1_run_id=run_id if c1 else None)
            model = session.configuration.model

            def model_call(stage, system, user):
                started = time.perf_counter()
                stream = model_completion(session,
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
                return {"content": "".join(pieces), "model_identifier": actual_model,
                        "input_tokens": getattr(usage, "prompt_tokens", None),
                        "output_tokens": getattr(usage, "completion_tokens", None),
                        "duration_ms": round((time.perf_counter() - started) * 1000)}

            def persist_dimension(dimension):
                with connect() as db:
                    db.execute("INSERT OR REPLACE INTO professional_review_dimensions VALUES (?, ?, ?)",
                               (run_id, dimension.dimension_id, dimension.model_dump_json()))

            def persist_trace(trace):
                with connect() as db:
                    db.execute("INSERT INTO professional_review_model_calls VALUES "
                               "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                               (uuid.uuid4().hex, run_id, trace.dimension_id, trace.prompt_version,
                                trace.source_bundle_id, json.dumps(trace.source_ref_ids),
                                trace.requested_model, trace.actual_model,
                                trace.input_tokens, trace.output_tokens, trace.duration_ms,
                                trace.status, trace.error))

            result = run_p2b(bundle, plan, profile, mapping, understanding_run_id, run_id,
                model_call, model, row["target_journal"], review_run["review_requirements"],
                budget=c1.p2_budget(run_id) if c1 else RunBudget(
                    max_calls=int(os.environ.get("P2B_MAX_CALLS", "18")),
                    max_input_tokens=int(os.environ.get("P2B_MAX_INPUT_TOKENS", "180000")),
                    max_output_tokens=int(os.environ.get("P2B_MAX_OUTPUT_TOKENS", "50000")),
                    max_duration_ms=int(os.environ.get("P2B_MAX_DURATION_MS", "1800000"))),
                on_dimension=persist_dimension, on_trace=persist_trace)
            with connect() as db:
                db.execute("UPDATE professional_review_runs SET status = ?, result_json = ?, "
                           "finished_at = datetime('now') WHERE id = ?",
                           (result.execution_status, result.model_dump_json(), run_id))
        except Exception as exc:
            with connect() as db:
                completed = db.execute("SELECT count(*) FROM professional_review_dimensions "
                                       "WHERE run_id = ?", (run_id,)).fetchone()[0]
                db.execute("UPDATE professional_review_runs SET status = ?, error = ?, "
                           "finished_at = datetime('now') WHERE id = ?",
                           ("partial_failed" if completed else "failed",
                            f"professional_review:{type(exc).__name__}", run_id))
        finally:
            if c1:
                with connect() as db:
                    state = db.execute("SELECT status FROM professional_review_runs WHERE id=?",
                                       (run_id,)).fetchone()
                c1.store.finish_stage(run_id, completed=bool(
                    state and state["status"] in {"completed", "limited"}))

    @app.post("/api/understanding-runs/{run_id}/professional-review-runs", status_code=202)
    def create_professional_review_run(
        run_id: str,
        background_tasks: BackgroundTasks,
        http_request: Request,
        request: ProfessionalReviewRunRequest | None = None,
    ):
        model_configuration_or_503()
        row, bundle, _, _, _ = load_professional_inputs(run_id)
        c1_claimed = c1_claim(http_request, "p2b", run_id)
        if c1_claimed and not c1_claimed[1]:
            return {"id": c1_claimed[0], "understanding_run_id": run_id,
                    "status": c1.store.get_run(c1_claimed[0])["status"]}
        new_id = c1_claimed[0] if c1_claimed else uuid.uuid4().hex
        review_requirements = request.review_requirements if request else None
        with connect() as db:
            active = db.execute("SELECT 1 FROM professional_review_runs WHERE understanding_run_id = ? "
                                "AND status IN ('queued', 'running')", (run_id,)).fetchone()
            if active:
                raise HTTPException(409, "该论文已有正在运行的专业审查任务")
            db.execute("INSERT INTO professional_review_runs "
                       "(id, understanding_run_id, document_id, source_bundle_id, "
                       "review_requirements, status, created_at) "
                       "VALUES (?, ?, ?, ?, ?, 'queued', datetime('now'))",
                       (new_id, run_id, row["document_id"], bundle["bundle_id"],
                        review_requirements))
        background_tasks.add_task(run_professional_review, run_id, new_id)
        return {"id": new_id, "understanding_run_id": run_id, "status": "queued"}

    @app.get("/api/professional-review-runs/{run_id}")
    def get_professional_review_run(run_id: str):
        with connect() as db:
            row = db.execute("SELECT * FROM professional_review_runs WHERE id = ?", (run_id,)).fetchone()
            if not row:
                raise HTTPException(404, "专业审查任务不存在")
            dimensions = [json.loads(item["result_json"]) for item in db.execute(
                "SELECT result_json FROM professional_review_dimensions WHERE run_id = ? "
                "ORDER BY dimension_id", (run_id,))]
            calls = [dict(item) for item in db.execute(
                "SELECT * FROM professional_review_model_calls WHERE run_id = ? ORDER BY rowid", (run_id,))]
        for call in calls:
            call["source_ref_ids"] = json.loads(call["source_ref_ids"])
        result = dict(row)
        result_json = result.pop("result_json")
        result["result"] = json.loads(result_json) if result_json else None
        result["dimensions"] = dimensions
        result["candidates"] = [candidate for dimension in dimensions
                                for candidate in dimension["candidates"]]
        result["model_calls"] = calls
        return result

    def load_consolidation_inputs(professional_review_run_id):
        with connect() as db:
            row = db.execute("SELECT * FROM professional_review_runs WHERE id = ?",
                             (professional_review_run_id,)).fetchone()
        if not row:
            raise HTTPException(404, "专业审查任务不存在")
        if row["status"] not in {"completed", "limited"} or not row["result_json"]:
            raise HTTPException(409, "专业审查任务不是完整可整理输入")
        understanding, bundle, plan, profile, mapping = load_professional_inputs(
            row["understanding_run_id"])
        try:
            review = ProfessionalReviewResult.model_validate_json(row["result_json"])
            if row["document_id"] != understanding["document_id"] or \
                    row["source_bundle_id"] != bundle["bundle_id"]:
                raise ValueError("P2-B run source metadata mismatch")
            validate_consolidation_inputs(bundle, plan, profile, mapping, review,
                row["understanding_run_id"], professional_review_run_id, row["status"])
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(409, f"专业审查快照无效：{type(exc).__name__}") from None
        return row, bundle, plan, profile, mapping, review

    def run_candidate_consolidation(professional_review_run_id, run_id):
        try:
            source, bundle, plan, profile, mapping, review = load_consolidation_inputs(
                professional_review_run_id)
            with connect() as db:
                db.execute("UPDATE candidate_consolidation_runs SET status = 'running' WHERE id = ?", (run_id,))
            session = model_session(timeout=180, max_retries=0, c1_run_id=run_id if c1 else None)
            model = session.configuration.model

            def model_call(stage, system, user):
                started = time.perf_counter()
                stream = model_completion(session,
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
                return {"content": "".join(pieces), "model_identifier": actual_model,
                        "input_tokens": getattr(usage, "prompt_tokens", None),
                        "output_tokens": getattr(usage, "completion_tokens", None),
                        "duration_ms": round((time.perf_counter() - started) * 1000)}

            def persist_inventory(inventory):
                with connect() as db:
                    db.execute("UPDATE candidate_consolidation_runs SET inventory_json = ? WHERE id = ?",
                               (inventory.model_dump_json(), run_id))

            def persist_batch(batch_number, relations):
                with connect() as db:
                    db.execute("INSERT OR REPLACE INTO candidate_consolidation_relation_batches "
                               "VALUES (?, ?, ?)",
                               (run_id, batch_number, json.dumps([item.model_dump() for item in relations])))

            def persist_trace(trace):
                with connect() as db:
                    db.execute("INSERT INTO candidate_consolidation_model_calls VALUES "
                               "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                               (uuid.uuid4().hex, run_id, trace.stage, trace.prompt_version,
                                trace.requested_model, trace.actual_model,
                                trace.input_tokens, trace.output_tokens, trace.duration_ms,
                                trace.status, trace.error))

            result = run_p2c(bundle, plan, profile, mapping, review,
                source["understanding_run_id"], professional_review_run_id,
                run_id, model_call, model, p2b_status=source["status"],
                budget=c1.p2_budget(run_id) if c1 else RunBudget(
                    max_calls=int(os.environ.get("P2C_MAX_CALLS", "16")),
                    max_input_tokens=int(os.environ.get("P2C_MAX_INPUT_TOKENS", "200000")),
                    max_output_tokens=int(os.environ.get("P2C_MAX_OUTPUT_TOKENS", "50000")),
                    max_duration_ms=int(os.environ.get("P2C_MAX_DURATION_MS", "1800000"))),
                on_inventory=persist_inventory, on_batch=persist_batch, on_trace=persist_trace)
            with connect() as db:
                db.execute("UPDATE candidate_consolidation_runs SET status = ?, result_json = ?, "
                           "finished_at = datetime('now') WHERE id = ?",
                           (result.execution_status, result.model_dump_json(), run_id))
        except Exception as exc:
            with connect() as db:
                row = db.execute("SELECT inventory_json FROM candidate_consolidation_runs WHERE id = ?",
                                 (run_id,)).fetchone()
                db.execute("UPDATE candidate_consolidation_runs SET status = ?, error = ?, "
                           "finished_at = datetime('now') WHERE id = ?",
                           ("partial_failed" if row and row["inventory_json"] else "failed",
                            f"consolidation:{type(exc).__name__}", run_id))
        finally:
            if c1:
                with connect() as db:
                    state = db.execute("SELECT status FROM candidate_consolidation_runs WHERE id=?",
                                       (run_id,)).fetchone()
                c1.store.finish_stage(run_id, completed=bool(
                    state and state["status"] in {"completed", "limited"}))

    @app.post("/api/professional-review-runs/{run_id}/consolidation-runs", status_code=202)
    def create_consolidation_run(run_id: str, background_tasks: BackgroundTasks,
                                 http_request: Request,
                                 payload: ConsolidationRunRequest | None = None):
        model_configuration_or_503()
        source, bundle, _, _, _, _ = load_consolidation_inputs(run_id)
        c1_claimed = c1_claim(http_request, "p2c", run_id)
        if c1_claimed and not c1_claimed[1]:
            return {"id": c1_claimed[0], "professional_review_run_id": run_id,
                    "status": c1.store.get_run(c1_claimed[0])["status"]}
        new_id = c1_claimed[0] if c1_claimed else uuid.uuid4().hex
        try:
            with connect() as db:
                existing = db.execute("SELECT id, status FROM candidate_consolidation_runs "
                                      "WHERE professional_review_run_id = ? ORDER BY rowid DESC LIMIT 1",
                                      (run_id,)).fetchone()
                if existing and (existing["status"] in {"queued", "running"} or
                                 not payload or not payload.retry):
                    raise HTTPException(409, "该专业审查任务已有整合运行；请先查看现有结果")
                db.execute("INSERT INTO candidate_consolidation_runs "
                           "(id, professional_review_run_id, understanding_run_id, document_id, "
                           "source_bundle_id, status, created_at) "
                           "VALUES (?, ?, ?, ?, ?, 'queued', datetime('now'))",
                           (new_id, run_id, source["understanding_run_id"],
                            source["document_id"], bundle["bundle_id"]))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "该专业审查任务已有整合运行") from None
        background_tasks.add_task(run_candidate_consolidation, run_id, new_id)
        return {"id": new_id, "professional_review_run_id": run_id, "status": "queued"}

    @app.get("/api/candidate-consolidation-runs/{run_id}")
    def get_consolidation_run(run_id: str):
        with connect() as db:
            row = db.execute("SELECT * FROM candidate_consolidation_runs WHERE id = ?", (run_id,)).fetchone()
            if not row:
                raise HTTPException(404, "候选问题整合任务不存在")
            batches = [dict(item) for item in db.execute(
                "SELECT batch_number, relations_json FROM candidate_consolidation_relation_batches "
                "WHERE run_id = ? ORDER BY batch_number", (run_id,))]
            calls = [dict(item) for item in db.execute(
                "SELECT * FROM candidate_consolidation_model_calls WHERE run_id = ? ORDER BY rowid",
                (run_id,))]
        result = dict(row)
        inventory_json = result.pop("inventory_json")
        result_json = result.pop("result_json")
        result["inventory"] = json.loads(inventory_json) if inventory_json else None
        result["result"] = json.loads(result_json) if result_json else None
        result["relations"] = [item for batch in batches for item in json.loads(batch["relations_json"])]
        result["model_calls"] = calls
        return result

    def load_p3_inputs(consolidation_run_id):
        with connect() as db:
            row = db.execute("SELECT * FROM candidate_consolidation_runs WHERE id = ?",
                             (consolidation_run_id,)).fetchone()
        if not row:
            raise HTTPException(404, "候选问题整合任务不存在")
        if row["status"] not in {"completed", "limited"} or not row["result_json"]:
            raise HTTPException(409, "候选问题整合结果尚不可用于证据核验")
        _, bundle, plan, profile, mapping, review = load_consolidation_inputs(
            row["professional_review_run_id"])
        try:
            consolidation = CandidateConsolidationResult.model_validate_json(row["result_json"])
            if (consolidation.consolidation_run_id != consolidation_run_id or
                    row["document_id"] != consolidation.document_id or
                    row["source_bundle_id"] != consolidation.source_bundle_id or
                    row["understanding_run_id"] != consolidation.understanding_run_id or
                    row["professional_review_run_id"] != consolidation.professional_review_run_id):
                raise ValueError("P2-C stored run identity mismatch")
            pdf_path = files_dir / f'{row["document_id"]}.pdf'
            validate_p3_inputs(bundle, plan, profile, mapping, review,
                               consolidation, pdf_path)
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(409, f"证据核验来源快照无效：{type(exc).__name__}") from None
        return row, bundle, plan, profile, mapping, review, consolidation, pdf_path

    def run_evidence_verification(consolidation_run_id, run_id):
        try:
            _, bundle, plan, profile, mapping, review, consolidation, pdf_path = \
                load_p3_inputs(consolidation_run_id)
            with connect() as db:
                db.execute("UPDATE evidence_verification_runs SET status = 'running' WHERE id = ?",
                           (run_id,))
            session = model_session(timeout=180, max_retries=0, c1_run_id=run_id if c1 else None)
            model = session.configuration.model

            def model_call(stage, system, user):
                started = time.perf_counter()
                stream = model_completion(session,
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
                return {"content": "".join(pieces), "model_identifier": actual_model,
                        "input_tokens": getattr(usage, "prompt_tokens", None),
                        "output_tokens": getattr(usage, "completion_tokens", None),
                        "duration_ms": round((time.perf_counter() - started) * 1000)}

            def persist_trace(trace):
                with connect() as db:
                    db.execute("INSERT INTO evidence_verification_model_calls VALUES "
                               "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                               (uuid.uuid4().hex, run_id, trace.stage, trace.prompt_version,
                                trace.requested_model, trace.actual_model, trace.input_tokens,
                                trace.output_tokens, trace.duration_ms, trace.status, trace.error))

            def persist_tool(check):
                with connect() as db:
                    db.execute("INSERT OR REPLACE INTO evidence_verification_tool_results "
                               "VALUES (?, ?, ?, ?)",
                               (run_id, check.tool_result_id, check.verification_task_id,
                                check.model_dump_json()))

            def persist_task(adjudication):
                with connect() as db:
                    db.execute("INSERT OR REPLACE INTO evidence_verification_tasks "
                               "VALUES (?, ?, ?, ?, datetime('now'))",
                               (run_id, adjudication.verification_task_id,
                                adjudication.execution_status, adjudication.model_dump_json()))

            result = run_p3(bundle, plan, profile, mapping, review, consolidation,
                pdf_path, run_id, model_call, model,
                budget=c1.p2_budget(run_id) if c1 else RunBudget(
                    max_calls=int(os.environ.get("P3_MAX_CALLS", "48")),
                    max_input_tokens=int(os.environ.get("P3_MAX_INPUT_TOKENS", "450000")),
                    max_output_tokens=int(os.environ.get("P3_MAX_OUTPUT_TOKENS", "100000")),
                    max_duration_ms=int(os.environ.get("P3_MAX_DURATION_MS", "2700000"))),
                region_dir=data_dir / "p3-regions" / run_id,
                on_trace=persist_trace, on_task=persist_task, on_tool=persist_tool)
            with connect() as db:
                db.execute("UPDATE evidence_verification_runs SET status = ?, result_json = ?, "
                           "finished_at = datetime('now') WHERE id = ?",
                           (result.execution_status, result.model_dump_json(), run_id))
        except Exception as exc:
            with connect() as db:
                completed = db.execute("SELECT 1 FROM evidence_verification_tasks WHERE run_id = ? "
                                       "LIMIT 1", (run_id,)).fetchone()
                db.execute("UPDATE evidence_verification_runs SET status = ?, error = ?, "
                           "finished_at = datetime('now') WHERE id = ?",
                           ("partial_failed" if completed else "failed",
                            f"evidence_verification:{type(exc).__name__}", run_id))
        finally:
            if c1:
                with connect() as db:
                    state = db.execute("SELECT status FROM evidence_verification_runs WHERE id=?",
                                       (run_id,)).fetchone()
                c1.store.finish_stage(run_id, completed=bool(
                    state and state["status"] in {"completed", "limited"}))

    @app.post("/api/candidate-consolidation-runs/{run_id}/evidence-verification-runs",
              status_code=202)
    def create_evidence_verification_run(run_id: str, background_tasks: BackgroundTasks,
                                         http_request: Request,
                                         payload: EvidenceVerificationRunRequest | None = None):
        model_configuration_or_503()
        source, bundle, _, _, _, _, _, _ = load_p3_inputs(run_id)
        c1_claimed = c1_claim(http_request, "p3", run_id)
        if c1_claimed and not c1_claimed[1]:
            return {"id": c1_claimed[0], "consolidation_run_id": run_id,
                    "status": c1.store.get_run(c1_claimed[0])["status"]}
        new_id = c1_claimed[0] if c1_claimed else uuid.uuid4().hex
        try:
            with connect() as db:
                existing = db.execute("SELECT id, status FROM evidence_verification_runs "
                    "WHERE consolidation_run_id = ? ORDER BY rowid DESC LIMIT 1", (run_id,)).fetchone()
                if existing and (existing["status"] in {"queued", "running"} or
                                 not payload or not payload.retry):
                    raise HTTPException(409, "该整合任务已有证据核验运行；请先查看现有结果")
                db.execute("INSERT INTO evidence_verification_runs "
                    "(id, consolidation_run_id, professional_review_run_id, "
                    "understanding_run_id, document_id, source_bundle_id, status, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, 'queued', datetime('now'))",
                    (new_id, run_id, source["professional_review_run_id"],
                     source["understanding_run_id"], source["document_id"], bundle["bundle_id"]))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "该整合任务已有正在执行的证据核验") from None
        background_tasks.add_task(run_evidence_verification, run_id, new_id)
        return {"id": new_id, "consolidation_run_id": run_id, "status": "queued"}

    @app.get("/api/evidence-verification-runs/{run_id}")
    def get_evidence_verification_run(run_id: str):
        with connect() as db:
            row = db.execute("SELECT * FROM evidence_verification_runs WHERE id = ?",
                             (run_id,)).fetchone()
            if not row:
                raise HTTPException(404, "证据核验任务不存在")
            tasks = [dict(item) for item in db.execute(
                "SELECT * FROM evidence_verification_tasks WHERE run_id = ? ORDER BY rowid",
                (run_id,))]
            calls = [dict(item) for item in db.execute(
                "SELECT * FROM evidence_verification_model_calls WHERE run_id = ? "
                "ORDER BY rowid", (run_id,))]
        result = dict(row)
        result_json = result.pop("result_json")
        result["result"] = json.loads(result_json) if result_json else None
        result["tasks"] = [{**item, "adjudication": json.loads(item.pop("adjudication_json"))}
                           for item in tasks]
        result["model_calls"] = calls
        return result

    @app.get("/api/evidence-verification-runs/{run_id}/tasks/{task_id}")
    def get_evidence_verification_task(run_id: str, task_id: str):
        with connect() as db:
            run = db.execute("SELECT result_json FROM evidence_verification_runs WHERE id = ?",
                             (run_id,)).fetchone()
            if not run:
                raise HTTPException(404, "证据核验任务不存在")
            row = db.execute("SELECT * FROM evidence_verification_tasks WHERE run_id = ? "
                             "AND task_id = ?", (run_id, task_id)).fetchone()
            if not row:
                raise HTTPException(404, "单项核验尚无结果")
            tools = [json.loads(item["result_json"]) for item in db.execute(
                "SELECT result_json FROM evidence_verification_tool_results WHERE run_id = ? "
                "AND task_id = ? ORDER BY rowid", (run_id, task_id))]
        observations = []
        if run["result_json"]:
            observations = [item for item in json.loads(run["result_json"])["observations"]
                            if item["verification_task_id"] == task_id]
        return {"task_id": task_id, "status": row["status"],
                "adjudication": json.loads(row["adjudication_json"]),
                "observations": observations, "tool_results": tools}

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

    def review_model_call(run_id, stage, session, messages):
        started = time.perf_counter()
        usage = None
        status = "failed"
        model = session.configuration.model
        try:
            completion = model_completion(session, model=model, messages=messages)
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
            session = model_session(timeout=120, max_retries=1)
            proposal = review_model_call(run_id, "propose", session, [
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
                challenge = review_model_call(run_id, "challenge", session, [
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
        model_configuration_or_503()
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
            session = model_session(timeout=120, max_retries=1)
            response = review_model_call(finding["run_id"], "deep_challenge", session, [
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
        model_configuration_or_503()
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
        configuration = model_configuration_or_503()
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
        model = configuration.model

        def generate():
            active_model = model
            started = time.perf_counter()
            answer = []
            usage = None
            status = "failed"
            try:
                yield sse("sources", {"evidence": [{"evidence_id": row["id"], "page_index": row["page_index"],
                                                    "excerpt": row["excerpt"][:400]} for row in chosen]})
                session = model_session(timeout=90, max_retries=1)
                active_model = session.configuration.model
                messages = ([{"role": "system", "content": instructions}] + history +
                            [{"role": "user", "content": question}])
                stream = model_completion(
                    session, model=active_model, messages=messages,
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
                               (uuid.uuid4().hex, conversation_id, active_model,
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
            configuration = model_configuration_or_503()
            model = configuration.model
            started = time.perf_counter()
            usage = None
            status = "failed"
            try:
                session = model_session(timeout=120, max_retries=1)
                model = session.configuration.model
                completion = model_completion(session, model=model, messages=[
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
            except ModelConfigurationError as exc:
                raise HTTPException(status_code=503, detail=exc.safe_detail()) from None
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

    register_p4_routes(
        app, connect, files_dir, load_p3_inputs,
        lambda: model_session_or_503(timeout=180, max_retries=0,
                                     allow_c1_p4=True),
        model_completion,
        model_configuration_or_503,
        model_session_preflight,
        lambda: require_p4_budget_configuration(MODEL_ENVIRONMENT_FILE),
        c1_control=c1,
    )
    return app


app = create_app(Path(os.environ.get("PAPER_REVIEW_DATA_DIR", Path(__file__).resolve().parents[1] / "data")))
