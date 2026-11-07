import io
import json
import re
import sqlite3
from types import SimpleNamespace

import fitz
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.tests.model_test_config import configure_test_model


def test_existing_p2a_run_table_receives_source_snapshot_column(tmp_path):
    with sqlite3.connect(tmp_path / "review.db") as db:
        db.execute("CREATE TABLE paper_understanding_runs (id TEXT PRIMARY KEY, document_id TEXT NOT NULL, "
                   "source_bundle_id TEXT, status TEXT NOT NULL, error TEXT, target_journal TEXT, "
                   "special_requirements TEXT, review_plan TEXT, paper_profile TEXT, "
                   "claim_evidence_map TEXT, created_at TEXT NOT NULL, finished_at TEXT)")
    create_app(tmp_path)
    with sqlite3.connect(tmp_path / "review.db") as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(paper_understanding_runs)")}
    assert "source_bundle_json" in columns


def test_understanding_results_require_existing_session_guard(tmp_path, monkeypatch):
    monkeypatch.setenv("PAPER_REVIEW_ACCESS_TOKEN", "local-private-access-token")
    client = TestClient(create_app(tmp_path))
    assert client.get("/api/understanding-runs/example").status_code == 401
    assert client.get("/api/understanding-runs/example/source-bundle").status_code == 401


def _pdf():
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((70, 70), "1 Method")
    page.insert_text((70, 100), "The authors introduce a model component.")
    page.insert_text((70, 130), "An experiment measures its result.")
    data = pdf.tobytes()
    pdf.close()
    return data


def _fake_completion(messages):
    system = messages[0]["content"]
    user = messages[1]["content"]
    source_id = None if "contribution-normalizer/" in system else re.search(r"\[(ru_[^\]]+)\]", user).group(1)
    if "paper-type-planner/v1" in system:
        payload = {"paper_type": "AI algorithm research", "research_domain": "AI",
                   "methodological_paradigm": "empirical", "applicable_review_dimensions": ["method"],
                   "priority_dimensions": ["method"], "excluded_dimensions": [],
                   "required_materials": ["method"], "missing_materials": []}
    elif "paper-understanding/v1" in system:
        payload = {"research_problem": {"text": "A model problem", "evidence_ref_ids": [source_id]},
                   "research_motivation": None, "research_gap_claimed_by_authors": None,
                   "proposed_method": None, "method_components": [], "theoretical_assumptions": [],
                   "experimental_setup": [], "datasets_and_metrics": [], "main_results": [],
                   "author_claimed_contributions": [], "conclusions": [],
                   "author_stated_limitations": [], "unresolved_source_gaps": ["table not checked"]}
    elif "contribution-normalizer/" in system:
        data = json.loads(user)
        payload = {"contribution_groups": [list(range(len(data["contributions"])))],
                   "claim_groups": [list(range(len(data["claims"])))], "global_support": []}
    else:
        payload = {"contributions": [{"id": "c1", "title": "Component",
                                      "description": "Authors introduce a component",
                                      "evidence_ref_ids": [source_id]}],
                   "claims": [{"id": "cl1", "contribution_id": "c1",
                               "statement": "Component is introduced", "evidence_ref_ids": [source_id]}],
                   "support_relations": [{"claim_id": "cl1", "evidence_ref_id": source_id,
                                          "relation": "direct_source", "explanation": "Direct text"}],
                   "evidence_gaps": []}
    content = json.dumps(payload)
    middle = len(content) // 2
    return [SimpleNamespace(model="actual-model", choices=[SimpleNamespace(
        delta=SimpleNamespace(content=content[:middle]))], usage=None),
        SimpleNamespace(model="actual-model", choices=[SimpleNamespace(
            delta=SimpleNamespace(content=content[middle:]))], usage=None),
        SimpleNamespace(model="actual-model", choices=[],
                        usage=SimpleNamespace(prompt_tokens=80, completion_tokens=20))]


