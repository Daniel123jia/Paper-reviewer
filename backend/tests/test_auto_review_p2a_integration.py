import io
import sqlite3
from types import SimpleNamespace
import pytest

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.tests.model_test_config import configure_test_model
from backend.tests.test_p2a_api import _fake_completion, _pdf


def budget(**overrides):
    value = {'max_calls': 10, 'max_input_tokens': 100000, 'max_output_tokens': 5000,
             'max_output_tokens_per_request': 500, 'max_cost_usd': '1.00',
             'input_usd_per_million': '1', 'output_usd_per_million': '2'}
    return {**value, **overrides}


def setup_client(tmp_path, monkeypatch, fail_second=False, model_mismatch=False):
    configure_test_model(monkeypatch, key='fake', model='actual-model')
    calls = []
    class FakeOpenAI:
        def __init__(self, **kwargs):
            assert kwargs['max_retries'] == 0
        @property
        def chat(self):
            def create(**kwargs):
                calls.append(kwargs)
                if fail_second and len(calls) == 2:
                    def broken():
                        yield SimpleNamespace(model='actual-model', choices=[], usage=None)
                        raise ConnectionError('sensitive failure')
                    return broken()
                chunks = _fake_completion(kwargs['messages'])
                if model_mismatch:
                    for chunk in chunks:
                        chunk.model = 'other-model'
                chunks[-1].usage.total_tokens = 100
                return iter(chunks)
            return SimpleNamespace(completions=SimpleNamespace(create=create))
    monkeypatch.setattr('backend.app.OpenAI', FakeOpenAI)
    client = TestClient(create_app(tmp_path))
    doc = client.post('/api/documents', files={'file': ('paper.pdf', io.BytesIO(_pdf()), 'application/pdf')}).json()
    return client, doc, calls


def create_task(client, doc, total=None, stage=None):
    return client.post('/api/auto-review-tasks', json={'document_id': doc['id'],
        'target_journal': 'Example', 'special_requirements': 'Method',
        'total_budget': total or budget(), 'p2a_budget': stage or budget()})


def test_real_p2a_engine_with_fake_sdk_and_persistent_ledger(tmp_path, monkeypatch):
    client, doc, calls = setup_client(tmp_path, monkeypatch)
    created = create_task(client, doc)
    assert created.status_code == 201, created.text
    task = created.json(); assert task['status'] == 'pending' and calls == []
    assert client.post(f"/api/auto-review-tasks/{task['id']}/start", json={'confirm_budget': False}).status_code == 409
    assert calls == []
    started = client.post(f"/api/auto-review-tasks/{task['id']}/start", json={'confirm_budget': True})
    assert started.status_code == 202, started.text
    detail = client.get(f"/api/auto-review-tasks/{task['id']}").json()
    assert detail['status'] == 'completed', detail
    assert detail['p2a_run_id'] and detail['known_input_tokens'] == 320 and detail['known_output_tokens'] == 80
    assert detail['request_count'] == len(calls) == 4 and detail['unknown_usage_calls'] == 0
    assert detail['known_cost_usd'] == '0.00048'
    assert all(item['status'] == 'usage_valid' and item['actual_model'] == 'actual-model' for item in detail['calls'])
    run = client.get(f"/api/understanding-runs/{detail['p2a_run_id']}").json()
    assert all(run[key] for key in ('review_plan', 'paper_profile', 'claim_evidence_map'))
    assert run['document_id'] == doc['id'] and run['source_bundle_id']
    bundle = client.get(f"/api/understanding-runs/{detail['p2a_run_id']}/source-bundle").json()
    from backend.p2a_schema import ReviewPlan, PaperProfile, ClaimEvidenceMap
    from backend.p2b_engine import validate_understanding_snapshot
    validate_understanding_snapshot(bundle, ReviewPlan.model_validate(run['review_plan']),
                                    PaperProfile.model_validate(run['paper_profile']),
                                    ClaimEvidenceMap.model_validate(run['claim_evidence_map']),
                                    detail['p2a_run_id'], run['status'])
    with sqlite3.connect(tmp_path / 'review.db') as db:
        assert db.execute('select count(*) from auto_review_calls where task_id=? and status=?', (task['id'], 'usage_valid')).fetchone()[0] == 4
        assert db.execute('select p2a_run_id,status from auto_review_tasks where id=?', (task['id'],)).fetchone() == (detail['p2a_run_id'], 'completed')
    assert all(call['max_tokens'] == 500 and call['stream'] for call in calls)
    assert client.post(f"/api/auto-review-tasks/{task['id']}/start", json={'confirm_budget': True}).status_code == 202
    assert len(calls) == 4


def test_unknown_usage_stops_entire_task_and_no_third_call(tmp_path, monkeypatch):
    client, doc, calls = setup_client(tmp_path, monkeypatch, fail_second=True)
    task = create_task(client, doc).json()
    client.post(f"/api/auto-review-tasks/{task['id']}/start", json={'confirm_budget': True})
    detail = client.get(f"/api/auto-review-tasks/{task['id']}").json()
    assert detail['status'] == 'unknown_usage' and detail['unknown_usage_calls'] == 1
    assert detail['budget_remaining'] is None
    assert len(calls) == 2 and 'sensitive failure' not in str(detail)


