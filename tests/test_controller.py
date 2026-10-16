from pathlib import Path

import numpy as np
import pytest

from backend.controller import PROFILE, reconstruct_training, quantities, profile_for, closed_loop_rollout, validate_profile
from backend.data import extract, read_csv, suggest_mapping
from backend.identification import identify
from backend.pann_adapter import training_windows
from backend.schemas import FitOptions, Mapping, ControllerParameters
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]


def sample():
    original = read_csv((ROOT / "examples/plecs_buck_case16a.csv").read_bytes(), "source.csv")
    raw = original.frame.iloc[:, :3].to_csv(index=False).encode()
    data = read_csv(raw, "unknown-three-columns.csv")
    return data, Mapping(**suggest_mapping(data))


def config(**updates):
    return FitOptions(**{**dict(mode="LC", resistance_ohm=6, method="weighted_integral",
        pwm_source="controller", controller_confirmed=True, start_s=0, end_s=.0008), **updates})


def test_three_column_fixed_load_estimate_and_autonomous_validation():
    data, mapping = sample()
    assert mapping.pwm is None
    stages = []
    result = identify(data, mapping, config(), progress=stages.append)
    assert stages == ["checking", "pwm_reconstruction", "initialization", "validation"]
    np.testing.assert_allclose([result["parameters"]["L_H"], result["parameters"]["C_F"]],
                               [60e-6, 500e-6], rtol=.02)
    assert result["parameters"]["R_ohm"] == 6 and not result["R_estimated"]
    assert result["metrics"]["current_nrmse_pct"] < 2
    assert result["diagnostics"]["controller"] == PROFILE
    assert result["diagnostics"]["sampling_branch_included"]
    reconstruction = result["diagnostics"]["pwm_reconstruction"]
    assert reconstruction["status"] == "complete"
    assert reconstruction["start_s"] == 0
    assert reconstruction["end_s"] <= result["train_end_s"] + 1e-12
    assert reconstruction["samples"] > 1000
    assert reconstruction["rising_edges"] > 10 and reconstruction["falling_edges"] > 10
    assert not reconstruction["uses_heldout_measurements"]
    assert "自主闭环" in result["validation_kind"]
    assert "pwm" not in result["chart"]  # Do not present generated gates as measured data.


def test_holdout_voltage_cannot_change_reconstruction_or_fit():
    data, mapping = sample()
    original = identify(data, mapping, config())
    # Perturb only points strictly after the included training endpoint.
    heldout = data.frame.iloc[:, 0] > original["train_end_s"]
    data.frame.loc[heldout, data.frame.columns[1:3]] *= 1.2
    changed = identify(data, mapping, config())
    assert changed["parameters"] == original["parameters"]
    assert changed["diagnostics"]["pwm_reconstruction"] == original["diagnostics"]["pwm_reconstruction"]
    assert changed["diagnostics"]["controller_reconstruction_end_s"] <= original["train_end_s"]+1e-12
    assert changed["metrics"]["voltage_rmse_V"] > original["metrics"]["voltage_rmse_V"]


@pytest.mark.parametrize("changes,match", [
    ({"mode":"LCR"}, "固定已知负载"),
    ({"controller_confirmed":False}, "确认"),
    ({"frequency_hz":20000}, "100 kHz"),
    ({"start_s":.0001}, "t=0"),
    ({"end_s":.001}, "1 ms"),
    ({"method":"integral"}, "PANN"),
])
def test_unsupported_profiles_are_rejected(changes, match):
    data, mapping = sample()
    with pytest.raises(ValueError, match=match):
        identify(data, mapping, config(**changes))


def test_missing_csv_start_and_missing_measured_pwm_still_rejected():
    data, mapping = sample()
    with pytest.raises(ValueError, match="PWM"):
        identify(data, mapping, config(pwm_source="measured"))
    data.frame = data.frame.iloc[5:]
    with pytest.raises(ValueError, match="t=0"):
        identify(data, mapping, config())


def test_training_windows_include_reconstructed_sensing_current():
    data, mapping = sample()
    a, _ = extract(data, mapping)
    train = reconstruct_training(a[a[:, 0] <= .0005])
    inputs, targets, initial, starts, dt = training_windows(train, 100000)
    assert inputs.shape[-1] == 2 and inputs.shape[1] == 500
    assert dt == pytest.approx(20e-9)
    assert set(np.unique(inputs[:, :, 0])) == {0., 1.}
    assert np.max(np.abs(inputs[:, :, 1])) > 0
    assert np.isfinite(targets).all() and np.isfinite(initial).all()
    assert starts[-1]+inputs.shape[1]*dt <= train[-1, 0]+1e-12


