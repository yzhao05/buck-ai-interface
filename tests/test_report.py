import copy
from io import BytesIO
import uuid

from fastapi.testclient import TestClient
from pypdf import PdfReader
import pytest

from backend import main
from backend.report import ReportRequest, build_pdf


@pytest.mark.parametrize("language", ["zh", "en"])
def test_user_ground_truth_errors_are_recomputed(snapshot, language):
    snapshot["language"]=language
    snapshot["result"]["parameters"].update(L_H=59.9779e-6,C_F=500.03e-6)
    snapshot["result"]["parameter_comparison"]={"ground_truth":{"L_H":60e-6,"C_F":500e-6},"relative_error_pct":{"L":999,"C":999}}
    report=ReportRequest.model_validate(snapshot)
    assert report.result.parameter_errors()["L"]==pytest.approx(.0368333333333)
    assert report.result.parameter_errors()["C"]==pytest.approx(.006)
    text="\n".join(p.extract_text() for p in PdfReader(BytesIO(build_pdf(report))).pages)
    assert "0.0368333%" in text and "0.006%" in text and "999%" not in text
    assert ("已对照用户提供的真值" if language=="zh" else "Compared with user-provided ground truth") in text
    assert ("未验证：未提供真值" if language=="zh" else "Unverified: no ground truth provided") not in text


@pytest.mark.parametrize("truth", [0,-1,float('inf'),1e-320])
def test_invalid_ground_truth_is_rejected(snapshot, truth):
    from pydantic import ValidationError
    snapshot["result"]["parameter_comparison"]={"ground_truth":{"L_H":truth,"C_F":500e-6}}
    with pytest.raises(ValidationError):
        ReportRequest.model_validate(snapshot)


@pytest.mark.parametrize('language,expected', [('zh','不适用'),('en','Not applicable')])
def test_no_smoothing_report_has_no_active_filter_window(snapshot, language, expected):
    snapshot['language']=language
    snapshot['result']['options'].update(pwm_denoise='none',pwm_filter_cycles=1)
    text=' '.join(p.extract_text() for p in PdfReader(BytesIO(build_pdf(ReportRequest.model_validate(snapshot)))).pages)
    assert expected in text


@pytest.fixture
def snapshot():
    return {"language": "zh", "exported_at": "2026-10-23T08:00:00Z", "result": {
        "filename": "Case10A_noise03_seed12.csv", "file_sha256": "a" * 64,
        "method": "pann", "quality": "需复核", "R_estimated": False,
        "parameters": {"L_H": 60e-6, "C_F": 500e-6, "R_ohm": 6},
        "metrics": {"current_rmse_A": .008649, "voltage_rmse_V": .0008368,
                    "current_nrmse_pct": .02, "voltage_nrmse_pct": .01},
        "options": {"vin": 28, "frequency_hz": 100000, "epochs": 100,
                    "pwm_source": "controller", "pann_window_cycles": 0, "pann_initial_state": "joint"},
        "review_reasons": [{"code": "pann_best_at_end", "best_epoch": 100, "completed_epochs": 100}],
        "train_start_s": 0, "train_end_s": .00056, "validation_end_s": .0008, "elapsed_s": 107.837,
        "chart": {"time_s": [0, .0004, .0008], "current_A": [0, 1, 2], "voltage_V": [0, 12, 15],
                  "predicted_current_A": [0, 1.01, 2.01], "predicted_voltage_V": [0, 12.01, 15.01]}}}


@pytest.mark.parametrize("language", ["zh", "en"])
def test_readable_report_values_language_and_provenance(snapshot, language):
    snapshot["language"] = language
    original = copy.deepcopy(snapshot)
    reader = PdfReader(BytesIO(build_pdf(ReportRequest.model_validate(snapshot))))
    assert 3 <= len(reader.pages) <= 5
    embedded = [f.get_object().get("/FontDescriptor") for f in reader.pages[0]["/Resources"]["/Font"].values()]
    assert any(f and "/FontFile2" in f.get_object() for f in embedded)
    text = "\n".join(page.extract_text() for page in reader.pages)
    for value in ("60", "500", "6", "µH", "µF", "Ω", "8.649", "0.8368", "100 / 100", "a" * 64, snapshot["result"]["filename"]):
        assert value in text
    if language == "zh":
        assert "最佳结果出现在最后一次训练评估" in text
        assert "固定已知值" in text
    else:
        assert "best result was at the final training evaluation" in text
        assert "Fixed known value, not fitted" in text
        assert "not an independent operating-condition test" in text
        assert not any("\u4e00" <= c <= "\u9fff" for c in text)
    assert snapshot == original


