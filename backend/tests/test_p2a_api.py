import io
import json
import re
import sqlite3
from types import SimpleNamespace

import fitz
import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.p2a_engine import RunBudget, UnknownUsageError, VERSIONS
from backend.p2a_resume import (build_n3_partition_descriptors,
                                checkpoint_result_hash, inherit_n3_checkpoint,
                                run_p2a_n3)
from backend.p2b_engine import validate_understanding_snapshot
from backend.p2a_schema import ClaimEvidenceMap, PaperProfile, ReviewPlan
from backend.tests.test_p2a_engine import MAP, _aux_reply, _reply
from backend.tests.test_p2b_engine import inputs as p2a_inputs
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
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        indexes = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert "source_bundle_json" in columns
    assert {"paper_understanding_run_lineage",
            "paper_understanding_n3_checkpoints"}.issubset(tables)
    assert {"p2a_one_active_per_document", "p2a_one_continuation_per_parent",
            "p2a_n3_checkpoint_run"}.issubset(indexes)


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
    assert result["lineage"] is None
    assert len(result["n3_checkpoints"]) == 1
    assert result["n3_checkpoints"][0]["generation_mode"] == "generated"
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


def test_failed_n3_run_can_create_lineaged_continuation_without_replaying_n1_n2(
        tmp_path, monkeypatch):
    configure_test_model(monkeypatch, key="fake-key", model="requested-model")
    state = {"fail_mapper": True, "calls": []}

    class ResumeOpenAI:
        def __init__(self, **kwargs):
            pass

        @property
        def chat(self):
            def create(**kwargs):
                system = kwargs["messages"][0]["content"]
                state["calls"].append(system.splitlines()[0])
                if state["fail_mapper"] and "contribution-evidence-mapper/v2" in system:
                    raise RuntimeError("private provider diagnostic")
                return _fake_completion(kwargs["messages"])
            return SimpleNamespace(completions=SimpleNamespace(create=create))

    monkeypatch.setattr("backend.app.OpenAI", ResumeOpenAI)
    client = TestClient(create_app(tmp_path))
    document = client.post("/api/documents", files={"file": (
        "paper.pdf", io.BytesIO(_pdf()), "application/pdf")}).json()
    first = client.post(
        f'/api/documents/{document["id"]}/understanding-runs',
        json={"target_journal": "Example Journal", "special_requirements": "N3 only"},
    ).json()
    parent = client.get(f'/api/understanding-runs/{first["id"]}').json()
    assert parent["status"] == "failed"
    assert parent["review_plan"] and parent["paper_profile"]
    assert parent["claim_evidence_map"] is None
    assert parent["n3_checkpoints"] == []

    state["fail_mapper"] = False
    calls_before = len(state["calls"])
    response = client.post(f'/api/understanding-runs/{first["id"]}/n3-resume-runs')
    assert response.status_code == 202, response.json()
    child = client.get(f'/api/understanding-runs/{response.json()["id"]}').json()

    assert child["status"] == "completed", child["error"]
    assert child["lineage"]["parent_run_id"] == first["id"]
    assert child["lineage"]["plan_source_run_id"] == first["id"]
    assert child["lineage"]["profile_source_run_id"] == first["id"]
    assert child["review_plan"]["review_run_id"] == child["id"]
    assert child["paper_profile"]["review_run_id"] == child["id"]
    assert child["claim_evidence_map"]["review_run_id"] == child["id"]
    assert [item["stage"] for item in child["model_calls"]] == [
        "contribution-evidence-mapper", "contribution-normalizer"]
    assert len(state["calls"]) - calls_before == 2
    assert child["n3_checkpoints"][0]["generation_mode"] == "generated"

    bundle = client.get(f'/api/understanding-runs/{child["id"]}/source-bundle').json()
    validate_understanding_snapshot(
        bundle,
        ReviewPlan.model_validate(child["review_plan"]),
        PaperProfile.model_validate(child["paper_profile"]),
        ClaimEvidenceMap.model_validate(child["claim_evidence_map"]),
        child["id"],
        child["status"],
    )

    unchanged_parent = client.get(f'/api/understanding-runs/{first["id"]}').json()
    assert unchanged_parent["status"] == "failed"
    assert unchanged_parent["claim_evidence_map"] is None
    calls_after_completion = len(state["calls"])
    duplicate = client.post(f'/api/understanding-runs/{first["id"]}/n3-resume-runs')
    assert duplicate.status_code == 409
    assert len(state["calls"]) == calls_after_completion


