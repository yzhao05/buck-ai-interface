import time
import uuid

import pytest
from fastapi.testclient import TestClient

from backend import main


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(main, "TOKEN", "test-access-token-long-enough")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    c = TestClient(main.app)
    c.headers.update({"X-Session-ID":str(uuid.uuid4()), "Authorization":"Bearer test-access-token-long-enough"})
    return c


def test_upload_fit_chat_and_isolation(client):
    demo=client.post("/api/demo"); assert demo.status_code==200
    data=demo.json()
    body={"dataset_id":data["dataset_id"],"mapping":data["suggested_mapping"],"options":{"end_s":.0008}}
    assert client.post("/api/analyze",json={k:body[k] for k in ("dataset_id","mapping")}).status_code==200
    blocked=client.post("/api/chat",json={**body,"message":"开始参数辨识"})
    assert blocked.status_code==200
    assert blocked.json()["job_id"] is None
    assert "进入参数辨识" in blocked.json()["answer"]
    job=client.post("/api/identify",json=body);assert job.status_code==202
    job_id=job.json()["job_id"]
    for _ in range(100):
        status=client.get("/api/jobs/"+job_id).json()
        if status["status"] in ("complete","failed"):break
        time.sleep(.05)
    assert status["status"]=="complete", status
    response=client.post("/api/chat",json={**body,"result_id":job_id,"message":"分析结果误差并给出修改计划"})
    assert response.status_code==200
    assert "RMSE" in response.json()["answer"]
    assert response.json()["mode"]=="local"
    changed=client.post("/api/chat",json={**body,"options":{"end_s":.0007},"result_id":job_id,"message":"分析结果误差"})
    assert "当前估计" not in changed.json()["answer"]  # stale config cannot reuse fit
    other={"X-Session-ID":str(uuid.uuid4())}
    assert client.get("/api/jobs/"+job_id,headers=other).status_code==404
    assert client.post("/api/analyze",headers=other,json={k:body[k] for k in ("dataset_id","mapping")}).status_code==404
    assert client.delete("/api/datasets/"+data["dataset_id"]).status_code==200
    assert client.get("/api/jobs/"+job_id).status_code==404


def test_auth_cors_invalid_upload_and_static(client):
    origin="http://localhost:5500"
    response=client.post("/api/demo",headers={"Authorization":"Bearer wrong", "Origin":origin})
    assert response.status_code==401
    assert response.headers["access-control-allow-origin"]==origin
    preflight=client.options("/api/demo",headers={"Origin":origin,"Access-Control-Request-Method":"POST","Access-Control-Request-Headers":"authorization,x-session-id"})
    assert preflight.status_code==200
    assert client.post("/api/datasets",files={"file":("bad.csv",b"not csv")}).status_code==422
    assert client.get("/").status_code==200
    assert client.get("/.env").status_code==404
    assert client.get("/api/health").json()["requires_token"] is True


def test_no_fake_lifetime(client):
    response=client.post("/api/chat",json={"message":"这个电容还有多少年寿命？"})
    data=response.json()
    assert data["mode"]=="local"
    assert "不能" in data["answer"] and "器件型号" in data["answer"]
    assert any(s["id"]=="capacitor-life" for s in data["sources"])
