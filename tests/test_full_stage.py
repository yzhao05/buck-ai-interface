"""Exercise the public identification path, SI units, component switches and holdout."""
import numpy as np
import pandas as pd
import pytest
from backend.data import read_csv, suggest_mapping
from backend.full_stage import PARTS, simulate
from backend.identification import identify
from backend.schemas import FitOptions, Mapping, SavedExperiment
from backend.report import ReportRequest, build_pdf


@pytest.fixture(scope="module")
def waveform():
    t = np.arange(1801)*1e-7
    gate = (np.arange(len(t))%100 < 45).astype(float)
    parts = [1.3e-4,.21,4e-9,83e-6,.12,2e-7,.24]
    return t, gate, parts


def run(t, gate, y, entries):
    frame = pd.DataFrame(dict(time=t,current=y[:,0],voltage=y[:,1],pwm=gate))
    data = read_csv(frame.to_csv(index=False).encode(),"circuit.csv")
    opts = FitOptions(mode="FULL_STAGE", pwm_source="measured", **{p[1]:v for p,v in zip(PARTS,entries)})
    return identify(data, Mapping(**suggest_mapping(data)), opts)


def test_blank_and_mixed(waveform):
    t, gate, parts = waveform
    y = simulate(t,gate,28,6,parts,1e5)
    result = run(t,gate,y,[None]*7)
    np.testing.assert_allclose([result["parameters"][p[0]] for p in PARTS],parts,rtol=.015)
    assert result["metrics"]["voltage_nrmse_pct"] < .1
    assert set(result["diagnostics"]["parameter_status"].values()) == {"estimate"}
    parts = [*parts]
    parts[2] = parts[5] = 0.
    y = simulate(t,gate,28,6,parts,1e5)
    supplied = [parts[0],None,0,parts[3],None,0,parts[6]]
    result = run(t,gate,y,supplied)
    assert result["diagnostics"]["parameter_status"]["Cw_F"] == "ignored"
    assert result["diagnostics"]["parameter_status"]["L_H"] == "fixed"
    np.testing.assert_allclose([result["parameters"][p[0]] for p in PARTS],parts,rtol=.01,atol=1e-12)
    # Held-out voltage cannot affect estimates.
    y[-200:,1] += .5
    shifted = run(t,gate,y,supplied)
    assert shifted["parameters"] == result["parameters"]
    assert shifted["metrics"] != result["metrics"]


@pytest.mark.parametrize("terminal", ["Load Voltage / V", "Vout", "Vo [V]", "输出端电压"])
def test_scope_mapping_prefers_output_terminal(terminal):
    columns = ["Time","Inductor Current","Capacitor Voltage / V",terminal]
    data = read_csv(pd.DataFrame(np.zeros((40,4)),columns=columns).to_csv(index=False).encode(), "scope.csv")
    assert suggest_mapping(data)["voltage"] == terminal


def test_all_zero_and_algebraic_switch(waveform):
    t,gate,_ = waveform
    # L shorted, capacitor branch open. Independent voltage-divider reference.
    for ron in (0., .4):
        parts = [0., .2, 0., 0., 0., 0., ron]
        current = gate*28/(6+.2+ron)
        y = np.column_stack((current,6*current))
        result = run(t,gate,y,parts)
        # The RC sensing network also sources/sinks a small current at vo.
        i, vo = (np.array(result["chart"][key]) for key in ("predicted_current_A","predicted_voltage_V"))
        np.testing.assert_allclose(vo[gate==1] + (ron+.2)*i[gate==1], 28., atol=1e-8)
        assert np.max(abs(i - vo/6)) < 28/11000
        assert result["metrics"]["current_rmse_A"] < .003
        assert result["metrics"]["voltage_rmse_V"] < .01
        assert result["parameters"]["Ron_ohm"] == ron
        SavedExperiment(id="run",created=0,updated=0,dataset={},result={k:v for k,v in result.items() if k!="chart"})
        report = ReportRequest.model_validate(dict(result=result,exported_at="2026-11-20T08:00:00Z",language="en"))
        assert build_pdf(report).startswith(b"%PDF")
    result=run(t,gate,np.c_[gate*28/6,gate*28],[0.]*7)
    assert set(result["diagnostics"]["parameter_status"].values()) == {"ignored"}


def test_explicit_zero_in_simplified_web_mode():
    from backend.parasitics import estimated_resistances
    settings=FitOptions(mode="LC_RL_RC",joint_zero_is_fixed=True,inductor_resistance_ohm=0)
    assert estimated_resistances(settings)==(False,True)


