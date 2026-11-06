"""Model-selected searches must retain terms, scope, ordering and pagination."""
import asyncio
import pytest
from backend import chat, harness_runtime, plecs_local
from backend.conversation_retrieval import ConversationIndex
from backend.schemas import ConversationMessage, ChatRequest


@pytest.mark.parametrize('term', ['分析', '历史', '最近', '全部', '六次', 'analysis', 'latest', 'six answers'])
def test_conversation_query_words_are_not_intent_filters(term):
    store = ConversationIndex([ConversationMessage(id='hit', role='user', content=term),
                               ConversationMessage(id='miss', role='user', content='zzzz')])
    result = store.search(term)
    assert [row['id'] for row in result['excerpts']] == ['hit']
    assert store.searches[0]['query'] == term


@pytest.mark.parametrize('name', ['circuit', 'image', '截图', '电路图'])
def test_model_search_does_not_discard_generic_filename_hints(tmp_path, monkeypatch, name):
    (tmp_path / (name + '.plecs')).write_text('fixture', encoding='utf-8')
    monkeypatch.setattr(plecs_local, 'settings', lambda _: {'model_directory': str(tmp_path)})
    result = plecs_local.search_models('owner', [name])
    assert result['status'] == 'matched'
    assert result['match']['name'] == name


@pytest.mark.parametrize('message', ['解释公式', '介绍整个界面', '检索六次历史回答', '请先看目录再看知识'])
def test_agent_may_search_in_its_own_order_or_answer_without_search(monkeypatch, message):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-only')
    async def forbidden(*args):
        pytest.fail('No automatic data access')
    async def agent(messages, tools, execute, budget):
        roster = {t['function']['name']: t['function'] for t in tools}
        assert {'search_knowledge', 'search_conversation', 'read_conversation_message', 'search_plecs_models'} <= roster.keys()
        assert '界面' in roster['search_knowledge']['description']
        return '请提供需要解释的具体对象。'
    monkeypatch.setattr(harness_runtime, 'run', agent)
    result = asyncio.run(chat.answer(ChatRequest(message=message), forbidden))
    assert result['mode'] == 'deepseek'
    assert result['tool_calls'] == []


def test_model_selected_history_filters_are_forwarded(monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-only')
    records = [ConversationMessage(id=str(i), role='assistant', content=f'历史分析 {i}') for i in range(7)]
    async def agent(messages, tools, execute, budget):
        result = await execute('search_conversation', {'query': '历史分析', 'order': 'oldest', 'offset': 2, 'limit': 3, 'role': 'assistant'})
        assert [r['id'] for r in result['excerpts']] == ['2', '3', '4']
        return '已读取指定片段。'
    monkeypatch.setattr(harness_runtime, 'run', agent)
    result = asyncio.run(chat.answer(ChatRequest(message='最近的记录', conversation_messages=records), None))
    assert result['mode'] == 'deepseek'
    assert result['conversation_retrieval']['searches'][0]['offset'] == 2


@pytest.mark.parametrize('query', ['!!!', '🙂', '——'])
def test_nonempty_unsearchable_query_does_not_list_all_history(query):
    store = ConversationIndex([ConversationMessage(id='private', role='user', content='其他讨论')])
    assert store.search(query)['matched_count'] == 0
    assert store.metadata()['read_count'] == 0
    assert store.search('')['matched_count'] == 1
