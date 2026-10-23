import numpy as np
import pytest

from backend.convergence import assess_training, assess_result
from backend.identification import review_reasons


def trace(n=60, loss=lambda i: .01, l=lambda i: 60e-6, r=lambda i: 6):
    return [{"epoch": i, "loss": loss(i), "L_H": l(i), "C_F": 500e-6, "R_ohm": r(i)} for i in range(n+1)]


def assess(h, mode="LC", selected=None):
    best = min(h, key=lambda x:x["loss"])
    return assess_training(h, selected or [best[k] for k in ("L_H", "C_F", "R_ohm")], best["loss"], mode)


def codes(c):
    return {r["code"] for r in c["reasons"]}


def test_tiny_improvement_at_last_epoch_is_stable():
    c = assess(trace(loss=lambda i:.01-i*1e-8))
    assert c["status"] == "stable"
    assert c["current_mean_loss"] < c["previous_mean_loss"]
    assert c["previous_start_epoch"] == 21 and c["previous_end_epoch"] == 40
    assert c["start_epoch"] == 41 and c["end_epoch"] == 60
    assert c["stable_checks"] == 21
    d = {"parameter_at_bound":False,"CR_condition_number":1,"pann":{"best_at_budget_end":True,"convergence":c}}
    assert review_reasons({"current_nrmse_pct":.1,"voltage_nrmse_pct":.1},d,"pann") == []


def test_penultimate_best_does_not_hide_continuing_improvement():
    h=trace(loss=lambda i:.1-i*.001 if i<60 else .042)
    assert min(h,key=lambda x:x["loss"])["epoch"] == 59
    assert "pann_loss_mean_change" in codes(assess(h))


def test_equal_window_means_follow_trial_rule_despite_within_window_oscillation():
    c=assess(trace(loss=lambda i:.01*(1+.05*np.sin(i*np.pi/2))))
    # Explicit limitation: mean comparisons can hide periodic loss oscillation.
    assert c["status"] == "stable"


def test_parameter_drift_matters_despite_constant_loss():
    c=assess(trace(l=lambda i:60e-6*(1+.001*i)))
    assert "pann_parameter_window_change" in codes(c)


def test_stability_must_persist_at_end_not_earlier():
    h=trace(n=41)
    assert assess(h)["stable_checks"] == 2
    assert codes(assess(h)) == {"pann_stability_short"}
    assert assess(trace(n=42))["status"] == "stable"
    h=trace();h[-1]["loss"] *= 2
    assert assess(h)["stable_checks"] == 0


def test_best_result_gaps_are_informational_not_extra_trial_gates():
    h=trace(l=lambda i:60e-6 if i<5 else 65e-6)
    c=assess(h)
    assert c["stable_checks"] >= 10
    assert c["status"] == "stable"
    assert c["selected_parameter_gap_pct"]["L_H"] > .5
    h=trace(loss=lambda i:.01 if i==0 else .02)
    assert assess(h)["status"] == "stable"
    assert assess(h)["selected_loss_gap_pct"] > 1


def test_fixed_r_is_not_a_training_variable():
    h=trace(r=lambda i:6*(1+.01*i))
    assert assess(h,"LC")["status"] == "stable"
    assert "pann_parameter_window_change" in codes(assess(h,"LCR"))


@pytest.mark.parametrize("history", [[], trace(n=10), trace()[::10]])
def test_missing_short_or_sparse_history_is_unknown(history):
    assert assess_training(history,[60e-6,500e-6,6],.01,"LC")["status"] == "insufficient_history"


def test_invalid_history_and_near_zero_loss():
    h=trace();h[-1]["loss"]=float("nan")
    assert assess(h)["status"] == "insufficient_history"
    assert assess(trace(loss=lambda i:1e-14*(1+i%2)))["status"] == "stable"


def test_formula_and_absolute_tolerance_are_recorded():
    h=trace(n=42,loss=lambda i: .01 if i<=22 else .01002)
    c=assess(h)
    assert c["previous_mean_loss"] == pytest.approx(.01)
    assert c["current_mean_loss"] == pytest.approx(.01002)
    assert c["loss_mean_change_pct"] == pytest.approx(.2)
    assert "pann_loss_mean_change" in codes(c)
    c=assess(trace(loss=lambda i:1e-14 if i<40 else 2e-14))
    assert c["status"] == "stable" and c["loss_absolute_tolerance_used"]
    # Epoch-zero loss must not enter either window.
    h=trace(n=42);h[0]["loss"]=1e6
    assert assess(h)["status"] == "stable"


def test_parameter_peak_to_peak_uses_recent_window_mean():
    h=trace(l=lambda i:60e-6*(1+.00006*(i%2)))
    assert assess(h)["status"] == "stable"
    h=trace(l=lambda i:60e-6*(1+.002*(i%2)))
    assert "pann_parameter_window_change" in codes(assess(h))


def test_exact_threshold_is_not_less_than_threshold():
    c=assess(trace(n=42,loss=lambda i:1000. if i<=22 else 1001.))
    assert c["loss_mean_change_pct"] == .1
    assert "pann_loss_mean_change" in codes(c)
    c=assess(trace(l=lambda i:999.5 if i%2 else 1000.5))
    assert c["parameter_span_pct"]["L_H"] == .1
    assert "pann_parameter_window_change" in codes(c)


def test_waveform_training_and_truth_are_independent():
    c=assess(trace())
    a=assess_result({"current_nrmse_pct":8,"voltage_nrmse_pct":.1},{"pann":{"convergence":c}},"pann")
    assert a["waveform"]["status"] == "review"
    assert a["training"]["status"] == "stable"
    assert a["parameter_accuracy"]["status"] == "unverified"
    a=assess_result({"current_nrmse_pct":.1,"voltage_nrmse_pct":.1},{},"weighted_integral")
    assert a["waveform"]["status"] == "passed"
    assert a["training"]["status"] == "not_applicable"
    assert a["parameter_accuracy"]["status"] == "unverified"
