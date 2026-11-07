import asyncio
import json

import httpx
import pytest

from backend import chat, configuration
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
            if kwargs['json']['messages'][-1]['role']=='tool':
                return httpx.Response(200, request=httpx.Request('POST',url),json={'choices':[{'message':{'role':'assistant','content':'Configuration proposed.'}}]})
            tool_calls = calls if calls is not None else [{"id": "tool-1", "type": "function", "function": {
                "name": "configure_identification", "arguments": json.dumps(arguments)}}]
            return httpx.Response(200, request=httpx.Request("POST", url), json={"choices": [{"message": {"tool_calls": tool_calls}}]})
    monkeypatch.setattr(chat.httpx, "AsyncClient", Client)
    return captured


def test_combined_command_proposes_patch_using_draft_not_displayed_result(monkeypatch):
    captured = provider(monkeypatch, {"open_panel": True, "changes": {"method": "pann", "mode": "LC", "resistance_ohm": 6}, "start_after_apply": True})
    async def invoke(name): pytest.fail("A proposal must never invoke old-configuration tools: " + name)
    req = request(options=FitOptions(vin=28, mode="LCR", resistance_ohm=5.7))
    result = asyncio.run(chat.answer(req, invoke))
    assert result["job_id"] is None
    assert result["ui_action"]["changes"] == {"method": "pann", "mode": "LC", "resistance_ohm": 6}
    assert result["ui_action"]["id"] == "operation-1"
    context = captured[0]["messages"][1]["content"]
    ui = json.loads(context.split('：',1)[1])['ui_context']
    assert ui['options']['vin']==35 and ui['options']['epochs']==230
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
    result = asyncio.run(chat.answer(request(message), None))
    assert result["ui_action"]["changes"] == changes
    assert not result["ui_action"]["start_after_apply"]


@pytest.mark.parametrize("changes", [
    {"resistance_ohm": 0}, {"resistance_ohm": 100001}, {"resistance_ohm": True},
    {"resistance_ohm": "6"}, {"resistance_ohm": None}, {"method": "shell"},
    {"mode": "OTHER"}, {"epochs": 301}, {"vin": -30},
])
def test_invalid_or_unavailable_fields_are_atomic_rejections(monkeypatch, changes):
    provider(monkeypatch, {"open_panel": True, "changes": changes, "start_after_apply": True})
    result = asyncio.run(chat.answer(request(), None))
    assert result["ui_action"] is None and result["job_id"] is None


def test_controller_conflict_no_action(monkeypatch):
    provider(monkeypatch, {"open_panel": True, "changes": {"mode": "LCR"}, "start_after_apply": False})
    req = request("切换到 LCR")
    req.ui_context.options.pwm_source = "controller"
    req.ui_context.options.mode = "LC"
    result = asyncio.run(chat.answer(req, None))
    assert result["ui_action"] is None


def test_no_key_does_not_start_old_configuration(monkeypatch):
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    async def invoke(_): pytest.fail("Must not start a fit on fallback")
    result = asyncio.run(chat.answer(request(), invoke))
    assert result["ui_action"] is None and result["job_id"] is None
    assert result["answer"] == "" and result["error_code"] == "missing_api_key"


def test_start_with_panel_closed_also_uses_frontend_apply_path(monkeypatch):
    provider(monkeypatch, {"open_panel": True, "changes": {}, "start_after_apply": True})
    async def invoke(_): pytest.fail("Must not execute before frontend application")
    action=asyncio.run(chat.answer(request("开始参数辨识"), invoke))["ui_action"]
    assert not action["start_after_apply"] and action['request_selection']
