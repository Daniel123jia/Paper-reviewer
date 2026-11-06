from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from backend.model_runtime import (
    REQUIRED_MODEL_ENV_KEYS,
    ModelConfigurationError,
    create_model_session,
    resolve_model_runtime_config,
)


def valid_environment(**overrides):
    values = {
        "OPENAI_API_KEY": "test-only-key",
        "OPENAI_BASE_URL": "https://provider.example/v1",
        "OPENAI_MODEL": "gpt-6.1-sol",
    }
    values.update(overrides)
    return values


@pytest.mark.parametrize("source", ["process", "file"])
def test_resolver_accepts_one_explicit_source(source):
    process = valid_environment() if source == "process" else {}
    file_values = valid_environment() if source == "file" else {}

    config = resolve_model_runtime_config(process, file_values)

    assert config.api_key == "test-only-key"
    assert config.base_url == "https://provider.example/v1"
    assert config.model == "gpt-6.1-sol"
    assert "test-only-key" not in repr(config)
    with pytest.raises(FrozenInstanceError):
        config.model = "other-model"


def test_resolver_accepts_identical_explicit_sources():
    config = resolve_model_runtime_config(valid_environment(), valid_environment())

    assert config.model == "gpt-6.1-sol"


@pytest.mark.parametrize("name", REQUIRED_MODEL_ENV_KEYS)
def test_resolver_fails_closed_for_different_nonempty_sources(name):
    process = valid_environment()
    file_values = valid_environment(**{name: "different-nonempty-value"})

    with pytest.raises(ModelConfigurationError) as caught:
        resolve_model_runtime_config(process, file_values)

    detail = caught.value.safe_detail()
    assert detail == {
        "code": "model_configuration_invalid",
        "blockers": ["model_configuration_source_conflict"],
        "variables": [name],
    }
    encoded = repr(detail)
    assert "test-only-key" not in encoded
    assert "different-nonempty-value" not in encoded
    assert "provider.example" not in encoded


@pytest.mark.parametrize(
    ("overrides", "blocker", "variable"),
    [
        ({"OPENAI_API_KEY": ""}, "model_credential_not_configured", "OPENAI_API_KEY"),
        ({"OPENAI_API_KEY": "replace-with-your-api-key"},
         "model_credential_is_placeholder", "OPENAI_API_KEY"),
        ({"OPENAI_API_KEY": " key-with-space"},
         "model_credential_contains_whitespace", "OPENAI_API_KEY"),
        ({"OPENAI_MODEL": ""}, "requested_model_not_explicitly_configured", "OPENAI_MODEL"),
        ({"OPENAI_MODEL": "replace-with-your-model-name"},
         "requested_model_is_placeholder", "OPENAI_MODEL"),
        ({"OPENAI_MODEL": "gpt model"},
         "requested_model_contains_whitespace", "OPENAI_MODEL"),
        ({"OPENAI_BASE_URL": ""},
         "model_endpoint_not_explicitly_configured", "OPENAI_BASE_URL"),
        ({"OPENAI_BASE_URL": "https://api.example.com/v1"},
         "model_endpoint_is_placeholder", "OPENAI_BASE_URL"),
        ({"OPENAI_BASE_URL": "http://provider.example/v1"},
         "model_endpoint_must_use_https", "OPENAI_BASE_URL"),
        ({"OPENAI_BASE_URL": " https://provider.example/v1"},
         "model_endpoint_contains_whitespace", "OPENAI_BASE_URL"),
        ({"OPENAI_BASE_URL": "https://user:password@provider.example/v1"},
         "model_endpoint_contains_credentials", "OPENAI_BASE_URL"),
        ({"OPENAI_BASE_URL": "https://provider.example/v1?key=value"},
         "model_endpoint_contains_query", "OPENAI_BASE_URL"),
        ({"OPENAI_BASE_URL": "https://provider.example/v1#fragment"},
         "model_endpoint_contains_fragment", "OPENAI_BASE_URL"),
        ({"OPENAI_BASE_URL": "https://provider.example:invalid/v1"},
         "model_endpoint_invalid_port", "OPENAI_BASE_URL"),
        ({"OPENAI_BASE_URL": "https://provider.example:70000/v1"},
         "model_endpoint_invalid_port", "OPENAI_BASE_URL"),
        ({"OPENAI_BASE_URL": "https:///v1"},
         "model_endpoint_invalid_host", "OPENAI_BASE_URL"),
        ({"OPENAI_BASE_URL": "https://bad_host.example/v1"},
         "model_endpoint_invalid_host", "OPENAI_BASE_URL"),
        ({"OPENAI_BASE_URL": "https://-provider.example/v1"},
         "model_endpoint_invalid_host", "OPENAI_BASE_URL"),
        ({"OPENAI_BASE_URL": "not-a-url"},
         "model_endpoint_must_use_https", "OPENAI_BASE_URL"),
    ],
)
def test_resolver_rejects_invalid_required_values(overrides, blocker, variable):
    environment = valid_environment(**overrides)

    with pytest.raises(ModelConfigurationError) as caught:
        resolve_model_runtime_config(environment, {})

    detail = caught.value.safe_detail()
    assert blocker in detail["blockers"]
    assert variable in detail["variables"]
    if environment[variable]:
        assert environment[variable] not in repr(detail)


def test_resolver_ignores_blank_secondary_source_without_hiding_explicit_value():
    config = resolve_model_runtime_config(
        valid_environment(),
        {key: "" for key in REQUIRED_MODEL_ENV_KEYS},
    )

    assert config.model == "gpt-6.1-sol"


def test_session_factory_uses_validated_key_endpoint_and_runtime_options():
    calls = []
    fake_client = object()

    def factory(**kwargs):
        calls.append(kwargs)
        return fake_client

    config = resolve_model_runtime_config(valid_environment(), {})
    session = create_model_session(
        config,
        timeout=91,
        max_retries=0,
        client_factory=factory,
    )

    assert calls == [{
        "api_key": "test-only-key",
        "base_url": "https://provider.example/v1",
        "timeout": 91,
        "max_retries": 0,
    }]
    assert session.client is fake_client
    assert session.configuration is config
    assert session.configuration.model == "gpt-6.1-sol"
    assert "test-only-key" not in repr(session)
