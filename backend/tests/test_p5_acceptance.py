from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest
import httpx
from pydantic import ValidationError

from scripts.p5.contracts import (
    BlindReferenceSet,
    EvaluationManifest,
    ModelCallSummary,
    PaperBudget,
    PaperSpec,
    ScientificEvaluation,
    RunLedger,
    StageRun,
)
from scripts.p5.environment import prompt_inventory, public_environment_snapshot
from scripts.p5.audit_lineage import audit_lineage
from scripts.p5.run_evaluation import BudgetState, EvaluationRunner, summarize_api_calls
from scripts.p5.scientific_quality import compute_quality_metrics
from scripts.p5.run_product_acceptance import run as run_product_acceptance


NOW = datetime(2026, 11, 6, tzinfo=timezone.utc)


def test_manifest_distinguishes_execution_modes_and_contains_no_secret(tmp_path):
    prompt = tmp_path / "sample.v1.txt"
    prompt.write_text("system prompt", encoding="utf-8")
    manifest = EvaluationManifest(
        baseline_commit="c4b613f486b4db6f118dc0ef99b7c0c103676843",
        code_commit="c4b613f486b4db6f118dc0ef99b7c0c103676843",
        created_at=NOW,
        operating_system="test-os",
        python_version="3.11.15",
        node_version="22.21.1",
        schema_versions={"source_bundle": "1.0", "p4": "1.1"},
        prompt_inventory=prompt_inventory(tmp_path),
        requested_model="test-model",
        provider_label="configured_custom_endpoint",
        papers=[PaperSpec(
            paper_id="holdout-1", title="Paper", group="holdout",
            official_pdf_url="https://example.org/paper.pdf",
            pdf_sha256="a" * 64, version="v1", reference_status="frozen")],
        budgets={"holdout-1": PaperBudget(max_calls=10, max_input_tokens=100,
                                           max_output_tokens=50, max_duration_seconds=60,
                                           max_retries=1)},
    )
    payload = manifest.model_dump_json()
    assert '"fresh_model_run"' in payload
    assert "system prompt" not in payload
    assert len(manifest.prompt_inventory[0].sha256) == 64


def test_manifest_rejects_duplicate_papers_and_missing_budget():
    paper = PaperSpec(paper_id="x", title="X", group="holdout",
                      official_pdf_url="https://example.org/x.pdf",
                      pdf_sha256="b" * 64, version="v1", reference_status="frozen")
    with pytest.raises(ValidationError):
        EvaluationManifest(
            baseline_commit="c" * 40, code_commit="d" * 40, created_at=NOW,
            operating_system="os", python_version="3.11", node_version="20",
            schema_versions={}, prompt_inventory=[], requested_model="model",
            provider_label="official", papers=[paper, paper], budgets={})


@pytest.mark.parametrize(("reference_path", "summary_path"), [
    ("docs/examples/p5-holdout-1/blind-reference.json",
     "docs/examples/p5-holdout-1/run-summary.json"),
    ("docs/examples/p5-holdout-2/blind-reference.json",
     "docs/examples/p5-holdout-2/run-summary.json"),
])
def test_holdout_identity_is_consistent_across_manifest_reference_ledger_and_summary(
        reference_path, summary_path):
    manifest = EvaluationManifest.model_validate_json(Path(
        "docs/examples/p5-regression/evaluation-manifest.json").read_text(encoding="utf-8"))
    reference = BlindReferenceSet.model_validate_json(
        Path(reference_path).read_text(encoding="utf-8"))
    summary = json.loads(Path(summary_path).read_text(encoding="utf-8"))

    canonical = manifest.resolve_paper_id(summary["paper_id"])
    assert canonical == summary["canonical_paper_id"] == reference.paper_id
    ledger = RunLedger(
        evaluation_id=summary["evaluation_id"], paper_id=canonical,
        execution_paper_id=summary["paper_id"], document_id=summary["pdf_sha256"],
        pdf_sha256=summary["pdf_sha256"], source_bundle_id=summary["source_bundle_id"],
        started_at=summary["started_at"], finished_at=summary["finished_at"],
        overall_status=summary["overall_status"], stages=[])
    assert ledger.paper_id == reference.paper_id
    assert manifest.resolve_paper_id(ledger.execution_paper_id) == ledger.paper_id


