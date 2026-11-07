from __future__ import annotations

import io
import inspect
import sqlite3
from types import SimpleNamespace

import fitz
from fastapi.testclient import TestClient

import backend.app as app_module
from backend.app import create_app
from backend.model_runtime import ModelConfigurationError, ModelRuntimeConfiguration
from backend.tests.test_p4_api import seeded_p4_client
from backend.tests.test_p4_drafts_api import FakeOpenAI
from backend.tests.model_test_config import configure_test_model


MODEL_KEYS = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL")


def sample_pdf():
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 80), "1 Method")
    page.insert_text((72, 120), "A deterministic model safety fixture.")
    content = document.tobytes()
    document.close()
    return content


def set_explicit_model_environment(monkeypatch):
    configure_test_model(monkeypatch, key="test-only-key",
                         model="validated-test-model")


def upload_document(client):
    return client.post(
        "/api/documents",
        files={"file": ("paper.pdf", io.BytesIO(sample_pdf()), "application/pdf")},
    ).json()


def test_p2a_rejects_missing_endpoint_before_accepting_task_or_creating_client(
        tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-key")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("OPENAI_MODEL", "validated-test-model")
    client_constructions = []

    class RecordingOpenAI:
        def __init__(self, **kwargs):
            client_constructions.append(kwargs)

    monkeypatch.setattr("backend.app.OpenAI", RecordingOpenAI)
    client = TestClient(create_app(tmp_path))
    document = upload_document(client)

    response = client.post(f'/api/documents/{document["id"]}/understanding-runs', json={})

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "model_configuration_invalid",
        "blockers": ["model_endpoint_not_explicitly_configured"],
        "variables": ["OPENAI_BASE_URL"],
    }
    assert client_constructions == []


