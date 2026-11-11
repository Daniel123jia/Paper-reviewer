"""All C1 control tests use synthetic identities, content and Fake model streams."""

from __future__ import annotations

import json
import io
import os
import sqlite3
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from backend.app import create_app
from backend.p5c_runtime import C1Runtime
from backend.model_runtime import ModelConfigurationError
from backend.tests.model_test_config import configure_test_model
from backend.tests.test_p2a_api import _pdf, _fake_completion
from backend.p2a_engine import BudgetExceeded, RunBudget
from scripts.p5c.stage_control import (
    C1ActualBudgetExceeded, C1Budget, C1ControlError, C1Identity, C1Store,
    GuardedChatClient,
)


@pytest.fixture
def control(tmp_path: Path):
    store = C1Store(tmp_path / "private-control.db")
    identity = C1Identity(
        paper_id="sardet100k-neurips2024", pdf_sha256="a" * 64,
        manifest_sha256="b" * 64, execution_lock_sha256="c" * 64,
        execution_commit="d" * 40, execution_tree="e" * 40,
        requested_model="gpt-6.1-sol", model_config_sha256="f" * 64,
    )
    budget = C1Budget(max_calls=3, max_input_tokens=1000,
                      max_output_tokens=20, max_output_tokens_per_request=8,
                      max_duration_ms=100000, max_transport_retries=0,
                      content_repair_policy="valid_usage_within_stage_budget",
                      max_planned_cost_usd="0.01", input_usd_per_million="0.8",
                      output_usd_per_million="4", pricing_status="estimated_unverified")
    store.freeze_trial(identity)
    return store, identity, budget


def grant(control, stage="p2a", upstream="a" * 64):
    store, identity, budget = control
    return store.authorize(stage, upstream, identity, budget,
                           acknowledgement="P5C_STAGE_AUTHORIZED")


def test_missing_authorization_and_wrong_lineage_fail_closed(control):
    store, identity, _ = control
    with pytest.raises(C1ControlError, match="authorization_missing"):
        store.claim("missing", "p2a", "a" * 64, identity)
    token, run_id = grant(control)
    with pytest.raises(C1ControlError, match="stage_source_mismatch"):
        store.claim(token, "p2a", "wrong-run", identity)
    assert store.get_run(run_id)["status"] == "authorized"


def test_claim_is_stable_after_duplicate_or_lost_response(control):
    store, identity, _ = control
    token, run_id = grant(control)
    first = store.claim(token, "p2a", "a" * 64, identity)
    second = store.claim(token, "p2a", "a" * 64, identity)
    assert first == (run_id, True)
    assert second == (run_id, False)
    assert store.get_run(run_id)["status"] == "dispatch_pending"


def test_concurrent_claim_has_exactly_one_dispatch(control):
    store, identity, _ = control
    token, run_id = grant(control)
    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(lambda _: store.claim(token, "p2a", "a" * 64, identity), range(8)))
    assert all(row[0] == run_id for row in outcomes)
    assert sum(row[1] for row in outcomes) == 1


def test_identity_and_budget_are_frozen(control):
    store, identity, budget = control
    token, run_id = grant(control)
    changed = identity.model_copy(update={"execution_commit": "0" * 40})
    with pytest.raises(C1ControlError, match="execution_identity_mismatch"):
        store.claim(token, "p2a", "a" * 64, changed)
    changed = identity.model_copy(update={"manifest_sha256": "0" * 64})
    with pytest.raises(C1ControlError, match="execution_identity_mismatch"):
        store.claim(token, "p2a", "a" * 64, changed)
    with pytest.raises(C1ControlError, match="trial_paper_mismatch"):
        store.freeze_trial(identity.model_copy(update={"paper_id": "sam-iclr2021"}))
    assert store.get_run(run_id)["budget"]["max_calls"] == budget.max_calls


