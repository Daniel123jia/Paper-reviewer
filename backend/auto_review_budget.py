"""Private, fail-closed request ledger for a single automatic review.

Costs are local estimates. Dispatched requests without valid usage remain unknown.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import asdict, dataclass
from decimal import Decimal
from pathlib import Path


class BudgetBlocked(RuntimeError):
    pass


@dataclass(frozen=True)
class AutoReviewBudget:
    max_calls: int
    max_input_tokens: int
    max_output_tokens: int
    max_output_tokens_per_request: int
    max_cost_usd: Decimal
    input_usd_per_million: Decimal
    output_usd_per_million: Decimal

    def __post_init__(self):
        ints = (self.max_calls, self.max_input_tokens, self.max_output_tokens,
                self.max_output_tokens_per_request)
        amounts = (self.max_cost_usd, self.input_usd_per_million, self.output_usd_per_million)
        if (any(type(x) is not int or x <= 0 for x in ints)
                or self.max_output_tokens_per_request > self.max_output_tokens
                or any(not isinstance(x, Decimal) or not x.is_finite() or x <= 0 for x in amounts)):
            raise ValueError('invalid_budget')

    def encode(self):
        return json.dumps(asdict(self), default=str, sort_keys=True)

    @classmethod
    def decode(cls, value):
        data = json.loads(value)
        for key in ('max_cost_usd', 'input_usd_per_million', 'output_usd_per_million'):
            data[key] = Decimal(data[key])
        return cls(**data)


class AutoReviewLedger:
    def __init__(self, path: Path):
        self.path = Path(path)
        with self.connect() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS auto_review_budget_tasks
              (id TEXT PRIMARY KEY, budget TEXT NOT NULL, status TEXT NOT NULL,
               requested_model TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS auto_review_stage_budgets
              (task_id TEXT NOT NULL, stage TEXT NOT NULL, budget TEXT NOT NULL,
               PRIMARY KEY(task_id,stage));
              CREATE TABLE IF NOT EXISTS auto_review_calls
              (id TEXT PRIMARY KEY, task_id TEXT NOT NULL, stage TEXT NOT NULL,
               status TEXT NOT NULL, input_estimate INTEGER NOT NULL, output_reservation INTEGER NOT NULL,
               input_tokens INTEGER, output_tokens INTEGER, known_cost TEXT, actual_model TEXT);
            ''')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def create_task(self, task_id, total, stages, *, requested_model):
        if not task_id or not stages or not requested_model or not isinstance(requested_model, str):
            raise ValueError('invalid_task')
        if any((budget.input_usd_per_million, budget.output_usd_per_million) !=
               (total.input_usd_per_million, total.output_usd_per_million)
               for budget in stages.values()):
            raise ValueError('price_snapshot_mismatch')
        with self.connect() as db:
            db.execute('INSERT INTO auto_review_budget_tasks VALUES (?,?,?,?)', (task_id, total.encode(), 'running', requested_model))
            db.executemany('INSERT INTO auto_review_stage_budgets VALUES (?,?,?)',
                           [(task_id, stage, budget.encode()) for stage, budget in stages.items()])

    @staticmethod
    def _admit(budget, rows, estimated, output):
        if output > budget.max_output_tokens_per_request:
            raise BudgetBlocked('request_output_budget')
        input_used = sum(r['input_tokens'] if r['input_tokens'] is not None else r['input_estimate'] for r in rows)
        output_used = sum(r['output_tokens'] if r['output_tokens'] is not None else r['output_reservation'] for r in rows)
        cost = sum((Decimal(r['known_cost']) if r['known_cost'] is not None else
                    (Decimal(r['input_estimate']) * budget.input_usd_per_million +
                     Decimal(r['output_reservation']) * budget.output_usd_per_million) / 1_000_000)
                   for r in rows)
        reserve = (Decimal(estimated) * budget.input_usd_per_million + Decimal(output) * budget.output_usd_per_million) / 1_000_000
        if (len(rows) + 1 > budget.max_calls or input_used + estimated > budget.max_input_tokens
                or output_used + output > budget.max_output_tokens or cost + reserve > budget.max_cost_usd):
            raise BudgetBlocked('budget_exhausted')

    def reserve(self, task_id, stage, *, input_estimate, output_reservation):
        if type(input_estimate) is not int or input_estimate < 0 or type(output_reservation) is not int or output_reservation <= 0:
            raise BudgetBlocked('invalid_reservation')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            task = db.execute('SELECT * FROM auto_review_budget_tasks WHERE id=?', (task_id,)).fetchone()
            stage_row = db.execute('SELECT budget FROM auto_review_stage_budgets WHERE task_id=? AND stage=?', (task_id, stage)).fetchone()
            if not task or task['status'] != 'running' or not stage_row:
                raise BudgetBlocked('task_blocked')
            rows = db.execute('SELECT * FROM auto_review_calls WHERE task_id=?', (task_id,)).fetchall()
            if any(row['status'] in ('reserved', 'unknown_usage', 'dispatch_pending') for row in rows):
                raise BudgetBlocked('unsettled_request')
            self._admit(AutoReviewBudget.decode(task['budget']), rows, input_estimate, output_reservation)
            self._admit(AutoReviewBudget.decode(stage_row['budget']), [r for r in rows if r['stage'] == stage], input_estimate, output_reservation)
            call_id = uuid.uuid4().hex
            db.execute('INSERT INTO auto_review_calls (id,task_id,stage,status,input_estimate,output_reservation) VALUES (?,?,?,?,?,?)',
                       (call_id, task_id, stage, 'reserved', input_estimate, output_reservation))
            return call_id

    def mark_dispatched(self, call_id):
        with self.connect() as db:
            changed = db.execute("UPDATE auto_review_calls SET status='dispatch_pending' WHERE id=? AND status='reserved' AND task_id IN (SELECT id FROM auto_review_budget_tasks WHERE status='running')", (call_id,)).rowcount
            if changed != 1:
                raise BudgetBlocked('invalid_call_state')

    def mark_unknown(self, call_id):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT task_id FROM auto_review_calls WHERE id=? AND status='dispatch_pending'", (call_id,)).fetchone()
            if not row:
                raise BudgetBlocked('invalid_call_state')
            db.execute("UPDATE auto_review_calls SET status='unknown_usage' WHERE id=?", (call_id,))
            db.execute("UPDATE auto_review_budget_tasks SET status='unknown_usage' WHERE id=?", (row['task_id'],))

    def settle(self, call_id, *, input_tokens, output_tokens, total_tokens, actual_model):
        if any(type(x) is not int or x < 0 for x in (input_tokens, output_tokens, total_tokens)) or input_tokens + output_tokens != total_tokens:
            self.mark_unknown(call_id)
            raise BudgetBlocked('usage_invalid')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT c.*,s.budget,t.requested_model FROM auto_review_calls c JOIN auto_review_stage_budgets s ON s.task_id=c.task_id AND s.stage=c.stage JOIN auto_review_budget_tasks t ON t.id=c.task_id WHERE c.id=? AND c.status='dispatch_pending'", (call_id,)).fetchone()
            if not row:
                raise BudgetBlocked('invalid_call_state')
            if actual_model != row['requested_model']:
                db.execute("UPDATE auto_review_calls SET status='unknown_usage' WHERE id=?", (call_id,))
                db.execute("UPDATE auto_review_budget_tasks SET status='unknown_usage' WHERE id=?", (row['task_id'],))
                db.commit()
                raise BudgetBlocked('model_mismatch')
            budget = AutoReviewBudget.decode(row['budget'])
            cost = (Decimal(input_tokens) * budget.input_usd_per_million + Decimal(output_tokens) * budget.output_usd_per_million) / 1_000_000
            db.execute("UPDATE auto_review_calls SET status='usage_valid',input_tokens=?,output_tokens=?,known_cost=?,actual_model=? WHERE id=?", (input_tokens, output_tokens, str(cost), actual_model, call_id))
            allrows = db.execute('SELECT * FROM auto_review_calls WHERE task_id=?', (row['task_id'],)).fetchall()
            total = AutoReviewBudget.decode(db.execute('SELECT budget FROM auto_review_budget_tasks WHERE id=?', (row['task_id'],)).fetchone()[0])
            stage_rows = [r for r in allrows if r['stage'] == row['stage']]
            def exceeded(limits, rows):
                return (sum(r['input_tokens'] or 0 for r in rows) > limits.max_input_tokens
                        or sum(r['output_tokens'] or 0 for r in rows) > limits.max_output_tokens
                        or sum((Decimal(r['known_cost'] or '0') for r in rows), Decimal(0)) > limits.max_cost_usd)
            if output_tokens > row['output_reservation'] or exceeded(total, allrows) or exceeded(budget, stage_rows):
                db.execute("UPDATE auto_review_budget_tasks SET status='budget_exhausted' WHERE id=?", (row['task_id'],))

    def recover_interrupted(self):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE auto_review_budget_tasks SET status='unknown_usage' WHERE id IN (SELECT task_id FROM auto_review_calls WHERE status='dispatch_pending')")
            db.execute("UPDATE auto_review_calls SET status='unknown_usage' WHERE status='dispatch_pending'")
            db.execute("UPDATE auto_review_budget_tasks SET status='interrupted' WHERE status='running'")

    def task_status(self, task_id):
        with self.connect() as db:
            row = db.execute('SELECT status FROM auto_review_budget_tasks WHERE id=?', (task_id,)).fetchone()
            return row['status'] if row else None

    def summary(self, task_id):
        with self.connect() as db:
            rows = db.execute('SELECT * FROM auto_review_calls WHERE task_id=? ORDER BY rowid', (task_id,)).fetchall()
        return {'request_count': sum(r['status'] != 'reserved' for r in rows),
                'reserved_not_sent_calls': sum(r['status'] == 'reserved' for r in rows),
                'known_input_tokens': sum(r['input_tokens'] for r in rows if r['input_tokens'] is not None),
                'known_output_tokens': sum(r['output_tokens'] for r in rows if r['output_tokens'] is not None),
                'known_cost_usd': str(sum((Decimal(r['known_cost']) for r in rows if r['known_cost'] is not None), Decimal(0))),
                'unknown_usage_calls': sum(r['status'] in ('unknown_usage', 'dispatch_pending') for r in rows),
                'calls': [{'id': r['id'], 'stage': r['stage'], 'status': r['status'],
                           'input_tokens': r['input_tokens'], 'output_tokens': r['output_tokens'],
                           'actual_model': r['actual_model'], 'known_cost_usd': r['known_cost']}
                          for r in rows],
                'provider_billed_cost_usd': None}

    def mark_budget_exhausted(self, task_id):
        with self.connect() as db:
            db.execute("UPDATE auto_review_budget_tasks SET status='budget_exhausted' WHERE id=? AND status='running'", (task_id,))
