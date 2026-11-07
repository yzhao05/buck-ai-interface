import asyncio
import copy
import json

import httpx
import pytest

from backend import chat
from backend.schemas import ChatRequest


def search(query, **kwargs):
    return {"role": "assistant", "content": None, "tool_calls": [{"id": query,
            "type": "function", "function": {"name": "search_knowledge",
            "arguments": json.dumps({"query": query, **kwargs})}}]}


def fake_provider(monkeypatch, replies):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-secret")
    requests = []
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            requests.append(copy.deepcopy(kwargs["json"]))
            reply = replies[len(requests)-1]
            if isinstance(reply, Exception): raise reply
            return httpx.Response(200, request=httpx.Request("POST", url),
                                  json={"choices": [{"message": reply}]})
    monkeypatch.setattr(chat.httpx, "AsyncClient", Client)
    return requests


async def no_execution(*args):
    raise AssertionError("Knowledge requests must not execute numerical tools")


def run():
    return asyncio.run(chat.answer(ChatRequest(message="解释这个电路公式"), no_execution))


def test_agent_refines_empty_search_and_cites_only_used_documents(monkeypatch):
    requests = fake_provider(monkeypatch, [search("zzzzzzzz"), search("Buck 模型", top_k=8),
        {"role": "assistant", "content": "根据模型解释。[kb:buck-physics]"}])
    result = run()
    assert result["mode"] == "deepseek"
    assert [d["id"] for d in result["sources"]] == ["buck-physics"]
    assert len(result["retrieved_sources"]) > 1
    assert result["tool_calls"][0]["count"] == 0
    assert result["tool_calls"][1]["query"] == "Buck 模型"
    assert "retrieved_documents" not in requests[0]["messages"][1]["content"]
    assert requests[0]["tool_choice"] == "auto"  # Harness chooses the retrieval plan.
    assert requests[1]["tool_choice"] == "auto"


@pytest.mark.parametrize("content", ["伪造来源。[kb:invented]"])
def test_unsearched_answer_is_rejected(monkeypatch, content):
    fake_provider(monkeypatch, [{"role": "assistant", "content": content}])
    result = run()
    assert result["mode"] == "provider_error" and result["answer"] == ""


def test_duplicate_query_is_not_reexecuted(monkeypatch):
    requests = fake_provider(monkeypatch, [search("zzzzzzzz"), search("zzzzzzzz"),
        {"role": "assistant", "content": "知识库未找到相关依据。"}])
    result = run()
    assert result["mode"] == "deepseek" and result["sources"] == []
    assert result["tool_calls"][1]["status"] == "已执行"
    assert json.loads(requests[2]["messages"][-1]["content"])["cached"]


@pytest.mark.parametrize("top_k", [0, 9, True, "4"])
def test_invalid_top_k_cannot_count_as_successful_search(monkeypatch, top_k):
    requests = fake_provider(monkeypatch, [search("Buck", top_k=top_k),
        {"role": "assistant", "content": "invalid"}])
    result = run()
    assert result["sources"] == []
    assert result["tool_calls"][0]["status"] == "未执行"
    assert "error" in json.loads(requests[1]["messages"][-1]["content"])


def test_provider_failure_after_search_preserves_trace_without_answer(monkeypatch):
    fake_provider(monkeypatch, [search("Buck 模型"), httpx.ReadTimeout("test-secret")])
    result = run()
    assert result["answer"] == "" and result["mode"] == "provider_error"
    assert result["tool_calls"] and result["retrieved_sources"]
    assert "test-secret" not in json.dumps(result)


def test_unknown_citation_after_search_is_rejected(monkeypatch):
    fake_provider(monkeypatch, [search("Buck 模型"),
        {"role": "assistant", "content": "Unsupported source [kb:invented]"}])
    result = run()
    assert result["mode"] == "provider_error"
    assert result['error_code'] == 'citation_validation_failed'
    assert result['retrieved_sources'] and result['tool_calls']


