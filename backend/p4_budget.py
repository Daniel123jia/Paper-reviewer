"""Fail-closed configuration and accounting primitives for one P4 generation."""

from __future__ import annotations

import os
import json
import sqlite3
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Callable, Iterable, Literal, Mapping

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


class P4BudgetStateError(RuntimeError):
    """A stable, redacted failure to persist or transition budget state."""

    def __init__(self, blocker: str):
        self.code = "p4_budget_state_invalid"
        self.blocker = blocker
        super().__init__(self.code)

    def safe_detail(self) -> dict:
        return {"code": self.code, "blockers": [self.blocker]}


@dataclass(frozen=True)
class P4RequestReservation:
    model_call_id: str
    request_index: int
    stage: str
    prompt_version: str
    estimated_input_tokens: int
    reserved_output_tokens: int
    max_tokens: int
    remaining_duration_ms: int
    planned_cost_usd: Decimal


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


def _configuration_json(configuration: P4BudgetConfiguration) -> str:
    values = asdict(configuration)
    for name, value in values.items():
        if isinstance(value, Decimal):
            values[name] = str(value)
    return json.dumps(values, sort_keys=True, separators=(",", ":"))


class P4BudgetStore:
    """Durable P4 budget state; no provider call may precede its reservation."""

    def __init__(self, connect: Callable, now: Callable[[], str]):
        self._connect = connect
        self._now = now

    def create_run(
        self,
        generation_run_id: str,
        draft_id: str | None,
        project_id: str,
        kind: str,
        language: str,
        configuration: P4BudgetConfiguration,
    ) -> None:
        timestamp = self._now()
        try:
            with self._connect() as db:
                db.execute(
                    """INSERT INTO p4_generation_runs
                       (id, draft_id, project_id, kind, language, budget_json,
                        state, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, 'ready', ?, ?)""",
                    (generation_run_id, draft_id, project_id, kind, language,
                     _configuration_json(configuration), timestamp, timestamp),
                )
        except sqlite3.Error:
            raise P4BudgetStateError("p4_generation_run_persist_failed") from None

    def reserve_request(
        self,
        generation_run_id: str,
        reservation: P4RequestReservation,
        requested_model: str,
    ) -> None:
        timestamp = self._now()
        try:
            with self._connect() as db:
                run = db.execute(
                    "SELECT draft_id, project_id, budget_json FROM p4_generation_runs "
                    "WHERE id = ?", (generation_run_id,),
                ).fetchone()
                if run is None:
                    raise P4BudgetStateError("p4_generation_run_missing")
                pricing_status = json.loads(run["budget_json"])["pricing_status"]
                db.execute(
                    """INSERT INTO p4_model_calls
                       (id, draft_id, project_id, stage, prompt_version,
                        requested_model, actual_model, input_tokens, output_tokens,
                        duration_ms, status, error, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, NULL, NULL, NULL, 0,
                               'reserved', NULL, ?)""",
                    (reservation.model_call_id, run["draft_id"], run["project_id"],
                     reservation.stage, reservation.prompt_version, requested_model,
                     timestamp),
                )
                db.execute(
                    """INSERT INTO p4_model_call_budget
                       (model_call_id, generation_run_id, request_index,
                        estimated_input_tokens, reserved_output_tokens, max_tokens,
                        remaining_duration_ms, planned_cost_usd, pricing_status,
                        dispatch_state, usage_status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?,
                               'reserved_not_dispatched', 'not_available', ?, ?)""",
                    (reservation.model_call_id, generation_run_id,
                     reservation.request_index, reservation.estimated_input_tokens,
                     reservation.reserved_output_tokens, reservation.max_tokens,
                     reservation.remaining_duration_ms,
                     str(reservation.planned_cost_usd), pricing_status,
                     timestamp, timestamp),
                )
                current = db.execute(
                    "SELECT planned_cost_usd FROM p4_generation_runs WHERE id = ?",
                    (generation_run_id,),
                ).fetchone()
                planned = Decimal(current["planned_cost_usd"]) + reservation.planned_cost_usd
                changed = db.execute(
                    """UPDATE p4_generation_runs
                       SET state = 'request_reserved', request_count = request_count + 1,
                           planned_cost_usd = ?, updated_at = ?
                       WHERE id = ?""",
                    (str(planned), timestamp, generation_run_id),
                ).rowcount
                if changed != 1:
                    raise P4BudgetStateError("p4_generation_run_transition_failed")
        except P4BudgetStateError:
            raise
        except sqlite3.Error:
            raise P4BudgetStateError("p4_request_reservation_persist_failed") from None

    def mark_dispatch_pending(self, generation_run_id: str, model_call_id: str) -> None:
        timestamp = self._now()
        try:
            with self._connect() as db:
                changed = db.execute(
                    """UPDATE p4_model_call_budget
                       SET dispatch_state = 'dispatch_pending', updated_at = ?
                       WHERE model_call_id = ? AND generation_run_id = ?
                         AND dispatch_state = 'reserved_not_dispatched'""",
                    (timestamp, model_call_id, generation_run_id),
                ).rowcount
                if changed != 1:
                    raise P4BudgetStateError("p4_dispatch_transition_failed")
                changed = db.execute(
                    """UPDATE p4_generation_runs
                       SET state = 'dispatch_pending',
                           potentially_billable_requests = potentially_billable_requests + 1,
                           updated_at = ? WHERE id = ?""",
                    (timestamp, generation_run_id),
                ).rowcount
                if changed != 1:
                    raise P4BudgetStateError("p4_generation_run_transition_failed")
        except P4BudgetStateError:
            raise
        except sqlite3.Error:
            raise P4BudgetStateError("p4_dispatch_state_persist_failed") from None

    def settle_valid_usage(
        self,
        generation_run_id: str,
        model_call_id: str,
        *,
        actual_model: str,
        input_tokens: int,
        output_tokens: int,
        total_tokens: int,
        duration_ms: int,
        usage_cost_usd: Decimal,
    ) -> None:
        if total_tokens != input_tokens + output_tokens:
            raise P4BudgetStateError("p4_usage_totals_invalid")
        timestamp = self._now()
        try:
            with self._connect() as db:
                changed = db.execute(
                    """UPDATE p4_model_call_budget
                       SET dispatch_state = 'completed_usage_valid',
                           usage_status = 'valid', actual_total_tokens = ?,
                           usage_cost_usd = ?, updated_at = ?
                       WHERE model_call_id = ? AND generation_run_id = ?
                         AND dispatch_state = 'dispatch_pending'""",
                    (total_tokens, str(usage_cost_usd), timestamp,
                     model_call_id, generation_run_id),
                ).rowcount
                if changed != 1:
                    raise P4BudgetStateError("p4_usage_transition_failed")
                changed = db.execute(
                    """UPDATE p4_model_calls SET actual_model = ?, input_tokens = ?,
                           output_tokens = ?, duration_ms = ?, status = 'completed'
                       WHERE id = ? AND status = 'reserved'""",
                    (actual_model, input_tokens, output_tokens, duration_ms,
                     model_call_id),
                ).rowcount
                if changed != 1:
                    raise P4BudgetStateError("p4_legacy_call_transition_failed")
                current = db.execute(
                    "SELECT usage_cost_usd FROM p4_generation_runs WHERE id = ?",
                    (generation_run_id,),
                ).fetchone()
                prior_cost = Decimal(current["usage_cost_usd"] or "0")
                changed = db.execute(
                    """UPDATE p4_generation_runs
                       SET state = 'usage_valid', input_tokens = input_tokens + ?,
                           output_tokens = output_tokens + ?, usage_cost_usd = ?,
                           updated_at = ? WHERE id = ?""",
                    (input_tokens, output_tokens, str(prior_cost + usage_cost_usd),
                     timestamp, generation_run_id),
                ).rowcount
                if changed != 1:
                    raise P4BudgetStateError("p4_generation_run_transition_failed")
        except P4BudgetStateError:
            raise
        except sqlite3.Error:
            raise P4BudgetStateError("p4_usage_persist_failed") from None

    def fail_usage_unknown(
        self,
        generation_run_id: str,
        model_call_id: str,
        reason: str,
        duration_ms: int,
    ) -> None:
        timestamp = self._now()
        try:
            with self._connect() as db:
                changed = db.execute(
                    """UPDATE p4_model_call_budget
                       SET dispatch_state = 'usage_unknown',
                           usage_status = 'unknown', updated_at = ?
                       WHERE model_call_id = ? AND generation_run_id = ?
                         AND dispatch_state = 'dispatch_pending'""",
                    (timestamp, model_call_id, generation_run_id),
                ).rowcount
                if changed != 1:
                    raise P4BudgetStateError("p4_usage_transition_failed")
                changed = db.execute(
                    """UPDATE p4_model_calls
                       SET duration_ms = ?, status = 'failed', error = ?
                       WHERE id = ? AND status = 'reserved'""",
                    (duration_ms, reason, model_call_id),
                ).rowcount
                if changed != 1:
                    raise P4BudgetStateError("p4_legacy_call_transition_failed")
                changed = db.execute(
                    """UPDATE p4_generation_runs
                       SET state = 'usage_unknown', reason = ?,
                           unknown_usage_calls = unknown_usage_calls + 1,
                           cost_complete = 0, updated_at = ? WHERE id = ?""",
                    (reason, timestamp, generation_run_id),
                ).rowcount
                if changed != 1:
                    raise P4BudgetStateError("p4_generation_run_transition_failed")
        except P4BudgetStateError:
            raise
        except sqlite3.Error:
            raise P4BudgetStateError("p4_usage_persist_failed") from None

    def save_validated_partial(self, generation_run_id: str, payload: Mapping) -> None:
        """Append only caller-validated structured output to the private run record."""
        timestamp = self._now()
        try:
            encoded_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"))
            with self._connect() as db:
                row = db.execute(
                    "SELECT validated_partial_json FROM p4_generation_runs WHERE id = ?",
                    (generation_run_id,),
                ).fetchone()
                if row is None:
                    raise P4BudgetStateError("p4_generation_run_missing")
                partials = json.loads(row["validated_partial_json"])
                partials.append(json.loads(encoded_payload))
                changed = db.execute(
                    """UPDATE p4_generation_runs
                       SET validated_partial_json = ?, updated_at = ? WHERE id = ?""",
                    (json.dumps(partials, sort_keys=True, separators=(",", ":")),
                     timestamp, generation_run_id),
                ).rowcount
                if changed != 1:
                    raise P4BudgetStateError("p4_generation_run_transition_failed")
        except P4BudgetStateError:
            raise
        except (sqlite3.Error, TypeError, ValueError):
            raise P4BudgetStateError("p4_validated_partial_persist_failed") from None
