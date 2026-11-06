"""Explicit fake model configuration for zero-network API tests."""


def configure_test_model(monkeypatch, *, key="test-only-key", model="test-model"):
    monkeypatch.setenv("OPENAI_API_KEY", key)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://provider.example/v1")
    monkeypatch.setenv("OPENAI_MODEL", model)
