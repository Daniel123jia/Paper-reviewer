from __future__ import annotations

from decimal import Decimal

import pytest

from backend.p4_budget import (
    P4_BUDGET_ENV_KEYS,
    P4BudgetConfigurationError,
    require_p4_budget_configuration,
)


def valid_budget_environment(**overrides):
    values = {
        "P4_MAX_PROVIDER_REQUESTS": "8",
        "P4_MAX_INPUT_TOKENS": "12000",
        "P4_MAX_OUTPUT_TOKENS": "4000",
        "P4_MAX_OUTPUT_TOKENS_PER_REQUEST": "500",
        "P4_MAX_DURATION_SECONDS": "180",
        "P4_MAX_PLANNED_COST_USD": "0.25",
        "P4_INPUT_USD_PER_MILLION": "0.80",
        "P4_OUTPUT_USD_PER_MILLION": "4.00",
        "P4_PRICING_STATUS": "estimated_unverified",
    }
    values.update(overrides)
    return values


def write_environment(path, values):
    path.write_text("".join(f"{key}={value}\n" for key, value in values.items()),
                    encoding="utf-8")


def test_budget_configuration_accepts_explicit_process_values(tmp_path):
    configuration = require_p4_budget_configuration(
        tmp_path / "missing.env", valid_budget_environment()
    )

    assert configuration.max_provider_requests == 8
    assert configuration.max_input_tokens == 12000
    assert configuration.max_output_tokens == 4000
    assert configuration.max_output_tokens_per_request == 500
    assert configuration.max_duration_seconds == Decimal("180")
    assert configuration.max_planned_cost_usd == Decimal("0.25")
    assert configuration.input_usd_per_million == Decimal("0.80")
    assert configuration.output_usd_per_million == Decimal("4.00")
    assert configuration.pricing_status == "estimated_unverified"


@pytest.mark.parametrize("missing", P4_BUDGET_ENV_KEYS)
def test_every_budget_value_is_required(tmp_path, missing):
    environment = valid_budget_environment()
    del environment[missing]

    with pytest.raises(P4BudgetConfigurationError) as caught:
        require_p4_budget_configuration(tmp_path / "missing.env", environment)

    detail = caught.value.safe_detail()
    assert detail["code"] == "p4_budget_configuration_invalid"
    assert missing in detail["variables"]
    assert repr(environment) not in repr(detail)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("P4_MAX_PROVIDER_REQUESTS", "0"),
        ("P4_MAX_PROVIDER_REQUESTS", "1.5"),
        ("P4_MAX_INPUT_TOKENS", "-1"),
        ("P4_MAX_OUTPUT_TOKENS", "nan"),
        ("P4_MAX_OUTPUT_TOKENS_PER_REQUEST", "0"),
        ("P4_MAX_DURATION_SECONDS", "Infinity"),
        ("P4_MAX_DURATION_SECONDS", "-2"),
        ("P4_MAX_PLANNED_COST_USD", "0"),
        ("P4_INPUT_USD_PER_MILLION", "0"),
        ("P4_OUTPUT_USD_PER_MILLION", "0"),
        ("P4_PRICING_STATUS", "unchecked"),
    ],
)
def test_budget_configuration_rejects_invalid_values(tmp_path, name, value):
    environment = valid_budget_environment(**{name: value})

    with pytest.raises(P4BudgetConfigurationError) as caught:
        require_p4_budget_configuration(tmp_path / "missing.env", environment)

    assert name in caught.value.safe_detail()["variables"]
    assert value not in repr(caught.value.safe_detail())


def test_per_request_output_cannot_exceed_total_output(tmp_path):
    with pytest.raises(P4BudgetConfigurationError) as caught:
        require_p4_budget_configuration(
            tmp_path / "missing.env",
            valid_budget_environment(
                P4_MAX_OUTPUT_TOKENS="499",
                P4_MAX_OUTPUT_TOKENS_PER_REQUEST="500",
            ),
        )

    assert caught.value.safe_detail()["variables"] == [
        "P4_MAX_OUTPUT_TOKENS", "P4_MAX_OUTPUT_TOKENS_PER_REQUEST"
    ]


def test_budget_configuration_rejects_different_process_and_file_values(tmp_path):
    environment_file = tmp_path / ".env.local"
    write_environment(environment_file, valid_budget_environment())
    process = valid_budget_environment(P4_MAX_PROVIDER_REQUESTS="9")

    with pytest.raises(P4BudgetConfigurationError) as caught:
        require_p4_budget_configuration(environment_file, process)

    assert caught.value.safe_detail() == {
        "code": "p4_budget_configuration_invalid",
        "blockers": ["p4_budget_configuration_source_conflict"],
        "variables": ["P4_MAX_PROVIDER_REQUESTS"],
    }


def test_identical_process_and_file_values_are_allowed(tmp_path):
    environment_file = tmp_path / ".env.local"
    environment = valid_budget_environment()
    write_environment(environment_file, environment)

    configuration = require_p4_budget_configuration(environment_file, environment)

    assert configuration.max_provider_requests == 8


def test_unreadable_budget_environment_fails_closed(tmp_path):
    environment_file = tmp_path / ".env.local"
    environment_file.write_bytes(b"P4_MAX_PROVIDER_REQUESTS=secret-\xff\xfe")

    with pytest.raises(P4BudgetConfigurationError) as caught:
        require_p4_budget_configuration(environment_file, {})

    assert caught.value.safe_detail() == {
        "code": "p4_budget_configuration_invalid",
        "blockers": ["p4_budget_environment_file_unreadable"],
        "variables": list(P4_BUDGET_ENV_KEYS),
    }
    assert "secret" not in str(caught.value)
