"""Agent-selected full-text access independent of keyword ranking."""
import asyncio
import json
from pathlib import Path

import pytest

from backend import chat
from backend.knowledge import DOCUMENTS, list_knowledge, read_knowledge, retrieve
from backend.schemas import ChatRequest
from test_agent_knowledge import fake_provider, no_execution, run


def call(name, **arguments):
    return {'role': 'assistant', 'content': None, 'tool_calls': [{
        'id': name, 'type': 'function', 'function': {
            'name': name, 'arguments': json.dumps(arguments)}}]}


def test_catalogue_pages_cover_every_document_once_without_full_text():
    ids, offset = [], 0
    while offset is not None:
        page = list_knowledge(offset=offset, limit=7)
        assert page['total'] == len(DOCUMENTS)
        assert all(d['summary'] and 'content' not in d for d in page['entries'])
        ids.extend(d['id'] for d in page['entries'])
        offset = page['next_offset']
    assert ids == [d['id'] for d in DOCUMENTS]
    categories = [list_knowledge(category=category, limit=100) for category in ('method', 'formula', 'ui')]
    assert [p['total'] for p in categories] == [9, 24, 61]
    assert set(ids) == {d['id'] for page in categories for d in page['entries']}
    assert list_knowledge(offset=10000)['entries'] == []


def test_direct_read_is_complete_ordered_and_reports_missing_ids():
    result = read_knowledge(['ui-overview', 'unknown-id', 'buck-map', 'ui-overview'])
    assert [d['id'] for d in result['documents']] == ['ui-overview', 'buck-map']
    assert result['missing_ids'] == ['unknown-id']
    assert result['documents'][1] == next(d for d in DOCUMENTS if d['id'] == 'buck-map')
    result['documents'][1]['references'].clear()
    assert read_knowledge(['buck-map'])['documents'][0]['references']


def test_catalogue_ids_sources_and_ui_summaries_are_connected():
    root = Path(__file__).resolve().parents[1]
    assert len({d['id'] for d in DOCUMENTS}) == len(DOCUMENTS)
    for entry in list_knowledge(limit=100)['entries']:
        doc = read_knowledge([entry['id']])['documents'][0]
        assert entry['title'] == doc['title']
        for reference in doc.get('references', []):
            if reference.get('path'):
                assert (root / reference['path']).is_file()
        for source in doc.get('implementation_sources', []):
            assert (root / source).is_file()
        if entry['category'] == 'ui':
            assert not entry['summary'].startswith('位置/控件：')
            assert entry['summary'] in doc['content']


def test_search_results_do_not_mutate_direct_read_sources():
    found = retrieve('控制电路 R1 R2 R3 R4 C2 C3 节点方程', 8)
    doc = next(d for d in found if d.get('references'))
    original = read_knowledge([doc['id']])['documents'][0]
    doc['references'].clear()
    assert read_knowledge([doc['id']])['documents'][0] == original


@pytest.mark.parametrize('name,arguments', [
    ('list_knowledge', {'query': 'Buck'}),
    ('list_knowledge', {'offset': True}),
    ('read_knowledge', {'ids': ['buck-map'], 'extra': 1}),
    ('read_knowledge', {'ids': ['buck-map', None]}),
])
def test_invalid_tool_call_cannot_create_citation_evidence(monkeypatch, name, arguments):
    requests = fake_provider(monkeypatch, [call(name, **arguments),
        {'role': 'assistant', 'content': '未经读取的结论。[kb:buck-map]'}])
    result = run()
    assert 'error' in json.loads(requests[1]['messages'][-1]['content'])
    assert result['tool_calls'][0]['status'] == '未执行'
    assert result['retrieved_sources'] == []
    assert result['error_code'] == 'citation_validation_failed'


@pytest.mark.parametrize('arguments', [{'offset': True}, {'offset': -1}, {'limit': 0}, {'limit': 101}, {'category': 'unknown'}])
def test_invalid_catalogue_arguments(arguments):
    with pytest.raises(ValueError):
        list_knowledge(**arguments)


@pytest.mark.parametrize('ids', [[], 'buck-map', [None], [''], ['buck-map'] * 9])
def test_invalid_read_arguments(ids):
    with pytest.raises(ValueError):
        read_knowledge(ids)


def test_agent_browses_and_selects_full_text_without_search(monkeypatch):
    monkeypatch.setattr(chat, 'retrieve', lambda *args: pytest.fail('BM25 must not run'))
    requests = fake_provider(monkeypatch, [
        call('list_knowledge', category='formula', limit=10),
        call('list_knowledge', category='formula', offset=10, limit=10),
        call('read_knowledge', ids=['buck-map', 'buck-physics']),
        call('read_knowledge', ids=['identification-limits']),
        {'role': 'assistant', 'content': '模型与验证的适用范围。[kb:buck-map][kb:identification-limits]'}])
    result = run()
    assert result['mode'] == 'deepseek'
    assert {d['id'] for d in result['sources']} == {'buck-map', 'identification-limits'}
    assert {d['id'] for d in result['retrieved_sources']} == {'buck-map', 'buck-physics', 'identification-limits'}
    assert all(request['tool_choice'] == 'auto' for request in requests)
    assert json.loads(requests[1]['messages'][-1]['content'])['next_offset'] == 10


@pytest.mark.parametrize('tool', [call('list_knowledge', limit=100), call('read_knowledge', ids=['invented'])])
def test_catalogue_or_missing_id_does_not_authorize_citation(monkeypatch, tool):
    fake_provider(monkeypatch, [tool, {'role': 'assistant', 'content': '未经读取的结论。[kb:buck-map]'}])
    result = run()
    assert result['error_code'] == 'citation_validation_failed'
    assert result['retrieved_sources'] == []


def test_new_tools_available_in_bound_result_analysis(monkeypatch):
    from backend import harness_runtime
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-only')

    async def harness(messages, tools, execute, budget):
        assert {'list_knowledge', 'read_knowledge'} <= {t['function']['name'] for t in tools}
        assert (await execute('list_knowledge', {'category': 'method'}))['count'] == 9
        assert (await execute('read_knowledge', {'ids': ['identification-limits']}))['count'] == 1
        return '验证范围说明。[kb:identification-limits]'

    monkeypatch.setattr(harness_runtime, 'run', harness)
    async def invoke(name):
        assert name == 'get_identification_result'
        return {'parameters': {}, 'diagnostics': {}}
    result = asyncio.run(chat.answer(ChatRequest(message='解释结果', result_id='result-1', current_result_analysis=True), invoke))
    assert result['mode'] == 'deepseek'


def test_new_tools_are_safe_to_replay():
    from backend.agent_tasks import REPLAYABLE_READS
    assert {'list_knowledge', 'read_knowledge'} <= REPLAYABLE_READS
