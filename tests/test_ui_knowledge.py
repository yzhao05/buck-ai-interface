"""UI inventory coverage and realistic help retrieval through the agent path."""
import asyncio
import json
import re
from html.parser import HTMLParser
from pathlib import Path

import pytest

from backend import chat
from backend.knowledge import retrieve
from backend.knowledge_base.ui import UI_DOCUMENTS
from backend.schemas import ChatRequest

ROOT = Path(__file__).resolve().parents[1]


class Controls(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.anonymous = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in {'button', 'input', 'select', 'textarea', 'details', 'dialog'}:
            if tag == 'input' and attrs.get('type') == 'hidden':
                return
            if 'id' in attrs:
                self.ids.add(attrs['id'])
            else:
                self.anonymous.append((tag, attrs))


def test_all_html_and_generated_circuit_controls_have_help():
    parser = Controls()
    parser.feed((ROOT / 'frontend/index.html').read_text(encoding='utf-8'))
    documented = {control for doc in UI_DOCUMENTS for control in doc['controls']}
    assert not parser.ids - documented, 'New controls need searchable help'
    circuit = (ROOT / 'frontend/circuit.js').read_text(encoding='utf-8').split('let draftImage')[0]
    generated = {'circuit-' + key for key in re.findall(r'\["([\w-]+)",', circuit)}
    assert generated <= documented
    # The anonymous controls are three quick prompts, settings submit, and
    # two details panels. Make additions visible rather than silently ignored.
    assert len(parser.anonymous) == 6
    assert len({doc['id'] for doc in UI_DOCUMENTS}) == len(UI_DOCUMENTS)
    for doc in UI_DOCUMENTS:
        assert all((ROOT / source).is_file() for source in doc['implementation_sources'])


@pytest.mark.parametrize('query,expected', [
    ('前端每个面板怎么用，工作台总览', 'overview'),
    ('应用并检查数据会开始辨识吗', 'mapping'),
    ('恢复默认图和恢复默认参数有什么区别', 'circuit-image'),
    ('训练轮数这个参数怎么填', 'pann-epochs'),
    ('末10周期按钮会改变辨识区间吗', 'waveform'),
    ('train_fraction training split slider', 'train-fraction'),
    ('What does the reasoning mode dropdown do?', 'reasoning'),
    ('导出报告文件夹如何选择保存位置', 'reports'),
    ('真实电感单位是真值参与训练吗', 'truth'),
    ('停止任务和取消后续任务的区别', 'agent-stop'),
    ('找回恢复任务', 'agent-stop'),
    ('删除当前会话会删除备份吗', 'delete-chat'),
    ('应用访问口令是DeepSeek API Key吗', 'access-token'),
    ('plecs-binding-current 电流输出端口', 'plecs-binding'),
    ('circuit-carrier_phase_at_t0 载波初始相位单位', 'controller-pwm'),
    ('同步恢复码怎么用', 'sync-code'),
    ('填写检索问题按钮会自动发送吗', 'conversation-search'),
])
def test_ui_help_retrieval(query, expected):
    assert 'ui-' + expected in [doc['id'] for doc in retrieve(query, 4)]


@pytest.mark.parametrize('doc', UI_DOCUMENTS, ids=lambda doc: doc['id'])
def test_each_control_id_can_retrieve_its_manual(doc):
    for control in doc['controls']:
        assert doc['id'] in [hit['id'] for hit in retrieve(control, 4)], control


def test_ui_help_is_available_via_agent_search_without_execution(monkeypatch):
    from test_agent_knowledge import fake_provider, search, no_execution
    requests = fake_provider(monkeypatch, [search('恢复默认图'),
        {'role': 'assistant', 'content': '恢复默认图只改变参考图片，不重置参数。[kb:ui-circuit-image]'}])
    result = asyncio.run(chat.answer(ChatRequest(message='只解释恢复默认图按钮，不要修改任何设置'), no_execution))
    assert result['mode'] == 'deepseek'
    assert [doc['id'] for doc in result['sources']] == ['ui-circuit-image']
    assert all(call['name'] == 'search_knowledge' for call in result['tool_calls'])
    assert not result.get('ui_action') and not result.get('job_id')
    tool_result = json.loads(requests[1]['messages'][-1]['content'])
    assert '不重置参数' in json.dumps(tool_result, ensure_ascii=False)
    assert '界面功能说明是静态资料' in requests[0]['messages'][0]['content']


@pytest.mark.parametrize('queries', [
    [('Buck CCM DCM 临界条件', 2), ('恢复默认图', 6)],
    [('恢复默认图', 3), ('Buck CCM DCM 临界条件', 5)],
])
def test_ui_question_does_not_route_or_rewrite_agent_searches(monkeypatch, queries):
    from test_agent_knowledge import fake_provider, search, no_execution
    calls = []
    def observe(query, count):
        calls.append((query, count))
        return retrieve(query, count)
    monkeypatch.setattr(chat, 'retrieve', observe)
    requests = fake_provider(monkeypatch, [
        *[search(query, top_k=count) for query, count in queries],
        {'role': 'assistant', 'content': '恢复默认图不会重置参数。[kb:ui-circuit-image]'},
    ])
    result = asyncio.run(chat.answer(ChatRequest(message='请介绍整个前端界面的功能'), no_execution))
    assert result['mode'] == 'deepseek'
    assert calls == queries  # No automatic overview, query rewrite or UI-only filter.
    for index, (query, count) in enumerate(queries):
        output = json.loads(requests[index + 1]['messages'][-1]['content'])
        assert output['documents'] == retrieve(query, count)
    assert all(request['tool_choice'] == 'auto' for request in requests)
