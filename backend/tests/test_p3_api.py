import json
import sqlite3
from types import SimpleNamespace

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.tests.test_p3_engine import model
from backend.tests.test_p3_sources import frozen_case
from backend.tests.model_test_config import configure_test_model


def seeded_client(tmp_path):
    bundle, plan, profile, mapping, review, consolidation, pdf_path = frozen_case(tmp_path)
    client = TestClient(create_app(tmp_path))
    (tmp_path / "files" / "doc1.pdf").write_bytes(pdf_path.read_bytes())
    with sqlite3.connect(tmp_path / "review.db") as db:
        db.execute("INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?)",
                   ("doc1", "paper.pdf", bundle["document"]["sha256"], "snap1", 1,
                    "[]", "[]"))
        db.execute("INSERT INTO paper_understanding_runs "
                   "(id, document_id, source_bundle_id, source_bundle_json, status, "
                   "review_plan, paper_profile, claim_evidence_map, created_at) "
                   "VALUES (?, ?, ?, ?, 'completed', ?, ?, ?, datetime('now'))",
                   ("u1", "doc1", "sb1", json.dumps(bundle), plan.model_dump_json(),
                    profile.model_dump_json(), mapping.model_dump_json()))
        db.execute("INSERT INTO professional_review_runs "
                   "(id, understanding_run_id, document_id, source_bundle_id, status, "
                   "result_json, created_at) VALUES (?, ?, ?, ?, 'limited', ?, datetime('now'))",
                   ("b1", "u1", "doc1", "sb1", review.model_dump_json()))
        db.execute("INSERT INTO candidate_consolidation_runs "
                   "(id, professional_review_run_id, understanding_run_id, document_id, "
                   "source_bundle_id, status, result_json, created_at) "
                   "VALUES (?, ?, ?, ?, ?, 'limited', ?, datetime('now'))",
                   ("c1", "b1", "u1", "doc1", "sb1", consolidation.model_dump_json()))
    return client, consolidation


class FakeOpenAI:
    def __init__(self, **kwargs):
        pass

    @property
    def chat(self):
        def create(**kwargs):
            user = kwargs["messages"][1]["content"]
            payload = json.loads(user)
            if "original_facts" in payload:
                stage = "task:source_observation"
            elif "fact_assessments" in payload:
                stage = "task:adjudication"
            elif "source_observations" in payload:
                stage = "task:technical"
            else:
                stage = "task:counterevidence"
            content = model(stage, kwargs["messages"][0]["content"], user)["content"]
            return [SimpleNamespace(model="actual-model", choices=[SimpleNamespace(
                delta=SimpleNamespace(content=content))], usage=None),
                SimpleNamespace(model="actual-model", choices=[], usage=SimpleNamespace(
                    prompt_tokens=100, completion_tokens=40))]
        return SimpleNamespace(completions=SimpleNamespace(create=create))


def test_p3_api_auth_persistence_task_detail_and_explicit_retry(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_REVIEW_ACCESS_TOKEN", "private")
    configure_test_model(monkeypatch, key="fake-secret", model="requested-model")
    monkeypatch.setattr("backend.app.OpenAI", FakeOpenAI)
    client, consolidation = seeded_client(tmp_path)
    path = "/api/candidate-consolidation-runs/c1/evidence-verification-runs"
    assert client.post(path).status_code == 401
    assert client.get("/api/evidence-verification-runs/nope").status_code == 401
    assert client.post("/api/auth/login", json={"access_token": "private"}).status_code == 200
    created = client.post(path)
    assert created.status_code == 202, created.text
    run_id = created.json()["id"]
    response = client.get(f"/api/evidence-verification-runs/{run_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "limited"
    assert len(data["tasks"]) == len(consolidation.verification_tasks) == 1
    assert data["result"]["call_count"] == len(data["model_calls"]) == 3
    assert all(call["actual_model"] == "actual-model" for call in data["model_calls"])
    assert "fake-secret" not in str(data)
    task_id = consolidation.verification_tasks[0].verification_task_id
    detail = client.get(f"/api/evidence-verification-runs/{run_id}/tasks/{task_id}")
    assert detail.status_code == 200
    assert detail.json()["adjudication"]["verification_task_id"] == task_id
    assert detail.json()["observations"]
    with sqlite3.connect(tmp_path / "review.db") as db:
        assert db.execute("SELECT result_json FROM candidate_consolidation_runs WHERE id = 'c1'").fetchone()[0] \
            == consolidation.model_dump_json()
    assert client.post(path).status_code == 409
    retry = client.post(path, json={"retry": True})
    assert retry.status_code == 202
    assert retry.json()["id"] != run_id
    assert client.get(f"/api/evidence-verification-runs/{run_id}").json()["result"]


def test_p3_api_rejects_changed_actual_pdf_and_foreign_snapshot(tmp_path, monkeypatch):
    monkeypatch.delenv("PAPER_REVIEW_ACCESS_TOKEN", raising=False)
    configure_test_model(monkeypatch, key="fake-secret")
    client, _ = seeded_client(tmp_path)
    path = "/api/candidate-consolidation-runs/c1/evidence-verification-runs"
    (tmp_path / "files" / "doc1.pdf").write_bytes(b"different PDF")
    assert client.post(path).status_code == 409
    with sqlite3.connect(tmp_path / "review.db") as db:
        db.execute("UPDATE documents SET snapshot_id = 'foreign' WHERE id = 'doc1'")
    assert client.post(path).status_code == 409


def test_restart_preserves_completed_task_history(tmp_path, monkeypatch):
    monkeypatch.delenv("PAPER_REVIEW_ACCESS_TOKEN", raising=False)
    client, consolidation = seeded_client(tmp_path)
    task_id = consolidation.verification_tasks[0].verification_task_id
    with sqlite3.connect(tmp_path / "review.db") as db:
        db.execute("INSERT INTO evidence_verification_runs "
                   "(id, consolidation_run_id, professional_review_run_id, "
                   "understanding_run_id, document_id, source_bundle_id, status, created_at) "
                   "VALUES ('v1', 'c1', 'b1', 'u1', 'doc1', 'sb1', 'running', datetime('now'))")
        db.execute("INSERT INTO evidence_verification_tasks VALUES "
                   "('v1', ?, 'completed', '{}', datetime('now'))", (task_id,))
    restarted = TestClient(create_app(tmp_path))
    run = restarted.get("/api/evidence-verification-runs/v1")
    assert run.status_code == 200
    assert run.json()["status"] == "partial_failed"
    assert run.json()["tasks"][0]["task_id"] == task_id
