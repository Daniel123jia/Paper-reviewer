import io

import fitz
import pytest
from fastapi.testclient import TestClient

from backend.app import create_app


def sample_pdf():
    pdf = fitz.open()
    pdf.new_page().insert_text((72, 90), "Private paper evidence")
    data = pdf.tobytes()
    pdf.close()
    return data


def test_single_account_cookie_protects_documents_pdf_and_exports(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_REVIEW_ACCESS_TOKEN", "a" * 40)
    client = TestClient(create_app(tmp_path))
    assert client.get("/api/auth/status").json() == {"required": True, "authenticated": False}
    assert client.get("/api/documents").status_code == 401
    assert client.post("/api/auth/login", json={"access_token": "wrong"}).status_code == 401
    login = client.post("/api/auth/login", json={"access_token": "a" * 40})
    assert login.status_code == 200
    assert "httponly" in login.headers["set-cookie"].lower()
    assert client.get("/api/auth/status").json()["authenticated"] is True
    uploaded = client.post("/api/documents", files={"file": ("private.pdf", io.BytesIO(sample_pdf()), "application/pdf")}).json()
    case = client.post("/api/review-cases", json={"mode": "first_review", "original_document_id": uploaded["id"]}).json()
    draft = client.post(f'/api/review-cases/{case["id"]}/drafts', json={"kind": "report", "language": "zh"}).json()
    stranger = TestClient(client.app)
    for path in ("/api/documents", f'/api/documents/{uploaded["id"]}/pdf',
                 f'/api/review-drafts/{draft["id"]}/export?format=md'):
        assert stranger.get(path).status_code == 401
    assert client.get(f'/api/documents/{uploaded["id"]}/pdf').content.startswith(b"%PDF-")
    assert client.get(f'/api/review-drafts/{draft["id"]}/export?format=md').status_code == 200
    assert client.post("/api/auth/logout").status_code == 200
    assert client.get("/api/documents").status_code == 401


def test_predeployment_requires_strong_secret_and_secure_cookie(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_REVIEW_PREDEPLOY", "1")
    monkeypatch.setenv("PAPER_REVIEW_WORKER_COUNT", "1")
    monkeypatch.delenv("PAPER_REVIEW_ACCESS_TOKEN", raising=False)
    with pytest.raises(ValueError, match="PAPER_REVIEW_ACCESS_TOKEN"):
        create_app(tmp_path)
    monkeypatch.setenv("PAPER_REVIEW_ACCESS_TOKEN", "short")
    with pytest.raises(ValueError, match="PAPER_REVIEW_ACCESS_TOKEN"):
        create_app(tmp_path)
    monkeypatch.setenv("PAPER_REVIEW_ACCESS_TOKEN", "b" * 40)
    with pytest.raises(ValueError, match="PAPER_REVIEW_SECURE_COOKIE"):
        create_app(tmp_path)
    monkeypatch.setenv("PAPER_REVIEW_SECURE_COOKIE", "1")
    client = TestClient(create_app(tmp_path))
    response = client.post("/api/auth/login", json={"access_token": "b" * 40})
    assert "secure" in response.headers["set-cookie"].lower()
