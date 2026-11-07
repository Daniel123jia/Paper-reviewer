"""Explicit fake model configuration for zero-network API tests."""


def configure_test_model(monkeypatch, *, key="test-only-key", model="test-model"):
    monkeypatch.setenv("OPENAI_API_KEY", key)
    monkeypatch.setenv("OPENAI_BASE_URL", "https://provider.example/v1")
    monkeypatch.setenv("OPENAI_MODEL", model)
    budget = {
        "P4_MAX_PROVIDER_REQUESTS": "20",
        "P4_MAX_INPUT_TOKENS": "200000",
        "P4_MAX_OUTPUT_TOKENS": "20000",
        "P4_MAX_OUTPUT_TOKENS_PER_REQUEST": "1000",
        "P4_MAX_DURATION_SECONDS": "180",
        "P4_MAX_PLANNED_COST_USD": "10",
        "P4_INPUT_USD_PER_MILLION": "0.80",
        "P4_OUTPUT_USD_PER_MILLION": "4.00",
        "P4_PRICING_STATUS": "estimated_unverified",
    }
    for name, value in budget.items():
        monkeypatch.setenv(name, value)
