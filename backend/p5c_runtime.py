"""C1-only bridge from reviewed execution assets to the private stage ledger."""

from __future__ import annotations

import os
import hashlib
import sqlite3
from decimal import Decimal
from pathlib import Path

from backend.model_runtime import ModelConfigurationError
from scripts.p5c.stage_control import (
    C1Budget, C1ControlError, C1Identity, C1Store, checked_runtime_identity,
)


PAPER_ENV = {
    "sardet100k-neurips2024": "P5C_H1_PDF",
    "sam-iclr2021": "P5C_H2_PDF",
}


class C1Runtime:
    def __init__(self, data_dir: Path, *, root: Path, environment_file: Path,
                 recover_on_start: bool = True):
        self.data_dir = Path(data_dir)
        self.root = Path(root)
        self.environment_file = Path(environment_file)
        if self.data_dir.resolve().is_relative_to(self.root.resolve()):
            raise C1ControlError("private_data_directory_required")
        if os.stat(data_dir).st_mode & 0o077:
            raise C1ControlError("private_data_directory_required")
        if ((self.data_dir / "review.db").exists()
                and not (self.data_dir / "p5c-stage-control.db").exists()):
            raise C1ControlError("new_private_trial_database_required")
        try:
            output_dir = Path(os.environ["P5C_OUTPUT_DIR"])
            if not output_dir.is_dir() or os.stat(output_dir).st_mode & 0o077:
                raise C1ControlError("private_output_directory_required")
        except KeyError:
            raise C1ControlError("private_output_directory_required") from None
        if (output_dir.resolve().is_relative_to(self.root.resolve())
                or output_dir.resolve() == self.data_dir.resolve()):
            raise C1ControlError("private_output_directory_required")
        self.output_dir = output_dir
        self.store = C1Store(self.data_dir / "p5c-stage-control.db")
        if recover_on_start:
            self.store.recover_unknown_dispatches()

    def identity(self) -> C1Identity:
        try:
            paper_id = os.environ["P5C_PAPER_ID"]
            paths = {paper: Path(os.environ[key]) for paper, key in PAPER_ENV.items()}
            return checked_runtime_identity(
                self.root, Path(os.environ["P5C_MANIFEST"]),
                Path(os.environ["P5C_EXECUTION_LOCK"]),
                os.environ["P5C_EXPECTED_LOCK_SHA256"], paper_id, paths,
                self.environment_file,
            )
        except (KeyError, OSError, ModelConfigurationError):
            raise C1ControlError("execution_configuration_invalid") from None

    def validate_source(self, stage: str, upstream_id: str, identity: C1Identity):
        """Only completed current-trial ancestry may be authorized."""
        targets = {
            "p2a": ("documents", "id", None),
            "p2b": ("paper_understanding_runs", "id", {"completed"}),
            "p2c": ("professional_review_runs", "id", {"completed", "limited"}),
            "p3": ("candidate_consolidation_runs", "id", {"completed", "limited"}),
            "p4": ("p4_report_projects", "id", None),
        }
        if stage not in targets:
            raise C1ControlError("stage_invalid")
        table, column, statuses = targets[stage]
        with sqlite3.connect(self.data_dir / "review.db") as db:
            db.row_factory = sqlite3.Row
            row = db.execute(f"SELECT * FROM {table} WHERE {column}=?", (upstream_id,)).fetchone()
            if row is None or (statuses and row["status"] not in statuses):
                raise C1ControlError("stage_source_invalid")
            document_id = row["id"] if stage == "p2a" else row["document_id"]
            document = db.execute("SELECT id, sha256 FROM documents WHERE id=?",
                                  (document_id,)).fetchone()
            if (document is None or document["sha256"] != identity.pdf_sha256
                    or document_id != identity.pdf_sha256):
                raise C1ControlError("trial_pdf_mismatch")
            try:
                actual_pdf_sha = hashlib.sha256(
                    (self.data_dir / "files" / f"{document_id}.pdf").read_bytes()).hexdigest()
            except OSError:
                raise C1ControlError("trial_pdf_mismatch") from None
            if actual_pdf_sha != identity.pdf_sha256:
                raise C1ControlError("trial_pdf_mismatch")
            if stage == "p4":
                if db.execute("SELECT 1 FROM p4_drafts WHERE kind='internal' "
                              "AND language='zh' LIMIT 1").fetchone():
                    raise C1ControlError("trial_internal_report_already_exists")
                p3 = db.execute("SELECT status, document_id FROM evidence_verification_runs "
                                "WHERE id=?", (row["p3_run_id"],)).fetchone()
                if not p3 or p3["status"] not in {"completed", "limited"} or p3["document_id"] != document_id:
                    raise C1ControlError("stage_source_invalid")
            if stage != "p2a":
                parent_id = row["p3_run_id"] if stage == "p4" else upstream_id
                expected = {"p2b": "p2a", "p2c": "p2b", "p3": "p2c", "p4": "p3"}[stage]
                try:
                    parent = self.store.get_run(parent_id)
                except C1ControlError:
                    raise C1ControlError("stage_source_not_authorized") from None
                if parent["stage"] != expected or parent["status"] != "completed":
                    raise C1ControlError("stage_source_not_authorized")

    def authorize(self, stage: str, upstream_id: str, budget: C1Budget,
                  acknowledgement: str):
        identity = self.identity()
        self.validate_source(stage, upstream_id, identity)
        self.store.freeze_trial(identity)
        source_stage_run_id = self.source_stage_run_id(stage, upstream_id)
        return self.store.authorize(stage, upstream_id, identity, budget,
                                    acknowledgement=acknowledgement,
                                    source_stage_run_id=source_stage_run_id)

    def source_stage_run_id(self, stage: str, upstream_id: str):
        if stage != "p4":
            return upstream_id
        with sqlite3.connect(self.data_dir / "review.db") as db:
            row = db.execute("SELECT p3_run_id FROM p4_report_projects WHERE id=?",
                             (upstream_id,)).fetchone()
        if not row:
            raise C1ControlError("stage_source_invalid")
        return row[0]

    def claim(self, token: str | None, stage: str, upstream_id: str):
        if not token:
            raise C1ControlError("authorization_missing")
        identity = self.identity()
        self.validate_source(stage, upstream_id, identity)
        return self.store.claim(token, stage, upstream_id, identity,
                                source_stage_run_id=self.source_stage_run_id(stage, upstream_id))

    def p2_budget(self, run_id: str):
        from backend.p2a_engine import RunBudget

        budget = self.store.budget_for(run_id)
        return RunBudget(max_calls=budget.max_calls,
                         max_input_tokens=budget.max_input_tokens,
                         max_output_tokens=budget.max_output_tokens,
                         max_duration_ms=budget.max_duration_ms)

    def check_p4_budget(self, run_id: str, actual):
        budget = self.store.budget_for(run_id)
        if (budget.max_calls != actual.max_provider_requests
                or budget.max_input_tokens != actual.max_input_tokens
                or budget.max_output_tokens != actual.max_output_tokens
                or budget.max_output_tokens_per_request != actual.max_output_tokens_per_request
                or Decimal(budget.max_duration_ms) != actual.max_duration_seconds * 1000
                or Decimal(budget.max_planned_cost_usd) != actual.max_planned_cost_usd
                or Decimal(budget.input_usd_per_million) != actual.input_usd_per_million
                or Decimal(budget.output_usd_per_million) != actual.output_usd_per_million
                or budget.pricing_status != actual.pricing_status):
            raise C1ControlError("p4_budget_configuration_mismatch")
