import copy
import json
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import chat, main
from backend.schemas import ChatExperiment
from backend.experiment_history import summarize_experiments
from backend.task_router import make_route, rule_selection


def experiments():
    return [{"id": f"experiment-{i}", "dataset": {"filename": "CASE16A_clean_grid.csv", "sha256": "a"*64},
             "method": ["pann", "weighted_integral"][i % 2],
             "pwm_source": "measured" if i < 2 else "controller",
             "parameters": {"L_H": (60+i)*1e-6, "C_F": 500e-6, "R_ohm": 6},
             "ground_truth": {"result_id": f"experiment-{i}", "L_H": 60e-6, "C_F": 500e-6} if i == 3 else None}
            for i in range(4)]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "TOKEN", "")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    async def classify(request):
        return make_route(rule_selection(request.message), request.message, "rules")
    monkeypatch.setattr(chat, "classify", classify)
    return TestClient(main.app, headers={"X-Session-ID": str(uuid.uuid4())})


def test_compare_saved_runs_without_live_job_or_csv(client):
    # Expired current cache must not interfere with reading stored experiments.
    before = set(main.jobs)
    response = client.post("/api/chat", json={"message": "纵向横向对比这四个情况", "experiments": experiments(),
        "dataset_id": "expired", "result_id": "expired", "identification_enabled": True,
        "mapping": {"time": "t", "current": "i", "voltage": "v"}})
    assert response.status_code == 200, response.text
    answer = response.json()["answer"]
    assert "已保存 4 次实验" in answer and "5% / 0%" in answer
    assert answer.count("CASE16A_clean_grid.csv") == 4
    assert answer.count("未计算") == 3
    assert "PANN | 实测" in answer and "PANN | 重建" in answer
    assert "抗噪加权积分 | 实测" in answer and "抗噪加权积分 | 重建" in answer
    assert response.json()["job_id"] is None and set(main.jobs) == before


def test_explicit_run_then_compare_retains_execution_context(client, monkeypatch):
    calls=[]
    def start(body, owner):
        calls.append(body.model_dump())
        return {"job_id":"mixed-request-job","status":"queued"}
    monkeypatch.setattr(main,"start_fit",start)
    monkeypatch.setattr(main,"get_dataset",lambda *args: object())
    monkeypatch.setattr(main,"analyze",lambda *args: {})
    response=client.post("/api/chat",json={"message":"先辨识参数，再比较这四次实验", "experiments":experiments(),
        "dataset_id":"selected-data", "mapping":{"time":"t","current":"i","voltage":"v"},
        "options":{"method":"weighted_integral","vin":30},"identification_enabled":True})
    assert response.status_code == 200, response.text
    assert response.json()["job_id"] == "mixed-request-job"
    assert len(calls) == 1 and calls[0]["dataset_id"] == "selected-data"
    assert calls[0]["options"]["vin"] == 30 and calls[0]["options"]["method"] == "weighted_integral"


@pytest.mark.parametrize("extra", ["mapping", "metrics", "training_metrics", "chart", "epoch_history", "result"])
def test_history_payload_rejects_unapproved_fields(client, extra):
    records=experiments();records[0][extra]={"data": [1,2,3]}
    assert client.post("/api/chat", json={"message": "对比实验", "experiments": records}).status_code == 422


def test_truth_binding_and_recomputed_relative_error(client):
    records=experiments()
    records[3]["ground_truth"]["result_id"]="experiment-0"
    assert client.post("/api/chat", json={"message": "对比实验", "experiments": records}).status_code == 422
    records=experiments();original=copy.deepcopy(records)
    summary=summarize_experiments([ChatExperiment.model_validate(r) for r in records])
    assert all(r["parameter_comparison"] is None for r in summary[:3])
    assert summary[3]["parameter_comparison"]["relative_error_pct"]["L"] == pytest.approx(5)
    assert records==original


@pytest.mark.parametrize("provider_fails", [False, True])
def test_provider_context_tool_reads_and_fallback(client, monkeypatch, provider_fails):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    seen=[]
    class Provider:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            payload=kwargs["json"];messages=payload["messages"]
            seen.append(copy.deepcopy(messages))
            context=json.loads(messages[1]["content"].split("：",1)[1])
            assert context["configuration"] is None
            records=context["experiment_history"]
            assert len(records)==4
            for record in records:
                assert set(record)=={"id","dataset","method","pwm_source","parameters","parameter_comparison"}
            assert records[0]["parameter_comparison"] is None
            assert records[3]["parameter_comparison"]["relative_error_pct"]["L"]==pytest.approx(5)
            assert "start_identification" not in [t["function"]["name"] for t in payload["tools"]]
            if provider_fails: raise httpx.ConnectError("offline")
            if len(seen)==1:
                message={"role":"assistant","content":None,"tool_calls":[
                    {"id":"read","type":"function","function":{"name":"get_experiment_history","arguments":"{}"}},
                    {"id":"blocked","type":"function","function":{"name":"start_identification","arguments":"{}"}}]}
            else:
                output=json.loads(messages[-2]["content"])
                assert output["count"]==4 and output["experiments"]==records
                assert "error" in json.loads(messages[-1]["content"])
                message={"role":"assistant","content":"已对比四组参数。"}
            return httpx.Response(200, request=httpx.Request("POST",url), json={"choices":[{"message":message}]})
    monkeypatch.setattr(chat.httpx,"AsyncClient",Provider)
    response=client.post("/api/chat",json={"message":"比较这四次实验", "experiments":experiments()})
    assert response.status_code==200,response.text
    assert response.json()["job_id"] is None
    assert len(seen)==(1 if provider_fails else 2)
    assert response.json()["mode"]==("local_fallback" if provider_fails else "deepseek")
    if provider_fails: assert "已保存 4 次实验" in response.json()["answer"]


def test_current_result_traces_are_not_forwarded():
    result={"chart":{"raw":[1]},"diagnostics":{"pann":{"history":[2],"epoch_history":[3],"convergence":{"status":"stable"}}}}
    cleaned=chat.analysis_result(result)
    assert "chart" not in cleaned
    assert cleaned["diagnostics"]["pann"]=={"convergence":{"status":"stable"}}
    assert result["chart"]["raw"]==[1] and result["diagnostics"]["pann"]["history"]==[2]