def test_v1_contracts_remain_readable_after_p5_1_schema_update(tmp_path):
    paper = PaperSpec(paper_id="paper", title="Paper", group="holdout",
                      official_pdf_url="https://example.org/paper.pdf",
                      pdf_sha256="a" * 64, version="v1", reference_status="pending")
    manifest = EvaluationManifest(
        manifest_schema_version="1.0", baseline_commit="b" * 40, code_commit="c" * 40,
        created_at=NOW, operating_system="os", python_version="3.11", node_version="22",
        schema_versions={}, prompt_inventory=[], requested_model="model", provider_label="test",
        papers=[paper], budgets={"paper": PaperBudget(
            max_calls=1, max_input_tokens=1, max_output_tokens=1,
            max_duration_seconds=1, max_retries=0)})
    ledger = RunLedger(
        ledger_schema_version="1.0", evaluation_id="old", paper_id="paper",
        document_id="doc", pdf_sha256="a" * 64, source_bundle_id=None,
        started_at=NOW, finished_at=NOW, overall_status="failed", stages=[])
    assert manifest.manifest_schema_version == "1.0"
    assert ledger.ledger_schema_version == "1.0"


def test_run_ledger_preserves_unknown_usage_and_rejects_cross_document_stage():
    call = ModelCallSummary(
        stage="p2a", status="failed", requested_model="model", actual_model=None,
        input_tokens=None, output_tokens=None, duration_ms=123, error_type="TimeoutError")
    stage = StageRun(
        stage="p2a", status="partial_failed", execution_mode="fresh_model_run",
        started_at=NOW, finished_at=NOW, document_id="doc-1",
        source_bundle_id="bundle-1", input_run_id=None, output_run_id="run-1",
        model_calls=[call], artifact_path="private/p2a.json")
    ledger = RunLedger(
        evaluation_id="eval-1", paper_id="holdout-1", document_id="doc-1",
        pdf_sha256="a" * 64, source_bundle_id="bundle-1", started_at=NOW,
        finished_at=NOW, overall_status="partial_failed", stages=[stage])
    assert ledger.totals.unknown_token_calls == 1
    assert ledger.totals.known_input_tokens == 0

    bad = stage.model_copy(update={"document_id": "doc-2"})
    with pytest.raises(ValidationError):
        RunLedger(
            evaluation_id="eval-1", paper_id="holdout-1", document_id="doc-1",
            pdf_sha256="a" * 64, source_bundle_id="bundle-1", started_at=NOW,
            finished_at=NOW, overall_status="failed", stages=[bad])


def test_public_environment_snapshot_reports_presence_without_values(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "super-secret")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://private.example/v1")
    snapshot = public_environment_snapshot()
    encoded = json.dumps(snapshot)
    assert snapshot["model_api_key"] == "configured"
    assert snapshot["model_endpoint"] == "configured_custom_endpoint"
    assert "super-secret" not in encoded
    assert "private.example" not in encoded


def test_blind_reference_cannot_claim_blinding_after_viewing_system_output():
    with pytest.raises(ValidationError):
        BlindReferenceSet(
            paper_id="holdout-1", pdf_sha256="a" * 64, created_at=NOW,
            provenance="non_expert_source_grounded_reference",
            frozen_before_etpaper_output=True, etpaper_output_viewed=True,
            limitations=["not an expert gold standard"],
            issues=[{
                "reference_issue_id": "h1-r1",
                "scientific_question": "Does the evaluation prevent source-scene leakage?",
                "importance": "major", "reference_category": "clarification",
                "evidence": [{"pdf_page": 1, "object_label": "split protocol",
                              "source_note": "The grouping unit is not specified."}],
                "verification_condition": "Show scene-grouped split identifiers.",
                "current_assessment": "open_question",
            }])


@pytest.mark.parametrize("path", [
    "docs/examples/p5-holdout-1/blind-reference.json",
    "docs/examples/p5-holdout-2/blind-reference.json",
])
def test_frozen_holdout_reference_files_validate_and_are_blind(path):
    reference = BlindReferenceSet.model_validate_json(Path(path).read_text(encoding="utf-8"))
    assert reference.frozen_before_etpaper_output is True
    assert reference.etpaper_output_viewed is False
    assert reference.provenance == "non_expert_source_grounded_reference"
    assert len(reference.issues) >= 5