def test_resume_creation_rejects_source_drift_and_active_duplicate_without_supplier_call(
        tmp_path, monkeypatch):
    configure_test_model(monkeypatch, key="fake-key", model="requested-model")
    supplier_calls = []

    class ProfileOnlyOpenAI:
        def __init__(self, **kwargs):
            pass

        @property
        def chat(self):
            def create(**kwargs):
                system = kwargs["messages"][0]["content"]
                supplier_calls.append(system)
                if "contribution-evidence-mapper/v2" in system:
                    raise RuntimeError("offline")
                return _fake_completion(kwargs["messages"])
            return SimpleNamespace(completions=SimpleNamespace(create=create))

    monkeypatch.setattr("backend.app.OpenAI", ProfileOnlyOpenAI)
    client = TestClient(create_app(tmp_path))
    document = client.post("/api/documents", files={"file": (
        "paper.pdf", io.BytesIO(_pdf()), "application/pdf")}).json()
    parent_id = client.post(
        f'/api/documents/{document["id"]}/understanding-runs', json={}).json()["id"]
    calls_before = len(supplier_calls)

    with sqlite3.connect(tmp_path / "review.db") as db:
        frozen = json.loads(db.execute(
            "SELECT source_bundle_json FROM paper_understanding_runs WHERE id = ?",
            (parent_id,),
        ).fetchone()[0])
        frozen["document"]["snapshot_id"] = "foreign-snapshot"
        db.execute("UPDATE paper_understanding_runs SET source_bundle_json = ? WHERE id = ?",
                   (json.dumps(frozen), parent_id))
    rejected = client.post(f"/api/understanding-runs/{parent_id}/n3-resume-runs")
    assert rejected.status_code == 409
    assert len(supplier_calls) == calls_before
    assert "foreign-snapshot" not in str(rejected.json())

    with sqlite3.connect(tmp_path / "review.db") as db:
        db.execute("UPDATE paper_understanding_runs SET source_bundle_json = ? WHERE id = ?",
                   (json.dumps({**frozen, "document": {
                       **frozen["document"], "snapshot_id": document["snapshot_id"]}}), parent_id))
        db.execute(
            "INSERT INTO paper_understanding_runs "
            "(id, document_id, status, created_at) VALUES (?, ?, 'queued', datetime('now'))",
            ("already-active", document["id"]),
        )
    duplicate = client.post(f"/api/understanding-runs/{parent_id}/n3-resume-runs")
    assert duplicate.status_code == 409
    assert len(supplier_calls) == calls_before


def test_restart_marks_continuation_failed_without_replaying_or_deleting_checkpoint(
        tmp_path, monkeypatch):
    configure_test_model(monkeypatch, key="fake-key", model="requested-model")
    client = TestClient(create_app(tmp_path))
    document = client.post("/api/documents", files={"file": (
        "paper.pdf", io.BytesIO(_pdf()), "application/pdf")}).json()
    bundle = client.get(f'/api/documents/{document["id"]}/source-bundle').json()
    with sqlite3.connect(tmp_path / "review.db") as db:
        db.execute(
            "INSERT INTO paper_understanding_runs "
            "(id, document_id, source_bundle_id, source_bundle_json, status, created_at) "
            "VALUES ('interrupted-child', ?, ?, ?, 'running', datetime('now'))",
            (document["id"], bundle["bundle_id"], json.dumps(bundle)),
        )
        db.execute(
            "INSERT INTO paper_understanding_n3_checkpoints "
            "(id, run_id, generation_mode, partition_id, partition_index, partition_count, "
            "unit_ids_json, input_fingerprint, document_id, source_bundle_id, "
            "source_snapshot_id, pdf_sha256, prompt_version, schema_version, result_json, "
            "result_hash, created_at) VALUES "
            "('preserved-checkpoint', 'interrupted-child', 'generated', 'part', 0, 1, "
            "'[]', 'fingerprint', ?, ?, ?, ?, 'contribution-evidence-mapper/v2', '1.1', "
            "'{}', 'hash', datetime('now'))",
            (document["id"], bundle["bundle_id"], document["snapshot_id"], document["sha256"]),
        )

    restarted = TestClient(create_app(tmp_path))
    result = restarted.get("/api/understanding-runs/interrupted-child").json()
    assert result["status"] == "failed"
    assert result["error"] == "interrupted_by_restart"
    assert result["model_calls"] == []
    assert [item["id"] for item in result["n3_checkpoints"]] == ["preserved-checkpoint"]


