import io
import json
import sqlite3
import zipfile
from types import SimpleNamespace

import fitz
import httpx
from docx import Document
from fastapi.testclient import TestClient
from openai import APIConnectionError

from backend.app import create_app
from backend.tests.test_p4_api import seeded_p4_client


class FakeOpenAI:
    def __init__(self, **kwargs):
        pass

    @property
    def chat(self):
        def create(*, model, messages):
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
                usage=SimpleNamespace(prompt_tokens=120, completion_tokens=40),
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
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
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

    monkeypatch.setenv("OPENAI_API_KEY", "fake-secret")
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


def test_transient_model_connection_is_retried_and_unknown_tokens_stay_unknown(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")

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
    assert response.status_code == 201, response.text
    calls = response.json()["model_calls"]
    assert [item["status"] for item in calls] == ["failed", "completed", "completed"]
    assert calls[0]["input_tokens"] is None and calls[0]["output_tokens"] is None
    assert calls[0]["error"] == "APIConnectionError"


def test_p4_internal_export_keeps_editable_table_and_math(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
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
    assert document.tables and document.tables[0].cell(1, 1).text == "1.3"
    with zipfile.ZipFile(io.BytesIO(word.content)) as archive:
        assert b"oMath" in archive.read("word/document.xml")
    with fitz.open(stream=pdf.content, filetype="pdf") as rendered:
        content = " ".join(page.get_text() for page in rendered)
        assert "Formula and table" in content and "1.3" in content
