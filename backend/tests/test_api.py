import io
import os
import sqlite3

import fitz
from fastapi.testclient import TestClient

from backend.app import create_app


def sample_pdf():
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 80), "Abstract")
    page.insert_text((72, 120), "Cross formula verification matters")
    content = document.tobytes()
    document.close()
    return content


def test_upload_search_and_evidence_round_trip(tmp_path):
    client = TestClient(create_app(tmp_path))
    content = sample_pdf()
    response = client.post("/api/documents", files={"file": ("paper.pdf", io.BytesIO(content), "application/pdf")})
    assert response.status_code == 201
    document = response.json()
    assert document["page_count"] == 1
    assert document["sha256"]
    assert client.get(f'/api/documents/{document["id"]}/pdf').content == content

    results = client.get(f'/api/documents/{document["id"]}/search', params={"q": "verification"}).json()
    assert results and results[0]["page_index"] == 0
    evidence = client.get(f'/api/documents/{document["id"]}/evidence/{results[0]["evidence_id"]}').json()
    assert evidence["excerpt"] == results[0]["excerpt"]
    assert evidence["bbox"][0] >= 0


def test_rejects_non_pdf(tmp_path):
    client = TestClient(create_app(tmp_path))
    response = client.post("/api/documents", files={"file": ("bad.pdf", io.BytesIO(b"not a pdf"), "application/pdf")})
    assert response.status_code == 400
    assert client.get("/api/documents").json() == []


def test_same_pdf_reuses_existing_parse(tmp_path):
    client = TestClient(create_app(tmp_path))
    content = sample_pdf()
    first = client.post("/api/documents", files={"file": ("paper.pdf", content, "application/pdf")}).json()
    second = client.post("/api/documents", files={"file": ("paper.pdf", content, "application/pdf")}).json()
    assert first["id"] == second["id"]
    assert len(client.get("/api/documents").json()) == 1


def test_numbered_section_is_navigable(tmp_path):
    client = TestClient(create_app(tmp_path))
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 80), "1 Introduction")
    page.insert_text((72, 120), "A useful paper body")
    response = client.post("/api/documents", files={"file": ("paper.pdf", document.tobytes(), "application/pdf")})
    assert any(section["title"] == "1 Introduction" for section in response.json()["sections"])


def test_reupload_repairs_legacy_sections_without_changing_evidence(tmp_path):
    client = TestClient(create_app(tmp_path))
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 80), "1 Introduction")
    page.insert_text((72, 120), "A useful paper body")
    content = pdf.tobytes()
    pdf.close()
    first = client.post("/api/documents", files={"file": ("paper.pdf", content, "application/pdf")}).json()
    original_evidence = client.get(f'/api/documents/{first["id"]}/search', params={"q": "Introduction"}).json()[0]["evidence_id"]
    with sqlite3.connect(tmp_path / "review.db") as db:
        db.execute("UPDATE documents SET sections = '[]' WHERE id = ?", (first["id"],))
    repaired = client.post("/api/documents", files={"file": ("paper.pdf", content, "application/pdf")}).json()
    assert repaired["id"] == first["id"]
    assert any(section["title"] == "1 Introduction" for section in repaired["sections"])
    assert client.get(f'/api/documents/{first["id"]}/search', params={"q": "Introduction"}).json()[0]["evidence_id"] == original_evidence


def test_image_only_page_requests_ocr(tmp_path):
    client = TestClient(create_app(tmp_path))
    document = fitz.open()
    document.new_page()
    response = client.post("/api/documents", files={"file": ("scan.pdf", document.tobytes(), "application/pdf")})
    assert response.json()["pages"][0]["needs_ocr"] is True


def test_conversation_belongs_to_document_and_persists(tmp_path):
    client = TestClient(create_app(tmp_path))
    document = client.post("/api/documents", files={"file": ("paper.pdf", sample_pdf(), "application/pdf")}).json()
    response = client.post(f'/api/documents/{document["id"]}/conversations')
    assert response.status_code == 201
    conversation = response.json()
    assert conversation["document_id"] == document["id"]
    assert client.get(f'/api/documents/{document["id"]}/conversations').json()[0]["id"] == conversation["id"]
    assert client.get(f'/api/conversations/{conversation["id"]}').json()["messages"] == []


def test_chat_requires_backend_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = TestClient(create_app(tmp_path))
    document = client.post("/api/documents", files={"file": ("paper.pdf", sample_pdf(), "application/pdf")}).json()
    conversation = client.post(f'/api/documents/{document["id"]}/conversations').json()
    response = client.post(f'/api/conversations/{conversation["id"]}/messages/stream', json={"message": "Explain verification"})
    assert response.status_code == 503
    assert client.get(f'/api/conversations/{conversation["id"]}').json()["messages"] == []