@pytest.mark.parametrize("path", [
    "docs/examples/p5-holdout-1/run-summary.json",
    "docs/examples/p5-holdout-2/run-summary.json",
])
def test_public_holdout_run_summaries_are_explicitly_partial_and_sanitized(path):
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    encoded = json.dumps(payload).lower()
    assert payload["execution_mode"] == "fresh_model_run"
    assert payload["evaluation_id"]
    assert payload["started_at"] < payload["finished_at"]
    assert payload["source_bundle_id"].startswith("sb_")
    assert payload["overall_status"] == "blocked_missing_credential"
    assert payload["stages"]["p1"] == "completed"
    assert payload["stages"]["p2a"] == "blocked_missing_credential"
    assert payload["model_usage"]["calls"] == 0
    assert payload["p1"]["pages_with_unverified_reading_order"] > 0
    for forbidden in ("api_key", "authorization", "raw_request", "raw_response",
                      "base_url", "endpoint_host"):
        assert forbidden not in encoded


def test_budget_counts_unknown_usage_as_a_call_and_stops_before_next_stage():
    budget = PaperBudget(max_calls=2, max_input_tokens=100, max_output_tokens=50,
                         max_duration_seconds=60, max_retries=0)
    state = BudgetState(budget)
    calls = summarize_api_calls("p2a", [
        {"status": "completed", "requested_model": "m", "actual_model": "m",
         "input_tokens": 80, "output_tokens": 20, "duration_ms": 100},
        {"status": "failed", "requested_model": "m", "actual_model": None,
         "input_tokens": None, "output_tokens": None, "duration_ms": 200,
         "error": "TimeoutError: provider detail"},
    ])
    state.record(calls)
    assert state.unknown_token_calls == 1
    assert state.can_start_stage() is False
    assert calls[1].error_type == "TimeoutError"
    assert calls[1].status == "failed"


def _p4_call(status="completed", input_tokens=12, output_tokens=4):
    return {"stage": "report-writing", "status": status, "requested_model": "requested",
            "actual_model": "actual" if status == "completed" else None,
            "input_tokens": input_tokens, "output_tokens": output_tokens,
            "duration_ms": 17, "error": None if status == "completed" else "ModelError"}


def test_p4_usage_comes_from_draft_api_without_database(tmp_path):
    runner = EvaluationRunner("http://test", tmp_path, PaperBudget(
        max_calls=10, max_input_tokens=100, max_output_tokens=100,
        max_duration_seconds=30, max_retries=0))
    try:
        calls, usage_incomplete, source = runner._p4_calls(
            "project", [{"model_calls": [_p4_call()]}, {"model_calls": []}])
    finally:
        runner.close()
    assert calls == [_p4_call()]
    assert usage_incomplete is False
    assert source == "draft_api"


def test_p4_usage_falls_back_to_validated_database_path(tmp_path):
    database = tmp_path / "paper-review.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE p4_model_calls (project_id TEXT, stage TEXT, "
                   "requested_model TEXT, actual_model TEXT, input_tokens INTEGER, "
                   "output_tokens INTEGER, duration_ms INTEGER, status TEXT, error TEXT)")
        db.execute("INSERT INTO p4_model_calls VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   ("project", "report-writing", "requested", "actual", 12, 4, 17,
                    "completed", None))
    runner = EvaluationRunner("http://test", tmp_path / "out", PaperBudget(
        max_calls=10, max_input_tokens=100, max_output_tokens=100,
        max_duration_seconds=30, max_retries=0), database=database)
    try:
        calls, usage_incomplete, source = runner._p4_calls("project", [{}])
    finally:
        runner.close()
    assert len(calls) == 1
    assert usage_incomplete is False
    assert source == "sqlite_fallback"


def test_p4_usage_marks_unknown_tokens_incomplete(tmp_path):
    runner = EvaluationRunner("http://test", tmp_path, PaperBudget(
        max_calls=10, max_input_tokens=100, max_output_tokens=100,
        max_duration_seconds=30, max_retries=0))
    try:
        raw = [_p4_call(input_tokens=None, output_tokens=None)]
        calls, usage_incomplete, source = runner._p4_calls(
            "project", [{"model_calls": raw}])
    finally:
        runner.close()
    summaries = summarize_api_calls("p4", calls)
    assert usage_incomplete is True
    assert source == "draft_api"
    assert [item.status for item in summaries] == ["unknown_usage"]
    assert all(item.input_tokens is None for item in summaries)


