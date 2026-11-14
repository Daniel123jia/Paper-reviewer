import io
import sqlite3

from fastapi.testclient import TestClient
from backend.app import create_app
from backend.tests.model_test_config import configure_test_model
from backend.tests.test_p2a_api import _pdf
from backend.tests.test_auto_review_p2a_integration import budget
from backend.tests.auto_review_fake_sdk import AutoReviewFakeSDK


import pytest

@pytest.mark.parametrize('interrupt_at', [None,5,12,19])
def test_nonempty_real_business_chain_through_sdk_only(tmp_path, monkeypatch,interrupt_at):
    configure_test_model(monkeypatch, key='fake', model='actual-model')
    AutoReviewFakeSDK.calls = []
    monkeypatch.setattr(AutoReviewFakeSDK,'fail_at',interrupt_at)
    monkeypatch.setattr('backend.app.OpenAI', AutoReviewFakeSDK)
    client = TestClient(create_app(tmp_path))
    document = client.post('/api/documents', files={'file':('paper.pdf',io.BytesIO(_pdf()),'application/pdf')}).json()
    stage = budget(max_calls=30, max_input_tokens=500000, max_output_tokens=30000, max_output_tokens_per_request=2000)
    payload = {'document_id':document['id'], 'total_budget':budget(max_calls=90,max_input_tokens=2000000,max_output_tokens=180000,max_output_tokens_per_request=2000),
               **{key+'_budget':stage for key in ('p2a','p2b','p2c','p3','p4')}}
    response = client.post('/api/auto-review-tasks',json=payload)
    assert response.status_code == 201, response.text
    task_id = response.json()['id']
    assert not AutoReviewFakeSDK.calls
    client.post(f'/api/auto-review-tasks/{task_id}/start',json={'confirm_budget':True})
    task = client.get(f'/api/auto-review-tasks/{task_id}').json()
    if interrupt_at:
        assert task['status'] == 'unknown_usage', task
        assert task['request_count'] == len(AutoReviewFakeSDK.calls) == interrupt_at
        assert task['unknown_usage_calls'] == 1
        assert not task['p4_draft_id']
        assert 'private test error' not in str(task)
        assert client.post(f'/api/auto-review-tasks/{task_id}/start',json={'confirm_budget':True}).json()['p2a_run_id'] == task['p2a_run_id']
        assert len(AutoReviewFakeSDK.calls) == interrupt_at
        return
    assert task['status'] == 'completed', task
    assert all(task[key] for key in ('p2a_run_id','p2b_run_id','p2c_run_id','p3_run_id','p4_project_id','p4_draft_id'))
    assert task['request_count'] == len(AutoReviewFakeSDK.calls)
    assert {c['stage'] for c in task['calls']} >= {'p2a','p2b','p2c','p3','p4'}
    draft = client.get(f"/api/review-report-drafts/{task['p4_draft_id']}").json()
    assert draft['writing']['comments'] and '内部' in draft['body']
    assert draft['approval_status'] == 'pending'
    with sqlite3.connect(tmp_path/'review.db') as db:
        assert db.execute('SELECT count(*) FROM p4_model_calls').fetchone()[0] == 4
        assert db.execute("SELECT count(*) FROM auto_review_calls WHERE stage='p4' AND status='usage_valid'").fetchone()[0] == 4
    export = client.get(f"/api/review-report-drafts/{task['p4_draft_id']}/export?format=md&mode=draft")
    assert export.status_code == 200 and 'DRAFT' in export.text


def test_deadline_budget_is_explicit_and_persisted(tmp_path):
    from decimal import Decimal
    from backend.auto_review_budget import AutoReviewBudget, AutoReviewLedger, BudgetBlocked
    import time
    import pytest
    values = budget(max_duration_seconds=1)
    for k in ('max_cost_usd','input_usd_per_million','output_usd_per_million'):
        values[k] = Decimal(values[k])
    configured = AutoReviewBudget(**values)
    ledger = AutoReviewLedger(tmp_path/'deadline.db')
    ledger.create_task('job',configured,{'p2a':configured},requested_model='actual-model')
    with ledger.connect() as db:
        db.execute('UPDATE auto_review_budget_tasks SET started_at=? WHERE id=?',(time.time()-5,'job'))
    with pytest.raises(BudgetBlocked):
        ledger.reserve('job','p2a',input_estimate=1,output_reservation=1)
    assert ledger.summary('job')['request_count'] == 0


