import json
import sqlite3
from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.p2b_engine import run_p2b
from backend.tests.test_p2b_api import seeded_client
from backend.tests.test_p2b_engine import candidate, inputs, reply


def seeded_p2c_client(tmp_path):
    client = seeded_client(tmp_path)
    bundle, plan, profile, mapping = inputs()
    review = run_p2b(bundle, plan, profile, mapping, "u1", "b1",
        lambda stage, system, user: reply([candidate(title=f"Concern {stage}")])
        if stage in {"D1", "D2"} else reply(), "model")
    with sqlite3.connect(tmp_path / "review.db") as db:
        db.execute("INSERT INTO professional_review_runs "
                   "(id, understanding_run_id, document_id, source_bundle_id, status, result_json, created_at) "
                   "VALUES (?, ?, ?, ?, ?, ?, datetime('now'))",
                   ("b1", "u1", "doc1", "sb1", "limited", review.model_dump_json()))
    return client


def test_api_auth_snapshot_and_incomplete_p2b_input(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_REVIEW_ACCESS_TOKEN", "private")
    monkeypatch.setenv("OPENAI_API_KEY", "fake-secret")
    client = seeded_p2c_client(tmp_path)
    assert client.post("/api/professional-review-runs/b1/consolidation-runs").status_code == 401
    assert client.get("/api/candidate-consolidation-runs/none").status_code == 401
    assert client.post("/api/auth/login", json={"access_token": "private"}).status_code == 200
    with sqlite3.connect(tmp_path / "review.db") as db:
        db.execute("UPDATE professional_review_runs SET status = 'partial_failed' WHERE id = 'b1'")
    assert client.post("/api/professional-review-runs/b1/consolidation-runs").status_code == 409
    with sqlite3.connect(tmp_path / "review.db") as db:
        db.execute("UPDATE professional_review_runs SET status = 'limited' WHERE id = 'b1'")
        db.execute("UPDATE documents SET snapshot_id = 'other' WHERE id = 'doc1'")
    assert client.post("/api/professional-review-runs/b1/consolidation-runs").status_code == 409


def test_api_persists_inventory_relations_tasks_and_safe_usage(tmp_path, monkeypatch):
    monkeypatch.delenv("PAPER_REVIEW_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "fake-secret")
    monkeypatch.setenv("OPENAI_MODEL", "requested-model")

    class FakeOpenAI:
        def __init__(self, **kwargs):
            pass

        @property
        def chat(self):
            def create(**kwargs):
                user = json.loads(kwargs["messages"][1]["content"])
                if "pairs" in user:
                    content = {"relations": [{"left_issue_id": a, "right_issue_id": b,
                        "relation": "semantic_duplicate", "rationale": "same check",
                        "shared_verification_need": "source check"} for a, b in user["pairs"]]}
                else:
                    content = {"groups": [{"issue_ids": sorted(user["candidates"]),
                                           "rationale": "one source check"}]}
                return [SimpleNamespace(model="actual-model", choices=[SimpleNamespace(
                    delta=SimpleNamespace(content=json.dumps(content)))], usage=None),
                    SimpleNamespace(model="actual-model", choices=[],
                        usage=SimpleNamespace(prompt_tokens=100, completion_tokens=40))]
            return SimpleNamespace(completions=SimpleNamespace(create=create))

    monkeypatch.setattr("backend.app.OpenAI", FakeOpenAI)
    client = seeded_p2c_client(tmp_path)
    with sqlite3.connect(tmp_path / "review.db") as db:
        original_p2b = db.execute("SELECT result_json FROM professional_review_runs WHERE id = 'b1'").fetchone()[0]
    created = client.post("/api/professional-review-runs/b1/consolidation-runs")
    assert created.status_code == 202, created.text
    run_id = created.json()["id"]
    response = client.get(f"/api/candidate-consolidation-runs/{run_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "limited"
    assert body["inventory"]["candidate_count"] == 2
    assert len(body["relations"]) == 1
    assert len(body["result"]["verification_tasks"]) == 1
    assert len(body["model_calls"]) == 2
    assert all(item["actual_model"] == "actual-model" for item in body["model_calls"])
    assert "fake-secret" not in str(body)
    with sqlite3.connect(tmp_path / "review.db") as db:
        assert db.execute("SELECT result_json FROM professional_review_runs WHERE id = 'b1'").fetchone()[0] == original_p2b
    assert client.post("/api/professional-review-runs/b1/consolidation-runs").status_code == 409
    explicit_retry = client.post("/api/professional-review-runs/b1/consolidation-runs",
                                 json={"retry": True})
    assert explicit_retry.status_code == 202
    assert explicit_retry.json()["id"] != run_id


def test_restart_keeps_inventory_and_marks_interrupted_run_partial_failed(tmp_path, monkeypatch):
    monkeypatch.delenv("PAPER_REVIEW_ACCESS_TOKEN", raising=False)
    client = seeded_p2c_client(tmp_path)
    with sqlite3.connect(tmp_path / "review.db") as db:
        db.execute("INSERT INTO candidate_consolidation_runs "
                   "(id, professional_review_run_id, understanding_run_id, document_id, "
                   "source_bundle_id, status, inventory_json, created_at) "
                   "VALUES ('c1', 'b1', 'u1', 'doc1', 'sb1', 'running', '{}', datetime('now'))")
    client = TestClient(create_app(tmp_path))
    result = client.get("/api/candidate-consolidation-runs/c1")
    assert result.status_code == 200
    assert result.json()["status"] == "partial_failed"
    assert result.json()["error"] == "interrupted_by_restart"
    assert result.json()["inventory"] == {}
