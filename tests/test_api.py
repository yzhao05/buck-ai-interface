import time
import uuid
from collections import OrderedDict
import hashlib
from types import SimpleNamespace

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
    body={"dataset_id":data["dataset_id"],"mapping":data["suggested_mapping"],"options":{"end_s":.0008,"method":"weighted_integral"}}
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
    assert status["stage"] == "complete"
    response=client.post("/api/chat",json={**body,"result_id":job_id,"message":"分析结果误差并给出修改计划"})
    assert response.status_code==200
    assert "当前估计" in response.json()["answer"] and "结论" in response.json()["answer"]
    assert "估计噪声标准差" in response.json()["answer"]
    assert "5% 复核门槛" in response.json()["answer"]
    assert status['result']['measurement_diagnostics']['scope'] == 'same_record_holdout_not_new_record_prediction'
    assert response.json()["mode"]=="local"
    changed=client.post("/api/chat",json={**body,"options":{"end_s":.0007,"method":"weighted_integral"},"result_id":job_id,"message":"分析结果误差"})
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


def test_csv_archive_bytes_access_expiry_and_reupload(client, monkeypatch):
    monkeypatch.setattr(main, "datasets", OrderedDict())
    raw = ("时间;电感电流;电压\r\n" + "".join(f"{i*.001};{i*.1};12\r\n" for i in range(40))).encode("gb18030")
    upload = client.post("/api/datasets", files={"file":("original.csv",raw,"text/csv")})
    assert upload.status_code == 200
    data = upload.json()
    path = "/api/datasets/" + data["dataset_id"]
    assert client.get(path).json()["sha256"] == hashlib.sha256(raw).hexdigest()
    response = client.get(path+"/csv")
    assert response.content == raw  # Original encoding, delimiter and CRLF preserved.
    assert response.headers["cache-control"] == "no-store"
    assert main.datasets[data["dataset_id"]]["size"] >= len(raw)
    for suffix in ("", "/csv"):
        assert client.get(path+suffix, headers={"Authorization":"Bearer wrong"}).status_code == 401
        assert client.get(path+suffix, headers={"X-Session-ID":str(uuid.uuid4())}).status_code == 404
    main.datasets[data["dataset_id"]]["created"] -= main.TTL+1
    assert client.get(path).status_code == 404
    assert client.get(path+"/csv").status_code == 404
    restored = client.post("/api/datasets", files={"file":("original.csv",raw,"text/csv")}).json()
    assert restored["dataset_id"] != data["dataset_id"]
    assert restored["sha256"] == data["sha256"]
    assert client.delete("/api/datasets/"+restored["dataset_id"]).status_code == 200


def test_no_fake_lifetime(client):
    response=client.post("/api/chat",json={"message":"这个电容还有多少年寿命？"})
    data=response.json()
    assert data["mode"]=="local"
    assert "不能" in data["answer"] and "器件型号" in data["answer"]
    assert any(s["id"]=="capacitor-life" for s in data["sources"])
    assert data["route"]["task"] == "hardware" and data["route"]["mode"] == "rules"


@pytest.mark.parametrize("fails", [False, True])
def test_long_job_survives_ttl_and_result_retention_restarts(client, monkeypatch, fails):
    monkeypatch.setattr(main, "datasets", OrderedDict())
    monkeypatch.setattr(main, "jobs", OrderedDict())
    data = client.post("/api/demo").json()
    dataset_id = data["dataset_id"]
    owner = client.headers["X-Session-ID"]
    # A queued job must protect its data too.
    main.jobs["queued-test"] = {"owner": owner, "dataset_id": dataset_id, "created": time.time()-main.TTL-1, "status": "queued"}
    main.datasets[dataset_id]["created"] -= main.TTL+1
    main.cleanup()
    assert dataset_id in main.datasets and "queued-test" in main.jobs
    del main.jobs["queued-test"]
    main.datasets[dataset_id]["created"] = time.time()

    def long_fit(*args, **kwargs):
        job = next(iter(main.jobs.values()))
        job["created"] -= main.TTL+1
        main.datasets[dataset_id]["created"] -= main.TTL+1
        main.cleanup()
        assert job["status"] == "running" and dataset_id in main.datasets
        if fails:
            raise ValueError("test training failure")
        return {"completed_epochs": 200}

    monkeypatch.setattr(main, "identify", long_fit)
    monkeypatch.setattr(main, "pool", SimpleNamespace(submit=lambda task: task()))
    job_id = client.post("/api/identify", json={"dataset_id":dataset_id,"mapping":data["suggested_mapping"],
                                               "options":{"method":"weighted_integral"}}).json()["job_id"]
    response = client.get("/api/jobs/"+job_id)
    assert response.status_code == 200
    assert response.json()["status"] == ("failed" if fails else "complete")
    assert client.get("/api/datasets/"+dataset_id).status_code == 200
    assert time.time()-main.jobs[job_id]["created"] < 10
    assert time.time()-main.datasets[dataset_id]["created"] < 10
    main.jobs[job_id]["created"] -= main.TTL+1
    main.datasets[dataset_id]["created"] -= main.TTL+1
    assert client.get("/api/jobs/"+job_id).status_code == 404
    assert client.get("/api/datasets/"+dataset_id).status_code == 404


def test_formula_route_does_not_require_live_dataset(client):
    response = client.post("/api/chat", json={"message": "推导补偿器传递函数", "dataset_id": "expired",
        "mapping": {"time": "t", "current": "i", "voltage": "v"}})
    assert response.status_code == 200
    assert response.json()["route"]["task"] == "knowledge"
    assert response.json()["job_id"] is None


def test_identification_route_preserves_entry_and_data_checks(client):
    blocked = client.post("/api/chat", json={"message": "开始参数辨识"}).json()
    assert blocked["route"]["task"] == "identification" and blocked["job_id"] is None
    assert "进入参数辨识" in blocked["answer"]
    missing = client.post("/api/chat", json={"message": "开始参数辨识", "identification_enabled": True}).json()
    assert missing["job_id"] is None and "CSV" in missing["answer"]


@pytest.mark.parametrize("message", ["查看辨识结果", "参数辨识按钮在哪里？", "停止辨识", "辨识已经完成了"])
def test_non_execution_requests_never_queue_identification(client, monkeypatch, message):
    def unexpected_fit(*args, **kwargs):
        raise AssertionError("Read-only request reached start_fit")
    monkeypatch.setattr(main, "start_fit", unexpected_fit)
    data = client.post("/api/demo").json()
    response = client.post("/api/chat", json={"message": message, "identification_enabled": True,
        "dataset_id": data["dataset_id"], "mapping": data["suggested_mapping"]})
    assert response.status_code == 200
    assert response.json()["job_id"] is None
    assert response.json()["route"]["task"] != "identification"