def test_p4_usage_keeps_failed_model_call_with_unknown_cost(tmp_path):
    runner = EvaluationRunner("http://test", tmp_path, PaperBudget(
        max_calls=10, max_input_tokens=100, max_output_tokens=100,
        max_duration_seconds=30, max_retries=0))
    try:
        raw = [_p4_call(status="failed", input_tokens=None, output_tokens=None)]
        calls, usage_incomplete, source = runner._p4_calls(
            "project", [{"model_calls": raw}])
    finally:
        runner.close()
    summaries = summarize_api_calls("p4", calls)
    assert usage_incomplete is True
    assert source == "draft_api"
    assert summaries[0].status == "failed"
    assert summaries[0].error_type == "ModelError"
    assert summaries[0].input_tokens is None


def test_p4_usage_without_api_records_or_database_is_explicitly_incomplete(tmp_path):
    runner = EvaluationRunner("http://test", tmp_path, PaperBudget(
        max_calls=10, max_input_tokens=100, max_output_tokens=100,
        max_duration_seconds=30, max_retries=0))
    try:
        calls, usage_incomplete, source = runner._p4_calls("project", [{}])
    finally:
        runner.close()
    assert calls == []
    assert usage_incomplete is True
    assert source == "unavailable"


def test_runner_rejects_a_missing_explicit_database_path(tmp_path):
    with pytest.raises(ValueError, match="database path"):
        EvaluationRunner("http://test", tmp_path, PaperBudget(
            max_calls=10, max_input_tokens=100, max_output_tokens=100,
            max_duration_seconds=30, max_retries=0), database=tmp_path / "missing.sqlite3")


def test_runner_wall_clock_budget_blocks_the_next_stage(tmp_path):
    budget = PaperBudget(max_calls=10, max_input_tokens=100, max_output_tokens=100,
                         max_duration_seconds=30, max_retries=0)
    runner = EvaluationRunner("http://test", tmp_path, budget)
    try:
        runner.deadline = 0
        assert runner._can_start_stage() is False
    finally:
        runner.close()


def test_scientific_metrics_separate_valid_false_positive_and_unresolved():
    metrics = compute_quality_metrics(["A", "B", "B", "C", "D", "E"])
    assert metrics["counts"] == {"A": 1, "B": 2, "C": 1, "D": 1, "E": 1}
    assert metrics["effective_issue_numerator"] == 3
    assert metrics["effective_issue_denominator"] == 5
    assert metrics["effective_issue_ratio"] == pytest.approx(0.6)
    assert metrics["explicit_false_positive_count"] == 1
    assert metrics["unresolved_count"] == 1


def test_scientific_metrics_do_not_invent_a_rate_without_judged_issues():
    metrics = compute_quality_metrics(["D", "D"])
    assert metrics["effective_issue_denominator"] == 0
    assert metrics["effective_issue_ratio"] is None


def test_scientific_evaluation_requires_traceable_issue_evidence():
    with pytest.raises(ValidationError):
        ScientificEvaluation(
            paper_id="paper", pdf_sha256="a" * 64,
            evaluator_provenance="codex_non_expert_review", reference_scope="none",
            judgments=[{
                "issue_id": "issue-1", "question": "Is this sufficiently specific to test?",
                "judgment": "D", "source_result_execution_mode": "recorded_model_replay",
                "evidence_refs": [], "pdf_pages": [], "rationale": "No source was supplied.",
                "factual_accuracy": "unassessed", "evidence_support": "unverified",
                "scientific_value": "unassessed", "actionable": None,
            }], p3_conservatism=[], limitations=["Non-expert review"])


@pytest.mark.parametrize(("path", "evidence_map"), [
    ("docs/examples/p5-regression/shaw2018-scientific-evaluation.json",
     "docs/examples/p2b-shaw2018/evidence_mapping.json"),
    ("docs/examples/p5-regression/distilbert2019-scientific-evaluation.json",
     "docs/examples/p2b-distilbert2019/evidence_mapping.json"),
    ("docs/examples/p5-regression/fasttext2016-scientific-evaluation.json",
     "docs/examples/p2b-fasttext2016/evidence_mapping.json"),
])
def test_historical_scientific_evaluations_validate_and_disclose_replay(path, evidence_map):
    result = ScientificEvaluation.model_validate_json(Path(path).read_text(encoding="utf-8"))
    source_ids = set(json.loads(Path(evidence_map).read_text(encoding="utf-8")))
    assert result.evaluator_provenance == "codex_non_expert_review"
    assert result.judgments
    assert all(item.source_result_execution_mode == "recorded_model_replay"
               for item in result.judgments)
    assert all(source_id in source_ids for item in result.judgments
               for source_id in item.evidence_refs)
    assert any("not a fresh P5 model run" in limitation for limitation in result.limitations)


