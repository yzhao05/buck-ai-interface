import json
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import chat, main
from backend.conversation_retrieval import ConversationIndex, history_requested
from backend.schemas import ConversationMessage, ChatRequest


def corpus():
    records = [
        {"id": "deploy-question", "role": "user", "content": "GitHub 部署分支应该选哪个？"},
        {"id": "deploy-answer", "role": "assistant", "content": "选择 main，提交后检查自动构建。", "request_message_id": "deploy-question"},
        {"id": "long", "role": "assistant", "content": "背景资料。"*2000+"\n散热试验结果：热阻 2.73 K/W，温升 18.5 °C。"},
    ]
    for i in range(20):
        records.append({"id": f"unrelated-{i}", "role": "user" if i % 2 == 0 else "assistant", "content": f"无关的新话题 {i}"})
    return records


def index(records=None):
    return ConversationIndex([ConversationMessage.model_validate(m) for m in (records if records is not None else corpus())])


def test_old_user_question_retrieves_answer_without_repeated_keywords():
    found = index().initial("总结之前关于 GitHub 部署的讨论")
    ids = {m["message_id"] for m in found["excerpts"]}
    assert ids == {"deploy-question", "deploy-answer"}
    assert found["available_count"] == 23
    assert next(m for m in found["excerpts"] if m["message_id"] == "deploy-answer")["request_message_id"] == "deploy-question"


def test_long_tail_and_exact_offsets_with_continuation():
    store = index()
    found = store.initial("查找之前关于散热试验的回答")
    tail = found["excerpts"][0]
    assert tail["message_id"] == "long" and tail["start"] > 8000 and tail["partial"]
    assert "2.73 K/W" in tail["content"]
    assert tail["content"] == corpus()[2]["content"][tail["start"]:tail["end"]]
    first = store.read("long", limit=8000)
    rest = store.read("long", start=first["next_start"])
    assert first["content"]+rest["content"] == corpus()[2]["content"]
    assert not store.metadata()["sources"][0]["partial"]
    paired = index([{"id": "q", "role": "user", "content": "散热试验的结果是什么？"},
                    {**corpus()[2], "request_message_id": "q"}])
    hit = paired.initial("查找之前关于散热试验的回答")["excerpts"][0]
    assert hit["start"] > 8000 and "2.73 K/W" in hit["content"]


def test_arbitrary_count_order_paging_and_missing_material():
    data = [{"id": str(i), "role": "assistant", "content": f"噪声实验，第 {i} 次。"} for i in range(12)]
    store = index(data)
    result = store.initial("找出最早三条关于噪声的回答")
    assert [r["id"] for r in result["excerpts"]] == ["0", "1", "2"]
    latest = store.initial("比较最近六次分析")
    assert [r["id"] for r in latest["excerpts"]] == [str(i) for i in range(11, 5, -1)]
    page = store.search("噪声", limit=3, offset=3, order="oldest")
    assert [r["id"] for r in page["excerpts"]] == ["3", "4", "5"]
    assert page["next_offset"] == 6
    assert store.search("unseen-topic")["matched_count"] == 0
    assert index([]).read("0").get("error")
    english = store.initial("Compare the last six replies")
    assert [r["id"] for r in english["excerpts"]] == [str(i) for i in range(11, 5, -1)]
    assert len(store.initial("Find the first 3 messages")["excerpts"]) == 3


def test_analysis_markers_select_original_replies_without_fixed_four():
    data = corpus() + [{"id": f"analysis-{i}", "role": "assistant", "content": f"L={60+i} μH",
                       "analysis": {"kind": "identification_analysis", "method": "pann"}} for i in range(6)]
    found = index(data).initial("对比前面四次分析")
    assert [r["id"] for r in found["excerpts"]] == [f"analysis-{i}" for i in range(4)]
    assert len(index(data).initial("比较最近六次分析")["excerpts"]) == 6


@pytest.mark.parametrize("query", ["总结本会话全部内容", "总结当前会话的所有讨论", "总结整个会话", "总结这次会话", "Summarize this entire conversation"])
def test_whole_conversation_queries_do_not_search_for_scaffolding_words(query):
    assert history_requested(query)
    found = index().initial(query)
    assert found["matched_count"] == len(corpus())
    assert found["next_offset"] == 8


def test_latest_twelve_and_general_analyses_with_experiment_markers():
    data = [{"id": str(i), "role": "assistant", "content": f"deployment variant {i}"} for i in range(15)]
    data.append({"id": "experiment", "role": "assistant", "content": "L=60 μH",
                 "analysis": {"kind": "identification_analysis", "method": "pann"}})
    found = index(data).initial("Compare the latest six deployment analyses")
    assert [r["id"] for r in found["excerpts"]] == [str(i) for i in range(14, 8, -1)]
    assert index(data).initial("Compare previous deployment result analyses")["matched_count"] == 15
    assert len(index(data).initial("读取最早十二条回答")["excerpts"]) == 12


