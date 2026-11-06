import json
from datetime import date
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
        "OPENAI_PRICING_SOURCE_URL": "https://billing.provider.example/pricing",
        "OPENAI_PRICING_VERIFIED_AT": date.today().isoformat(),
        "OPENAI_PRICING_VERIFICATION": "provider_source_checked",
    }
    environment.update(overrides)
    return environment


def test_configuration_report_is_redacted_and_does_not_run_a_probe():
    environment = configured_environment()

    report = build_configuration_report(environment, FROZEN_MODEL)

    encoded = json.dumps(report)
    assert report["probe_schema_version"] == "1.3"
    assert report["configuration_ready"] is True
    assert report["credential_status"] == "configured"
    assert report["endpoint_status"] == "configured_valid"
    assert report["requested_model"] == FROZEN_MODEL
    assert report["probe"] == {
        "status": "not_run_pending_authorization",
        "network_attempted": False,
        "model_calls": 0,
    }
    assert report["pricing"]["status"] == "operator_attested_provider_pricing"
    assert report["pricing"]["estimated_probe_cost_usd"] == 0.000576
    assert report["pricing"]["independently_verified"] is False
    assert report["pricing"]["account_hard_limit"] is False
    assert report["limits"]["input_tokens"] == {
        "kind": "planning_estimate",
        "estimated_tokens": 128,
        "enforced": False,
    }
    assert report["limits"]["output_tokens"] == {
        "requested_tokens": 64,
        "request_parameter": "max_tokens",
        "provider_enforcement": "pending_real_probe",
    }
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
        ({"OPENAI_PRICING_SOURCE_URL": ""}, "provider_pricing_not_verified"),
        ({"OPENAI_PRICING_VERIFIED_AT": ""}, "provider_pricing_not_verified"),
        ({"OPENAI_PRICING_VERIFIED_AT": "not-a-date"}, "provider_pricing_not_verified"),
        ({"OPENAI_PRICING_VERIFICATION": ""}, "provider_pricing_not_verified"),
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
        "OPENAI_OUTPUT_USD_PER_MILLION=6\n"
        "OPENAI_PRICING_SOURCE_URL=https://billing.provider.example/pricing\n"
        f"OPENAI_PRICING_VERIFIED_AT={date.today().isoformat()}\n"
        "OPENAI_PRICING_VERIFICATION=provider_source_checked\n",
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


def test_rate_values_without_provider_evidence_remain_blocked():
    report = build_configuration_report(configured_environment(
        OPENAI_PRICING_SOURCE_URL="",
        OPENAI_PRICING_VERIFIED_AT="",
        OPENAI_PRICING_VERIFICATION="",
    ), FROZEN_MODEL)

    assert report["configuration_ready"] is False
    assert report["pricing"]["status"] == "operator_configured_unverified"
    assert report["pricing"]["basis"] == \
        "operator-supplied rates without valid provider-source attestation"
    assert report["pricing"]["account_hard_limit"] is False
    assert "provider_pricing_not_verified" in report["blockers"]


def test_estimated_pricing_requires_explicit_mode_and_reports_unverified_scope():
    environment = configured_environment(
        OPENAI_INPUT_USD_PER_MILLION="0.80",
        OPENAI_OUTPUT_USD_PER_MILLION="4.00",
        OPENAI_PRICING_SOURCE_URL="",
        OPENAI_PRICING_VERIFIED_AT="",
        OPENAI_PRICING_VERIFICATION="",
    )

    strict = build_configuration_report(environment, FROZEN_MODEL)
    estimated = build_configuration_report(
        environment,
        FROZEN_MODEL,
        allow_estimated_pricing=True,
    )

    assert strict["configuration_ready"] is False
    assert strict["pricing"]["status"] == "operator_configured_unverified"
    assert "provider_pricing_not_verified" in strict["blockers"]
    assert estimated["probe_schema_version"] == "1.3"
    assert estimated["configuration_ready"] is True
    assert estimated["pricing"] == {
        "status": "estimated_unverified",
        "estimated_probe_cost_usd": 0.0003584,
        "basis": (
            "operator-supplied unverified planning rates; "
            "input usage remains an estimate"
        ),
        "source_evidence_status": "not_provided_or_not_used",
        "scope": "p5r3b_single_nonpaper_capability_probe",
        "independently_verified": False,
        "account_hard_limit": False,
    }
    assert estimated["blockers"] == []


