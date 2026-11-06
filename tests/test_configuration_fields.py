import asyncio

import pytest

from backend import configuration
from backend.schemas import ControllerParameters, ConfigureIdentification, IdentificationPlanProposal
from backend.configuration_fields import validate_fields
from test_configuration import provider, request, ROUTE


@pytest.mark.parametrize('message,changes',[
    ('训练轮数改为300',{'epochs':300}),
    ('时间窗口改为0–0.8 ms',{'start_s':0,'end_s':.0008}),
    ('时间窗口改为100 us到0.8 ms',{'start_s':.0001,'end_s':.0008}),
    ('只把结束时间改为800微秒',{'end_s':.0008}),
    ('Set the time range to 0-.8 ms',{'start_s':0,'end_s':.0008}),
    ('清空开始时间',{'start_s':None}),
    ('关闭滤波',{'pwm_denoise':'none'}),
    ('滤波改为modified-sinc，滤波窗口设为4周期',{'pwm_denoise':'modified_sinc','pwm_filter_cycles':4}),
    ('SG滤波，窗口4周期',{'pwm_denoise':'sg','pwm_filter_cycles':4}),
])
def test_tuning_patch_only_explicit_fields(monkeypatch,message,changes):
    provider(monkeypatch,dict(open_panel=False,changes=changes,start_after_apply=False))
    req=request(message)
    before=req.ui_context.model_dump()
    result=asyncio.run(configuration.propose(req,ROUTE))
    assert result['ui_action']['changes']==changes, result
    assert not result['ui_action']['start_after_apply']
    assert req.ui_context.model_dump()==before


@pytest.mark.parametrize('message,changes',[
    ('训练轮数改为300',{'epochs':250}),
    ('训练轮数改为300',{'epochs':300,'pwm_denoise':'sg'}),
    ('时间窗口改为0–0.8 ms',{'start_s':0,'end_s':.8}),
    ('时间窗口改为0–0.8 ms',{'end_s':.0008}),
    ('结束时间改为0.1 ms',{'end_s':.0001}),
    ('时间窗口改为0–0.8',{'start_s':0,'end_s':.0008}),
    ('调整滤波然后开始',{}),
    ('保持训练轮数不变，改用PANN',{'method':'pann','epochs':300}),
    ('训练轮数改为-300',{'epochs':300}),
    ('训练轮数改为301',{'epochs':301}),
    ('滤波窗口改为9周期',{'pwm_filter_cycles':9}),
    ('清空开始时间',{'start_s':None,'end_s':None}),
])
def test_tuning_rejects_wrong_units_partial_changes_and_invalid_intervals(monkeypatch,message,changes):
    provider(monkeypatch,dict(open_panel=True,changes=changes,start_after_apply=False))
    req=request(message);req.ui_context.options.start_s=.0002
    result=asyncio.run(configuration.propose(req,ROUTE))
    assert result['ui_action'] is None,result


CASES=[
    ('输入电压改为35 V',{'vin':35}),('开关频率改为80 kHz',{'frequency_hz':80000}),
    ('训练占比设为75%',{'train_fraction':.75}),('窗口初值改为固定测量初值',{'pann_initial_state':'measured'}),
    ('PANN窗口长度改为3周期',{'pann_window_cycles':3}),('PWM来源改为实测',{'pwm_source':'measured'}),
    ('PWM来源改为自动',{'pwm_source':'auto'}),('我确认当前接线和参数一致',{'controller_confirmed':True}),
    ('取消电路确认',{'controller_confirmed':False}),
]
@pytest.mark.parametrize('message,changes',CASES)
def test_all_additional_form_options(monkeypatch,message,changes):
    evidence={'changes.'+key:message for key in changes}
    provider(monkeypatch,dict(open_panel=False,changes=changes,evidence=evidence,start_after_apply=False))
    req=request(message);req.ui_context.options.mode='LC'
    result=asyncio.run(configuration.propose(req,ROUTE))
    assert result['ui_action']['changes']==changes,result


@pytest.mark.parametrize('key,value',ControllerParameters().model_dump().items())
def test_each_controller_field_is_partial_and_other_values_are_preserved(key,value):
    req=request('设置 '+key+' 为 '+str(value));req.ui_context.options.controller=ControllerParameters(R1_ohm=17000,C2_F=3e-9)
    changes={'controller':{key:value}}
    p=ConfigureIdentification(open_panel=False,start_after_apply=False,changes=changes,evidence={'changes.controller.'+key:req.message})
    merged,_=validate_fields(req,p,changes)
    assert merged.controller.model_dump()=={**req.ui_context.options.controller.model_dump(),key:value}
    assert p.changes.model_dump(exclude_unset=True)==changes


def test_controller_cross_field_limits_and_non_numeric_values():
    with pytest.raises(ValueError):ConfigureIdentification(open_panel=False,start_after_apply=False,changes={'controller':{'R1_ohm':True}})
    req=request('设置占空比下限为0.95');p=ConfigureIdentification(open_panel=False,start_after_apply=False,changes={'controller':{'duty_min':.95}},evidence={'changes.controller.duty_min':req.message})
    with pytest.raises(ValueError):validate_fields(req,p,p.changes.model_dump(exclude_unset=True))