def test_wrong_returned_model_stops_before_second_call(tmp_path, monkeypatch):
    client, doc, calls = setup_client(tmp_path, monkeypatch, model_mismatch=True)
    task = create_task(client, doc).json()
    client.post(f"/api/auto-review-tasks/{task['id']}/start", json={'confirm_budget': True})
    detail = client.get(f"/api/auto-review-tasks/{task['id']}").json()
    assert detail['status'] == 'unknown_usage' and detail['unknown_usage_calls'] == 1
    assert len(calls) == 1


def test_budget_blocks_before_sdk_dispatch(tmp_path, monkeypatch):
    client, doc, calls = setup_client(tmp_path, monkeypatch)
    task = create_task(client, doc, total=budget(max_input_tokens=1)).json()
    client.post(f"/api/auto-review-tasks/{task['id']}/start", json={'confirm_budget': True})
    detail = client.get(f"/api/auto-review-tasks/{task['id']}").json()
    assert detail['status'] == 'budget_exhausted' and not calls


def test_restart_reconciles_dispatched_request_without_replaying(tmp_path, monkeypatch):
    from backend.auto_review_budget import AutoReviewLedger
    client, doc, calls = setup_client(tmp_path, monkeypatch)
    task_id = create_task(client, doc).json()['id']
    run_id = 'interrupted-test-run'
    with sqlite3.connect(tmp_path / 'review.db') as db:
        db.execute("UPDATE auto_review_tasks SET status='running',p2a_run_id=? WHERE id=?", (run_id, task_id))
        db.execute("UPDATE auto_review_budget_tasks SET status='running' WHERE id=?", (task_id,))
        db.execute("INSERT INTO paper_understanding_runs (id,document_id,status,created_at) VALUES (?,?,'running',datetime('now'))", (run_id, doc['id']))
    ledger = AutoReviewLedger(tmp_path / 'review.db')
    call = ledger.reserve(task_id, 'p2a', input_estimate=20, output_reservation=20)
    ledger.mark_dispatched(call)
    restarted = TestClient(create_app(tmp_path))
    detail = restarted.get(f'/api/auto-review-tasks/{task_id}').json()
    assert detail['status'] == 'unknown_usage' and detail['unknown_usage_calls'] == 1
    assert detail['known_input_tokens'] == detail['known_output_tokens'] == 0
    assert restarted.get(f'/api/understanding-runs/{run_id}').json()['status'] == 'failed'
    assert restarted.post(f'/api/auto-review-tasks/{task_id}/start', json={'confirm_budget': True}).status_code == 202
    assert not calls


def test_task_routes_inherit_authentication(tmp_path, monkeypatch):
    monkeypatch.setenv('PAPER_REVIEW_ACCESS_TOKEN', 'private-test')
    client = TestClient(create_app(tmp_path))
    assert client.post('/api/auto-review-tasks', json={}).status_code == 401
    assert client.get('/api/auto-review-tasks/anything').status_code == 401
    assert client.post('/api/auto-review-tasks/anything/start', json={'confirm_budget': True}).status_code == 401


def test_config_change_after_creation_is_blocked(tmp_path, monkeypatch):
    client, doc, calls = setup_client(tmp_path, monkeypatch)
    task = create_task(client, doc).json()
    monkeypatch.setenv('OPENAI_MODEL', 'changed-model')
    response = client.post(f"/api/auto-review-tasks/{task['id']}/start", json={'confirm_budget': True})
    assert response.status_code == 409 and not calls


def test_reservation_persistence_failure_never_calls_sdk(tmp_path, monkeypatch):
    from backend.auto_review_budget import AutoReviewLedger
    client, doc, calls = setup_client(tmp_path, monkeypatch)
    task = create_task(client, doc).json()
    def failure(*args, **kwargs):
        raise sqlite3.OperationalError('sensitive database path')
    monkeypatch.setattr(AutoReviewLedger, 'reserve', failure)
    client.post(f"/api/auto-review-tasks/{task['id']}/start", json={'confirm_budget': True})
    detail = client.get(f"/api/auto-review-tasks/{task['id']}").json()
    assert detail['status'] == 'failed' and not calls
    assert 'sensitive database path' not in str(detail)


