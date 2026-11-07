import io
import json
import sqlite3
import zipfile
from types import SimpleNamespace

import fitz
import httpx
import pytest
from docx import Document
from fastapi.testclient import TestClient
from openai import APIConnectionError

import backend.app as app_module
from backend.app import create_app
from backend.p4_budget import P4BudgetStateError, P4BudgetStore
from backend.tests.test_p4_api import seeded_p4_client
from backend.tests.model_test_config import configure_test_model


class FakeOpenAI:
    def __init__(self, **kwargs):
        pass

    @property
    def chat(self):
        def create(*, model, messages, max_tokens=None, timeout=None):
            payload = json.loads(messages[1]["content"])
            if "report" in payload:
                content = {"acceptable": True, "unsupported_facts": [],
                           "state_promotions": [], "refuted_accusations": [],
                           "language_or_scope_changes": []}
            else:
                plan = payload["plan"]
                issues = {item["source_issue_id"]: item for item in
                          payload["evidence_pack"]["issues"]}
                content = {"schema_version": "1.0",
                    "prompt_version": f'review-comment-writing-{plan["language"]}/v1',
                    "language": plan["language"], "report_kind": plan["report_kind"],
                    "general_assessment": "The authors describe a method requiring careful scope clarification.",
                    "comments": [{"report_comment_id": group["group_id"],
                        "source_issue_ids": group["source_issue_ids"], "source": "p3",
                        "title_zh": "方法主张范围", "title_en": "Scope of the method claim",
                        "comment_body": "The paper describes the method and novelty claim in PDF page 1. "
                            "Could the authors clarify which part of the method is new and how the claim is bounded?",
                        "scientific_question": group["scientific_question"],
                        "evidence_refs": issues[group["source_issue_ids"][0]]["source_refs"][:1],
                        "author_action_requested": "Clarify the method's specific novelty scope.",
                        "comment_category": group["category"],
                        "uncertainty_qualification": "A clarification, not a proven error",
                        "reviewer_approval_status": "pending"} for group in plan["groups"]]}
            return SimpleNamespace(model="actual-mock",
                usage=SimpleNamespace(prompt_tokens=120, completion_tokens=40,
                                      total_tokens=160),
                choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(content)))])
        return SimpleNamespace(completions=SimpleNamespace(create=create))


