import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
import uuid
import os
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from backend import harness_runtime, plecs_bridge, simulation_analysis, agent_tasks, main
from backend.agent_policy import Budget
from backend.diagnosis_notebook import Notebook
from backend.schemas import ChatRequest, FitOptions
from conftest import REAL_RUN


@pytest.mark.parametrize('thinking', [False, True])
def test_official_harness_mcp_stream_resume_and_tool_roster(monkeypatch, thinking):
    requests, executions, emitted = [], [], []
    class Provider(BaseHTTPRequestHandler):
        def log_message(self,*_): pass
        def do_POST(self):
            payload=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(payload)
            called=any(any(b.get('type')=='tool_result' for b in item['content']) for item in payload['messages'] if isinstance(item['content'],list))
            block={'type':'text','text':''} if called else {'type':'tool_use','id':'c1','name':'mcp__buck__probe','input':{}}
            delta={'type':'text_delta','text':'verified'} if called else {'type':'input_json_delta','partial_json':'{}'}
            frames=[{'type':'message_start','message':{'id':'m1','type':'message','role':'assistant','content':[],'model':'deepseek-flash','stop_reason':None,'stop_sequence':None,'usage':{'input_tokens':10,'output_tokens':0}}},
                {'type':'content_block_start','index':0,'content_block':block}, {'type':'content_block_delta','index':0,'delta':delta},
                {'type':'content_block_stop','index':0},{'type':'message_delta','delta':{'stop_reason':'end_turn' if called else 'tool_use','stop_sequence':None},'usage':{'output_tokens':5}}, {'type':'message_stop'}]
            raw=''.join('event: '+f['type']+'\ndata: '+json.dumps(f)+'\n\n' for f in frames).encode()
            self.send_response(200);self.send_header('Content-Type','text/event-stream');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    server=ThreadingHTTPServer(('127.0.0.1',0),Provider)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    monkeypatch.setenv('DEEPSEEK_API_KEY','local-mock-only')
    monkeypatch.setenv('HARNESS_BASE_URL','http://127.0.0.1:'+str(server.server_port))
    async def invoke(name,args):
        executions.append(name)
        return {'status':'completed','value':12}
    async def run():
        identifier=uuid.uuid4().hex
        state={'id':identifier,'emit':lambda kind,data:emitted.append((kind,data))}
        token=harness_runtime.scope.set(state)
        tools=[{'type':'function','function':{'name':'probe','description':'Read a value','parameters':{'type':'object','properties':{}}}}]
        try:
            messages=[{'role':'system','content':'Use probe then answer'},{'role':'user','content':'Test'}]
            if not thinking:
                # The old adapter could persist this marker before the SDK saved any session.
                home = harness_runtime.ROOT / identifier
                home.mkdir(parents=True)
                (home/'session.started').touch()
                state.update(resume=True, can_restart_session=False)
                from backend.provider_errors import SessionRestoreError
                with pytest.raises(SessionRestoreError, match='missing_after_tools'):
                    await REAL_RUN(messages,tools,invoke,Budget(thinking))
                assert not requests and not executions
                state['can_restart_session']=True
            assert await REAL_RUN(messages,tools,invoke,Budget(thinking))=='verified'
            # A successful response/close alone does not prove persistence:
            # the Windows packaged runtime can swallow native-module errors.
            home = harness_runtime.ROOT / identifier
            # The SDK stores the escaped absolute cwd inside its directory name.
            # Windows paths can exceed MAX_PATH even for a short pytest base;
            # ordinary pathlib glob then silently omits the existing files.
            durable_home = Path('\\\\?\\' + str(home.resolve())) if os.name == 'nt' else home
            saved = list((durable_home / 'sessions').rglob('session*.jsonl'))
            assert len(saved) == 1, 'Harness must persist the session before restart'
            before_resume = saved[0].read_text(encoding='utf-8')
            assert before_resume.strip()
            for line in before_resume.splitlines():
                json.loads(line)
            assert json.loads(before_resume.splitlines()[0])['cwd'] == str(home.resolve())
            state['resume']=True
            assert await REAL_RUN(messages,tools,invoke,Budget(thinking))=='verified'
            assert len(saved[0].read_text(encoding='utf-8')) > len(before_resume)
        finally:harness_runtime.scope.reset(token)
    try:asyncio.run(run())
    finally:server.shutdown();server.server_close()
    assert executions==['probe']
    assert len(requests)==3
    assert all([tool['name'] for tool in p['tools']]==['mcp__buck__probe'] for p in requests)
    assert all(p['thinking']['type']==('enabled' if thinking else 'disabled') for p in requests)
    assert ('text_delta',{'text':'verified'}) in emitted