def test_api_resume_inherits_three_valid_checkpoints_and_runs_only_two_missing_parts(
        tmp_path, monkeypatch):
    configure_test_model(monkeypatch, key="fake-key", model="requested-model")
    bundle, plan, profile, _ = p2a_inputs()
    parent_id = "failed-parent"
    bundle["document"]["sha256"] = "frozen-pdf-sha"
    template = bundle["reading_units"][0]
    unit_ids = ["ru1", "ru2", "ru_3", "ru_4", "ru_5"]
    bundle["reading_units"] = [
        {**template, "id": unit_ids[index - 1], "evidence_ids": [f"ev{index}"],
         "text": chr(64 + index) * 9100}
        for index in range(1, 6)
    ]
    bundle["body"] = [
        {"evidence_id": f"ev{index}", "page_index": 0,
         "text": chr(64 + index) * 9100, "bbox": [1, index, 3, index + 1]}
        for index in range(1, 6)
    ]
    bundle["pages"][0]["evidence_ids"] = [f"ev{index}" for index in range(1, 6)]
    plan = plan.model_copy(update={
        "review_run_id": parent_id,
        "prompt_version": VERSIONS["paper-type-planner"],
    }, deep=True)
    profile = profile.model_copy(update={
        "review_run_id": parent_id,
        "prompt_version": VERSIONS["paper-understanding"],
    }, deep=True)
    saved = []
    part_calls = 0

    def interrupted_model(stage, system, user):
        nonlocal part_calls
        if stage == "contribution-evidence-mapper":
            part_calls += 1
            if part_calls == 4:
                raise RuntimeError("connection interrupted")
            return _reply(MAP)
        return _aux_reply(stage, user)

    with pytest.raises(UnknownUsageError):
        run_p2a_n3(bundle, profile, parent_id, interrupted_model, "requested-model",
                    special_requirements="N3 only", budget=RunBudget(),
                    on_checkpoint=saved.append)
    assert len(saved) == 3

    app = create_app(tmp_path)
    with sqlite3.connect(tmp_path / "review.db") as db:
        db.execute("INSERT INTO documents VALUES (?, 'paper.pdf', ?, ?, 1, '[]', '[]')",
                   (bundle["document"]["id"], bundle["document"]["sha256"],
                    bundle["document"]["snapshot_id"]))
        db.execute(
            "INSERT INTO paper_understanding_runs "
            "(id, document_id, source_bundle_id, source_bundle_json, status, error, "
            "target_journal, special_requirements, review_plan, paper_profile, created_at, finished_at) "
            "VALUES (?, ?, ?, ?, 'failed', 'unknown_usage', NULL, 'N3 only', ?, ?, "
            "datetime('now'), datetime('now'))",
            (parent_id, bundle["document"]["id"], bundle["bundle_id"], json.dumps(bundle),
             plan.model_dump_json(), profile.model_dump_json()),
        )
        for checkpoint in saved:
            db.execute(
                "INSERT INTO paper_understanding_n3_checkpoints "
                "(id, run_id, origin_checkpoint_id, origin_run_id, generation_mode, "
                "partition_id, partition_index, partition_count, unit_ids_json, input_fingerprint, "
                "document_id, source_bundle_id, source_snapshot_id, pdf_sha256, prompt_version, "
                "schema_version, result_json, result_hash, created_at) VALUES "
                "(?, ?, NULL, NULL, 'generated', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
                (checkpoint.checkpoint_id, parent_id, checkpoint.partition_id,
                 checkpoint.partition_index, checkpoint.partition_count,
                 json.dumps(checkpoint.unit_ids), checkpoint.input_fingerprint,
                 bundle["document"]["id"], bundle["bundle_id"],
                 bundle["document"]["snapshot_id"], bundle["document"]["sha256"],
                 checkpoint.prompt_version, checkpoint.schema_version,
                 checkpoint.result.model_dump_json(), checkpoint.result_hash),
            )

    supplier_stages = []

    class MissingOnlyOpenAI:
        def __init__(self, **kwargs):
            pass

        @property
        def chat(self):
            def create(**kwargs):
                system = kwargs["messages"][0]["content"]
                stage = ("contribution-normalizer" if "contribution-normalizer/" in system
                         else "contribution-evidence-mapper")
                supplier_stages.append(stage)
                return _fake_completion(kwargs["messages"])
            return SimpleNamespace(completions=SimpleNamespace(create=create))

    monkeypatch.setattr("backend.app.OpenAI", MissingOnlyOpenAI)
    client = TestClient(app)
    created = client.post(f"/api/understanding-runs/{parent_id}/n3-resume-runs")
    assert created.status_code == 202, created.json()
    child = client.get(f'/api/understanding-runs/{created.json()["id"]}').json()
    assert child["status"] == "completed", child["error"]
    assert supplier_stages == ["contribution-evidence-mapper"] * 2 + [
        "contribution-normalizer"]
    assert [item["generation_mode"] for item in child["n3_checkpoints"]] == [
        "inherited", "inherited", "inherited", "generated", "generated"]
    assert len(child["model_calls"]) == 3
    assert all(item["stage"] not in {"paper-type-planner", "paper-understanding"}
               for item in child["model_calls"])


