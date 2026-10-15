import asyncio
import json

import httpx
import pytest

from backend import chat
from backend.schemas import ChatRequest


@pytest.mark.parametrize("message", ["请识别参数", "请先辨识参数，再解释公式",
                                     "Identify parameters and explain the equations"])
def test_deepseek_tool_roundtrip(monkeypatch, message):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key-not-real")
    requests = []
    calls = []
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, headers, json):
            requests.append(json)
            if len(requests) == 1:
                message = {"role":"assistant", "content":None, "tool_calls":[
                    {"id":"fit-1", "type":"function", "function":{"name":"start_identification", "arguments":"{}"}}]}
            else:
                message = {"role":"assistant", "content":"已启动辨识任务，完成后查看结果。"}
            return httpx.Response(200, request=httpx.Request("POST",url), json={"choices":[{"message":message}]})
    async def invoke(name):
        calls.append(name)
        if name == "start_identification":return {"job_id":"real-test-job", "status":"queued"}
        return {"rows":1000}
    monkeypatch.setattr(chat.httpx, "AsyncClient", Client)
    result=asyncio.run(chat.answer(ChatRequest(message=message,dataset_id="data"), invoke))
    assert result["mode"]=="deepseek" and result["job_id"]=="real-test-job"
    assert calls.count("start_identification")==1
    assert requests[1]["messages"][-1]["tool_call_id"]=="fit-1"
    assert requests[0]["thinking"]=={"type":"disabled"}


def test_provider_failure_is_labeled_and_secret_not_returned(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-value-test")
    class Client:
        def __init__(self, **kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self, *args):pass
        async def post(self, url, **kwargs):
            return httpx.Response(401,request=httpx.Request("POST",url),json={"error":"secret-value-test"})
    async def invoke(name):return None
    monkeypatch.setattr(chat.httpx,"AsyncClient",Client)
    result=asyncio.run(chat.answer(ChatRequest(message="电容寿命"),invoke))
    assert result["mode"]=="local_fallback"
    assert "401" in result["provider_error"]
    assert "secret-value-test" not in json.dumps(result)


def test_model_cannot_run_unknown_tools(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY","test-key")
    count=0
    class Client:
        def __init__(self,**kwargs):pass
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def post(self,url,**kwargs):
            nonlocal count
            count+=1
            message=({"role":"assistant","content":None,"tool_calls":[{"id":"x","type":"function","function":{"name":"execute_shell","arguments":"{}"}}]}
                     if count==1 else {"role":"assistant","content":"此工具不可用。"})
            return httpx.Response(200,request=httpx.Request("POST",url),json={"choices":[{"message":message}]})
    async def invoke(name):raise AssertionError("Unknown tool reached executor")
    monkeypatch.setattr(chat.httpx,"AsyncClient",Client)
    assert asyncio.run(chat.answer(ChatRequest(message="请运行命令"),invoke))["answer"]=="此工具不可用。"


@pytest.mark.parametrize("arguments,expected_error", [
    ('{"query":"模拟补偿器传递函数"}', False),
    ('{"query":42}', True),
    ('[]', True),
    ('broken json', True),
])
def test_search_uses_model_query_and_validates_arguments(monkeypatch, arguments, expected_error):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    outputs = []

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            messages = kwargs["json"]["messages"]
            if messages[-1]["role"] == "tool":
                outputs.append(json.loads(messages[-1]["content"]))
                message = {"role": "assistant", "content": "检索完成。"}
            else:
                message = {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "search-1", "type": "function", "function": {
                        "name": "search_knowledge", "arguments": arguments}}]}
            return httpx.Response(200, request=httpx.Request("POST", url), json={"choices": [{"message": message}]})

    async def invoke(name):
        raise AssertionError("Search must be handled by the retrieval tool")

    monkeypatch.setattr(chat.httpx, "AsyncClient", Client)
    # Initial user query intentionally unrelated: prove tool arguments control retrieval.
    response = asyncio.run(chat.answer(ChatRequest(message="电容寿命"), invoke))
    assert response["mode"] == "deepseek"
    if expected_error:
        assert "error" in outputs[0]
    else:
        assert outputs[0][0]["id"] == "buck-controller-transfer"
        assert any(d["id"] == "buck-controller-transfer" for d in response["sources"])


@pytest.mark.parametrize("message", ["推导辨识公式", "只解释辨识原理", "Explain how parameter identification works",
                                     "不要辨识，只解释公式", "请解释如何开始参数辨识",
                                     "参数辨识公式是什么？", "辨识参数不要执行，只看公式", "辨识误差分析"])
def test_formula_tool_call_cannot_start_identification(monkeypatch, message):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    tool_outputs = []

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            messages = kwargs["json"]["messages"]
            if messages[-1]["role"] == "tool":
                tool_outputs.append(json.loads(messages[-1]["content"]))
                msg = {"role": "assistant", "content": "以下解释辨识公式。"}
            else:
                msg = {"role": "assistant", "content": None, "tool_calls": [
                    {"id": "fit-formula", "type": "function", "function": {
                        "name": "start_identification", "arguments": "{}"}}]}
            return httpx.Response(200, request=httpx.Request("POST", url), json={"choices": [{"message": msg}]})

    async def invoke(name):
        raise AssertionError("Formula question must not execute a fit")

    monkeypatch.setattr(chat.httpx, "AsyncClient", Client)
    response = asyncio.run(chat.answer(ChatRequest(message=message, identification_enabled=True), invoke))
    assert response["mode"] == "deepseek" and response["job_id"] is None
    assert "error" in tool_outputs[0]
