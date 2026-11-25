"""Offline concurrency, admission and observability regression."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import threading

import pytest

from backend.auto_review_budget import AutoReviewLedger, BudgetBlocked
from backend.tests.test_auto_review_budget import limits


def test_parallel_reservations_are_accounted_and_unknown_drains_peers(tmp_path):
    ledger = AutoReviewLedger(tmp_path/'ledger.db')
    ledger.create_task('job', limits(), {'p2b': limits()}, requested_model='m')
    ledger.set_execution_policy('job', {'max_concurrency':2})
    a = ledger.reserve('job','p2b',input_estimate=20,output_reservation=20)
    b = ledger.reserve('job','p2b',input_estimate=20,output_reservation=20)
    with pytest.raises(BudgetBlocked):
        ledger.reserve('job','p2b',input_estimate=20,output_reservation=20)
    ledger.mark_dispatched(a)
    ledger.mark_dispatched(b)
    ledger.mark_unknown(a)
    ledger.settle(b,input_tokens=5,output_tokens=3,total_tokens=8,actual_model='m')
    assert ledger.task_status('job') == 'unknown_usage'
    assert ledger.summary('job')['known_input_tokens'] == 5
    with pytest.raises(BudgetBlocked):
        ledger.reserve('job','p2b',input_estimate=20,output_reservation=20)


def test_parallel_reservation_cannot_overspend_or_dispatch_after_latch(tmp_path):
    ledger = AutoReviewLedger(tmp_path/'ledger.db')
    ledger.create_task('job', limits(max_input_tokens=30), {'p2b': limits()}, requested_model='m')
    ledger.set_execution_policy('job', {'max_concurrency':2})
    a = ledger.reserve('job','p2b',input_estimate=20,output_reservation=20)
    with pytest.raises(BudgetBlocked):
        ledger.reserve('job','p2b',input_estimate=20,output_reservation=20)
    ledger.mark_dispatched(a)
    ledger.mark_unknown(a)
    with pytest.raises(BudgetBlocked):
        ledger.mark_dispatched(a)


def test_bounded_workers_overlap_and_drain_after_failure():
    from backend.auto_review_performance import bounded_map
    barrier = threading.Barrier(2)
    seen=[]
    def work(n):
        seen.append(n)
        barrier.wait(timeout=5)
        if n==0: raise ValueError('stop')
        return n
    with pytest.raises(ValueError):
        bounded_map(work, range(6), max_workers=2)
    assert sorted(seen) == [0,1]


def test_unknown_blocks_reserved_peer_dispatch(tmp_path):
    ledger=AutoReviewLedger(tmp_path/'ledger.db')
    ledger.create_task('job',limits(),{'p2b':limits()},requested_model='m')
    ledger.set_execution_policy('job',{'max_concurrency':2})
    a=ledger.reserve('job','p2b',input_estimate=10,output_reservation=20)
    b=ledger.reserve('job','p2b',input_estimate=10,output_reservation=20)
    ledger.mark_dispatched(a)
    ledger.mark_unknown(a)
    with pytest.raises(BudgetBlocked): ledger.mark_dispatched(b)
    ledger.recover_interrupted()
    assert ledger.summary('job')['request_count']==1
    assert ledger.summary('job')['reserved_not_sent_calls']==1


@pytest.mark.parametrize('failure_stage',[None,'p2b','p3','p3_write_failure'])
def test_real_http_engines_overlap_and_fail_closed(tmp_path, monkeypatch, failure_stage):
    import json
    from types import SimpleNamespace as NS
    from backend.tests.test_auto_review_full_chain import full_chain_setup
    from backend.tests.auto_review_fake_sdk import AutoReviewFakeSDK
    client,doc,payload=full_chain_setup(tmp_path,monkeypatch)
    payload['execution_policy']={'max_concurrency':2}
    original=AutoReviewFakeSDK.create
    pairs={'p2b':threading.Barrier(2),'p3':threading.Barrier(2)}
    target='p3' if failure_stage=='p3_write_failure' else failure_stage
    latched=threading.Event()
    original_block=AutoReviewLedger.block_dispatch
    def blocked(ledger,task_id):
        original_block(ledger,task_id)
        latched.set()
    monkeypatch.setattr(AutoReviewLedger,'block_dispatch',blocked)
    if failure_stage=='p3_write_failure':
        import sqlite3
        def unavailable(*_): raise sqlite3.OperationalError('private DB failure')
        monkeypatch.setattr(AutoReviewLedger,'mark_unknown',unavailable)
    lock=threading.Lock()
    counters={'p2b':0,'p3':0}
    def overlap(self,**kwargs):
        stream=original(**kwargs)
        try: data=json.loads(kwargs['messages'][1]['content'])
        except ValueError: return stream
        stage='p2b' if 'allowed_source_ids' in data else 'p3' if 'original_facts' in data else None
        if not stage: return stream
        with lock:
            index=counters[stage]
            counters[stage]+=1
        if index>=2: return stream
        def read():
            pairs[stage].wait(timeout=5)
            if stage==target and index==0:
                yield NS(model='actual-model',choices=[],usage=None)
                raise ConnectionError('secret transport detail must not be retained')
            if stage==target:
                assert latched.wait(5)
            yield from stream
        return read()
    monkeypatch.setattr(AutoReviewFakeSDK,'create',overlap)
    created=client.post('/api/auto-review-tasks',json=payload)
    assert created.status_code==201,created.text
    task_id=created.json()['id']
    assert not AutoReviewFakeSDK.calls
    client.post(f'/api/auto-review-tasks/{task_id}/start',json={'confirm_budget':True})
    result=client.get(f'/api/auto-review-tasks/{task_id}').json()
    assert result['performance']['stages']['p2b']['peak_concurrency']==2,result
    assert result['performance']['stages']['p2b']['stage_wall_ms'] >= 0
    assert 'secret transport detail' not in str(result)
    if failure_stage:
        assert result['status']=='unknown_usage',result
        assert counters[target]==2  # no next wave / next check after latch
        failed=[c for c in result['calls'] if c['stage']==target]
        assert len(failed)==2
        assert {c['status'] for c in failed}=={'usage_valid','dispatch_pending' if failure_stage=='p3_write_failure' else 'unknown_usage'}
        assert not result['p4_draft_id']
        count=len(AutoReviewFakeSDK.calls)
        client.post(f'/api/auto-review-tasks/{task_id}/start',json={'confirm_budget':True})
        assert len(AutoReviewFakeSDK.calls)==count
    else:
        assert result['status']=='completed',result
        assert result['performance']['stages']['p3']['peak_concurrency']==2
        assert result['unknown_usage_calls']==0
        assert all(c['duration_ms'] is not None and c['duration_ms']>=0 for c in result['calls'])
        assert len(client.get(f"/api/professional-review-runs/{result['p2b_run_id']}").json()['result']['dimensions'])==6
        report=client.get(f"/api/review-report-drafts/{result['p4_draft_id']}").json()
        assert len(report['writing']['comments'])==2
        assert client.get(f"/api/review-report-drafts/{result['p4_draft_id']}/export?format=md&mode=draft").status_code==200


def test_concurrent_engine_budget_counts_inflight_and_wall_clock():
    from backend.auto_review_performance import ConcurrentRunBudget
    from backend.p2a_engine import BudgetExceeded
    budget=ConcurrentRunBudget(max_calls=2,max_input_tokens=100,max_output_tokens=5000)
    entered,release=threading.Event(),threading.Event()
    def waiting(*_):
        entered.set()
        assert release.wait(5)
        return {'input_tokens':5,'output_tokens':3,'model_identifier':'m'}
    with ThreadPoolExecutor(max_workers=2) as pool:
        future=pool.submit(budget.call,'a','a','b',waiting)
        assert entered.wait(5)
        try:
            with pytest.raises(BudgetExceeded): budget.call('b','a','b',waiting)
        finally: release.set()
        future.result()
    assert budget.calls==1 and budget.reserved_input_tokens==5


def test_policy_and_rate_fail_closed_without_sdk(tmp_path):
    from pydantic import ValidationError
    from backend.auto_review_performance import ExecutionPolicy
    with pytest.raises(ValidationError): ExecutionPolicy(max_concurrency=3)
    ledger=AutoReviewLedger(tmp_path/'ledger.db')
    ledger.create_task('job',limits(),{'p2b':limits()},requested_model='m')
    ledger.set_execution_policy('job',{'max_concurrency':2,'input_reservation_per_minute':5})
    a=ledger.reserve('job','p2b',input_estimate=10,output_reservation=20)
    with pytest.raises(BudgetBlocked,match='local_rate'): ledger.mark_dispatched(a)
    assert ledger.summary('job')['request_count']==0


def test_concurrent_sql_reservations_respect_capacity(tmp_path):
    ledger=AutoReviewLedger(tmp_path/'ledger.db')
    ledger.create_task('job',limits(),{'p2b':limits()},requested_model='m')
    ledger.set_execution_policy('job',{'max_concurrency':2})
    barrier=threading.Barrier(8)
    def reserve(_):
        barrier.wait(timeout=5)
        try: return ledger.reserve('job','p2b',input_estimate=10,output_reservation=20)
        except BudgetBlocked: return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        admitted=[r for r in pool.map(reserve,range(8)) if r]
    assert len(admitted)==2
    ledger.recover_interrupted()
    assert ledger.task_status('job')=='interrupted'
    for call in admitted:
        with pytest.raises(BudgetBlocked): ledger.mark_dispatched(call)


def test_rate_wait_rechecks_unknown_before_dispatch(tmp_path,monkeypatch):
    ledger=AutoReviewLedger(tmp_path/'ledger.db')
    ledger.create_task('job',limits(),{'p2b':limits()},requested_model='m')
    ledger.set_execution_policy('job',{'max_concurrency':2,'requests_per_minute':1})
    a=ledger.reserve('job','p2b',input_estimate=10,output_reservation=20)
    b=ledger.reserve('job','p2b',input_estimate=10,output_reservation=20)
    ledger.mark_dispatched(a)
    monkeypatch.setattr('backend.auto_review_budget.time.sleep',lambda _:ledger.mark_unknown(a))
    with pytest.raises(BudgetBlocked): ledger.mark_dispatched(b)
    assert ledger.summary('job')['request_count']==1


def test_compact_profile_transport_preserves_all_fields_and_text():
    import json
    from backend.p2a_engine import _content_json
    # Compare decoded payloads directly; only JSON separator whitespace may change.
    from types import SimpleNamespace
    payload={'text':'A scientific statement with spaces and 1.25','facts':[{'source_id':'ru_1','value':'中文 数据'}]}
    result=SimpleNamespace(model_dump=lambda **_:payload)
    encoded=_content_json(result)
    assert json.loads(encoded)==payload
    assert len(encoded.encode())<len(json.dumps(payload,ensure_ascii=False).encode())


def test_parallel_p2a_keeps_every_source_unit_and_checkpoint(tmp_path,monkeypatch):
    import pymupdf
    import time
    from backend.tests.test_auto_review_full_chain import full_chain_setup
    from backend.tests.auto_review_fake_sdk import AutoReviewFakeSDK
    client,_,payload=full_chain_setup(tmp_path,monkeypatch)
    pdf=pymupdf.open()
    for index in range(4):
        page=pdf.new_page()
        text=' '.join(f'Paragraph {i} reports scientific method evaluation and comparison of experimental results.' for i in range(60))
        page.insert_textbox((40,40,570,800),text,fontsize=8)
    doc=client.post('/api/documents',files={'file':('parallel.pdf',pdf.tobytes(),'application/pdf')}).json()
    payload['document_id']=doc['id']
    payload['execution_policy']={'max_concurrency':2}
    # Stop after P2-A using the actual single-stage product contract.
    for s in ('p2b','p2c','p3','p4'): payload.pop(s+'_budget')
    original=AutoReviewFakeSDK.create
    def delayed(self,**kwargs):
        stream=original(**kwargs)
        def read():
            time.sleep(.01)
            yield from stream
        return read()
    monkeypatch.setattr(AutoReviewFakeSDK,'create',delayed)
    task=client.post('/api/auto-review-tasks',json=payload).json()
    client.post(f"/api/auto-review-tasks/{task['id']}/start",json={'confirm_budget':True})
    result=client.get(f"/api/auto-review-tasks/{task['id']}").json()
    assert result['status']=='completed',result
    assert result['performance']['stages']['p2a']['peak_concurrency']==2
    run=client.get(result['p2a_result_url']).json()
    for name in ('paper_profile','claim_evidence_map'):
        coverage=run[name]['input_coverage']
        assert coverage['included_unit_count']==coverage['total_unit_count']
        assert not coverage['excluded_unit_ids']


def test_local_latch_survives_failed_unknown_persistence(tmp_path):
    ledger=AutoReviewLedger(tmp_path/'ledger.db')
    ledger.create_task('job',limits(),{'p3':limits()},requested_model='m')
    ledger.set_execution_policy('job',{'max_concurrency':2})
    a=ledger.reserve('job','p3',input_estimate=10,output_reservation=20)
    ledger.mark_dispatched(a)
    # Even if persisting unknown fails, a peer must not fill the free slot.
    ledger.block_dispatch('job')
    with pytest.raises(BudgetBlocked): ledger.reserve('job','p3',input_estimate=10,output_reservation=20)
    ledger.recover_interrupted()
    assert ledger.task_status('job')=='unknown_usage'


def test_stage_clock_is_persisted_and_failure_finishes_without_replay(tmp_path):
    ledger=AutoReviewLedger(tmp_path/'ledger.db')
    ledger.create_task('job',limits(),{'p2a':limits()},requested_model='m')
    with pytest.raises(ValueError):
        with ledger.measure_stage('job','p2a'):
            raise ValueError('offline failure')
    stage=AutoReviewLedger(ledger.path).performance('job')['stages']['p2a']
    assert stage['stage_finished_at'] >= stage['stage_started_at']
    assert stage['stage_wall_ms'] >= 0
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        with ledger.measure_stage('job','p2a'):
            pytest.fail('must not replay')