def _failed_parent_client(tmp_path, monkeypatch):
    configure_test_model(monkeypatch, key="fake-key", model="requested-model")
    supplier_calls = []

    class N3FailsOpenAI:
        def __init__(self, **kwargs):
            pass

        @property
        def chat(self):
            def create(**kwargs):
                system = kwargs["messages"][0]["content"]
                supplier_calls.append(system)
                if "contribution-evidence-mapper/v2" in system:
                    raise RuntimeError("private upstream detail")
                return _fake_completion(kwargs["messages"])
            return SimpleNamespace(completions=SimpleNamespace(create=create))

    monkeypatch.setattr("backend.app.OpenAI", N3FailsOpenAI)
    client = TestClient(create_app(tmp_path))
    document = client.post("/api/documents", files={"file": (
        "paper.pdf", io.BytesIO(_pdf()), "application/pdf")}).json()
    parent_id = client.post(
        f'/api/documents/{document["id"]}/understanding-runs', json={}).json()["id"]
    assert client.get(f"/api/understanding-runs/{parent_id}").json()["status"] == "failed"
    return client, document, parent_id, supplier_calls


@pytest.mark.parametrize(("column", "field"), [
    ("review_plan", "document_id"),
    ("review_plan", "source_bundle_id"),
    ("paper_profile", "document_id"),
    ("paper_profile", "source_bundle_id"),
])
def test_resume_rejects_foreign_understanding_metadata_before_supplier_call(
        tmp_path, monkeypatch, column, field):
    client, _, parent_id, supplier_calls = _failed_parent_client(tmp_path, monkeypatch)
    with sqlite3.connect(tmp_path / "review.db") as db:
        result = json.loads(db.execute(
            f"SELECT {column} FROM paper_understanding_runs WHERE id = ?", (parent_id,)
        ).fetchone()[0])
        result[field] = f"foreign-{field}"
        db.execute(f"UPDATE paper_understanding_runs SET {column} = ? WHERE id = ?",
                   (json.dumps(result), parent_id))
        before_runs = db.execute("SELECT count(*) FROM paper_understanding_runs").fetchone()[0]
    calls_before = len(supplier_calls)

    response = client.post(f"/api/understanding-runs/{parent_id}/n3-resume-runs")

    assert response.status_code == 409
    assert response.json() == {"detail": "父任务续跑资产无效"}
    assert len(supplier_calls) == calls_before
    with sqlite3.connect(tmp_path / "review.db") as db:
        assert db.execute("SELECT count(*) FROM paper_understanding_runs").fetchone()[0] == before_runs


