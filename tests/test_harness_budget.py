"""Budget diagnostics through the real SDK, without a paid provider call."""
import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from backend import chat, harness_runtime
from backend.agent_policy import Budget, BudgetExceeded
from backend.provider_errors import provider_failure
from backend.schemas import ChatRequest
from conftest import REAL_RUN


@pytest.mark.parametrize('stop_reason', ['end_turn', 'max_tokens', 'long_answer'])
def test_first_ui_question_with_real_harness(monkeypatch, stop_reason):
    requests = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(payload)
            final_reason = ('end_turn' if payload['max_tokens'] >= 12000 else 'max_tokens') if stop_reason == 'long_answer' else stop_reason
            called = any(any(b.get('type') == 'tool_result' for b in item['content'])
                         for item in payload['messages'] if isinstance(item['content'], list))
            block = {'type': 'text', 'text': ''} if called else {
                'type': 'tool_use', 'id': 'search1', 'name': 'mcp__buck__search_knowledge', 'input': {}}
            delta = {'type': 'text_delta', 'text': '界面功能说明 [kb:ui-overview]'} if called else {
                'type': 'input_json_delta', 'partial_json': json.dumps({'query': '前端工作台总览', 'top_k': 4})}
            frames = [
                {'type': 'message_start', 'message': {'id': 'm1', 'type': 'message', 'role': 'assistant',
                    'content': [], 'model': 'deepseek-flash', 'stop_reason': None, 'stop_sequence': None,
                    'usage': {'input_tokens': 10, 'output_tokens': 0}}},
                {'type': 'content_block_start', 'index': 0, 'content_block': block},
                {'type': 'content_block_delta', 'index': 0, 'delta': delta},
                {'type': 'content_block_stop', 'index': 0},
                {'type': 'message_delta', 'delta': {'stop_reason': final_reason if called else 'tool_use',
                    'stop_sequence': None}, 'usage': {'input_tokens':100, 'cache_read_input_tokens':5000,
                        'cache_creation_input_tokens':0, 'output_tokens':12000 if called and stop_reason == 'long_answer' else 5}},
                {'type': 'message_stop'},
            ]
            raw = ''.join('event: ' + f['type'] + '\ndata: ' + json.dumps(f) + '\n\n' for f in frames).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

    for name in ('AGENT_TIME_LIMIT_S', 'AGENT_COST_LIMIT_USD', 'AGENT_INPUT_USD_PER_M', 'AGENT_CACHED_INPUT_USD_PER_M', 'AGENT_OUTPUT_USD_PER_M', 'AGENT_MAX_OUTPUT_TOKENS'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'local-mock-only')
    monkeypatch.setattr(harness_runtime, 'run', REAL_RUN)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv('HARNESS_BASE_URL', f'http://127.0.0.1:{server.server_port}')

    async def no_execution(*_):
        raise AssertionError('UI explanation must not run numerical work')

    try:
        result = asyncio.run(chat.answer(ChatRequest(message='告诉我前端的各个按键有什么作用', reasoning_mode='auto'), no_execution))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    if stop_reason == 'max_tokens':
        assert result['mode'] == 'provider_error', result
        assert result['error_code'] == 'output_token_limit', result
        assert result['finish_reason'] == 'max-tokens'
    else:
        assert result['mode'] == 'deepseek', result
        assert any(d['id'] == 'ui-overview' for d in result['sources'])
    assert len(requests) == 2
    assert result['agent_budget']['reserved_cost_usd'] < .5
    assert result['agent_budget']['unsettled_model_calls'] == 0
    print('First UI question budget:', result['agent_budget'])


