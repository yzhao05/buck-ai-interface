"""Regression cases found while reviewing configuration and plan integration."""
import asyncio
import pytest

from backend import configuration
from backend.configuration_fields import validate_fields
from backend.schemas import ConfigureIdentification, IdentificationPlanProposal, Mapping
from test_configuration import provider, request, ROUTE
from test_identification_plan import proposal, request as plan_request


@pytest.mark.parametrize('message',['开始时间改为0.2 ms','Set start time to 0.2 ms','设置start_s为0.0002秒'])
def test_time_field_name_does_not_authorize_execution(monkeypatch,message):
    provider(monkeypatch,dict(open_panel=True,changes={'start_s':.0002},start_after_apply=True))
    assert not configuration.may_start(message)
    assert asyncio.run(configuration.propose(request(message),ROUTE))['ui_action'] is None


@pytest.mark.parametrize('changes',[{'epochs':300,'method':'weighted_integral'},{'epochs':300,'resistance_ohm':8},{'epochs':300,'mode':'LC'}])
def test_epoch_edit_cannot_silently_change_method_mode_or_load(monkeypatch,changes):
    provider(monkeypatch,dict(open_panel=True,changes=changes,start_after_apply=False))
    assert asyncio.run(configuration.propose(request('训练轮数改为300'),ROUTE))['ui_action'] is None


@pytest.mark.parametrize('message,changes,path',[
    ('输入电压改为35V',{'vin':45},'changes.vin'),
    ('频率改为80 kHz',{'frequency_hz':8000},'changes.frequency_hz'),
    ('R1改为12 kΩ',{'controller':{'R1_ohm':12}},'changes.controller.R1_ohm'),
    ('C2改为2.2 nF',{'controller':{'C2_F':2.2e-6}},'changes.controller.C2_F'),
    ('训练占比改为75%',{'train_fraction':.8},'changes.train_fraction'),
])
def test_literal_evidence_must_support_numeric_value(message,changes,path):
    req=request(message)
    p=ConfigureIdentification(open_panel=False,changes=changes,start_after_apply=False,evidence={path:message})
    with pytest.raises(ValueError,match='数值或单位'):validate_fields(req,p,changes)


def test_pann_window_and_filter_window_are_independent(monkeypatch):
    changes={'pann_window_cycles':3,'pwm_denoise':'sg'}
    provider(monkeypatch,dict(open_panel=True,changes=changes,start_after_apply=False,evidence={'changes.pann_window_cycles':'PANN窗口长度改为3周期'}))
    r=asyncio.run(configuration.propose(request('PANN窗口长度改为3周期，滤波改为SG'),ROUTE))
    assert r['ui_action']['changes']==changes


def test_quoted_uploaded_filename_is_data_not_a_quoted_command():
    req=request('切换到“my circuit.png”');req.ui_context.catalog=[{'id':'picture-1','kind':'circuit','name':'my circuit.png'}]
    p=ConfigureIdentification(open_panel=False,changes={},assets={'circuit_id':'picture-1'},start_after_apply=False,evidence={'assets.circuit_id':'切换到“my circuit.png”'})
    assert validate_fields(req,p,{})[1]['assets']['circuit_id']=='picture-1'


def test_auto_pwm_is_resolved_after_mapping_changes():
    req=request('把PWM列设为sw');req.ui_context.pwm_source_setting='auto';req.ui_context.options.pwm_source='controller'
    req.ui_context.mapping=Mapping(time='t',current='i',voltage='v');req.ui_context.columns=['t','i','v','sw']
    p=ConfigureIdentification(open_panel=False,changes={},mapping={'pwm':'sw'},start_after_apply=False,evidence={'mapping.pwm':req.message})
    assert validate_fields(req,p,{})[0].pwm_source=='measured'


def test_plan_cannot_override_explicit_source_from_second_parameter_container():
    p=proposal(pwm_source='measured',pwm_evidence='实际PWM',tuning={'pwm_source':'controller'},evidence={'changes.pwm_source':'实际PWM'})
    with pytest.raises(ValueError,match='冲突'):
        configuration.batch_action(plan_request('运行两种方法，使用实际PWM'),IdentificationPlanProposal.model_validate(p))


def test_plan_filename_and_asset_must_refer_to_same_file():
    req=plan_request('使用a.csv运行两种方法');req.ui_context.catalog=[{'id':'csv-b','kind':'csv','name':'b.csv'}]
    p=proposal(filename='a.csv',assets={'csv_id':'csv-b'},evidence={'assets.csv_id':'使用a.csv'})
    with pytest.raises(ValueError,match='不一致'):configuration.batch_action(req,IdentificationPlanProposal.model_validate(p))