def test_continuation_revalidates_inherited_assets_before_first_supplier_call(
        tmp_path, monkeypatch):
    client, _, parent_id, supplier_calls = _failed_parent_client(tmp_path, monkeypatch)

    class CapturedBackground:
        task = None
        args = ()

        def add_task(self, task, *args, **kwargs):
            assert not kwargs
            self.task = task
            self.args = args

    route = next(
        item for item in client.app.routes
        if item.path == "/api/understanding-runs/{parent_run_id}/n3-resume-runs"
    )
    background = CapturedBackground()
    created = route.endpoint(parent_id, background)
    assert background.task is not None

    with sqlite3.connect(tmp_path / "review.db") as db:
        plan = json.loads(db.execute(
            "SELECT review_plan FROM paper_understanding_runs WHERE id = ?",
            (created["id"],),
        ).fetchone()[0])
        plan["source_bundle_id"] = "foreign-bundle-after-creation"
        db.execute(
            "UPDATE paper_understanding_runs SET review_plan = ? WHERE id = ?",
            (json.dumps(plan), created["id"]),
        )
    calls_before = len(supplier_calls)

    background.task(*background.args)

    result = client.get(f'/api/understanding-runs/{created["id"]}').json()
    assert result["status"] == "failed"
    assert result["error"] == "resume: ValueError"
    assert len(supplier_calls) == calls_before


@pytest.mark.parametrize("lineage_field", ["plan_schema_version", "source_snapshot_id"])
def test_resume_rejects_parent_lineage_version_or_source_drift_before_supplier_call(
        tmp_path, monkeypatch, lineage_field):
    client, document, parent_id, supplier_calls = _failed_parent_client(tmp_path, monkeypatch)
    with sqlite3.connect(tmp_path / "review.db") as db:
        db.row_factory = sqlite3.Row
        parent = db.execute(
            "SELECT * FROM paper_understanding_runs WHERE id = ?", (parent_id,)
        ).fetchone()
        plan = ReviewPlan.model_validate_json(parent["review_plan"])
        profile = PaperProfile.model_validate_json(parent["paper_profile"])
        source_id = "n1n2-source"
        source_plan = plan.model_copy(update={"review_run_id": source_id}, deep=True)
        source_profile = profile.model_copy(update={"review_run_id": source_id}, deep=True)
        db.execute(
            "INSERT INTO paper_understanding_runs "
            "(id, document_id, source_bundle_id, source_bundle_json, status, review_plan, "
            "paper_profile, created_at, finished_at) VALUES "
            "(?, ?, ?, ?, 'failed', ?, ?, datetime('now'), datetime('now'))",
            (source_id, parent["document_id"], parent["source_bundle_id"],
             parent["source_bundle_json"], source_plan.model_dump_json(),
             source_profile.model_dump_json()),
        )
        lineage = {
            "run_id": parent_id,
            "parent_run_id": source_id,
            "plan_source_run_id": source_id,
            "profile_source_run_id": source_id,
            "document_id": document["id"],
            "source_bundle_id": parent["source_bundle_id"],
            "source_snapshot_id": document["snapshot_id"],
            "pdf_sha256": document["sha256"],
            "plan_prompt_version": plan.prompt_version,
            "plan_schema_version": plan.schema_version,
            "profile_prompt_version": profile.prompt_version,
            "profile_schema_version": profile.schema_version,
            "n3_prompt_version": VERSIONS["contribution-evidence-mapper"],
            "n3_schema_version": "1.1",
        }
        lineage[lineage_field] = ("foreign-snapshot" if lineage_field == "source_snapshot_id"
                                  else "1.0" if plan.schema_version != "1.0" else "1.1")
        db.execute(
            "INSERT INTO paper_understanding_run_lineage "
            "(run_id, parent_run_id, plan_source_run_id, profile_source_run_id, document_id, "
            "source_bundle_id, source_snapshot_id, pdf_sha256, plan_prompt_version, "
            "plan_schema_version, profile_prompt_version, profile_schema_version, "
            "n3_prompt_version, n3_schema_version, created_at) VALUES "
            "(:run_id, :parent_run_id, :plan_source_run_id, :profile_source_run_id, "
            ":document_id, :source_bundle_id, :source_snapshot_id, :pdf_sha256, "
            ":plan_prompt_version, :plan_schema_version, :profile_prompt_version, "
            ":profile_schema_version, :n3_prompt_version, :n3_schema_version, datetime('now'))",
            lineage,
        )
    calls_before = len(supplier_calls)

    response = client.post(f"/api/understanding-runs/{parent_id}/n3-resume-runs")

    assert response.status_code == 409
    assert len(supplier_calls) == calls_before


