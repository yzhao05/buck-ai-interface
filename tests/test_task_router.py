"""Classifier behavior, task-specific dispatch, and executor boundary regressions."""
import asyncio
import json

import httpx
import pytest

from backend import chat, task_router
from backend.schemas import ChatRequest


def completion(message, url="https://example.test/chat/completions"):
    return httpx.Response(200, request=httpx.Request("POST", url), json={"choices": [{"message": message}]})


def selection(task, secondary=None):
    return {"role": "assistant", "content": None, "tool_calls": [{"id": "route-1", "type": "function",
            "function": {"name": "route_task", "arguments": json.dumps({"task": task, "secondary_tasks": secondary or []})}}]}


@pytest.mark.parametrize("message,task", [
    ("如何上传 CSV？", "guidance"), ("推导 Buck 小信号传递函数", "knowledge"),
    ("请分析当前波形", "waveform"), ("开始参数辨识", "identification"),
    ("分析结果误差并给出修改计划", "diagnosis"), ("电容还有多久寿命", "hardware"),
    ("你好", "general"), ("Explain the identification equations", "knowledge"),
    ("Analyze current waveforms", "waveform"), ("Identify parameters and explain formulas", "identification"),
    ("Analyze the errors in the current results", "diagnosis"), ("How to upload CSV?", "guidance"),
    ("MOSFET thermal limits", "hardware"), ("继续", "general"),
    ("参数辨识按钮在哪里？", "guidance"), ("查看辨识结果", "diagnosis"),
    ("辨识已经完成了", "diagnosis"),
    ("Identify causes of the errors", "diagnosis"), ("识别这个 MOSFET 的型号", "hardware"),
])
def test_rules_without_key(monkeypatch, message, task):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    route = asyncio.run(task_router.classify(ChatRequest(message=message)))
    assert route["task"] == task
    assert route["mode"] == "rules"
    assert ("start_identification" in task_router.route_tools(route)) == (task == "identification")


def test_classifier_is_a_separate_bounded_model_call(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-secret")
    monkeypatch.setenv("DEEPSEEK_ROUTER_MODEL", "router-test-model")
    requests = []

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["timeout"].read == 15
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            requests.append(kwargs["json"])
            return completion(selection("diagnosis"), url)

    monkeypatch.setattr(task_router.httpx, "AsyncClient", Client)
    request = ChatRequest(message="这个结果该怎么改？", dataset_id="private-dataset", result_id="private-result",
                          history=[{"role": "user", "content": f"history-{i}"} for i in range(10)])
    route = asyncio.run(task_router.classify(request))
    assert route["task"] == "diagnosis" and route["mode"] == "agent"
    assert len(requests) == 1
    body = requests[0]
    assert body["model"] == "router-test-model" and body["thinking"] == {"type": "disabled"}
    assert [t["function"]["name"] for t in body["tools"]] == ["route_task"]
    assert len(body["messages"]) == 9  # two system, six history, current user
    serialized = json.dumps(body)
    assert "history-0" not in serialized and "history-9" in serialized
    assert "private-dataset" not in serialized and "private-result" not in serialized
    assert "test-secret" not in json.dumps(route)


@pytest.mark.parametrize("bad", [
    {"task": "shell", "secondary_tasks": []},
    {"task": "knowledge", "secondary_tasks": ["general"]*3},
    {"task": "knowledge", "tools": ["start_identification"]},
    {"task": 1}, ["knowledge"], "invalid JSON",
])
def test_invalid_model_output_falls_back_without_execution(monkeypatch, bad):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-secret")

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            message = selection("knowledge")
            message["tool_calls"][0]["function"]["arguments"] = bad if isinstance(bad, str) else json.dumps(bad)
            return completion(message, url)

    monkeypatch.setattr(task_router.httpx, "AsyncClient", Client)
    route = asyncio.run(task_router.classify(ChatRequest(message="推导 Buck 公式")))
    assert route["task"] == "knowledge" and route["mode"] == "fallback"
    assert route["notice"] and "start_identification" not in task_router.route_tools(route)


def test_router_timeout_is_labeled(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "secret-never-return")

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, *args, **kwargs):
            raise httpx.ReadTimeout("secret-never-return")

    monkeypatch.setattr(task_router.httpx, "AsyncClient", Client)
    route = asyncio.run(task_router.classify(ChatRequest(message="电容寿命")))
    assert route["mode"] == "fallback" and route["task"] == "hardware"
    assert "secret-never-return" not in json.dumps(route)


@pytest.mark.parametrize("message", ["只解释辨识原理", "不要辨识，只解释公式", "好的", "continue",
    "好的。", "continue!", "停止辨识", "查看辨识结果", "参数辨识按钮在哪里？", "辨识已经完成了",
    "“开始参数辨识”", "Explain identification status", "Show identification results",
    "Identify causes of the errors", "识别这个 MOSFET 的型号"])
def test_incorrect_identification_classification_cannot_override_read_only_intent(message):
    route = task_router.make_route(task_router.TaskSelection(task="identification"), message, "agent")
    assert "start_identification" not in task_router.route_tools(route)


def test_secondary_task_cannot_grant_execution():
    route = task_router.make_route(task_router.TaskSelection(task="hardware", secondary_tasks=["identification"]),
                                  "电容寿命是多少？", "agent")
    assert route["secondary_tasks"] == []
    assert "start_identification" not in task_router.route_tools(route)


