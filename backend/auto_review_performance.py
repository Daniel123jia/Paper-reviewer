"""Bounded opt-in execution for ordinary automatic reviews, not C1.

No request bodies or credentials are retained. Concurrency improves overlap, not
scientific coverage or provider identity. Local rate bounds are not account limits.
"""
from concurrent.futures import ThreadPoolExecutor
from threading import Lock
import time

from pydantic import BaseModel, ConfigDict, Field
from backend.p2a_engine import RunBudget, BudgetExceeded, UnknownUsageError
from backend.model_runtime import ModelConfigurationError
from scripts.p5c.stage_control import C1ActualBudgetExceeded


class ExecutionPolicy(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    max_concurrency: int = Field(default=1, ge=1, le=2)
    requests_per_minute: int = Field(default=60, ge=1, le=120)
    input_reservation_per_minute: int = Field(default=1000000, ge=1, le=2000000)


def bounded_map(work, items, *, max_workers=1):
    """Small waves: never queue the next wave after an error; drain sent peers."""
    if max_workers not in (1, 2):
        raise ValueError('invalid_concurrency')
    items = list(items)
    if max_workers == 1:
        return [work(item) for item in items]
    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        for offset in range(0, len(items), max_workers):
            futures = [pool.submit(work, item) for item in items[offset:offset+max_workers]]
            # Executor shutdown waits for peers even when result() raises.
            results.extend(future.result() for future in futures)
    return results


class ConcurrentRunBudget(RunBudget):
    """Atomic stage admission, including in-flight reservations and wall time.

The durable task ledger remains authoritative at SDK dispatch. This budget keeps
existing engine-local limits valid when two independent engine jobs overlap.
"""
    def __init__(self, **limits):
        super().__init__(**limits)
        self._lock = Lock()
        self._started = time.monotonic()

    def call(self, stage, system, user, model_call):
        estimated_input = max(1, (len(system) + len(user) + 3)//4)
        estimated_output = 4096
        with self._lock:
            if self.unknown_usage_latched:
                raise UnknownUsageError(stage, 'previous_call_unsettled')
            if (self.calls >= self.max_calls or
                    self.reserved_input_tokens + estimated_input > self.max_input_tokens or
                    self.reserved_output_tokens + estimated_output > self.max_output_tokens or
                    (time.monotonic()-self._started)*1000 >= self.max_duration_ms):
                raise BudgetExceeded(stage, 'budget_exceeded', [])
            self.calls += 1
            self.reserved_input_tokens += estimated_input
            self.reserved_output_tokens += estimated_output
        started = time.monotonic()
        try:
            reply = model_call(stage, system, user)
        except (ModelConfigurationError, C1ActualBudgetExceeded) as exc:
            with self._lock:
                self.reserved_input_tokens -= estimated_input
                self.reserved_output_tokens -= estimated_output
                if isinstance(exc, ModelConfigurationError):
                    self.calls -= 1
                else:
                    self.reserved_input_tokens += exc.input_tokens
                    self.reserved_output_tokens += exc.output_tokens
            if isinstance(exc, C1ActualBudgetExceeded):
                raise BudgetExceeded(stage, 'actual_budget_exceeded', []) from None
            raise
        except Exception as exc:
            with self._lock:
                self.unknown_usage_calls += 1
                self.unknown_usage_latched = True
            raise UnknownUsageError(stage, type(exc).__name__,
                duration_ms=round((time.monotonic()-started)*1000)) from exc
        with self._lock:
            self.elapsed_ms = round((time.monotonic()-self._started)*1000)
            valid = isinstance(reply, dict) and all(type(reply.get(key)) is int and
                reply[key] >= 0 for key in ('input_tokens','output_tokens'))
            if not valid:
                self.unknown_usage_calls += 1
                self.unknown_usage_latched = True
                raise UnknownUsageError(stage, 'invalid_usage')
            self.reserved_input_tokens += reply['input_tokens']-estimated_input
            self.reserved_output_tokens += reply['output_tokens']-estimated_output
        return reply


def summarize_performance(rows):
    """Measured SDK-span overlap; no inferred supplier time or true token estimate.

    Critical path here is the union of dispatch-to-settlement intervals within
    the strictly sequential stages. It excludes parser/assembly/queue CPU time.
    """
    stages={}
    for stage in ('p2a','p2b','p2c','p3','p4'):
        calls=[dict(r) for r in rows if r['stage']==stage]
        if not calls:
            continue
        intervals=[(r['dispatched_at'],r['finished_at']) for r in calls
                   if r['dispatched_at'] is not None and r['finished_at'] is not None]
        events=sorted([(a,1) for a,b in intervals]+[(b,-1) for a,b in intervals])
        active=peak=0
        for _,delta in events:
            active+=delta
            peak=max(peak,active)
        union=0
        end=None
        for a,b in sorted(intervals):
            union+=max(0,b-max(a,end if end is not None else a))
            end=max(b,end if end is not None else b)
        first=min((r['dispatched_at'] for r in calls if r['dispatched_at'] is not None),default=None)
        last=max((r['finished_at'] for r in calls if r['finished_at'] is not None),default=None)
        seen=set()
        repeated=0
        for r in calls:
            if r['system_sha256']:
                if r['system_sha256'] in seen:
                    repeated+=r['system_bytes'] or 0
                seen.add(r['system_sha256'])
        timing_available=all(r['dispatched_at'] is not None for r in calls if r['status']!='reserved')
        timing_complete=timing_available and all(r['finished_at'] is not None for r in calls if r['status']!='reserved')
        stages[stage]={
            'requests':sum(r['status']!='reserved' for r in calls),
            'started_at':first, 'last_settled_at':last,
            'wall_span_ms':round((last-first)*1000) if first is not None and last is not None else None,
            'model_wait_sum_ms':round(sum(b-a for a,b in intervals)*1000) if timing_available else None,
            'model_wait_union_ms':round(union*1000) if timing_available else None, 'peak_concurrency':peak if timing_available else None,
            'input_bytes':sum(r['input_estimate'] for r in calls),
            'repeated_system_bytes':repeated,
            'known_input_tokens':sum(r['input_tokens'] or 0 for r in calls),
            'known_output_tokens':sum(r['output_tokens'] or 0 for r in calls),
            'timing_complete':timing_complete,
        }
    return {'clock':'unix_seconds', 'stages':stages,
            'model_critical_path_ms':sum(v['model_wait_union_ms'] for v in stages.values()) if all(v['timing_complete'] for v in stages.values()) else None,
            'five_minute_target':'unverified',
            'timing_scope':'SDK dispatch through usage settlement; includes transport and local settlement; excludes parsing/assembly',
            'repeated_context_scope':'exact repeated system prompts only; lower bound, not all semantic duplication'}
