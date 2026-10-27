import asyncio
import copy
import json
import time
import uuid
from pathlib import Path

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend import chat, main
from backend.data import read_csv, suggest_mapping, extract
from backend.identification import identify, rollout, metric
from backend.diagnostic_controls import run_controls, compatible
from backend.schemas import FitOptions, Mapping, ChatRequest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def experiment():
    data=read_csv((ROOT/'examples/plecs_buck_case16a.csv').read_bytes(),'source.csv')
    mapping=Mapping(**suggest_mapping(data))
    baseline=identify(data,mapping,FitOptions(method='weighted_integral',end_s=.0008))
    # A saved PANN-like snapshot with deliberately distinct factors lets the
    # tests isolate the ablation math without relying on stochastic training.
    source=copy.deepcopy(baseline)
    source['method']=source['options']['method']='pann'
    source['parameters']['C_F'] *= 1.02
    source['diagnostics']['pann']={'initial_parameters':list(baseline['parameters'].values()),
        'rollout_initial_state_A_V':[.1,.01]}
    return data,mapping,baseline,source


def test_four_controls_hold_each_other_factor_fixed(experiment):
    data,mapping,baseline,source=experiment
    before=copy.deepcopy(source)
    out=run_controls(data,source,result_id='current',source_id='source')
    assert source==before and out['retrained'] is False
    cases=out['cases']
    assert len(cases)==4 and out['physical_causes_verified']==[]
    assert cases['integral_measured']['heldout_measured']==pytest.approx(baseline['metrics'])
    assert cases['integral_measured']['parameters']==cases['integral_pann_state']['parameters']
    assert cases['integral_measured']['initial_state_A_V']==cases['pann_measured']['initial_state_A_V']
    a,_=extract(data,mapping);a=a[a[:,0]<=.0008+1e-14]
    split=np.searchsorted(a[:,0],source['train_end_s'])
    pred=rollout(a,list(source['parameters'].values()),source['options']['vin'],np.maximum(a[0,1:3],0))
    assert cases['pann_measured']['heldout_measured']==pytest.approx(metric(a[split:,0],a[split:,1:3],pred[split:]))
    assert out['reference']['status']=='not_provided'
    assert out['pann_source_result_id']=='source'


def test_reference_only_changes_evaluation_not_predictions(experiment):
    data,mapping,_,source=experiment
    ref=copy.deepcopy(data);ref.sha256='f'*64;ref.frame[mapping.current] += .2
    plain=run_controls(data,source,result_id='r',source_id='r')
    out=run_controls(data,source,result_id='r',source_id='r',reference=ref)
    assert out['reference']['status']=='evaluated_user_declared_reference'
    assert not out['reference']['used_for_training_or_initial_state']
    for name,row in out['cases'].items():
        assert row['heldout_measured']==plain['cases'][name]['heldout_measured']
        assert row['parameters']==plain['cases'][name]['parameters']
        assert row['initial_state_A_V']==plain['cases'][name]['initial_state_A_V']
        assert row['heldout_reference']['current_rmse_A']!=row['heldout_measured']['current_rmse_A']


@pytest.mark.parametrize('kind',['same','timestamps','pwm'])
def test_bad_reference_rejected_but_swaps_still_run(experiment,kind):
    data,mapping,_,source=experiment
    ref=copy.deepcopy(data)
    if kind!='same':ref.sha256='e'*64
    if kind=='timestamps':ref.frame[mapping.time] += 1e-8
    if kind=='pwm':ref.frame[mapping.pwm] = 1-ref.frame[mapping.pwm]
    out=run_controls(data,source,result_id='r',source_id='r',reference=ref)
    assert out['status']=='completed' and out['reference']['status']=='rejected'
    assert all(row['heldout_reference'] is None for row in out['cases'].values())


def test_mismatched_data_and_missing_states_are_not_replayed(experiment):
    data,_,baseline,source=experiment
    assert compatible(baseline,source)
    changed=copy.deepcopy(source);changed['options']['vin']=35
    assert not compatible(baseline,changed)
    changed=copy.deepcopy(source);changed['file_sha256']='wrong'
    with pytest.raises(ValueError,match='校验值'):run_controls(data,changed,result_id='r',source_id='r')
    source['diagnostics']['pann'].pop('rollout_initial_state_A_V')
    with pytest.raises(ValueError,match='初始状态'):run_controls(data,source,result_id='r',source_id='r')
    with pytest.raises(ValidationError):
        ChatRequest(message='分析',enable_diagnostic_controls=True,result_id='r',diagnostic_reference={
            'dataset_id':'x','result_id':'different','confirmed_same_experiment':True})


