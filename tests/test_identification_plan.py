import asyncio
import json

import httpx
import pytest

from backend import chat, configuration, task_router
from backend.schemas import ChatRequest, FitOptions, IdentificationPlanProposal


def request(message, **opts):
    return ChatRequest(message=message, ui_context={"request_id": "plan-1", "options": FitOptions(**opts).model_dump()})


def proposal(**updates):
    return dict(execute=True, clarification='', filename=None, pwm_source='unchanged',
                pwm_evidence='', mode=None, resistance_ohm=None) | updates


def provider(monkeypatch, arguments, *, fail=False, calls=None):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-key')
    captured=[]
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            captured.append(kwargs['json'])
            if fail:
                raise httpx.ConnectError('unavailable')
            tool_calls=calls if calls is not None else [{'function': {
                'name':'propose_identification_plan', 'arguments':json.dumps(arguments)}}]
            return httpx.Response(200, request=httpx.Request('POST',url),
                                  json={'choices':[{'message':{'tool_calls':tool_calls}}]})
    monkeypatch.setattr(configuration.httpx, 'AsyncClient', Client)
    return captured


@pytest.mark.parametrize('wording', [
    '已有pwm', '已知 PWM', '已知的PWM', '已有的 PWM 参数', '现有 PWM', '给定 PWM',
    'known PWM', 'existing PWM', 'provided PWM', 'CSV里已经记录下来的开关信号',
])
def test_deepseek_parses_source_instead_of_reusing_controller(monkeypatch, wording):
    captured=provider(monkeypatch, proposal(filename='CASE16A_noise03_seed14.csv',
        pwm_source='measured', pwm_evidence=wording))
    req=request(f'请对于CASE16A_noise03_seed14.csv根据{wording}完成两种方式参数识别',
                pwm_source='controller', mode='LC', controller_confirmed=True)
    route=asyncio.run(task_router.classify(req))
    result=asyncio.run(configuration.propose(req,route))
    assert result['mode']=='deepseek'
    assert result['ui_action']['parser']=='deepseek'
    assert result['ui_action']['version']==2
    assert result['ui_action']['changes']=={'pwm_source':'measured'}
    assert result['ui_action']['methods']==['weighted_integral','pann']
    assert result['ui_action']['filename']=='CASE16A_noise03_seed14.csv'
    assert len(captured)==1
    assert captured[0]['tools'][0]['function']['name']=='propose_identification_plan'
    assert captured[0]['messages'][-1]=={'role':'user','content':req.message}
    assert not any('当前表单' in m['content'] for m in captured[0]['messages'])
    assert req.ui_context.options.pwm_source=='controller'


def test_no_provider_does_not_fall_back_to_rules(monkeypatch):
    monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    result=asyncio.run(configuration.propose(request('根据已有PWM运行两种方法'),{}))
    assert result['ui_action'] is None and result['job_id'] is None
    assert 'DeepSeek' in result['answer']
    assert result['mode']=='plan_error'


def test_chat_dispatch_uses_model_plan_without_starting_old_configuration(monkeypatch):
    captured=provider(monkeypatch,proposal(pwm_source='measured',pwm_evidence='已有PWM'))
    async def invoke(name):
        pytest.fail('A plan proposal must not execute a numerical job: '+name)
    result=asyncio.run(chat.answer(request('根据已有PWM运行两种方法',pwm_source='controller',mode='LC'),invoke))
    assert result['mode']=='deepseek'
    assert result['job_id'] is None
    assert result['ui_action']['changes']=={'pwm_source':'measured'}
    assert len(captured)==1


def test_provider_failure_does_not_fall_back_to_old_settings(monkeypatch):
    provider(monkeypatch,{},fail=True)
    result=asyncio.run(configuration.propose(request('根据已有PWM运行两种方法',pwm_source='controller',mode='LC'),{}))
    assert result['ui_action'] is None and result['job_id'] is None
    assert result['mode']=='plan_error'


@pytest.mark.parametrize('updates', [
    {'pwm_source':'unchanged'}, {'pwm_source':'auto'},
    {'pwm_source':'controller','pwm_evidence':'重建PWM'},
    {'filename':'invented.csv'}, {'resistance_ohm':0}, {'resistance_ohm':100001},
    {'controller_confirmed':True}, {'execute':'true'}, {'resistance_ohm':'6'},
])
def test_invalid_model_plan_never_applies(monkeypatch,updates):
    provider(monkeypatch,proposal(**updates))
    result=asyncio.run(configuration.propose(request('根据已有PWM运行两种方法'),{}))
    assert result['ui_action'] is None and result['job_id'] is None