def test_understanding_run_persists_scoped_results_without_changing_review_baseline(tmp_path, monkeypatch):
    configure_test_model(monkeypatch, key="fake-key", model="requested-model")

    class FakeOpenAI:
        def __init__(self, **kwargs):
            pass

        @property
        def chat(self):
            return SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs:
                                                                _fake_completion(kwargs["messages"])))

    monkeypatch.setattr("backend.app.OpenAI", FakeOpenAI)
    client = TestClient(create_app(tmp_path))
    document = client.post("/api/documents", files={"file": ("paper.pdf", io.BytesIO(_pdf()),
                                                      "application/pdf")}).json()
    created = client.post(f'/api/documents/{document["id"]}/understanding-runs',
                          json={"target_journal": "Example Journal", "special_requirements": "Focus on method"})
    assert created.status_code == 202
    detail = client.get(f'/api/understanding-runs/{created.json()["id"]}')
    assert detail.status_code == 200
    result = detail.json()
    assert result["status"] == "completed", result["error"]
    frozen_bundle = client.get(f'/api/understanding-runs/{result["id"]}/source-bundle').json()
    assert frozen_bundle["bundle_id"] == result["source_bundle_id"]
    assert frozen_bundle["document"]["snapshot_id"] == document["snapshot_id"]
    assert result["review_plan"]["review_run_id"] == result["id"]
    assert result["paper_profile"]["source_bundle_id"] == result["review_plan"]["source_bundle_id"]
    assert result["claim_evidence_map"]["evidence_refs"][0]["snapshot_id"] == document["snapshot_id"]
    assert [item["stage"] for item in result["model_calls"]] == [
        "paper-type-planner", "paper-understanding", "contribution-evidence-mapper",
        "contribution-normalizer"]
    assert all(item["actual_model"] == "actual-model" and item["input_tokens"] == 80
               for item in result["model_calls"])
    usage = client.get("/api/usage/summary").json()
    assert next(item for item in usage["by_category"] if item["category"] == "understanding")["calls"] == 4
    assert client.get("/api/review-cases").json() == []


def test_understanding_run_reports_model_failure_and_missing_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    client = TestClient(create_app(tmp_path))
    document = client.post("/api/documents", files={"file": ("paper.pdf", io.BytesIO(_pdf()),
                                                      "application/pdf")}).json()
    route = f'/api/documents/{document["id"]}/understanding-runs'
    assert client.post(route, json={}).status_code == 503
    configure_test_model(monkeypatch, key="fake-key")

    class BrokenOpenAI:
        def __init__(self, **kwargs):
            pass

        @property
        def chat(self):
            return SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs:
                                                                (_ for _ in ()).throw(RuntimeError("offline"))))

    monkeypatch.setattr("backend.app.OpenAI", BrokenOpenAI)
    created = client.post(route, json={})
    assert created.status_code == 202
    result = client.get(f'/api/understanding-runs/{created.json()["id"]}').json()
    assert result["status"] == "failed"
    assert result["error"] == "paper-type-planner: unknown_usage:RuntimeError"
    assert len(result["model_calls"]) == 1
    assert result["model_calls"][0]["status"] == "unknown_usage"
    usage = client.get("/api/usage/summary").json()
    assert usage["total"]["unknown_token_calls"] == 1


