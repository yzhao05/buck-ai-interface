"""Regression checks for interrupted tasks, result delivery and late bindings."""
import asyncio
import json
import time
import uuid

import httpx
import pytest

from backend import agent_tasks, plecs_bridge, plecs_companion, simulation_analysis, harness_runtime
from backend.agent_policy import Budget
from conftest import REAL_RUN
from backend.schemas import ChatRequest, FitOptions


@pytest.mark.parametrize('tool,can_restart', [('read_experiment_results',True),('run_plecs_experiment',False),('unknown',False)])
def test_session_restart_uses_task_tool_history(tmp_path,monkeypatch,tool,can_restart):
    monkeypatch.setattr(agent_tasks,'ROOT',tmp_path)
    async def scenario():
        async def fail(_):
            harness_runtime.scope.get()['emit']('tool_start',{'name':tool})
            return {'mode':'provider_error'}
        async def resume(_):
            assert harness_runtime.scope.get()['can_restart_session'] is can_restart
            return {'mode':'deepseek'}
        task_id = agent_tasks.launch('owner',ChatRequest(message='Compare results'),fail)['id']
        await agent_tasks.running[task_id]
        agent_tasks.launch('owner',None,resume,task_id,resume=True)
        await agent_tasks.running[task_id]
        assert agent_tasks.read('owner',task_id)['state'] == 'completed'
    asyncio.run(scenario())


def test_only_process_restart_interrupts_running_tasks(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_tasks, 'ROOT', tmp_path)
    with agent_tasks.database() as db:
        db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?)',('task','owner','running','{}',None,0))
    # A GET can arrive between the admission commit and the in-memory task registration.
    assert agent_tasks.read('owner','task')['state'] == 'running'
    # Reopen the existing database as a fresh worker.
    monkeypatch.setattr(agent_tasks, '_initialized_database', None)
    assert agent_tasks.latest('owner')['state'] == 'interrupted'


def test_resuming_older_task_still_blocks_new_task(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_tasks, 'ROOT', tmp_path)

    async def scenario():
        async def fail(_):
            return {'mode': 'provider_error'}
        async def succeed(_):
            return {'mode': 'deepseek', 'answer': 'done'}
        async def wait(_):
            await asyncio.Event().wait()
        older = agent_tasks.launch('owner', ChatRequest(message='old'), fail)['id']
        await agent_tasks.running[older]
        newer = agent_tasks.launch('owner', ChatRequest(message='new'), succeed)['id']
        await agent_tasks.running[newer]
        agent_tasks.launch('owner', None, wait, older, resume=True)
        running = agent_tasks.running[older]
        await asyncio.sleep(0)
        assert agent_tasks.latest('owner')['id']==older
        with pytest.raises(ValueError, match='active Agent task'):
            agent_tasks.launch('owner', ChatRequest(message='another'), succeed)
        agent_tasks.cancel('owner', older)
        await running
        assert agent_tasks.read('owner', older)['state'] == 'cancelled'
        assert agent_tasks.latest('owner')['id'] == older
        assert agent_tasks.read('owner', older)['result'] is None
        with pytest.raises(ValueError, match='Only interrupted'):
            agent_tasks.launch('owner', None, succeed, newer, resume=True)
    asyncio.run(scenario())


def test_cancel_before_runner_starts_releases_capacity(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_tasks, 'ROOT', tmp_path)

    async def scenario():
        async def never(_):
            pytest.fail('Cancelled task must not start')
        identifier = agent_tasks.launch('owner', ChatRequest(message='stop'), never)['id']
        task = agent_tasks.running[identifier]
        agent_tasks.cancel('owner', identifier)
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)
        assert identifier not in agent_tasks.running
        assert agent_tasks.read('owner', identifier)['state'] == 'cancelled'
    asyncio.run(scenario())


def test_repair_revokes_uncertain_jobs(tmp_path, monkeypatch):
    monkeypatch.setattr(plecs_bridge, 'ROOT', tmp_path)
    owner, identifier = 'owner', uuid.uuid4().hex
    old = plecs_bridge.pair(owner)
    with plecs_bridge.database() as db:
        db.execute('INSERT INTO work VALUES (?,?,?,?,?,?,?,?)',
                   (identifier, owner, 'once', 'uncertain', '{}', None, time.time(), time.time()))
    new = plecs_bridge.pair(owner)
    with pytest.raises(plecs_bridge.InvalidCredential):
        plecs_bridge.heartbeat(owner,old['token'])
    with pytest.raises(plecs_bridge.InvalidCredential):
        plecs_bridge.claim(owner,old['token'])
    assert not plecs_bridge.status(owner)['online']
    with pytest.raises(ValueError, match='credential'):
        plecs_bridge.complete(owner, old['token'], identifier, {})
    with pytest.raises(ValueError, match='no longer accepts'):
        plecs_bridge.complete(owner, new['token'], identifier, {})