def fake_chunk(*, usage=None, model="gpt-6.1-sol"):
    return SimpleNamespace(model=model, usage=usage, choices=[])


class FakeCompletions:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return iter(response)


def fake_client(responses):
    completion = FakeCompletions(responses)
    return SimpleNamespace(chat=SimpleNamespace(completions=completion)), completion


def request(client):
    return list(client.chat.completions.create(
        model="gpt-6.1-sol", stream=True,
        stream_options={"include_usage": True},
        messages=[{"role": "system", "content": "s"},
                  {"role": "user", "content": "u"}],
    ))


def test_fake_stream_settles_usage_then_next_request(control):
    store, identity, _ = control
    token, run_id = grant(control)
    store.claim(token, "p2a", "a" * 64, identity)
    sdk, completion = fake_client([
        [fake_chunk(usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2, total_tokens=5))],
        [fake_chunk(usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2, total_tokens=5))],
    ])
    client = GuardedChatClient(sdk, store, run_id, identity)
    request(client)
    request(client)
    view = store.get_run(run_id)
    assert completion.calls == 2
    assert view["known_input_tokens"] == 6
    assert view["known_output_tokens"] == 4
    assert view["unknown_usage_calls"] == 0
    assert all(item["status"] == "usage_valid" for item in view["model_calls"])


def test_missing_usage_latches_without_second_sdk_request(control):
    store, identity, _ = control
    token, run_id = grant(control)
    store.claim(token, "p2a", "a" * 64, identity)
    sdk, completion = fake_client([[fake_chunk()], [fake_chunk()]])
    client = GuardedChatClient(sdk, store, run_id, identity)
    with pytest.raises(C1ControlError, match="usage_unknown"):
        request(client)
    with pytest.raises(ModelConfigurationError) as blocked:
        request(client)
    assert "previous_usage_unknown" in blocked.value.blockers
    assert completion.calls == 1
    view = store.get_run(run_id)
    assert view["unknown_usage_calls"] == 1
    assert view["cost_complete"] is False


def test_sdk_disconnect_is_unknown_and_not_retried(control):
    store, identity, _ = control
    token, run_id = grant(control)
    store.claim(token, "p2a", "a" * 64, identity)
    sdk, completion = fake_client([ConnectionError("sensitive upstream details")])
    client = GuardedChatClient(sdk, store, run_id, identity)
    with pytest.raises(C1ControlError, match="usage_unknown"):
        request(client)
    assert completion.calls == 1
    assert "sensitive upstream details" not in json.dumps(store.get_run(run_id))


def test_budget_exhaustion_is_before_next_sdk_call(control):
    store, identity, budget = control
    budget = budget.model_copy(update={"max_calls": 1})
    token, run_id = store.authorize("p2a", "a" * 64, identity, budget,
                                    acknowledgement="P5C_STAGE_AUTHORIZED")
    store.claim(token, "p2a", "a" * 64, identity)
    sdk, completion = fake_client([
        [fake_chunk(usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2, total_tokens=5))],
        [fake_chunk(usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2, total_tokens=5))],
    ])
    client = GuardedChatClient(sdk, store, run_id, identity)
    request(client)
    with pytest.raises(ModelConfigurationError) as blocked:
        request(client)
    assert "request_budget_exhausted" in blocked.value.blockers
    assert completion.calls == 1


def test_recovery_keeps_dispatch_uncertain_without_replay(control):
    store, identity, _ = control
    token, run_id = grant(control)
    store.claim(token, "p2a", "a" * 64, identity)
    call_id, _ = store.reserve_request(run_id, identity, "gpt-6.1-sol",
                                       [{"role": "user", "content": "fixed"}])
    store.mark_dispatch_pending(call_id)
    assert store.get_run(run_id)["status"] == "unknown_usage"
    assert store.get_run(run_id)["cost_complete"] is False
    reopened = C1Store(store.path)
    reopened.recover_unknown_dispatches()
    assert reopened.get_run(run_id)["unknown_usage_calls"] == 1
    assert reopened.claim(token, "p2a", "a" * 64, identity) == (run_id, False)


