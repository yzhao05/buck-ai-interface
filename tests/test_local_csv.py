"""Local folder search, verified file reads, and structured UI actions."""
import uuid
import hashlib
import asyncio
import tracemalloc
import pytest
from fastapi.testclient import TestClient
from backend import main, harness_runtime, local_csv, plecs_local
from backend import chat
from backend.schemas import ChatRequest


async def select_csv(messages, tools, execute, budget):
    assert any(t['function']['name']=='search_local_csv' for t in tools)
    found = await execute('search_local_csv', {'query':'case16a noise03 seed14'})
    assert found['match_count']==1 and found['search_complete']
    chosen = found['files'][0]
    action = await execute('configure_identification', {'changes':{},'open_panel':False,
        'start_after_apply':False, 'assets':{'csv_id':chosen['id']}})
    assert action['status']=='pending_ui', action
    return 'CSV selected'


@pytest.mark.parametrize('mode',['fast','thinking'])
def test_folder_to_chat_import(tmp_path, monkeypatch, mode):
    monkeypatch.setattr(main,'TOKEN','')
    monkeypatch.delenv('RENDER',raising=False)
    monkeypatch.setenv('APP_ENV','development')
    monkeypatch.setenv('DEEPSEEK_API_KEY','test')
    monkeypatch.setattr(plecs_local,'ROOT',tmp_path/'settings')
    monkeypatch.setattr(harness_runtime,'run',select_csv)
    owner = str(uuid.uuid4())
    client = TestClient(main.app,headers={'X-Session-ID':owner})
    root = tmp_path/'folder'
    (root/'nested').mkdir(parents=True)
    path = root/'nested/CASE16A_noise03_seed14.csv'
    raw = b'time,current,voltage\n'+b''.join(f'{i},1,2\n'.encode() for i in range(100))
    path.write_bytes(raw)
    assert client.post('/api/plecs/settings',json={'model_directory':str(root)}).is_success
    response = client.post('/api/chat',json={'message':'加载实验示例case16a noise03 seed14',
        'reasoning_mode':mode,'ui_context':{'request_id':'test','options':{},'pwm_source_setting':'auto','catalog':[]}}).json()
    action = response['ui_action']
    file, = action['local_csv_files']
    assert file['bytes']==len(raw) and file['sha256']==hashlib.sha256(raw).hexdigest()
    assert action['assets']['csv_id']==file['id'] and not action['start_after_apply']
    body = {'id':file['id'],'relative_path':file['relative_path']}
    assert client.post('/api/local-csv/file',json=body).content==raw
    assert client.post('/api/local-csv/file',json={**body,'relative_path':'../outside.csv'}).status_code==422
    assert client.post('/api/local-csv/file',json=body,headers={'Origin':'https://other.example'}).status_code==403
    path.write_bytes(raw+b'100,1,2\n')
    assert client.post('/api/local-csv/file',json=body).status_code==422
    monkeypatch.setenv('RENDER','true')
    assert client.post('/api/local-csv/file',json=body).status_code==403


def test_failed_file_check_does_not_stage_browser_import(monkeypatch):
    from backend import chat
    from backend.schemas import ChatRequest
    import asyncio, json
    monkeypatch.setenv('DEEPSEEK_API_KEY','test')
    file={'id':'local-csv:'+'f'*64,'name':'x.csv','relative_path':'x.csv'}
    async def invoke(name,args):
        if name=='search_local_csv':
            return {'files':[file]}
        assert name=='verify_local_csv_selection'
        return {'error':'File changed; search again'}
    async def agent(messages,tools,execute,budget):
        context=json.loads(messages[1]['content'].split('：',1)[1])
        assert context['ui_context']['previous_local_csv_action']['current_dataset'] is None
        await execute('search_local_csv',{})
        result=await execute('configure_identification',{'assets':{'csv_id':file['id']},
            'changes':{},'start_after_apply':False,'open_panel':False})
        assert result['error']=='File changed; search again'
        return result['error']
    monkeypatch.setattr(harness_runtime,'run',agent)
    request=ChatRequest(message='再试一次',ui_context={'request_id':'retry','options':{},
        'previous_local_csv_action':{'filename':'x.csv','browser_report':'上一次界面没有应用操作'}})
    result=asyncio.run(chat.answer(request,invoke,local_files=True))
    assert result['ui_action'] is None and result['tool_calls'][-1]['status']=='未执行'


def test_local_csv_paging_and_same_names(tmp_path,monkeypatch):
    monkeypatch.setattr(plecs_local,'ROOT',tmp_path/'settings')
    root = tmp_path/'csv'
    for i in range(32):
        folder=root/str(i)
        folder.mkdir(parents=True)
        (folder/'same.csv').write_text('1')
    plecs_local.configure('owner','',str(root))
    first=local_csv.search('owner','same')
    assert first['match_count']==32 and first['next_offset']==30
    second=local_csv.search('owner','same',30)
    assert second['next_offset'] is None and len(second['files'])==2
    assert len({f['id'] for f in first['files']+second['files']})==32


def test_agent_can_browse_then_refine_unordered_fragments(tmp_path,monkeypatch):
    monkeypatch.setattr(plecs_local,'ROOT',tmp_path/'settings')
    root=tmp_path/'experiments'
    folder=root/'case16a'
    folder.mkdir(parents=True)
    for name in ['noise03_seed14.csv','noise03_seed15.csv']:
        (folder/name).touch()
    plecs_local.configure('owner','',str(root))
    assert local_csv.search('owner')['match_count']==2
    found=local_csv.search('owner',terms=['seed14','CASE16A','noise03'])
    assert [f['relative_path'] for f in found['files']]==['case16a/noise03_seed14.csv']


@pytest.mark.parametrize('local_files',[True,False])
def test_client_catalog_cannot_admit_an_old_local_file(monkeypatch,local_files):
    key='local-csv:'+'1'*64
    monkeypatch.setenv('DEEPSEEK_API_KEY','test')
    async def agent(messages,tools,execute,budget):
        result=await execute('configure_identification',{'changes':{},'open_panel':False,
            'start_after_apply':False,'assets':{'csv_id':key}})
        assert result.get('error')
        return 'File must be selected in this task'
    monkeypatch.setattr(harness_runtime,'run',agent)
    request=ChatRequest(message='load',ui_context={'request_id':'r','options':{},
        'catalog':[{'id':key,'name':'old.csv','kind':'csv'}]})
    result=asyncio.run(chat.answer(request,None,local_files=local_files))
    assert result['ui_action'] is None and result['mode']=='deepseek'


def test_verification_hashes_large_file_in_chunks(tmp_path,monkeypatch):
    monkeypatch.setattr(plecs_local,'ROOT',tmp_path/'settings')
    root=tmp_path/'data'
    root.mkdir()
    raw=b'0,1\n' * (2*1024*1024)
    (root/'large.csv').write_bytes(raw)
    plecs_local.configure('owner','',str(root))
    match,=local_csv.search('owner')['files']
    tracemalloc.start()
    try:
        receipt=local_csv.verify_selection('owner','large.csv',match['id'])
        peak=tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert receipt=={'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
    assert peak < len(raw)//2


def test_local_entry_points_version_csv_scripts():
    client=TestClient(main.app)
    for url in ('/', '/index.html'):
        html=client.get(url).text
        for name in ('app.js','configuration-fields.js','agent-harness.js'):
            content=client.get('/'+name).content
            version=hashlib.sha256(content).hexdigest()[:12]
            assert f'./{name}?v={version}"' in html
