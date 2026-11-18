from copy import deepcopy
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.tests.test_auto_review_p2a_integration import setup_client
from scripts.auto_review.verify_saved_configuration import verify


def _budget(calls, inputs, outputs, per, seconds, cost):
    return dict(max_calls=calls, max_input_tokens=inputs, max_output_tokens=outputs,
                max_output_tokens_per_request=per, max_duration_seconds=seconds,
                max_cost_usd=cost, input_usd_per_million='0.80', output_usd_per_million='4.00')


def _payload(document_id):
    return dict(document_id=document_id, target_journal='Venue',
        special_requirements='Distinct P2-A instruction', review_requirements='Distinct P2-B instruction',
        execution_policy=dict(max_concurrency=1, requests_per_minute=10, input_reservation_per_minute=500000),
        total_budget=_budget(78,1170000,206000,6000,3600,'1.85'),
        p2a_budget=_budget(24,280000,66000,6000,1800,'0.50'),
        p2b_budget=_budget(10,300000,32000,6000,900,'0.40'),
        p2c_budget=_budget(4,90000,16000,6000,600,'0.14'),
        p3_budget=_budget(24,300000,60000,6000,900,'0.50'),
        p4_budget=_budget(16,200000,32000,2000,600,'0.30'))


def test_frozen_config_persists_and_mismatches_block_preflight(tmp_path, monkeypatch):
    client, doc, calls = setup_client(tmp_path, monkeypatch)
    payload = _payload(doc['id'])
    bad = deepcopy(payload); bad['unexpected'] = 'forbidden'
    assert client.post('/api/auto-review-tasks', json=bad).status_code == 422
    bad = deepcopy(payload); bad['p2a_budget']['extra'] = 1
    assert client.post('/api/auto-review-tasks', json=bad).status_code == 422
    task = client.post('/api/auto-review-tasks', json=payload)
    assert task.status_code == 201, task.text
    task_id = task.json()['id']
    saved = client.get(f'/api/auto-review-tasks/{task_id}').json()
    # The fake test model is expected only in this isolated test.
    saved['requested_model'] = 'gpt-6.1-sol'
    assert verify(payload, saved)
    for mutate in ('missing', 'requirements', 'policy', 'budget', 'unknown_field'):
        c, s = deepcopy(payload), deepcopy(saved)
        if mutate == 'missing': del s['special_requirements']
        elif mutate == 'requirements': s['review_requirements'] = 'changed'
        elif mutate == 'policy': s['execution_policy']['max_concurrency'] = 2
        elif mutate == 'budget': s['stage_budgets']['p3']['max_calls'] += 1
        else: c['unknown'] = 1
        assert not verify(c, s)
    assert client.post(f'/api/auto-review-tasks/{task_id}/start', json={'confirm_budget':False}).status_code == 409
    restarted = TestClient(create_app(tmp_path)).get(f'/api/auto-review-tasks/{task_id}').json()
    restarted['requested_model'] = 'gpt-6.1-sol'
    assert verify(payload, restarted) and calls == []
