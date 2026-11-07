from __future__ import annotations

from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from backend.model_runtime import (
    REQUIRED_MODEL_ENV_KEYS,
    ModelConfigurationError,
    create_model_session,
    create_validated_chat_completion,
    require_model_runtime_config,
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


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://[::1/v1",
        "https://[gggg::1]/v1",
        "https://exam\uff0fple.com/v1",
        "https://provider.example:invalid/v1",
        "https://[::1]extra/v1",
        "https://%ZZ/v1",
    ],
)
def test_url_parser_failures_are_safe_configuration_errors(endpoint):
    with pytest.raises(ModelConfigurationError) as caught:
        resolve_model_runtime_config(
            valid_environment(OPENAI_BASE_URL=endpoint), {}
        )

    detail = caught.value.safe_detail()
    assert detail["code"] == "model_configuration_invalid"
    assert detail["blockers"] in (["model_endpoint_invalid_url"],
                                  ["model_endpoint_invalid_port"],
                                  ["model_endpoint_invalid_host"])
    assert detail["variables"] == ["OPENAI_BASE_URL"]
    assert endpoint not in repr(detail)
    assert endpoint not in str(caught.value)


def test_unreadable_environment_file_fails_closed_without_exposing_contents(tmp_path):
    environment_file = tmp_path / ".env.local"
    environment_file.write_bytes(b"OPENAI_API_KEY=secret-before-invalid-byte\n\xff")

    with pytest.raises(ModelConfigurationError) as caught:
        require_model_runtime_config(environment_file, {})

    assert caught.value.safe_detail() == {
        "code": "model_configuration_invalid",
        "blockers": ["model_environment_file_unreadable"],
        "variables": list(REQUIRED_MODEL_ENV_KEYS),
    }
    assert "secret-before-invalid-byte" not in str(caught.value)
    assert "secret-before-invalid-byte" not in repr(caught.value.safe_detail())


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


class RecordingChatClient:
    def __init__(self):
        self.calls = []
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create)
        )

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(model=kwargs["model"])


def write_model_environment(path, **overrides):
    values = valid_environment(**overrides)
    path.write_text(
        "".join(f"{name}={values[name]}\n" for name in REQUIRED_MODEL_ENV_KEYS),
        encoding="utf-8",
    )


def configured_recording_session(environment_file):
    client = RecordingChatClient()
    configuration = require_model_runtime_config(environment_file, {})
    session = create_model_session(
        configuration,
        timeout=10,
        max_retries=0,
        client_factory=lambda **_: client,
    )
    return session, client


def test_two_requests_revalidate_and_use_the_original_session_model(tmp_path):
    environment_file = tmp_path / ".env.local"
    write_model_environment(environment_file)
    session, client = configured_recording_session(environment_file)

    for _ in range(2):
        create_validated_chat_completion(
            session,
            environment_file,
            process_environment={},
            model="gpt-6.1-sol",
            messages=[],
        )

    assert [call["model"] for call in client.calls] == [
        "gpt-6.1-sol", "gpt-6.1-sol"
    ]


@pytest.mark.parametrize(
    ("mutation", "expected_blocker", "expected_variable"),
    [
        ("delete_key", "model_credential_not_configured", "OPENAI_API_KEY"),
        ("change_endpoint", "model_configuration_changed", "OPENAI_BASE_URL"),
        ("change_model", "model_configuration_changed", "OPENAI_MODEL"),
        ("source_conflict", "model_configuration_source_conflict", "OPENAI_BASE_URL"),
    ],
)
def test_second_request_stops_before_sdk_when_configuration_changes(
        tmp_path, mutation, expected_blocker, expected_variable):
    environment_file = tmp_path / ".env.local"
    write_model_environment(environment_file)
    session, client = configured_recording_session(environment_file)
    create_validated_chat_completion(
        session, environment_file, process_environment={},
        model="gpt-6.1-sol", messages=[],
    )

    process_environment = {}
    if mutation == "delete_key":
        environment_file.write_text(
            "OPENAI_BASE_URL=https://provider.example/v1\n"
            "OPENAI_MODEL=gpt-6.1-sol\n",
            encoding="utf-8",
        )
    elif mutation == "change_endpoint":
        write_model_environment(
            environment_file,
            OPENAI_BASE_URL="https://other-provider.example/v1",
        )
    elif mutation == "change_model":
        write_model_environment(environment_file, OPENAI_MODEL="other-model")
    else:
        process_environment = {
            "OPENAI_BASE_URL": "https://conflicting-provider.example/v1"
        }

    with pytest.raises(ModelConfigurationError) as caught:
        create_validated_chat_completion(
            session,
            environment_file,
            process_environment=process_environment,
            model="gpt-6.1-sol",
            messages=[],
        )

    detail = caught.value.safe_detail()
    assert expected_blocker in detail["blockers"]
    assert expected_variable in detail["variables"]
    assert len(client.calls) == 1
    assert "other-provider" not in repr(detail)
    assert "conflicting-provider" not in repr(detail)
    assert "other-model" not in repr(detail)


def test_request_model_must_match_the_validated_session_model(tmp_path):
    environment_file = tmp_path / ".env.local"
    write_model_environment(environment_file)
    session, client = configured_recording_session(environment_file)

    with pytest.raises(ModelConfigurationError) as caught:
        create_validated_chat_completion(
            session,
            environment_file,
            process_environment={},
            model="unvalidated-model",
            messages=[],
        )

    assert caught.value.safe_detail() == {
        "code": "model_configuration_invalid",
        "blockers": ["model_request_model_mismatch"],
        "variables": ["OPENAI_MODEL"],
    }
    assert client.calls == []
