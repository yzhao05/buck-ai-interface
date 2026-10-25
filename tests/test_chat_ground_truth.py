import copy
import json
import time
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import chat, main
from backend.schemas import FitOptions
from backend.task_router import make_route, rule_selection


@pytest.fixture
def context(monkeypatch):
    monkeypatch.setattr(main, "TOKEN", "")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    async def classify(request):
        return make_route(rule_selection(request.message), request.message, "rules")
    monkeypatch.setattr(chat, "classify", classify)
    owner, identifier = str(uuid.uuid4()), uuid.uuid4().hex
    result = {"parameters": {"L_H": 59.9779e-6, "C_F": 500.03e-6, "R_ohm": 6},
              "method": "weighted_integral", "R_estimated": False, "quality": "模型内拟合一致",
              "metrics": {"current_rmse_A": .0088, "voltage_rmse_V": .0008853,
                          "current_nrmse_pct": .378, "voltage_nrmse_pct": .005},
              "diagnostics": {}, "warnings": [], "parameter_error": None}
    options = FitOptions(method="weighted_integral").model_dump()
    monkeypatch.setitem(main.jobs, identifier, {"owner": owner, "dataset_id": None, "status": "complete",
        "created": time.time(), "result": copy.deepcopy(result), "config": {"mapping": None, "options": options}})
    client = TestClient(main.app)
    client.headers["X-Session-ID"] = owner
    body = {"message": "分析结果误差", "result_id": identifier, "options": copy.deepcopy(options),
            "ground_truth": {"result_id": identifier, "L_H": 60e-6, "C_F": 500e-6}}
    return client, body, result


def test_local_comparison_and_no_persistent_truth(context):
    client, body, original = context
    response = client.post("/api/chat", json=body)
    assert response.status_code == 200
    answer = response.json()["answer"]
    assert "真实电感 60 μH" in answer and "真实电容 500 μF" in answer
    assert "电感相对误差 0.0368333%" in answer and "电容相对误差 0.006%" in answer
    assert "未提供" not in answer and "未独立核验" not in answer and "RMSE" not in answer
    assert main.jobs[body["result_id"]]["result"] == original
    del body["ground_truth"]
    answer = client.post("/api/chat", json=body).json()["answer"]
    assert "未计算参数相对误差" in answer
    assert "电感相对误差 0.0368333%" not in answer


@pytest.mark.parametrize("change", ["result", "owner", "options"])
def test_truth_cannot_bypass_result_binding(context, change):
    client, body, _ = context
    if change == "result":
        body["ground_truth"]["result_id"] = "another-result"
    elif change == "owner":
        client.headers["X-Session-ID"] = str(uuid.uuid4())
    else:
        body["options"]["vin"] = 29
    response = client.post("/api/chat", json=body)
    if change == "result":
        assert response.status_code == 422
    else:
        assert response.status_code == 200
        assert "当前没有" in response.json()["answer"]
        assert "电感相对误差" not in response.json()["answer"]


@pytest.mark.parametrize("value", [0, -1, "nan", "inf", 1e31, 1e-320])
def test_invalid_or_overflowing_truth_is_not_used(context, value):
    client, body, _ = context
    body["ground_truth"]["L_H"] = value
    response = client.post("/api/chat", json=body)
    if value == 1e-320:
        assert response.status_code == 200
        assert "超出可计算范围" in response.json()["answer"]
    else:
        assert response.status_code == 422