def test_api_selected_source_ownership_reference_cache_and_no_training(experiment,monkeypatch):
    data,mapping,baseline,source=experiment
    monkeypatch.setattr(main,'TOKEN','');monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    owner=str(uuid.uuid4());client=TestClient(main.app,headers={'X-Session-ID':owner})
    raw=(ROOT/'examples/plecs_buck_case16a.csv').read_bytes()
    d=client.post('/api/datasets',files={'file':('source.csv',raw)}).json()
    now=time.time(); current_id=uuid.uuid4().hex;pann_id=uuid.uuid4().hex
    for key,result in [(current_id,baseline),(pann_id,source)]:
        monkeypatch.setitem(main.jobs,key,{'owner':owner,'status':'complete','dataset_id':d['dataset_id'],
            'created':now,'result':result,'config':{'mapping':mapping.model_dump(),'options':result['options']}})
    # Reference uploads use a separate one-file slot and do not evict the source.
    for _ in range(3):
        ref=client.post('/api/datasets?reference=true',files={'file':('reference.csv',raw)}).json()
    assert d['dataset_id'] in main.datasets
    body=dict(message='请分析当前辨识结果的误差，并给出有依据的修改计划。',current_result_analysis=True,
        enable_diagnostic_controls=True,result_id=current_id,dataset_id=d['dataset_id'],mapping=mapping.model_dump(),options=baseline['options'])
    before=set(main.jobs)
    response=client.post('/api/chat',json=body)
    assert response.status_code==200,response.text
    output=response.json()
    assert output['control_checks']['pann_source_result_id']==pann_id
    assert output['answer'].index('已验证的数值效应')<output['answer'].index('可能原因')
    assert set(main.jobs)==before and output['job_id'] is None
    other=TestClient(main.app,headers={'X-Session-ID':str(uuid.uuid4())})
    assert other.post('/api/chat',json=body).status_code==422
    other_ref=other.post('/api/datasets?reference=true',files={'file':('reference.csv',raw)}).json()
    body['diagnostic_reference']={'dataset_id':other_ref['dataset_id'],'result_id':current_id,'confirmed_same_experiment':True}
    output=client.post('/api/chat',json=body).json()['control_checks']
    assert output['status']=='completed' and output['reference']['status']=='rejected'


def test_actual_control_evidence_reaches_model_before_answer(experiment,monkeypatch):
    data,_,_,source=experiment
    report=run_controls(data,source,result_id='r',source_id='r')
    monkeypatch.setenv('DEEPSEEK_API_KEY','test-only');calls=[];payloads=[]
    class Provider:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,**kwargs):
            payloads.append(kwargs['json'])
            return httpx.Response(200,request=httpx.Request('POST',url),json={'choices':[{'message':{'content':'已验证的数值效应在前，可能原因待验证。'}}]})
    monkeypatch.setattr(chat.httpx,'AsyncClient',Provider)
    async def invoke(name):
        calls.append(name)
        return report if name=='run_diagnostic_controls' else source
    response=asyncio.run(chat.answer(ChatRequest(message='分析当前结果',current_result_analysis=True,
        result_id='r',enable_diagnostic_controls=True),invoke))
    assert calls==['get_identification_result','run_diagnostic_controls']
    context=json.loads(payloads[0]['messages'][1]['content'].split('：',1)[1])
    assert context['control_checks']==report and response['control_checks']==report
    assert '物理原因已证实' in payloads[0]['messages'][0]['content']


def test_controller_controls_reproduce_closed_loop_without_heldout_drive():
    from test_controller import sample, config
    data,mapping=sample()
    baseline=identify(data,mapping,config())
    source=copy.deepcopy(baseline);source['method']=source['options']['method']='pann'
    source['diagnostics']['pann']={'initial_parameters':list(baseline['parameters'].values()),
        'rollout_initial_state_A_V':baseline['diagnostics']['rollout_initial_state_A_V']}
    out=run_controls(data,source,result_id='r',source_id='r')
    assert out['pwm_source']=='controller'
    for case in out['cases'].values():
        assert case['heldout_measured']==pytest.approx(baseline['metrics'])


@pytest.mark.parametrize('message,execute',[
    ('固定积分参数，改用 PANN 初值做对照',True),('运行这些对照',True),
    ('Run diagnostic controls',True),('不要运行对照，只分析当前结果',False),
    ('参数初值交换的原理是什么',False),
    ("Don't run controls, explain the current result",False)])
def test_control_commands_never_change_live_settings_or_retrain(experiment,monkeypatch,message,execute):
    monkeypatch.delenv('DEEPSEEK_API_KEY',raising=False)
    _,_,_,source=experiment;calls=[]
    async def invoke(name):
        calls.append(name)
        assert name in {'get_identification_result','run_diagnostic_controls'}
        return {'status':'unavailable','error':'测试：数据不足'} if name=='run_diagnostic_controls' else source
    response=asyncio.run(chat.answer(ChatRequest(message=message,result_id='r',enable_diagnostic_controls=True),invoke))
    assert ('run_diagnostic_controls' in calls)==execute
    assert response['job_id'] is None and 'ui_action' not in response
