import asyncio

import pytest

from backend import configuration, task_router
from backend.schemas import ChatRequest, FitOptions


def request(message, **opts):
    return ChatRequest(message=message, ui_context={"request_id": "plan-1", "options": FitOptions(**opts).model_dump()})


@pytest.mark.parametrize("message,filename,source", [
    ("请根据我上传的CASE16A_noise03_seed14.csv完成重建pwm下两种方式的参数识别", "CASE16A_noise03_seed14.csv", "controller"),
    ('Run both methods on "another case.csv" with measured PWM', "another case.csv", "measured"),
    ("使用重建 PWM，运行 PANN 和加权积分", None, "controller"),
    ("开始两种方法的参数辨识", None, None),
])
def test_explicit_plan_without_provider(monkeypatch, message, filename, source):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    req = request(message, vin=35, epochs=230, resistance_ohm=7)
    route = asyncio.run(task_router.classify(req))
    assert route['task'] == 'configuration'
    assert task_router.identification_intent(message) == 'execute'
    action = asyncio.run(configuration.propose(req, route))['ui_action']
    assert action['type'] == 'identification_plan'
    assert action['filename'] == filename
    assert action['methods'] == ['weighted_integral', 'pann']
    assert action['changes'].get('pwm_source') == source
    if source == 'controller':
        assert action['changes']['mode'] == 'LC'
    assert 'resistance_ohm' not in action['changes']
    assert req.ui_context.options.epochs == 230


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


@pytest.mark.parametrize('text', [
    '运行两种方法，设置训练轮数为300',
    '使用实际 PWM 和重建 PWM 运行两种方法',
    '运行两种方法，用 a.csv 和 b.csv',
    '运行两种方法，固定 R=-1',
    '运行两种方法，固定 R=100001',
    '使用重建PWM，运行两种方法，LCR 联合辨识',
    '运行两种方法，Vin=30', '运行两种方法，epochs=300',
    '运行两种方法，固定 R=6e3',
    '运行两种方法，R1=11', '运行两种方法，C2=1.1',
])
def test_unsupported_or_conflicting_requests_are_not_partially_applied(text):
    result = asyncio.run(configuration.propose(request(text), {}))
    assert result['ui_action'] is None
    assert result['job_id'] is None


def test_explicit_fixed_resistance():
    action = configuration.batch_action(request('固定 R=6，完成重建 PWM 下两种方法的辨识'))
    assert action['changes'] == {'pwm_source': 'controller', 'mode': 'LC', 'resistance_ohm': 6}


def test_quoted_old_configuration_does_not_change_plan():
    action = configuration.batch_action(request('运行两种方法；上次说的是“固定 R=10，重建 PWM”。'))
    assert action['changes'] == {}


@pytest.mark.parametrize('filename', ['R6_data.csv', 'R1.csv', 'R60_data.csv'])
def test_filename_cannot_set_load_resistance(filename):
    action=configuration.batch_action(request(f'请使用 {filename} 运行两种方法', resistance_ohm=9))
    assert action['filename']==filename
    assert action['changes']=={}


@pytest.mark.parametrize('text', ['请运行两种方法，然后比较结果', '完成两种方法的参数识别，并分析结果', 'Run both methods, then compare the results'])
def test_execution_then_comparison_remains_authorized(text):
    assert configuration.batch_requested(text)
