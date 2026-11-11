"""Private, fail-closed ledger for one-paper P5-C1 stage execution.

This module never loads a blind reference or a credential. All identifiers in
the ledger are hashes or opaque run IDs; model request bodies are not stored.
"""

from __future__ import annotations

import hashlib
import os
import json
import secrets
import sqlite3
import time
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class C1ControlError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class C1ActualBudgetExceeded(C1ControlError):
    """Supplier usage is known, but its measured amount exceeded this stage."""

    def __init__(self, input_tokens: int, output_tokens: int, actual_model: str):
        super().__init__("actual_budget_exhausted")
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.actual_model = actual_model


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class C1Identity(_Strict):
    paper_id: str
    pdf_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    manifest_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_lock_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    execution_tree: str = Field(pattern=r"^[0-9a-f]{40}$")
    requested_model: Literal["gpt-6.1-sol"]
    model_config_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class C1Budget(_Strict):
    max_calls: int = Field(gt=0)
    max_input_tokens: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    max_output_tokens_per_request: int = Field(gt=0)
    max_duration_ms: int = Field(gt=0)
    max_transport_retries: Literal[0]
    content_repair_policy: Literal["valid_usage_within_stage_budget"]
    max_planned_cost_usd: str
    input_usd_per_million: str
    output_usd_per_million: str
    pricing_status: Literal["estimated_unverified", "provider_verified"]
    provider_price_evidence_sha256: str | None = Field(
        default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_prices(self):
        try:
            values = [Decimal(self.max_planned_cost_usd),
                      Decimal(self.input_usd_per_million),
                      Decimal(self.output_usd_per_million)]
        except InvalidOperation:
            raise ValueError("budget_price_invalid") from None
        if any(not value.is_finite() or value <= 0 for value in values):
            raise ValueError("budget_price_invalid")
        if self.max_output_tokens_per_request > self.max_output_tokens:
            raise ValueError("request_output_exceeds_stage_budget")
        if self.pricing_status == "provider_verified" and not self.provider_price_evidence_sha256:
            raise ValueError("provider_price_evidence_required")
        return self


def _canonical(value: BaseModel) -> str:
    return json.dumps(value.model_dump(), sort_keys=True, separators=(",", ":"))


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def checked_runtime_identity(root: Path, manifest_path: Path, lock_path: Path,
                             expected_lock_sha256: str, paper_id: str,
                             paper_paths: Mapping[str, Path],
                             environment_file: Path) -> C1Identity:
    """Verify the reviewed lock and all frozen assets without exposing reference text."""
    from dotenv import dotenv_values
    from backend.model_runtime import require_model_runtime_config
    from scripts.p5c.preflight import _manifest, git_facts, validate_preflight

    try:
        file_values = dotenv_values(environment_file) if environment_file.is_file() else {}
        manifest = _manifest(manifest_path)
        papers = {paper.paper_id: paper for paper in manifest.papers}
        if paper_id not in papers:
            raise C1ControlError("trial_paper_invalid")
        facts = git_facts(root)
        lock_bytes = lock_path.read_bytes()
        validate_preflight(
            manifest_path=manifest_path, prompt_directory=root / "backend/prompts",
            paper_paths=paper_paths,
            reference_paths={key: root / ref.path for key, ref in manifest.references.items()},
            git_facts=facts, lock_bytes=lock_bytes,
            lock_sha256=expected_lock_sha256, process_environment=os.environ,
            environment_file_values=file_values,
        )
        config = require_model_runtime_config(environment_file)
        if config.model != manifest.requested_model:
            raise C1ControlError("requested_model_mismatch")
        # This digest is an identity check, not a recoverable credential or endpoint.
        fingerprint = _digest(json.dumps([config.api_key, config.base_url, config.model]))
        return C1Identity(
            paper_id=paper_id, pdf_sha256=papers[paper_id].pdf_sha256,
            manifest_sha256=hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
            execution_lock_sha256=expected_lock_sha256,
            execution_commit=facts["commit"], execution_tree=facts["tree"],
            requested_model=config.model, model_config_sha256=fingerprint,
        )
    except C1ControlError:
        raise
    except Exception:
        raise C1ControlError("execution_preflight_invalid") from None


class C1Store:
    """One SQLite file per private C1 trial; transactions serialize claims."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS c1_trial (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    identity_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS c1_stage (
                    run_id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE,
                    stage TEXT NOT NULL, upstream_run_id TEXT NOT NULL,
                    source_stage_run_id TEXT NOT NULL,
                    identity_json TEXT NOT NULL, budget_json TEXT NOT NULL,
                    status TEXT NOT NULL, created_monotonic REAL NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE UNIQUE INDEX IF NOT EXISTS c1_one_active_stage
                    ON c1_stage(stage)
                    WHERE status IN ('authorized', 'dispatch_pending');
                CREATE TABLE IF NOT EXISTS c1_call (
                    model_call_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
                    ordinal INTEGER NOT NULL, status TEXT NOT NULL,
                    estimated_input_tokens INTEGER NOT NULL,
                    reserved_output_tokens INTEGER NOT NULL,
                    planned_cost_usd TEXT NOT NULL,
                    input_tokens INTEGER, output_tokens INTEGER, total_tokens INTEGER,
                    known_cost_usd TEXT, actual_model TEXT,
                    error_code TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(run_id, ordinal),
                    FOREIGN KEY(run_id) REFERENCES c1_stage(run_id)
                );
            """)
        os.chmod(self.path, 0o600)

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        return db

    def freeze_trial(self, identity: C1Identity):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT identity_json FROM c1_trial WHERE singleton=1").fetchone()
            if row and row["identity_json"] != _canonical(identity):
                raise C1ControlError("trial_paper_mismatch")
            db.execute("INSERT OR IGNORE INTO c1_trial VALUES (1, ?)", (_canonical(identity),))

    def _assert_trial(self, db, identity: C1Identity):
        row = db.execute("SELECT identity_json FROM c1_trial WHERE singleton=1").fetchone()
        if not row or row["identity_json"] != _canonical(identity):
            raise C1ControlError("execution_identity_mismatch")

    def authorize(self, stage: str, upstream_run_id: str, identity: C1Identity,
                  budget: C1Budget, *, acknowledgement: str,
                  report_kind: str = "internal:zh",
                  source_stage_run_id: str | None = None) -> tuple[str, str]:
        if acknowledgement != "P5C_STAGE_AUTHORIZED":
            raise C1ControlError("operator_ack_required")
        if stage not in {"p2a", "p2b", "p2c", "p3", "p4"}:
            raise C1ControlError("stage_invalid")
        if stage == "p4" and report_kind != "internal:zh":
            raise C1ControlError("report_scope_invalid")
        if not upstream_run_id:
            raise C1ControlError("stage_source_missing")
        source_stage_run_id = source_stage_run_id or upstream_run_id
        token = secrets.token_urlsafe(32)
        run_id = uuid.uuid4().hex
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._assert_trial(db, identity)
            if db.execute("SELECT 1 FROM c1_stage WHERE stage=? AND status='completed'",
                          (stage,)).fetchone():
                raise C1ControlError("stage_already_completed")
            try:
                db.execute("INSERT INTO c1_stage (run_id, token_hash, stage, upstream_run_id, "
                           "source_stage_run_id, "
                           "identity_json, budget_json, status, created_monotonic) "
                           "VALUES (?, ?, ?, ?, ?, ?, ?, 'authorized', ?)",
                           (run_id, _digest(token), stage, upstream_run_id, source_stage_run_id,
                            _canonical(identity), _canonical(budget), time.monotonic()))
            except sqlite3.IntegrityError:
                raise C1ControlError("stage_already_authorized") from None
        return token, run_id

    def claim(self, token: str, stage: str, upstream_run_id: str,
              identity: C1Identity,
              source_stage_run_id: str | None = None) -> tuple[str, bool]:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM c1_stage WHERE token_hash=?", (_digest(token),)).fetchone()
            if row is None:
                raise C1ControlError("authorization_missing")
            if (row["stage"] != stage or row["upstream_run_id"] != upstream_run_id
                    or row["source_stage_run_id"] != (source_stage_run_id or upstream_run_id)):
                raise C1ControlError("stage_source_mismatch")
            self._assert_trial(db, identity)
            if row["identity_json"] != _canonical(identity):
                raise C1ControlError("execution_identity_mismatch")
            if row["status"] == "authorized":
                db.execute("UPDATE c1_stage SET status='dispatch_pending' WHERE run_id=?",
                           (row["run_id"],))
                return row["run_id"], True
            return row["run_id"], False

    def cancel_unclaimed_authorization(self, run_id: str):
        with self._connect() as db:
            db.execute("UPDATE c1_stage SET status='cancelled' "
                       "WHERE run_id=? AND status='authorized'", (run_id,))

    def reserve_request(self, run_id: str, identity: C1Identity, model: str,
                        messages: list[dict], *, max_tokens: int | None = None):
        if model != identity.requested_model:
            raise C1ControlError("requested_model_mismatch")
        if max_tokens is not None and (type(max_tokens) is not int or max_tokens <= 0):
            raise C1ControlError("request_output_limit_invalid")
        # UTF-8 byte length is a planning estimate, not a supplier token ceiling.
        estimated = sum(len(str(message.get("content", "")).encode())
                        for message in messages)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._assert_trial(db, identity)
            row = db.execute("SELECT * FROM c1_stage WHERE run_id=?", (run_id,)).fetchone()
            if row is None or row["identity_json"] != _canonical(identity):
                raise C1ControlError("execution_identity_mismatch")
            if row["status"] == "unknown_usage":
                raise C1ControlError("previous_usage_unknown")
            if row["status"] != "dispatch_pending":
                raise C1ControlError("stage_not_dispatchable")
            budget = C1Budget.model_validate_json(row["budget_json"])
            calls = db.execute("SELECT * FROM c1_call WHERE run_id=? ORDER BY ordinal", (run_id,)).fetchall()
            if any(call["status"] != "usage_valid" for call in calls):
                raise C1ControlError("previous_usage_unknown")
            if len(calls) >= budget.max_calls:
                raise C1ControlError("request_budget_exhausted")
            if (time.monotonic() - row["created_monotonic"]) * 1000 >= budget.max_duration_ms:
                raise C1ControlError("time_budget_exhausted")
            known_input = sum(call["input_tokens"] for call in calls)
            known_output = sum(call["output_tokens"] for call in calls)
            reserve_output = min(max_tokens or budget.max_output_tokens_per_request,
                                 budget.max_output_tokens_per_request)
            if estimated + known_input > budget.max_input_tokens:
                raise C1ControlError("input_budget_exhausted")
            if reserve_output + known_output > budget.max_output_tokens:
                raise C1ControlError("output_budget_exhausted")
            planned = (Decimal(estimated) * Decimal(budget.input_usd_per_million)
                       + Decimal(reserve_output) * Decimal(budget.output_usd_per_million)) / 1_000_000
            known_cost = sum((Decimal(call["known_cost_usd"]) for call in calls), Decimal(0))
            if planned + known_cost > Decimal(budget.max_planned_cost_usd):
                raise C1ControlError("planned_cost_budget_exhausted")
            call_id = uuid.uuid4().hex
            db.execute("INSERT INTO c1_call (model_call_id, run_id, ordinal, status, "
                       "estimated_input_tokens, reserved_output_tokens, planned_cost_usd) "
                       "VALUES (?, ?, ?, 'reserved', ?, ?, ?)",
                       (call_id, run_id, len(calls) + 1, estimated, reserve_output, str(planned)))
            return call_id, reserve_output

    def mark_dispatch_pending(self, call_id: str):
        with self._connect() as db:
            db.execute("UPDATE c1_call SET status='dispatch_pending' "
                       "WHERE model_call_id=? AND status='reserved'", (call_id,))
            if db.total_changes != 1:
                raise C1ControlError("reservation_missing")

    def mark_unknown(self, call_id: str, code: str = "usage_unknown"):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT run_id, status FROM c1_call WHERE model_call_id=?", (call_id,)).fetchone()
            if not row or row["status"] == "usage_valid":
                return
            db.execute("UPDATE c1_call SET status='unknown_usage', error_code=? WHERE model_call_id=?",
                       (code, call_id))
            db.execute("UPDATE c1_stage SET status='unknown_usage' WHERE run_id=?", (row["run_id"],))

    def settle_usage(self, call_id: str, usage, actual_model: str | None,
                     expected_model: str):
        values = (getattr(usage, "prompt_tokens", None),
                  getattr(usage, "completion_tokens", None),
                  getattr(usage, "total_tokens", None))
        if (any(type(value) is not int or value < 0 for value in values)
                or values[0] + values[1] != values[2]
                or actual_model != expected_model):
            self.mark_unknown(call_id, "usage_invalid_or_model_mismatch")
            raise C1ControlError("usage_unknown")
        exceeded = False
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            call = db.execute("SELECT * FROM c1_call WHERE model_call_id=?", (call_id,)).fetchone()
            if not call or call["status"] != "dispatch_pending":
                raise C1ControlError("call_state_invalid")
            stage = db.execute("SELECT budget_json FROM c1_stage WHERE run_id=?", (call["run_id"],)).fetchone()
            budget = C1Budget.model_validate_json(stage["budget_json"])
            cost = (Decimal(values[0]) * Decimal(budget.input_usd_per_million)
                    + Decimal(values[1]) * Decimal(budget.output_usd_per_million)) / 1_000_000
            db.execute("UPDATE c1_call SET status='usage_valid', input_tokens=?, output_tokens=?, "
                       "total_tokens=?, known_cost_usd=?, actual_model=? WHERE model_call_id=?",
                       (*values, str(cost), actual_model, call_id))
            valid = db.execute("SELECT input_tokens, output_tokens, known_cost_usd "
                               "FROM c1_call WHERE run_id=? AND status='usage_valid'",
                               (call["run_id"],)).fetchall()
            if (values[1] > call["reserved_output_tokens"]
                    or sum(item["input_tokens"] for item in valid) > budget.max_input_tokens
                    or sum(item["output_tokens"] for item in valid) > budget.max_output_tokens
                    or sum((Decimal(item["known_cost_usd"]) for item in valid), Decimal(0))
                    > Decimal(budget.max_planned_cost_usd)):
                db.execute("UPDATE c1_stage SET status='budget_exhausted' WHERE run_id=?",
                           (call["run_id"],))
                exceeded = True
        if exceeded:
            raise C1ActualBudgetExceeded(values[0], values[1], actual_model)

    def recover_unknown_dispatches(self):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE c1_call SET status='unknown_usage', error_code='process_interrupted' "
                       "WHERE status='dispatch_pending'")
            db.execute("UPDATE c1_stage SET status='interrupted' WHERE status='dispatch_pending'")
            db.execute("UPDATE c1_stage SET status='unknown_usage' WHERE run_id IN "
                       "(SELECT run_id FROM c1_call WHERE status='unknown_usage')")

    def budget_for(self, run_id: str) -> C1Budget:
        with self._connect() as db:
            row = db.execute("SELECT budget_json FROM c1_stage WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise C1ControlError("run_missing")
        return C1Budget.model_validate_json(row["budget_json"])

    def assert_run_identity(self, run_id: str, identity: C1Identity):
        with self._connect() as db:
            self._assert_trial(db, identity)
            row = db.execute("SELECT identity_json, status FROM c1_stage WHERE run_id=?",
                             (run_id,)).fetchone()
        if (not row or row["identity_json"] != _canonical(identity)
                or row["status"] != "dispatch_pending"):
            raise C1ControlError("execution_identity_mismatch")

    def finish_stage(self, run_id: str, *, completed: bool):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM c1_stage WHERE run_id=?", (run_id,)).fetchone()
            if not row:
                raise C1ControlError("run_missing")
            if row["status"] == "dispatch_pending":
                db.execute("UPDATE c1_stage SET status=? WHERE run_id=?",
                           ("completed" if completed else "failed", run_id))

    def mark_stage_unknown(self, run_id: str):
        with self._connect() as db:
            db.execute("UPDATE c1_stage SET status='unknown_usage' "
                       "WHERE run_id=? AND status IN ('dispatch_pending','interrupted')", (run_id,))

    def list_stage_states(self):
        with self._connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT run_id, stage, status FROM c1_stage ORDER BY created_at, rowid")]

    def recover_verified_completion(self, run_id: str):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            calls = db.execute("SELECT status FROM c1_call WHERE run_id=?", (run_id,)).fetchall()
            if any(call["status"] != "usage_valid" for call in calls):
                raise C1ControlError("recovery_usage_not_complete")
            db.execute("UPDATE c1_stage SET status='completed' WHERE run_id=? "
                       "AND status='interrupted'", (run_id,))

    def get_run(self, run_id: str) -> dict:
        with self._connect() as db:
            row = db.execute("SELECT * FROM c1_stage WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                raise C1ControlError("run_missing")
            calls = [dict(item) for item in db.execute("SELECT * FROM c1_call "
                    "WHERE run_id=? ORDER BY ordinal", (run_id,))]
        return {
            "run_id": run_id, "stage": row["stage"],
            "status": ("unknown_usage" if any(item["status"] == "dispatch_pending"
                                              for item in calls) else row["status"]),
            "upstream_run_id": row["upstream_run_id"],
            "source_stage_run_id": row["source_stage_run_id"],
            "execution_identity": json.loads(row["identity_json"]),
            "budget": json.loads(row["budget_json"]), "model_calls": calls,
            "provider_request_count": sum(item["status"] != "reserved" for item in calls),
            "known_input_tokens": sum(item["input_tokens"] or 0 for item in calls
                                      if item["status"] == "usage_valid"),
            "known_output_tokens": sum(item["output_tokens"] or 0 for item in calls
                                       if item["status"] == "usage_valid"),
            "unknown_usage_calls": sum(item["status"] in {"unknown_usage", "dispatch_pending"}
                                       for item in calls),
            "cost_complete": all(item["status"] == "usage_valid" for item in calls),
            "planned_cost_usd": str(sum((Decimal(item["planned_cost_usd"]) for item in calls),
                                        Decimal(0))),
            "known_usage_cost_usd": str(sum((Decimal(item["known_cost_usd"])
                                             for item in calls if item["status"] == "usage_valid"),
                                            Decimal(0))),
            "provider_billed_cost_usd": None,
            "provider_account_hard_limit_usd": None,
        }


class GuardedChatClient:
    """Wrap only the C1 SDK client. Reserve before every streamed request."""

    def __init__(self, client, store: C1Store, run_id: str, identity: C1Identity,
                 identity_check=None):
        self._client = client
        self.chat = _ChatProxy(client.chat, store, run_id, identity, identity_check)


class _ChatProxy:
    def __init__(self, chat, store, run_id, identity, identity_check):
        self.completions = _CompletionsProxy(chat.completions, store, run_id,
                                             identity, identity_check)


class _CompletionsProxy:
    def __init__(self, completions, store, run_id, identity, identity_check):
        self._completions = completions
        self._store = store
        self._run_id = run_id
        self._identity = identity
        self._identity_check = identity_check

    def create(self, **kwargs):
        from backend.model_runtime import ModelConfigurationError

        try:
            if self._identity_check is not None and self._identity_check() != self._identity:
                raise C1ControlError("execution_identity_mismatch")
            if (kwargs.get("stream") is not True or
                    kwargs.get("stream_options", {}).get("include_usage") is not True):
                raise C1ControlError("usage_stream_required")
            call_id, output_cap = self._store.reserve_request(
                self._run_id, self._identity, kwargs.get("model"), kwargs.get("messages", []),
                max_tokens=kwargs.get("max_tokens"))
            kwargs["max_tokens"] = output_cap
            self._store.mark_dispatch_pending(call_id)
        except Exception as exc:
            code = exc.code if isinstance(exc, C1ControlError) else "c1_reservation_persist_failed"
            raise ModelConfigurationError([code]) from None
        try:
            stream = self._completions.create(**kwargs)
        except Exception:
            self._store.mark_unknown(call_id)
            raise C1ControlError("usage_unknown") from None

        def guarded_stream():
            usage = None
            actual_model = None
            try:
                for chunk in stream:
                    actual_model = getattr(chunk, "model", None) or actual_model
                    usage = getattr(chunk, "usage", None) or usage
                    yield chunk
                if usage is None:
                    raise C1ControlError("usage_unknown")
                self._store.settle_usage(call_id, usage, actual_model,
                                         self._identity.requested_model)
            except C1ActualBudgetExceeded:
                raise
            except C1ControlError:
                self._store.mark_unknown(call_id)
                raise C1ControlError("usage_unknown") from None
            except Exception:
                self._store.mark_unknown(call_id)
                raise C1ControlError("usage_unknown") from None

        return guarded_stream()
