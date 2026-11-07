"""Shared zero-network safety fixtures for backend tests."""

import pytest

import backend.app as app_module


@pytest.fixture(autouse=True)
def isolate_developer_model_environment(tmp_path, monkeypatch):
    """Never let deterministic tests inspect a developer's real `.env.local`."""
    monkeypatch.setattr(
        app_module,
        "MODEL_ENVIRONMENT_FILE",
        tmp_path / "test-model-environment.env",
    )
