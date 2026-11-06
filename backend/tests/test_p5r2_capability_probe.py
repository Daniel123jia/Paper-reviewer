import json
from types import SimpleNamespace

import pytest

from scripts.p5r2.capability_probe import (
    AUTHORIZATION_ACK,
    FROZEN_MODEL,
    ProbeAuthorizationError,
    build_configuration_report,
    main,
    run_capability_probe,
)


def configured_environment(**overrides):
    environment = {
        "OPENAI_API_KEY": "configured-test-secret",
        "OPENAI_BASE_URL": "https://provider.example/v1",
        "OPENAI_MODEL": FROZEN_MODEL,
        "OPENAI_INPUT_USD_PER_MILLION": "1.5",
        "OPENAI_OUTPUT_USD_PER_MILLION": "6",
    }
    environment.update(overrides)
    return environment


def test_configuration_report_is_redacted_and_does_not_run_a_probe():
    environment = configured_environment()

    report = build_configuration_report(environment, FROZEN_MODEL)

    encoded = json.dumps(report)
    assert report["configuration_ready"] is True
    assert report["credential_status"] == "configured"
    assert report["endpoint_status"] == "configured_valid"
    assert report["requested_model"] == FROZEN_MODEL
    assert report["probe"] == {
        "status": "not_run_pending_authorization",
        "network_attempted": False,
        "model_calls": 0,
    }
    assert report["pricing"]["status"] == "operator_configured"
    assert report["pricing"]["estimated_probe_ceiling_usd"] == 0.000576
    assert "configured-test-secret" not in encoded
    assert "provider.example" not in encoded
    assert "OPENAI_API_KEY" not in encoded
    assert "Authorization" not in encoded


@pytest.mark.parametrize(
    ("overrides", "blocker"),
    [
        ({"OPENAI_API_KEY": ""}, "model_credential_not_configured"),
        ({"OPENAI_API_KEY": "replace-with-your-api-key"}, "model_credential_is_placeholder"),
        ({"OPENAI_BASE_URL": ""}, "model_endpoint_not_explicitly_configured"),
        ({"OPENAI_BASE_URL": "https://api.example.com/v1"}, "model_endpoint_is_placeholder"),
        ({"OPENAI_BASE_URL": "not-a-url"}, "model_endpoint_invalid"),
        ({"OPENAI_BASE_URL": "https://provider.example/v1?api_key=secret"}, "model_endpoint_invalid"),
        ({"OPENAI_MODEL": ""}, "requested_model_not_explicitly_configured"),
        ({"OPENAI_MODEL": "different-model"}, "requested_model_mismatch"),
        ({"OPENAI_INPUT_USD_PER_MILLION": ""}, "pricing_not_configured"),
        ({"OPENAI_OUTPUT_USD_PER_MILLION": "unknown"}, "pricing_invalid"),
        ({"OPENAI_OUTPUT_USD_PER_MILLION": "Infinity"}, "pricing_invalid"),
    ],
)
def test_configuration_report_fails_closed(overrides, blocker):
    report = build_configuration_report(configured_environment(**overrides), FROZEN_MODEL)

    assert report["configuration_ready"] is False
    assert blocker in report["blockers"]
    assert report["probe"]["model_calls"] == 0


def test_default_cli_never_constructs_a_model_client(tmp_path):
    called = False

    def forbidden_client(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError("client must not be constructed in check-only mode")

    environment_file = tmp_path / ".env.local"
    environment_file.write_text(
        "OPENAI_API_KEY=configured-test-secret\n"
        "OPENAI_BASE_URL=https://provider.example/v1\n"
        f"OPENAI_MODEL={FROZEN_MODEL}\n"
        "OPENAI_INPUT_USD_PER_MILLION=1.5\n"
        "OPENAI_OUTPUT_USD_PER_MILLION=6\n",
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"requested_model": FROZEN_MODEL}), encoding="utf-8")
    output = tmp_path / "report.json"

    assert main([
        "--environment-file", str(environment_file),
        "--manifest", str(manifest),
        "--output", str(output),
    ], client_factory=forbidden_client) == 0

    report = json.loads(output.read_text(encoding="utf-8"))
    assert called is False
    assert report["probe"]["network_attempted"] is False
    assert report["probe"]["model_calls"] == 0


