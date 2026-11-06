import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from backend import chat, plecs_local, plecs_diagnostics as rpc
from backend.agent_policy import Budget, BudgetExceeded, evidence
from backend.schemas import ChatRequest


MODEL = '''Plecs {
  Name "test"
  TimeSpan "0.001"
    Component {
      Type Capacitor
      Name "C1"
      Parameter {
        Variable "C"
        Value "0.001"
      }
    }
    Component {
      Type Output
      Name "Out"
    }
}
'''


def test_recursive_unique_and_ambiguous(tmp_path, monkeypatch):
    monkeypatch.setattr(plecs_local,'ROOT',tmp_path/'settings')
    root=tmp_path/'models'; nested=root/'sub'; nested.mkdir(parents=True)
    source=nested/'buck.plecs';source.write_text(MODEL)
    plecs_local.save('a',{'executable':'','model_directory':str(root),'profiles':{}})
    result=plecs_local.search_models('a',['buck.csv'])
    assert result['status']=='selected'
    assert 'C1.C' in result['selection']['parameters']
    (root/'buck.plecs').write_text(MODEL)
    assert plecs_local.search_models('a',['buck.csv'])['status']=='ambiguous'
    assert plecs_local.search_models('a',['unrelated.csv'])['status']=='not_found'
    assert plecs_local.settings('b')['profiles']=={}


def test_simulation_copy_and_bounds(tmp_path, monkeypatch):
    monkeypatch.setattr(plecs_local,'ROOT',tmp_path/'settings')
    monkeypatch.setattr(rpc,'ROOT',tmp_path)
    source=tmp_path/'test.plecs';source.write_text(MODEL)
    profile=plecs_local.register('a',str(source))
    changes=[];closed=[];loaded=[]
    class Fake:
        def load(self,path):loaded.append(path)
        def get(self,*a):return '0.001'
        def set(self,*a):changes.append(a)
        def close(self,name):closed.append(name)
        def simulate(self,name,opts):
            assert opts['SolverOpts']['Timeout']==12
            return {'Time':[0,.0005,.001], 'Values':[[0,1,2 if not changes else 3]]}
    monkeypatch.setattr(rpc,'proxy',lambda:SimpleNamespace(plecs=Fake()))
    args={'model':profile['model'],'parameter':'C1.C','values':[.0011],
          'hypothesis':'C sensitivity','expected_observation':'voltage change'}
    report=rpc.experiment(args,'a','result-1')
    assert report['status']=='completed'
    assert report['cases'][1]['signals']['output_1']['rms_change_from_baseline']>0
    assert source.read_text()==MODEL
    assert loaded[0]!=str(source) and closed[0].startswith('agent_')
    assert report['source_result_id']=='result-1'
    with pytest.raises(ValueError):rpc.experiment({**args,'values':[99]},'a')
    with pytest.raises(ValueError):rpc.experiment(args,'other-owner')


def test_budget_and_missing_evidence(monkeypatch):
    monkeypatch.setenv('AGENT_COST_LIMIT_USD','0.00001')
    with pytest.raises(BudgetExceeded):Budget(True).reserve({'messages':[{'content':'hello'}]})
    assert evidence({'diagnostics':{}})['missing']
    history=[{'epoch':i,'loss':1/(i+1),'C_F':.001} for i in range(500)]
    result={'diagnostics':{'pann':{'epoch_history':history}}}
    out=evidence(result)
    assert out['trajectory'][0]==history[0] and out['trajectory'][-1]==history[-1]
    assert len(out['trajectory'])<=81


@pytest.mark.parametrize('message,thinking',[('怎么上传 CSV',False),('为什么 C 误差很大',True),('比较两次实验',True)])
def test_thinking_and_reasoning_replay(monkeypatch,message,thinking):
    monkeypatch.setenv('DEEPSEEK_API_KEY','mock')
    async def classify(request):
        return {'task':'diagnosis' if thinking else 'guidance','label':'test','secondary_tasks':[],'mode':'agent'}
    monkeypatch.setattr(chat,'classify',classify)
    captured=[]
    class Client:
        def __init__(self,**kw):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*a):pass
        async def post(self,url,**kw):
            payload=copy.deepcopy(kw['json']);captured.append(payload)
            if len(captured)==1:
                msg={'role':'assistant','content':None,'reasoning_content':'opaque-provider-state',
                     'tool_calls':[{'id':'one','type':'function','function':{'name':'search_knowledge','arguments':'{"query":"Buck parameter identification"}'}}]}
            else:msg={'role':'assistant','content':'checked'}
            return SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'choices':[{'message':msg}]})
    monkeypatch.setattr(chat.httpx,'AsyncClient',Client)
    async def invoke(*args):return None
    result=asyncio.run(chat.answer(ChatRequest(message=message),invoke))
    assert result['answer']=='checked'
    assert captured[0]['thinking']['type']==('enabled' if thinking else 'disabled')
    replay=[m for m in captured[1]['messages'] if m.get('tool_calls')][0]
    assert replay['reasoning_content']=='opaque-provider-state'
    assert result['agent_budget']['reserved_cost_usd']<=.5


