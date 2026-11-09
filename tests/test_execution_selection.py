import asyncio
import pytest
from backend import chat, configuration, harness_runtime
from backend.schemas import ChatRequest, FitOptions, IdentificationPlanProposal
from backend.execution_selection import require_execution_selection


def selection(methods=None,sources=None):
    return {'methods':methods or ['weighted_integral'],'pwm_sources':sources or ['measured'],
            'method_evidence':'用户选择抗噪加权积分','pwm_evidence':'用户选择CSV实际PWM'}


def request():
    return ChatRequest(message='数据就绪后直接开始',ui_context={'request_id':'r','options':FitOptions(method='weighted_integral').model_dump()})


def test_start_without_choices_becomes_main_conversation_question():
    action=configuration.agent_action(request(),'configure_identification',{'open_panel':True,'changes':{},'start_after_apply':True})
    assert action['start_after_apply'] is False
    assert action['request_selection'] is True
    assert action['execution_selection'] is None


def test_confirmation_of_circuit_or_default_settings_is_not_selection():
    req=request();req.ui_context.pending_start=True
    action=configuration.agent_action(req,'configure_identification',{'open_panel':True,'changes':{'controller_confirmed':True},'start_after_apply':True})
    assert action['request_selection'] and not action['start_after_apply']


def test_explicit_matching_choices_allow_start_and_are_forwarded():
    choices=selection()
    action=configuration.agent_action(request(),'configure_identification',{'open_panel':True,'changes':{},'start_after_apply':True,'execution_selection':choices})
    assert action['start_after_apply'] and not action['request_selection']
    assert action['execution_selection']==choices


@pytest.mark.parametrize('sources,version', [(['auto'],2),(['measured'],2),(['measured','controller'],3)])
def test_configure_start_honors_all_selected_methods(sources,version):
    req=request()
    req.ui_context.options=FitOptions(method='pann',mode='LC',resistance_ohm=6)
    choices=selection(['weighted_integral','pann'],sources)
    action=configuration.agent_action(req,'configure_identification',{'open_panel':False,
        'changes':{},'start_after_apply':True,'execution_selection':choices})
    assert action['type']=='identification_plan' and action['version']==version
    assert action['methods']==['weighted_integral','pann']
    assert action['execution_selection']==choices
    if version==2:
        assert action['changes']['pwm_source']==sources[0]
    else:
        assert len(action['runs'])==4


def test_configure_can_continue_a_paused_dual_plan():
    req=request()
    req.ui_context.paused_plan=True
    action=configuration.agent_action(req,'configure_identification',{'open_panel':False,'changes':{},
        'start_after_apply':True,'execution_selection':selection(['weighted_integral','pann'])})
    assert action['type']=='configure_identification' and action['start_after_apply']


@pytest.mark.parametrize('sources,version', [(['auto'],2),(['controller'],2),(['measured','controller'],3)])
def test_plan_tool_uses_full_selection_even_if_form_was_measured(sources,version):
    req=request()
    req.ui_context.pwm_source_setting='measured'
    req.ui_context.options=FitOptions(mode='LC',resistance_ohm=6)
    plan=IdentificationPlanProposal(execute=True,clarification='',filename=None,pwm_source='unchanged',
        mode=None,resistance_ohm=None,execution_selection=selection(['weighted_integral','pann'],sources))
    result=configuration.batch_action(req,plan)
    assert result['version']==version
    if version==2:
        assert result['changes']['pwm_source']==sources[0]
    else:
        assert len(result['runs'])==4
    with pytest.raises(ValueError,match='PWM 来源与用户选择冲突'):
        configuration.batch_action(req,plan.model_copy(update={'pwm_source':'measured'}))


@pytest.mark.parametrize('field,expected,absent',[('methods','计算方法','PWM 来源'),('pwm_sources','PWM 来源','计算方法')])
def test_only_missing_choice_is_asked(field,expected,absent):
    choices=selection();choices[field]=[]
    with pytest.raises(ValueError) as err:require_execution_selection(choices,['weighted_integral'],['measured'])
    assert expected in str(err.value) and absent not in str(err.value)


def test_no_evidence_or_mismatched_method_cannot_use_default():
    choices=selection();choices['method_evidence']=' '
    with pytest.raises(ValueError):require_execution_selection(choices,['weighted_integral'],['measured'])
    with pytest.raises(ValueError):require_execution_selection(selection(),['pann'],['measured'])
    with pytest.raises(ValueError):require_execution_selection(selection(),['weighted_integral'],['controller'])


def test_auto_is_a_choice_only_when_explicitly_authorized():
    choices=selection(sources=['auto'])
    with pytest.raises(ValueError):require_execution_selection(choices,['weighted_integral'],['measured'])
    assert require_execution_selection(choices,['weighted_integral'],['controller'],allow_auto=True)==choices


def test_four_run_plan_requires_all_methods_and_sources():
    proposal={'execute':True,'clarification':'','filename':None,'pwm_source':'both','pwm_evidence':'两种来源',
              'mode':'LC','resistance_ohm':6,'execution_selection':selection(methods=['weighted_integral','pann'])}
    with pytest.raises(ValueError,match='PWM 来源'):
        configuration.batch_action(request(),IdentificationPlanProposal.model_validate(proposal))
    proposal['execution_selection']['pwm_sources']=['measured','controller']
    result=configuration.batch_action(request(),IdentificationPlanProposal.model_validate(proposal))
    assert len(result['runs'])==4 and result['execution_selection']==proposal['execution_selection']


def test_direct_tool_cannot_bypass_selection_and_read_only_tools_remain_available(monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY','test-only')
    calls=[]
    async def invoke(name):calls.append(name);return {'job_id':'started'}
    async def agent(messages,tools,execute,budget):
        assert {'search_knowledge','get_experiment_history','read_experiment_results'} <= {t['function']['name'] for t in tools}
        blocked=await execute('start_identification',{})
        assert '计算方法' in blocked['error'] and 'PWM 来源' in blocked['error']
        assert calls==[]
        batch=await execute('start_identification',{'execution_selection':selection(['weighted_integral','pann'])})
        assert 'plan' in batch['error'] and calls==[]
        started=await execute('start_identification',{'execution_selection':selection()})
        assert started['job_id']=='started'
        return '已按选择提交。'
    monkeypatch.setattr(harness_runtime,'run',agent)
    result=asyncio.run(chat.answer(ChatRequest(message='开始',identification_enabled=True,options=FitOptions(method='weighted_integral')),invoke))
    assert result['job_id']=='started' and calls==['start_identification']
