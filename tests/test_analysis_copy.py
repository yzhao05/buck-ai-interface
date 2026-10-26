import uuid
from collections import OrderedDict
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from backend import main


@pytest.fixture
def experiment(monkeypatch):
    monkeypatch.setattr(main, "TOKEN", "")
    monkeypatch.setattr(main, "datasets", OrderedDict())
    monkeypatch.setattr(main, "jobs", OrderedDict())
    calls = []
    monkeypatch.setattr(main, "pool", SimpleNamespace(submit=calls.append))
    client = TestClient(main.app, headers={"X-Session-ID": str(uuid.uuid4())})
    dataset = client.post("/api/demo").json()
    body = {"dataset_id": dataset["dataset_id"], "mapping": dataset["suggested_mapping"], "options": {"method": "weighted_integral"}}
    job_id = client.post("/api/identify", json=body).json()["job_id"]
    main.jobs[job_id].update(status="complete", result={"parameters": {"L_H": 60e-6}, "options": {"method": "weighted_integral"}})
    return client, dataset, job_id, calls


def test_copy_keeps_source_and_isolates_owners_without_training(experiment):
    client, dataset, job_id, calls = experiment
    reply = client.post(f"/api/jobs/{job_id}/analysis-copy")
    assert reply.status_code == 200
    copy = reply.json()
    assert copy["dataset"]["sha256"] == dataset["sha256"]
    assert copy["dataset"]["dataset_id"] != dataset["dataset_id"]
    assert copy["job_id"] != job_id
    target = {"X-Session-ID": copy["session_id"]}
    assert client.get(f"/api/jobs/{copy['job_id']}", headers=target).status_code == 200
    assert client.get(f"/api/jobs/{copy['job_id']}").status_code == 404
    assert client.get(f"/api/jobs/{job_id}", headers=target).status_code == 404
    assert main.jobs[copy["job_id"]]["config"]["dataset_id"] == copy["dataset"]["dataset_id"]
    assert len(calls) == 1  # Only the original submission; copying never trains.
    main.jobs[copy["job_id"]]["result"]["parameters"]["L_H"] = 1
    assert main.jobs[job_id]["result"]["parameters"]["L_H"] == 60e-6
    client.delete(f"/api/datasets/{copy['dataset']['dataset_id']}", headers=target)
    assert client.get(f"/api/jobs/{job_id}").status_code == 200
    assert client.get(f"/api/datasets/{dataset['dataset_id']}").status_code == 200


def test_copy_rejects_foreign_pending_and_missing_data(experiment):
    client, dataset, job_id, _ = experiment
    assert client.post(f"/api/jobs/{job_id}/analysis-copy", headers={"X-Session-ID": str(uuid.uuid4())}).status_code == 404
    main.jobs[job_id]["status"] = "running"
    assert client.post(f"/api/jobs/{job_id}/analysis-copy").status_code == 409
    main.jobs[job_id]["status"] = "complete"
    main.datasets.pop(dataset["dataset_id"])
    assert client.post(f"/api/jobs/{job_id}/analysis-copy").status_code == 404
    assert len(main.jobs) == 1
