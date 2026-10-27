"""Training-only noise proxy and held-out residual diagnostics; never a fitter.

All screening thresholds below are engineering heuristics, not calibrated tests.
Raw timestamps/measurements are used, never display or resampled training curves.
"""
import numpy as np


def describe_measurements(report):
    if not report:
        return ""
    labels = {"bias": "均值偏置", "slow_variation": "慢变化", "correlated_residual": "相邻相关", "edge_concentration": "开关边沿集中"}
    lines = []
    for key, label in (("current", "电流"), ("voltage", "电压")):
        c = report["channels"][key]
        if c["noise_sigma"] is None:
            line = f"{label}：噪声代理估计不足或低于分辨水平，暂不能判断是否接近噪声底"
        else:
            line = f"{label}：估计噪声标准差 {c['noise_sigma']:.4g} {c['unit']}，留出残差 RMSE 为其 {c['rmse_to_noise_ratio']:.3g} 倍"
        if c["flags"]:
            line += "；诊断线索：" + "、".join(labels[f] for f in c["flags"])
        if c["noise_above_application_threshold"]:
            line += "；估计噪声已高于 5% 应用门槛，应优先检查测量条件"
        lines.append(line + "。")
    return "\n".join(lines) + "\n噪声与残差筛查是启发式估计，不改变原有 5% 复核门槛；线索需对照验证。"


def _weights(t):
    dt = np.diff(t)
    return np.r_[dt[0]/2, (dt[:-1]+dt[1:])/2, dt[-1]/2] / (t[-1]-t[0])


def noise_proxy(train, frequency_hz, gate=None):
    t, y = train[:, 0], train[:, 1:3]
    result = {"status": "insufficient_samples", "sigma_A_V": [None, None],
              "samples_used": 0, "source": "raw_training_measurements_only",
              "edge_exclusion": "gate_triplets" if gate is not None else "unavailable",
              "calibrated": False}
    if len(t) < 66:
        return result
    dt = np.diff(t)
    left = dt[:-1] / (dt[:-1]+dt[1:])
    # Linear interpolation removes a local slope (including much of the real
    # switching ripple); normalization assumes independent equal-variance noise.
    residual = (y[1:-1] - (1-left[:, None])*y[:-2] - left[:, None]*y[2:])
    residual /= np.sqrt(1+(1-left[:, None])**2+left[:, None]**2)
    good = (np.maximum(dt[:-1], dt[1:])*frequency_hz <= .1)
    if gate is not None:
        good &= (gate[:-2] == gate[1:-1]) & (gate[1:-1] == gate[2:])
    residual = residual[good]
    result["samples_used"] = len(residual)
    if len(residual) < 64:
        return result
    sigma = np.median(np.abs(residual-np.median(residual, axis=0)), axis=0)/.67448975
    scale = np.sqrt(np.sum(y*y*_weights(t)[:, None], axis=0))
    values = [float(s) if s > max(1e-12, rms*1e-9) else None for s, rms in zip(sigma, scale)]
    result.update(status="estimated" if all(s is not None for s in values) else "below_resolution",
                  sigma_A_V=values)
    return result


