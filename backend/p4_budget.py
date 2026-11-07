"""Fail-closed configuration and accounting primitives for one P4 generation."""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable, Literal, Mapping

from dotenv import dotenv_values


P4_BUDGET_ENV_KEYS = (
    "P4_MAX_PROVIDER_REQUESTS",
    "P4_MAX_INPUT_TOKENS",
    "P4_MAX_OUTPUT_TOKENS",
    "P4_MAX_OUTPUT_TOKENS_PER_REQUEST",
    "P4_MAX_DURATION_SECONDS",
    "P4_MAX_PLANNED_COST_USD",
    "P4_INPUT_USD_PER_MILLION",
    "P4_OUTPUT_USD_PER_MILLION",
    "P4_PRICING_STATUS",
)
_INTEGER_KEYS = P4_BUDGET_ENV_KEYS[:4]
_DECIMAL_KEYS = P4_BUDGET_ENV_KEYS[4:8]
_PRICING_STATUSES = {"estimated_unverified", "provider_verified"}


@dataclass(frozen=True)
class P4BudgetConfiguration:
    max_provider_requests: int
    max_input_tokens: int
    max_output_tokens: int
    max_output_tokens_per_request: int
    max_duration_seconds: Decimal
    max_planned_cost_usd: Decimal
    input_usd_per_million: Decimal
    output_usd_per_million: Decimal
    pricing_status: Literal["estimated_unverified", "provider_verified"]


class P4BudgetConfigurationError(ValueError):
    def __init__(self, blockers: Iterable[str], variables: Iterable[str] = ()):
        self.code = "p4_budget_configuration_invalid"
        self.blockers = tuple(dict.fromkeys(blockers))
        self.variables = tuple(dict.fromkeys(variables))
        super().__init__(self.code)

    def safe_detail(self) -> dict:
        return {"code": self.code, "blockers": list(self.blockers),
                "variables": list(self.variables)}


def _present(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _positive_integer(value: str) -> int:
    if not value.isascii() or not value.isdigit():
        raise ValueError
    parsed = int(value)
    if parsed <= 0:
        raise ValueError
    return parsed


def _positive_decimal(value: str) -> Decimal:
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        raise ValueError from None
    if not parsed.is_finite() or parsed <= 0:
        raise ValueError
    return parsed


def resolve_p4_budget_configuration(
    process_environment: Mapping[str, str | None],
    environment_file_values: Mapping[str, str | None],
) -> P4BudgetConfiguration:
    conflicts = [name for name in P4_BUDGET_ENV_KEYS
                 if _present(process_environment.get(name))
                 and _present(environment_file_values.get(name))
                 and process_environment.get(name) != environment_file_values.get(name)]
    if conflicts:
        raise P4BudgetConfigurationError(
            ["p4_budget_configuration_source_conflict"], conflicts
        )

    values = {name: (process_environment.get(name)
                     if _present(process_environment.get(name))
                     else environment_file_values.get(name))
              for name in P4_BUDGET_ENV_KEYS}
    missing = [name for name, value in values.items() if not _present(value)]
    if missing:
        raise P4BudgetConfigurationError(["p4_budget_value_missing"], missing)

    parsed_integers = {}
    parsed_decimals = {}
    invalid = []
    for name in _INTEGER_KEYS:
        try:
            parsed_integers[name] = _positive_integer(values[name])
        except ValueError:
            invalid.append(name)
    for name in _DECIMAL_KEYS:
        try:
            parsed_decimals[name] = _positive_decimal(values[name])
        except ValueError:
            invalid.append(name)
    if values["P4_PRICING_STATUS"] not in _PRICING_STATUSES:
        invalid.append("P4_PRICING_STATUS")
    if invalid:
        raise P4BudgetConfigurationError(["p4_budget_value_invalid"], invalid)
    if (parsed_integers["P4_MAX_OUTPUT_TOKENS_PER_REQUEST"] >
            parsed_integers["P4_MAX_OUTPUT_TOKENS"]):
        raise P4BudgetConfigurationError(
            ["p4_budget_output_limit_invalid"],
            ["P4_MAX_OUTPUT_TOKENS", "P4_MAX_OUTPUT_TOKENS_PER_REQUEST"],
        )

    return P4BudgetConfiguration(
        max_provider_requests=parsed_integers["P4_MAX_PROVIDER_REQUESTS"],
        max_input_tokens=parsed_integers["P4_MAX_INPUT_TOKENS"],
        max_output_tokens=parsed_integers["P4_MAX_OUTPUT_TOKENS"],
        max_output_tokens_per_request=parsed_integers["P4_MAX_OUTPUT_TOKENS_PER_REQUEST"],
        max_duration_seconds=parsed_decimals["P4_MAX_DURATION_SECONDS"],
        max_planned_cost_usd=parsed_decimals["P4_MAX_PLANNED_COST_USD"],
        input_usd_per_million=parsed_decimals["P4_INPUT_USD_PER_MILLION"],
        output_usd_per_million=parsed_decimals["P4_OUTPUT_USD_PER_MILLION"],
        pricing_status=values["P4_PRICING_STATUS"],
    )


def require_p4_budget_configuration(
    environment_file: Path,
    process_environment: Mapping[str, str | None] | None = None,
) -> P4BudgetConfiguration:
    try:
        file_values = ({name: value for name, value in dotenv_values(environment_file).items()
                        if name in P4_BUDGET_ENV_KEYS}
                       if environment_file.is_file() else {})
    except Exception:
        raise P4BudgetConfigurationError(
            ["p4_budget_environment_file_unreadable"], P4_BUDGET_ENV_KEYS
        ) from None
    return resolve_p4_budget_configuration(
        os.environ if process_environment is None else process_environment,
        file_values,
    )