def test_bridge_owner_auth_claim_once_and_result_retransmit(tmp_path,monkeypatch):
    monkeypatch.setattr(plecs_bridge,'ROOT',tmp_path/'bridge')
    first,second=str(uuid.uuid4()),str(uuid.uuid4())
    one,two=plecs_bridge.pair(first),plecs_bridge.pair(second)
    with pytest.raises(ValueError):plecs_bridge.claim(first,two['token'])
    plecs_bridge.claim(first,one['token'])
    async def run():
        pending=asyncio.create_task(plecs_bridge.dispatch(first,{'tool':'get_plecs_models','context':'c','request_id':'once'},timeout=5))
        await asyncio.sleep(.05)
        assert plecs_bridge.claim(second,two['token'])['job'] is None
        job=plecs_bridge.claim(first,one['token'])['job']
        assert plecs_bridge.claim(first,one['token'])['job'] is None
        result={'models':{'buck':{}}}
        with pytest.raises(ValueError):plecs_bridge.complete(second,two['token'],job['id'],result)
        plecs_bridge.complete(first,one['token'],job['id'],result)
        plecs_bridge.complete(first,one['token'],job['id'],result)
        with pytest.raises(ValueError):plecs_bridge.complete(first,one['token'],job['id'],{'different':True})
        assert await pending==result
        assert await plecs_bridge.dispatch(first,{'tool':'get_plecs_models','context':'c','request_id':'once'},timeout=5)==result
    asyncio.run(run())


def test_bridge_routes_require_pair_token_even_without_app_token(tmp_path,monkeypatch):
    monkeypatch.setattr(plecs_bridge,'ROOT',tmp_path/'bridge')
    owner=str(uuid.uuid4());client=TestClient(main.app,headers={'X-Session-ID':owner})
    peer=client.post('/api/bridge/pair').json()
    assert client.post('/api/bridge/claim',headers={'X-Bridge-Token':'wrong'}).status_code==401
    assert client.post('/api/bridge/claim',headers={'X-Bridge-Token':peer['token']}).status_code==200
    monkeypatch.setattr(main,'TOKEN','application-secret')
    assert client.post('/api/bridge/claim',headers={'X-Bridge-Token':peer['token']}).status_code==200
    assert client.post('/api/bridge/pair').status_code==401


def test_interrupted_bridge_receipt_accepts_late_results_without_reexecution(tmp_path,monkeypatch):
    monkeypatch.setattr(plecs_bridge,'ROOT',tmp_path/'bridge')
    owner=str(uuid.uuid4());peer=plecs_bridge.pair(owner)
    plecs_bridge.claim(owner,peer['token'])
    async def run():
        task=asyncio.create_task(plecs_bridge.dispatch(owner,{'tool':'run_plecs_experiment','context':'selected'},timeout=5))
        await asyncio.sleep(.05)
        job=plecs_bridge.claim(owner,peer['token'])['job']
        task.cancel()
        receipt=await task
        assert receipt['bridge_job_id']==job['id']
        assert plecs_bridge.result(owner,job['id'],'selected')['status']=='uncertain'
        plecs_bridge.complete(owner,peer['token'],job['id'],{'status':'completed','experiment_id':'actual'})
        assert plecs_bridge.result(owner,job['id'],'selected')['result']['experiment_id']=='actual'
        assert plecs_bridge.claim(owner,peer['token'])['job'] is None
        with pytest.raises(ValueError):plecs_bridge.result(owner,job['id'],'another-circuit')
    asyncio.run(run())