def test_user_provided_resistance_remains_fixed():
    data, mapping = sample()
    result = identify(data, mapping, config(resistance_ohm=5.5))
    assert result["parameters"]["R_ohm"] == 5.5 and not result["R_estimated"]


@pytest.mark.parametrize("custom", [False, True])
def test_reconstruction_precedes_pann_and_supplies_binary_drive(monkeypatch, custom):
    data, mapping = sample()
    stages = []
    options = config(method="pann", controller=ControllerParameters(R1_ohm=12000,
        C2_F=1.3e-9, carrier_phase_at_t0=.2) if custom else None)
    a, _ = extract(data, mapping)

    def fake_refine(train, initial, options):
        assert stages == ["checking", "pwm_reconstruction", "initialization", "pann_training"]
        assert train.shape[1] == 5  # t, i, v, reconstructed gate, sensing current
        assert set(np.unique(train[:, 3])) == {0., 1.}
        assert train[-1, 0] <= .000561
        assert options.mode == "LC" and initial[2] == 6
        # Compare the complete reconstructed drive against this request's
        # controller, including values that differ from the original defaults.
        selected = a[a[:, 0] <= options.end_s + 1e-14]
        split = np.searchsorted(selected[:, 0], options.train_fraction*selected[-1, 0])
        expected = reconstruct_training(selected[:split+1], profile_for(options))
        np.testing.assert_allclose(train, expected)
        return initial, {"rollout_initial_state_A_V": train[0, 1:3].tolist(),
                         "stop_reason": "epoch_budget", "best_at_budget_end": False}

    monkeypatch.setattr("backend.pann_adapter.refine", fake_refine)
    result = identify(data, mapping, options, progress=stages.append)
    assert stages[-1] == "validation"
    assert result["parameters"]["R_ohm"] == 6
    assert result["diagnostics"]["controller"] == profile_for(options)


def test_custom_controller_obeys_circuit_kcl():
    p = profile_for(config(controller=ControllerParameters(R1_ohm=12000, R2_ohm=90000,
        R3_ohm=110000, R4_ohm=50000, C2_F=1.3e-9, C3_F=3e-9, opamp_gain=2e5, reference_V=4.8)))
    q = np.array([1.3, -.4]); vo = 14.2
    dq, duty, sense = quantities(q, vo, p)
    va = vo - sense*p["R1_ohm"]
    vm = va-q[0]; vb = vm+q[1]; vop = p["opamp_gain"]*(p["reference_V"]-vm)
    assert sense == pytest.approx(q[0]/p["R2_ohm"]+p["C2_F"]*dq[0])
    assert (vop-vb)/p["R3_ohm"] == pytest.approx(p["C3_F"]*dq[1], abs=1e-12)
    assert sense+p["C3_F"]*dq[1] == pytest.approx(vm/p["R4_ohm"], abs=1e-12)
    assert duty == pytest.approx(np.clip(p["pwm_gain"]*vop,p["duty_min"],p["duty_max"]))


def test_custom_phase_initial_state_and_frequency_drive_reconstruction():
    data, mapping = sample(); a, _ = extract(data,mapping); train=a[a[:,0]<=.0003]
    base=reconstruct_training(train)
    options=config(controller=ControllerParameters(carrier_phase_at_t0=.5, initial_c2_V=1., initial_c3_V=.5), frequency_hz=80000)
    p=profile_for(options); validate_profile(a,options)
    actual=reconstruct_training(train,p)
    assert p["dt_s"] == 1/(80000*500)
    assert p["initial_C2_C3_V"] == [1.,.5]
    assert actual[0,4] == pytest.approx(quantities(np.array([1.,.5]),train[0,2],p)[2])
    assert len(actual) != len(base)
    assert not np.array_equal(actual[:,3],base[:len(actual),3])
    first=closed_loop_rollout(a[:100,0],[60e-6,500e-6,6],28,a[0,1:3])
    changed=closed_loop_rollout(a[:100,0],[60e-6,500e-6,6],28,a[0,1:3],p)
    assert not np.allclose(first,changed)


@pytest.mark.parametrize("values", [{"R1_ohm":0},{"C2_F":-1},{"reference_V":float('nan')},
    {"duty_min":.9,"duty_max":.1},{"carrier_phase_at_t0":1},{"unknown_component":2}])
def test_invalid_controller_parameters_are_rejected(values):
    with pytest.raises(ValidationError):
        ControllerParameters(**values)


def test_custom_controller_runtime_and_stability_limits():
    data,mapping=sample();a,_=extract(data,mapping)
    with pytest.raises(ValueError, match="100000"):
        validate_profile(a,config(controller=ControllerParameters(),frequency_hz=2e6))
    with pytest.raises(ValueError, match="步长"):
        validate_profile(a,config(controller=ControllerParameters(R1_ohm=1,R2_ohm=1,C2_F=1e-12)))
