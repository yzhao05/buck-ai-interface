import asyncio
import json

import httpx
import pytest

from backend import chat, configuration
from backend.schemas import ChatRequest, FitOptions, IdentificationPlanProposal


def request(message, **opts):
    return ChatRequest(message=message, ui_context={"request_id": "plan-1", "options": FitOptions(**opts).model_dump()})


def proposal(**updates):
    result=dict(execute=True, clarification='', filename=None, pwm_source='unchanged',
                pwm_evidence='', mode=None, resistance_ohm=None) | updates
    if result['pwm_source'] in ('measured','controller','both') and result['pwm_evidence']:
        result.setdefault('execution_selection',{'methods':['weighted_integral','pann'],
            'pwm_sources':['measured','controller'] if result['pwm_source']=='both' else [result['pwm_source']],
            'method_evidence':'用户要求两种方法','pwm_evidence':result['pwm_evidence']})
    return result


def provider(monkeypatch, arguments, *, fail=False, calls=None):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-key')
    captured=[]
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            captured.append(kwargs['json'])
            if kwargs['json']['messages'][-1]['role']=='tool':
                return httpx.Response(200,request=httpx.Request('POST',url),json={'choices':[{'message':{'role':'assistant','content':'Plan proposed.'}}]})
            if fail:
                raise httpx.ConnectError('unavailable')
            tool_calls=calls if calls is not None else [{'id':'plan-1','type':'function','function': {
                'name':'propose_identification_plan', 'arguments':json.dumps(arguments)}}]
            return httpx.Response(200, request=httpx.Request('POST',url),
                                  json={'choices':[{'message':{'tool_calls':tool_calls}}]})
    monkeypatch.setattr(chat.httpx, 'AsyncClient', Client)
    return captured


def test_no_provider_does_not_fall_back_to_rules(monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    result=asyncio.run(chat.answer(request('根据已有PWM运行两种方法'),{}))
    assert result['ui_action'] is None and result['job_id'] is None
    assert result['answer'] == '' and result['error_code'] == 'missing_api_key'
    assert result['mode']=='provider_error'


def test_chat_dispatch_uses_model_plan_without_starting_old_configuration(monkeypatch):
    captured=provider(monkeypatch,proposal(pwm_source='measured',pwm_evidence='已有PWM'))
    async def invoke(name):
        pytest.fail('A plan proposal must not execute a numerical job: '+name)
    result=asyncio.run(chat.answer(request('根据已有PWM运行两种方法',pwm_source='controller',mode='LC'),invoke))
    assert result['mode']=='deepseek'
    assert result['job_id'] is None
    assert result['ui_action']['changes']=={'pwm_source':'measured'}
    assert len(captured)==2


def test_provider_failure_does_not_fall_back_to_old_settings(monkeypatch):
    provider(monkeypatch,{},fail=True)
    result=asyncio.run(chat.answer(request('根据已有PWM运行两种方法',pwm_source='controller',mode='LC'),{}))
    assert result['ui_action'] is None and result['job_id'] is None
    assert result['mode']=='provider_error'


@pytest.mark.parametrize('updates', [
    {'pwm_source':'auto'},
    {'filename':'invented.csv'}, {'resistance_ohm':0}, {'resistance_ohm':100001},
    {'controller_confirmed':True}, {'execute':'true'}, {'resistance_ohm':'6'},
])
def test_invalid_model_plan_never_applies(monkeypatch,updates):
    provider(monkeypatch,proposal(**updates))
    result=asyncio.run(chat.answer(request('根据已有PWM运行两种方法'),{}))
    assert result['ui_action'] is None and result['job_id'] is None


@pytest.mark.parametrize('calls', [[], [{'function':{'name':'start_identification','arguments':'{}'}}],
    [{'function':{'name':'propose_identification_plan','arguments':'not json'}}]])
def test_invalid_provider_tool_calls(monkeypatch,calls):
    provider(monkeypatch,{},calls=calls)
    assert asyncio.run(chat.answer(request('运行两种方法'),{}))['ui_action'] is None


@pytest.mark.parametrize('text', ['根据特殊PWM运行两种方法','使用实际PWM或重建PWM运行两种方法'])
def test_model_clarification_is_returned_without_execution(monkeypatch,text):
    provider(monkeypatch,proposal(execute=False,pwm_source='unclear',clarification='请选择一种 PWM 来源。'))
    result=asyncio.run(chat.answer(request(text),{}))
    assert result['ui_action'] is None and result['job_id'] is None
    assert result['mode']=='deepseek'
    assert any(t.get('status')=='未执行' for t in result['tool_calls'])


@pytest.mark.parametrize('text,parsed,changes', [
    ('使用已知控制器重建PWM运行两种方法',proposal(pwm_source='controller',pwm_evidence='已知控制器重建PWM'),{'pwm_source':'controller','mode':'LC'}),
    ('固定 R=6，完成重建 PWM 下两种方法的辨识',proposal(pwm_source='controller',pwm_evidence='重建 PWM',resistance_ohm=6),{'pwm_source':'controller','mode':'LC','resistance_ohm':6}),
    ('请使用 R1.csv 运行两种方法',proposal(filename='R1.csv'),{}),
    ('运行两种方法；上次说的是“固定 R=10，重建 PWM”。',proposal(),{}),
    ('Run both methods on "another case.csv" with measured PWM',proposal(filename='another case.csv',pwm_source='measured',pwm_evidence='measured PWM'),{'pwm_source':'measured'}),
])
def test_validated_model_plan(monkeypatch,text,parsed,changes):
    provider(monkeypatch,parsed)
    req=request(text)
    if parsed.get('filename'):req.ui_context.catalog=[{'id':'csv-1','kind':'csv','name':parsed['filename']}]
    result=asyncio.run(chat.answer(req,None))
    if parsed.get('execution_selection'):
        assert result['ui_action']['changes']==changes
    else:
        assert result['ui_action'] is None
        assert result['tool_calls'][-1]['status']=='未执行'


FOUR_REQUEST='根据我的CASE16A_noise03_seed13.csv，完成rebuild pwm和实际pwm；pann和加权积分四个仿真'


def test_four_runs_parsed_by_deepseek_with_identification_panel_closed(monkeypatch):
    calls=provider(monkeypatch,proposal(filename='CASE16A_noise03_seed13.csv',
        pwm_source='both',pwm_evidence='rebuild pwm和实际pwm'))
    req=request(FOUR_REQUEST)
    req.ui_context.catalog=[{'id':'csv-1','kind':'csv','name':'CASE16A_noise03_seed13.csv'}]
    assert req.identification_enabled is False
    async def invoke(name): pytest.fail('Only propose a plan; do not start old-config jobs: '+name)
    result=asyncio.run(chat.answer(req,invoke))
    assert result['route']['mode']=='agent'
    assert result['mode']=='deepseek' and result['job_id'] is None
    action=result['ui_action']
    assert action['version']==3 and action['changes']=={'mode':'LC'}
    assert action['runs']==[{'method':method,'pwm_source':source}
        for source in ('measured','controller') for method in ('weighted_integral','pann')]
    assert len(calls)==2