@pytest.mark.parametrize('status', [400, 401, 429, 500])
def test_provider_retries_preserve_original_error_and_do_not_spend_again(monkeypatch, status):
    requests = []
    real_client = httpx.Client

    def reject(request):
        requests.append(request)
        return httpx.Response(status, text='private upstream error')

    monkeypatch.setenv('DEEPSEEK_API_KEY', 'local-test-secret')
    monkeypatch.setenv('AGENT_COST_LIMIT_USD', '0.10')
    budget = Budget(True)
    with real_client(trust_env=False) as client:
        monkeypatch.setattr(harness_runtime.httpx, 'Client',
                            lambda **kwargs: real_client(transport=httpx.MockTransport(reject), **kwargs))
        with harness_runtime.Gateway([], None, budget, lambda *_: None) as gateway:
            for _ in range(8):
                response = client.post(gateway.url + '/v1/messages',
                                       headers={'Authorization': 'Bearer ' + gateway.secret},
                                       json={'messages': [], 'max_tokens': 8000})
                assert response.status_code == status
                assert 'private upstream error' not in response.text
                assert 'local-test-secret' not in response.text
            assert isinstance(gateway.error, httpx.HTTPStatusError)
            tool = client.post(gateway.url + '/mcp',
                               headers={'Authorization': 'Bearer ' + gateway.secret},
                               json={'id': 1, 'method': 'tools/call', 'params': {'name': 'probe'}})
            assert tool.json()['result']['isError']
            assert 'no further tools' in tool.text
            failure = provider_failure(gateway.error, agent_budget=budget.metadata())
    assert failure['error_code'] == 'provider_failed'
    assert f'HTTP {status}' in failure['provider_error']
    assert budget.calls == len(requests) == 1


@pytest.mark.parametrize('expired', [False, True])
def test_sdk_timeout_is_not_mislabeled_as_total_budget(monkeypatch, expired):
    import deepseek_harness
    budget = Budget(False)
    closed = []

    class Runtime:
        def __init__(self, **_):
            pass

        def start(self):
            if expired:
                budget.started -= budget.seconds + 1
            raise TimeoutError('private SDK timeout diagnostics')

        def close(self):
            closed.append(True)

    monkeypatch.setattr(deepseek_harness, 'DeepSeekHarness', Runtime)
    expected = BudgetExceeded if expired else TimeoutError
    with pytest.raises(expected) as failure:
        asyncio.run(REAL_RUN([{'role': 'user', 'content': 'test'}], [], None, budget))
    public = provider_failure(failure.value, agent_budget=budget.metadata())
    assert public['error_code'] == ('agent_budget_exceeded' if expired else 'provider_timeout')
    assert 'private SDK' not in public['provider_error']
    assert closed and not harness_runtime.active


@pytest.mark.parametrize('message,reason', [
    ('time budget reached', 'time'),
    ('model step budget reached', 'model_calls'),
    ('estimated cost budget reached', 'estimated_cost'),
])
def test_budget_error_identifies_exact_limit(message, reason):
    result = provider_failure(BudgetExceeded(message), agent_budget=Budget(True).metadata())
    assert result['budget_reason'] == reason
    assert result['agent_budget']['model_calls'] == 0
    assert 'Model requests admitted: 0' in result['provider_error']


def test_invalid_protocol_requests_do_not_fail_model_turn():
    invoked = []
    tools = [{'function': {'name': 'probe', 'description': 'probe', 'parameters': {'type': 'object'}}}]
    budget = Budget(False)
    with harness_runtime.Gateway(tools, lambda *args: invoked.append(args) or {'ok': True}, budget, lambda *_: None) as gateway:
        with httpx.Client(trust_env=False) as client:
            headers = {'Authorization': 'Bearer ' + gateway.secret}
            for body in ['not json', '[]', '{"method":"tools/list","params":[]}']:
                assert client.post(gateway.url + '/mcp', headers=headers, content=body).status_code == 400
            response = client.post(gateway.url + '/mcp', headers=headers,
                                   json={'id': 1, 'method': 'tools/call', 'params': {'name': ['bad']}})
            assert response.json()['result']['isError']
            response = client.post(gateway.url + '/mcp', headers=headers,
                                   json={'id': 2, 'method': 'tools/call', 'params': {'name': 'probe'}})
            assert not response.json()['result']['isError']
    assert gateway.error is None and budget.calls == 0
    assert invoked == [('probe', {})]


