"""Private, fail-closed request ledger for a single automatic review.

Costs are local estimates. Dispatched requests without valid usage remain unknown.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
import time
from threading import Lock
from contextlib import contextmanager
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
    max_duration_seconds: int = 3600

    def __post_init__(self):
        ints = (self.max_calls, self.max_input_tokens, self.max_output_tokens,
                self.max_output_tokens_per_request, self.max_duration_seconds)
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
        self._dispatch_lock = Lock()
        self._blocked_tasks = set()
        with self.connect() as db:
            db.executescript('''
              CREATE TABLE IF NOT EXISTS auto_review_budget_tasks
              (id TEXT PRIMARY KEY, budget TEXT NOT NULL, status TEXT NOT NULL,
               requested_model TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS auto_review_stage_budgets
              (task_id TEXT NOT NULL, stage TEXT NOT NULL, budget TEXT NOT NULL,
               PRIMARY KEY(task_id,stage));
              CREATE TABLE IF NOT EXISTS auto_review_stage_timings
              (task_id TEXT NOT NULL, stage TEXT NOT NULL, started_at REAL NOT NULL,
               finished_at REAL, PRIMARY KEY(task_id,stage));
              CREATE TABLE IF NOT EXISTS auto_review_calls
              (id TEXT PRIMARY KEY, task_id TEXT NOT NULL, stage TEXT NOT NULL,
               status TEXT NOT NULL, input_estimate INTEGER NOT NULL, output_reservation INTEGER NOT NULL,
               input_tokens INTEGER, output_tokens INTEGER, known_cost TEXT, actual_model TEXT);
            ''')
            for name, kind in (('reserved_at','REAL'), ('dispatched_at','REAL'),
                               ('finished_at','REAL'), ('operation','TEXT'),
                               ('input_sha256','TEXT'), ('system_sha256','TEXT'), ('system_bytes','INTEGER'),
                               ('diagnostic_json','TEXT')):
                columns = {r['name'] for r in db.execute('PRAGMA table_info(auto_review_calls)')}
                if name not in columns:
                    db.execute(f'ALTER TABLE auto_review_calls ADD COLUMN {name} {kind}')
            if 'execution_policy' not in {r['name'] for r in db.execute('PRAGMA table_info(auto_review_budget_tasks)')}:
                db.execute('ALTER TABLE auto_review_budget_tasks ADD COLUMN execution_policy TEXT')
            for table in ('auto_review_budget_tasks', 'auto_review_stage_budgets'):
                columns = {row['name'] for row in db.execute(f'PRAGMA table_info({table})')}
                if 'started_at' not in columns:
                    db.execute(f'ALTER TABLE {table} ADD COLUMN started_at REAL')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def block_dispatch(self, task_id):
        # A transient SQLite failure must not let a parallel peer dispatch more.
        with self._dispatch_lock:
            self._blocked_tasks.add(task_id)

    def _dispatch_blocked(self, task_id):
        with self._dispatch_lock:
            return task_id in self._blocked_tasks

    def create_task(self, task_id, total, stages, *, requested_model):
        if not task_id or not stages or not requested_model or not isinstance(requested_model, str):
            raise ValueError('invalid_task')
        if any((budget.input_usd_per_million, budget.output_usd_per_million) !=
               (total.input_usd_per_million, total.output_usd_per_million)
               for budget in stages.values()):
            raise ValueError('price_snapshot_mismatch')
        with self.connect() as db:
            db.execute('INSERT INTO auto_review_budget_tasks (id,budget,status,requested_model,started_at) VALUES (?,?,?,?,?)', (task_id, total.encode(), 'running', requested_model,time.time()))
            db.executemany('INSERT INTO auto_review_stage_budgets (task_id,stage,budget) VALUES (?,?,?)',
                           [(task_id, stage, budget.encode()) for stage, budget in stages.items()])

    def set_execution_policy(self, task_id, policy):
        from backend.auto_review_performance import ExecutionPolicy
        value = ExecutionPolicy.model_validate(policy).model_dump_json()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM auto_review_calls WHERE task_id=?', (task_id,)).fetchone():
                raise BudgetBlocked('policy_already_in_use')
            db.execute('UPDATE auto_review_budget_tasks SET execution_policy=? WHERE id=?', (value,task_id))

    def execution_policy(self, task_id):
        from backend.auto_review_performance import ExecutionPolicy
        with self.connect() as db:
            row = db.execute('SELECT execution_policy FROM auto_review_budget_tasks WHERE id=?',(task_id,)).fetchone()
        return ExecutionPolicy.model_validate_json(row[0]) if row and row[0] else ExecutionPolicy()

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

    def reserve(self, task_id, stage, *, input_estimate, output_reservation, call_id=None, operation=None, input_sha256=None, system_sha256=None, system_bytes=None):
        if type(input_estimate) is not int or input_estimate < 0 or type(output_reservation) is not int or output_reservation <= 0:
            raise BudgetBlocked('invalid_reservation')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            task = db.execute('SELECT * FROM auto_review_budget_tasks WHERE id=?', (task_id,)).fetchone()
            stage_row = db.execute('SELECT * FROM auto_review_stage_budgets WHERE task_id=? AND stage=?', (task_id, stage)).fetchone()
            if not task or task['status'] != 'running' or not stage_row or self._dispatch_blocked(task_id):
                raise BudgetBlocked('task_blocked')
            now = time.time()
            if ((task['started_at'] is not None and now-task['started_at'] >= AutoReviewBudget.decode(task['budget']).max_duration_seconds)
                    or (stage_row['started_at'] is not None and now-stage_row['started_at'] >= AutoReviewBudget.decode(stage_row['budget']).max_duration_seconds)):
                raise BudgetBlocked('duration_exhausted')
            if stage_row['started_at'] is None:
                db.execute('UPDATE auto_review_stage_budgets SET started_at=? WHERE task_id=? AND stage=?', (now,task_id,stage))
            rows = db.execute('SELECT * FROM auto_review_calls WHERE task_id=?', (task_id,)).fetchall()
            policy = json.loads(task['execution_policy']) if task['execution_policy'] else {}
            capacity = policy.get('max_concurrency',1) if stage in ('p2a','p2b','p3') else 1
            active = [r for r in rows if r['status'] in ('reserved','dispatch_pending')]
            if (any(r['status']=='unknown_usage' for r in rows) or
                    len(active) >= capacity or any(r['stage']!=stage for r in active)):
                raise BudgetBlocked('unsettled_request')
            self._admit(AutoReviewBudget.decode(task['budget']), rows, input_estimate, output_reservation)
            self._admit(AutoReviewBudget.decode(stage_row['budget']), [r for r in rows if r['stage'] == stage], input_estimate, output_reservation)
            call_id = call_id or uuid.uuid4().hex
            db.execute('INSERT INTO auto_review_calls (id,task_id,stage,status,input_estimate,output_reservation) VALUES (?,?,?,?,?,?)',
                       (call_id, task_id, stage, 'reserved', input_estimate, output_reservation))
            db.execute('UPDATE auto_review_calls SET reserved_at=?,operation=?,input_sha256=?,system_sha256=?,system_bytes=? WHERE id=?',
                       (now,operation,input_sha256,system_sha256,system_bytes,call_id))
            return call_id

    def mark_dispatched(self, call_id):
        # One transaction serializes the admission point with unknown latching.
        # Wait only for local rolling-minute rate capacity, never retry an SDK call.
        while True:
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                row = db.execute("SELECT c.*,t.status AS task_status,t.execution_policy,t.budget,t.started_at AS task_started FROM auto_review_calls c JOIN auto_review_budget_tasks t ON t.id=c.task_id WHERE c.id=?",(call_id,)).fetchone()
                if not row or row['status']!='reserved' or row['task_status']!='running' or self._dispatch_blocked(row['task_id']):
                    raise BudgetBlocked('invalid_call_state')
                now = time.time()
                limits = AutoReviewBudget.decode(row['budget'])
                stage = db.execute('SELECT budget,started_at FROM auto_review_stage_budgets WHERE task_id=? AND stage=?',(row['task_id'],row['stage'])).fetchone()
                if ((row['task_started'] is not None and now-row['task_started'] >= limits.max_duration_seconds) or
                        (stage['started_at'] is not None and now-stage['started_at'] >= AutoReviewBudget.decode(stage['budget']).max_duration_seconds)):
                    raise BudgetBlocked('duration_exhausted')
                policy = json.loads(row['execution_policy']) if row['execution_policy'] else {}
                recent = db.execute('SELECT dispatched_at,input_estimate FROM auto_review_calls WHERE task_id=? AND dispatched_at>? ORDER BY dispatched_at', (row['task_id'],now-60)).fetchall()
                token_rate = policy.get('input_reservation_per_minute',1000000)
                if row['input_estimate'] > token_rate:
                    raise BudgetBlocked('request_exceeds_local_rate')
                if (len(recent) < policy.get('requests_per_minute',60) and
                        sum(r['input_estimate'] for r in recent)+row['input_estimate'] <= token_rate):
                    with self._dispatch_lock:
                        if row['task_id'] in self._blocked_tasks:
                            raise BudgetBlocked('task_blocked')
                        db.execute("UPDATE auto_review_calls SET status='dispatch_pending',dispatched_at=? WHERE id=?",(now,call_id))
                        db.commit()
                    return
            time.sleep(min(0.5,max(0.01,recent[0]['dispatched_at']+60-time.time())))

    def mark_unknown(self, call_id):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT task_id FROM auto_review_calls WHERE id=? AND status='dispatch_pending'", (call_id,)).fetchone()
            if not row:
                raise BudgetBlocked('invalid_call_state')
            self.block_dispatch(row['task_id'])
            db.execute("UPDATE auto_review_calls SET status='unknown_usage',finished_at=(julianday('now')-2440587.5)*86400.0 WHERE id=?", (call_id,))
            db.execute("UPDATE auto_review_budget_tasks SET status='unknown_usage' WHERE id=?", (row['task_id'],))

    def settle(self, call_id, *, input_tokens, output_tokens, total_tokens, actual_model):
        if any(type(x) is not int or x < 0 for x in (input_tokens, output_tokens, total_tokens)) or input_tokens + output_tokens != total_tokens:
            self.mark_unknown(call_id)
            raise BudgetBlocked('usage_invalid')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT c.*,s.budget,s.started_at AS stage_started_at,t.started_at AS task_started_at,t.requested_model,t.budget AS total_budget FROM auto_review_calls c JOIN auto_review_stage_budgets s ON s.task_id=c.task_id AND s.stage=c.stage JOIN auto_review_budget_tasks t ON t.id=c.task_id WHERE c.id=? AND c.status='dispatch_pending'", (call_id,)).fetchone()
            if not row:
                raise BudgetBlocked('invalid_call_state')
            if actual_model != row['requested_model']:
                self.block_dispatch(row['task_id'])
                db.execute("UPDATE auto_review_calls SET status='unknown_usage',finished_at=(julianday('now')-2440587.5)*86400.0 WHERE id=?", (call_id,))
                db.execute("UPDATE auto_review_budget_tasks SET status='unknown_usage' WHERE id=?", (row['task_id'],))
                db.commit()
                raise BudgetBlocked('model_mismatch')
            budget = AutoReviewBudget.decode(row['budget'])
            cost = (Decimal(input_tokens) * budget.input_usd_per_million + Decimal(output_tokens) * budget.output_usd_per_million) / 1_000_000
            db.execute("UPDATE auto_review_calls SET status='usage_valid',input_tokens=?,output_tokens=?,known_cost=?,actual_model=?,finished_at=(julianday('now')-2440587.5)*86400.0 WHERE id=?", (input_tokens, output_tokens, str(cost), actual_model, call_id))
            allrows = db.execute('SELECT * FROM auto_review_calls WHERE task_id=?', (row['task_id'],)).fetchall()
            total = AutoReviewBudget.decode(db.execute('SELECT budget FROM auto_review_budget_tasks WHERE id=?', (row['task_id'],)).fetchone()[0])
            stage_rows = [r for r in allrows if r['stage'] == row['stage']]
            def exceeded(limits, rows):
                return (sum(r['input_tokens'] or 0 for r in rows) > limits.max_input_tokens
                        or sum(r['output_tokens'] or 0 for r in rows) > limits.max_output_tokens
                        or sum((Decimal(r['known_cost'] or '0') for r in rows), Decimal(0)) > limits.max_cost_usd)
            expired = ((row['task_started_at'] is not None and time.time()-row['task_started_at'] >= total.max_duration_seconds)
                       or (row['stage_started_at'] is not None and time.time()-row['stage_started_at'] >= budget.max_duration_seconds))
            if expired or output_tokens > row['output_reservation'] or exceeded(total, allrows) or exceeded(budget, stage_rows):
                db.execute("UPDATE auto_review_budget_tasks SET status='budget_exhausted' WHERE id=? AND status='running'", (row['task_id'],))

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

    def save_diagnostic(self, call_id, observation):
        with self.connect() as db:
            db.execute('UPDATE auto_review_calls SET diagnostic_json=? WHERE id=?',
                       (json.dumps(observation), call_id))

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
                           'actual_model': r['actual_model'], 'known_cost_usd': r['known_cost'],
                           'diagnostic': json.loads(r['diagnostic_json']) if r['diagnostic_json'] else None,
                           'operation': r['operation'], 'input_bytes': r['input_estimate'],
                           'reserved_at': r['reserved_at'], 'dispatched_at': r['dispatched_at'],
                           'finished_at': r['finished_at'],
                           'duration_ms': round((r['finished_at']-r['dispatched_at'])*1000) if r['finished_at'] is not None and r['dispatched_at'] is not None else None}
                          for r in rows],
                'provider_billed_cost_usd': None}

    @contextmanager
    def measure_stage(self, task_id, stage):
        # One invocation per stage; never overwrite a historical start or replay.
        with self.connect() as db:
            db.execute('INSERT INTO auto_review_stage_timings VALUES (?,?,?,NULL)',
                       (task_id,stage,time.time()))
        try:
            yield
        finally:
            with self.connect() as db:
                db.execute('UPDATE auto_review_stage_timings SET finished_at=? WHERE task_id=? AND stage=?',
                           (time.time(),task_id,stage))

    def performance(self, task_id):
        from backend.auto_review_performance import summarize_performance
        with self.connect() as db:
            rows=db.execute('SELECT * FROM auto_review_calls WHERE task_id=? ORDER BY rowid',(task_id,)).fetchall()
            timings=db.execute('SELECT * FROM auto_review_stage_timings WHERE task_id=?',(task_id,)).fetchall()
        result=summarize_performance(rows)
        for row in timings:
            stage=result['stages'].setdefault(row['stage'], {})
            stage.update({'stage_started_at':row['started_at'], 'stage_finished_at':row['finished_at'],
                          'stage_wall_ms':round((row['finished_at']-row['started_at'])*1000) if row['finished_at'] is not None else None})
        return result

    def mark_budget_exhausted(self, task_id):
        with self.connect() as db:
            db.execute("UPDATE auto_review_budget_tasks SET status='budget_exhausted' WHERE id=? AND status='running'", (task_id,))
