import io
import json
import sqlite3
from types import SimpleNamespace

import fitz
from docx import Document
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.tests.model_test_config import configure_test_model


def paper_and_verified_clarification(client, data_dir):
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 90), "The overall memory complexity scope is not defined.")
    data = pdf.tobytes()
    pdf.close()
    paper = client.post("/api/documents", files={"file": ("memory.pdf", io.BytesIO(data), "application/pdf")}).json()
    anchor = client.get(f'/api/documents/{paper["id"]}/search', params={"q": "memory complexity"}).json()[0]
    case = client.post("/api/review-cases", json={"mode": "first_review", "original_document_id": paper["id"]}).json()
    db = sqlite3.connect(data_dir / "review.db")
    db.execute("INSERT INTO review_runs VALUES ('run1', ?, 'completed', NULL, datetime('now'), datetime('now'))", (case["id"],))
    db.execute("INSERT INTO review_findings VALUES ('finding1', 'run1', ?, ?, ?, 'source_located', 'needs_independent_verification', 'pending', ?, ?)",
               ("Clarify memory scope", "The paper should define the memory complexity scope.",
                json.dumps([{"evidence_id": anchor["evidence_id"], "document_id": paper["id"], "page_index": 0}]),
                "The scope may refer only to representations.", "The cited statement does not define scope."))
    db.execute("INSERT INTO finding_verifications VALUES ('verify1', 'finding1', 'completed', ?, NULL, datetime('now'), datetime('now'))",
               (json.dumps({"verdict": "supported_clarification", "support_assessment": "Source supports clarification.",
                            "rebuttal": "Narrower scope is plausible.", "evidence_ids": [anchor["evidence_id"]]}),))
    db.commit()
    db.close()
    return case, anchor


def test_independent_draft_templates_and_editable_exports(tmp_path):
    client = TestClient(create_app(tmp_path))
    case, anchor = paper_and_verified_clarification(client, tmp_path)
    report = client.post(f'/api/review-cases/{case["id"]}/drafts', json={"kind": "report", "language": "zh"})
    opinion = client.post(f'/api/review-cases/{case["id"]}/drafts', json={"kind": "opinion", "language": "zh"})
    assert report.status_code == opinion.status_code == 201
    assert "内部完整审稿报告" in report.json()["body"]
    assert "正式审稿意见" in opinion.json()["body"]
    assert "候选问题核查" in report.json()["body"]
    assert "候选问题核查" not in opinion.json()["body"]
    assert anchor["evidence_id"] in opinion.json()["body"]
    assert "已证实数学错误" not in opinion.json()["body"]

    draft_id = opinion.json()["id"]
    edited = opinion.json()["body"] + "\n\n## 公式和表格\n\n$$x^2+y^2=z^2$$\n\n| 项目 | 数值 |\n|---|---:|\n| A | 1.3 |\n"
    update = client.put(f'/api/review-drafts/{draft_id}', json={"body": edited})
    assert update.status_code == 200
    assert TestClient(create_app(tmp_path)).get(f'/api/review-drafts/{draft_id}').json()["body"] == edited

    markdown = client.get(f'/api/review-drafts/{draft_id}/export?format=md')
    word = client.get(f'/api/review-drafts/{draft_id}/export?format=docx')
    pdf = client.get(f'/api/review-drafts/{draft_id}/export?format=pdf')
    assert markdown.status_code == word.status_code == pdf.status_code == 200
    assert edited.encode() == markdown.content
    assert word.content[:2] == b"PK"
    document = Document(io.BytesIO(word.content))
    assert any("正式审稿意见" in paragraph.text for paragraph in document.paragraphs)
    assert document.tables
    assert b"<m:oMath" in word.content or any("oMath" in paragraph._p.xml for paragraph in document.paragraphs)
    with fitz.open(stream=pdf.content, filetype="pdf") as rendered:
        assert rendered.page_count >= 1
        text = " ".join(page.get_text() for page in rendered)
        assert "正式审稿意见" in text
        assert "1.3" in text
        assert "Clarify memory scope" in text


def test_english_draft_uses_model_and_keeps_evidence_ids(tmp_path, monkeypatch):
    client = TestClient(create_app(tmp_path))
    case, anchor = paper_and_verified_clarification(client, tmp_path)

    class FakeCompletions:
        def create(self, *, model, messages):
            source = messages[-1]["content"]
            assert anchor["evidence_id"] in source
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=source))],
                                   usage=SimpleNamespace(prompt_tokens=100, completion_tokens=80))

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=FakeCompletions())

    configure_test_model(monkeypatch, key="test-key")
    monkeypatch.setattr("backend.app.OpenAI", FakeOpenAI)
    created = client.post(f'/api/review-cases/{case["id"]}/drafts', json={"kind": "opinion", "language": "en"})
    assert created.status_code == 201
    assert anchor["evidence_id"] in created.json()["body"]
    assert created.json()["model_calls"][0]["input_tokens"] == 100