@pytest.mark.parametrize('key,value',dict(time='t',current='i',voltage='v',pwm=None,time_unit='ms',current_unit='mA',voltage_unit='mV').items())
def test_every_mapping_field(key,value):
    req=request('修改映射 '+key+' 为 '+str(value));req.ui_context.columns=['t','i','v','sw'];req.ui_context.mapping={'time':'t','current':'i','voltage':'v','pwm':'sw'}
    # Assignment uses Pydantic explicitly rather than an unchecked test assignment.
    from backend.schemas import Mapping
    req.ui_context.mapping=Mapping.model_validate(req.ui_context.mapping)
    p=ConfigureIdentification(open_panel=False,start_after_apply=False,changes={},mapping={key:value},evidence={'mapping.'+key:req.message})
    _,extra=validate_fields(req,p,{})
    assert extra['mapping']=={key:value}


@pytest.mark.parametrize('section,key,kind',[('assets','csv_id','csv'),('assets','circuit_id','circuit'),('assets','result_id','result'),('analysis','reference_csv_id','csv')])
def test_files_must_come_from_current_private_catalog(section,key,kind):
    req=request('切换到已上传的 example.csv');req.ui_context.catalog=[{'id':'file-1','kind':kind,'name':'example.csv'}]
    p=ConfigureIdentification(open_panel=False,start_after_apply=False,changes={},evidence={section+'.'+key:req.message},**{section:{key:'file-1'}})
    assert validate_fields(req,p,{})[1][section][key]=='file-1'
    req.ui_context.catalog=[]
    with pytest.raises(ValueError):validate_fields(req,p,{})


@pytest.mark.parametrize('key,value',[('truth_L_H',.001),('truth_C_F',.002),('truth_L_H',None),('reference_confirmed',True),('calculate_error',True)])
def test_analysis_options(key,value):
    req=request('我确认同一实验同一工况，设置 '+key+' 为 '+str(value)+' 并计算误差')
    p=ConfigureIdentification(open_panel=False,start_after_apply=False,changes={},analysis={key:value},evidence={'analysis.'+key:req.message})
    assert validate_fields(req,p,{})[1]['analysis']=={key:value}


def test_confirmation_is_not_inferred_from_start_request(monkeypatch):
    provider(monkeypatch,dict(open_panel=True,changes={'controller_confirmed':True},evidence={'changes.controller_confirmed':'开始辨识'},start_after_apply=True))
    assert asyncio.run(configuration.propose(request('开始辨识'),ROUTE))['ui_action'] is None


def test_explicit_confirmation_resumes_only_a_pending_start(monkeypatch):
    provider(monkeypatch,dict(open_panel=True,changes={'controller_confirmed':True},evidence={'changes.controller_confirmed':'是的'},start_after_apply=True))
    req=request('是的');req.ui_context.pending_start=True
    assert asyncio.run(configuration.propose(req,ROUTE))['ui_action']['start_after_apply']
    req.ui_context.pending_start=False
    assert asyncio.run(configuration.propose(req,ROUTE))['ui_action'] is None


def test_reference_confirmation_answers_pending_question_without_starting(monkeypatch):
    provider(monkeypatch,dict(open_panel=False,changes={},analysis={'reference_confirmed':True},evidence={'analysis.reference_confirmed':'是的'},start_after_apply=False))
    req=request('是的');req.ui_context.pending_reference_confirmation=True
    action=asyncio.run(configuration.propose(req,ROUTE))['ui_action']
    assert action['analysis']=={'reference_confirmed':True} and not action['start_after_apply']
    req.ui_context.pending_reference_confirmation=False
    assert asyncio.run(configuration.propose(req,ROUTE))['ui_action'] is None


@pytest.mark.parametrize('message',['不确认','未确认','如何确认？'])
def test_negative_or_question_cannot_resume(message,monkeypatch):
    provider(monkeypatch,dict(open_panel=True,changes={'controller_confirmed':True},evidence={'changes.controller_confirmed':message},start_after_apply=True))
    req=request(message);req.ui_context.pending_start=True
    assert asyncio.run(configuration.propose(req,ROUTE))['ui_action'] is None


def test_two_method_plan_preserves_tuning_and_nested_controller():
    from test_identification_plan import request as plan_request, proposal
    text='设置训练轮数为300，时间窗口改为0–0.8 ms，R1改为12 kΩ，然后运行两种方法'
    parsed=proposal(tuning={'epochs':300,'start_s':0,'end_s':.0008,'controller':{'R1_ohm':12000}},evidence={'changes.controller.R1_ohm':'R1改为12 kΩ'})
    action=configuration.batch_action(plan_request(text),IdentificationPlanProposal.model_validate(parsed))
    assert action['changes']==parsed['tuning']
