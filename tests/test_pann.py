"""Window geometry, state-selection and train/holdout separation regressions."""
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from backend.data import extract, read_csv, suggest_mapping
from backend.identification import identify, weak_integral_seed
from backend.pann_adapter import available, measurement_noise_std, refine, training_windows, select_window_cycles
from backend.schemas import FitOptions, Mapping

ROOT = Path(__file__).resolve().parents[1]


def sample():
    data = read_csv((ROOT / "examples/plecs_buck_case16a.csv").read_bytes(), "unknown.csv")
    mapping = Mapping(**suggest_mapping(data))
    a, _ = extract(data, mapping)
    return data, mapping, a


def test_windows_cover_late_training_without_truncation():
    t = np.linspace(0, .02, 10001)
    a = np.column_stack((t, 2 + t, 12 + t, (np.arange(len(t)) % 2)))
    u, target, x0, starts, dt = training_windows(a, 100000, 2)
    assert len(starts) == 40
    assert starts[0] == 0
    assert starts[-1] + u.shape[1] * dt == pytest.approx(t[-1])
    assert np.all(np.diff(starts) >= u.shape[1] * dt - 1e-12)
    assert target[-1, -1, 1] == pytest.approx(a[-1, 2])
    assert set(np.unique(u)) == {0., 1.}
    np.testing.assert_allclose(x0[:, 1], 12 + starts)


def test_windows_do_not_interpolate_pwm_or_targets_across_holdout():
    _, _, a = sample()
    train = a[a[:, 0] <= .0005]
    u, target, x0, starts, dt = training_windows(train, 100000)
    times = starts[:, None] + np.arange(u.shape[1]) * dt
    expected = train[np.searchsorted(train[:, 0], times, side="right") - 1, 3]
    np.testing.assert_array_equal(u[:, :, 0], expected)
    assert np.isfinite(target).all() and np.isfinite(x0).all()
    invalid = train.copy(); invalid[:, 3] *= 28
    with pytest.raises(ValueError, match="0/1"):
        training_windows(invalid, 100000)
    with pytest.raises(ValueError, match="窗口不足"):
        training_windows(train[:3], 100000, 3)


def test_noise_estimate_and_config_bounds():
    rng = np.random.default_rng(123)
    t = np.arange(20000) * 1e-7
    a = np.column_stack((t, 3 + rng.normal(0,.1,len(t)), 12 + rng.normal(0,.2,len(t)), np.ones(len(t))))
    np.testing.assert_allclose(measurement_noise_std(a), [.1,.2], rtol=.05)
    assert select_window_cycles(a, 100000, 0)[0] == 3
    assert select_window_cycles(a, 100000, 1)[0] == 1
    assert select_window_cycles(a[:801], 100000, 0)[0] == 2
    for values in ({"pann_initial_state":"clean"}, {"pann_window_cycles":-1}, {"pann_window_cycles":4}, {"epochs":301}):
        with pytest.raises(ValidationError):
            FitOptions(**values)


def test_weighted_seed_clean_fixed_and_estimated_resistance():
    _, _, a = sample()
    train = a[a[:, 0] <= .0008]
    assert select_window_cycles(train, 100000, 0)[0] == 1
    for mode in ("LC", "LCR"):
        params, diagnostic = weak_integral_seed(train, FitOptions(mode=mode, resistance_ohm=6))
        np.testing.assert_allclose(params, [60e-6, 500e-6, 6], rtol=.02)
        assert not diagnostic["parameter_at_bound"]
        if mode == "LC":
            assert params[2] == 6
    # Endpoint-zero weighting must not manufacture identifiability in DC data.
    constant = train.copy(); constant[:, 1:3] = [2., 12.]
    for mode in ("LC", "LCR"):
        with pytest.raises(ValueError, match="动态"):
            weak_integral_seed(constant, FitOptions(mode=mode))


@pytest.mark.parametrize("seed", [12, 13, 14])
def test_weighted_seed_with_noisy_plecs_waveform(seed):
    _, _, a = sample()
    # Same order as the archived noise protocol: interpolate THEN add independent
    # per-sample noise. These are regression fixtures, not new PLECS simulations.
    t = np.linspace(0, .0008, 40001)
    clean = np.column_stack([np.interp(t, a[:, 0], a[:, col]) for col in (1, 2)])
    sigma = .03*np.sqrt(np.mean(clean**2, axis=0))
    noisy = clean + np.random.default_rng(seed).normal(size=clean.shape)*sigma
    gate = a[np.maximum(0, np.searchsorted(a[:, 0], t, side="right")-1), 3]
    train = np.column_stack((t, noisy, gate))
    assert select_window_cycles(train, 100000, 0)[0] == 3
    params, _ = weak_integral_seed(train, FitOptions(mode="LCR"))
    np.testing.assert_allclose(params, [60e-6, 500e-6, 6], rtol=.03)


