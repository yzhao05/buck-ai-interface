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


def saved_request(body, result):
    body = copy.deepcopy(body)
    body["current_result_analysis"] = True
    body["saved_result"] = {"id": body["result_id"], "created": 1000, "updated": 1000,
                            "dataset": {"filename": "archived.csv", "sha256": "a"*64},
                            "result": {**copy.deepcopy(result), "options": copy.deepcopy(body["options"]), "mapping": None}}
    return body


def test_saved_result_without_dataset_or_cached_job(context, monkeypatch):
    client, body, result = context
    body = saved_request(body, result)
    main.jobs.pop(body["result_id"])
    def no_fit(*args, **kwargs):
        pytest.fail("Reading a saved result must not start identification")
    monkeypatch.setattr(main, "start_fit", no_fit)
    response = client.post("/api/chat", json=body)
    assert response.status_code == 200
    assert "电感相对误差 0.0368333%" in response.json()["answer"]
    assert not response.json().get("job_id")


@pytest.mark.parametrize("change", ["id", "options", "mapping", "execute"])
def test_saved_result_requires_matching_read_only_request(context, change):
    client, body, result = context
    body = saved_request(body, result)
    if change == "id":
        body["saved_result"]["id"] = "different-run"
    elif change == "options":
        body["options"]["vin"] += 1
    elif change == "mapping":
        body["saved_result"]["result"]["mapping"] = {"time": "different"}
    else:
        body["identification_enabled"] = True
    assert client.post("/api/chat", json=body).status_code == 422


def test_saved_snapshot_provenance_reaches_model(context, monkeypatch):
    client, body, result = context
    body = saved_request(body, result)
    main.jobs.pop(body["result_id"])
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only")
    class Provider:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            messages = kwargs["json"]["messages"]
            assert "来自用户会话保存的实验快照" in messages[0]["content"]
            data = json.loads(messages[1]["content"].split("：", 1)[1])["previous_result"]
            assert data["result_source"] == "saved_conversation_snapshot"
            assert data["result_id"] == body["result_id"]
            assert data["parameters"] == result["parameters"]
            assert data["parameter_comparison"]["relative_error_pct"]["L"] == pytest.approx(.0368333333333)
            return httpx.Response(200, request=httpx.Request("POST", url), json={"choices": [{"message": {"role": "assistant", "content": "已读取历史结果。"}}]})
    monkeypatch.setattr(chat.httpx, "AsyncClient", Provider)
    response = client.post("/api/chat", json=body)
    assert response.status_code == 200
    assert response.json()["mode"] == "deepseek"


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
            assert "完整分析保留预测参数、单位、R 固定或估计身份" in messages[0]["content"]
            assert "不固定使用同一组标题" in messages[0]["content"]
            assert "不为了让回答不同而提出新原因" in messages[0]["content"]
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


@pytest.mark.parametrize("legacy", [False, True])
def test_current_lc_analysis_excludes_old_lcr_on_repeated_requests(context, monkeypatch, legacy):
    client, body, _ = context
    body["message"] = "请分析当前辨识结果的误差，并给出有依据的修改计划。"
    if not legacy:
        body["current_result_analysis"] = True
    body["options"]["mode"] = "LC"
    job = main.jobs[body["result_id"]]
    job["config"]["options"] = copy.deepcopy(body["options"])
    job["result"]["parameters"] = {"L_H": 60.020985e-6, "C_F": 493.6151e-6, "R_ohm": 6}
    job["result"]["options"] = copy.deepcopy(body["options"])
    body["history"] = [{"role": "assistant", "content": "OLD_LCR_RESULT: C=489.8 uF, R=5.7 ohm"}]
    body["conversation_messages"] = [{"id": "old-answer", "role": "assistant", "content": body["history"][0]["content"], "created": 1}]
    body["experiments"] = [{"id": "OLD_LCR_RESULT", "dataset": {"filename": "same.csv"},
                            "parameters": {"L_H": 60.02e-6, "C_F": 489.8e-6, "R_ohm": 5.7}}]
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only")
    async def forbidden_classifier(request):
        raise AssertionError("The explicit result action must not rely on model classification")
    monkeypatch.setattr(chat, "classify", forbidden_classifier)
    seen = []
    class Provider:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            payload = kwargs["json"]
            assert "OLD_LCR_RESULT" not in json.dumps(payload)
            names = {t["function"]["name"] for t in payload["tools"]}
            assert not names.intersection({"start_identification", "get_experiment_history", "get_analysis_history", "search_conversation", "read_conversation_message"})
            messages = payload["messages"]
            data = json.loads(messages[1]["content"].split("：", 1)[1])
            assert data["analysis_scope"] == "current_result"
            assert data["current_result_id"] == body["result_id"]
            result = data["previous_result"]
            assert result["result_id"] == body["result_id"]
            assert result["options"]["mode"] == "LC" and result["R_estimated"] is False
            assert result["parameters"]["R_ohm"] == 6
            assert result["parameters"]["C_F"] == pytest.approx(493.6151e-6)
            assert result["parameter_comparison"]["relative_error_pct"]["C"] == pytest.approx(1.27698)
            seen.append(result)
            if messages[-1]["role"] != "tool":
                msg = {"role": "assistant", "content": None, "tool_calls": [{"id": "current", "type": "function",
                       "function": {"name": "get_identification_result", "arguments": "{}"}}]}
            else:
                assert json.loads(messages[-1]["content"]) == result
                msg = {"role": "assistant", "content": "LC，R 固定为 6 Ω，C=493.6151 μF。"}
            return httpx.Response(200, request=httpx.Request("POST", url), json={"choices": [{"message": msg}]})
    monkeypatch.setattr(chat.httpx, "AsyncClient", Provider)
    for _ in range(2):
        response = client.post("/api/chat", json=body)
        assert response.status_code == 200
        assert response.json()["mode"] == "deepseek"
        assert "固定为 6" in response.json()["answer"]
    assert len(seen) == 4


