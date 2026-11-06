import os

import pytest

from backend.app import load_local_environment
from backend.model_runtime import ModelConfigurationError, require_model_runtime_config


MODEL_KEYS = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL")


def clear_model_environment(monkeypatch):
    for key in MODEL_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_local_environment_does_not_copy_model_values_into_process(tmp_path, monkeypatch):
    clear_model_environment(monkeypatch)
    monkeypatch.delenv("PAPER_REVIEW_PREDEPLOY", raising=False)
    config = tmp_path / ".env.local"
    config.write_text(
        "OPENAI_API_KEY=file-key\n"
        "OPENAI_BASE_URL=https://file-provider.example/v1\n"
        "OPENAI_MODEL=file-model\n"
        "PAPER_REVIEW_PREDEPLOY=1\n",
        encoding="utf-8",
    )

    load_local_environment(config)

    assert all(key not in os.environ for key in MODEL_KEYS)
    assert os.environ["PAPER_REVIEW_PREDEPLOY"] == "1"
    resolved = require_model_runtime_config(config)
    assert resolved.api_key == "file-key"
    assert resolved.base_url == "https://file-provider.example/v1"
    assert resolved.model == "file-model"


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
