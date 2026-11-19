import errno
import json
from types import SimpleNamespace

import httpx
import openai
import pytest

from backend.tests.test_auto_review_p2a_integration import setup_client, create_task


@pytest.mark.parametrize('failure,phase,chunks', [
    ('create', 'sdk_request', 0),
    ('no_first', 'await_first_chunk', 0),
    ('partial', 'stream_read', 1),
    ('missing', 'usage_validation', 1),
    ('audit_write', None, 0),
])
def test_failed_sdk_call_cannot_dispatch_again(tmp_path, monkeypatch, failure, phase, chunks):
    client, doc, _ = setup_client(tmp_path, monkeypatch)
    attempts = []
    secret = 'private-body-and-credential'

    def create(**params):
        attempts.append(1)
        if failure in ('create', 'audit_write'):
            try:
                raise ConnectionRefusedError(errno.ECONNREFUSED, secret)
            except ConnectionRefusedError as exc:
                raise openai.APIConnectionError(request=httpx.Request('POST', 'https://offline.invalid/' + secret)) from exc

        def stream():
            if failure != 'no_first':
                yield SimpleNamespace(model='actual-model', choices=[], usage=None)
            if failure != 'missing':
                raise httpx.ReadError(secret)
        return stream()

    def fake(**kwargs):
        assert kwargs['max_retries'] == 0
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    monkeypatch.setattr('backend.app.OpenAI', fake)
    if failure == 'audit_write':
        def broken(*args):
            raise RuntimeError(secret)
        monkeypatch.setattr('backend.auto_review_budget.AutoReviewLedger.save_diagnostic', broken)
    job = create_task(client, doc).json()['id']
    url = f'/api/auto-review-tasks/{job}'
    client.post(url + '/start', json={'confirm_budget': True})
    task = client.get(url).json()
    call, = task['calls']
    assert call['status'] == task['status'] == 'unknown_usage'
    assert call['input_tokens'] is None and secret not in json.dumps(task)
    assert len(attempts) == 1
    diagnostic = call['diagnostic']
    if failure == 'audit_write':
        assert diagnostic is None
        return
    assert diagnostic['phase'] == phase
    assert call['dispatched_at'] <= diagnostic['failure_at']
    assert diagnostic['request_id'] == 'unknown'
    assert diagnostic['stream_established'] == (failure != 'create')
    assert diagnostic['chunk_count'] == chunks
    assert (diagnostic['first_chunk_ms'] is not None) == bool(chunks)
    assert not diagnostic['valid_usage_received']
    chain = diagnostic['error_chain']
    if failure == 'create':
        assert chain[0]['type'] == 'APIConnectionError'
        assert chain[1] == dict(type='ConnectionRefusedError', code='ECONNREFUSED', relation='cause', parent=0)
        assert diagnostic['network_phase'] == 'connect'
    elif failure != 'missing':
        assert len(chain) == 1 and chain[0]['type'] == 'ReadError'


@pytest.mark.parametrize('header,expected', [
    ('req_12345678', 'req_12345678'),
    ('req_secret/path?token=private', 'unknown'),
])
def test_status_error_request_id_and_context_are_sanitized(header, expected):
    from unittest.mock import Mock
    from backend.auto_review_diagnostics import CallObservation

    request = httpx.Request('POST', 'https://offline.invalid/private')
    response = httpx.Response(429, request=request, headers={'x-request-id': header})
    error = openai.RateLimitError('private', response=response, body='private')
    error.__context__ = httpx.ReadTimeout('private')
    ledger = Mock()
    CallObservation(streaming=False).persist(ledger, 'local-id', error, 123)
    _, diag = ledger.save_diagnostic.call_args.args
    assert diag['request_id'] == expected and diag['http_status'] == 429
    assert diag['error_chain'][1]['relation'] == 'context'
    assert 'private' not in json.dumps(diag)