def test_retry_start_returns_original_task_without_new_calls(tmp_path, monkeypatch):
    from backend.tests.test_auto_review_p2a_integration import setup_client,create_task
    client,doc,calls = setup_client(tmp_path,monkeypatch)
    task = create_task(client,doc).json()
    first=client.post(f"/api/auto-review-tasks/{task['id']}/start",json={'confirm_budget':True})
    count=len(calls)
    retry=client.post(f"/api/auto-review-tasks/{task['id']}/start",json={'confirm_budget':True})
    assert retry.status_code==202, retry.text
    assert retry.json()['p2a_run_id']==first.json()['p2a_run_id']
    assert len(calls)==count


def full_chain_setup(tmp_path,monkeypatch):
    configure_test_model(monkeypatch,key='fake',model='actual-model')
    AutoReviewFakeSDK.calls=[]
    monkeypatch.setattr(AutoReviewFakeSDK,'fail_at',None)
    monkeypatch.setattr('backend.app.OpenAI',AutoReviewFakeSDK)
    client=TestClient(create_app(tmp_path))
    doc=client.post('/api/documents',files={'file':('test.pdf',_pdf(),'application/pdf')}).json()
    stage=budget(max_calls=30,max_input_tokens=500000,max_output_tokens=30000,max_output_tokens_per_request=2000)
    payload={'document_id':doc['id'],'total_budget':budget(max_calls=100,max_input_tokens=2000000,max_output_tokens=180000,max_output_tokens_per_request=2000),**{s+'_budget':stage for s in ('p2a','p2b','p2c','p3','p4')}}
    return client,doc,payload


def test_p4_ledger_mismatch_stops_before_next_request(tmp_path,monkeypatch):
    from backend.p4_budget import P4BudgetStore
    client,doc,payload=full_chain_setup(tmp_path,monkeypatch)
    original=P4BudgetStore.settle_valid_usage
    def damaged(store,*args,**kwargs):
        original(store,*args,**kwargs)
        with sqlite3.connect(tmp_path/'review.db') as db:
            db.execute("UPDATE p4_model_call_budget SET usage_cost_usd='99'")
    monkeypatch.setattr(P4BudgetStore,'settle_valid_usage',damaged)
    task=client.post('/api/auto-review-tasks',json=payload).json()
    client.post(f"/api/auto-review-tasks/{task['id']}/start",json={'confirm_budget':True})
    result=client.get(f"/api/auto-review-tasks/{task['id']}").json()
    assert result['status']=='failed' and not result['p4_draft_id']
    assert len([c for c in result['calls'] if c['stage']=='p4'])==1
    assert len(AutoReviewFakeSDK.calls)==result['request_count']


def test_changed_pdf_between_stages_blocks_before_next_sdk_request(tmp_path,monkeypatch):
    client,doc,payload=full_chain_setup(tmp_path,monkeypatch)
    original=AutoReviewFakeSDK.create
    def mutate(self,**kwargs):
        result=original(**kwargs)
        if len(AutoReviewFakeSDK.calls)==4:
            (tmp_path/'files'/f"{doc['id']}.pdf").write_bytes(b'changed-source')
        return result
    monkeypatch.setattr(AutoReviewFakeSDK,'create',mutate)
    task=client.post('/api/auto-review-tasks',json=payload).json()
    client.post(f"/api/auto-review-tasks/{task['id']}/start",json={'confirm_budget':True})
    result=client.get(f"/api/auto-review-tasks/{task['id']}").json()
    assert result['status'] not in ('completed','running')
    assert len(AutoReviewFakeSDK.calls)==4 and not result['p2c_run_id']