@pytest.mark.parametrize("fail_provider", [False, True])
def test_deepseek_context_tool_and_fallback_share_comparison(context, monkeypatch, fail_provider):
    client, body, _ = context
    stored = main.jobs[body["result_id"]]["result"]
    stored["diagnostics"]["pann"] = {"best_epoch": 100, "best_step": 150, "best_at_budget_end": True,
                                      "completed_epochs": 100, "convergence": {"status": "stable", "reasons": []}}
    stored["warnings"] = ["最佳训练结果位于最后一轮，仅作记录；是否稳定由末段损失和参数变化共同判断。", "保留其他计算条件提醒。"]
    stored_before = copy.deepcopy(stored)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only")
    seen = []
    class Provider:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            messages = kwargs["json"]["messages"]
            assert "不列出或展开分析‘其他过程事实’‘数据和波形事实’" in messages[0]["content"]
            assert "默认只展示预测参数、真值相对误差、简短结论及必要建议" in messages[0]["content"]
            assert "分析正文不输出‘明确无法给出的结论’" in messages[0]["content"]
            assert "不以‘如果你希望，我可以……’" in messages[0]["content"]
            data = json.loads(messages[1]["content"].split("：", 1)[1])["previous_result"]
            seen.append(copy.deepcopy(data))
            assert data["parameter_error"] == data["parameter_comparison"]
            assert data["assessment"]["parameter_accuracy"]["status"] == "compared"
            assert data["parameter_comparison"]["relative_error_pct"]["L"] == pytest.approx(.0368333333333)
            assert "best_epoch" not in data["diagnostics"]["pann"]
            assert data["diagnostics"]["pann"]["convergence"]["status"] == "stable"
            assert data["warnings"] == ["保留其他计算条件提醒。"]
            if fail_provider:
                raise httpx.ReadTimeout("simulated")
            if len(seen) == 1:
                msg = {"role": "assistant", "content": None, "tool_calls": [{"id": "read-result", "type": "function",
                       "function": {"name": "get_identification_result", "arguments": "{}"}}]}
            else:
                tool = json.loads(messages[-1]["content"])
                assert tool["parameter_comparison"] == data["parameter_comparison"]
                assert tool["diagnostics"] == data["diagnostics"] and tool["warnings"] == data["warnings"]
                msg = {"role": "assistant", "content": "已读取真值对照。"}
            return httpx.Response(200, request=httpx.Request("POST", url), json={"choices": [{"message": msg}]})
    monkeypatch.setattr(chat.httpx, "AsyncClient", Provider)
    response = client.post("/api/chat", json=body)
    assert response.status_code == 200
    assert len(seen) == (1 if fail_provider else 2)
    assert stored == stored_before
    if fail_provider:
        assert response.json()["mode"] == "local_fallback"
        assert "电感相对误差 0.0368333%" in response.json()["answer"]
        assert "最后一轮" not in response.json()["answer"]
        assert "保留其他计算条件提醒" not in response.json()["answer"]
        assert "RMSE" not in response.json()["answer"]


def test_concise_report_still_mentions_failed_validation(context):
    client, body, _ = context
    stored = main.jobs[body["result_id"]]["result"]
    stored["metrics"]["current_nrmse_pct"] = 8
    stored["warnings"] = ["窗口选择过程", "PWM 重建边沿"]
    answer = client.post("/api/chat", json=body).json()["answer"]
    assert "验证尚未通过" in answer
    assert "电感相对误差" in answer
    for hidden in ("其他过程事实", "数据和波形事实", "窗口选择过程", "PWM 重建边沿", "RMSE", "可能原因"):
        assert hidden not in answer


@pytest.mark.parametrize("include_truth", [False, True])
def test_analysis_omits_routine_limitations_without_inventing_accuracy(context, include_truth):
    client, body, _ = context
    stored = main.jobs[body["result_id"]]["result"]
    stored["method"] = "pann"
    stored["diagnostics"] = {"pann": {"convergence": {"status": "stable", "reasons": []}}}
    if not include_truth:
        del body["ground_truth"]
    answer = client.post("/api/chat", json=body).json()["answer"]
    assert "符合稳定性判据" in answer
    for hidden in ("明确无法", "不能得出", "parameter_error", "unverified", "非收敛证明", "未独立核验", "如果你希望"):
        assert hidden not in answer
    if include_truth:
        assert "电感相对误差 0.0368333%" in answer
    else:
        assert "未计算参数相对误差" in answer
        assert "%" not in answer


def test_analysis_ignores_only_final_best_evidence():
    result = {"diagnostics": {"pann": {"best_epoch": 100, "best_step": 150, "best_at_budget_end": True,
              "completed_epochs": 100, "convergence": {"status": "not_stable", "reasons": [{"code": "pann_stability_short"}]}}},
              "warnings": ["PANN 最佳结果出现在最后一次训练评估，尚不能确认充分收敛；请增加训练轮数或对照不同窗口长度。", "其他警告"],
              "review_reasons": [{"code": "pann_best_at_end"}, {"code": "current_nrmse", "value": 8, "threshold": 5}]}
    before = copy.deepcopy(result)
    cleaned = chat.analysis_result(result)
    assert result == before
    assert cleaned["warnings"] == ["其他警告"]
    assert cleaned["review_reasons"] == [{"code": "current_nrmse", "value": 8, "threshold": 5}]
    assert cleaned["diagnostics"]["pann"] == {"completed_epochs": 100,
        "convergence": {"status": "not_stable", "reasons": [{"code": "pann_stability_short"}]}}


@pytest.mark.parametrize("question,expected,excluded", [
    ("当前结果的 RMSE 是多少？", "留出电流 RMSE = 0.0088 A", "留出电流 NRMSE"),
    ("当前结果的 NRMSE 是多少？", "留出电流 NRMSE = 0.378%", "留出电流 RMSE"),
    ("当前结果的均方根误差是多少？", "留出电流 RMSE = 0.0088 A", "留出电流 NRMSE"),
    ("当前结果的RMSE是多少？", "留出电流 RMSE = 0.0088 A", "留出电流 NRMSE"),
])
def test_explicit_waveform_error_question_is_answered(context, question, expected, excluded):
    client, body, _ = context
    body["message"] = question
    answer = client.post("/api/chat", json=body).json()["answer"]
    assert expected in answer and excluded not in answer
    assert "电感相对误差" in answer