def test_authorization_requires_separate_ack_and_internal_only(control):
    store, identity, budget = control
    with pytest.raises(C1ControlError, match="operator_ack_required"):
        store.authorize("p4", "p3-run", identity, budget, acknowledgement="")
    with pytest.raises(C1ControlError, match="report_scope_invalid"):
        store.authorize("p4", "p3-run", identity, budget,
                        acknowledgement="P5C_STAGE_AUTHORIZED", report_kind="author")


def test_second_active_authorization_is_rejected(control):
    store, identity, budget = control
    grant(control)
    with pytest.raises(C1ControlError, match="stage_already_authorized"):
        store.authorize("p2a", "a" * 64, identity, budget,
                        acknowledgement="P5C_STAGE_AUTHORIZED")


def test_c1_mode_cannot_be_activated_by_dotenv(tmp_path, monkeypatch):
    from backend.app import load_local_environment

    monkeypatch.delenv("P5C_CONTROLLED_MODE", raising=False)
    file = tmp_path / ".env.local"
    file.write_text("P5C_CONTROLLED_MODE=1\nP5C_PAPER_ID=sam-iclr2021\n",
                    encoding="utf-8")
    load_local_environment(file)
    assert os.environ.get("P5C_CONTROLLED_MODE") is None
    assert os.environ.get("P5C_PAPER_ID") is None


def test_offline_authorization_cli_writes_private_token_without_printing_it(
        tmp_path, monkeypatch, capsys):
    from scripts.p5c import authorize_stage
    import sys

    class FakeRuntime:
        def __init__(self, *args, **kwargs):
            pass

        def authorize(self, stage, upstream_id, budget, acknowledgement):
            assert stage == "p2a" and acknowledgement == "P5C_STAGE_AUTHORIZED"
            return "synthetic-authorization-token", "run-1"

    monkeypatch.setattr(authorize_stage, "C1Runtime", FakeRuntime)
    budget_path = tmp_path / "budget.json"
    budget_path.write_text(C1Budget(
        max_calls=1, max_input_tokens=100, max_output_tokens=8,
        max_output_tokens_per_request=8, max_duration_ms=10000,
        max_transport_retries=0,
        content_repair_policy="valid_usage_within_stage_budget",
        max_planned_cost_usd="0.01", input_usd_per_million="0.8",
        output_usd_per_million="4", pricing_status="estimated_unverified",
    ).model_dump_json())
    output = tmp_path / "authorization.json"
    monkeypatch.setattr(sys, "argv", [
        "authorize_stage", "--root", str(tmp_path), "--data-dir", str(tmp_path),
        "--stage", "p2a", "--upstream-run-id", "document-id",
        "--budget-json", str(budget_path), "--acknowledge", "P5C_STAGE_AUTHORIZED",
        "--output", str(output),
    ])
    assert authorize_stage.main() == 0
    assert json.loads(output.read_text())["authorization_token"] == "synthetic-authorization-token"
    assert output.stat().st_mode & 0o777 == 0o600
    assert "synthetic-authorization-token" not in capsys.readouterr().out


def test_completed_stage_cannot_be_paid_again(control):
    store, identity, budget = control
    token, run_id = grant(control)
    store.claim(token, "p2a", "a" * 64, identity)
    store.finish_stage(run_id, completed=True)
    with pytest.raises(C1ControlError, match="stage_already_completed"):
        store.authorize("p2a", "a" * 64, identity, budget,
                        acknowledgement="P5C_STAGE_AUTHORIZED")


