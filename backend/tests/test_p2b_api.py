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
    assert result["review_requirements"] is None
    assert [item["dimension_id"] for item in result["dimensions"]] == [f"D{i}" for i in range(1, 7)]
    assert len(result["model_calls"]) == 6
    assert all(item["source_bundle_id"] == "sb1" and item["actual_model"] == "actual-model"
               for item in result["model_calls"])
    assert "fake-secret" not in str(result)
    with sqlite3.connect(tmp_path / "review.db") as db:
        assert db.execute("SELECT count(*) FROM professional_review_dimensions").fetchone()[0] == 6


def test_p2a_requirements_do_not_leak_into_default_p2b_prompts(tmp_path, monkeypatch):
    monkeypatch.delenv("PAPER_REVIEW_ACCESS_TOKEN", raising=False)
    configure_test_model(monkeypatch, key="fake-secret", model="requested-model")
    upstream_requirement = "Only perform N1-N3; do not generate review issues."
    seen_users = []

    class FakeOpenAI:
        def __init__(self, **kwargs):
            pass

        @property
        def chat(self):
            def create(**kwargs):
                seen_users.append(json.loads(kwargs["messages"][1]["content"]))
                content = reply()["content"]
                return [SimpleNamespace(model="actual-model", choices=[SimpleNamespace(
                    delta=SimpleNamespace(content=content))], usage=None),
                    SimpleNamespace(model="actual-model", choices=[],
                                    usage=SimpleNamespace(prompt_tokens=100,
                                                          completion_tokens=50))]
            return SimpleNamespace(completions=SimpleNamespace(create=create))

    monkeypatch.setattr("backend.app.OpenAI", FakeOpenAI)
    client = seeded_client(tmp_path)
    with sqlite3.connect(tmp_path / "review.db") as db:
        db.execute("UPDATE paper_understanding_runs SET special_requirements = ? WHERE id = 'u1'",
                   (upstream_requirement,))
    created = client.post("/api/understanding-runs/u1/professional-review-runs")
    assert created.status_code == 202, created.text
    run = client.get(f'/api/professional-review-runs/{created.json()["id"]}').json()
    assert run["review_requirements"] is None
    assert len(seen_users) == 6
    assert all(item["review_requirements"] is None for item in seen_users)
    assert all("special_requirements" not in item for item in seen_users)
    assert all(upstream_requirement not in json.dumps(item) for item in seen_users)
    with sqlite3.connect(tmp_path / "review.db") as db:
        assert db.execute("SELECT special_requirements FROM paper_understanding_runs "
                          "WHERE id = 'u1'").fetchone()[0] == upstream_requirement


def test_explicit_p2b_requirements_are_auditable_and_stage_scoped(tmp_path, monkeypatch):
    monkeypatch.delenv("PAPER_REVIEW_ACCESS_TOKEN", raising=False)
    configure_test_model(monkeypatch, key="fake-secret", model="requested-model")
    upstream_requirement = "Do not generate review issues."
    review_requirement = "Focus on reproducibility while preserving all six dimensions."
    seen_users = []

    class FakeOpenAI:
        def __init__(self, **kwargs):
            pass

        @property
        def chat(self):
            def create(**kwargs):
                seen_users.append(json.loads(kwargs["messages"][1]["content"]))
                content = reply()["content"]
                return [SimpleNamespace(model="actual-model", choices=[SimpleNamespace(
                    delta=SimpleNamespace(content=content))], usage=None),
                    SimpleNamespace(model="actual-model", choices=[],
                                    usage=SimpleNamespace(prompt_tokens=100,
                                                          completion_tokens=50))]
            return SimpleNamespace(completions=SimpleNamespace(create=create))

    monkeypatch.setattr("backend.app.OpenAI", FakeOpenAI)
    client = seeded_client(tmp_path)
    with sqlite3.connect(tmp_path / "review.db") as db:
        db.execute("UPDATE paper_understanding_runs SET special_requirements = ? WHERE id = 'u1'",
                   (upstream_requirement,))
    created = client.post("/api/understanding-runs/u1/professional-review-runs",
                          json={"review_requirements": review_requirement})
    assert created.status_code == 202, created.text
    run = client.get(f'/api/professional-review-runs/{created.json()["id"]}').json()
    assert run["review_requirements"] == review_requirement
    assert len(seen_users) == 6
    assert all(item["review_requirements"] == review_requirement for item in seen_users)
    assert all("special_requirements" not in item for item in seen_users)
    assert all(upstream_requirement not in json.dumps(item) for item in seen_users)


def test_legacy_professional_review_table_gains_nullable_stage_requirements(tmp_path):
    with sqlite3.connect(tmp_path / "review.db") as db:
        db.execute("CREATE TABLE professional_review_runs ("
                   "id TEXT PRIMARY KEY, understanding_run_id TEXT NOT NULL, "
                   "document_id TEXT NOT NULL, source_bundle_id TEXT NOT NULL, "
                   "status TEXT NOT NULL, error TEXT, result_json TEXT, "
                   "created_at TEXT NOT NULL, finished_at TEXT)")
        db.execute("INSERT INTO professional_review_runs "
                   "VALUES ('legacy', 'u1', 'doc1', 'sb1', 'failed', NULL, NULL, "
                   "datetime('now'), NULL)")
    create_app(tmp_path)
    with sqlite3.connect(tmp_path / "review.db") as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(professional_review_runs)")}
        row = db.execute("SELECT status, review_requirements FROM professional_review_runs "
                         "WHERE id = 'legacy'").fetchone()
    assert "review_requirements" in columns
    assert row == ("failed", None)


def test_unknown_usage_keeps_partial_run_out_of_p2c(tmp_path, monkeypatch):
    monkeypatch.delenv("PAPER_REVIEW_ACCESS_TOKEN", raising=False)
    configure_test_model(monkeypatch, key="fake-secret", model="requested-model")
    calls = []

    class FakeOpenAI:
        def __init__(self, **kwargs):
            pass

        @property
        def chat(self):
            def create(**kwargs):
                calls.append(kwargs["messages"][0]["content"])
                if len(calls) == 6:
                    raise RuntimeError("private upstream diagnostic")
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
    run_id = created.json()["id"]
    run = client.get(f"/api/professional-review-runs/{run_id}").json()
    assert run["status"] == "partial_failed"
    assert len(calls) == 6
    assert len(run["dimensions"]) == 5
    assert len(run["model_calls"]) == 6
    assert run["model_calls"][-1]["status"] == "unknown_usage"
    assert run["model_calls"][-1]["input_tokens"] is None
    assert run["model_calls"][-1]["output_tokens"] is None
    assert "private upstream diagnostic" not in str(run)
    assert client.post(f"/api/professional-review-runs/{run_id}/consolidation-runs").status_code == 409
