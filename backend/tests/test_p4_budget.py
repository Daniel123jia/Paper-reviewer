from __future__ import annotations

from decimal import Decimal
import json
from pathlib import Path
import sqlite3

import pytest

from backend.p4_budget import (
    P4_BUDGET_ENV_KEYS,
    P4BudgetConfigurationError,
    P4BudgetStateError,
    P4BudgetStore,
    P4RequestReservation,
    require_p4_budget_configuration,
)
from backend.p4_api import initialize_p4_tables


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


def database_connect(path):
    def connect():
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        return connection
    return connect


def budget_configuration():
    return require_p4_budget_configuration(
        Path("missing.env"), valid_budget_environment()
    )


def reservation(call_id="call-1"):
    return P4RequestReservation(
        model_call_id=call_id,
        request_index=1,
        stage="writing-1-attempt-1",
        prompt_version="review-comment-writing-en/v1",
        estimated_input_tokens=120,
        reserved_output_tokens=500,
        max_tokens=500,
        remaining_duration_ms=180000,
        planned_cost_usd=Decimal("0.002096"),
    )


def test_p4_budget_tables_are_idempotent_and_keep_historical_calls(tmp_path):
    path = tmp_path / "review.db"
    with database_connect(path)() as db:
        initialize_p4_tables(db)
        db.execute("INSERT INTO p4_model_calls VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                   ("old-call", None, "project-1", "writing", "v1", "old-model",
                    "old-model", 10, 3, 20, "completed", None, "old-time"))
    with database_connect(path)() as db:
        initialize_p4_tables(db)
        tables = {row[0] for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
        old = db.execute("SELECT status, input_tokens FROM p4_model_calls "
                         "WHERE id = 'old-call'").fetchone()

    assert {"p4_generation_runs", "p4_model_call_budget"}.issubset(tables)
    assert tuple(old) == ("completed", 10)


def test_store_persists_reservation_before_dispatch_and_settles_same_ids(tmp_path):
    path = tmp_path / "review.db"
    connect = database_connect(path)
    with connect() as db:
        initialize_p4_tables(db)
    store = P4BudgetStore(connect, lambda: "2026-11-07T00:00:00+00:00")
    config = budget_configuration()
    store.create_run("run-1", "draft-1", "project-1", "author", "en", config)

    store.reserve_request("run-1", reservation(), "model-1")
    with connect() as db:
        run = db.execute("SELECT state, request_count FROM p4_generation_runs "
                         "WHERE id = 'run-1'").fetchone()
        call = db.execute("SELECT dispatch_state, request_index FROM p4_model_call_budget "
                          "WHERE model_call_id = 'call-1'").fetchone()
        legacy = db.execute("SELECT id, status FROM p4_model_calls "
                            "WHERE id = 'call-1'").fetchone()
    assert tuple(run) == ("request_reserved", 1)
    assert tuple(call) == ("reserved_not_dispatched", 1)
    assert tuple(legacy) == ("call-1", "reserved")

    store.mark_dispatch_pending("run-1", "call-1")
    store.settle_valid_usage(
        "run-1", "call-1", actual_model="model-1", input_tokens=121,
        output_tokens=40, total_tokens=161, duration_ms=25,
        usage_cost_usd=Decimal("0.0002568"),
    )
    with connect() as db:
        run = dict(db.execute("SELECT * FROM p4_generation_runs WHERE id = 'run-1'").fetchone())
        call = dict(db.execute("SELECT * FROM p4_model_call_budget "
                               "WHERE model_call_id = 'call-1'").fetchone())
        legacy = dict(db.execute("SELECT * FROM p4_model_calls WHERE id = 'call-1'").fetchone())
    assert run["state"] == "usage_valid"
    assert run["input_tokens"] == 121 and run["output_tokens"] == 40
    assert call["dispatch_state"] == "completed_usage_valid"
    assert call["actual_total_tokens"] == 161
    assert legacy["status"] == "completed" and legacy["input_tokens"] == 121


def test_store_rejects_duplicate_call_id_without_double_counting(tmp_path):
    path = tmp_path / "review.db"
    connect = database_connect(path)
    with connect() as db:
        initialize_p4_tables(db)
    store = P4BudgetStore(connect, lambda: "2026-11-07T00:00:00+00:00")
    store.create_run("run-1", "draft-1", "project-1", "author", "en",
                     budget_configuration())
    store.reserve_request("run-1", reservation(), "model-1")

    with pytest.raises(P4BudgetStateError):
        store.reserve_request("run-1", reservation(), "model-1")

    with connect() as db:
        assert db.execute("SELECT request_count FROM p4_generation_runs "
                          "WHERE id = 'run-1'").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM p4_model_call_budget").fetchone()[0] == 1


def test_unknown_usage_is_terminal_and_never_recorded_as_zero(tmp_path):
    path = tmp_path / "review.db"
    connect = database_connect(path)
    with connect() as db:
        initialize_p4_tables(db)
    store = P4BudgetStore(connect, lambda: "2026-11-07T00:00:00+00:00")
    store.create_run("run-1", "draft-1", "project-1", "author", "en",
                     budget_configuration())
    store.reserve_request("run-1", reservation(), "model-1")
    store.mark_dispatch_pending("run-1", "call-1")

    store.fail_usage_unknown("run-1", "call-1", "provider_timeout", 180000)

    with connect() as db:
        run = dict(db.execute("SELECT * FROM p4_generation_runs WHERE id = 'run-1'").fetchone())
        call = dict(db.execute("SELECT * FROM p4_model_call_budget "
                               "WHERE model_call_id = 'call-1'").fetchone())
        legacy = dict(db.execute("SELECT * FROM p4_model_calls WHERE id = 'call-1'").fetchone())
    assert run["state"] == "usage_unknown"
    assert run["unknown_usage_calls"] == 1
    assert run["cost_complete"] == 0
    assert run["input_tokens"] == 0 and run["output_tokens"] == 0
    assert call["dispatch_state"] == "usage_unknown"
    assert call["actual_total_tokens"] is None
    assert legacy["status"] == "failed" and legacy["error"] == "provider_timeout"


def test_only_validated_partial_payloads_are_appended(tmp_path):
    path = tmp_path / "review.db"
    connect = database_connect(path)
    with connect() as db:
        initialize_p4_tables(db)
    store = P4BudgetStore(connect, lambda: "2026-11-07T00:00:00+00:00")
    store.create_run("run-1", "draft-1", "project-1", "author", "en",
                     budget_configuration())

    store.save_validated_partial("run-1", {"batch": 1, "content": "checked"})
    store.save_validated_partial("run-1", {"batch": 2, "content": "checked twice"})

    with connect() as db:
        row = db.execute("SELECT validated_partial_json FROM p4_generation_runs "
                         "WHERE id = 'run-1'").fetchone()
    assert json.loads(row[0]) == [
        {"batch": 1, "content": "checked"},
        {"batch": 2, "content": "checked twice"},
    ]
