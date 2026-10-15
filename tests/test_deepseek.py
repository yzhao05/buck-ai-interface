import asyncio
import json

import httpx

from backend import chat
from backend.schemas import ChatRequest


def test_deepseek_tool_roundtrip(monkeypatch):
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
    result=asyncio.run(chat.answer(ChatRequest(message="请识别参数",dataset_id="data"), invoke))
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
