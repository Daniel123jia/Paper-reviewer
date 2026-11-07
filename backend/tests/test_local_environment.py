import os

import pytest

from backend.app import load_local_environment
from backend.model_runtime import ModelConfigurationError, require_model_runtime_config
from backend.p4_budget import P4_BUDGET_ENV_KEYS, P4_WORKER_ENV_KEYS


MODEL_KEYS = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL")


def clear_model_environment(monkeypatch):
    for key in MODEL_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_local_environment_does_not_copy_model_values_into_process(tmp_path, monkeypatch):
    clear_model_environment(monkeypatch)
    monkeypatch.delenv("P5R4_TEST_NON_MODEL_SETTING", raising=False)
    config = tmp_path / ".env.local"
    config.write_text(
        "OPENAI_API_KEY=file-key\n"
        "OPENAI_BASE_URL=https://file-provider.example/v1\n"
        "OPENAI_MODEL=file-model\n"
        "P5R4_TEST_NON_MODEL_SETTING=file-value\n",
        encoding="utf-8",
    )

    load_local_environment(config)

    assert all(key not in os.environ for key in MODEL_KEYS)
    assert os.environ["P5R4_TEST_NON_MODEL_SETTING"] == "file-value"
    resolved = require_model_runtime_config(config)
    assert resolved.api_key == "file-key"
    assert resolved.base_url == "https://file-provider.example/v1"
    assert resolved.model == "file-model"
    os.environ.pop("P5R4_TEST_NON_MODEL_SETTING", None)


def test_local_environment_never_overwrites_process_model_values(tmp_path, monkeypatch):
    process = {
        "OPENAI_API_KEY": "process-key",
        "OPENAI_BASE_URL": "https://process-provider.example/v1",
        "OPENAI_MODEL": "process-model",
    }
    for key, value in process.items():
        monkeypatch.setenv(key, value)
    config = tmp_path / ".env.local"
    config.write_text(
        "OPENAI_API_KEY=file-key\n"
        "OPENAI_BASE_URL=https://file-provider.example/v1\n"
        "OPENAI_MODEL=file-model\n",
        encoding="utf-8",
    )

    load_local_environment(config)

    assert {key: os.environ[key] for key in MODEL_KEYS} == process
    with pytest.raises(ModelConfigurationError) as caught:
        require_model_runtime_config(config)
    assert caught.value.conflicting_variables == MODEL_KEYS


def test_repeated_loads_and_file_updates_do_not_pollute_process_source(tmp_path, monkeypatch):
    clear_model_environment(monkeypatch)
    first = tmp_path / "first.env"
    second = tmp_path / "second.env"
    first.write_text(
        "OPENAI_API_KEY=first-key\n"
        "OPENAI_BASE_URL=https://first-provider.example/v1\n"
        "OPENAI_MODEL=first-model\n",
        encoding="utf-8",
    )
    second.write_text(
        "OPENAI_API_KEY=second-key\n"
        "OPENAI_BASE_URL=https://second-provider.example/v1\n"
        "OPENAI_MODEL=second-model\n",
        encoding="utf-8",
    )

    load_local_environment(first)
    load_local_environment(second)
    first_result = require_model_runtime_config(first)
    second_result = require_model_runtime_config(second)
    first.write_text(
        "OPENAI_API_KEY=updated-key\n"
        "OPENAI_BASE_URL=https://updated-provider.example/v1\n"
        "OPENAI_MODEL=updated-model\n",
        encoding="utf-8",
    )
    updated_result = require_model_runtime_config(first)

    assert all(key not in os.environ for key in MODEL_KEYS)
    assert first_result.model == "first-model"
    assert second_result.model == "second-model"
    assert updated_result.model == "updated-model"


def test_local_environment_load_ignores_unreadable_file_until_safe_model_preflight(
        tmp_path, monkeypatch):
    clear_model_environment(monkeypatch)
    config = tmp_path / ".env.local"
    config.write_bytes(b"OPENAI_API_KEY=secret-\xff\xfe\n")

    load_local_environment(config)

    assert all(key not in os.environ for key in MODEL_KEYS)
    with pytest.raises(ModelConfigurationError) as caught:
        require_model_runtime_config(config)
    assert caught.value.safe_detail() == {
        "code": "model_configuration_invalid",
        "blockers": ["model_environment_file_unreadable"],
        "variables": list(MODEL_KEYS),
    }
    assert "secret" not in str(caught.value)


def test_local_environment_does_not_copy_p4_budget_values(tmp_path, monkeypatch):
    for key in P4_BUDGET_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    config = tmp_path / ".env.local"
    config.write_text("\n".join(f"{key}=private-file-value"
                                 for key in P4_BUDGET_ENV_KEYS), encoding="utf-8")

    load_local_environment(config)

    assert all(key not in os.environ for key in P4_BUDGET_ENV_KEYS)


def test_local_environment_does_not_copy_worker_values(tmp_path, monkeypatch):
    for key in P4_WORKER_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    config = tmp_path / ".env.local"
    config.write_text(
        "PAPER_REVIEW_WORKER_COUNT=1\nWEB_CONCURRENCY=1\n", encoding="utf-8"
    )

    load_local_environment(config)

    assert all(key not in os.environ for key in P4_WORKER_ENV_KEYS)