@pytest.mark.parametrize("usage", [
    SimpleNamespace(prompt_tokens=-1, completion_tokens=2, total_tokens=1),
    SimpleNamespace(prompt_tokens=1.2, completion_tokens=2, total_tokens=3),
    SimpleNamespace(prompt_tokens=1, completion_tokens=2, total_tokens=4),
    SimpleNamespace(prompt_tokens=True, completion_tokens=2, total_tokens=3),
])
def test_invalid_usage_latches_before_another_request(control, usage):
    store, identity, _ = control
    token, run_id = grant(control)
    store.claim(token, "p2a", "a" * 64, identity)
    sdk, completion = fake_client([[fake_chunk(usage=usage)]])
    client = GuardedChatClient(sdk, store, run_id, identity)
    with pytest.raises(C1ControlError, match="usage_unknown"):
        request(client)
    with pytest.raises(ModelConfigurationError) as blocked:
        request(client)
    assert "previous_usage_unknown" in blocked.value.blockers
    assert completion.calls == 1
    assert store.get_run(run_id)["cost_complete"] is False


def test_actual_usage_over_output_cap_is_accounted_then_stops(control):
    store, identity, _ = control
    token, run_id = grant(control)
    store.claim(token, "p2a", "a" * 64, identity)
    sdk, completion = fake_client([
        [fake_chunk(usage=SimpleNamespace(prompt_tokens=3, completion_tokens=9,
                                          total_tokens=12))],
    ])
    client = GuardedChatClient(sdk, store, run_id, identity)
    with pytest.raises(C1ControlError, match="actual_budget_exhausted"):
        request(client)
    view = store.get_run(run_id)
    assert view["status"] == "budget_exhausted"
    assert view["known_output_tokens"] == 9
    assert view["unknown_usage_calls"] == 0
    assert completion.calls == 1


def test_existing_run_budget_does_not_call_known_overage_unknown():
    budget = RunBudget(max_calls=2, max_input_tokens=1000,
                       max_output_tokens=10000, max_duration_ms=10000)
    def known_overage(*_):
        raise C1ActualBudgetExceeded(42, 9, "gpt-6.1-sol")
    with pytest.raises(BudgetExceeded, match="actual_budget_exceeded"):
        budget.call("fake-stage", "s", "u", known_overage)
    assert budget.unknown_usage_calls == 0
    assert budget.reserved_input_tokens == 42
    assert budget.reserved_output_tokens == 9


def test_existing_run_budget_does_not_call_predispatch_block_unknown():
    budget = RunBudget(max_calls=2, max_input_tokens=1000,
                       max_output_tokens=10000, max_duration_ms=10000)
    def blocked(*_):
        raise ModelConfigurationError(["request_budget_exhausted"])
    with pytest.raises(ModelConfigurationError):
        budget.call("fake-stage", "s", "u", blocked)
    assert budget.calls == 0
    assert budget.unknown_usage_calls == 0


def test_reservation_failure_never_reaches_sdk(control, monkeypatch):
    store, identity, _ = control
    token, run_id = grant(control)
    store.claim(token, "p2a", "a" * 64, identity)
    sdk, completion = fake_client([])
    client = GuardedChatClient(sdk, store, run_id, identity)
    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("synthetic database failure")
    monkeypatch.setattr(store, "reserve_request", broken)
    with pytest.raises(ModelConfigurationError) as blocked:
        request(client)
    assert "c1_reservation_persist_failed" in blocked.value.blockers
    assert completion.calls == 0


def test_stale_dispatch_is_unknown_after_restart(control):
    store, identity, _ = control
    token, run_id = grant(control)
    store.claim(token, "p2a", "a" * 64, identity)
    call_id, _ = store.reserve_request(run_id, identity, identity.requested_model,
                                       [{"role": "user", "content": "x"}])
    store.mark_dispatch_pending(call_id)
    reopened = C1Store(store.path)
    reopened.recover_unknown_dispatches()
    assert reopened.get_run(run_id)["status"] == "unknown_usage"
    assert reopened.claim(token, "p2a", "a" * 64, identity) == (run_id, False)


