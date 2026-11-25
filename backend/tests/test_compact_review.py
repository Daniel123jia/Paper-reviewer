import hashlib
import os
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.tests.compact_fake_sdk import CompactFakeSDK
from backend.tests.model_test_config import configure_test_model
from backend.tests.test_p2a_api import _pdf


@pytest.mark.parametrize(
    ("failure", "status", "count"),
    [
        (None, "completed", 2),
        ("disconnect", "unknown_usage", 1),
        ("missing_usage", "unknown_usage", 2),
        ("bad_quote", "failed", 1),
        ("missing_check", "failed", 2),
    ],
)
def test_two_pass_routes(tmp_path, monkeypatch, failure, status, count):
    configure_test_model(monkeypatch, key="offline", model="fixture")
    monkeypatch.setattr(CompactFakeSDK, "failure", failure)
    monkeypatch.setattr(CompactFakeSDK, "calls", [])
    monkeypatch.setattr("backend.app.OpenAI", CompactFakeSDK)
    data_dir = tmp_path / "data"
    client = TestClient(create_app(data_dir))
    pdf = _pdf()
    if os.environ.get("KIM_2014_PDF"):
        pdf = Path(os.environ["KIM_2014_PDF"]).read_bytes()
        assert (
            hashlib.sha256(pdf).hexdigest()
            == "a996da85e0abf6bb1a16cebbb82137af5ca8c16cadc81bc41358829a3687a6cf"
        )
    doc = client.post(
        "/api/documents", files={"file": ("paper.pdf", pdf, "application/pdf")}
    ).json()
    url = "/api/documents/" + doc["id"] + "/compact-review"
    assert client.get(url).json()["status"] == "ready"
    assert (
        client.post(url + "/start", json={"confirm_budget": False}).status_code == 422
    )
    assert not CompactFakeSDK.calls
    assert client.post(url + "/start", json={"confirm_budget": True}).status_code == 202
    job = client.get(url).json()
    assert job["status"] == status, job.get("error")
    assert job["usage"]["request_count"] == count == len(CompactFakeSDK.calls)
    assert job["usage"]["unknown_usage_calls"] == (
        1 if status == "unknown_usage" else 0
    )
    assert job["usage"]["known_input_tokens"] == 101 * (
        count - job["usage"]["unknown_usage_calls"]
    )
    assert job["usage"]["known_output_tokens"] == 37 * (
        count - job["usage"]["unknown_usage_calls"]
    )
    assert job["usage"]["provider_billed_cost_usd"] is None
    if status == "completed":
        bundle = client.get("/api/documents/" + doc["id"] + "/source-bundle").json()
        packet = CompactFakeSDK.calls[0]["source"]
        assert [(b["id"], b["text"]) for b in packet["blocks"]] == [
            (b["evidence_id"], b["text"]) for b in bundle["body"]
        ]
        assert CompactFakeSDK.calls[1] == {"source": packet, "draft": job["draft"]}
        assert (
            job["draft"]["conclusion"]
            and job["verification"]["checks"][0]["verdict"] == "revised"
        )
        assert "共享划分" in job["verification"]["checks"][0]["reason"]
        c = job["verification"]["report"]["comments"][0]["evidence"][0]
        assert (
            client.get(
                "/api/documents/" + doc["id"] + "/evidence/" + c["evidence_id"]
            ).status_code
            == 200
        )
        assert client.get(url + "/export?format=md").text == job["body"]
        assert client.get(url + "/export?format=pdf").content.startswith(b"%PDF-")
    else:
        assert (
            not job["body"] and client.get(url + "/export?format=md").status_code == 409
        )
    reboot = TestClient(create_app(data_dir))
    assert (
        reboot.post(url + "/start", json={"confirm_budget": True}).json()["status"]
        == status
    )
    assert len(CompactFakeSDK.calls) == count


def test_restart_after_dispatch_never_replays(tmp_path, monkeypatch):
    class LostWorker(BaseException):
        pass

    class CrashedSDK(CompactFakeSDK):
        attempts = 0

        def create(self, **kwargs):
            type(self).attempts += 1
            raise LostWorker()

    configure_test_model(monkeypatch)
    monkeypatch.setattr("backend.app.OpenAI", CrashedSDK)
    client = TestClient(create_app(tmp_path))
    doc = client.post(
        "/api/documents", files={"file": ("paper.pdf", _pdf(), "application/pdf")}
    ).json()
    url = "/api/documents/" + doc["id"] + "/compact-review"
    # Simulate disappearing after durable dispatch, before the normal Exception handler.
    try:
        client.post(url + "/start", json={"confirm_budget": True})
    except BaseException:
        pass
    assert CrashedSDK.attempts == 1
    reboot = TestClient(create_app(tmp_path))
    job = reboot.get(url).json()
    assert job["status"] == "unknown_usage" and job["usage"]["unknown_usage_calls"] == 1
    assert (
        reboot.post(url + "/start", json={"confirm_budget": True}).json()["status"]
        == "unknown_usage"
    )
    assert CrashedSDK.attempts == 1