def test_p5_product_check_enforces_approval_and_emits_real_exports(tmp_path):
    result = run_product_acceptance(tmp_path / "product-check")
    assert result["execution_mode"] == "deterministic_regression"
    assert result["preapproval_final_export_status"] == 409
    assert result["post_edit_approval_status"] == "pending"
    assert result["post_edit_final_export_status"] == 409
    assert result["exports"]["docx_bytes"] > 10_000
    assert result["exports"]["pdf_bytes"] > 1_000
    assert result["exports"]["pdf_contains_report_title"] is True
    assert result["exports"]["pdf_contains_internal_evidence_id"] is False
    assert result["chinese_draft_check"] == {
        "docx_contains_chinese": True,
        "docx_has_editable_table": True,
        "pdf_contains_chinese": True,
        "pdf_contains_table_value": True,
        "draft_marker_present": True,
    }


def test_public_product_summary_discloses_fixture_and_layout_failure():
    path = Path("docs/examples/p5-regression/product-check-summary.json")
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["execution_mode"] == "deterministic_regression"
    assert "not a real model-quality run" in payload["fixture_scope"]
    assert payload["approval_controls"]["preapproval_final_export_http_status"] == 409
    assert payload["chinese_math_table_draft"]["visual_status"].startswith("failed_")
    encoded = json.dumps(payload).lower()
    assert "api_key" not in encoded and "authorization" not in encoded


def test_public_evaluation_manifest_is_frozen_and_secret_free():
    path = Path("docs/examples/p5-regression/evaluation-manifest.json")
    manifest = EvaluationManifest.model_validate_json(path.read_text(encoding="utf-8"))
    assert manifest.baseline_commit == "c4b613f486b4db6f118dc0ef99b7c0c103676843"
    assert len(manifest.papers) == 5
    assert len([paper for paper in manifest.papers if paper.group == "holdout"]) == 2
    assert manifest.actual_model is None
    assert manifest.chatgpt_benchmark_status == "pending"
    assert manifest.run_parameters["fresh_model_execution"] is False
    assert manifest.run_parameters["fresh_model_blocker"] == "model_credential_not_configured"
    encoded = path.read_text(encoding="utf-8").lower()
    for forbidden in ("api_key", "authorization", "bearer ", "private endpoint"):
        assert forbidden not in encoded


def test_lineage_audit_rejects_cross_document_and_unknown_evidence():
    artifacts = {
        "p1": {"document": {"id": "doc-a", "sha256": "a" * 64,
                            "snapshot_id": "snap-a"},
               "source_bundle": {"bundle_id": "bundle-a",
                    "document": {"id": "doc-a", "sha256": "a" * 64,
                                 "snapshot_id": "snap-a"},
                    "body": [{"evidence_id": "ev-1"}], "reading_units": []}},
        "p2a": {"document_id": "doc-b", "source_bundle_id": "bundle-a",
                "claim_evidence_map": {"evidence_refs": [
                    {"source_ref_id": "missing-evidence"}]}},
    }
    report = audit_lineage(artifacts)
    assert report.passed is False
    assert any("document_id" in error for error in report.errors)
    assert any("missing-evidence" in error for error in report.errors)