def test_offline_authorization_runtime_does_not_recover_live_dispatch(
        control, tmp_path, monkeypatch):
    store, identity, _ = control
    token, run_id = grant(control)
    store.claim(token, "p2a", "a" * 64, identity)
    call_id, _ = store.reserve_request(run_id, identity, identity.requested_model,
                                       [{"role": "user", "content": "x"}])
    store.mark_dispatch_pending(call_id)
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    monkeypatch.setenv("P5C_OUTPUT_DIR", str(output))
    # Reopen the same ledger through the offline authorization path. A live
    # provider request must not be relabeled as a crashed request.
    (private / "p5c-stage-control.db").write_bytes(store.path.read_bytes())
    C1Runtime(private, root=Path(__file__).resolve().parents[2],
              environment_file=tmp_path / "synthetic.env", recover_on_start=False)
    with sqlite3.connect(private / "p5c-stage-control.db") as db:
        status = db.execute("SELECT status FROM c1_call WHERE model_call_id=?",
                            (call_id,)).fetchone()[0]
    assert status == "dispatch_pending"


def test_second_backend_process_cannot_recover_active_c1_trial(tmp_path, monkeypatch):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    monkeypatch.setenv("P5C_OUTPUT_DIR", str(output))
    first = C1Runtime(private, root=Path(__file__).resolve().parents[2],
                      environment_file=tmp_path / "synthetic.env")
    with pytest.raises(C1ControlError, match="single_worker_process_required"):
        C1Runtime(private, root=Path(__file__).resolve().parents[2],
                  environment_file=tmp_path / "synthetic.env")
    first.close()
    restarted = C1Runtime(private, root=Path(__file__).resolve().parents[2],
                          environment_file=tmp_path / "synthetic.env")
    restarted.close()


def test_verified_stage_completion_can_reconcile_after_restart(control):
    store, identity, _ = control
    token, run_id = grant(control)
    store.claim(token, "p2a", "a" * 64, identity)
    sdk, _ = fake_client([
        [fake_chunk(usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2,
                                          total_tokens=5))],
    ])
    request(GuardedChatClient(sdk, store, run_id, identity))
    reopened = C1Store(store.path)
    reopened.recover_unknown_dispatches()
    assert reopened.get_run(run_id)["status"] == "interrupted"
    reopened.recover_verified_completion(run_id)
    assert reopened.get_run(run_id)["status"] == "completed"


def test_changed_identity_blocks_before_sdk(control):
    store, identity, _ = control
    token, run_id = grant(control)
    store.claim(token, "p2a", "a" * 64, identity)
    sdk, completion = fake_client([])
    drifted = identity.model_copy(update={"execution_tree": "0" * 40})
    client = GuardedChatClient(sdk, store, run_id, identity, lambda: drifted)
    with pytest.raises(ModelConfigurationError) as blocked:
        request(client)
    assert "execution_identity_mismatch" in blocked.value.blockers
    assert completion.calls == 0


def test_c1_p4_budget_must_match_all_nine_runtime_values(control):
    store, identity, _ = control
    budget = C1Budget(max_calls=8, max_input_tokens=200000,
                      max_output_tokens=32000, max_output_tokens_per_request=2000,
                      max_duration_ms=1800000, max_transport_retries=0,
                      content_repair_policy="valid_usage_within_stage_budget",
                      max_planned_cost_usd="0.30", input_usd_per_million="0.80",
                      output_usd_per_million="4.00", pricing_status="estimated_unverified")
    _, run_id = store.authorize("p4", "project-id", identity, budget,
                                 acknowledgement="P5C_STAGE_AUTHORIZED")
    runtime = object.__new__(C1Runtime)
    runtime.store = store
    actual = SimpleNamespace(
        max_provider_requests=8, max_input_tokens=200000, max_output_tokens=32000,
        max_output_tokens_per_request=2000, max_duration_seconds=Decimal("1800"),
        max_planned_cost_usd=Decimal("0.30"),
        input_usd_per_million=Decimal("0.80"),
        output_usd_per_million=Decimal("4.00"),
        pricing_status="estimated_unverified",
    )
    runtime.check_p4_budget(run_id, actual)
    actual.max_provider_requests = 9
    with pytest.raises(C1ControlError, match="p4_budget_configuration_mismatch"):
        runtime.check_p4_budget(run_id, actual)