@pytest.mark.parametrize("change", ["expired", "options", "owner"])
def test_current_analysis_unavailable_never_uses_history(context, monkeypatch, change):
    client, body, _ = context
    body.update(current_result_analysis=True, history=[{"role": "assistant", "content": "旧结果 R=5.7"}])
    if change == "expired":
        main.jobs.pop(body["result_id"])
    elif change == "options":
        body["options"]["mode"] = "LC"
    else:
        client.headers["X-Session-ID"] = str(uuid.uuid4())
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only")
    def forbidden_provider(**kwargs):
        raise AssertionError("Missing current result must not be sent to the model")
    monkeypatch.setattr(chat.httpx, "AsyncClient", forbidden_provider)
    response = client.post("/api/chat", json=body)
    assert response.status_code == 200
    assert "本次没有使用历史结果" in response.json()["answer"]
    assert "5.7" not in response.json()["answer"]


@pytest.mark.parametrize("question", ["为什么？", "还有其他可能吗？", "请复查刚才的结论", "Why?", "Any other possible causes?", "Please recheck that conclusion"])
def test_followup_keeps_current_analysis_and_live_truth_only(context, monkeypatch, question):
    client, body, _ = context
    body["message"] = question
    body["conversation_messages"] = [
        {"id": "old", "role": "assistant", "content": "OLD_LCR: R=5.7", "analysis": {"result_id": "old-result"}},
        {"id": "user-new", "role": "user", "content": "分析当前结果"},
        {"id": "new", "role": "assistant", "content": "CURRENT_EXPLANATION: 请核对这个假设。", "request_message_id": "user-new", "analysis": {"result_id": body["result_id"]}},
        {"id": "next-question", "role": "user", "content": "这个解释有依据吗？"},
        {"id": "next-answer", "role": "assistant", "content": "FOLLOWUP_EXPLANATION: 这是待验证假设。"},
    ]
    body["history"] = [{"role": "assistant", "content": "OLD_LCR: stale recent history"}]
    # Re-evaluate the ground truth instead of trusting numbers in earlier prose.
    body["ground_truth"]["C_F"] = 510e-6
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only")
    seen = []
    class Provider:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            payload = kwargs["json"]; serialized = json.dumps(payload)
            assert "OLD_LCR" not in serialized
            assert "CURRENT_EXPLANATION" in serialized and "FOLLOWUP_EXPLANATION" in serialized
            assert "temperature" not in payload
            messages = payload["messages"]
            assert "重新核对上一条推理的数字、假设和因果链" in messages[0]["content"]
            assert "本次是同一结果的继续讨论" in messages[0]["content"]
            data = json.loads(messages[1]["content"].split("：", 1)[1])
            assert data["analysis_scope"] == "result_followup"
            assert data["current_result_id"] == body["result_id"]
            assert data["previous_result"]["parameter_comparison"]["ground_truth"]["C_F"] == 510e-6
            assert data["experiment_history"] == []
            assert not {t["function"]["name"] for t in payload["tools"]}.intersection({"start_identification", "get_experiment_history", "search_conversation"})
            seen.append(data)
            return httpx.Response(200, request=httpx.Request("POST", url), json={"choices": [{"message": {"role": "assistant", "content": "针对前文证据继续解释。"}}]})
    monkeypatch.setattr(chat.httpx, "AsyncClient", Provider)
    response = client.post("/api/chat", json=body)
    assert response.status_code == 200
    assert response.json()["mode"] == "deepseek" and response.json()["route"]["task"] == "diagnosis"
    assert len(seen) == 1
