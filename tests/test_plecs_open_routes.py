"""Exercise the same folder/open workflow through both supported transports."""
import contextlib
import threading
import uuid
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from backend import main, harness_runtime, plecs_bridge, plecs_execution, plecs_local, plecs_diagnostics
from test_agent_plecs import MODEL


@pytest.mark.parametrize('mode', ['fast','thinking','auto'])
@pytest.mark.parametrize('remote', [False,True])
def test_folder_paging_selection_and_open(tmp_path,monkeypatch,mode,remote):
    monkeypatch.setattr(main,'TOKEN','')
    monkeypatch.setenv('APP_ENV','development')
    monkeypatch.delenv('RENDER',raising=False)
    monkeypatch.setenv('DEEPSEEK_API_KEY','mock')
    monkeypatch.setattr(plecs_local,'ROOT',tmp_path/'settings')
    monkeypatch.setattr(plecs_bridge,'ROOT',tmp_path/'bridge')
    monkeypatch.setattr(plecs_diagnostics,'ROOT',tmp_path)
    folder=tmp_path/'models';folder.mkdir()
    for i in range(35):(folder/f'buck {i:02d}.plecs').write_text(MODEL)
    monkeypatch.setattr(plecs_local,'listening',lambda:True)
    loaded=[]
    def load(path):
        assert not Path(path).is_relative_to(folder)
        loaded.append(path)
    monkeypatch.setattr(plecs_diagnostics,'proxy',lambda:contextlib.nullcontext(SimpleNamespace(plecs=SimpleNamespace(load=load))))
    owner=str(uuid.uuid4())
    client=TestClient(main.app,headers={'X-Session-ID':owner})
    stop=threading.Event();thread=None
    if remote:
        pair=client.post('/api/bridge/pair',json={'model_directory':str(folder)}).json()
        worker=TestClient(main.app,headers={'X-Session-ID':owner,'X-Bridge-Token':pair['token']})
        worker.post('/api/bridge/heartbeat')
        def work():
            while not stop.is_set():
                job=worker.post('/api/bridge/claim').json().get('job')
                if job:
                    plecs_local.configure(owner,'',str(folder))
                    result=plecs_execution.execute(owner,job['payload'])
                    worker.post('/api/bridge/complete/'+job['id'],json={'result':result})
                stop.wait(.01)
        thread=threading.Thread(target=work,daemon=True);thread.start()
    else:
        assert client.post('/api/plecs/settings',json={'model_directory':str(folder)}).status_code==200
    async def agent(messages,tools,invoke,budget):
        assert budget.thinking==(mode!='fast')
        assert (await invoke('check_plecs_connection',{}))['status']=='ready'
        first=await invoke('search_plecs_models',{})
        assert first['status']=='listed'
        assert len(first['candidates'])==30 and first['next_offset']==30
        second=await invoke('search_plecs_models',{'offset':first['next_offset']})
        assert len(second['candidates'])==5 and second['next_offset'] is None
        assert second['candidate_count']==35 and second['search_complete']
        assert plecs_local.settings(owner)['profiles']=={}
        selected=await invoke('select_plecs_model',{'path':second['candidates'][-1]['path']})
        opened=await invoke('open_plecs_model',{'model':selected['model']})
        assert opened['status']=='opened' and opened['simulated'] is False
        return 'Opened the requested model.'
    monkeypatch.setattr(harness_runtime,'run',agent)
    try:
        response=client.post('/api/chat',json={'message':'打开目录中指定的电路，不运行仿真','reasoning_mode':mode})
        assert response.json()['mode']=='deepseek',response.json()
        assert len(loaded)==1 and (folder/'buck 34.plecs').read_text()==MODEL
    finally:
        stop.set()
        if thread:thread.join(timeout=5)


@pytest.mark.parametrize('offset,limit', [(-1,30),(False,30),(0,0),(0,101),(0,True)])
def test_invalid_pagination_is_rejected(offset,limit):
    with pytest.raises(ValueError):plecs_local.search_models('unused',[],offset,limit)