@pytest.mark.parametrize("language", ["zh", "en"])
def test_stability_assessment_in_pdf(snapshot, language):
    from backend.convergence import assess_training
    h = [{"epoch":i,"loss":.01,"L_H":60e-6,"C_F":500e-6,"R_ohm":6} for i in range(61)]
    c=assess_training(h,[60e-6,500e-6,6],.01,"LC")
    snapshot["language"]=language
    snapshot["result"]["assessment"]={"training":c}
    snapshot["result"]["quality"]="模型内拟合一致"
    snapshot["result"]["review_reasons"]=[]
    text="\n".join(p.extract_text() for p in PdfReader(BytesIO(build_pdf(ReportRequest.model_validate(snapshot)))).pages)
    assert ("训练已稳定" if language=="zh" else "Training is stable") in text
    assert ("未验证" if language=="zh" else "Unverified") in text
    assert "0.1%" in text and "21" in text
    assert "two-window-stability-v2" in text
    snapshot["result"]["metrics"]["current_nrmse_pct"]=8
    text="\n".join(p.extract_text() for p in PdfReader(BytesIO(build_pdf(ReportRequest.model_validate(snapshot)))).pages)
    assert ("训练已稳定，但拟合未达标" if language=="zh" else "Training is stable, but the waveform fit has not passed") in text


def test_all_reasons_missing_chart_and_escaped_filename(snapshot):
    snapshot["notes"] = ["额外计算说明：PWM 采样边沿需核对。"]
    snapshot["result"]["filename"] = '<img src="https://invalid.example/test"> & experiment.csv'
    snapshot["result"]["chart"] = None
    snapshot["result"]["review_reasons"] = [
        {"code": "current_nrmse", "value": 6.2, "threshold": 5},
        {"code": "voltage_nrmse", "value": 7.1, "threshold": 5},
        {"code": "condition_number", "value": 123, "threshold": 100},
        {"code": "parameter_at_bound"},
        {"code": "pann_time_budget", "completed_epochs": 72, "requested_epochs": 100},
        {"code": "pann_best_at_end", "completed_epochs": 72, "best_epoch": 72},
    ]
    text = "\n".join(page.extract_text() for page in PdfReader(BytesIO(build_pdf(ReportRequest.model_validate(snapshot)))).pages)
    for expected in ("6.2% > 5%", "7.1% > 5%", "123 > 100", "触及上下限", "72 / 100", "72 / 72", "未保存波形", "<img", "PWM 采样边沿需核对"):
        assert expected in text


def test_pdf_endpoint_auth_validation_and_saved_result(snapshot, monkeypatch):
    monkeypatch.setattr(main, "TOKEN", "test-access-token-long-enough")
    client = TestClient(main.app)
    assert client.post("/api/reports/pdf", json=snapshot).status_code == 401
    client.headers.update({"Authorization": "Bearer test-access-token-long-enough", "X-Session-ID": str(uuid.uuid4())})
    # No live job/dataset is required, so archived snapshots remain exportable.
    response = client.post("/api/reports/pdf", json=snapshot)
    assert response.status_code == 200
    assert response.content.startswith(b"%PDF-")
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "no-store"
    invalid = copy.deepcopy(snapshot)
    invalid["result"]["chart"]["time_s"] = [0, 0, .0008]
    assert client.post("/api/reports/pdf", json=invalid).status_code == 422
    invalid["result"]["chart"]["time_s"] = [0, .0008]
    assert client.post("/api/reports/pdf", json=invalid).status_code == 422
    invalid["result"]["chart"]["time_s"] = list(range(10001))
    assert client.post("/api/reports/pdf", json=invalid).status_code == 422
    assert client.post("/api/reports/pdf", json=snapshot, headers={"X-Session-ID": "invalid"}).status_code == 400
