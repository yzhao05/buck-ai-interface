"""The Harness interprets prose; the adapter validates typed actions and scope."""
import asyncio
import json
import pytest
from backend import chat, harness_runtime
from backend.schemas import ChatRequest


@pytest.mark.parametrize('mode', ['fast', 'thinking', 'auto'])
@pytest.mark.parametrize('message', [
    '不要打开文件，不运行仿真，只检查连接和目录。',
    '只看目录，别做实验。',
    'Do not simulate or open a model; check the connection and search.',
    '为什么上次失败？请先查旧消息，再检查连接。',
    '请分析当前辨识结果的误差，并给出有依据的修改计划。',
])
def test_wording_does_not_prune_tools(monkeypatch, mode, message):
    monkeypatch.setenv('DEEPSEEK_API_KEY','test-only')
    calls=[]
    async def invoke(name,args=None):
        calls.append(name)
        return {'status':'ready'}
    async def agent(messages,tools,execute,budget):
        roster={t['function']['name'] for t in tools}
        assert {'connect_plecs','search_plecs_models','open_plecs_model','run_plecs_experiment','search_conversation'} <= roster
        assert budget.thinking == (mode!='fast')
        # The model chooses only the requested read/connection operations.
        await execute('check_plecs_connection',{})
        await execute('search_plecs_models',{})
        return 'Connection checked.'
    monkeypatch.setattr(harness_runtime,'run',agent)
    result=asyncio.run(chat.answer(ChatRequest(message=message,reasoning_mode=mode),invoke))
    assert result['mode']=='deepseek'
    assert calls==['check_plecs_connection','search_plecs_models']