def test_evaluation_runner_executes_frozen_api_chain_and_writes_ledger(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\nfixture")
    import hashlib
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    calls = [{"stage": "x", "status": "completed", "requested_model": "m",
              "actual_model": "m", "input_tokens": 1, "output_tokens": 1,
              "duration_ms": 1, "error": None}]

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        responses = {
            ("POST", "/api/documents"): {"id": digest, "sha256": digest,
                "snapshot_id": "snap", "filename": "paper.pdf", "page_count": 1},
            ("GET", f"/api/documents/{digest}/source-bundle"): {
                "bundle_id": "bundle", "document": {"id": digest, "sha256": digest,
                    "snapshot_id": "snap"}, "body": [{"id": "ev-1", "evidence_id": "ev-1"}],
                "reading_units": []},
            ("GET", f"/api/documents/{digest}/parse-quality"): {"status": "needs_review"},
            ("POST", f"/api/documents/{digest}/understanding-runs"): {"id": "p2a"},
            ("GET", "/api/understanding-runs/p2a"): {"id": "p2a", "status": "completed",
                "document_id": digest, "source_bundle_id": "bundle", "model_calls": calls,
                "claim_evidence_map": {"evidence_refs": [{"source_ref_id": "ev-1"}]}},
            ("POST", "/api/understanding-runs/p2a/professional-review-runs"): {"id": "p2b"},
            ("GET", "/api/professional-review-runs/p2b"): {"id": "p2b", "status": "limited",
                "document_id": digest, "source_bundle_id": "bundle", "model_calls": calls,
                "source_limitations": ["methods coverage requires review"],
                "candidates": [{"issue_id": "candidate-1", "source_refs": [
                    {"source_id": "ev-1"}]}]},
            ("POST", "/api/professional-review-runs/p2b/consolidation-runs"): {"id": "p2c"},
            ("GET", "/api/candidate-consolidation-runs/p2c"): {"id": "p2c",
                "status": "limited", "document_id": digest, "source_bundle_id": "bundle",
                "source_limitations": ["candidate coverage remains limited"],
                "model_calls": calls, "result": {"lineage": [
                    {"original_issue_id": "candidate-1"}], "verification_tasks": [
                    {"verification_task_id": "task-1", "original_candidate_ids": ["candidate-1"],
                     "source_refs_to_check": [{"source_id": "ev-1"}]}]}},
            ("POST", "/api/candidate-consolidation-runs/p2c/evidence-verification-runs"): {
                "id": "p3"},
            ("GET", "/api/evidence-verification-runs/p3"): {"id": "p3", "status": "limited",
                "document_id": digest, "source_bundle_id": "bundle", "model_calls": calls,
                "source_limitations": ["table relation remains unverified"],
                "tasks": [{"verification_task_id": "task-1", "source_refs": ["ev-1"]}]},
            ("POST", "/api/evidence-verification-runs/p3/report-projects"): {
                "id": "p4", "document_id": digest},
        }
        if request.method == "POST" and path == "/api/review-report-projects/p4/drafts":
            payload = json.loads(request.content)
            kind, language = payload["kind"], payload["language"]
            return httpx.Response(201, json={
                "id": f"draft-{kind}-{language}", "kind": kind, "language": language,
                "approval_status": "pending", "selected_issue_ids": [],
                "plan": {"report_kind": kind, "language": language,
                         "selected_issue_ids": [], "groups": []},
                "body": "There are currently no automatically admitted comments for authors."
                        if kind == "author" else "Internal report",
                "model_calls": [_p4_call()],
            })
        if request.method == "GET" and path.startswith("/api/review-report-drafts/draft-author-"):
            return httpx.Response(409, json={"detail": "not approved"})
        value = responses.get((request.method, path))
        return httpx.Response(200 if value is not None else 404,
                              json=value or {"detail": "missing fixture"})

    client = httpx.Client(base_url="http://test", transport=httpx.MockTransport(handler))
    budget = PaperBudget(max_calls=20, max_input_tokens=100, max_output_tokens=100,
                         max_duration_seconds=30, max_retries=0)
    runner = EvaluationRunner("http://test", tmp_path / "out", budget,
                              poll_seconds=0, client=client)
    try:
        ledger = runner.run("paper", pdf, digest)
    finally:
        runner.close()
    assert ledger.overall_status == "limited"
    assert [stage.stage for stage in ledger.stages] == ["p1", "p2a", "p2b", "p2c", "p3", "p4"]
    assert {stage.execution_mode for stage in ledger.stages} == {"fresh_model_run"}
    assert ledger.workflow_reached_final_stage is True
    assert ledger.lineage_audit_passed is True
    assert ledger.limited_stages == ["p2b", "p2c", "p3"]
    assert ledger.completed_stages == ["p1", "p2a", "p4"]
    assert len(ledger.outstanding_verification_items) == 3
    assert ledger.totals.model_calls == 8
    assert ledger.usage_incomplete is False
    p4_artifact = json.loads((tmp_path / "out" / "p4.json").read_text(encoding="utf-8"))
    assert {(draft["kind"], draft["language"]) for draft in p4_artifact["drafts"]} == {
        ("internal", "zh"), ("internal", "en"), ("author", "zh"), ("author", "en")}
    assert p4_artifact["author_draft_checks"] == {
        "draft-author-zh": "final_export_blocked_409",
        "draft-author-en": "final_export_blocked_409",
    }
    assert (tmp_path / "out" / "lineage-audit.json").exists()


def test_author_draft_validation_preserves_empty_draft_and_source_mapping(tmp_path):
    runner = EvaluationRunner("http://test", tmp_path, PaperBudget(
        max_calls=10, max_input_tokens=100, max_output_tokens=100,
        max_duration_seconds=30, max_retries=0))
    try:
        empty = {"id": "empty", "kind": "author", "language": "zh",
                 "approval_status": "pending", "selected_issue_ids": [],
                 "plan": {"report_kind": "author", "language": "zh",
                          "selected_issue_ids": [], "groups": []},
                 "body": "目前没有自动准入的正式审稿问题。", "model_calls": []}
        selected = {"id": "selected", "kind": "author", "language": "en",
                    "approval_status": "pending", "selected_issue_ids": ["issue-1"],
                    "plan": {"report_kind": "author", "language": "en",
                             "selected_issue_ids": ["issue-1"], "groups": [{
                                 "source_issue_ids": ["issue-1"]}]},
                    "body": "Could the authors clarify the evidence for this scoped claim?",
                    "model_calls": [_p4_call()]}
        p3 = {"result": {"adjudications": [{"consolidated_issue_id": "issue-1",
               "verdict": "concern_narrowed",
               "report_eligibility": "eligible_with_qualification"}]}}
        runner._validate_author_draft(empty, "zh", p3)
        runner._validate_author_draft(selected, "en", p3)
    finally:
        runner.close()


def test_author_draft_validation_rejects_unverified_accusation_or_fake_approval(tmp_path):
    runner = EvaluationRunner("http://test", tmp_path, PaperBudget(
        max_calls=10, max_input_tokens=100, max_output_tokens=100,
        max_duration_seconds=30, max_retries=0))
    draft = {"id": "draft", "kind": "author", "language": "en",
             "approval_status": "approved", "selected_issue_ids": ["issue-1"],
             "plan": {"report_kind": "author", "language": "en",
                      "selected_issue_ids": ["issue-1"],
                      "groups": [{"source_issue_ids": ["issue-1"]}]},
             "body": "The authors made a confirmed error.", "model_calls": []}
    p3 = {"result": {"adjudications": [{"consolidated_issue_id": "issue-1",
           "verdict": "inconclusive", "report_eligibility": "requires_human_review"}]}}
    try:
        with pytest.raises(ValueError, match="approval"):
            runner._validate_author_draft(draft, "en", p3)
        draft["approval_status"] = "pending"
        with pytest.raises(ValueError, match="eligible"):
            runner._validate_author_draft(draft, "en", p3)
    finally:
        runner.close()


def test_evaluation_runner_records_missing_credential_as_blocked_stage(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4\nfixture")
    import hashlib
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if (request.method, path) == ("POST", "/api/documents"):
            return httpx.Response(200, json={"id": digest, "sha256": digest,
                                             "snapshot_id": "snap"})
        if (request.method, path) == ("GET", f"/api/documents/{digest}/source-bundle"):
            return httpx.Response(200, json={"bundle_id": "bundle", "document": {
                "id": digest, "sha256": digest, "snapshot_id": "snap"},
                "body": [{"evidence_id": "ev-1"}], "reading_units": []})
        if (request.method, path) == ("GET", f"/api/documents/{digest}/parse-quality"):
            return httpx.Response(200, json={"status": "needs_review"})
        if (request.method, path) == ("POST", f"/api/documents/{digest}/understanding-runs"):
            return httpx.Response(503, json={"detail": "model credential unavailable"})
        return httpx.Response(404, json={"detail": "missing fixture"})

    client = httpx.Client(base_url="http://test", transport=httpx.MockTransport(handler))
    budget = PaperBudget(max_calls=20, max_input_tokens=100, max_output_tokens=100,
                         max_duration_seconds=30, max_retries=0)
    runner = EvaluationRunner("http://test", tmp_path / "out", budget,
                              poll_seconds=0, client=client)
    try:
        ledger = runner.run("paper", pdf, digest)
    finally:
        runner.close()

    assert ledger.overall_status == "blocked_missing_credential"
    assert [stage.stage for stage in ledger.stages] == ["p1", "p2a"]
    assert ledger.stages[1].status == "blocked_missing_credential"
    assert ledger.stages[1].error_type == "HTTPStatusError"
    assert {stage.execution_mode for stage in ledger.stages} == {"fresh_model_run"}