def _insert_checkpoint(db, bundle, checkpoint):
    db.execute(
        "INSERT INTO paper_understanding_n3_checkpoints "
        "(id, run_id, origin_checkpoint_id, origin_run_id, generation_mode, partition_id, "
        "partition_index, partition_count, unit_ids_json, input_fingerprint, document_id, "
        "source_bundle_id, source_snapshot_id, pdf_sha256, prompt_version, schema_version, "
        "result_json, result_hash, created_at) VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
        (checkpoint.checkpoint_id, checkpoint.generating_run_id,
         checkpoint.origin_checkpoint_id, checkpoint.origin_run_id,
         checkpoint.generation_mode, checkpoint.partition_id,
         checkpoint.partition_index, checkpoint.partition_count,
         json.dumps(checkpoint.unit_ids), checkpoint.input_fingerprint,
         bundle["document"]["id"], bundle["bundle_id"],
         bundle["document"]["snapshot_id"], bundle["document"]["sha256"],
         checkpoint.prompt_version, checkpoint.schema_version,
         checkpoint.result.model_dump_json(), checkpoint.result_hash),
    )


def _seed_inherited_checkpoint_parent(tmp_path, monkeypatch):
    configure_test_model(monkeypatch, key="fake-key", model="requested-model")
    supplier_calls = []

    class RecordingOpenAI:
        def __init__(self, **kwargs):
            pass

        @property
        def chat(self):
            def create(**kwargs):
                supplier_calls.append(kwargs["messages"][0]["content"])
                return _fake_completion(kwargs["messages"])
            return SimpleNamespace(completions=SimpleNamespace(create=create))

    monkeypatch.setattr("backend.app.OpenAI", RecordingOpenAI)
    app = create_app(tmp_path)
    bundle, plan, profile, _ = p2a_inputs()
    bundle["document"]["sha256"] = "frozen-pdf-sha"
    root_id, child_id = "checkpoint-root", "checkpoint-child"
    root_plan = plan.model_copy(update={
        "review_run_id": root_id, "prompt_version": VERSIONS["paper-type-planner"]}, deep=True)
    root_profile = profile.model_copy(update={
        "review_run_id": root_id, "prompt_version": VERSIONS["paper-understanding"]}, deep=True)
    generated = []

    def model(stage, system, user):
        if stage == "contribution-evidence-mapper":
            return _reply(MAP)
        return _aux_reply(stage, user)

    run_p2a_n3(bundle, root_profile, root_id, model, "requested-model",
                on_checkpoint=generated.append)
    descriptor = build_n3_partition_descriptors(bundle, root_profile, None, None)[0]
    child_checkpoint = inherit_n3_checkpoint(generated[0], descriptor, bundle, child_id)
    child_plan = root_plan.model_copy(update={"review_run_id": child_id}, deep=True)
    child_profile = root_profile.model_copy(update={"review_run_id": child_id}, deep=True)

    with sqlite3.connect(tmp_path / "review.db") as db:
        db.row_factory = sqlite3.Row
        db.execute("INSERT INTO documents VALUES (?, 'paper.pdf', ?, ?, 1, '[]', '[]')",
                   (bundle["document"]["id"], bundle["document"]["sha256"],
                    bundle["document"]["snapshot_id"]))
        for run_id, run_plan, run_profile in (
                (root_id, root_plan, root_profile), (child_id, child_plan, child_profile)):
            db.execute(
                "INSERT INTO paper_understanding_runs "
                "(id, document_id, source_bundle_id, source_bundle_json, status, error, "
                "review_plan, paper_profile, created_at, finished_at) VALUES "
                "(?, ?, ?, ?, 'failed', 'unknown_usage', ?, ?, datetime('now'), datetime('now'))",
                (run_id, bundle["document"]["id"], bundle["bundle_id"], json.dumps(bundle),
                 run_plan.model_dump_json(), run_profile.model_dump_json()),
            )
        db.execute(
            "INSERT INTO paper_understanding_run_lineage VALUES "
            "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))",
            (child_id, root_id, root_id, root_id, bundle["document"]["id"],
             bundle["bundle_id"], bundle["document"]["snapshot_id"],
             bundle["document"]["sha256"], root_plan.prompt_version,
             root_plan.schema_version, root_profile.prompt_version,
             root_profile.schema_version, VERSIONS["contribution-evidence-mapper"], "1.1"),
        )
        _insert_checkpoint(db, bundle, generated[0])
        _insert_checkpoint(db, bundle, child_checkpoint)
    return TestClient(app), bundle, root_id, child_id, generated[0], child_checkpoint, supplier_calls