def test_execute_requires_exact_authorization_before_client_construction():
    called = False

    def forbidden_client(**_kwargs):
        nonlocal called
        called = True

    with pytest.raises(ProbeAuthorizationError, match="explicit authorization"):
        run_capability_probe(
            configured_environment(), FROZEN_MODEL, "not-authorized", forbidden_client
        )

    assert called is False


class FakeStreamClient:
    def __init__(self, chunks):
        self.chunks = chunks
        self.calls = []
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self.create)
        )

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return iter(self.chunks)


def successful_chunks(*, actual_model=FROZEN_MODEL, usage=True, content=None):
    response = content or '{"ok":true,"probe":"p5r2"}'
    chunks = [
        SimpleNamespace(
            model=actual_model,
            choices=[SimpleNamespace(delta=SimpleNamespace(content=response))],
            usage=None,
        )
    ]
    chunks.append(SimpleNamespace(
        model=actual_model,
        choices=[],
        usage=(SimpleNamespace(prompt_tokens=18, completion_tokens=11, total_tokens=29)
               if usage else None),
    ))
    return chunks


def test_authorized_probe_makes_one_bounded_streaming_call():
    client = FakeStreamClient(successful_chunks())
    factory_calls = []

    def factory(**kwargs):
        factory_calls.append(kwargs)
        return client

    result = run_capability_probe(
        configured_environment(), FROZEN_MODEL, AUTHORIZATION_ACK, factory,
        clock=iter([10.0, 10.125]).__next__,
    )

    assert result["status"] == "passed"
    assert result["network_attempted"] is True
    assert result["model_calls"] == 1
    assert result["requested_model"] == result["actual_model"] == FROZEN_MODEL
    assert result["structured_json"] == {"ok": True, "probe": "p5r2"}
    assert result["usage"] == {
        "status": "complete",
        "input_tokens": 18,
        "output_tokens": 11,
        "total_tokens": 29,
    }
    assert result["cost"] == {
        "status": "operator_rate_calculation",
        "actual_cost_usd": 0.000093,
    }
    assert result["duration_ms"] == 125
    assert len(factory_calls) == 1
    assert factory_calls[0]["timeout"] == 180
    assert factory_calls[0]["max_retries"] == 0
    request = client.calls[0]
    assert request["model"] == FROZEN_MODEL
    assert request["stream"] is True
    assert request["stream_options"] == {"include_usage": True}
    assert request["max_tokens"] == 64


def test_probe_fails_closed_for_model_mismatch_or_missing_usage():
    mismatch = FakeStreamClient(successful_chunks(actual_model="provider-alias"))
    mismatch_result = run_capability_probe(
        configured_environment(), FROZEN_MODEL, AUTHORIZATION_ACK,
        lambda **_kwargs: mismatch,
    )
    missing_usage = FakeStreamClient(successful_chunks(usage=False))
    usage_result = run_capability_probe(
        configured_environment(), FROZEN_MODEL, AUTHORIZATION_ACK,
        lambda **_kwargs: missing_usage,
    )

    assert mismatch_result["status"] == "failed"
    assert mismatch_result["blockers"] == ["actual_model_mismatch"]
    assert usage_result["status"] == "failed"
    assert usage_result["usage"]["status"] == "incomplete"
    assert usage_result["blockers"] == ["usage_reporting_incomplete"]


def test_probe_rejects_invalid_json_and_sanitizes_provider_error():
    invalid = FakeStreamClient(successful_chunks(content="not-json"))
    invalid_result = run_capability_probe(
        configured_environment(), FROZEN_MODEL, AUTHORIZATION_ACK,
        lambda **_kwargs: invalid,
    )

    class ProviderFailure(RuntimeError):
        status_code = 401

    def failed_factory(**_kwargs):
        raise ProviderFailure("Bearer configured-test-secret at provider.example")

    failed_result = run_capability_probe(
        configured_environment(), FROZEN_MODEL, AUTHORIZATION_ACK, failed_factory,
    )

    assert invalid_result["status"] == "failed"
    assert invalid_result["blockers"] == ["structured_json_invalid"]
    assert failed_result["status"] == "failed"
    assert failed_result["error"] == {"type": "ProviderFailure", "status_code": 401}
    assert "configured-test-secret" not in json.dumps(failed_result)
    assert "provider.example" not in json.dumps(failed_result)
