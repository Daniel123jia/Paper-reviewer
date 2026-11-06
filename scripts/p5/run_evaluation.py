from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from scripts.p5.audit_lineage import audit_lineage
from scripts.p5.contracts import (EvaluationManifest, ModelCallSummary, PaperBudget,
                                  RunLedger, StageRun)


TERMINAL = {"completed", "limited", "partial_failed", "blocked", "budget_exceeded", "failed"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _error_type(value: Any) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    return text.split(":", 1)[0][:120]


def summarize_api_calls(stage: str, calls: list[dict]) -> list[ModelCallSummary]:
    return [ModelCallSummary(
        stage=str(call.get("stage") or call.get("dimension_id") or stage),
        status=("unknown_usage" if call.get("status") == "completed" and
                (call.get("input_tokens") is None or call.get("output_tokens") is None)
                else "completed" if call.get("status") == "completed" else "failed"),
        requested_model=str(call.get("requested_model") or "unknown"),
        actual_model=call.get("actual_model"), input_tokens=call.get("input_tokens"),
        output_tokens=call.get("output_tokens"), duration_ms=int(call.get("duration_ms") or 0),
        error_type=_error_type(call.get("error")),
    ) for call in calls]


@dataclass
class BudgetState:
    budget: PaperBudget
    calls: int = 0
    known_input_tokens: int = 0
    known_output_tokens: int = 0
    unknown_token_calls: int = 0

    def record(self, calls: list[ModelCallSummary]) -> None:
        self.calls += len(calls)
        self.known_input_tokens += sum(call.input_tokens or 0 for call in calls)
        self.known_output_tokens += sum(call.output_tokens or 0 for call in calls)
        self.unknown_token_calls += sum(call.input_tokens is None or call.output_tokens is None
                                        for call in calls)

    def can_start_stage(self) -> bool:
        return (self.calls < self.budget.max_calls and
                self.known_input_tokens < self.budget.max_input_tokens and
                self.known_output_tokens < self.budget.max_output_tokens)


class EvaluationRunner:
    def __init__(self, base_url: str, output_dir: Path, budget: PaperBudget,
                 timeout_seconds: float = 30, poll_seconds: float = 2,
                 database: Path | None = None, client: httpx.Client | None = None,
                 execution_mode: str = "fresh_model_run"):
        self.client = client or httpx.Client(base_url=base_url.rstrip("/"),
                                             timeout=timeout_seconds)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.budget = budget
        self.budget_state = BudgetState(budget)
        self.poll_seconds = poll_seconds
        self.database = Path(database) if database is not None else None
        if self.database is not None and not self.database.is_file():
            raise ValueError(f"database path does not exist or is not a file: {self.database}")
        self.execution_mode = execution_mode
        self.artifacts: dict[str, dict] = {}
        self.stages: list[StageRun] = []
        self.started = utcnow()
        self.deadline = time.monotonic() + budget.max_duration_seconds
        self.lineage_audit_passed: bool | None = None

    def close(self):
        self.client.close()

    def _can_start_stage(self) -> bool:
        return self.budget_state.can_start_stage() and time.monotonic() < self.deadline

    def _save(self, name: str, payload: Any) -> str:
        path = self.output_dir / f"{name}.json"
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
        return str(path)

    def _request_json(self, method: str, path: str, **kwargs) -> dict:
        response = self.client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()

    def _poll(self, path: str) -> dict:
        while True:
            if time.monotonic() >= self.deadline:
                raise TimeoutError("paper wall-clock budget exhausted")
            payload = self._request_json("GET", path)
            if payload.get("status") in TERMINAL:
                return payload
            time.sleep(self.poll_seconds)

    def _p4_calls(self, project_id: str, drafts: list[dict],
                  expected_drafts: int | None = None) -> tuple[list[dict], bool, str]:
        api_complete = bool(drafts) and all("model_calls" in draft for draft in drafts)
        if expected_drafts is not None and len(drafts) != expected_drafts:
            api_complete = False
        if api_complete:
            calls = [call for draft in drafts for call in draft["model_calls"]]
            incomplete = any(call.get("input_tokens") is None or
                             call.get("output_tokens") is None for call in calls)
            return calls, incomplete, "draft_api"
        if self.database is not None:
            with sqlite3.connect(self.database) as db:
                db.row_factory = sqlite3.Row
                calls = [dict(row) for row in db.execute(
                    "SELECT stage, requested_model, actual_model, input_tokens, output_tokens, "
                    "duration_ms, status, error FROM p4_model_calls "
                    "WHERE project_id = ? ORDER BY rowid", (project_id,))]
            incomplete = (not calls or any(call.get("input_tokens") is None or
                                           call.get("output_tokens") is None for call in calls))
            return calls, incomplete, "sqlite_fallback"
        calls = [call for draft in drafts for call in draft.get("model_calls", [])]
        return calls, True, "draft_api_partial" if calls else "unavailable"

    @staticmethod
    def _validate_author_draft(draft: dict, language: str, p3: dict) -> None:
        if draft.get("kind") != "author" or draft.get("language") != language:
            raise ValueError("author draft kind or language mismatch")
        if draft.get("approval_status") == "approved":
            raise ValueError("runner must not manufacture reviewer approval")
        plan = draft.get("plan") or {}
        selected = list(draft.get("selected_issue_ids") or [])
        if plan.get("report_kind") != "author" or plan.get("language") != language or \
                list(plan.get("selected_issue_ids") or []) != selected:
            raise ValueError("author draft plan identity mismatch")
        group_sources = {issue_id for group in plan.get("groups", [])
                         for issue_id in group.get("source_issue_ids", [])}
        if set(selected) != group_sources:
            raise ValueError("author draft source mapping does not cover selected issues")
        adjudications = (p3.get("result") or {}).get("adjudications", [])
        by_id = {item.get("consolidated_issue_id"): item for item in adjudications}
        for issue_id in selected:
            item = by_id.get(issue_id)
            if not item or item.get("report_eligibility") != "eligible_with_qualification" or \
                    item.get("verdict") not in {"concern_supported", "concern_narrowed"}:
                raise ValueError("author draft contains an issue not eligible for automatic drafting")

    @staticmethod
    def _stage_limitations(stage: str, response: dict) -> list[str]:
        limitations = [str(item) for item in response.get("source_limitations", [])]
        nested = response.get("result") or {}
        limitations.extend(str(item) for item in nested.get("unresolved_coverage_gaps", []))
        limitations.extend(str(item) for item in nested.get("uncovered_tasks", []))
        for item in nested.get("adjudications", []):
            limitations.extend(str(check) for check in item.get("outstanding_checks", []))
        if response.get("status") == "limited" and not limitations:
            limitations.append(f"{stage} returned limited without a complete verification record")
        return list(dict.fromkeys(limitations))

    def _record_stage(self, stage: str, response: dict, started_at: datetime,
                      document_id: str, source_bundle_id: str | None,
                      input_run_id: str | None, output_run_id: str | None,
                      artifact_path: str, calls: list[dict] | None = None,
                      usage_status: str | None = None) -> None:
        summaries = summarize_api_calls(stage, calls if calls is not None else
                                        response.get("model_calls", []))
        self.budget_state.record(summaries)
        if usage_status is None:
            usage_observed = calls is not None or "model_calls" in response
            usage_status = ("incomplete" if any(call.input_tokens is None or
                                                 call.output_tokens is None
                                                 for call in summaries)
                            else "complete" if usage_observed else "not_applicable")
        self.stages.append(StageRun(
            stage=stage, status=response.get("status", "completed"),
            execution_mode=self.execution_mode,
            started_at=started_at, finished_at=utcnow(), document_id=document_id,
            source_bundle_id=source_bundle_id, input_run_id=input_run_id,
            output_run_id=output_run_id, model_calls=summaries,
            usage_status=usage_status,
            source_limitations=self._stage_limitations(stage, response),
            artifact_path=artifact_path, error_type=_error_type(response.get("error"))))

    def run(self, paper_id: str, pdf_path: Path, expected_sha256: str,
            target_journal: str = "", special_requirements: str = "",
            canonical_paper_id: str | None = None) -> RunLedger:
        execution_paper_id = paper_id
        paper_id = canonical_paper_id or paper_id
        pdf_path = Path(pdf_path)
        actual_sha = sha256_file(pdf_path)
        if actual_sha != expected_sha256:
            raise ValueError(f"PDF SHA-256 mismatch: expected {expected_sha256}, got {actual_sha}")

        document_id = actual_sha
        source_bundle_id = None
        overall_status = "failed"
        active_stage: str | None = None
        active_started: datetime | None = None
        active_input_run_id: str | None = None
        active_output_run_id: str | None = None
        p4_drafts: list[dict] = []
        try:
            p1_started = utcnow()
            active_stage, active_started = "p1", p1_started
            with pdf_path.open("rb") as stream:
                document = self._request_json("POST", "/api/documents",
                    files={"file": (pdf_path.name, stream, "application/pdf")})
            source_bundle = self._request_json("GET", f"/api/documents/{document['id']}/source-bundle")
            parse_quality = self._request_json("GET", f"/api/documents/{document['id']}/parse-quality")
            document_id = document["id"]
            source_bundle_id = source_bundle["bundle_id"]
            self.artifacts["p1"] = {"document": document, "source_bundle": source_bundle,
                                    "parse_quality": parse_quality}
            path = self._save("p1", self.artifacts["p1"])
            self._record_stage("p1", {"status": "completed"}, p1_started, document_id,
                               source_bundle_id, None, document_id, path)
            active_stage = None

            if not self._can_start_stage():
                overall_status = "budget_exceeded"
                return self._ledger(paper_id, actual_sha, document_id, source_bundle_id,
                                    overall_status, execution_paper_id)

            p2a_started = utcnow()
            active_stage, active_started, active_input_run_id = "p2a", p2a_started, document_id
            created = self._request_json("POST", f"/api/documents/{document_id}/understanding-runs",
                json={"target_journal": target_journal,
                      "special_requirements": special_requirements})
            p2a = self._poll(f"/api/understanding-runs/{created['id']}")
            self.artifacts["p2a"] = p2a
            path = self._save("p2a", p2a)
            self._record_stage("p2a", p2a, p2a_started, document_id, source_bundle_id,
                               document_id, created["id"], path)
            active_stage = None
            if p2a["status"] != "completed":
                overall_status = p2a["status"]
                return self._ledger(paper_id, actual_sha, document_id, source_bundle_id,
                                    overall_status, execution_paper_id)

            stage_specs = [
                ("p2b", f"/api/understanding-runs/{created['id']}/professional-review-runs",
                 "/api/professional-review-runs/{id}"),
                ("p2c", None, "/api/candidate-consolidation-runs/{id}"),
                ("p3", None, "/api/evidence-verification-runs/{id}"),
            ]
            previous_id = created["id"]
            for stage, create_path, get_template in stage_specs:
                if not self._can_start_stage():
                    overall_status = "budget_exceeded"
                    return self._ledger(paper_id, actual_sha, document_id, source_bundle_id,
                                        overall_status, execution_paper_id)
                if stage == "p2c":
                    create_path = f"/api/professional-review-runs/{previous_id}/consolidation-runs"
                elif stage == "p3":
                    create_path = (f"/api/candidate-consolidation-runs/{previous_id}/"
                                   "evidence-verification-runs")
                stage_started = utcnow()
                active_stage, active_started, active_input_run_id = stage, stage_started, previous_id
                item = self._request_json("POST", create_path)
                result = self._poll(get_template.format(id=item["id"]))
                self.artifacts[stage] = result
                path = self._save(stage, result)
                self._record_stage(stage, result, stage_started, document_id, source_bundle_id,
                                   previous_id, item["id"], path)
                active_stage = None
                previous_id = item["id"]
                if result["status"] not in {"completed", "limited"}:
                    overall_status = result["status"]
                    return self._ledger(paper_id, actual_sha, document_id, source_bundle_id,
                                        overall_status, execution_paper_id)

            if not self._can_start_stage():
                overall_status = "budget_exceeded"
                return self._ledger(paper_id, actual_sha, document_id, source_bundle_id,
                                    overall_status, execution_paper_id)
            p4_started = utcnow()
            active_stage, active_started, active_input_run_id = "p4", p4_started, previous_id
            project = self._request_json("POST",
                f"/api/evidence-verification-runs/{previous_id}/report-projects")
            active_output_run_id = project["id"]
            author_draft_checks = {}
            for kind in ("internal", "author"):
                for language in ("zh", "en"):
                    draft = self._request_json("POST",
                        f"/api/review-report-projects/{project['id']}/drafts",
                        json={"kind": kind, "language": language})
                    p4_drafts.append(draft)
                    if kind == "author":
                        self._validate_author_draft(draft, language, self.artifacts["p3"])
                        response = self.client.get(
                            f"/api/review-report-drafts/{draft['id']}/export",
                            params={"format": "md", "final": "true"})
                        if response.status_code != 409:
                            raise ValueError("unapproved author draft allowed final export")
                        author_draft_checks[draft["id"]] = "final_export_blocked_409"
            p4_calls, usage_incomplete, usage_source = self._p4_calls(
                project["id"], p4_drafts, expected_drafts=4)
            p4 = {"status": "limited" if usage_incomplete else "completed",
                  "document_id": document_id,
                  "source_bundle_id": source_bundle_id, "p3_run_id": previous_id,
                  "project": project, "drafts": p4_drafts,
                  "author_draft_checks": author_draft_checks,
                  "usage_source": usage_source, "usage_incomplete": usage_incomplete}
            self.artifacts["p4"] = p4
            path = self._save("p4", p4)
            self._record_stage("p4", p4, p4_started, document_id, source_bundle_id,
                               previous_id, project["id"], path,
                               calls=p4_calls,
                               usage_status="incomplete" if usage_incomplete else "complete")
            active_stage = None

            audit = audit_lineage(self.artifacts)
            self.lineage_audit_passed = audit.passed
            self._save("lineage-audit", {"passed": audit.passed, "errors": audit.errors,
                                         "checks": audit.checks})
            overall_status = ("limited" if audit.passed and
                              any(stage.status == "limited" for stage in self.stages)
                              else "completed" if audit.passed else "blocked")
        except (httpx.HTTPError, TimeoutError, ValueError) as exc:
            response_detail = ""
            if isinstance(exc, httpx.HTTPStatusError):
                try:
                    response_detail = str(exc.response.json().get("detail") or "")
                except (ValueError, AttributeError):
                    response_detail = ""
            missing_credential = (isinstance(exc, httpx.HTTPStatusError) and
                                  exc.response.status_code == 503 and
                                  any(marker in response_detail.lower() for marker in
                                      ("api_key", "credential", "model")))
            error = {"status": "blocked_missing_credential" if missing_credential else "failed",
                     "error_type": type(exc).__name__,
                     "message": (f"HTTP {exc.response.status_code} during {active_stage}"
                                 if isinstance(exc, httpx.HTTPStatusError)
                                 else f"{type(exc).__name__} during {active_stage}")}
            self._save("runner-error", error)
            overall_status = error["status"]
            if active_stage and active_started:
                summaries: list[ModelCallSummary] = []
                usage_status = "not_applicable"
                limitations = ["stage did not complete because its prerequisite or execution failed"]
                if active_stage == "p4" and active_output_run_id:
                    calls, incomplete, source = self._p4_calls(
                        active_output_run_id, p4_drafts, expected_drafts=4)
                    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 502 \
                            and source != "sqlite_fallback":
                        calls.append({"stage": "p4_unobserved_failure", "status": "failed",
                                      "requested_model": "unknown", "actual_model": None,
                                      "input_tokens": None, "output_tokens": None,
                                      "duration_ms": 0, "error": "HTTPStatusError"})
                    summaries = summarize_api_calls("p4", calls)
                    self.budget_state.record(summaries)
                    usage_status = "incomplete" if incomplete else "complete"
                    limitations.append(f"P4 usage source: {source}; usage_incomplete={incomplete}")
                self.stages.append(StageRun(
                    stage=active_stage, status=overall_status,
                    execution_mode=self.execution_mode, started_at=active_started,
                    finished_at=utcnow(), document_id=document_id,
                    source_bundle_id=source_bundle_id, input_run_id=active_input_run_id,
                    output_run_id=active_output_run_id, error_type=error["error_type"],
                    model_calls=summaries, usage_status=usage_status,
                    source_limitations=limitations
                ))
        return self._ledger(paper_id, actual_sha, document_id, source_bundle_id, overall_status,
                            execution_paper_id)

    def _ledger(self, paper_id: str, pdf_sha: str, document_id: str,
                source_bundle_id: str | None, status: str,
                execution_paper_id: str | None = None) -> RunLedger:
        completed = [stage.stage for stage in self.stages if stage.status == "completed"]
        limited = [stage.stage for stage in self.stages if stage.status == "limited"]
        outstanding = list(dict.fromkeys(item for stage in self.stages
                                         for item in stage.source_limitations
                                         if stage.status == "limited"))
        ledger = RunLedger(evaluation_id=uuid.uuid4().hex, paper_id=paper_id,
            execution_paper_id=execution_paper_id,
            document_id=document_id, pdf_sha256=pdf_sha, source_bundle_id=source_bundle_id,
            started_at=self.started, finished_at=utcnow(), overall_status=status,
            stages=self.stages,
            workflow_reached_final_stage=any(stage.stage == "p4" for stage in self.stages),
            completed_stages=completed, limited_stages=limited,
            outstanding_verification_items=outstanding,
            lineage_audit_passed=self.lineage_audit_passed, cost_status="unknown")
        self._save("run-ledger", ledger.model_dump(mode="json"))
        return ledger


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one controlled P5 P1-P4 evaluation")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--paper-id", required=True)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--database", type=Path)
    parser.add_argument("--manifest", type=Path,
                        help="Evaluation manifest used to resolve historical paper ID aliases")
    parser.add_argument("--max-calls", type=int, default=90)
    parser.add_argument("--max-input-tokens", type=int, default=1_200_000)
    parser.add_argument("--max-output-tokens", type=int, default=220_000)
    parser.add_argument("--max-duration-seconds", type=int, default=10_800)
    parser.add_argument("--max-retries", type=int, default=2)
    parser.add_argument("--target-journal", default="")
    parser.add_argument("--special-requirements", default="")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    budget = PaperBudget(max_calls=args.max_calls, max_input_tokens=args.max_input_tokens,
                         max_output_tokens=args.max_output_tokens,
                         max_duration_seconds=args.max_duration_seconds,
                         max_retries=args.max_retries)
    runner = EvaluationRunner(args.base_url, args.output, budget, database=args.database)
    try:
        canonical_paper_id = args.paper_id
        if args.manifest:
            manifest = EvaluationManifest.model_validate_json(
                args.manifest.read_text(encoding="utf-8"))
            canonical_paper_id = manifest.resolve_paper_id(args.paper_id)
        ledger = runner.run(args.paper_id, args.pdf, args.sha256,
                            args.target_journal, args.special_requirements,
                            canonical_paper_id=canonical_paper_id)
    finally:
        runner.close()
    print(ledger.model_dump_json(indent=2))
    return 0 if ledger.overall_status in {"completed", "limited"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
