import copy
import time
import uuid
import pytest
from pydantic import ValidationError

from fastapi.testclient import TestClient

from backend import archive, main
from backend.schemas import SavedExperiment


@pytest.mark.parametrize("parameters", [None, [], {"L_H": 10**400, "C_F": .0005, "R_ohm": 6},
                                      {"L_H": True, "C_F": .0005, "R_ohm": 6}])
def test_bad_snapshot_parameters_are_validation_errors(parameters):
    invalid=run()
    invalid["result"]["parameters"]=parameters
    with pytest.raises(ValidationError):
        SavedExperiment.model_validate(invalid)


def run(index=0):
    method = ["pann", "weighted_integral"][index % 2]
    pwm = "measured" if index < 2 else "controller"
    return {"id": f"run-{index}", "created": 100+index, "updated": 100+index,
            "dataset": {"filename": "CASE16A_clean_grid.csv", "sha256": "a"*64},
            "result": {"parameters": {"L_H": (60+index)*1e-6, "C_F": 500e-6, "R_ohm": 6},
                       "method": method, "options": {"method": method, "pwm_source": pwm},
                       "metrics": {}, "diagnostics": {}, "warnings": []}, "ground_truth": None}


def test_archive_runs_union_truth_version_and_deletion(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "TOKEN", "")
    monkeypatch.setattr(archive, "store", archive.ArchiveStore(tmp_path))
    client = TestClient(main.app, headers={"X-Archive-Key": "a"*64})
    identifier = str(uuid.uuid4())
    url = "/api/archive/conversations/"+identifier
    record = {"id": identifier, "updated": 100, "messages": []}
    for index in range(4):
        response = client.post(url, json={**record, "experiments": [run(index)]})
        assert response.status_code == 200, response.text
    assert len(response.json()["record"]["experiments"]) == 4
    changed = run()
    changed.update(updated=200, ground_truth={"result_id": changed["id"], "L_H": 60e-6, "C_F": 500e-6})
    client.post(url, json={**record, "experiments": [changed]})
    # Old tabs and older client versions cannot erase runs or newer truth edits.
    client.post(url, json={**record, "experiments": [run()]})
    response = client.post(url, json=record)
    saved = response.json()["record"]["experiments"]
    assert len(saved) == 4 and saved[0]["ground_truth"]["L_H"] == 60e-6
    assert client.get(url, headers={"X-Archive-Key": "b"*64}).status_code == 404
    client.delete(url)
    assert client.post(url, json={**record, "experiments": saved}).json()["deleted"]


def test_cached_runs_recovered_only_for_owner_without_dataset(monkeypatch):
    monkeypatch.setattr(main, "TOKEN", "")
    owner = str(uuid.uuid4())
    entries = {}
    for index in range(4):
        result = copy.deepcopy(run(index)["result"])
        result.update(filename="CASE16A_clean_grid.csv", file_sha256="a"*64, chart={"big": [1,2,3]})
        result["diagnostics"] = {"pann": {"epoch_history": [1,2], "history": [1], "convergence": {"status": "stable"}}}
        entries[str(index)] = {"owner": owner, "dataset_id": "expired", "created": time.time(), "status": "complete", "result": result}
    entries["private"] = {**entries["0"], "owner": str(uuid.uuid4())}
    entries["pending"] = {**entries["0"], "status": "running"}
    monkeypatch.setattr(main, "jobs", entries)
    client = TestClient(main.app, headers={"X-Session-ID": owner})
    response = client.get("/api/experiments")
    assert response.status_code == 200, response.text
    runs = response.json()["experiments"]
    assert len(runs) == 4
    assert runs[0]["dataset"]["sha256"] == "a"*64
    assert "chart" not in runs[0]["result"]
    assert "epoch_history" not in runs[0]["result"]["diagnostics"]["pann"]
    assert "epoch_history" in entries["0"]["result"]["diagnostics"]["pann"]
    assert client.get("/api/experiments", headers={"X-Session-ID": str(uuid.uuid4())}).json()["experiments"] == []