def test_invalid_pair_settings_do_not_revoke_existing_credential(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from backend import main
    monkeypatch.setattr(plecs_bridge, 'ROOT', tmp_path)
    monkeypatch.setattr(main, 'TOKEN', '')
    owner = str(uuid.uuid4())
    client = TestClient(main.app, headers={'X-Session-ID': owner})
    peer = client.post('/api/bridge/pair', json={'executable': '', 'model_directory': 'models'}).json()
    assert plecs_bridge.settings(owner)['model_directory'] == 'models'
    response = client.post('/api/bridge/pair', json={'analysis_binding': {'confirmed': True}})
    assert response.status_code == 422
    assert client.post('/api/bridge/claim', headers={'X-Bridge-Token': peer['token']}).status_code == 200
    assert response.headers['cache-control'] == 'no-store'


def test_companion_restart_delivers_receipts_without_reexecution(tmp_path, monkeypatch):
    calls, delivered = [], []
    monkeypatch.setattr(plecs_companion, 'execute', lambda *args: calls.append(args) or {'status': 'completed', 'sample': 4})
    identifier = uuid.uuid4().hex
    job = {'id': identifier, 'deadline': time.time()+30, 'payload': {'tool': 'probe'}}
    result = plecs_companion.perform_job('owner', tmp_path, job)
    assert plecs_companion.perform_job('owner', tmp_path, job) == result
    assert len(calls) == 1
    def offline(request):
        raise httpx.ConnectError('offline', request=request)
    with httpx.Client(base_url='https://test', transport=httpx.MockTransport(offline)) as client:
        with pytest.raises(httpx.ConnectError):
            plecs_companion.flush_receipts(client, tmp_path)
    def accept(request):
        delivered.append(json.loads(request.content)['result'])
        return httpx.Response(200, json={'status': 'completed'})
    with httpx.Client(base_url='https://test', transport=httpx.MockTransport(accept)) as client:
        plecs_companion.flush_receipts(client, tmp_path)
        plecs_companion.flush_receipts(client, tmp_path)
        interrupted = plecs_companion.receipt_path(tmp_path, uuid.uuid4().hex)
        plecs_companion.save_receipt(interrupted, {'state': 'running'})
        plecs_companion.flush_receipts(client, tmp_path)
    assert delivered == [result, {'status': 'uncertain', 'error': 'Local execution was interrupted; not repeated.'}]
    assert len(calls) == 1


def test_later_binding_allows_evaluation_without_repeating_simulation(tmp_path, monkeypatch):
    monkeypatch.setattr(simulation_analysis, 'ROOT', tmp_path)
    identifier = 'agent_'+uuid.uuid4().hex
    folder = simulation_analysis.directory('owner', identifier)
    folder.mkdir(parents=True)
    report = {'source_result_id': 'r1', 'experiment_id': identifier,
              'resolved_base_parameters': {'L1.L': .002, 'C1.C': .001},
              'design': {'parameter': 'C1.C'},
              'cases': [{'kind': 'baseline', 'value': .001}, {'kind': 'intervention', 'value': .0011}]}
    snapshot = {'report': report, 'options': FitOptions(method='weighted_integral').model_dump(),
                'profile': {'description': 'buck', 'parameters': {'L1.L': {'field': 'L'}, 'C1.C': {'field': 'C'}}},
                'binding': None}
    source = json.dumps(snapshot)
    (folder/'source.json').write_text(source, encoding='utf-8')
    raw = 'time_s,output_1,output_2\n'+''.join(f'{i*1e-6},1,5\n' for i in range(200))
    for index in range(2):
        (folder/f'case_{index}.csv').write_text(raw, encoding='utf-8')
    binding = {'model_name': 'buck.plecs', 'current_port': 1, 'voltage_port': 2,
               'L_parameter': 'L1.L', 'C_parameter': 'C1.C', 'confirmed': False}
    simulation_analysis.confirm_binding('owner', identifier, 'r1', binding)
    assert not (folder/'binding.json').exists()
    binding['confirmed'] = True
    simulation_analysis.confirm_binding('owner', identifier, 'r1', binding)
    fits = []
    def fit(*args, **kwargs):
        fits.append(1)
        return {'parameters': {'L_H': .002, 'C_F': .001}, 'metrics': {}}
    monkeypatch.setattr(simulation_analysis, 'identify', fit)
    assert simulation_analysis.evaluate('owner', identifier, 'r1')['method_preserved']
    assert simulation_analysis.saved_evaluation('owner', identifier, 'r1')['cached']
    assert len(fits) == 2
    assert (folder/'source.json').read_text(encoding='utf-8') == source
    with pytest.raises(ValueError, match='different source'):
        simulation_analysis.saved_evaluation('owner', identifier, 'other')


def test_harness_cancel_stops_pending_tool_coroutine(monkeypatch):
    import deepseek_harness
    from types import SimpleNamespace
    class Runtime:
        def __init__(self, **kwargs):
            self.config = kwargs
        def start(self):
            pass
        def start_session(self, identifier):
            return self
        def close(self):
            pass
        def run(self, prompt):
            with httpx.Client(trust_env=False, timeout=10) as client:
                client.post(self.config['base_url']+'/mcp',
                            headers={'Authorization': 'Bearer '+self.config['api_key']},
                            json={'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': 'wait'}})
            return SimpleNamespace(finish_reason='completed', final_response='done')
    monkeypatch.setattr(deepseek_harness, 'DeepSeekHarness', Runtime)
    async def scenario():
        started, cancelled = asyncio.Event(), asyncio.Event()
        async def invoke(name, args):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        tools = [{'type': 'function', 'function': {'name': 'wait', 'description': 'wait', 'parameters': {'type': 'object'}}}]
        task = asyncio.create_task(REAL_RUN([{'role': 'user', 'content': 'wait'}], tools, invoke, Budget(False)))
        await asyncio.wait_for(started.wait(), timeout=10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(cancelled.wait(), timeout=2)
        assert not harness_runtime.active
    asyncio.run(scenario())
