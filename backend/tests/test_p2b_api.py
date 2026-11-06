import json
import sqlite3
from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.tests.test_p2b_engine import inputs, reply
from backend.tests.model_test_config import configure_test_model


def seeded_client(tmp_path):
    client = TestClient(create_app(tmp_path))
    bundle, plan, profile, mapping = inputs()
    with sqlite3.connect(tmp_path / "review.db") as db:
        db.execute("INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?)",
                   ("doc1", "paper.pdf", "hash", "snap1", 1, "[]", "[]"))
        db.execute("INSERT INTO paper_understanding_runs "
                   "(id, document_id, source_bundle_id, source_bundle_json, status, "
                   "review_plan, paper_profile, claim_evidence_map, created_at) "
                   "VALUES (?, ?, ?, ?, 'completed', ?, ?, ?, datetime('now'))",
                   ("u1", "doc1", "sb1", json.dumps(bundle), plan.model_dump_json(),
                    profile.model_dump_json(), mapping.model_dump_json()))
    return client


def test_api_requires_authentication_and_completed_frozen_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_REVIEW_ACCESS_TOKEN", "private")
    client = seeded_client(tmp_path)
    assert client.post("/api/understanding-runs/u1/professional-review-runs").status_code == 401
    assert client.get("/api/professional-review-runs/nope").status_code == 401
    assert client.post("/api/auth/login", json={"access_token": "private"}).status_code == 200
    configure_test_model(monkeypatch, key="fake")
    with sqlite3.connect(tmp_path / "review.db") as db:
        db.execute("UPDATE paper_understanding_runs SET source_bundle_id = 'other' WHERE id = 'u1'")
    assert client.post("/api/understanding-runs/u1/professional-review-runs").status_code == 409


def test_api_persists_all_dimensions_and_safe_call_diagnostics(tmp_path, monkeypatch):
    monkeypatch.delenv("PAPER_REVIEW_ACCESS_TOKEN", raising=False)
    configure_test_model(monkeypatch, key="fake-secret", model="requested-model")

    class FakeOpenAI:
        def __init__(self, **kwargs):
            pass

        @property
        def chat(self):
            def create(**kwargs):
                content = reply()["content"]
                return [SimpleNamespace(model="actual-model", choices=[SimpleNamespace(
                    delta=SimpleNamespace(content=content))], usage=None),
                    SimpleNamespace(model="actual-model", choices=[],
                                    usage=SimpleNamespace(prompt_tokens=100,
                                                          completion_tokens=50))]
            return SimpleNamespace(completions=SimpleNamespace(create=create))

    monkeypatch.setattr("backend.app.OpenAI", FakeOpenAI)
    client = seeded_client(tmp_path)
    created = client.post("/api/understanding-runs/u1/professional-review-runs")
    assert created.status_code == 202, created.text
    run = client.get(f'/api/professional-review-runs/{created.json()["id"]}')
    assert run.status_code == 200
    result = run.json()
    assert result["status"] == "limited"
    assert [item["dimension_id"] for item in result["dimensions"]] == [f"D{i}" for i in range(1, 7)]
    assert len(result["model_calls"]) == 6
    assert all(item["source_bundle_id"] == "sb1" and item["actual_model"] == "actual-model"
               for item in result["model_calls"])
    assert "fake-secret" not in str(result)
    with sqlite3.connect(tmp_path / "review.db") as db:
        assert db.execute("SELECT count(*) FROM professional_review_dimensions").fetchone()[0] == 6