@pytest.mark.parametrize('first_fails', [False, True])
@pytest.mark.parametrize('scope_change', ['none', 'owner', 'dataset'])
def test_saved_search_evidence_survives_only_in_same_harness_session(monkeypatch, first_fails, scope_change):
    from backend import harness_runtime
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'test-only')

    async def scenario():
        async def first(messages, tools, execute, budget):
            found = await execute('search_knowledge', {'query':'Buck 模型', 'top_k':8})
            assert any(d['id'] == 'buck-physics' for d in found['documents'])
            if first_fails:
                raise httpx.ReadTimeout('interrupted after retrieval')
            return '已检索。[kb:buck-physics]'
        monkeypatch.setattr(harness_runtime, 'run', first)
        token = harness_runtime.scope.set({'owner':'first-owner', 'id':'first-task'})
        try:
            prior = await chat.answer(ChatRequest(message='解释模型'), no_execution)
        finally:
            harness_runtime.scope.reset(token)
        assert prior['mode'] == ('provider_error' if first_fails else 'deepseek')

        def no_search(*args):
            pytest.fail('Reusing saved evidence must not trigger another search')
        monkeypatch.setattr(chat, 'retrieve', no_search)
        async def second(messages, tools, execute, budget):
            return '继续解释已读取的模型。[kb:buck-physics]'
        monkeypatch.setattr(harness_runtime, 'run', second)
        token = harness_runtime.scope.set({'owner':'other-owner' if scope_change == 'owner' else 'first-owner', 'id':'second-task'})
        try:
            result = await chat.answer(ChatRequest(message='继续解释', dataset_id='new-data' if scope_change == 'dataset' else None), no_execution)
        finally:
            harness_runtime.scope.reset(token)
        assert result['tool_calls'] == []
        if scope_change == 'none':
            assert result['mode'] == 'deepseek'
            assert [d['id'] for d in result['sources']] == ['buck-physics']
            assert [d['id'] for d in result['retrieved_sources']] == ['buck-physics']
        else:
            assert result['error_code'] == 'citation_validation_failed'
            assert result['retrieved_sources'] == []
    asyncio.run(scenario())


def test_tool_round_limit_stops_more_searches(monkeypatch):
    requests = fake_provider(monkeypatch, [search("Buck " + str(i)) for i in range(20)])
    result = run()
    assert result["mode"] == "provider_error"
    assert result['error_code']=='agent_budget_exceeded' and len(requests)<=14


def test_failure_after_job_submission_keeps_job_id(monkeypatch):
    choices={'methods':['pann'],'pwm_sources':['measured'],
             'method_evidence':'使用 PANN','pwm_evidence':'使用 CSV 实测 PWM'}
    fake_provider(monkeypatch, [{"role": "assistant", "content": None, "tool_calls": [
        {"id": "fit", "type": "function", "function": {"name": "start_identification", "arguments": json.dumps({'execution_selection':choices})}}]},
        httpx.ReadTimeout("test-secret")])
    calls = []
    async def invoke(name):
        calls.append(name)
        return {"job_id": "submitted-job"}
    result = asyncio.run(chat.answer(ChatRequest(message="使用 CSV 实测 PWM 和 PANN 开始参数辨识", identification_enabled=True), invoke))
    assert result["mode"] == "provider_error" and result["job_id"] == "submitted-job"
    assert calls == ["start_identification"]


@pytest.mark.parametrize("message", ["解释公式", "开始参数辨识", "比较历史实验", "修改负载为10欧姆"])
def test_missing_key_stops_before_routing_or_tools(monkeypatch, message):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    result = asyncio.run(chat.answer(ChatRequest(message=message), no_execution))
    assert result["answer"] == "" and result["error_code"] == "missing_api_key"
    assert result["provider_error"].isascii() and result["job_id"] is None