@pytest.mark.parametrize(
    ("input_rate", "output_rate", "blocker"),
    [
        ("", "4.00", "pricing_not_configured"),
        ("0.80", "", "pricing_not_configured"),
        ("invalid", "4.00", "pricing_invalid"),
        ("0.80", "-1", "pricing_invalid"),
    ],
)
def test_estimated_pricing_still_rejects_missing_or_invalid_rates(
        input_rate, output_rate, blocker):
    report = build_configuration_report(
        configured_environment(
            OPENAI_INPUT_USD_PER_MILLION=input_rate,
            OPENAI_OUTPUT_USD_PER_MILLION=output_rate,
            OPENAI_PRICING_SOURCE_URL="",
            OPENAI_PRICING_VERIFIED_AT="",
            OPENAI_PRICING_VERIFICATION="",
        ),
        FROZEN_MODEL,
        allow_estimated_pricing=True,
    )

    assert report["configuration_ready"] is False
    assert blocker in report["blockers"]


def test_pricing_verification_date_rejects_future_and_expired_evidence():
    today = date(2026, 11, 6)

    future = build_configuration_report(
        configured_environment(OPENAI_PRICING_VERIFIED_AT="2026-11-07"),
        FROZEN_MODEL,
        today=today,
    )
    expired = build_configuration_report(
        configured_environment(OPENAI_PRICING_VERIFIED_AT="2026-10-06"),
        FROZEN_MODEL,
        today=today,
    )
    boundary = build_configuration_report(
        configured_environment(OPENAI_PRICING_VERIFIED_AT="2026-10-07"),
        FROZEN_MODEL,
        today=today,
    )

    assert future["configuration_ready"] is False
    assert future["pricing"]["source_evidence_status"] == "future_dated"
    assert "provider_pricing_verification_future_dated" in future["blockers"]
    assert expired["configuration_ready"] is False
    assert expired["pricing"]["source_evidence_status"] == "expired"
    assert "provider_pricing_verification_expired" in expired["blockers"]
    assert boundary["configuration_ready"] is True
    assert boundary["pricing"]["source_evidence_status"] == "operator_attested"


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


