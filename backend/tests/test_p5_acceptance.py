from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from scripts.p5.contracts import (
    EvaluationManifest,
    ModelCallSummary,
    PaperBudget,
    PaperSpec,
    RunLedger,
    StageRun,
)
from scripts.p5.environment import prompt_inventory, public_environment_snapshot


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
