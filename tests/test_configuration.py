import asyncio
import json

import httpx
import pytest

from backend import chat, configuration, task_router
from backend.schemas import ChatRequest, FitOptions


def request(message="进入参数辨识，选择 PANN，固定 R=6 Ω，然后开始。", **kwargs):
    return ChatRequest(message=message, ui_context={"request_id": "operation-1",
        "options": FitOptions(vin=35, frequency_hz=80000, epochs=230).model_dump()}, **kwargs)


def provider(monkeypatch, arguments, calls=None):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    captured = []
    class Client:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            captured.append(kwargs["json"])
            tool_calls = calls if calls is not None else [{"id": "tool-1", "type": "function", "function": {
                "name": "configure_identification", "arguments": json.dumps(arguments)}}]
            return httpx.Response(200, request=httpx.Request("POST", url), json={"choices": [{"message": {"tool_calls": tool_calls}}]})
    monkeypatch.setattr(configuration.httpx, "AsyncClient", Client)
    return captured


ROUTE = {"task": "configuration", "label": "辨识配置", "secondary_tasks": [], "mode": "agent"}


def test_combined_command_proposes_patch_using_draft_not_displayed_result(monkeypatch):
    captured = provider(monkeypatch, {"open_panel": True, "changes": {"method": "pann", "mode": "LC", "resistance_ohm": 6}, "start_after_apply": True})
    async def classify(_): return ROUTE
    monkeypatch.setattr(chat, "classify", classify)
    async def invoke(name): pytest.fail("A proposal must never invoke old-configuration tools: " + name)
    req = request(options=FitOptions(vin=28, mode="LCR", resistance_ohm=5.7))
    result = asyncio.run(chat.answer(req, invoke))
    assert result["job_id"] is None
    assert result["ui_action"]["changes"] == {"method": "pann", "mode": "LC", "resistance_ohm": 6}
    assert result["ui_action"]["id"] == "operation-1"
    context = captured[0]["messages"][1]["content"]
    assert '"vin":35.0' in context and '"epochs":230' in context
    assert "history" not in json.dumps(captured[0])
    assert req.options.mode == "LCR" and req.ui_context.options.mode == "LCR"


@pytest.mark.parametrize("message,changes,opened", [
    ("打开参数辨识面板", {}, True),
    ("将计算方法改为抗噪加权积分", {"method": "weighted_integral"}, False),
    ("改成 LCR 联合辨识", {"mode": "LCR"}, True),
    ("Set the load resistance to 8 ohms and use LC", {"mode": "LC", "resistance_ohm": 8}, True),
    ("选用 PANN，暂时不要开始", {"method": "pann"}, True),
])
def test_configuration_without_execution(monkeypatch, message, changes, opened):
    provider(monkeypatch, {"open_panel": opened, "changes": changes, "start_after_apply": False})
    result = asyncio.run(configuration.propose(request(message), ROUTE))
    assert result["ui_action"]["changes"] == changes
    assert not result["ui_action"]["start_after_apply"]


@pytest.mark.parametrize("changes", [
    {"resistance_ohm": 0}, {"resistance_ohm": 100001}, {"resistance_ohm": True},
    {"resistance_ohm": "6"}, {"resistance_ohm": None}, {"method": "shell"},
    {"mode": "OTHER"}, {"controller_confirmed": True}, {"epochs": 300}, {"vin": 30},
])
def test_invalid_or_unavailable_fields_are_atomic_rejections(monkeypatch, changes):
    provider(monkeypatch, {"open_panel": True, "changes": changes, "start_after_apply": True})
    result = asyncio.run(configuration.propose(request(), ROUTE))
    assert result["ui_action"] is None and result["job_id"] is None


@pytest.mark.parametrize("message", ["选择 PANN", "不要开始辨识", "先不运行", "如何开始辨识？", "例如：开始参数辨识", '解释“开始辨识”', "Use PANN, do not start", "Open identification"])
def test_execution_needs_explicit_request_even_if_model_says_start(monkeypatch, message):
    provider(monkeypatch, {"open_panel": True, "changes": {}, "start_after_apply": True})
    assert asyncio.run(configuration.propose(request(message), ROUTE))["ui_action"] is None