def successful_chunks(*, actual_model=FROZEN_MODEL, usage=True, content=None,
                      input_tokens=18, output_tokens=11, total_tokens="auto"):
    response = content or '{"ok":true,"probe":"p5r2"}'
    if total_tokens == "auto":
        total_tokens = input_tokens + output_tokens
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
        usage=(SimpleNamespace(prompt_tokens=input_tokens, completion_tokens=output_tokens,
                               total_tokens=total_tokens)
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
    assert result["probe_schema_version"] == "1.3"
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
        "status": "operator_attested_rate_calculation",
        "actual_cost_usd": 0.000093,
        "provider_billed_cost_usd": None,
        "is_provider_billed_cost": False,
        "account_hard_limit": False,
    }
    assert result["budget_observation"] == {
        "input_tokens": {
            "estimate": 128,
            "actual": 18,
            "status": "within_estimate",
        },
        "output_tokens": {
            "requested_limit": 64,
            "actual": 11,
            "status": "within_requested_limit",
        },
    }
    assert result["output_limit_compatibility"] == {
        "parameter": "max_tokens",
        "status": "accepted_within_observed_limit",
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


def test_authorized_estimated_probe_preserves_request_limits_and_cost_semantics():
    client = FakeStreamClient(successful_chunks())
    factory_calls = []
    environment = configured_environment(
        OPENAI_INPUT_USD_PER_MILLION="0.80",
        OPENAI_OUTPUT_USD_PER_MILLION="4.00",
        OPENAI_PRICING_SOURCE_URL="",
        OPENAI_PRICING_VERIFIED_AT="",
        OPENAI_PRICING_VERIFICATION="",
    )

    def factory(**kwargs):
        factory_calls.append(kwargs)
        return client

    result = run_capability_probe(
        environment,
        FROZEN_MODEL,
        AUTHORIZATION_ACK,
        factory,
        allow_estimated_pricing=True,
        clock=iter([10.0, 10.125]).__next__,
    )

    assert result["status"] == "passed"
    assert result["pricing"]["status"] == "estimated_unverified"
    assert result["pricing"]["scope"] == \
        "p5r3b_single_nonpaper_capability_probe"
    assert result["cost"] == {
        "status": "estimated_unverified_rate_calculation",
        "actual_cost_usd": 0.0000584,
        "provider_billed_cost_usd": None,
        "is_provider_billed_cost": False,
        "account_hard_limit": False,
    }
    assert result["model_calls"] == 1
    assert len(factory_calls) == 1
    assert factory_calls[0]["timeout"] == 180
    assert factory_calls[0]["max_retries"] == 0
    assert client.calls == [{
        "model": FROZEN_MODEL,
        "messages": [
            {
                "role": "system",
                "content": "Return exactly one compact JSON object and no Markdown.",
            },
            {
                "role": "user",
                "content": 'Return {"ok":true,"probe":"p5r2"}.',
            },
        ],
        "stream": True,
        "stream_options": {"include_usage": True},
        "max_tokens": 64,
    }]


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


@pytest.mark.parametrize(
    ("chunks", "blocker", "budget_field", "budget_status"),
    [
        (successful_chunks(input_tokens=129), "input_token_estimate_exceeded",
         "input_tokens", "estimate_exceeded"),
        (successful_chunks(output_tokens=65), "output_token_limit_exceeded",
         "output_tokens", "requested_limit_exceeded"),
    ],
)
def test_probe_fails_when_reported_usage_exceeds_planning_budget(
        chunks, blocker, budget_field, budget_status):
    client = FakeStreamClient(chunks)

    result = run_capability_probe(
        configured_environment(), FROZEN_MODEL, AUTHORIZATION_ACK,
        lambda **_kwargs: client,
    )

    assert result["status"] == "failed"
    assert blocker in result["blockers"]
    assert result["budget_observation"][budget_field]["status"] == budget_status
    if budget_field == "output_tokens":
        assert result["output_limit_compatibility"]["status"] == \
            "accepted_but_reported_usage_exceeded"


@pytest.mark.parametrize(
    ("usage_values", "validation_error"),
    [
        ({"input_tokens": -1}, "input_tokens_negative"),
        ({"output_tokens": -1}, "output_tokens_negative"),
        ({"total_tokens": -1}, "total_tokens_negative"),
        ({"input_tokens": 18.0, "total_tokens": 29}, "input_tokens_not_integer"),
        ({"output_tokens": "11", "total_tokens": 29}, "output_tokens_not_integer"),
        ({"total_tokens": 29.0}, "total_tokens_not_integer"),
        ({"input_tokens": True, "total_tokens": 12}, "input_tokens_not_integer"),
        ({"total_tokens": 30}, "total_tokens_inconsistent"),
    ],
)
def test_probe_rejects_invalid_or_inconsistent_usage(usage_values, validation_error):
    client = FakeStreamClient(successful_chunks(**usage_values))

    result = run_capability_probe(
        configured_environment(), FROZEN_MODEL, AUTHORIZATION_ACK,
        lambda **_kwargs: client,
    )

    assert result["status"] == "failed"
    assert result["usage"]["status"] == "invalid"
    assert validation_error in result["usage"]["validation_errors"]
    assert "usage_reporting_invalid" in result["blockers"]
    assert result["cost"]["status"] == "unknown"
    assert result["cost"]["actual_cost_usd"] is None
    assert "provider_request_failed" not in result["blockers"]


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


def write_cli_inputs(tmp_path, environment=None):
    environment = configured_environment() if environment is None else environment
    environment_file = tmp_path / ".env.local"
    environment_file.write_text(
        "".join(f"{key}={value}\n" for key, value in environment.items()),
        encoding="utf-8",
    )
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"requested_model": FROZEN_MODEL}), encoding="utf-8")
    return environment_file, manifest, tmp_path / "report.json"