def test_paid_http_requires_token_and_lost_response_retry_reuses_run(tmp_path, monkeypatch):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    os.chmod(private, 0o700)
    output_dir = tmp_path / "private-output"
    output_dir.mkdir(mode=0o700)
    monkeypatch.setenv("P5C_OUTPUT_DIR", str(output_dir))
    configure_test_model(monkeypatch, key="synthetic-only", model="gpt-6.1-sol")
    monkeypatch.setenv("P5C_CONTROLLED_MODE", "1")
    monkeypatch.setenv("PAPER_REVIEW_WORKER_COUNT", "1")
    pdf = _pdf()
    import hashlib
    identity = C1Identity(
        paper_id="sardet100k-neurips2024", pdf_sha256=hashlib.sha256(pdf).hexdigest(),
        manifest_sha256="b" * 64, execution_lock_sha256="c" * 64,
        execution_commit="d" * 40, execution_tree="e" * 40,
        requested_model="gpt-6.1-sol", model_config_sha256="f" * 64,
    )
    monkeypatch.setattr(C1Runtime, "identity", lambda self: identity)
    attempts = []

    class FakeOpenAI:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def create(self, **kwargs):
            attempts.append(kwargs)
            raise ConnectionError("synthetic secret error")

    monkeypatch.setattr("backend.app.OpenAI", FakeOpenAI)
    client = TestClient(create_app(private))
    doc = client.post("/api/documents", files={"file": ("paper.pdf", io.BytesIO(pdf),
                                                "application/pdf")}).json()
    route = f'/api/documents/{doc["id"]}/understanding-runs'
    denied = client.post(route, json={})
    assert denied.status_code == 409
    assert denied.json()["detail"]["code"] == "authorization_missing"
    assert attempts == []
    blocked_report = client.post("/api/review-report-projects/absent/drafts",
                                 json={"kind": "author", "language": "zh"})
    assert blocked_report.status_code == 409
    assert blocked_report.json()["detail"]["code"] == "c1_report_scope_invalid"
    unpaid_report = client.post("/api/review-report-projects/absent/drafts",
                                json={"kind": "internal", "language": "zh"})
    assert unpaid_report.status_code == 409
    assert unpaid_report.json()["detail"]["code"] == "authorization_missing"
    assert attempts == []
    store = C1Store(private / "p5c-stage-control.db")
    store.freeze_trial(identity)
    budget = C1Budget(max_calls=1, max_input_tokens=100000,
                      max_output_tokens=4096, max_output_tokens_per_request=4096,
                      max_duration_ms=100000, max_transport_retries=0,
                      content_repair_policy="valid_usage_within_stage_budget",
                      max_planned_cost_usd="1", input_usd_per_million="0.8",
                      output_usd_per_million="4", pricing_status="estimated_unverified")
    token, run_id = store.authorize("p2a", doc["id"], identity, budget,
                                    acknowledgement="P5C_STAGE_AUTHORIZED")
    headers = {"X-P5C-Stage-Authorization": token}
    with ThreadPoolExecutor(max_workers=4) as pool:
        concurrent = list(pool.map(lambda _: client.post(route, json={}, headers=headers),
                                   range(4)))
    assert all(response.status_code == 202 and response.json()["id"] == run_id
               for response in concurrent)
    created = concurrent[0]
    replay = client.post(route, json={}, headers=headers)
    assert created.status_code == 202
    assert created.json()["id"] == replay.json()["id"] == run_id
    assert len(attempts) == 1
    audit = client.get(f"/api/p5c/stage-runs/{run_id}").json()
    assert audit["unknown_usage_calls"] == 1
    assert "synthetic secret error" not in json.dumps(audit)
    assert audit["provider_billed_cost_usd"] is None