def test_empty_author_draft_is_saved_without_model_or_filler(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client, _ = seeded_p4_client(tmp_path)
    project = client.post("/api/evidence-verification-runs/v1/report-projects").json()
    url = f'/api/review-report-projects/{project["id"]}/drafts'
    draft = client.post(url, json={"kind": "author", "language": "zh"})
    assert draft.status_code == 201, draft.text
    assert draft.json()["selected_issue_ids"] == []
    assert draft.json()["model_calls"] == []
    assert "没有自动准入" in draft.json()["body"]
    draft_id = draft.json()["id"]
    assert client.put(f"/api/review-report-drafts/{draft_id}", json={
        "body": draft.json()["body"] + "\n新增未经核验的主张", "expected_revision": 1}).status_code == 400
    assert "DRAFT" in client.get(f"/api/review-report-drafts/{draft_id}/export?format=md").text
    assert client.get(f"/api/review-report-drafts/{draft_id}/export?format=md&final=true").status_code == 409


def test_versioned_edit_approval_and_real_exports(tmp_path, monkeypatch):
    configure_test_model(monkeypatch, key="test-secret")
    monkeypatch.setattr("backend.app.OpenAI", FakeOpenAI)
    client, _ = seeded_p4_client(tmp_path, reportable=True)
    project = client.post("/api/evidence-verification-runs/v1/report-projects").json()
    url = f'/api/review-report-projects/{project["id"]}/drafts'
    first = client.post(url, json={"kind": "author", "language": "en"})
    assert first.status_code == 201, first.text
    draft = first.json()
    draft_id = draft["id"]
    assert len(draft["model_calls"]) == 2
    assert all(item["actual_model"] == "actual-mock" for item in draft["model_calls"])
    assert "test-secret" not in str(draft)
    with sqlite3.connect(tmp_path / "review.db") as db:
        assert db.execute("SELECT count(*) FROM p4_model_calls").fetchone()[0] == 2
        assert db.execute("SELECT count(*) FROM evidence_verification_model_calls").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM draft_model_calls").fetchone()[0] == 0
    assert draft["approval_status"] == "pending"
    assert client.get(f"/api/review-report-drafts/{draft_id}/export?format=md&final=true").status_code == 409


    second = client.post(url, json={"kind": "author", "language": "en"})
    assert second.status_code == 201 and second.json()["id"] != draft_id
    assert client.get(f"/api/review-report-drafts/{draft_id}").json()["body"] == draft["body"]
    body = draft["body"].replace("which part of the method is new",
                                 "which specific part of the method is new")
    updated = client.put(f"/api/review-report-drafts/{draft_id}", json={
        "body": body, "expected_revision": 1})
    assert updated.status_code == 200, updated.text
    assert updated.json()["revision_number"] == 2
    assert updated.json()["approval_status"] == "pending"
    assert client.put(f"/api/review-report-drafts/{draft_id}", json={
        "body": body + "\n", "expected_revision": 1}).status_code == 409
    assert client.post(f"/api/review-report-drafts/{draft_id}/approve", json={
        "expected_revision": 2, "confirm_content": True}).status_code == 409
    issue = project["issues"][0]
    decision = client.patch(f'/api/review-report-projects/{project["id"]}/issues/'
        f'{issue["consolidated_issue_id"]}/decision', json={
            "decision": "approve_for_final", "reviewer_rationale": "The clarification is source-grounded",
            "selected_source_refs": issue["source_refs"][:1]})
    assert decision.status_code == 200, decision.text
    approved = client.post(f"/api/review-report-drafts/{draft_id}/approve", json={
        "expected_revision": 2, "confirm_content": True})
    assert approved.status_code == 200, approved.text
    assert approved.json()["approval_status"] == "approved"
    markdown = client.get(f"/api/review-report-drafts/{draft_id}/export?format=md&final=true")
    assert markdown.status_code == 200 and "draft" not in markdown.text.casefold()
    word = client.get(f"/api/review-report-drafts/{draft_id}/export?format=docx&final=true")
    pdf = client.get(f"/api/review-report-drafts/{draft_id}/export?format=pdf&final=true")
    assert word.status_code == pdf.status_code == 200, (word.text[:200], pdf.text[:200])
    document = Document(io.BytesIO(word.content))
    assert any("Scope of the method claim" in p.text for p in document.paragraphs)
    with fitz.open(stream=pdf.content, filetype="pdf") as rendered:
        assert rendered.page_count >= 1
        assert "Scope of the method claim" in " ".join(page.get_text() for page in rendered)
    changed = client.put(f"/api/review-report-drafts/{draft_id}", json={
        "body": body + "\nA further clarification is welcome.\n", "expected_revision": 2})
    assert changed.status_code == 200
    assert changed.json()["approval_status"] == "pending"
    assert client.get(f"/api/review-report-drafts/{draft_id}/export?format=md&final=true").status_code == 409


def test_export_failure_preserves_saved_draft_and_model_failure_is_recorded(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client, _ = seeded_p4_client(tmp_path)
    project = client.post("/api/evidence-verification-runs/v1/report-projects").json()
    draft = client.post(f'/api/review-report-projects/{project["id"]}/drafts',
                        json={"kind": "author", "language": "en"}).json()
    monkeypatch.setattr("backend.p4_drafts_api.render_export",
                        lambda *_: (_ for _ in ()).throw(RuntimeError("converter unavailable")))
    failed = client.get(f'/api/review-report-drafts/{draft["id"]}/export?format=pdf')
    assert failed.status_code == 500
    assert client.get(f'/api/review-report-drafts/{draft["id"]}').json()["body"] == draft["body"]

    class BrokenOpenAI:
        def __init__(self, **kwargs):
            pass

        @property
        def chat(self):
            def create(**kwargs):
                raise TimeoutError("model unavailable")
            return SimpleNamespace(completions=SimpleNamespace(create=create))

    configure_test_model(monkeypatch, key="fake-secret")
    monkeypatch.setattr("backend.app.OpenAI", BrokenOpenAI)
    (tmp_path / "other").mkdir()
    other_client, _ = seeded_p4_client(tmp_path / "other", reportable=True)
    other_project = other_client.post("/api/evidence-verification-runs/v1/report-projects").json()
    response = other_client.post(f'/api/review-report-projects/{other_project["id"]}/drafts',
        json={"kind": "author", "language": "en"})
    assert response.status_code == 502
    assert "fake-secret" not in response.text
    with sqlite3.connect(tmp_path / "other" / "review.db") as db:
        assert db.execute("SELECT count(*) FROM p4_drafts").fetchone()[0] == 0
        call = db.execute("SELECT status, input_tokens, error FROM p4_model_calls").fetchone()
        assert call == ("failed", None, "TimeoutError")


def test_p4_draft_api_requires_authentication(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_REVIEW_ACCESS_TOKEN", "private")
    client = TestClient(create_app(tmp_path))
    assert client.post("/api/evidence-verification-runs/v1/report-projects").status_code == 401
    assert client.get("/api/review-report-drafts/nope").status_code == 401
    assert client.post("/api/auth/login", json={"access_token": "private"}).status_code == 200
    assert client.get("/api/review-report-drafts/nope").status_code == 404


def test_missing_p4_budget_fails_before_client_or_provider_request(tmp_path, monkeypatch):
    configure_test_model(monkeypatch, key="test-secret")
    monkeypatch.delenv("P4_MAX_PLANNED_COST_USD")
    constructions = []

    class RecordingOpenAI:
        def __init__(self, **kwargs):
            constructions.append(kwargs)

    monkeypatch.setattr("backend.app.OpenAI", RecordingOpenAI)
    client, _ = seeded_p4_client(tmp_path, reportable=True)
    project = client.post("/api/evidence-verification-runs/v1/report-projects").json()

    response = client.post(
        f'/api/review-report-projects/{project["id"]}/drafts',
        json={"kind": "author", "language": "en"},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["variables"] == ["P4_MAX_PLANNED_COST_USD"]
    assert constructions == []
    with sqlite3.connect(tmp_path / "review.db") as db:
        run = db.execute("SELECT state, request_count FROM p4_generation_runs").fetchone()
    assert run == ("configuration_failed", 0)

    runs = client.get(
        f'/api/review-report-projects/{project["id"]}/generation-runs'
    )
    assert runs.status_code == 200
    view = runs.json()[0]
    assert view["state"] == "configuration_failed"
    assert view["attempted_requests"] == 0
    assert view["provider_billed_cost_usd"] is None
    assert view["has_validated_partial_output"] is False
    detail = client.get(
        f'/api/review-report-generation-runs/{view["generation_run_id"]}'
    )
    assert detail.status_code == 200 and detail.json() == view
    serialized = json.dumps(view)
    for forbidden in ("api_key", "base_url", "budget_json", "test-secret",
                      "validated_partial_json", "raw_response", "prompt"):
        assert forbidden not in serialized.casefold()


def test_content_repair_consumes_a_new_durable_budget_reservation(tmp_path, monkeypatch):
    configure_test_model(monkeypatch, key="test-secret")

    class RepairingOpenAI(FakeOpenAI):
        calls = 0

        @property
        def chat(self):
            delegate = super().chat

            def create(**kwargs):
                RepairingOpenAI.calls += 1
                response = delegate.completions.create(**kwargs)
                if RepairingOpenAI.calls == 1:
                    response.choices[0].message.content = "{}"
                return response

            return SimpleNamespace(completions=SimpleNamespace(create=create))

    monkeypatch.setattr("backend.app.OpenAI", RepairingOpenAI)
    client, _ = seeded_p4_client(tmp_path, reportable=True)
    project = client.post("/api/evidence-verification-runs/v1/report-projects").json()

    response = client.post(
        f'/api/review-report-projects/{project["id"]}/drafts',
        json={"kind": "author", "language": "en"},
    )

    assert response.status_code == 201, response.text
    assert RepairingOpenAI.calls == 3
    with sqlite3.connect(tmp_path / "review.db") as db:
        stages = [row[0] for row in db.execute(
            "SELECT stage FROM p4_model_calls ORDER BY rowid"
        )]
        sidecars = db.execute("SELECT count(*) FROM p4_model_call_budget").fetchone()[0]
        run = db.execute("SELECT state, request_count, input_tokens, output_tokens "
                         "FROM p4_generation_runs").fetchone()
    assert stages == [
        "writing-1-attempt-1", "writing-1-attempt-2", "consistency-1-attempt-2"
    ]
    assert sidecars == 3
    assert run == ("completed", 3, 360, 120)


@pytest.mark.parametrize("method_name", ["reserve_request", "mark_dispatch_pending"])
def test_persistence_failure_before_dispatch_produces_zero_sdk_calls(
        tmp_path, monkeypatch, method_name):
    configure_test_model(monkeypatch, key="test-secret")
    sdk_calls = []

    class RecordingOpenAI(FakeOpenAI):
        @property
        def chat(self):
            delegate = super().chat

            def create(**kwargs):
                sdk_calls.append(kwargs)
                return delegate.completions.create(**kwargs)

            return SimpleNamespace(completions=SimpleNamespace(create=create))

    def fail_persistence(*args, **kwargs):
        raise P4BudgetStateError("injected_persistence_failure")

    monkeypatch.setattr(app_module, "OpenAI", RecordingOpenAI)
    monkeypatch.setattr(P4BudgetStore, method_name, fail_persistence)
    client, _ = seeded_p4_client(tmp_path, reportable=True)
    project = client.post("/api/evidence-verification-runs/v1/report-projects").json()

    response = client.post(
        f'/api/review-report-projects/{project["id"]}/drafts',
        json={"kind": "author", "language": "en"},
    )

    assert response.status_code == 502
    assert response.json()["detail"]["blockers"] == ["injected_persistence_failure"]
    assert sdk_calls == []


def test_settlement_failure_recovers_as_unknown_usage_without_replay(tmp_path, monkeypatch):
    configure_test_model(monkeypatch, key="test-secret")
    sdk_calls = []

    class RecordingOpenAI(FakeOpenAI):
        @property
        def chat(self):
            delegate = super().chat

            def create(**kwargs):
                sdk_calls.append(kwargs)
                return delegate.completions.create(**kwargs)

            return SimpleNamespace(completions=SimpleNamespace(create=create))

    def fail_settlement(*args, **kwargs):
        raise P4BudgetStateError("injected_settlement_failure")

    monkeypatch.setattr(app_module, "OpenAI", RecordingOpenAI)
    monkeypatch.setattr(P4BudgetStore, "settle_valid_usage", fail_settlement)
    client, _ = seeded_p4_client(tmp_path, reportable=True)
    project = client.post("/api/evidence-verification-runs/v1/report-projects").json()

    response = client.post(
        f'/api/review-report-projects/{project["id"]}/drafts',
        json={"kind": "author", "language": "en"},
    )

    assert response.status_code == 502
    assert len(sdk_calls) == 1
    with sqlite3.connect(tmp_path / "review.db") as db:
        assert db.execute("SELECT dispatch_state FROM p4_model_call_budget").fetchone()[0] == \
            "dispatch_pending"

    TestClient(create_app(tmp_path))
    assert len(sdk_calls) == 1
    with sqlite3.connect(tmp_path / "review.db") as db:
        run = db.execute("SELECT state, reason, unknown_usage_calls, cost_complete "
                         "FROM p4_generation_runs").fetchone()
        call = db.execute("SELECT dispatch_state, usage_status "
                          "FROM p4_model_call_budget").fetchone()
    assert run == ("interrupted", "interrupted_after_dispatch", 1, 0)
    assert call == ("usage_unknown", "unknown")


def test_missing_provider_usage_stops_after_one_request_and_marks_cost_incomplete(
        tmp_path, monkeypatch):
    configure_test_model(monkeypatch, key="test-secret")
    sdk_calls = []

    class MissingUsageOpenAI(FakeOpenAI):
        @property
        def chat(self):
            delegate = super().chat

            def create(**kwargs):
                sdk_calls.append(kwargs)
                response = delegate.completions.create(**kwargs)
                response.usage = None
                return response

            return SimpleNamespace(completions=SimpleNamespace(create=create))

    monkeypatch.setattr(app_module, "OpenAI", MissingUsageOpenAI)
    client, _ = seeded_p4_client(tmp_path, reportable=True)
    project = client.post("/api/evidence-verification-runs/v1/report-projects").json()

    response = client.post(
        f'/api/review-report-projects/{project["id"]}/drafts',
        json={"kind": "author", "language": "en"},
    )

    assert response.status_code == 502
    assert response.json()["detail"]["blockers"] == ["p4_usage_missing"]
    assert len(sdk_calls) == 1
    with sqlite3.connect(tmp_path / "review.db") as db:
        run = db.execute("SELECT state, cost_complete, usage_cost_usd "
                         "FROM p4_generation_runs").fetchone()
        call = db.execute("SELECT dispatch_state, usage_status "
                          "FROM p4_model_call_budget").fetchone()
    assert run == ("usage_invalid", 0, None)
    assert call == ("usage_invalid", "invalid")


def test_valid_usage_over_budget_is_persisted_before_termination(tmp_path, monkeypatch):
    configure_test_model(monkeypatch, key="test-secret")

    class OverBudgetOpenAI(FakeOpenAI):
        @property
        def chat(self):
            delegate = super().chat

            def create(**kwargs):
                response = delegate.completions.create(**kwargs)
                response.usage = SimpleNamespace(
                    prompt_tokens=200001, completion_tokens=40, total_tokens=200041
                )
                return response

            return SimpleNamespace(completions=SimpleNamespace(create=create))

    monkeypatch.setattr(app_module, "OpenAI", OverBudgetOpenAI)
    client, _ = seeded_p4_client(tmp_path, reportable=True)
    project = client.post("/api/evidence-verification-runs/v1/report-projects").json()

    response = client.post(
        f'/api/review-report-projects/{project["id"]}/drafts',
        json={"kind": "author", "language": "en"},
    )

    assert response.status_code == 409
    assert response.json()["detail"]["blockers"] == ["p4_input_budget_exhausted"]
    with sqlite3.connect(tmp_path / "review.db") as db:
        run = db.execute("SELECT state, input_tokens, output_tokens, usage_cost_usd, "
                         "cost_complete FROM p4_generation_runs").fetchone()
        call = db.execute("SELECT input_tokens, output_tokens, status, error "
                          "FROM p4_model_calls").fetchone()
        sidecar = db.execute("SELECT usage_status, actual_total_tokens "
                             "FROM p4_model_call_budget").fetchone()
    assert run == ("budget_exhausted", 200001, 40, "0.1601608", 1)
    assert call == (200001, 40, "failed", "p4_input_budget_exhausted")
    assert sidecar == ("valid_over_budget", 200041)


def test_draft_and_completed_run_are_atomic_when_final_audit_update_fails(
        tmp_path, monkeypatch):
    configure_test_model(monkeypatch, key="test-secret")
    monkeypatch.setattr(app_module, "OpenAI", FakeOpenAI)

    def fail_completion(*args, **kwargs):
        raise P4BudgetStateError("injected_completion_failure")

    monkeypatch.setattr(P4BudgetStore, "complete_run_in_transaction", fail_completion)
    client, _ = seeded_p4_client(tmp_path, reportable=True)
    project = client.post("/api/evidence-verification-runs/v1/report-projects").json()

    response = client.post(
        f'/api/review-report-projects/{project["id"]}/drafts',
        json={"kind": "author", "language": "en"},
    )

    assert response.status_code == 502
    with sqlite3.connect(tmp_path / "review.db") as db:
        assert db.execute("SELECT count(*) FROM p4_drafts").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM p4_draft_revisions").fetchone()[0] == 0


def test_transient_model_connection_is_not_retried_and_unknown_usage_is_terminal(
        tmp_path, monkeypatch):
    configure_test_model(monkeypatch, key="test-secret")

    class FlakyOpenAI(FakeOpenAI):
        attempts = 0

        @property
        def chat(self):
            normal = super().chat

            def create(**kwargs):
                FlakyOpenAI.attempts += 1
                if FlakyOpenAI.attempts == 1:
                    raise APIConnectionError(request=httpx.Request("POST", "https://example.test"))
                return normal.completions.create(**kwargs)
            return SimpleNamespace(completions=SimpleNamespace(create=create))

    monkeypatch.setattr("backend.app.OpenAI", FlakyOpenAI)
    client, _ = seeded_p4_client(tmp_path, reportable=True)
    project = client.post("/api/evidence-verification-runs/v1/report-projects").json()
    response = client.post(f'/api/review-report-projects/{project["id"]}/drafts',
                           json={"kind": "author", "language": "en"})
    assert response.status_code == 502, response.text
    assert FlakyOpenAI.attempts == 1
    with sqlite3.connect(tmp_path / "review.db") as db:
        call = db.execute("SELECT status, input_tokens, output_tokens, error "
                          "FROM p4_model_calls").fetchone()
        run = db.execute("SELECT state, unknown_usage_calls, cost_complete "
                         "FROM p4_generation_runs").fetchone()
    assert call == ("failed", None, None, "APIConnectionError")
    assert run == ("usage_unknown", 1, 0)


def test_p4_internal_export_keeps_editable_table_and_math(tmp_path, monkeypatch):
    configure_test_model(monkeypatch, key="test-secret")
    monkeypatch.setattr("backend.app.OpenAI", FakeOpenAI)
    client, _ = seeded_p4_client(tmp_path, reportable=True)
    project = client.post("/api/evidence-verification-runs/v1/report-projects").json()
    draft_response = client.post(f'/api/review-report-projects/{project["id"]}/drafts',
                                 json={"kind": "internal", "language": "en"})
    assert draft_response.status_code == 201, draft_response.text
    draft = draft_response.json()
    body = draft["body"] + "\n## Formula and table\n\n$$x^2+y^2=z^2$$\n\n" \
           "| Item | Value |\n|:---|---:|\n| A | 1.3 |\n"
    saved = client.put(f'/api/review-report-drafts/{draft["id"]}', json={
        "body": body, "expected_revision": 1})
    assert saved.status_code == 200, saved.text
    word = client.get(f'/api/review-report-drafts/{draft["id"]}/export?format=docx')
    pdf = client.get(f'/api/review-report-drafts/{draft["id"]}/export?format=pdf')
    assert word.status_code == pdf.status_code == 200
    document = Document(io.BytesIO(word.content))
    assert document.tables
    assert any(cell.text == "1.3" for table in document.tables
               for row in table.rows for cell in row.cells)
    with zipfile.ZipFile(io.BytesIO(word.content)) as archive:
        assert b"oMath" in archive.read("word/document.xml")
    with fitz.open(stream=pdf.content, filetype="pdf") as rendered:
        content = " ".join(page.get_text() for page in rendered)
        assert "Formula and table" in content and "1.3" in content
