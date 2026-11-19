import sqlite3
from decimal import Decimal

import pytest

from backend.auto_review_budget import AutoReviewBudget, AutoReviewLedger, BudgetBlocked


def limits(**changes):
    values = dict(max_calls=3, max_input_tokens=1000, max_output_tokens=100,
                  max_output_tokens_per_request=20, max_cost_usd=Decimal('1'),
                  input_usd_per_million=Decimal('1'), output_usd_per_million=Decimal('2'))
    values.update(changes)
    return AutoReviewBudget(**values)


def test_unknown_usage_stops_entire_task_and_survives_restart(tmp_path):
    path = tmp_path / 'ledger.db'
    ledger = AutoReviewLedger(path)
    ledger.create_task('task', limits(), {'p2a': limits(), 'p2b': limits()}, requested_model='model-a')
    call = ledger.reserve('task', 'p2a', input_estimate=20, output_reservation=20)
    ledger.mark_dispatched(call)
    reopened = AutoReviewLedger(path)
    reopened.recover_interrupted()
    assert reopened.task_status('task') == 'unknown_usage'
    with pytest.raises(BudgetBlocked):
        reopened.reserve('task', 'p2b', input_estimate=20, output_reservation=20)


def test_actual_usage_counts_toward_stage_and_total_budget(tmp_path):
    ledger = AutoReviewLedger(tmp_path / 'ledger.db')
    ledger.create_task('task', limits(max_calls=1), {'p2a': limits()}, requested_model='model-a')
    call = ledger.reserve('task', 'p2a', input_estimate=30, output_reservation=20)
    ledger.mark_dispatched(call)
    ledger.settle(call, input_tokens=10, output_tokens=5, total_tokens=15, actual_model='model-a')
    with pytest.raises(BudgetBlocked):
        ledger.reserve('task', 'p2a', input_estimate=20, output_reservation=20)


def test_reservation_is_persisted_before_dispatch_and_unknown_not_zero(tmp_path):
    ledger = AutoReviewLedger(tmp_path / 'ledger.db')
    ledger.create_task('task', limits(), {'p2a': limits()}, requested_model='model-a')
    call = ledger.reserve('task', 'p2a', input_estimate=10, output_reservation=20)
    ledger.mark_dispatched(call)
    ledger.mark_unknown(call)
    with sqlite3.connect(tmp_path / 'ledger.db') as db:
        assert db.execute('SELECT status, input_tokens, output_tokens FROM auto_review_calls WHERE id=?', (call,)).fetchone() == ('unknown_usage', None, None)


def test_second_reservation_is_blocked_while_first_is_pending(tmp_path):
    ledger = AutoReviewLedger(tmp_path / 'ledger.db')
    ledger.create_task('task', limits(), {'p2a': limits()}, requested_model='model-a')
    ledger.reserve('task', 'p2a', input_estimate=20, output_reservation=20)
    with pytest.raises(BudgetBlocked):
        ledger.reserve('task', 'p2a', input_estimate=20, output_reservation=20)


def test_price_snapshot_must_match_across_stages(tmp_path):
    ledger = AutoReviewLedger(tmp_path / 'ledger.db')
    with pytest.raises(ValueError):
        ledger.create_task('task', limits(), {'p2a': limits(input_usd_per_million=Decimal('2'))}, requested_model='model-a')


def test_returned_model_mismatch_preserves_unknown_and_blocks_next_call(tmp_path):
    ledger = AutoReviewLedger(tmp_path / 'ledger.db')
    ledger.create_task('task', limits(), {'p2a': limits()}, requested_model='model-a')
    call = ledger.reserve('task', 'p2a', input_estimate=20, output_reservation=20)
    ledger.mark_dispatched(call)
    with pytest.raises(BudgetBlocked, match='model_mismatch'):
        ledger.settle(call, input_tokens=5, output_tokens=3, total_tokens=8, actual_model='model-b')
    assert ledger.task_status('task') == 'unknown_usage'
    with pytest.raises(BudgetBlocked):
        ledger.reserve('task', 'p2a', input_estimate=20, output_reservation=20)


def test_restart_does_not_replay_unsent_reservation(tmp_path):
    path = tmp_path / 'ledger.db'
    ledger = AutoReviewLedger(path)
    ledger.create_task('task', limits(), {'p2a': limits()}, requested_model='model-a')
    call = ledger.reserve('task', 'p2a', input_estimate=20, output_reservation=20)
    ledger.recover_interrupted()
    assert ledger.task_status('task') == 'interrupted'
    with sqlite3.connect(path) as db:
        assert db.execute('SELECT status FROM auto_review_calls WHERE id=?', (call,)).fetchone()[0] == 'reserved'
    with pytest.raises(BudgetBlocked):
        ledger.mark_dispatched(call)