def test_launch_once_and_ready(monkeypatch):
    states=iter([False,False,True])
    monkeypatch.setattr(plecs_local,'listening',lambda:next(states))
    monkeypatch.setattr(plecs_local,'settings',lambda owner:{'executable':'C:/PLECS.exe'})
    monkeypatch.setattr(plecs_local,'PROCESS',None)
    monkeypatch.setattr(plecs_local.time,'sleep',lambda _:None)
    calls=[]
    monkeypatch.setattr(plecs_local.subprocess,'Popen',lambda *a,**kw:calls.append((a,kw)) or SimpleNamespace(poll=lambda:None))
    assert plecs_local.ensure_running('a')['status']=='ready'
    assert len(calls)==1 and calls[0][1]['shell'] is False


def test_settings_search_experiment_chat_flow(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from backend import main
    import uuid
    monkeypatch.setattr(main,'TOKEN','')
    monkeypatch.setenv('APP_ENV','development')
    monkeypatch.delenv('RENDER',raising=False)
    monkeypatch.setenv('DEEPSEEK_API_KEY','mock')
    monkeypatch.setattr(plecs_local,'ROOT',tmp_path/'settings')
    exe=tmp_path/'PLECS.exe';exe.write_bytes(b'test-only')
    root=tmp_path/'models';(root/'nested').mkdir(parents=True)
    (root/'nested'/'buck.plecs').write_text(MODEL)
    owner=str(uuid.uuid4());client=TestClient(main.app);client.headers['X-Session-ID']=owner
    assert client.post('/api/plecs/settings',json={'executable':str(exe),'model_directory':str(root)}).status_code==200
    monkeypatch.setattr(plecs_local,'ensure_running',lambda owner:{'status':'ready'})
    async def classify(request):return {'task':'diagnosis','label':'diagnosis','secondary_tasks':[],'mode':'agent'}
    monkeypatch.setattr(chat,'classify',classify)
    def simulate(args,who,result_id):
        assert who==owner and args['model'] in plecs_local.settings(who)['profiles']
        return {'status':'completed','experiment_id':'test','cases':[]}
    monkeypatch.setattr(rpc,'experiment',simulate)
    class Provider:
        def __init__(self,**kw):self.round=0
        async def __aenter__(self):return self
        async def __aexit__(self,*a):pass
        async def post(self,url,**kw):
            self.round+=1
            if self.round==1:
                steps=[('connect_plecs',{}),('search_plecs_models',{})]
            elif self.round==2:steps=[('get_plecs_models',{})]
            elif self.round==3:
                model=next(iter(plecs_local.settings(owner)['profiles']))
                steps=[('run_plecs_experiment',{'model':model,'parameter':'C1.C','values':[.0011],
                    'hypothesis':'sensitivity','expected_observation':'waveform change'})]
            else:steps=[]
            msg={'role':'assistant','content':'完成数值对照' if not steps else None,'reasoning_content':'opaque'}
            if steps:msg['tool_calls']=[{'id':str(i),'type':'function','function':{'name':name,'arguments':json.dumps(args)}} for i,(name,args) in enumerate(steps)]
            return SimpleNamespace(raise_for_status=lambda:None,json=lambda:{'choices':[{'message':msg}]})
    monkeypatch.setattr(chat.httpx,'AsyncClient',Provider)
    response=client.post('/api/chat',json={'message':'请用 buck.plecs 做对比实验'})
    assert response.status_code==200
    data=response.json()
    assert data['answer']=='完成数值对照' and data['plecs_experiments'][0]['status']=='completed'
    assert [t['name'] for t in data['tool_calls']]==['connect_plecs','search_plecs_models','get_plecs_models','run_plecs_experiment']
    monkeypatch.setenv('APP_ENV','production')
    assert client.get('/api/plecs/settings').status_code==403
