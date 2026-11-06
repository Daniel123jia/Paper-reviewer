from types import SimpleNamespace

from backend.app import calculate_cost


def test_cost_uses_configured_per_million_rates(monkeypatch):
    monkeypatch.setenv("OPENAI_INPUT_USD_PER_MILLION", "0.5")
    monkeypatch.setenv("OPENAI_OUTPUT_USD_PER_MILLION", "2")
    usage = SimpleNamespace(input_tokens=1000, output_tokens=500)
    assert calculate_cost(usage) == 0.0015


def test_cost_is_unknown_without_rates(monkeypatch):
    monkeypatch.delenv("OPENAI_INPUT_USD_PER_MILLION", raising=False)
    monkeypatch.delenv("OPENAI_OUTPUT_USD_PER_MILLION", raising=False)
    assert calculate_cost(SimpleNamespace(input_tokens=100, output_tokens=100)) is None


def test_chat_completion_usage_uses_prompt_and_completion_tokens(monkeypatch):
    monkeypatch.setenv("OPENAI_INPUT_USD_PER_MILLION", "1")
    monkeypatch.setenv("OPENAI_OUTPUT_USD_PER_MILLION", "2")
    usage = SimpleNamespace(prompt_tokens=1000, completion_tokens=500)
    assert calculate_cost(usage) == 0.002
