import io
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from backend.data import analyze, extract, read_csv, suggest_mapping, weighted_stats
from backend.identification import identify
from backend.schemas import FitOptions, Mapping

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def case():
    data = read_csv((ROOT / "examples/plecs_buck_case16a.csv").read_bytes(), "unlabelled.csv")
    return data, Mapping(**suggest_mapping(data))


@pytest.mark.parametrize("method", ["weighted_integral", "integral"])
def test_real_plecs_joint_parameters_and_holdout(case, method):
    data, mapping = case
    result = identify(data, mapping, FitOptions(end_s=.0008, method=method))
    p = result["parameters"]
    # Truth is used here only for independent assessment, never passed to fit.
    assert p["L_H"] == pytest.approx(60e-6, rel=.02)
    assert p["C_F"] == pytest.approx(500e-6, rel=.02)
    assert p["R_ohm"] == pytest.approx(6, rel=.02)
    assert result["metrics"]["current_rmse_A"] < .02
    assert result["metrics"]["voltage_rmse_V"] < .02
    assert result["parameter_error"] is None
    assert result["method"] == method
    assert result["train_end_s"] < result["validation_end_s"]


def test_holdout_and_filename_cannot_leak_into_fit(case):
    data, mapping = case
    options = FitOptions(end_s=.0008, method="weighted_integral")
    original = identify(data, mapping, options)
    # Poison only the held-out measurements. Estimates must be identical.
    data.name = "L999uH_C999uF_R999.csv"
    mask = data.frame[mapping.time] > original["train_end_s"]
    data.frame.loc[mask, mapping.current] += 2
    data.frame.loc[mask, mapping.voltage] += 3
    changed = identify(data, mapping, options)
    assert changed["parameters"] == original["parameters"]
    assert changed["metrics"]["voltage_rmse_V"] > 2


def test_load_step_is_not_silently_accepted(case):
    data, mapping = case
    result = identify(data, mapping, FitOptions(method="weighted_integral"))
    assert result["quality"] == "需复核"
    assert result["review_reasons"]
    assert any(r["code"] in {"current_nrmse", "voltage_nrmse"} for r in result["review_reasons"])


def test_fixed_resistance_stays_fixed(case):
    data, mapping = case
    result = identify(data, mapping, FitOptions(mode="LC", resistance_ohm=6, end_s=.0008, method="weighted_integral"))
    assert result["parameters"]["R_ohm"] == 6
    assert result["R_estimated"] is False


def test_data_quality_and_units(case):
    data, mapping = case
    original, _ = extract(data, mapping)
    data.frame[mapping.time] *= 1000
    mapping.time_unit = "ms"
    converted, _ = extract(data, mapping)
    np.testing.assert_allclose(original, converted)
    data.frame[mapping.pwm] = data.frame[mapping.pwm].astype(float)
    data.frame.loc[10, mapping.pwm] = .5
    with pytest.raises(ValueError, match="PWM"):
        extract(data, mapping)


def test_invalid_time_nan_and_missing_pwm(case):
    data, mapping = case
    no_pwm = mapping.model_copy(update={"pwm":None})
    assert analyze(data, no_pwm)["rows"] > 100
    with pytest.raises(ValueError, match="PWM"):
        identify(data, no_pwm, FitOptions())
    data.frame.loc[10, mapping.time] = -1
    with pytest.raises(ValueError, match="时间"):
        extract(data, mapping)
    data.frame.loc[10, mapping.time] = np.nan
    with pytest.raises(ValueError, match="非数值"):
        extract(data, mapping)


def test_time_weighting_not_sample_weighting():
    stats = weighted_stats(np.array([0.,.01,1.]), np.array([0.,0.,2.]))
    assert stats["mean"] == pytest.approx(.99)
    assert stats["mean"] != pytest.approx(2/3)


def test_boundary_invalid_inputs(case):
    data, mapping = case
    for options in [FitOptions(end_s=4), FitOptions(start_s=.001, end_s=.0001)]:
        with pytest.raises(ValueError): identify(data, mapping, options)
    for values in ({"vin":float("nan")}, {"frequency_hz":float("inf")}, {"mode":"ESR"}):
        with pytest.raises(ValidationError): FitOptions(**values)


def test_steady_no_excitation_rejected():
    a = np.column_stack([np.linspace(0,.002,1001), np.ones(1001)*2, np.ones(1001)*12, np.tile([0,1],501)[:1001]])
    buf = io.StringIO(); np.savetxt(buf,a,delimiter=",",header="time,il,vo,pwm",comments="")
    data=read_csv(buf.getvalue().encode(),"steady.csv")
    with pytest.raises(ValueError): identify(data,Mapping(**suggest_mapping(data)),FitOptions())