def test_controller_conflict_no_action(monkeypatch):
    provider(monkeypatch, {"open_panel": True, "changes": {"mode": "LCR"}, "start_after_apply": False})
    req = request("切换到 LCR")
    req.ui_context.options.pwm_source = "controller"
    req.ui_context.options.mode = "LC"
    result = asyncio.run(configuration.propose(req, ROUTE))
    assert result["ui_action"] is None and "固定 R" in result["answer"]


def test_no_key_does_not_start_old_configuration(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    async def invoke(_): pytest.fail("Must not start a fit on fallback")
    result = asyncio.run(chat.answer(request(), invoke))
    assert result["ui_action"] is None and result["job_id"] is None
    assert "本次未修改" in result["answer"]


def test_start_with_panel_closed_also_uses_frontend_apply_path(monkeypatch):
    provider(monkeypatch, {"open_panel": True, "changes": {}, "start_after_apply": True})
    async def classify(_): return {**ROUTE, "task": "identification"}
    monkeypatch.setattr(chat, "classify", classify)
    async def invoke(_): pytest.fail("Must not execute before frontend application")
    assert asyncio.run(chat.answer(request("开始参数辨识"), invoke))["ui_action"]["start_after_apply"]


@pytest.mark.parametrize("message", ["进入参数辨识，选择 PANN，固定 R=6，然后开始参数辨识", "打开参数辨识", "把负载电阻改为8欧姆", "Use PANN, set R to 8 ohms, then start identification"])
def test_configuration_route_takes_precedence(message):
    route = task_router.make_route(task_router.rule_selection(message), message, "rules")
    assert route["task"] == "configuration"
    assert "start_identification" not in task_router.route_tools(route)


def test_multiple_tool_calls_are_not_partially_applied(monkeypatch):
    provider(monkeypatch, {}, calls=[{}, {}])
    assert asyncio.run(configuration.propose(request(), ROUTE))["ui_action"] is None


def test_open_panel_after_analysis_is_not_a_result_followup(monkeypatch):
    provider(monkeypatch, {"open_panel": True, "changes": {}, "start_after_apply": False})
    async def classify(_): return ROUTE
    monkeypatch.setattr(chat, "classify", classify)
    req = request("展开参数辨识面板", result_id="result-1", conversation_messages=[
        {"id": "analysis-1", "role": "assistant", "content": "已有分析",
         "analysis": {"kind": "identification_analysis", "result_id": "result-1"}}])
    assert task_router.result_followup_history(req) is None
    async def invoke(name): pytest.fail("Opening the panel must not read the old result: " + name)
    assert asyncio.run(chat.answer(req, invoke))["ui_action"]["open_panel"]


@pytest.mark.parametrize("message", [
    "把训练轮数改为250，然后开始辨识", "选择 PANN，PWM 切换为实测，然后开始",
    "设置输入电压为35，再开始辨识", "Set epochs to 240 and start identification",
    "Change the time range?", "把窗口长度改为3周期然后开始",
    "把去噪窗口改为2周期，然后开始辨识", "Set smoothing to modified-sinc and start identification",
])
def test_unsupported_changes_cannot_be_ignored_and_started(monkeypatch, message):
    captured = provider(monkeypatch, {"open_panel": True, "changes": {}, "start_after_apply": True})
    result = asyncio.run(configuration.propose(request(message), ROUTE))
    if message == "Change the time range?":
        # No affirmative start request, regardless of provider output.
        assert result["ui_action"] is None
    else:
        assert result["ui_action"] is None and "暂不支持" in result["answer"]
        assert not captured
        assert task_router.rule_selection(message).task == "configuration"


@pytest.mark.parametrize("message", ["保持训练轮数不变，改用 PANN，然后开始", "Keep epochs unchanged and set R to 6"])
def test_preserving_other_fields_is_not_an_unsupported_edit(message):
    assert not configuration.unsupported_change(message)