def assess_measurements(raw_train, valid, predicted, frequency_hz, *, training_gate=None, validation_gate=None):
    noise = noise_proxy(raw_train, frequency_hz, training_gate)
    t, measured = valid[:, 0], valid[:, 1:3]
    residual = measured-predicted
    weights = _weights(t)
    dt = np.diff(t)
    uniform = bool(np.max(np.abs(dt/np.median(dt)-1)) <= .1)
    edge_mask = np.zeros(len(t), dtype=bool)
    edges = np.array([])
    if validation_gate is not None:
        edges = t[1:][np.diff(validation_gate) != 0]
        if len(edges):
            index = np.searchsorted(edges, t)
            distance = np.minimum(np.abs(t-edges[np.clip(index, 0, len(edges)-1)]),
                                  np.abs(t-edges[np.clip(index-1, 0, len(edges)-1)]))
            edge_mask = distance <= max(np.median(dt), .01/frequency_hz)
    channels = {}
    bins = min(8, int((t[-1]-t[0])*frequency_hz/2))
    for j, name in enumerate(("current", "voltage")):
        r = residual[:, j]
        mean = float(np.sum(weights*r))
        rmse = float(np.sqrt(np.sum(weights*r*r)))
        rms = float(np.sqrt(np.sum(weights*measured[:, j]**2)))
        sigma = noise["sigma_A_V"][j]
        floor = max(1e-12, rms*1e-9)
        rho = None
        if uniform and len(r) >= 32 and np.std(r[:-1]) > floor and np.std(r[1:]) > floor:
            rho = float(np.corrcoef(r[:-1], r[1:])[0, 1])
        drift = None
        if bins >= 3:
            means = []
            boundaries = np.linspace(t[0], t[-1], bins+1)
            for k in range(bins):
                mask = (t >= boundaries[k]) & ((t <= boundaries[k+1]) if k == bins-1 else (t < boundaries[k+1]))
                if np.count_nonzero(mask) >= 3:
                    means.append(float(np.sum(weights[mask]*r[mask])/np.sum(weights[mask])))
            if len(means) == bins:
                drift = max(means)-min(means)
        edge_ratio = None
        if len(edges) >= 5 and edge_mask.sum() >= 20 and (~edge_mask).sum() >= 20:
            edge_rms = np.sqrt(np.sum(weights[edge_mask]*r[edge_mask]**2)/np.sum(weights[edge_mask]))
            rest_rms = np.sqrt(np.sum(weights[~edge_mask]*r[~edge_mask]**2)/np.sum(weights[~edge_mask]))
            if rest_rms > floor:
                edge_ratio = float(edge_rms/rest_rms)
        ratio = rmse/sigma if sigma is not None else None
        flags = []
        if sigma is not None and abs(mean) > .5*sigma:
            flags.append("bias")
        if sigma is not None and drift is not None and drift > 2*sigma:
            flags.append("slow_variation")
        if rho is not None and abs(rho) > .2:
            flags.append("correlated_residual")
        if edge_ratio is not None and edge_ratio > 2:
            flags.append("edge_concentration")
        comparison = "unavailable" if ratio is None else "above_noise" if ratio > 1.5 else "below_noise" if ratio < .5 else "same_order"
        noise_pct = 100*sigma/max(rms, 1e-9) if sigma is not None else None
        channels[name] = {"unit": "A" if j == 0 else "V", "noise_sigma": sigma,
                          "noise_pct_of_validation_rms": noise_pct, "residual_rmse": rmse,
                          "residual_mean": mean, "rmse_to_noise_ratio": ratio,
                          "noise_comparison": comparison, "lag1_correlation": rho,
                          "lag1_status": "available" if rho is not None else "irregular_or_insufficient",
                          "block_mean_span": drift, "edge_to_nonedge_rmse_ratio": edge_ratio,
                          "flags": flags, "noise_above_application_threshold": noise_pct is not None and noise_pct > 5}
    return {"version": "noise-residual-v1", "noise_estimation": noise, "channels": channels,
            "residual_definition": "heldout_measurement_minus_continuous_prediction",
            "scope": "same_record_holdout_not_new_record_prediction",
            "edge_source": "measured_heldout_PWM" if validation_gate is not None else "unavailable_no_heldout_switch_information",
            "application_threshold_pct": 5, "thresholds_are_heuristic": True,
            "thresholds": {"rmse_noise_same_order": [.5, 1.5], "bias_sigma": .5,
                           "block_mean_span_sigma": 2, "absolute_lag1": .2, "edge_rmse_ratio": 2},
            "limitations": ["Local noise proxy is not sensor calibration; curvature, correlated noise and sampling affect it.",
                            "Training noise may differ from held-out noise; zero/unresolved estimates are unavailable, not noiseless.",
                            "Flags are diagnostic clues, not causal diagnoses or a residual whiteness test.",
                            "No parameter confidence interval or new-condition validation is computed here."]}
