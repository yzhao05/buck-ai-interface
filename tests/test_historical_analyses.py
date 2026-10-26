import copy
import json
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import chat, main, archive
from backend.schemas import HistoricalAnalysis
from backend.historical_analyses import analysis_context, explicit_label
from backend.task_router import make_route, rule_selection


def replies():
    return [{"id":f"reply-{i}","request_id":f"request-{i}",
             "content":f"method={'pann' if i%2==0 else 'weighted_integral'}, pwm_source={'measured' if i<2 else 'controller'}\n"
                       + "历史分析正文。"*1500+f"\n完整回答末尾-{i}: L={60+i} μH，C=500 μF。"}
            for i in range(4)]


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main,"TOKEN","")
    monkeypatch.delenv("DEEPSEEK_API_KEY",raising=False)
    async def classify(request):
        return make_route(rule_selection(request.message),request.message,"rules")
    monkeypatch.setattr(chat,"classify",classify)
    return TestClient(main.app,headers={"X-Session-ID":str(uuid.uuid4())})


def test_full_bodies_and_two_comparison_axes():
    source=replies();original=copy.deepcopy(source)
    context=analysis_context([HistoricalAnalysis.model_validate(r) for r in source],9)
    assert context["source"]=="historical_assistant_answers"
    assert context["available_count"]==9 and context["selected_count"]==4
    assert [r["content"] for r in context["replies"]]==[r["content"] for r in source]
    assert all(len(r["content"])>8000 for r in context["replies"])
    assert context["comparison_groups"]["same_pwm"]["measured"]==["reply-0","reply-1"]
    assert context["comparison_groups"]["same_method"]["pann"]==["reply-0","reply-2"]
    assert source==original


def test_unknown_or_conflicting_labels_do_not_use_current_settings():
    assert explicit_label("PANN 从加权积分初值继续优化；可选择重建或实测 PWM。","method") is None
    assert explicit_label("method=pann, method=weighted_integral","method") is None
    assert explicit_label("可选方法：PANN/weighted_integral","method") is None
    assert explicit_label("PWM 来源：实测或重建","pwm_source") is None
    context=analysis_context([HistoricalAnalysis(id="a",request_id="u",content="预测 L=60 μH。")],1)
    assert context["replies"][0]["method"] is None and context["replies"][0]["pwm_source"] is None
    assert context["comparison_groups"]["unclassified"]==["a"]
    assert explicit_label("**方法：抗噪加权积分**","method")=="weighted_integral"
    assert explicit_label("PWM 来源：重建","pwm_source")=="controller"


def test_local_retrieval_without_experiment_snapshots_or_live_csv(client):
    before=set(main.jobs)
    response=client.post("/api/chat",json={"message":"对比本会话前面四次分析正文", "analysis_history":replies(),
        "analysis_history_requested":True,"analysis_history_total":4,"experiments":[],
        "dataset_id":"expired","result_id":"expired","mapping":{"time":"t","current":"i","voltage":"v"},
        "identification_enabled":True})
    assert response.status_code==200,response.text
    answer=response.json()["answer"]
    assert "4 条完整" in answer and "历史 AI 回答" in answer
    assert "PANN" in answer and "抗噪加权积分" in answer
    assert "尚未生成自动差异分析" in answer
    assert response.json()["job_id"] is None and set(main.jobs)==before


def test_missing_history_does_not_start_identification(client):
    response=client.post("/api/chat",json={"message":"对比历史分析", "analysis_history_requested":True})
    assert response.status_code==200
    assert "未找到" in response.json()["answer"] and response.json()["job_id"] is None


@pytest.mark.parametrize("fails",[False,True])
def test_provider_reads_full_replies_outside_recent_messages(client,monkeypatch,fails):
    monkeypatch.setenv("DEEPSEEK_API_KEY","test-key")
    seen=[];source=replies()
    class Provider:
        def __init__(self,**kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self,*args): pass
        async def post(self,url,**kwargs):
            body=kwargs["json"];messages=body["messages"];seen.append(body)
            context=json.loads(messages[1]["content"].split("：",1)[1])
            assert context["experiment_history"]==[] and context["previous_result"] is None
            assert context["analysis_history"]["selected_count"]==4
            assert [r["content"] for r in context["analysis_history"]["replies"]]==[r["content"] for r in source]
            assert "start_identification" not in [t["function"]["name"] for t in body["tools"]]
            if fails: raise httpx.ConnectError("offline")
            if len(seen)==1:
                message={"role":"assistant","content":None,"tool_calls":[{"id":"history","type":"function",
                    "function":{"name":"get_analysis_history","arguments":"{}"}}]}
            else:
                data=json.loads(messages[-1]["content"])
                assert data==context["analysis_history"]
                message={"role":"assistant","content":"依据本会话历史分析回答：按同一 PWM 与同一种方法分别对比四组。"}
            return httpx.Response(200,request=httpx.Request("POST",url),json={"choices":[{"message":message}]})
    monkeypatch.setattr(chat.httpx,"AsyncClient",Provider)
    response=client.post("/api/chat",json={"message":"依据前面四次分析回答比较", "analysis_history":source,
        "analysis_history_requested":True,"history":[{"role":"user","content":"后续闲聊"}]*10})
    assert response.status_code==200,response.text
    assert len(seen)==(1 if fails else 2)
    assert response.json()["mode"]==("local_fallback" if fails else "deepseek")


def test_analysis_metadata_survives_private_archive_roundtrip(client,tmp_path,monkeypatch):
    monkeypatch.setattr(archive,"store",archive.ArchiveStore(tmp_path))
    identifier=str(uuid.uuid4());url="/api/archive/conversations/"+identifier
    messages=[{"id":"u","created":1,"role":"user","content":"请分析当前辨识结果的误差，并给出有依据的修改计划。",
               "response":{"analysis_request":{"method":"pann","pwm_source":"measured","result_id":"result-a"}}},
              {"id":"a","created":2,"role":"assistant","content":replies()[0]["content"],
               "response":{"analysis":{"request_id":"u","method":"pann","pwm_source":"measured","result_id":"result-a"}}}]
    response=client.post(url,headers={"X-Archive-Key":"a"*64},json={"id":identifier,"updated":2,"messages":messages})
    assert response.status_code==200,response.text
    saved=client.get(url,headers={"X-Archive-Key":"a"*64}).json()["record"]["messages"]
    assert saved[1]["response"]["analysis"]["request_id"]=="u"
    assert saved[1]["content"]==messages[1]["content"]