def test_mixed_task_prompt_has_no_read_only_instruction():
    route = task_router.make_route(task_router.TaskSelection(task="identification", secondary_tasks=["guidance", "diagnosis"]),
                                  "先辨识参数，再告诉我操作步骤", "agent")
    prompt = task_router.route_prompt(route)
    assert "不要启动计算" not in prompt and "不自动重新辨识" not in prompt
    readonly = task_router.make_route(task_router.TaskSelection(task="guidance"), "如何上传数据？", "agent")
    assert "不启动新的数值辨识" in task_router.route_prompt(readonly)


@pytest.mark.parametrize("task,message,expected_prefetch", [
    ("knowledge", "这个传递函数呢？", []),
    ("waveform", "看看这一段", ["get_waveform_summary"]),
    ("diagnosis", "这个结果呢？", ["get_waveform_summary", "get_identification_result"]),
])
def test_model_classification_drives_actual_dispatch(monkeypatch, task, message, expected_prefetch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    requests, invoked, tool_errors = [], [], []

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            body = kwargs["json"]
            requests.append(body)
            if body["tools"][0]["function"]["name"] == "route_task":
                return completion(selection(task), url)
            if body["messages"][-1]["role"] == "tool":
                tool_errors.append(json.loads(body["messages"][-1]["content"]))
                return completion({"role": "assistant", "content": "按当前任务回答。"}, url)
            # An erroneous executor tries a tool that the route did not grant.
            return completion({"role": "assistant", "content": None, "tool_calls": [{"id": "bad-fit", "type": "function",
                "function": {"name": "start_identification", "arguments": "{}"}}]}, url)

    async def invoke(name):
        invoked.append(name)
        assert name != "start_identification"
        return None

    monkeypatch.setattr(task_router.httpx, "AsyncClient", Client)
    request = ChatRequest(message=message, dataset_id="data", result_id="result", identification_enabled=True,
                          mapping={"time": "t", "current": "i", "voltage": "v"})
    response = asyncio.run(chat.answer(request, invoke))
    assert len(requests) == 3  # classify, answer tool call, final answer
    assert response["route"]["task"] == task and response["route"]["mode"] == "agent"
    assert invoked == expected_prefetch and response["job_id"] is None
    assert "error" in tool_errors[0] and response["tool_calls"][0]["status"] == "未执行"
    assert task_router.TASKS[task]["prompt"] in requests[1]["messages"][0]["content"]
    assert "start_identification" not in {t["function"]["name"] for t in requests[1]["tools"]}


def test_mixed_request_routes_to_identification_and_knowledge(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    calls = []

    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            body = kwargs["json"]
            if body["tools"][0]["function"]["name"] == "route_task":
                return completion(selection("identification", ["knowledge"]), url)
            if body["messages"][-1]["role"] == "tool":
                return completion({"role": "assistant", "content": "已启动任务，下面解释公式。"}, url)
            return completion({"role": "assistant", "content": None, "tool_calls": [{"id": "fit", "type": "function",
                "function": {"name": "start_identification", "arguments": "{}"}}]}, url)

    async def invoke(name):
        calls.append(name)
        return {"job_id": "real-test-job"}

    monkeypatch.setattr(task_router.httpx, "AsyncClient", Client)
    response = asyncio.run(chat.answer(ChatRequest(message="先辨识参数，再解释公式", identification_enabled=True), invoke))
    assert response["route"]["task"] == "identification"
    assert response["route"]["secondary_tasks"] == ["knowledge"]
    assert response["job_id"] == "real-test-job" and calls == ["start_identification"]


@pytest.mark.parametrize("question", ["为什么？", "还有其他可能吗？", "请复查刚才的结论", "Why?", "Any other possible causes?", "Please recheck that conclusion"])
def test_result_followup_requires_matching_recent_analysis(question):
    request = ChatRequest(message=question, result_id="current", conversation_messages=[
        {"id": "a", "role": "assistant", "content": "current analysis", "analysis": {"result_id": "current"}}])
    assert task_router.result_followup_history(request) == [{"role": "assistant", "content": "current analysis"}]
    request.result_id = "different"
    assert task_router.result_followup_history(request) is None


@pytest.mark.parametrize("question", ["比较刚才两次结果", "为什么之前那几次不一样，请检索", "为什么上次的 LCR 结果不同？", "Why was R different in the previous experiment?", "Why was R 5.7 last time?", "请开始参数辨识", "总结整个会话", "如何上传 CSV", "为什么上传失败", "为什么 MOSFET 寿命短", "Search previous analyses", "Compare these results"])
def test_unrelated_requests_do_not_become_result_followups(question):
    request = ChatRequest(message=question, result_id="current", conversation_messages=[
        {"id": "a", "role": "assistant", "content": "current analysis", "analysis": {"result_id": "current"}}])
    assert task_router.result_followup_history(request) is None


def test_long_followup_keeps_analysis_anchor_and_marks_excerpts():
    messages = [{"id": "analysis", "role": "assistant", "content": "A"*9000, "analysis": {"result_id": "current"}}]
    messages += [{"id": str(i), "role": "user" if i%2==0 else "assistant", "content": f"turn-{i}"} for i in range(16)]
    request = ChatRequest(message="为什么？", result_id="current", conversation_messages=messages)
    history = task_router.result_followup_history(request)
    assert history[0]["content"].startswith("A"*8000)
    assert "片段" in history[0]["content"]
    assert [m["content"] for m in history[1:]] == [f"turn-{i}" for i in range(8,16)]