def test_joint_rollout_uses_training_start_without_holdout_reset(monkeypatch):
    data, mapping, _ = sample()
    calls = []

    def fake_refine(train, params, options):
        calls.append(train.copy())
        return params, {"rollout_initial_state_A_V":[.05, 12.01], "stop_reason":"epoch_budget", "best_at_budget_end":False}

    monkeypatch.setattr("backend.pann_adapter.refine", fake_refine)
    options = FitOptions(method="pann", end_s=.0008)
    original = identify(data, mapping, options)
    mask = data.frame[mapping.time] > original["train_end_s"]
    data.frame.loc[mask, mapping.current] += 2
    data.frame.loc[mask, mapping.voltage] += 3
    altered = identify(data, mapping, options)
    np.testing.assert_array_equal(calls[0], calls[1])
    assert original["parameters"] == altered["parameters"]
    assert original["diagnostics"]["rollout_initial_state_A_V"] == [.05,12.01]
    assert original["chart"]["predicted_voltage_V"] == altered["chart"]["predicted_voltage_V"]
    assert altered["metrics"]["voltage_rmse_V"] > 2


def test_weighted_method_returns_estimate_without_pann(monkeypatch):
    data, mapping, _ = sample()
    rng = np.random.default_rng(12)
    # Actual API path must accept modest negative measurement noise at startup.
    data.frame[mapping.current] += rng.normal(0, .1, len(data.frame))
    data.frame[mapping.voltage] += rng.normal(0, .2, len(data.frame))
    data.frame.loc[data.frame.index[0], mapping.current] = -.1
    def no_pann(*args, **kwargs):
        raise AssertionError("Standalone weighted identification must not call PANN")
    monkeypatch.setattr("backend.pann_adapter.refine", no_pann)
    options = FitOptions(method="weighted_integral", mode="LC", end_s=.0008)
    result = identify(data, mapping, options)
    a, _ = extract(data, mapping)
    train = a[a[:, 0] <= result["train_end_s"]]
    expected, _ = weak_integral_seed(train, options)
    np.testing.assert_allclose(list(result["parameters"].values()), expected)
    assert result["parameters"]["R_ohm"] == 6
    assert "pann" not in result["diagnostics"]
    assert result["diagnostics"]["rollout_initial_state_A_V"][0] == 0
    assert result["method"] == "weighted_integral"


@pytest.mark.skipif(not available(), reason="Optional PyTorch/PANN_PYTHON not configured")
def test_selected_joint_states_match_selected_parameter_epoch():
    _, _, a = sample()
    train = a[a[:, 0] <= .00016].copy()
    train[:, 1:3] += np.random.default_rng(12).normal(size=train[:, 1:3].shape) * [.04,.08]
    options = FitOptions(method="pann", mode="LC", resistance_ohm=6, epochs=10)
    initial, _ = weak_integral_seed(train, options)
    params, diagnostic = refine(train, initial, options)
    assert diagnostic["state_warmup_epochs"] == 50
    for record in diagnostic["history"]:
        if record["phase"] == "state_warmup":
            np.testing.assert_allclose([record["L_H"], record["C_F"], record["R_ohm"]], initial, rtol=1e-12)
    u, target, observed, starts, dt = training_windows(train, options.frequency_hz, diagnostic["window_cycles"])
    states = np.array(diagnostic["initial_states_A_V"])
    assert (states >= 0).all()
    assert np.max(np.abs(states-observed)) > 1e-6
    assert params[2] == 6
    # Independently replay the original explicit-Euler equations with the saved
    # states AND parameters. A final-epoch/best-epoch mismatch changes this loss.
    predictions = []
    x = states.copy()
    for step in range(u.shape[1]):
        va = options.vin*u[:,step,0]
        va = np.where((u[:,step,0] == 0) & (x[:,0] <= 0),x[:,1],va)
        x = np.column_stack((np.maximum(0,x[:,0]+dt/params[0]*(va-x[:,1])), x[:,1]+dt/params[1]*(x[:,0]-x[:,1]/params[2])))
        predictions.append(x.copy())
    scale = target.reshape(-1,2).std(axis=0,ddof=1).clip(1e-3)
    loss = np.mean(((np.stack(predictions,axis=1)-target)/scale)**2)
    assert loss == pytest.approx(diagnostic["best_training_loss"], rel=1e-9)
    assert diagnostic["rollout_initial_state_A_V"] == diagnostic["initial_states_A_V"][0]
    fixed, d_fixed = refine(train, initial, options.model_copy(update={"pann_initial_state":"measured"}))
    np.testing.assert_array_equal(d_fixed["initial_states_A_V"], observed)
    assert d_fixed["estimated_state_scalars"] == 0
    assert d_fixed["state_warmup_epochs"] == 0