def cli_args(
        environment_file, manifest, output, *, execute=False, authorized=False,
        estimated=False):
    args = [
        "--environment-file", str(environment_file),
        "--manifest", str(manifest),
        "--output", str(output),
    ]
    if execute:
        args.append("--execute")
    if authorized:
        args.extend(["--authorization-ack", AUTHORIZATION_ACK])
    if estimated:
        args.append("--allow-estimated-pricing")
    return args


def test_check_only_returns_zero_even_when_configuration_is_blocked(tmp_path):
    environment_file, manifest, output = write_cli_inputs(
        tmp_path, configured_environment(OPENAI_API_KEY="")
    )

    exit_code = main(cli_args(environment_file, manifest, output))

    assert exit_code == 0
    assert json.loads(output.read_text(encoding="utf-8"))["configuration_ready"] is False


def test_cli_returns_zero_for_successful_authorized_probe(tmp_path):
    environment_file, manifest, output = write_cli_inputs(tmp_path)
    client = FakeStreamClient(successful_chunks())

    exit_code = main(
        cli_args(environment_file, manifest, output, execute=True, authorized=True),
        client_factory=lambda **_kwargs: client,
    )

    assert exit_code == 0
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "passed"


def test_estimated_pricing_flag_does_not_bypass_authorization(tmp_path):
    called = False

    def forbidden_client(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError("estimated pricing must not authorize a request")

    environment_file, manifest, output = write_cli_inputs(
        tmp_path,
        configured_environment(
            OPENAI_INPUT_USD_PER_MILLION="0.80",
            OPENAI_OUTPUT_USD_PER_MILLION="4.00",
            OPENAI_PRICING_SOURCE_URL="",
            OPENAI_PRICING_VERIFIED_AT="",
            OPENAI_PRICING_VERIFICATION="",
        ),
    )

    exit_code = main(
        cli_args(
            environment_file,
            manifest,
            output,
            execute=True,
            estimated=True,
        ),
        client_factory=forbidden_client,
    )

    report = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 2
    assert called is False
    assert report["network_attempted"] is False
    assert report["model_calls"] == 0
    assert "probe_authorization_missing" in report["blockers"]


def test_cli_returns_nonzero_and_saves_redacted_failed_probe(tmp_path):
    environment_file, manifest, output = write_cli_inputs(tmp_path)

    class ProviderFailure(RuntimeError):
        status_code = 503

    def failed_factory(**_kwargs):
        raise ProviderFailure("configured-test-secret provider.example")

    exit_code = main(
        cli_args(environment_file, manifest, output, execute=True, authorized=True),
        client_factory=failed_factory,
    )

    encoded = output.read_text(encoding="utf-8")
    report = json.loads(encoded)
    assert exit_code == 1
    assert report["status"] == "failed"
    assert report["error"] == {"type": "ProviderFailure", "status_code": 503}
    assert "configured-test-secret" not in encoded
    assert "provider.example" not in encoded


@pytest.mark.parametrize("authorized", [False, True])
def test_cli_pre_request_block_returns_two_saves_report_and_never_constructs_client(
        tmp_path, authorized):
    called = False

    def forbidden_client(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError("blocked probe must not construct client")

    environment = (configured_environment() if not authorized else
                   configured_environment(OPENAI_API_KEY=""))
    environment_file, manifest, output = write_cli_inputs(tmp_path, environment)

    exit_code = main(
        cli_args(environment_file, manifest, output, execute=True, authorized=authorized),
        client_factory=forbidden_client,
    )

    report = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 2
    assert called is False
    assert report["status"] == "failed"
    assert report["network_attempted"] is False
    assert report["model_calls"] == 0
    expected = ("model_credential_not_configured" if authorized
                else "probe_authorization_missing")
    assert expected in report["blockers"]


def test_cli_unknown_pricing_blocks_before_client_construction(tmp_path):
    called = False

    def forbidden_client(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError("unknown pricing must block before client construction")

    environment_file, manifest, output = write_cli_inputs(
        tmp_path,
        configured_environment(
            OPENAI_INPUT_USD_PER_MILLION="",
            OPENAI_OUTPUT_USD_PER_MILLION="",
        ),
    )

    exit_code = main(
        cli_args(environment_file, manifest, output, execute=True, authorized=True),
        client_factory=forbidden_client,
    )

    report = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 2
    assert called is False
    assert "pricing_not_configured" in report["blockers"]


def test_request_network_error_is_sanitized_and_counted():
    class NetworkFailure(RuntimeError):
        status_code = 502

    class FailingCompletions:
        def create(self, **_kwargs):
            raise NetworkFailure("configured-test-secret provider.example")

    client = SimpleNamespace(
        chat=SimpleNamespace(completions=FailingCompletions())
    )

    result = run_capability_probe(
        configured_environment(), FROZEN_MODEL, AUTHORIZATION_ACK,
        lambda **_kwargs: client,
    )

    encoded = json.dumps(result)
    assert result["status"] == "failed"
    assert result["network_attempted"] is True
    assert result["model_calls"] == 1
    assert result["error"] == {"type": "NetworkFailure", "status_code": 502}
    assert "configured-test-secret" not in encoded
    assert "provider.example" not in encoded


@pytest.mark.parametrize(
    ("manifest_setup", "expected_blocker"),
    [
        ("missing", "manifest_file_not_found"),
        ("invalid_json", "manifest_json_invalid"),
        ("missing_model", "manifest_requested_model_invalid"),
        ("non_string_model", "manifest_requested_model_invalid"),
        ("wrong_model", "manifest_requested_model_mismatch"),
    ],
)
def test_manifest_errors_fail_before_client_and_save_redacted_report(
        tmp_path, manifest_setup, expected_blocker):
    called = False

    def forbidden_client(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError("invalid manifest must block before client construction")

    environment_file, manifest, output = write_cli_inputs(tmp_path)
    if manifest_setup == "missing":
        manifest.unlink()
    elif manifest_setup == "invalid_json":
        manifest.write_text('{"requested_model":"configured-test-secret"', encoding="utf-8")
    elif manifest_setup == "missing_model":
        manifest.write_text("{}", encoding="utf-8")
    elif manifest_setup == "non_string_model":
        manifest.write_text('{"requested_model":42}', encoding="utf-8")
    else:
        manifest.write_text('{"requested_model":"different-model"}', encoding="utf-8")

    exit_code = main(
        cli_args(environment_file, manifest, output, execute=True, authorized=True),
        client_factory=forbidden_client,
    )

    encoded = output.read_text(encoding="utf-8")
    report = json.loads(encoded)
    assert exit_code == 2
    assert called is False
    assert report["status"] == "failed"
    assert report["network_attempted"] is False
    assert expected_blocker in report["blockers"]
    assert "configured-test-secret" not in encoded
    assert "different-model" not in encoded


def test_output_path_is_verified_before_manifest_or_client(tmp_path):
    called = False
    environment_file, manifest, _output = write_cli_inputs(tmp_path)
    parent_file = tmp_path / "not-a-directory"
    parent_file.write_text("occupied", encoding="utf-8")
    output = parent_file / "report.json"

    def forbidden_client(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError("unusable output path must block before client construction")

    exit_code = main(
        cli_args(environment_file, manifest, output, execute=True, authorized=True),
        client_factory=forbidden_client,
    )

    assert exit_code == 2
    assert called is False


def test_probe_writes_pre_request_report_before_constructing_client(tmp_path):
    environment_file, manifest, output = write_cli_inputs(tmp_path)
    client = FakeStreamClient(successful_chunks())

    def factory(**_kwargs):
        pre_request = json.loads(output.read_text(encoding="utf-8"))
        assert pre_request["status"] == "failed"
        assert pre_request["network_attempted"] is False
        assert "probe_not_started" in pre_request["blockers"]
        return client

    exit_code = main(
        cli_args(environment_file, manifest, output, execute=True, authorized=True),
        client_factory=factory,
    )

    assert exit_code == 0
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "passed"
