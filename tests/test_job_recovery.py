import uuid
from collections import OrderedDict
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend import main


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setattr(main, "TOKEN", "")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setattr(main, "jobs", OrderedDict())
    monkeypatch.setattr(main, "datasets", OrderedDict())
    submissions = []
    monkeypatch.setattr(main, "pool", SimpleNamespace(submit=submissions.append))
    client = TestClient(main.app, headers={"X-Session-ID": str(uuid.uuid4())})
    demo = client.post("/api/demo").json()
    body = {"dataset_id": demo["dataset_id"], "mapping": demo["suggested_mapping"],
            "options": {"method": "weighted_integral", "vin": 28}}
    return client, body, submissions


def test_duplicate_submission_resumes_and_active_lookup_is_isolated(setup):
    client, body, submissions = setup
    first = client.post("/api/identify", json=body)
    second = client.post("/api/identify", json=body)
    assert first.status_code == second.status_code == 202
    ident = first.json()["job_id"]
    assert second.json() == {"job_id": ident, "status": "queued", "resumed": True}
    assert len(submissions) == 1
    active = client.get("/api/jobs/active")
    assert active.json()["job"]["job_id"] == ident
    assert active.headers["cache-control"] == "no-store"
    other = {"X-Session-ID": str(uuid.uuid4())}
    assert client.get("/api/jobs/active", headers=other).json() == {"job": None}
    assert client.get("/api/jobs/"+ident, headers=other).status_code == 404
    main.jobs[ident]["status"] = "running"
    assert client.post("/api/identify", json=body).json()["status"] == "running"
    main.jobs[ident]["status"] = "complete"
    assert client.get("/api/jobs/active").json() == {"job": None}
    assert client.get("/api/jobs/"+ident).headers["cache-control"] == "no-store"


def test_changed_config_keeps_old_job_and_chat_can_recover_it(setup):
    client, body, submissions = setup
    ident = client.post("/api/identify", json=body).json()["job_id"]
    changed = {**body, "options": {"method": "weighted_integral", "vin": 30}}
    assert client.post("/api/identify", json=changed).status_code == 409
    response = client.post("/api/chat", json={**changed, "message": "开始参数辨识", "identification_enabled": True})
    assert response.status_code == 200
    assert response.json()["job_id"] == ident
    assert "原任务仍使用提交时的配置" in response.json()["answer"]
    assert len(submissions) == 1
    assert main.jobs[ident]["config"]["options"]["vin"] == 28


def test_executor_rejection_does_not_leave_queued_job(setup, monkeypatch):
    client, body, _ = setup
    def reject(callback):
        raise RuntimeError("executor shut down")
    monkeypatch.setattr(main, "pool", SimpleNamespace(submit=reject))
    response = client.post("/api/identify", json=body)
    assert response.status_code == 503
    assert client.get("/api/jobs/active").json() == {"job": None}
    assert all(job["status"] == "failed" for job in main.jobs.values())