def test_completed_plan_survives_downstream_model_failure(tmp_path, monkeypatch):
    configure_test_model(monkeypatch, key="fake-key", model="requested-model")

    class PlanOnlyOpenAI:
        def __init__(self, **kwargs):
            pass

        @property
        def chat(self):
            def create(**kwargs):
                if "paper-understanding/v1" in kwargs["messages"][0]["content"]:
                    with sqlite3.connect(tmp_path / "review.db") as db:
                        assert db.execute("SELECT count(*) FROM paper_understanding_model_calls").fetchone()[0] == 1
                    raise RuntimeError("secret-bearing upstream message")
                return _fake_completion(kwargs["messages"])
            return SimpleNamespace(completions=SimpleNamespace(create=create))

    monkeypatch.setattr("backend.app.OpenAI", PlanOnlyOpenAI)
    client = TestClient(create_app(tmp_path))
    document = client.post("/api/documents", files={"file": ("paper.pdf", io.BytesIO(_pdf()),
                                                        "application/pdf")}).json()
    created = client.post(f'/api/documents/{document["id"]}/understanding-runs', json={})
    result = client.get(f'/api/understanding-runs/{created.json()["id"]}').json()
    assert result["status"] == "failed"
    assert result["review_plan"]["paper_type"] == "AI algorithm research"
    assert result["paper_profile"] is None
    assert "secret-bearing" not in str(result)


def test_completed_plan_and_profile_survive_unknown_usage_without_replay_on_restart(
        tmp_path, monkeypatch):
    configure_test_model(monkeypatch, key="fake-key", model="requested-model")
    provider_calls = []

    class ProfileOnlyOpenAI:
        def __init__(self, **kwargs):
            pass

        @property
        def chat(self):
            def create(**kwargs):
                system = kwargs["messages"][0]["content"]
                provider_calls.append(system.splitlines()[0])
                if "contribution-evidence-mapper/v2" in system:
                    raise RuntimeError("private provider diagnostic")
                return _fake_completion(kwargs["messages"])
            return SimpleNamespace(completions=SimpleNamespace(create=create))

    monkeypatch.setattr("backend.app.OpenAI", ProfileOnlyOpenAI)
    client = TestClient(create_app(tmp_path))
    document = client.post("/api/documents", files={"file": (
        "paper.pdf", io.BytesIO(_pdf()), "application/pdf")}).json()
    created = client.post(f'/api/documents/{document["id"]}/understanding-runs', json={})
    run_id = created.json()["id"]
    before_restart = client.get(f"/api/understanding-runs/{run_id}").json()

    assert before_restart["status"] == "failed"
    assert before_restart["review_plan"] is not None
    assert before_restart["paper_profile"] is not None
    assert before_restart["claim_evidence_map"] is None
    assert [call["status"] for call in before_restart["model_calls"]] == [
        "completed", "completed", "unknown_usage"]
    assert before_restart["model_calls"][-1]["input_tokens"] is None
    assert before_restart["model_calls"][-1]["output_tokens"] is None
    assert "private provider diagnostic" not in str(before_restart)

    call_count = len(provider_calls)
    restarted = TestClient(create_app(tmp_path))
    after_restart = restarted.get(f"/api/understanding-runs/{run_id}").json()
    assert len(provider_calls) == call_count
    assert after_restart == before_restart


def test_understanding_budget_exceeded_is_explicit(tmp_path, monkeypatch):
    configure_test_model(monkeypatch, key="fake-key")
    monkeypatch.setenv("P2A_MAX_CALLS", "1")

    class FakeOpenAI:
        def __init__(self, **kwargs):
            pass

        @property
        def chat(self):
            return SimpleNamespace(completions=SimpleNamespace(
                create=lambda **kwargs: _fake_completion(kwargs["messages"])))

    monkeypatch.setattr("backend.app.OpenAI", FakeOpenAI)
    client = TestClient(create_app(tmp_path))
    document = client.post("/api/documents", files={"file": ("paper.pdf", io.BytesIO(_pdf()),
                                                        "application/pdf")}).json()
    created = client.post(f'/api/documents/{document["id"]}/understanding-runs', json={})
    result = client.get(f'/api/understanding-runs/{created.json()["id"]}').json()
    assert result["status"] == "budget_exceeded"
    assert result["error"] == "paper-understanding: budget_exceeded"
    assert result["review_plan"] is not None
    assert result["paper_profile"] is None
    assert len(result["model_calls"]) == 1