def test_notebook_evidence_systematic_floor_and_reopening(tmp_path):
    book=Notebook(tmp_path/'book.json','run1')
    args={'hypotheses':[{'id':'bias','cause':'model bias','status':'systematic_floor','supporting_evidence':['e1'],'assessment':'Residual bias persists'}],
          'next_step':'Stop repeated equivalent experiments','stop_reason':'systematic_floor','rationale':'No additional discrimination'}
    with pytest.raises(ValueError):book.update(args)
    book.evidence('controls',{'bias':.2})
    book.update(args)
    restored=Notebook(book.path,'run1')
    assert restored.data['stop_reason']=='systematic_floor'
    args['hypotheses'][0]['status']='open';args['stop_reason']='continue'
    restored.update(args)
    assert restored.data['hypotheses'][0]['status']=='open'
    assert Notebook(book.path,'run2').data['hypotheses']==[]
    failed=restored.evidence('simulation',{'status':'failed','error':'offline'})
    args['hypotheses'][0].update(status='systematic_floor',supporting_evidence=[failed])
    with pytest.raises(ValueError):restored.update(args)


def test_simulation_artifacts_reidentification_truth_and_dedupe(tmp_path,monkeypatch):
    monkeypatch.setattr(simulation_analysis,'ROOT',tmp_path/'sim')
    identifier='agent_'+'a'*32
    raw='time_s,output_1,output_2,output_3\n'+''.join(f'{i*1e-6},1,5,{i%2}\n' for i in range(200))
    report={'status':'completed','experiment_id':identifier,'source_result_id':'r1','design':{'parameter':'C1.C'},
            'resolved_base_parameters':{'L1.L':.002,'C1.C':.001},
            'cases':[{'kind':'baseline','value':.001},{'kind':'intervention','value':.0012}],
            '_artifacts':[raw,raw], '_profile':{'description':'Buck','parameters':{'L1.L':{'field':'L','nominal':.002},'C1.C':{'field':'C','nominal':.001}}}}
    binding={'model_name':'Buck.plecs','current_port':1,'voltage_port':2,'pwm_port':3,'L_parameter':'L1.L','C_parameter':'C1.C','confirmed':True}
    received=simulation_analysis.ingest('owner',report,FitOptions(method='weighted_integral'),binding,None)
    assert '_artifacts' not in received and received['artifacts_received']==2
    runs=[]
    def fit(data,mapping,options,**kw):
        assert mapping.current=='output_1' and options.method=='weighted_integral'
        runs.append(1)
        return {'parameters':{'L_H':.002,'C_F':.0011,'R_ohm':6},'metrics':{}}
    monkeypatch.setattr(simulation_analysis,'identify',fit)
    result=simulation_analysis.evaluate('owner',identifier,'r1')
    assert result['cases'][0]['relative_error_pct']['C']==pytest.approx(10)
    assert result['cases'][1]['relative_error_pct']['C']==pytest.approx(100/12)
    assert result['cases'][1]['ground_truth']['C_F']==.0012
    assert simulation_analysis.evaluate('owner',identifier,'r1')['cached'] and len(runs)==2
    with pytest.raises(ValueError):simulation_analysis.evaluate('owner',identifier,'other')
    with pytest.raises(ValueError):simulation_analysis.directory('owner','../escape')


def test_durable_task_result_and_cross_owner_isolation(tmp_path,monkeypatch):
    monkeypatch.setattr(agent_tasks,'ROOT',tmp_path/'tasks')
    async def run():
        async def answer(body):return {'mode':'deepseek','answer':body.message}
        created=agent_tasks.launch('owner',ChatRequest(message='hello'),answer)
        with pytest.raises(ValueError):agent_tasks.read('other',created['id'])
        await agent_tasks.running[created['id']]
        assert agent_tasks.read('owner',created['id'])['result']['answer']=='hello'
    asyncio.run(run())