def test_ignored_capacitor_disconnects_its_series_branch(waveform):
    t,gate,original=waveform
    parts=original.copy()
    parts[3]=0.
    a=simulate(t,gate,28,6,parts,1e5)
    parts[4:6]=[.9,4e-6]
    b=simulate(t,gate,28,6,parts,1e5)
    np.testing.assert_allclose(a,b,atol=1e-8)
    supplied = list(parts)
    supplied[4:6] = [None,None]
    result = run(t,gate,a,supplied)
    assert set(result["diagnostics"]["unobservable_parameters"])=={"rC_ohm","ESL_H"}
    assert result["quality"]=="需复核"
    assert result["parameters"]["rC_ohm"] is None and result["parameters"]["ESL_H"] is None
    snapshot = {k:v for k,v in result.items() if k != "chart"}
    for old_value in (None, 0.):
        snapshot["parameters"] = {**result["parameters"], "rC_ohm": old_value, "ESL_H": old_value}
        saved = SavedExperiment(id="run",created=0,updated=0,dataset={},result=snapshot)
        assert saved.result["parameters"]["rC_ohm"] is None
    assert build_pdf(ReportRequest(result=result, exported_at="2026-11-20T08:00:00Z")).startswith(b"%PDF")
    from backend.experiment_history import repeatability_summary, summarize_experiments
    from backend.schemas import ChatExperiment
    records = [ChatExperiment(id=key, dataset={"sha256":key*64}, method="circuit_fit", pwm_source="measured",
        parameters=snapshot["parameters"], comparison_context={"options":result["options"], "mapping":result["mapping"],
        "start_s":result["train_start_s"], "end_s":result["validation_end_s"]}) for key in ("a","b")]
    assert summarize_experiments(records)[0]["parameters"]["ESL_H"] is None
    assert repeatability_summary(records)["groups"][0]["parameters"] == {}
    # Older archives used CSV-timed integration. Keep them in a separate group.
    records[1].comparison_context.fixed_step_s = result["diagnostics"]["fixed_step_s"]
    assert len(repeatability_summary(records)["groups"]) == 2



@pytest.mark.parametrize("first_ok,second_ok", [(True, False), (False, True)])
def test_fit_keeps_the_converged_start(monkeypatch, waveform, first_ok, second_ok):
    from types import SimpleNamespace
    from backend import full_stage
    t, gate, parts = waveform
    data = np.column_stack((t, gate, gate))
    opts = FitOptions(mode="FULL_STAGE", **{p[1]: (None if i == 2 else parts[i]) for i,p in enumerate(PARTS)})
    outcomes = iter([first_ok, second_ok])

    def optimizer(_, x, **kwargs):
        converged = next(outcomes)
        return SimpleNamespace(success=converged, x=x, fun=np.ones(2*len(t)),
                               cost=float(converged), jac=np.ones((2*len(t),1)),
                               active_mask=np.zeros(1), nfev=2)

    monkeypatch.setattr(full_stage, "least_squares", optimizer)
    fitted, diagnostics = full_stage.fit(data, opts, None)
    assert fitted[2] > 0
    assert diagnostics["parameter_status"]["Cw_F"] == "estimate"


def test_plecs_switching_edge_reference():
    # Independent PLECS Scope export on the internal ticks bracketing diode commutation.
    t = np.array([0., 1.002e-6, 21e-6, 21.002e-6, 21.004e-6])
    parts = [40e-6, .4, 30e-12, 500e-6, .3, 15e-9, .04]
    y = simulate(t, None, 28, 6, parts, 1e5)
    np.testing.assert_allclose(y[:,1], [15.89400038,10.42103571,8.96487321,38.152294,8.70736408], atol=2e-6, rtol=0)


def test_closed_loop_three_columns_and_cropped_power_up(waveform):
    t, _, parts = waveform
    y = simulate(t, None, 28, 6, parts, 1e5)
    # Corrupt the first observation: the power-stage and RC initial states come from the model.
    frame = pd.DataFrame(dict(time=t, current=y[:,0], voltage=y[:,1]))
    frame.loc[0, "voltage"] += 1.
    data = read_csv(frame.to_csv(index=False).encode(), "scope.csv")
    opts = FitOptions(mode="FULL_STAGE", **{p[1]:v for p,v in zip(PARTS,parts)})
    result = identify(data, Mapping(**suggest_mapping(data)), opts)
    assert opts.pwm_source == "controller"
    assert result["diagnostics"]["sampling_branch_included"]
    assert result["diagnostics"]["fixed_step_s"] == 2e-9
    np.testing.assert_allclose(result["diagnostics"]["rollout_initial_state_A_V"], y[0])
    assert result["metrics"]["voltage_rmse_V"] < 1e-6
    # Decimation and a late scoring window must not move the internal clock or reset storage.
    np.testing.assert_allclose(simulate(t[23::7],None,28,6,parts,1e5), y[23::7], atol=1e-7)