def test_budget_and_validation_are_explicit():
    store = index()
    store.remaining = 10
    found = store.search("", limit=20)
    assert found["budget_exhausted"] and found["excerpts"][0]["partial"]
    assert store.read("long").get("error")
    assert store.invoke("read_conversation_message", {"message_id": []}).get("error")
    assert store.invoke("search_conversation", {"limit": 500}).get("error")
    with pytest.raises(ValueError):
        ChatRequest(message="test", conversation_messages=[corpus()[0], corpus()[0]])


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "TOKEN", "")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    return TestClient(main.app, headers={"X-Session-ID": str(uuid.uuid4())})


def test_readonly_no_snapshots_no_csv_no_classifier(client, monkeypatch):
    async def forbidden(*args):
        raise AssertionError("Historical retrieval must not use live experiment routing")
    monkeypatch.setattr(chat, "classify", forbidden)
    before = set(main.jobs)
    response = client.post("/api/chat", json={"message": "总结之前关于 GitHub 部署的讨论",
        "conversation_messages": corpus(), "conversation_retrieval_requested": True,
        "dataset_id": "expired", "result_id": "expired", "identification_enabled": True})
    assert response.status_code == 200, response.text
    data = response.json()
    assert "尚未生成 AI 总结" in data["answer"] and "main" in data["answer"]
    assert data["conversation_retrieval"]["read_count"] == 2
    assert data["job_id"] is None and before == set(main.jobs)


def test_explicit_run_then_history_comparison_keeps_execution(client, monkeypatch):
    calls = []
    def start(body, owner):
        calls.append(body.model_dump())
        return {"job_id": "mixed-history-job", "status": "queued"}
    monkeypatch.setattr(main, "start_fit", start)
    monkeypatch.setattr(main, "get_dataset", lambda *args: object())
    monkeypatch.setattr(main, "analyze", lambda *args: {})
    response = client.post("/api/chat", json={"message": "先辨识参数，再比较之前的结果", "conversation_messages": corpus(),
        "conversation_retrieval_requested": True, "identification_enabled": True, "dataset_id": "current-csv",
        "mapping": {"time": "t", "current": "i", "voltage": "v"}, "options": {"method": "weighted_integral", "vin": 30}})
    assert response.status_code == 200, response.text
    assert response.json()["job_id"] == "mixed-history-job"
    assert len(calls) == 1 and calls[0]["options"]["vin"] == 30
    assert "已启动" in response.json()["answer"]


def test_provider_receives_selected_material_can_refine_and_read(client, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    calls = []
    class Provider:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            body = kwargs["json"]; calls.append(body)
            assert "无关的新话题" not in json.dumps(body, ensure_ascii=False)
            assert "start_identification" not in [t["function"]["name"] for t in body["tools"]]
            if len(calls) == 1:
                context = json.loads(body["messages"][1]["content"].split("：", 1)[1])
                assert context["conversation_search"]["matched_count"] == 2
                recent = body["messages"][2:-1]
                assert len(recent) == 4 and all(len(m["content"]) < 2100 for m in recent)
                assert all("片段" in m["content"] for m in recent)
                function = {"name": "search_conversation", "arguments": json.dumps({"query": "散热"})}
            elif len(calls) == 2:
                result = json.loads(body["messages"][-1]["content"])
                assert "2.73 K/W" in result["excerpts"][0]["content"]
                function = {"name": "read_conversation_message", "arguments": json.dumps({"message_id": "long", "start": 8000})}
            else:
                result = json.loads(body["messages"][-1]["content"])
                assert result["start"] == 8000 and "18.5 °C" in result["content"]
                return httpx.Response(200, request=httpx.Request("POST", url), json={"choices": [{"message": {
                    "role": "assistant", "content": "部署选择 main [消息 #2]。散热数据来自 [消息 #3]。"}}]})
            return httpx.Response(200, request=httpx.Request("POST", url), json={"choices": [{"message": {
                "role": "assistant", "content": None, "tool_calls": [{"id": f"call-{len(calls)}", "type": "function", "function": function}]}}]})
    monkeypatch.setattr(chat.httpx, "AsyncClient", Provider)
    response = client.post("/api/chat", json={"message": "总结之前关于 GitHub 部署的讨论", "conversation_messages": corpus(),
        "history": [{"role": "assistant", "content": "recent-only "*600} for _ in range(10)]})
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["mode"] == "deepseek" and len(calls) == 3
    assert data["conversation_retrieval"]["read_count"] == 3
    assert len(data["conversation_retrieval"]["searches"]) == 2


def test_failed_provider_preserves_local_material(client, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    class Provider:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, *args, **kwargs): raise httpx.ConnectError("offline")
    monkeypatch.setattr(chat.httpx, "AsyncClient", Provider)
    response = client.post("/api/chat", json={"message": "总结之前的 GitHub 讨论", "conversation_messages": corpus()})
    data = response.json()
    assert data["mode"] == "local_fallback" and "main" in data["answer"]
    assert data["conversation_retrieval"]["read_count"] == 2