def test_cloud_chat_bridge_simulation_return_and_error_comparison(tmp_path,monkeypatch):
    from backend import chat, plecs_local, plecs_execution, plecs_diagnostics
    from test_agent_plecs import MODEL
    monkeypatch.setattr(plecs_bridge,'ROOT',tmp_path/'bridge')
    monkeypatch.setattr(plecs_local,'ROOT',tmp_path/'settings')
    monkeypatch.setattr(plecs_diagnostics,'ROOT',tmp_path)
    monkeypatch.setattr(simulation_analysis,'ROOT',tmp_path/'analysis')
    model=tmp_path/'buck.plecs'
    model.write_text(MODEL.replace('    Component {\n      Type Output',
        '    Component {\n      Type Inductor\n      Name "L1"\n      Parameter {\n        Variable "L"\n        Value "0.002"\n      }\n    }\n    Component {\n      Type Output'),encoding='utf-8')
    monkeypatch.setattr(main,'TOKEN','private-app-token-123456')
    monkeypatch.setenv('APP_ENV','production')
    monkeypatch.setenv('DEEPSEEK_API_KEY','mock-only')
    owner=str(uuid.uuid4())
    client=TestClient(main.app,headers={'X-Session-ID':owner,'Authorization':'Bearer '+main.TOKEN})
    peer=client.post('/api/bridge/pair').json()
    binding={'model_name':'buck.plecs','current_port':1,'voltage_port':2,'pwm_port':3,
             'L_parameter':'L1.L','C_parameter':'C1.C','confirmed':True}
    assert client.post('/api/bridge/settings',json={'executable':'','model_directory':str(tmp_path),'analysis_binding':binding}).status_code==200
    worker=TestClient(main.app,headers={'X-Session-ID':owner,'X-Bridge-Token':peer['token']})
    worker.post('/api/bridge/heartbeat')
    monkeypatch.setattr(plecs_local,'ensure_running',lambda _: {'status':'ready'})
    class RPC:
        value=.001
        def load(self,*_):pass
        def close(self,*_):pass
        def get(self,*_):return self.value
        def set(self,component,field,value):self.value=float(value)
        def simulate(self,name,options):
            t=options['SolverOpts']['OutputTimes']
            return {'Time':t,'Values':[[1.]*len(t),[5+self.value]*len(t),[i%2 for i in range(len(t))]]}
    monkeypatch.setattr(plecs_diagnostics,'proxy',lambda:SimpleNamespace(plecs=RPC()))
    monkeypatch.setattr(simulation_analysis,'identify',lambda *a,**kw:{'parameters':{'L_H':.002,'C_F':.00105,'R_ohm':6},'metrics':{}})
    async def evaluation(owner,identifier,result_id):return simulation_analysis.evaluate(owner,identifier,result_id)
    monkeypatch.setattr(simulation_analysis,'evaluate_async',evaluation)
    collected=[]
    async def scenario(messages,tools,invoke,budget):
        connected=await invoke('connect_plecs',{})
        assert connected['status']=='ready'
        found=await invoke('search_plecs_models',{'hints':['buck.plecs']})
        await invoke('select_plecs_model',{'path':found['match']['path']})
        catalog=await invoke('get_plecs_models',{})
        selected=next(iter(catalog['models']))
        args={'model':selected,'parameter':'C1.C','values':[.0011],'hypothesis':'Sensitivity','expected_observation':'Parameter error changes'}
        experiment=await invoke('run_plecs_experiment',args)
        assert experiment['artifacts_received']==2
        repeat=await invoke('run_plecs_experiment',args)
        assert repeat['deduplicated']
        result=await invoke('evaluate_plecs_experiment',{'experiment_id':experiment['experiment_id']})
        collected.append(result)
        return 'Completed model comparison'
    monkeypatch.setattr(harness_runtime,'run',scenario)
    stop=threading.Event();claimed=[]
    def companion():
        while not stop.is_set():
            job=worker.post('/api/bridge/claim').json().get('job')
            if job:
                claimed.append(job['payload']['tool'])
                config=job['payload']['settings']
                plecs_local.configure(owner,config['executable'],config['model_directory'])
                result=plecs_execution.execute(owner,job['payload'])
                assert worker.post('/api/bridge/complete/'+job['id'],json={'result':result}).status_code==200
            stop.wait(.05)
    thread=threading.Thread(target=companion,daemon=True);thread.start()
    try:
        response=client.post('/api/chat',json={'message':'用 buck.plecs 排查误差','circuit_filename':'buck.plecs','options':{'method':'weighted_integral'}})
        assert response.json()['mode']=='deepseek',response.json()
    finally:stop.set();thread.join(timeout=5)
    assert claimed==['connect_plecs','search_plecs_models','select_plecs_model','get_plecs_models','run_plecs_experiment']
    assert collected[0]['cases'][0]['relative_error_pct']['C']==pytest.approx(5)
    assert collected[0]['cases'][1]['relative_error_pct']['C']==pytest.approx(100/22)
    assert 'error_change_percentage_points' in collected[0]['cases'][1]
