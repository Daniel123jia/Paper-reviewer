"""Offline pre-start comparison of a frozen task payload and an API GET response.

This tool never contacts a service and never starts a task. Both inputs must be
captured independently; a nonzero exit means the task must not be started.
"""
import argparse
import hashlib
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path

FIELDS = {'document_id', 'target_journal', 'special_requirements', 'review_requirements',
          'execution_policy', 'total_budget', 'p2a_budget', 'p2b_budget',
          'p2c_budget', 'p3_budget', 'p4_budget'}
BUDGET_FIELDS = {'max_calls', 'max_input_tokens', 'max_output_tokens',
                 'max_output_tokens_per_request', 'max_duration_seconds', 'max_cost_usd',
                 'input_usd_per_million', 'output_usd_per_million'}
POLICY_FIELDS = {'max_concurrency', 'requests_per_minute', 'input_reservation_per_minute'}


def verify(candidate, saved):
    if set(candidate) != FIELDS or saved.get('status') != 'pending':
        return False
    if any(not candidate.get(k) or saved.get(k) != candidate[k] for k in
           ('document_id', 'target_journal', 'special_requirements', 'review_requirements')):
        return False
    if saved.get('pdf_sha256') != candidate['document_id']:
        return False
    policy = candidate['execution_policy']
    if not isinstance(policy, dict) or set(policy) != POLICY_FIELDS or saved.get('execution_policy') != policy:
        return False
    for key in ('total_budget', 'p2a_budget', 'p2b_budget', 'p2c_budget', 'p3_budget', 'p4_budget'):
        expected = candidate[key]
        actual = saved.get('total_budget') if key == 'total_budget' else saved.get('stage_budgets', {}).get(key[:-7])
        if not isinstance(expected, dict) or not isinstance(actual, dict) or set(expected) != BUDGET_FIELDS or set(actual) != BUDGET_FIELDS:
            return False
        try:
            if any(isinstance(expected[k], bool) or isinstance(actual[k], bool) or
                   not Decimal(str(expected[k])).is_finite() or Decimal(str(expected[k])) <= 0 or
                   Decimal(str(expected[k])) != Decimal(str(actual[k])) for k in BUDGET_FIELDS):
                return False
        except (InvalidOperation, ValueError):
            return False
    return saved.get('requested_model') == 'gpt-6.1-sol'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('candidate', type=Path)
    parser.add_argument('saved_response', type=Path)
    parser.add_argument('--expected-payload-sha256', required=True)
    args = parser.parse_args()
    try:
        payload = args.candidate.read_bytes()
        valid = (hashlib.sha256(payload).hexdigest() == args.expected_payload_sha256 and
                 verify(json.loads(payload), json.loads(args.saved_response.read_text())))
    except (OSError, ValueError, TypeError, KeyError):
        valid = False
    print('CONFIGURATION_MATCH' if valid else 'CONFIGURATION_MISMATCH_DO_NOT_START')
    return 0 if valid else 1


if __name__ == '__main__':
    raise SystemExit(main())