@pytest.mark.parametrize('calls', [[], [{'function':{'name':'start_identification','arguments':'{}'}}],
    [{'function':{'name':'propose_identification_plan','arguments':'not json'}}]])
def test_invalid_provider_tool_calls(monkeypatch,calls):
    provider(monkeypatch,{},calls=calls)
    assert asyncio.run(configuration.propose(request('运行两种方法'),{}))['ui_action'] is None


@pytest.mark.parametrize('text', ['根据特殊PWM运行两种方法','使用实际PWM和重建PWM运行两种方法'])
def test_model_clarification_is_returned_without_execution(monkeypatch,text):
    provider(monkeypatch,proposal(execute=False,pwm_source='unclear',clarification='请选择一种 PWM 来源。'))
    result=asyncio.run(configuration.propose(request(text),{}))
    assert result['ui_action'] is None and result['job_id'] is None
    assert result['mode']=='deepseek'
    assert '请选择一种 PWM 来源' in result['answer']


@pytest.mark.parametrize('text,parsed,changes', [
    ('使用已知控制器重建PWM运行两种方法',proposal(pwm_source='controller',pwm_evidence='已知控制器重建PWM'),{'pwm_source':'controller','mode':'LC'}),
    ('固定 R=6，完成重建 PWM 下两种方法的辨识',proposal(pwm_source='controller',pwm_evidence='重建 PWM',resistance_ohm=6),{'pwm_source':'controller','mode':'LC','resistance_ohm':6}),
    ('请使用 R1.csv 运行两种方法',proposal(filename='R1.csv'),{}),
    ('运行两种方法；上次说的是“固定 R=10，重建 PWM”。',proposal(),{}),
    ('Run both methods on "another case.csv" with measured PWM',proposal(filename='another case.csv',pwm_source='measured',pwm_evidence='measured PWM'),{'pwm_source':'measured'}),
])
def test_validated_model_plan(monkeypatch,text,parsed,changes):
    provider(monkeypatch,parsed)
    result=asyncio.run(configuration.propose(request(text),{}))
    assert result['ui_action']['changes']==changes


@pytest.mark.parametrize('text,parsed', [
    ('使用重建PWM，运行两种方法，LCR联合辨识',proposal(pwm_source='controller',pwm_evidence='重建PWM',mode='LCR')),
    ('运行两种方法，用 a.csv 和 b.csv',proposal(filename='a.csv')),
    ('请使用 "../a.csv" 运行两种方法',proposal(filename='../a.csv')),
    ('运行两种方法，设置训练轮数为300',proposal()),
    ('运行两种方法，Vin=30',proposal()),
    ('运行两种方法，R1=11',proposal()),
    ('运行两种方法；上次说的是“重建PWM”。',proposal(pwm_source='controller',pwm_evidence='重建PWM')),
    ('请使用 known_pwm.csv 运行两种方法',proposal(filename='known_pwm.csv',pwm_source='measured',pwm_evidence='known_pwm')),
    ('不要运行两种方法',proposal()),
])
def test_server_rejects_unsafe_partial_or_unauthorized_proposals(text,parsed):
    with pytest.raises(ValueError):
        configuration.batch_action(request(text),IdentificationPlanProposal.model_validate(parsed))


@pytest.mark.parametrize('text', [
    '比较两种方法之前完成的参数辨识结果', '两种方法已经完成了参数识别，分析结果',
    '不要运行两种方法', '如何运行两种方法？', '例如“运行两种方法”是什么意思',
    'Compare PANN and integral results', 'Do not run both methods',
    '请比较已运行的两种方法结果', '请分析两种方法的运行情况',
    'I have run both methods. Compare their results.',
    'Both methods are complete. Explain the results.',
    '两种方法运行了吗？', '请查看上次执行的 PANN 和积分结果',
])
def test_analysis_and_examples_never_execute(text):
    assert not configuration.batch_requested(text)


@pytest.mark.parametrize('text', ['请运行两种方法，然后比较结果', '完成两种方法的参数识别，并分析结果', 'Run both methods, then compare the results'])
def test_execution_then_comparison_remains_authorized(text):
    assert configuration.batch_requested(text)