def test_stream_failure_does_not_append_another_http_response(monkeypatch):
    class BrokenStream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'data: {"type":"message_start"}\n\n'
            raise httpx.ReadError('private stream diagnostics')

    real_client = httpx.Client
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'local-mock-only')
    with real_client(trust_env=False) as client:
        monkeypatch.setattr(harness_runtime.httpx, 'Client', lambda **kwargs: real_client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=BrokenStream())), **kwargs))
        with harness_runtime.Gateway([], None, Budget(False), lambda *_: None) as gateway:
            response = client.post(gateway.url + '/v1/messages',
                                   headers={'Authorization': 'Bearer ' + gateway.secret}, json={'messages': []})
            assert response.status_code == 200
            assert response.text == 'data: {"type":"message_start"}\n\n'
            assert isinstance(gateway.error, httpx.ReadError)


@pytest.mark.parametrize('failure_type', [TimeoutError, asyncio.CancelledError])
def test_cleanup_failure_preserves_original_error(monkeypatch, failure_type):
    import deepseek_harness

    class Runtime:
        def __init__(self, **_):
            pass

        def start(self):
            raise failure_type('original failure')

        def close(self):
            raise RuntimeError('cleanup failure')

    monkeypatch.setattr(deepseek_harness, 'DeepSeekHarness', Runtime)
    with pytest.raises(failure_type, match='original failure'):
        asyncio.run(REAL_RUN([{'role': 'user', 'content': 'test'}], [], None, Budget(False)))
    assert not harness_runtime.active
    assert not list(harness_runtime.ROOT.rglob('buck.patch.yml'))


@pytest.mark.parametrize('value', ['nan', 'inf', '-inf', '0', '-1'])
def test_invalid_time_setting_is_rejected_before_capping(monkeypatch, value):
    monkeypatch.setenv('AGENT_TIME_LIMIT_S', value)
    with pytest.raises(ValueError, match='budget settings'):
        Budget(False)


def test_valid_time_setting_still_has_hard_cap(monkeypatch):
    monkeypatch.setenv('AGENT_TIME_LIMIT_S', '1200')
    assert Budget(False).seconds == 900


@pytest.mark.parametrize('complete', [False, True])
@pytest.mark.parametrize('data_prefix', ['data: ', 'data:'])
def test_gateway_settles_cumulative_usage_only_for_complete_stream(monkeypatch, complete, data_prefix):
    from backend.provider_errors import HarnessTurnError
    frames = [
        {'type':'message_start', 'message':{'usage':{'input_tokens':1000,
            'cache_read_input_tokens':9000, 'cache_creation_input_tokens':0, 'output_tokens':0}}},
        {'type':'message_delta', 'usage':{'output_tokens':100}, 'delta':{}},
        {'type':'message_delta', 'usage':{'output_tokens':200}, 'delta':{'stop_reason':'end_turn'}},
        # A later stop-only delta must not erase the last cumulative usage.
        {'type':'message_delta', 'delta':{'stop_reason':'end_turn'}},
    ]
    if complete:
        frames.append({'type':'message_stop'})
    raw = ''.join(data_prefix+json.dumps(frame)+'\n\n' for frame in frames).encode()

    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            for i in range(0, len(raw), 17):
                yield raw[i:i+17]

    real_client = httpx.Client
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'local-mock-only')
    budget = Budget(True)
    with real_client(trust_env=False) as client:
        monkeypatch.setattr(harness_runtime.httpx, 'Client', lambda **kwargs: real_client(
            transport=httpx.MockTransport(lambda _: httpx.Response(200, stream=Stream())), **kwargs))
        with harness_runtime.Gateway([], None, budget, lambda *_: None) as gateway:
            response = client.post(gateway.url+'/v1/messages',
                headers={'Authorization':'Bearer '+gateway.secret}, json={'messages':[]})
            assert response.content == raw
            if complete:
                assert gateway.error is None
                assert budget.reserved == pytest.approx((1000*budget.input_price+9000*budget.cached_input_price+200*budget.output_price)/1e6)
                assert not budget.reservations
            else:
                assert isinstance(gateway.error, HarnessTurnError)
                assert len(budget.reservations) == 1