def test_sse_chat_preflights_before_streaming_response_or_client_construction(
        tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://unsafe.example/v1")
    monkeypatch.setenv("OPENAI_MODEL", "validated-test-model")
    client_constructions = []

    class RecordingOpenAI:
        def __init__(self, **kwargs):
            client_constructions.append(kwargs)

    monkeypatch.setattr("backend.app.OpenAI", RecordingOpenAI)
    client = TestClient(create_app(tmp_path))
    document = upload_document(client)
    conversation = client.post(
        f'/api/documents/{document["id"]}/conversations'
    ).json()

    response = client.post(
        f'/api/conversations/{conversation["id"]}/messages/stream',
        json={"message": "Explain the method"},
    )

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["detail"]["blockers"] == ["model_endpoint_must_use_https"]
    assert client_constructions == []


def test_configuration_conflict_response_names_variable_without_values(
        tmp_path, monkeypatch):
    set_explicit_model_environment(monkeypatch)
    environment_file = tmp_path / ".env.local"
    environment_file.write_text(
        "OPENAI_API_KEY=different-file-key\n"
        "OPENAI_BASE_URL=https://provider.example/v1\n"
        "OPENAI_MODEL=validated-test-model\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("backend.app.MODEL_ENVIRONMENT_FILE", environment_file)
    client = TestClient(create_app(tmp_path / "data"))
    document = upload_document(client)

    response = client.post(f'/api/documents/{document["id"]}/understanding-runs', json={})

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["variables"] == ["OPENAI_API_KEY"]
    assert "test-only-key" not in response.text
    assert "different-file-key" not in response.text
    assert "provider.example" not in response.text


def test_malformed_endpoint_returns_redacted_503_without_creating_client(
        tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-key")
    malformed = "https://[invalid-ipv6/v1"
    monkeypatch.setenv("OPENAI_BASE_URL", malformed)
    monkeypatch.setenv("OPENAI_MODEL", "validated-test-model")
    client_constructions = []

    class RecordingOpenAI:
        def __init__(self, **kwargs):
            client_constructions.append(kwargs)

    monkeypatch.setattr(app_module, "OpenAI", RecordingOpenAI)
    client = TestClient(create_app(tmp_path))
    document = upload_document(client)

    response = client.post(f'/api/documents/{document["id"]}/understanding-runs', json={})

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "model_configuration_invalid",
        "blockers": ["model_endpoint_invalid_url"],
        "variables": ["OPENAI_BASE_URL"],
    }
    assert malformed not in response.text
    assert client_constructions == []


def test_background_task_revalidates_configuration_before_client_construction(
        tmp_path, monkeypatch):
    set_explicit_model_environment(monkeypatch)
    client_constructions = []

    class RecordingOpenAI:
        def __init__(self, **kwargs):
            client_constructions.append(kwargs)

    calls = 0

    def changing_configuration():
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelRuntimeConfiguration(
                api_key="test-only-key",
                base_url="https://provider.example/v1",
                model="validated-test-model",
            )
        raise ModelConfigurationError(
            ["model_configuration_source_conflict"], ["OPENAI_MODEL"]
        )

    monkeypatch.setattr(app_module, "runtime_model_configuration", changing_configuration)
    monkeypatch.setattr(app_module, "OpenAI", RecordingOpenAI)
    client = TestClient(create_app(tmp_path))
    document = upload_document(client)

    created = client.post(f'/api/documents/{document["id"]}/understanding-runs', json={})

    assert created.status_code == 202
    result = client.get(f'/api/understanding-runs/{created.json()["id"]}').json()
    assert result["status"] == "failed"
    assert result["error"] == "source: ModelConfigurationError"
    assert client_constructions == []


def test_p4_model_generation_fails_closed_but_empty_local_draft_remains_available(
        tmp_path, monkeypatch):
    for key in MODEL_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(app_module, "MODEL_ENVIRONMENT_FILE", tmp_path / "missing.env")
    client_constructions = []

    class RecordingOpenAI:
        def __init__(self, **kwargs):
            client_constructions.append(kwargs)

    monkeypatch.setattr(app_module, "OpenAI", RecordingOpenAI)

    local_root = tmp_path / "local"
    local_root.mkdir()
    local_client, _ = seeded_p4_client(local_root)
    local_project = local_client.post(
        "/api/evidence-verification-runs/v1/report-projects"
    ).json()
    local_response = local_client.post(
        f'/api/review-report-projects/{local_project["id"]}/drafts',
        json={"kind": "author", "language": "zh"},
    )
    assert local_response.status_code == 201
    assert local_response.json()["model_calls"] == []

    model_root = tmp_path / "model"
    model_root.mkdir()
    model_client, _ = seeded_p4_client(model_root, reportable=True)
    model_project = model_client.post(
        "/api/evidence-verification-runs/v1/report-projects"
    ).json()
    model_response = model_client.post(
        f'/api/review-report-projects/{model_project["id"]}/drafts',
        json={"kind": "author", "language": "en"},
    )
    assert model_response.status_code == 503
    assert model_response.json()["detail"]["code"] == "model_configuration_invalid"
    assert client_constructions == []


def test_p4_client_and_request_use_the_same_validated_runtime_configuration(
        tmp_path, monkeypatch):
    set_explicit_model_environment(monkeypatch)
    constructor_arguments = []
    requests = []

    class RecordingOpenAI(FakeOpenAI):
        def __init__(self, **kwargs):
            constructor_arguments.append(kwargs)

        @property
        def chat(self):
            delegate = super().chat

            def create(**kwargs):
                requests.append(kwargs)
                return delegate.completions.create(**kwargs)

            return SimpleNamespace(completions=SimpleNamespace(create=create))

    monkeypatch.setattr(app_module, "OpenAI", RecordingOpenAI)
    client, _ = seeded_p4_client(tmp_path, reportable=True)
    project = client.post("/api/evidence-verification-runs/v1/report-projects").json()

    response = client.post(
        f'/api/review-report-projects/{project["id"]}/drafts',
        json={"kind": "author", "language": "en"},
    )

    assert response.status_code == 201, response.text
    assert constructor_arguments == [{
        "api_key": "test-only-key",
        "base_url": "https://provider.example/v1",
        "timeout": 180,
        "max_retries": 0,
    }]
    assert [item["model"] for item in requests] == [
        "validated-test-model", "validated-test-model"
    ]
    assert all(item["max_tokens"] == 1000 for item in requests)
    assert all(0 < item["timeout"] <= 180 for item in requests)


def test_p4_revalidates_configuration_after_preflight_before_first_request(
        tmp_path, monkeypatch):
    set_explicit_model_environment(monkeypatch)
    client_constructions = []
    calls = 0

    class RecordingOpenAI:
        def __init__(self, **kwargs):
            client_constructions.append(kwargs)

    def changing_configuration():
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelRuntimeConfiguration(
                api_key="test-only-key",
                base_url="https://provider.example/v1",
                model="validated-test-model",
            )
        raise ModelConfigurationError(
            ["model_configuration_source_conflict"], ["OPENAI_BASE_URL"]
        )

    monkeypatch.setattr(app_module, "runtime_model_configuration", changing_configuration)
    monkeypatch.setattr(app_module, "OpenAI", RecordingOpenAI)
    client, _ = seeded_p4_client(tmp_path, reportable=True)
    project = client.post("/api/evidence-verification-runs/v1/report-projects").json()

    response = client.post(
        f'/api/review-report-projects/{project["id"]}/drafts',
        json={"kind": "author", "language": "en"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "model_configuration_invalid",
        "blockers": ["model_configuration_source_conflict"],
        "variables": ["OPENAI_BASE_URL"],
    }
    assert client_constructions == []


def test_p4_stops_before_second_request_if_session_configuration_changes_and_keeps_audit(
        tmp_path, monkeypatch):
    set_explicit_model_environment(monkeypatch)
    sdk_calls = []

    class MutatingOpenAI(FakeOpenAI):
        @property
        def chat(self):
            delegate = super().chat

            def create(**kwargs):
                sdk_calls.append(kwargs)
                response = delegate.completions.create(**kwargs)
                monkeypatch.setenv("OPENAI_MODEL", "changed-model-must-not-leak")
                return response

            return SimpleNamespace(completions=SimpleNamespace(create=create))

    monkeypatch.setattr(app_module, "OpenAI", MutatingOpenAI)
    client, _ = seeded_p4_client(tmp_path, reportable=True)
    project = client.post("/api/evidence-verification-runs/v1/report-projects").json()

    response = client.post(
        f'/api/review-report-projects/{project["id"]}/drafts',
        json={"kind": "author", "language": "en"},
    )

    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "model_configuration_invalid",
        "blockers": ["model_configuration_changed"],
        "variables": ["OPENAI_MODEL"],
    }
    assert "changed-model-must-not-leak" not in response.text
    assert len(sdk_calls) == 1
    assert sdk_calls[0]["model"] == "validated-test-model"
    with sqlite3.connect(tmp_path / "review.db") as db:
        calls = db.execute(
            "SELECT stage, requested_model, status, error "
            "FROM p4_model_calls ORDER BY rowid"
        ).fetchall()
        run = db.execute(
            "SELECT state, reason, request_count FROM p4_generation_runs"
        ).fetchone()
    assert calls == [
        ("writing-1-attempt-1", "validated-test-model", "completed", None),
    ]
    assert run == ("configuration_changed", "model_configuration_changed", 1)


def test_all_production_model_entrypoints_use_central_preflight_and_session():
    source = inspect.getsource(app_module.create_app)
    route_preflights = (
        "create_understanding_run",
        "create_professional_review_run",
        "create_consolidation_run",
        "create_evidence_verification_run",
        "analyze_review_case",
        "start_finding_verification",
        "chat",
        "create_draft",
    )
    for function_name in route_preflights:
        start = source.index(f"def {function_name}")
        next_route = source.find("\n    @app.", start + 1)
        function_source = source[start:next_route if next_route != -1 else None]
        assert "model_configuration_or_503()" in function_source, function_name

    assert "model_base_url" not in source
    assert 'os.environ.get("OPENAI_MODEL"' not in source
    assert "OpenAI(base_url=" not in source
    assert source.count("model_session(timeout=") >= 8


def test_production_model_modules_contain_no_implicit_gateway_or_model_default():
    app_source = inspect.getsource(app_module)
    from backend import model_runtime, p4_drafts_api

    p4_source = inspect.getsource(p4_drafts_api)
    combined = app_source + p4_source
    assert "yaoxing.best" not in combined
    assert 'os.environ.get("OPENAI_MODEL"' not in combined
    assert "gpt-6.1-sol" not in combined
    assert "chat.completions.create" not in combined
    assert inspect.getsource(model_runtime).count("chat.completions.create") == 1
