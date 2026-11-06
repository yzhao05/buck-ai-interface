from backend import chat
"""Regression cases found while reviewing configuration and plan integration."""
import asyncio
import pytest

from backend import configuration
from backend.configuration_fields import validate_structure
from backend.schemas import ConfigureIdentification, IdentificationPlanProposal, Mapping
from test_configuration import provider, request
from test_identification_plan import proposal, request as plan_request


def test_pann_window_and_filter_window_are_independent(monkeypatch):
    changes={'pann_window_cycles':3,'pwm_denoise':'sg'}
    provider(monkeypatch,dict(open_panel=True,changes=changes,start_after_apply=False,evidence={'changes.pann_window_cycles':'PANN窗口长度改为3周期'}))
    r=asyncio.run(chat.answer(request('PANN窗口长度改为3周期，滤波改为SG'),None))
    assert r['ui_action']['changes']==changes


def test_quoted_uploaded_filename_is_data_not_a_quoted_command():
    req=request('切换到“my circuit.png”');req.ui_context.catalog=[{'id':'picture-1','kind':'circuit','name':'my circuit.png'}]
    p=ConfigureIdentification(open_panel=False,changes={},assets={'circuit_id':'picture-1'},start_after_apply=False,evidence={'assets.circuit_id':'切换到“my circuit.png”'})
    assert validate_structure(req,p,{})[1]['assets']['circuit_id']=='picture-1'


def test_auto_pwm_is_resolved_after_mapping_changes():
    req=request('把PWM列设为sw');req.ui_context.pwm_source_setting='auto';req.ui_context.options.pwm_source='controller'
    req.ui_context.mapping=Mapping(time='t',current='i',voltage='v');req.ui_context.columns=['t','i','v','sw']
    p=ConfigureIdentification(open_panel=False,changes={},mapping={'pwm':'sw'},start_after_apply=False,evidence={'mapping.pwm':req.message})
    assert validate_structure(req,p,{})[0].pwm_source=='measured'


def test_plan_cannot_override_explicit_source_from_second_parameter_container():
    p=proposal(pwm_source='measured',pwm_evidence='实际PWM',tuning={'pwm_source':'controller'},evidence={'changes.pwm_source':'实际PWM'})
    with pytest.raises(ValueError,match='冲突'):
        configuration.batch_action(plan_request('运行两种方法，使用实际PWM'),IdentificationPlanProposal.model_validate(p))


def test_plan_filename_and_asset_must_refer_to_same_file():
    req=plan_request('使用a.csv运行两种方法');req.ui_context.catalog=[{'id':'csv-b','kind':'csv','name':'b.csv'}]
    p=proposal(filename='a.csv',assets={'csv_id':'csv-b'},evidence={'assets.csv_id':'使用a.csv'})
    with pytest.raises(ValueError,match='不一致'):configuration.batch_action(req,IdentificationPlanProposal.model_validate(p))