def test_stage_requirements_are_independent_and_audited(tmp_path,monkeypatch):
    import json
    client,doc,payload=full_chain_setup(tmp_path,monkeypatch)
    payload.update({'special_requirements':'P2-A only: do not generate review issues',
                    'review_requirements':'Evaluate experimental controls'})
    task=client.post('/api/auto-review-tasks',json=payload).json()
    client.post(f"/api/auto-review-tasks/{task['id']}/start",json={'confirm_budget':True})
    result=client.get(f"/api/auto-review-tasks/{task['id']}").json()
    assert result['status']=='completed',result
    dimensions=[c for c in AutoReviewFakeSDK.calls if 'allowed_source_ids' in c['messages'][1]['content']]
    assert len(dimensions)==6
    for request in dimensions:
        assert 'P2-A only' not in str(request['messages'])
        assert json.loads(request['messages'][1]['content'])['review_requirements']=='Evaluate experimental controls'
    assert len(result['artifacts'])==5


def test_second_app_does_not_interrupt_active_p4_request(tmp_path,monkeypatch):
    client,doc,payload=full_chain_setup(tmp_path,monkeypatch)
    original=AutoReviewFakeSDK.create
    reopened=[]
    def during_request(self,**kwargs):
        if not kwargs.get('stream') and not reopened:
            reopened.append(create_app(tmp_path))
        return original(**kwargs)
    monkeypatch.setattr(AutoReviewFakeSDK,'create',during_request)
    task=client.post('/api/auto-review-tasks',json=payload).json()
    client.post(f"/api/auto-review-tasks/{task['id']}/start",json={'confirm_budget':True})
    result=client.get(f"/api/auto-review-tasks/{task['id']}").json()
    assert reopened and result['status']=='completed',result


def test_concurrent_start_returns_one_paid_run(tmp_path,monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from backend.tests.test_auto_review_p2a_integration import setup_client,create_task
    client,doc,calls=setup_client(tmp_path,monkeypatch)
    task=create_task(client,doc).json()
    original=client.app
    # Synchronize at SDK dispatch, after the task/run transaction committed.
    from backend.tests.test_p2a_api import _fake_completion
    from types import SimpleNamespace as NS
    entered,release=threading.Event(),threading.Event()
    class PausedSDK:
        def __init__(self,**kwargs):
            assert kwargs['max_retries']==0
            self.chat=NS(completions=NS(create=self.create))
        def create(self,**kwargs):
            calls.append(kwargs)
            if len(calls)==1:
                entered.set()
                assert release.wait(10)
            chunks=_fake_completion(kwargs['messages'])
            chunks[-1].usage.total_tokens=100
            return iter(chunks)
    monkeypatch.setattr('backend.app.OpenAI',PausedSDK)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first=pool.submit(client.post,f"/api/auto-review-tasks/{task['id']}/start",json={'confirm_budget':True})
        assert entered.wait(10)
        try:
            retry=client.post(f"/api/auto-review-tasks/{task['id']}/start",json={'confirm_budget':True})
            assert retry.status_code==202
        finally:
            release.set()
        assert first.result().json()['p2a_run_id']==retry.json()['p2a_run_id']
    assert len(calls)==4
    assert TestClient(original).get(f"/api/auto-review-tasks/{task['id']}").json()['status']=='completed'


def test_settlement_db_failure_preserves_unknown_and_blocks_next_sdk(tmp_path,monkeypatch):
    from backend.auto_review_budget import AutoReviewLedger
    from backend.tests.test_auto_review_p2a_integration import setup_client,create_task
    client,doc,calls=setup_client(tmp_path,monkeypatch)
    task=create_task(client,doc).json()
    def unavailable(*args,**kwargs):
        raise sqlite3.OperationalError('private db detail')
    monkeypatch.setattr(AutoReviewLedger,'settle',unavailable)
    client.post(f"/api/auto-review-tasks/{task['id']}/start",json={'confirm_budget':True})
    result=client.get(f"/api/auto-review-tasks/{task['id']}").json()
    assert len(calls)==1 and result['status']=='unknown_usage'
    assert result['unknown_usage_calls']==1 and 'private db detail' not in str(result)
