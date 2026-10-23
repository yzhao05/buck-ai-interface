import pytest
from backend.identification import review_reasons


def evidence(**changes):
    data = dict(parameter_at_bound=False, CR_condition_number=100)
    data.update(changes)
    return data


def test_thresholds_are_strict_and_do_not_flag_clean_results():
    assert review_reasons(dict(current_nrmse_pct=5, voltage_nrmse_pct=5), evidence(), "weighted_integral") == []


@pytest.mark.parametrize("channel", ["current", "voltage"])
def test_only_the_triggering_channel_is_reported(channel):
    metrics = dict(current_nrmse_pct=.1, voltage_nrmse_pct=.2)
    metrics[channel + "_nrmse_pct"] = 5.01
    assert review_reasons(metrics, evidence(), "weighted_integral") == [dict(code=channel + "_nrmse", value=5.01, threshold=5)]


def test_small_waveform_errors_can_still_require_pann_review():
    diagnostics = evidence(pann=dict(stop_reason="time_budget", best_at_budget_end=True, epochs=100, completed_epochs=72, best_epoch=72))
    reasons = review_reasons(dict(current_nrmse_pct=.01, voltage_nrmse_pct=.01), diagnostics, "pann")
    assert reasons == [dict(code="pann_time_budget", completed_epochs=72, requested_epochs=100), dict(code="pann_best_at_end", best_epoch=72, completed_epochs=72)]
    assert review_reasons(dict(current_nrmse_pct=.01, voltage_nrmse_pct=.01), diagnostics, "weighted_integral") == []


def test_all_simultaneous_gates_are_retained():
    reasons = review_reasons(dict(current_nrmse_pct=6, voltage_nrmse_pct=7), evidence(parameter_at_bound=True, CR_condition_number=101), "pann")
    assert [r["code"] for r in reasons] == ["current_nrmse", "voltage_nrmse", "parameter_at_bound", "condition_number"]


def test_epoch_budget_alone_is_not_a_review_trigger():
    assert review_reasons(dict(current_nrmse_pct=0, voltage_nrmse_pct=0), evidence(pann=dict(stop_reason="epoch_budget", best_at_budget_end=False)), "pann") == []
