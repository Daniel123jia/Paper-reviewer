"""Bridge P4's per-report safety ledger to the whole-task ledger.

Both ledgers use the same request ID. Only the task ledger contributes to whole
review totals: the P4 ledger remains the report-local safety/audit authority.
Writes are conservative: either ledger failing stops the task; neither replays.
"""
from decimal import Decimal

from backend.auto_review_budget import BudgetBlocked
from backend.model_runtime import ModelConfigurationError
from backend.p4_budget import P4BudgetConfiguration


class AutoP4Bridge:
    def __init__(self, ledger, task_id, budget, source_check=lambda:None, config_check=lambda config:True):
        self.config_check = config_check
        self.source_check = source_check
        self.ledger = ledger
        self.task_id = task_id
        self.configuration = P4BudgetConfiguration(
            max_provider_requests=budget.max_calls,
            max_input_tokens=budget.max_input_tokens,
            max_output_tokens=budget.max_output_tokens,
            max_output_tokens_per_request=budget.max_output_tokens_per_request,
            max_duration_seconds=Decimal(getattr(budget, 'max_duration_seconds', 1800)),
            max_planned_cost_usd=budget.max_cost_usd,
            input_usd_per_million=budget.input_usd_per_million,
            output_usd_per_million=budget.output_usd_per_million,
            pricing_status='estimated_unverified')

    def ensure_reconciled(self):
        self.source_check()
        if self.ledger.task_status(self.task_id) != 'running':
            raise BudgetBlocked('task_blocked')
        with self.ledger.connect() as db:
            rows = db.execute('''SELECT c.*,p.status AS p4_status,
                p.input_tokens AS p4_input,p.output_tokens AS p4_output,
                p.actual_model AS p4_model,b.usage_cost_usd AS p4_cost
                FROM auto_review_calls c LEFT JOIN p4_model_calls p ON p.id=c.id
                LEFT JOIN p4_model_call_budget b ON b.model_call_id=c.id
                WHERE c.task_id=? AND c.stage='p4' ''', (self.task_id,)).fetchall()
        for row in rows:
            if (row['status'] != 'usage_valid' or row['p4_status'] != 'completed'
                    or row['input_tokens'] != row['p4_input']
                    or row['output_tokens'] != row['p4_output']
                    or row['actual_model'] != row['p4_model']
                    or row['p4_cost'] is None
                    or Decimal(row['known_cost']) != Decimal(row['p4_cost'])):
                raise BudgetBlocked('p4_ledger_mismatch')

    def completion(self, reservation, completion):
        def invoke(session, **kwargs):
            if not self.config_check(session.configuration):
                raise ModelConfigurationError(['auto_review_configuration_changed'])
            try:
                call_id = self.ledger.reserve(self.task_id, 'p4',
                    input_estimate=sum(len(m['content'].encode('utf-8')) for m in kwargs['messages']),
                    output_reservation=reservation.max_tokens, call_id=reservation.model_call_id)
            except BudgetBlocked:
                self.ledger.mark_budget_exhausted(self.task_id)
                raise ModelConfigurationError(['auto_review_budget_exhausted']) from None
            self.ledger.mark_dispatched(call_id)
            try:
                response = completion(session, **kwargs)
                usage = response.usage
                self.ledger.settle(call_id,
                    input_tokens=getattr(usage, 'prompt_tokens', None),
                    output_tokens=getattr(usage, 'completion_tokens', None),
                    total_tokens=getattr(usage, 'total_tokens', None),
                    actual_model=response.model)
            except BaseException:
                if self.ledger.task_status(self.task_id) == 'running':
                    self.ledger.mark_unknown(call_id)
                raise
            if self.ledger.task_status(self.task_id) != 'running':
                raise BudgetBlocked('actual_budget_exceeded')
            return response
        return invoke