def test_fake_c1_p2a_completes_and_enables_only_next_stage(tmp_path, monkeypatch):
    from hashlib import sha256

    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    output_dir = tmp_path / "private-output"
    output_dir.mkdir(mode=0o700)
    monkeypatch.setenv("P5C_OUTPUT_DIR", str(output_dir))
    monkeypatch.setenv("P5C_CONTROLLED_MODE", "1")
    monkeypatch.setenv("PAPER_REVIEW_WORKER_COUNT", "1")
    configure_test_model(monkeypatch, key="synthetic-only", model="gpt-6.1-sol")
    pdf = _pdf()
    identity = C1Identity(
        paper_id="sardet100k-neurips2024", pdf_sha256=sha256(pdf).hexdigest(),
        manifest_sha256="b" * 64, execution_lock_sha256="c" * 64,
        execution_commit="d" * 40, execution_tree="e" * 40,
        requested_model="gpt-6.1-sol", model_config_sha256="f" * 64,
    )
    monkeypatch.setattr(C1Runtime, "identity", lambda self: identity)
    calls = []

    class FakeOpenAI:
        def __init__(self, **kwargs):
            assert kwargs["max_retries"] == 0
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def create(self, **kwargs):
            calls.append(kwargs)
            chunks = _fake_completion(kwargs["messages"])
            for chunk in chunks:
                chunk.model = "gpt-6.1-sol"
                if chunk.usage is not None:
                    chunk.usage.total_tokens = 100
            return iter(chunks)

    monkeypatch.setattr("backend.app.OpenAI", FakeOpenAI)
    client = TestClient(create_app(private))
    doc = client.post("/api/documents", files={"file": ("paper.pdf", io.BytesIO(pdf),
                                                "application/pdf")}).json()
    store = C1Store(private / "p5c-stage-control.db")
    store.freeze_trial(identity)
    budget = C1Budget(max_calls=16, max_input_tokens=100000,
                      max_output_tokens=50000, max_output_tokens_per_request=4096,
                      max_duration_ms=100000, max_transport_retries=0,
                      content_repair_policy="valid_usage_within_stage_budget",
                      max_planned_cost_usd="1", input_usd_per_million="0.8",
                      output_usd_per_million="4", pricing_status="estimated_unverified")
    token, run_id = store.authorize("p2a", doc["id"], identity, budget,
                                    acknowledgement="P5C_STAGE_AUTHORIZED")
    created = client.post(f'/api/documents/{doc["id"]}/understanding-runs',
                          json={}, headers={"X-P5C-Stage-Authorization": token})
    assert created.status_code == 202
    assert client.get(f"/api/understanding-runs/{run_id}").json()["status"] == "completed"
    assert store.get_run(run_id)["status"] == "completed"
    assert len(calls) == 4
    assert all(call["max_tokens"] == 4096 and call["model"] == "gpt-6.1-sol"
               for call in calls)
    p2b_token, _ = store.authorize("p2b", run_id, identity, budget,
                                   acknowledgement="P5C_STAGE_AUTHORIZED")
    assert p2b_token != token
    runtime = C1Runtime(private, root=Path(__file__).resolve().parents[2],
                        environment_file=tmp_path / "fake.env", recover_on_start=False)
    runtime.validate_source("p2b", run_id, identity)
    (private / "files" / f'{doc["id"]}.pdf').write_bytes(pdf + b"tampered")
    with pytest.raises(C1ControlError, match="trial_pdf_mismatch"):
        runtime.validate_source("p2b", run_id, identity)
    assert client.get("/api/professional-review-runs/absent").status_code == 404