def test_model_proposal_not_reparsed_and_no_old_configuration_execution(monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY','test-only')
    async def invoke(*args):
        pytest.fail('No numerical action may follow a pending UI patch')
    async def agent(messages,tools,execute,budget):
        assert 'configure_identification' in {t['function']['name'] for t in tools}
        proposal=await execute('configure_identification',{'open_panel':True,'changes':{'method':'weighted_integral','epochs':120},'start_after_apply':False})
        assert proposal['status']=='pending_ui'
        assert 'error' in await execute('run_plecs_experiment',{})
        return 'Settings proposed.'
    monkeypatch.setattr(harness_runtime,'run',agent)
    result=asyncio.run(chat.answer(ChatRequest(message='Use the alternative estimator we discussed.',
        ui_context={'request_id':'r','options':{}}),invoke))
    assert result['ui_action']['changes']=={'method':'weighted_integral','epochs':120}


@pytest.mark.parametrize('changes', [{'epochs':999},{'start_s':2,'end_s':1},{'vin':None}])
def test_invalid_structured_configuration_is_rejected(monkeypatch,changes):
    monkeypatch.setenv('DEEPSEEK_API_KEY','test-only')
    async def agent(messages,tools,execute,budget):
        output=await execute('configure_identification',{'open_panel':True,'changes':changes,'start_after_apply':False})
        assert 'error' in output
        return 'Invalid settings.'
    monkeypatch.setattr(harness_runtime,'run',agent)
    result=asyncio.run(chat.answer(ChatRequest(message='Apply the requested values.',ui_context={'request_id':'r','options':{}}),None))
    assert result['mode']=='deepseek' and result['ui_action'] is None


def test_explicit_result_scope_still_isolates_historical_data(monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY','test-only')
    async def invoke(name):return {'parameters':{},'metrics':{}}
    async def agent(messages,tools,execute,budget):
        names={t['function']['name'] for t in tools}
        assert not names & {'start_identification','run_plecs_experiment','search_conversation'}
        assert 'search_knowledge' in names
        return 'Result analysis.'
    monkeypatch.setattr(harness_runtime,'run',agent)
    result=asyncio.run(chat.answer(ChatRequest(message='arbitrary wording',current_result_analysis=True,result_id='r'),invoke))
    assert result['mode']=='deepseek'


@pytest.mark.parametrize('source,version', [('measured',2),('both',3)])
def test_agent_can_propose_batch_without_keyword_router(monkeypatch,source,version):
    monkeypatch.setenv('DEEPSEEK_API_KEY','test-only')
    sources=['measured','controller'] if source=='both' else ['measured']
    choice_message=f'Use both weighted integral and PANN with {", ".join(sources)} PWM.'
    async def agent(messages,tools,execute,budget):
        output=await execute('propose_identification_plan',{
            'execute':True,'clarification':'','filename':None,'pwm_source':source,
            'pwm_evidence':choice_message,'mode':'LC','resistance_ohm':6,
            'execution_selection':{'methods':['weighted_integral','pann'],'pwm_sources':sources,
                                   'method_evidence':choice_message,'pwm_evidence':choice_message}})
        assert output['status']=='pending_ui'
        return 'Plan proposed.'
    monkeypatch.setattr(harness_runtime,'run',agent)
    result=asyncio.run(chat.answer(ChatRequest(message='Proceed with the agreed combination.',
        history=[{'role':'user','content':choice_message}],
        ui_context={'request_id':'r','options':{}}),None))
    assert result['ui_action']['version']==version
    assert result['ui_action']['changes']['mode']=='LC'


def test_configuration_rejects_unknown_uploaded_asset(monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY','test-only')
    async def agent(messages,tools,execute,budget):
        output=await execute('configure_identification',{'open_panel':True,'changes':{},
            'assets':{'csv_id':'not-owned'},'start_after_apply':True})
        assert 'error' in output
        return 'File unavailable.'
    monkeypatch.setattr(harness_runtime,'run',agent)
    result=asyncio.run(chat.answer(ChatRequest(message='Use that file.',ui_context={'request_id':'r','options':{}}),None))
    assert result['ui_action'] is None


@pytest.mark.parametrize('mode', ['fast','thinking'])
def test_cancel_is_an_agent_action_and_evidence_is_loaded_on_demand(monkeypatch,mode):
    monkeypatch.setenv('DEEPSEEK_API_KEY','test-only')
    calls=[]
    async def invoke(name,args=None):
        calls.append(name)
        return {'status':'completed','result_id':'result-1','metrics':{'value':123}}
    async def agent(messages,tools,execute,budget):
        context=json.loads(messages[1]['content'].split('：',1)[1])
        assert context['current_result_id']=='result-1'
        assert calls==[]
        assert (await execute('get_diagnostic_evidence',{}))['metrics']['value']==123
        assert (await execute('cancel_pending_identification',{}))['status']=='pending_ui'
        return 'Pending work cancelled.'
    monkeypatch.setattr(harness_runtime,'run',agent)
    result=asyncio.run(chat.answer(ChatRequest(message='Leave the remaining work for another day.',
        reasoning_mode=mode,result_id='result-1',ui_context={'request_id':'r','options':{}}),invoke))
    assert result['ui_action']=={'id':'r','type':'cancel_pending_identification'}
    assert calls==['get_diagnostic_evidence']


def test_plecs_reads_and_side_effects_are_separate(tmp_path,monkeypatch):
    from backend import plecs_local,plecs_execution
    from test_agent_plecs import MODEL
    monkeypatch.setattr(plecs_local,'ROOT',tmp_path/'settings')
    folder=tmp_path/'models';folder.mkdir()
    model=folder/'buck.plecs';model.write_text(MODEL)
    outside=tmp_path/'outside.plecs';outside.write_text(MODEL)
    plecs_local.configure('owner','',str(folder))
    monkeypatch.setattr(plecs_local,'listening',lambda:False)
    def forbidden(*args):pytest.fail('Read-only operations must not launch or register a model')
    real_register=plecs_local.register
    monkeypatch.setattr(plecs_local,'register',forbidden)
    monkeypatch.setattr(plecs_local,'ensure_running',forbidden)
    def run(tool,**args):return plecs_execution.execute('owner',{'tool':tool,'arguments':args,'context':'current'})
    assert run('check_plecs_connection')=={'status':'unavailable','port':1080,'started':False}
    found=run('search_plecs_models',hints=['buck'])
    assert found['match']['path']==str(model) and plecs_local.settings('owner')['profiles']=={}
    monkeypatch.setattr(plecs_local,'register',real_register)
    # A search result/prior-turn path is accepted without reparsing the current message.
    assert run('select_plecs_model',path=str(model))['status']=='registered'
    with pytest.raises(ValueError,match='文件夹'):
        run('select_plecs_model',path=str(outside))
    with pytest.raises(ValueError,match='文件夹'):
        run('select_plecs_model',path=str(folder/'..'/'outside.plecs'))
    monkeypatch.setattr(plecs_local,'ensure_running',lambda _: {'status':'ready','started':True})
    monkeypatch.setattr(plecs_local,'search_models',forbidden)
    assert run('connect_plecs')['started'] is True


@pytest.mark.parametrize('mode',['fast','thinking','auto'])
def test_ordinary_chat_never_prefetches_and_pending_patch_allows_independent_reads(monkeypatch,mode):
    monkeypatch.setenv('DEEPSEEK_API_KEY','test-only')
    calls=[]
    async def invoke(name,args=None):
        calls.append(name)
        return {'status':'ready'}
    async def agent(messages,tools,execute,budget):
        assert calls==[], 'No implicit reads before the Agent selects a tool'
        assert (await execute('configure_identification',{'open_panel':True,'changes':{},'start_after_apply':False}))['status']=='pending_ui'
        found=await execute('search_knowledge',{'query':'Buck ESR ripple'})
        assert found['count']>0 and 'error' not in found
        catalogue=await execute('list_knowledge',{'category':'formula'})
        assert catalogue['count']>0 and 'error' not in catalogue
        selected=await execute('read_knowledge',{'ids':[catalogue['entries'][0]['id']]})
        assert selected['count']==1 and 'error' not in selected
        assert 'error' not in await execute('search_conversation',{'query':'prior'})
        assert 'error' not in await execute('check_plecs_connection',{})
        assert 'error' in await execute('start_identification',{})
        assert 'error' in await execute('get_waveform_summary',{})
        return 'Configuration proposed with supporting evidence.'
    monkeypatch.setattr(harness_runtime,'run',agent)
    result=asyncio.run(chat.answer(ChatRequest(message='Use the agreed setting and explain its basis.',reasoning_mode=mode,
        dataset_id='d',mapping={'time':'t','current':'i','voltage':'v'},result_id='r',ui_context={'request_id':'r','options':{}}),invoke))
    assert result['mode']=='deepseek'
    assert calls==['check_plecs_connection']


@pytest.mark.parametrize('mode',['fast','thinking'])
def test_legacy_client_without_ui_context_does_not_trigger_reads(monkeypatch,mode):
    monkeypatch.setenv('DEEPSEEK_API_KEY','test-only')
    async def forbidden(*args):pytest.fail('Agent did not request a read')
    async def agent(*args):return 'No data access needed.'
    monkeypatch.setattr(harness_runtime,'run',agent)
    result=asyncio.run(chat.answer(ChatRequest(message='不要读取数据，只解释工具用途',reasoning_mode=mode,
        dataset_id='expired',mapping={'time':'t','current':'i','voltage':'v'},result_id='expired'),forbidden))
    assert result['mode']=='deepseek'


def test_directory_search_preserves_registered_catalog_state(monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY','test-only')
    calls=[]
    async def invoke(name,args=None):
        calls.append(name)
        return {'models':{'m':{}}} if name=='get_plecs_models' else {'status':'completed'}
    async def agent(messages,tools,execute,budget):
        await execute('get_plecs_models',{})
        await execute('search_plecs_models',{})
        result=await execute('run_plecs_experiment',{'model':'m','parameter':'C','values':[1],
            'hypothesis':'test','expected_observation':'test'})
        assert result['status']=='completed'
        return 'Done.'
    monkeypatch.setattr(harness_runtime,'run',agent)
    result=asyncio.run(chat.answer(ChatRequest(message='Use the registered model after listing the folder.'),invoke))
    assert result['mode']=='deepseek'
    assert calls==['get_plecs_models','search_plecs_models','run_plecs_experiment']


def test_unavailable_waveform_returns_tool_error_and_agent_can_continue(monkeypatch):
    from fastapi.testclient import TestClient
    from backend import main
    import uuid
    monkeypatch.setattr(main,'TOKEN','')
    monkeypatch.setenv('DEEPSEEK_API_KEY','test-only')
    async def agent(messages,tools,execute,budget):
        failed=await execute('get_waveform_summary',{})
        assert failed.get('error')
        found=await execute('search_knowledge',{'query':'Buck output ripple'})
        assert found['count']>0
        return 'The waveform is unavailable; here is the retrieved theory.'
    monkeypatch.setattr(harness_runtime,'run',agent)
    client=TestClient(main.app,headers={'X-Session-ID':str(uuid.uuid4())})
    result=client.post('/api/chat',json={'message':'Check waveform, then explain ripple.','dataset_id':'expired',
        'mapping':{'time':'t','current':'i','voltage':'v'}})
    assert result.status_code==200 and result.json()['mode']=='deepseek'
    assert result.json()['tool_calls'][0]['status']=='未执行'


@pytest.mark.parametrize('delayed', [False, True])
def test_changed_registered_circuit_does_not_reuse_old_experiment(monkeypatch, delayed):
    monkeypatch.setenv('DEEPSEEK_API_KEY','test-only')
    version='a'*64
    executed=[]
    async def invoke(name,args=None):
        nonlocal version
        if name=='get_plecs_models':return {'models':{'m':{'source_sha256':version}}}
        if name=='select_plecs_model':version='b'*64;return {'status':'registered'}
        if name=='get_plecs_job_result':return {'status':'running','bridge_job_id':'job'}
        if name=='run_plecs_experiment':
            executed.append(version)
            if delayed and len(executed)==1:
                return {'status':'uncertain','bridge_job_id':'job'}
            return {'status':'completed','source_sha256':version}
        pytest.fail(name)
    async def agent(messages,tools,execute,budget):
        args={'model':'m','parameter':'C','values':[1],'hypothesis':'test','expected_observation':'test'}
        await execute('get_plecs_models',{})
        await execute('run_plecs_experiment',args)
        assert (await execute('run_plecs_experiment',args))['deduplicated']
        await execute('select_plecs_model',{'path':'updated same path'})
        await execute('get_plecs_models',{})
        updated=await execute('run_plecs_experiment',args)
        assert updated['source_sha256']=='b'*64 and not updated.get('deduplicated')
        assert (await execute('run_plecs_experiment',args))['deduplicated']
        return 'Updated circuit verified.'
    monkeypatch.setattr(harness_runtime,'run',agent)
    result=asyncio.run(chat.answer(ChatRequest(message='Test the updated circuit.'),invoke))
    assert result['mode']=='deepseek' and executed==['a'*64,'b'*64]
