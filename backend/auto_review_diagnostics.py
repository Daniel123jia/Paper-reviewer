"""Best-effort observations of one ordinary review SDK call. No message text."""
import errno
import re
import socket
import sqlite3
import ssl
import time

import httpcore
import httpx
import openai


# Use literal known class identities; user-controlled class names and messages
# must not become audit strings.
_ERRORS = (
    openai.APITimeoutError, openai.APIConnectionError, openai.RateLimitError, openai.AuthenticationError,
    openai.PermissionDeniedError, openai.BadRequestError, openai.InternalServerError,
    openai.APIStatusError,
    httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout, httpx.PoolTimeout,
    httpx.ConnectError, httpx.ReadError, httpx.WriteError, httpx.ProxyError,
    httpx.RemoteProtocolError, httpx.LocalProtocolError,
    httpcore.ConnectTimeout, httpcore.ReadTimeout, httpcore.ConnectError,
    httpcore.ReadError, httpcore.RemoteProtocolError, httpcore.ProxyError,
    ssl.SSLCertVerificationError, ssl.SSLError, socket.gaierror,
    ConnectionResetError, ConnectionRefusedError, BrokenPipeError, TimeoutError,
    ConnectionError, OSError, sqlite3.Error,
)


def _error_chain(exc):
    nodes, seen = [], set()

    def visit(error, parent, relation):
        if error is None or id(error) in seen or len(nodes) >= 8:
            return
        seen.add(id(error))
        name = next((cls.__name__ for cls in _ERRORS if isinstance(error, cls)), 'OtherError')
        code = None
        if isinstance(error, OSError) and type(error.errno) is int:
            if isinstance(error, socket.gaierror):
                code = next((k for k in dir(socket) if k.startswith('EAI_')
                             and getattr(socket, k) == error.errno), None)
            elif not isinstance(error, ssl.SSLError):
                code = errno.errorcode.get(error.errno)
        idx = len(nodes)
        nodes.append(dict(type=name, code=code, parent=parent, relation=relation))
        visit(error.__cause__, idx, 'cause')
        visit(error.__context__, idx, 'context')

    visit(exc, None, 'raised')
    return nodes


class CallObservation:
    def __init__(self, streaming):
        self.started = time.monotonic()
        self.phase = 'sdk_request'
        self.stream_established = False if streaming else None
        self.chunks = 0
        self.first_ms = None
        self.request_id = None
        self.valid_usage = False

    def observe_id(self, response, *, streaming=False):
        try:
            value = (response.response.headers.get('x-request-id') if streaming
                     else getattr(response, '_request_id', None))
            self._set_id(value)
        except Exception:
            pass

    def _set_id(self, value):
        if type(value) is str and re.fullmatch(r'req_[a-zA-Z0-9]{8,64}', value):
            self.request_id = value

    def chunk(self):
        self.chunks += 1
        if self.chunks == 1:
            self.first_ms = round((time.monotonic() - self.started) * 1000)
        self.phase = 'chunk_processing'

    def observe_usage(self, usage):
        try:
            inp, out, total = (getattr(usage, field, None) for field in
                               ('prompt_tokens', 'completion_tokens', 'total_tokens'))
            self.valid_usage = (all(type(x) is int and x >= 0 for x in (inp, out, total))
                                and inp + out == total)
        except Exception:
            # Settlement still independently validates the original SDK object.
            pass

    def persist(self, ledger, call_id, error=None, failure_at=None):
        # Only called after settlement or after the normal failure latch. Audit
        # failures must never change the original exception or dispatch decision.
        try:
            status = None
            if isinstance(error, openai.APIStatusError):
                self._set_id(error.request_id)
                if type(error.status_code) is int and 100 <= error.status_code <= 599:
                    status = error.status_code
            chain = _error_chain(error)
            # Only report a lower-level phase if a typed exception identifies it.
            network_phase = 'unknown'
            for node in chain:
                if node['type'] == 'gaierror':
                    network_phase = 'dns'
                    break
                if node['type'] in ('SSLError', 'SSLCertVerificationError'):
                    network_phase = 'tls'
                    break
                if node['type'] in ('ConnectTimeout', 'ConnectionRefusedError', 'ConnectError'):
                    network_phase = 'connect'
            ledger.save_diagnostic(call_id, dict(
                phase=self.phase, failure_at=failure_at,
                error_chain=chain, network_phase=network_phase,
                request_id=self.request_id or 'unknown', http_status=status,
                stream_established=self.stream_established, chunk_count=self.chunks,
                first_chunk_ms=self.first_ms, valid_usage_received=self.valid_usage))
        except Exception:
            pass
