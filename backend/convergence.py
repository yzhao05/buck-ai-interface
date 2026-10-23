"""Training-only stability heuristic; never evidence of parameter accuracy.

Compare two adjacent, non-overlapping windows of 20 post-update evaluations.
Check after each epoch; exclude epoch zero and state warm-up. Three passing
checks at the end require at least 42 parameter-training epochs. Trial rule,
not calibrated: mean loss can conceal oscillations within a window.
"""
import numpy as np


def assess_training(history, selected, best_loss, mode):
    thresholds = {"window_epochs": 20, "required_stable_checks": 3,
                  "minimum_epochs": 42, "loss_mean_change_pct": .1,
                  "parameter_span_pct": .1, "loss_scale_floor": 1e-12,
                  "loss_absolute_tolerance": 1e-10}
    result = {"version": "two-window-stability-v2", "status": "insufficient_history",
              "thresholds": thresholds, "stable_checks": 0, "evaluated_checks": 0,
              "reasons": [{"code": "pann_insufficient_history"}]}
    if len(history) < 41:
        return result
    keys = ("L_H", "C_F", "R_ohm") if mode == "LCR" else ("L_H", "C_F")
    epochs = np.array([h["epoch"] for h in history])
    losses = np.array([h["loss"] for h in history], dtype=float)
    values = np.array([[h[k] for k in keys] for h in history], dtype=float)
    chosen = np.asarray(selected, dtype=float)[:len(keys)]
    if (epochs[0] != 0 or not np.all(np.diff(epochs) == 1)
            or not np.isfinite(losses).all() or np.any(losses < 0)
            or not np.isfinite(values).all() or np.any(values <= 0)
            or not np.isfinite(chosen).all() or np.any(chosen <= 0)
            or not np.isfinite(best_loss) or best_loss < 0):
        return result
    stable = 0
    for end in range(40, len(history)):
        previous_loss = losses[end-39:end-19]
        tail_loss, tail_params = losses[end-19:end+1], values[end-19:end+1]
        previous_mean, current_mean = float(np.mean(previous_loss)), float(np.mean(tail_loss))
        absolute_change = abs(current_mean-previous_mean)
        relative_change = absolute_change / max(previous_mean, thresholds["loss_scale_floor"]) * 100
        param_span = np.ptp(tail_params, axis=0) / np.mean(tail_params, axis=0) * 100
        loss_passed = relative_change < thresholds["loss_mean_change_pct"] or absolute_change < thresholds["loss_absolute_tolerance"]
        passed = loss_passed and np.max(param_span) < thresholds["parameter_span_pct"]
        stable = stable + 1 if passed else 0
    gaps = np.max(np.abs(tail_params-chosen), axis=0) / chosen * 100
    loss_gap = float(max(0., np.mean(tail_loss)-best_loss) / max(best_loss, thresholds["loss_scale_floor"]) * 100)
    reasons = []
    if not loss_passed:
        reasons.append({"code": "pann_loss_mean_change", "value": relative_change, "threshold": thresholds["loss_mean_change_pct"]})
    if np.max(param_span) >= thresholds["parameter_span_pct"]:
        reasons.append({"code": "pann_parameter_window_change", "value": float(np.max(param_span)), "threshold": thresholds["parameter_span_pct"]})
    if stable < thresholds["required_stable_checks"]:
        reasons.append({"code": "pann_stability_short", "value": stable, "threshold": thresholds["required_stable_checks"]})
    result.update(status="stable" if not reasons else "not_stable", reasons=reasons,
                  evaluated_checks=len(history)-40, stable_checks=stable,
                  previous_start_epoch=int(epochs[-40]), previous_end_epoch=int(epochs[-21]),
                  start_epoch=int(epochs[-20]), end_epoch=int(epochs[-1]),
                  previous_mean_loss=previous_mean, current_mean_loss=current_mean,
                  loss_mean_change_pct=relative_change, loss_absolute_change=absolute_change,
                  loss_absolute_tolerance_used=bool(absolute_change < thresholds["loss_absolute_tolerance"] and relative_change >= thresholds["loss_mean_change_pct"]),
                  parameter_span_pct=dict(zip(keys, param_span.tolist())),
                  selected_parameter_gap_pct=dict(zip(keys, gaps.tolist())), selected_loss_gap_pct=loss_gap)
    return result


def assess_result(metrics, diagnostics, method):
    waveform = all(np.isfinite(metrics.get(f"{c}_nrmse_pct", np.nan)) and metrics[f"{c}_nrmse_pct"] <= 5 for c in ("current", "voltage"))
    training = diagnostics.get("pann", {}).get("convergence") if method == "pann" else {"status": "not_applicable"}
    return {"waveform": {"status": "passed" if waveform else "review", "threshold_pct": 5},
            "training": training or {"status": "insufficient_history", "reasons": [{"code": "pann_insufficient_history"}]},
            "parameter_accuracy": {"status": "unverified", "reason": "no_ground_truth"}}
