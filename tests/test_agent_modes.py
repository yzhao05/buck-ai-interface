import asyncio
import json
from types import SimpleNamespace

import pytest
from backend import chat, configuration, plecs_local
from backend.schemas import ChatRequest
from test_agent_plecs import MODEL


@pytest.mark.parametrize('mode', ['fast', 'thinking'])
@pytest.mark.parametrize('task', ['knowledge', 'hardware', 'diagnosis', 'plecs', 'general'])
def test_modes_share_tool_permissions_and_knowledge_loop(monkeypatch, mode, task):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'mock')
    captured = []
    class Provider:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            payload = kwargs['json']; captured.append(payload)
            if len(captured) == 1:
                message = {'role':'assistant','content':None,'reasoning_content':'opaque', 'tool_calls':[
                    {'id':'search','type':'function','function':{'name':'search_knowledge','arguments':'{"query":"zzzzzzzz"}'}}]}
            else:
                assert payload['messages'][-2]['reasoning_content'] == 'opaque'
                message = {'role':'assistant','content':'No relevant knowledge found.'}
            return SimpleNamespace(raise_for_status=lambda:None, json=lambda:{'choices':[{'message':message}]})
    monkeypatch.setattr(chat.httpx, 'AsyncClient', Provider)
    async def invoke(*args): raise AssertionError('No numerical work expected')
    response = asyncio.run(chat.answer(ChatRequest(message='查询资料', reasoning_mode=mode), invoke))
    assert response['mode'] == 'deepseek'
    assert all(p['thinking']['type'] == ('enabled' if mode == 'thinking' else 'disabled') for p in captured)
    assert response['agent_budget']['thinking'] == (mode == 'thinking')
    tools = {t['function']['name'] for t in captured[0]['tools']}
    assert 'search_knowledge' in tools
    assert 'connect_plecs' in tools
    assert 'open_plecs_model' in tools
    assert 'opaque' not in json.dumps(response)


@pytest.mark.parametrize('mode', ['fast', 'thinking'])
def test_configuration_honors_mode_and_reports_budget(monkeypatch, mode):
    from test_configuration import provider, request, ROUTE
    captured = provider(monkeypatch, {'open_panel':True,'changes':{},'start_after_apply':False})
    req = request('打开参数辨识面板', reasoning_mode=mode)
    response = asyncio.run(chat.answer(req, None))
    assert response['mode'] == 'deepseek'
    assert captured[0]['thinking']['type'] == ('enabled' if mode == 'thinking' else 'disabled')
    assert response['agent_budget']['model_calls'] == 2


def test_directory_only_settings_exact_paths_and_stale_selection(tmp_path, monkeypatch):
    monkeypatch.setattr(plecs_local, 'ROOT', tmp_path/'settings')
    folder = tmp_path/'models'; folder.mkdir()
    original = folder/'buck with spaces.plecs'; original.write_text(MODEL)
    nested = folder/'backup'; nested.mkdir(); (nested/original.name).write_text(MODEL)
    plecs_local.configure('a', '', str(folder))
    assert plecs_local.search_models('a', [original.name])['status'] == 'ambiguous'
    found = plecs_local.search_models('a', [str(original)])
    assert found['status'] == 'matched' and found['match']['path'] == str(original)
    other = tmp_path/'other'; other.mkdir()
    plecs_local.configure('a', '', str(other))
    assert plecs_local.settings('a')['profiles'] == {}


def test_openable_model_without_outputs_has_explicit_experiment_limits(tmp_path, monkeypatch):
    monkeypatch.setattr(plecs_local, 'ROOT', tmp_path/'settings')
    model = tmp_path/'no-output.plecs'
    model.write_text(MODEL.replace('Type Output', 'Type Scope'))
    selected = plecs_local.register('a', str(model))
    assert selected['status'] == 'registered' and selected['experiment_issues']


def test_rpc_probe_does_not_accept_unrelated_server(monkeypatch):
    class Proxy:
        system = SimpleNamespace(listMethods=lambda:['proxy.ping'])
        def __enter__(self): return self
        def __exit__(self, *args): pass
    monkeypatch.setattr(plecs_local.xmlrpc.client, 'ServerProxy', lambda *a,**k:Proxy())
    assert not plecs_local.listening()


@pytest.mark.parametrize('mode', ['fast', 'thinking'])
@pytest.mark.parametrize('task', ['knowledge', 'plecs'])
def test_mode_failures_never_fall_back_or_execute(monkeypatch, mode, task):
    import httpx
    async def forbidden(*args): raise AssertionError('No tools may run')
    request = ChatRequest(message='Explain the formula', reasoning_mode=mode)
    monkeypatch.delenv('DEEPSEEK_API_KEY', raising=False)
    response = asyncio.run(chat.answer(request, forbidden))
    assert response['answer'] == '' and response['error_code'] == 'missing_api_key'
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'mock')
    class Provider:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, *args, **kwargs): raise httpx.ReadTimeout('private provider details')
    monkeypatch.setattr(chat.httpx, 'AsyncClient', Provider)
    response = asyncio.run(chat.answer(request, forbidden))
    assert response['answer'] == '' and response['mode'] == 'provider_error'
    assert response['provider_error'].isascii() and 'private provider details' not in json.dumps(response)


@pytest.mark.parametrize('mode', ['fast', 'thinking'])
def test_failed_catalog_never_authorizes_simulation(monkeypatch, mode):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'mock')
    captured = []
    class Provider:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            captured.append(kwargs['json'])
            if len(captured) <= 2:
                name = 'get_plecs_models' if len(captured) == 1 else 'run_plecs_experiment'
                msg = {'role':'assistant','content':None,'tool_calls':[{'id':name,'type':'function',
                    'function':{'name':name,'arguments':'{}'}}]}
            else:
                assert 'error' in json.loads(kwargs['json']['messages'][-1]['content'])
                msg = {'role':'assistant','content':'No model was available; no simulation ran.'}
            return SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'choices':[{'message':msg}]})
    monkeypatch.setattr(chat.httpx, 'AsyncClient', Provider)
    async def invoke(name, args=None):
        assert name == 'get_plecs_models'
        return {'error':'model context changed'}
    response = asyncio.run(chat.answer(ChatRequest(message='运行 PLECS 对照', reasoning_mode=mode), invoke))
    assert response['mode'] == 'deepseek' and response['plecs_experiments'] == []