@pytest.mark.parametrize("damage", ["origin_json", "origin_hash", "descendant_content", "cycle"])
def test_resume_rejects_corrupt_checkpoint_origin_chain_before_supplier_call(
        tmp_path, monkeypatch, damage):
    client, _, root_id, child_id, root_checkpoint, child_checkpoint, calls = \
        _seed_inherited_checkpoint_parent(tmp_path, monkeypatch)
    with sqlite3.connect(tmp_path / "review.db") as db:
        if damage == "origin_json":
            db.execute("UPDATE paper_understanding_n3_checkpoints SET result_json = '{' "
                       "WHERE id = ?", (root_checkpoint.checkpoint_id,))
        elif damage == "origin_hash":
            db.execute("UPDATE paper_understanding_n3_checkpoints SET result_hash = ? WHERE id = ?",
                       ("f" * 64, root_checkpoint.checkpoint_id))
        elif damage == "descendant_content":
            result = child_checkpoint.result.model_copy(deep=True)
            result.contributions[0].title = "tampered but structurally valid"
            db.execute("UPDATE paper_understanding_n3_checkpoints SET result_json = ?, result_hash = ? "
                       "WHERE id = ?", (result.model_dump_json(), checkpoint_result_hash(result),
                                        child_checkpoint.checkpoint_id))
        else:
            db.execute("UPDATE paper_understanding_n3_checkpoints SET origin_checkpoint_id = ?, "
                       "origin_run_id = ? WHERE id = ?",
                       (child_checkpoint.checkpoint_id, child_id, child_checkpoint.checkpoint_id))
    calls_before = len(calls)

    response = client.post(f"/api/understanding-runs/{child_id}/n3-resume-runs")

    assert response.status_code == 409
    assert response.json() == {"detail": "父任务续跑资产无效"}
    assert len(calls) == calls_before


def test_valid_multigeneration_checkpoint_chain_preserves_original_n1_n2_sources(
        tmp_path, monkeypatch):
    client, _, root_id, child_id, _, child_checkpoint, calls = \
        _seed_inherited_checkpoint_parent(tmp_path, monkeypatch)
    calls_before = len(calls)

    response = client.post(f"/api/understanding-runs/{child_id}/n3-resume-runs")

    assert response.status_code == 202, response.json()
    grandchild = client.get(f'/api/understanding-runs/{response.json()["id"]}').json()
    assert grandchild["status"] == "completed", grandchild["error"]
    assert grandchild["lineage"]["parent_run_id"] == child_id
    assert grandchild["lineage"]["plan_source_run_id"] == root_id
    assert grandchild["lineage"]["profile_source_run_id"] == root_id
    assert grandchild["n3_checkpoints"][0]["origin_checkpoint_id"] == child_checkpoint.checkpoint_id
    assert len(calls) - calls_before == 1
