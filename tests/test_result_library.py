"""The Agent reads the same saved runs as the right-hand result selector."""
import asyncio
import copy
import json

import pytest
from pydantic import ValidationError

from backend import chat, harness_runtime, agent_tasks
from backend.schemas import ChatRequest, ResultLibraryEntry, FitOptions
from backend.experiment_history import ResultLibrary


def runs():
    records=[]
    for index in (2,1):
        identifier=f'run-{index}'
        records.append({'id':identifier,'created':1000+index,'updated':2000,
            'display_name':'same-report.json','dataset':{'filename':'same.csv','sha256':'a'*64},
            'result':{'method':'weighted_integral','options':FitOptions(mode='LC' if index==1 else 'LCR',method='weighted_integral').model_dump(),
                      'mapping':{'time':'t','current':'i','voltage':'v','pwm':None,'time_unit':'s','current_unit':'A','voltage_unit':'V'},
                      'parameters':{'L_H':index*60e-6,'C_F':500e-6,'R_ohm':6},'R_estimated':index==2,
                      'metrics':{'current_rmse_A':index*.01},'diagnostics':{'pann':{'convergence':{'status':'stable'},'epoch_history':[1,2]}},
                      'chart':{'time_s':[0,1]}},
            'ground_truth':{'result_id':identifier,'L_H':60e-6,'C_F':500e-6} if index==1 else None})
    return records


def test_catalog_and_exact_reads_keep_same_name_runs_separate():
    data=runs();before=copy.deepcopy(data)
    library=ResultLibrary([ResultLibraryEntry.model_validate(r) for r in data],'run-1')
    catalog=library.catalog()
    assert [r['id'] for r in catalog]==['run-2','run-1']
    assert [r['position'] for r in catalog]==[1,2]
    assert [r['selected'] for r in catalog]==[False,True]
    assert [r['display_name'] for r in catalog]==['same-report.json']*2
    first,second=library.read({'result_ids':['run-1','run-2']})['experiments']
    assert first['result']['parameters']['L_H']==60e-6
    assert first['result']['parameter_comparison']['relative_error_pct']['L']==0
    assert first['result']['R_estimated'] is False
    assert second['result']['parameters']['L_H']==120e-6
    assert second['result']['R_estimated'] is True
    assert 'parameter_comparison' not in second['result']
    assert first['result']['metrics']['current_rmse_A']==.01
    assert first['result']['diagnostics']['pann']['convergence']['status']=='stable'
    assert 'chart' not in first['result']
    assert 'epoch_history' not in first['result']['diagnostics']['pann']
    assert data==before


@pytest.mark.parametrize('arguments',[{}, {'result_ids':[]},{'result_ids':['run-1','run-1']},
    {'result_ids':['same.csv']},{'result_ids':['run-1','other-conversation']},
    {'result_ids':['run-1'],'fallback':True},{'result_ids':[1]}])
def test_invalid_selection_never_guesses_or_falls_back(arguments):
    library=ResultLibrary([ResultLibraryEntry.model_validate(r) for r in runs()],'run-1')
    with pytest.raises(ValueError):library.read(arguments)


def test_request_rejects_duplicate_ids_and_wrong_truth():
    records=runs()
    with pytest.raises(ValidationError):ChatRequest(message='compare',result_library=[records[0],records[0]])
    records[1]['ground_truth']['result_id']='run-2'
    with pytest.raises(ValidationError):ChatRequest(message='compare',result_library=records)


def test_legacy_missing_metrics_are_unknown_not_a_failed_read():
    record=runs()[1];record['result'].pop('metrics')
    result=ResultLibrary([ResultLibraryEntry.model_validate(record)]).read({'result_ids':['run-1']})['experiments'][0]['result']
    assert 'metrics' not in result
    assert result['parameter_comparison']['relative_error_pct']['L']==0


def test_agent_can_locate_then_read_two_runs_without_cached_jobs(monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY','test-only')
    async def forbidden(*args):pytest.fail('Reading saved results must not fetch live data or execute a fit')
    async def agent(messages,tools,execute,budget):
        context=json.loads(messages[1]['content'].split('：',1)[1])
        assert 'read_experiment_results' in {t['function']['name'] for t in tools}
        assert 'current_rmse_A' not in json.dumps(context), 'Do not preload full snapshots into model context'
        directory=await execute('get_experiment_history',{})
        ids=[r['id'] for r in directory['result_catalog']]
        assert ids==['run-2','run-1']
        results=await execute('read_experiment_results',{'result_ids':ids})
        assert results['count']==2
        assert [r['result']['metrics']['current_rmse_A'] for r in results['experiments']]==[.02,.01]
        invalid=await execute('read_experiment_results',{'result_ids':['another-run']})
        assert 'error' in invalid
        return '读取两次辨识结果后完成对比。'
    monkeypatch.setattr(harness_runtime,'run',agent)
    result=asyncio.run(chat.answer(ChatRequest(message='对比列表中两次辨识',result_library=runs(),result_id='run-1'),forbidden))
    assert result['mode']=='deepseek'
    assert [row['name'] for row in result['tool_calls']]==['get_experiment_history','read_experiment_results','read_experiment_results']


def test_result_catalog_survives_task_resume(tmp_path,monkeypatch):
    monkeypatch.setattr(agent_tasks,'ROOT',tmp_path)
    async def scenario():
        async def disconnect(_):return {'mode':'provider_error'}
        request=ChatRequest(message='compare',result_library=runs())
        task=agent_tasks.launch('owner',request,disconnect)['id']
        await agent_tasks.running[task]
        async def resumed(body):
            library=ResultLibrary(body.result_library)
            assert library.read({'result_ids':['run-2','run-1']})['count']==2
            return {'mode':'deepseek','answer':'comparison'}
        agent_tasks.launch('owner',None,resumed,task,resume=True)
        await agent_tasks.running[task]
        assert agent_tasks.read('owner',task)['result']['answer']=='comparison'
    asyncio.run(scenario())
