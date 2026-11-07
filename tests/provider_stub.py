"""Provider transport stub for API integration tests, never used by the app."""
import copy
import json
import httpx
from backend import chat


def install(monkeypatch, *, execute=False, controls=False):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-only')
    captured = []
    class Provider:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            payload = copy.deepcopy(kwargs['json'])
            captured.append(payload)
            if controls and payload['messages'][-1]['role'] != 'tool' and any(t['function']['name']=='run_diagnostic_controls' for t in payload['tools']):
                msg = {'role':'assistant','content':None,'tool_calls':[{'id':'controls','type':'function',
                    'function':{'name':'run_diagnostic_controls','arguments':'{}'}}]}
            elif execute and payload['messages'][-1]['role'] != 'tool':
                opts=json.loads(payload['messages'][1]['content'].split('：',1)[1])['configuration']
                selection={'methods':[opts['method']],'pwm_sources':[opts['pwm_source']],
                           'method_evidence':'Explicit execution fixture method','pwm_evidence':'Explicit execution fixture PWM'}
                msg = {'role':'assistant', 'content':None, 'tool_calls':[{'id':'fit', 'type':'function',
                    'function':{'name':'start_identification', 'arguments':json.dumps({'execution_selection':selection})}}]}
            else:
                msg = {'role':'assistant', 'content':'Provider answer'}
            return httpx.Response(200, request=httpx.Request('POST',url), json={'choices':[{'message':msg}]})
    monkeypatch.setattr(chat.httpx, 'AsyncClient', Provider)
    return captured