def test_restart_while_worker_active_does_not_reconcile_live_request(tmp_path, monkeypatch):
    import fcntl
    from backend.auto_review_budget import AutoReviewLedger
    client, doc, calls = setup_client(tmp_path, monkeypatch)
    task_id = create_task(client, doc).json()['id']
    with sqlite3.connect(tmp_path / 'review.db') as db:
        db.execute("UPDATE auto_review_tasks SET status='running' WHERE id=?", (task_id,))
        db.execute("UPDATE auto_review_budget_tasks SET status='running' WHERE id=?", (task_id,))
    ledger = AutoReviewLedger(tmp_path / 'review.db')
    call = ledger.reserve(task_id, 'p2a', input_estimate=20, output_reservation=20)
    ledger.mark_dispatched(call)
    with open(tmp_path / 'auto-review-worker.lock', 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        second = TestClient(create_app(tmp_path))
        detail = second.get(f'/api/auto-review-tasks/{task_id}').json()
        assert detail['status'] == 'running'
        with sqlite3.connect(tmp_path / 'review.db') as db:
            assert db.execute('select status from auto_review_calls where id=?', (call,)).fetchone()[0] == 'dispatch_pending'
    assert not calls


def test_changed_pdf_is_blocked_before_dispatch(tmp_path, monkeypatch):
    client, doc, calls = setup_client(tmp_path, monkeypatch)
    task = create_task(client, doc).json()
    (tmp_path / 'files' / f"{doc['id']}.pdf").write_bytes(b'tampered')
    response = client.post(f"/api/auto-review-tasks/{task['id']}/start", json={'confirm_budget': True})
    assert response.status_code == 409 and not calls
    assert client.get(f"/api/auto-review-tasks/{task['id']}").json()['status'] == 'pending'


@pytest.mark.parametrize('through_p3', [False, True, 'p4'])
def test_p2b_automatically_runs_through_real_engine_with_shared_budget(tmp_path, monkeypatch, through_p3):
    from backend.tests.test_p2a_api import _fake_completion
    import json
    configure_test_model(monkeypatch, key='fake', model='actual-model')
    calls = []
    class FakeOpenAI:
        def __init__(self, **kwargs):
            assert kwargs['max_retries'] == 0
        @property
        def chat(self):
            def create(**kwargs):
                calls.append(kwargs)
                if any(name in kwargs['messages'][0]['content'] for name in ('paper-type-planner/', 'paper-understanding/', 'contribution-evidence-mapper/', 'contribution-normalizer/')):
                    chunks = _fake_completion(kwargs['messages'])
                    chunks[-1].usage.total_tokens = 100
                    return iter(chunks)
                content = json.dumps({'inspection_focus': [], 'inspected_claim_ids': [],
                    'inspected_source_refs': [], 'limitations': [], 'candidates': [],
                    'no_issue_explanation': 'No concern in available evidence.'})
                return iter([SimpleNamespace(model='actual-model', choices=[SimpleNamespace(delta=SimpleNamespace(content=content))], usage=None),
                             SimpleNamespace(model='actual-model', choices=[], usage=SimpleNamespace(prompt_tokens=80, completion_tokens=20, total_tokens=100))])
            return SimpleNamespace(completions=SimpleNamespace(create=create))
    monkeypatch.setattr('backend.app.OpenAI', FakeOpenAI)
    client = TestClient(create_app(tmp_path))
    doc = client.post('/api/documents', files={'file': ('paper.pdf', io.BytesIO(_pdf()), 'application/pdf')}).json()
    payload = {'document_id': doc['id'], 'total_budget': budget(max_calls=30, max_output_tokens=20000),
               'p2a_budget': budget(), 'p2b_budget': budget()}
    if through_p3:
        payload.update({'p2c_budget': budget(), 'p3_budget': budget()})
    if through_p3 == 'p4':
        payload['p4_budget'] = budget()
    task = client.post('/api/auto-review-tasks', json=payload).json()
    assert not calls
    client.post(f"/api/auto-review-tasks/{task['id']}/start", json={'confirm_budget': True})
    detail = client.get(f"/api/auto-review-tasks/{task['id']}").json()
    assert detail['p2b_run_id'], detail
    p2b = client.get(f"/api/professional-review-runs/{detail['p2b_run_id']}").json()
    assert p2b['status'] in ('completed', 'limited') and len(p2b['dimensions']) == 6
    assert detail['request_count'] == len(calls) and all(call['max_tokens'] == 500 for call in calls)
    assert any(call['stage'] == 'p2b' for call in detail['calls'])
    if through_p3:
        assert detail['p2c_run_id'] and detail['p3_run_id'], detail
        p2c = client.get(f"/api/candidate-consolidation-runs/{detail['p2c_run_id']}").json()
        p3 = client.get(f"/api/evidence-verification-runs/{detail['p3_run_id']}").json()
        assert p2c['status'] in ('completed', 'limited') and p2c['result']
        assert p3['status'] in ('completed', 'limited') and p3['result']

    if through_p3 == 'p4':
        assert detail['status'] == 'completed', detail
        assert detail['p4_project_id'] and detail['p4_draft_id'], detail
        draft = client.get(f"/api/review-report-drafts/{detail['p4_draft_id']}").json()
        assert draft['kind'] == 'internal' and draft['language'] == 'zh'
        assert draft['approval_status'] == 'pending' and draft['body']
        exported = client.get(f"/api/review-report-drafts/{detail['p4_draft_id']}/export?format=md&mode=draft")
        assert exported.status_code == 200, exported.text
