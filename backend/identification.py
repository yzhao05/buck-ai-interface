"""Bounded integral identification and independent held-out forward rollout.

Ideal asynchronous Buck, constant Rload over selected interval. The fitter
never reads filenames, nominal component values, or held-out measurements.
"""
import time

import numpy as np
from scipy.integrate import cumulative_trapezoid, solve_ivp
from scipy.optimize import lsq_linear

from .data import Dataset, chart_data, extract
from .schemas import FitOptions, Mapping


def window_indices(t, fs, cycles_list=(0.45, 1.4, 4.2), maximum=700):
    pairs = []
    for cycles in cycles_list:
        starts = np.linspace(t[0], t[-1] - cycles / fs, min(maximum, len(t) // 4))
        if len(starts) == 0 or starts[-1] <= starts[0]:
            continue
        left = np.searchsorted(t, starts)
        right = np.minimum(np.searchsorted(t, starts + cycles / fs), len(t) - 1)
        valid = right - left >= 2
        pairs.extend(zip(left[valid], right[valid]))
    if len(pairs) < 20:
        raise ValueError("训练段太短或采样太稀；需要至少约 10 个开关周期及充分的周期内采样。")
    return np.array(pairs).T


def weak_integral_seed(train, options):
    """Standalone weighted-integral estimation, also used to initialize PANN.

    phi(u)=u^2(1-u)^2. Integration by parts replaces noisy endpoint differences
    with -integral(phi' * measurement). Three-point Gauss quadrature exactly
    integrates the piecewise-linear measurements times this quartic weight.
    PWM stays ZOH. No smoothing targets or known component values are supplied.
    """
    t = train[:, 0]
    left, right = window_indices(t, options.frequency_hz, (2., 5., 10.), 60)
    nodes, weights = np.polynomial.legendre.leggauss(3)
    fractions = (nodes + 1) / 2
    rows, l_rows = [], []
    inactive = (train[:, 1] <= max(1e-6, np.max(np.abs(train[:, 1])) * .003)) & (train[:, 3] == 0)
    for a, b in zip(left, right):
        times = t[a:b + 1]
        span = times[-1] - times[0]
        dt = np.diff(times)
        u = (times[:-1, None] + dt[:, None] * fractions - times[0]) / span
        phi = u ** 2 * (1-u) ** 2
        dphi = (2*u - 6*u**2 + 4*u**3) / span
        measured = train[a:b, None, 1:3] + fractions[None, :, None] * np.diff(train[a:b+1, 1:3], axis=0)[:, None, :]
        quadrature = dt[:, None] * weights / 2
        integrals = np.sum((quadrature * phi)[:, :, None] * measured, axis=(0, 1))
        change = -np.sum((quadrature * dphi)[:, :, None] * measured, axis=(0, 1))
        pwm_integral = np.sum(quadrature * phi * train[a:b, 3, None])
        sense_integral = 0.
        if train.shape[1] == 5:
            sense = train[a:b, None, 4] + fractions * np.diff(train[a:b+1, 4])[:, None]
            sense_integral = np.sum(quadrature * phi * sense)
        rows.append((change[1], integrals[1], integrals[0]-sense_integral))
        if not inactive[a:b+1].any():
            l_rows.append((change[0], options.vin * pwm_integral - integrals[1]))
    if len(l_rows) < 12:
        raise ValueError("加权积分初始化的有效电感窗口不足。")
    l_rows = np.asarray(l_rows)
    rows = np.asarray(rows)
    if np.linalg.norm(l_rows[:, 0]) < 1e-10 or np.linalg.norm(rows[:, 0]) < 1e-10:
        raise ValueError("加权积分初始化缺少足够的电流或电压动态。")
    l_fit = lsq_linear(l_rows[:, :1] * 1e-4, l_rows[:, 1], bounds=([.001], [1000]))
    design = np.column_stack((rows[:, 0] * 1e-3, rows[:, 1]))
    if options.mode == "LC":
        target = rows[:, 2] - rows[:, 1] / options.resistance_ohm
        c_fit = lsq_linear(design[:, :1], target, bounds=([.00001], [1000]))
        capacitance, resistance, condition = c_fit.x[0]*1e-3, options.resistance_ohm, 1.
    else:
        norms = np.linalg.norm(design, axis=0)
        if (norms < 1e-14).any():
            raise ValueError("加权积分初始化缺少足够的电压动态。")
        condition = float(np.linalg.cond(design/norms))
        if not np.isfinite(condition) or condition > 1e5:
            raise ValueError("加权积分初始化的 C/R 回归病态，请增加动态激励。")
        c_fit = lsq_linear(design, rows[:, 2], bounds=([.00001, 1e-5], [1000, 100]))
        capacitance, resistance = c_fit.x[0]*1e-3, 1/c_fit.x[1]
    return np.array([l_fit.x[0]*1e-4, capacitance, resistance]), {
        "windows": len(rows), "L_windows": len(l_rows), "CR_condition_number": condition,
        "parameter_at_bound": bool(np.any(l_fit.active_mask) or np.any(c_fit.active_mask)),
        "method": "端点零权重积分初始化（仅训练段，2/5/10周期窗口）"}


def integral_fit(train, options):
    t, current, voltage, pwm = train.T
    left, right = window_indices(t, options.frequency_hz)
    iv = cumulative_trapezoid(voltage, t, initial=0)
    ii = cumulative_trapezoid(current, t, initial=0)
    iu = np.r_[0.0, np.cumsum(options.vin * pwm[:-1] * np.diff(t))]
    di, dv = current[right] - current[left], voltage[right] - voltage[left]
    v_integral, i_integral = iv[right] - iv[left], ii[right] - ii[left]
    # Off-state zero-current segments do not satisfy L di/dt = -vo in DCM.
    inactive = (current <= max(1e-6, np.max(np.abs(current)) * 0.003)) & (pwm == 0)
    inactive_count = np.r_[0, np.cumsum(inactive)]
    active = (inactive_count[right + 1] - inactive_count[left]) == 0
    active &= np.abs(di) > max(1e-6, np.ptp(current) * 0.002)
    if active.sum() < 12:
        raise ValueError("可用导通动态不足，无法可靠辨识 L；请选择包含电流变化的时间段。")
    x = di[active, None] * 1e-4
    y = (iu[right] - iu[left] - v_integral)[active]
    l_fit = lsq_linear(x, y, bounds=([0.001], [1000]))
    inductance = float(l_fit.x[0] * 1e-4)
    if options.mode == "LC":
        design = dv[:, None] * 1e-3
        target = i_integral - v_integral / options.resistance_ohm
        c_fit = lsq_linear(design, target, bounds=([0.00001], [1000]))
        capacitance, resistance = float(c_fit.x[0] * 1e-3), options.resistance_ohm
        condition = 1.0
    else:
        design = np.column_stack((dv * 1e-3, v_integral))
        norms = np.linalg.norm(design, axis=0)
        if (norms < 1e-14).any():
            raise ValueError("电压动态不足，C/R 无法分离；请加入启动或负载扰动数据。")
        condition = float(np.linalg.cond(design / norms))
        if not np.isfinite(condition) or condition > 1e5:
            raise ValueError("C/R 回归病态，当前数据不能独立确定参数；需要更丰富的动态激励。")
        c_fit = lsq_linear(design, i_integral, bounds=([0.00001, 1e-5], [1000, 100]))
        capacitance, resistance = float(c_fit.x[0] * 1e-3), float(1 / c_fit.x[1])
    boundary = bool(np.any(l_fit.active_mask) or np.any(c_fit.active_mask))
    return np.array([inductance, capacitance, resistance]), {
        "windows": int(len(left)), "L_windows": int(active.sum()),
        "CR_condition_number": condition, "parameter_at_bound": boundary,
        "method": "多时间窗积分方程 + 带正值边界的最小二乘",
    }


def rollout(a, params, vin, initial_state=None):
    """Continuous rollout with observed PWM. No measurement reset after x0.

    Solve separately between observed gate edges; DCM zero-current events
    switch to the capacitor discharge equation until the next turn-on.
    """
    t, _, _, gate = a.T
    L, C, R = params
    edges = np.r_[0, np.flatnonzero(np.diff(gate)) + 1, len(a) - 1]
    edges = np.unique(edges)
    out = np.zeros((len(a), 2))
    state = a[0, 1:3].copy() if initial_state is None else np.asarray(initial_state, dtype=float).copy()
    if state.shape != (2,) or not np.isfinite(state).all():
        raise ValueError("前向验证初始状态无效。")
    out[0] = state
    def zero_current(tt, xx):
        return xx[0]
    zero_current.terminal = True
    zero_current.direction = -1
    for left, right in zip(edges[:-1], edges[1:]):
        times = t[left:right + 1]
        on = gate[left] > 0.5
        if not on and state[0] <= 1e-10:
            segment = np.column_stack((np.zeros(len(times)), state[1] * np.exp(-(times - times[0]) / (R * C))))
        else:
            def rhs(tt, xx):
                return [(vin * on - xx[1]) / L, (xx[0] - xx[1] / R) / C]
            sol = solve_ivp(rhs, (times[0], times[-1]), state, rtol=2e-7, atol=1e-9,
                            dense_output=True, events=None if on else zero_current)
            if not sol.success:
                raise ValueError("前向仿真未收敛；请检查参数、工况和时间单位。")
            segment = sol.sol(times).T
            if not on and len(sol.t_events[0]):
                stop = sol.t_events[0][0]
                v_stop = sol.y_events[0][0, 1]
                tail = times >= stop
                segment[tail, 0] = 0
                segment[tail, 1] = v_stop * np.exp(-(times[tail] - stop) / (R * C))
        out[left:right + 1] = segment
        state = segment[-1].copy()
    if not np.isfinite(out).all():
        raise ValueError("前向仿真出现非有限值。")
    return out


def metric(t, actual, predicted):
    duration = t[-1] - t[0]
    rmse = np.sqrt(np.trapezoid((actual - predicted) ** 2, t, axis=0) / duration)
    rms = np.sqrt(np.trapezoid(actual ** 2, t, axis=0) / duration)
    return {"current_rmse_A": float(rmse[0]), "voltage_rmse_V": float(rmse[1]),
            "current_nrmse_pct": float(100 * rmse[0] / max(rms[0], 1e-9)),
            "voltage_nrmse_pct": float(100 * rmse[1] / max(rms[1], 1e-9))}


def identify(dataset: Dataset, mapping: Mapping, options: FitOptions, *, progress=None):
    started = time.perf_counter()
    def stage(name):
        if progress is not None:
            progress(name)
    stage("checking")
    a, notes = extract(dataset, mapping)
    reconstructed = options.pwm_source == "controller"
    if reconstructed:
        from .controller import PROFILE, validate_profile, reconstruct_training, closed_loop_rollout
        validate_profile(a, options)
        a = a[:, :3]
        notes = [n for n in notes if not n.startswith("未选择 PWM 列")]
    elif a.shape[1] != 4:
        raise ValueError("实测 PWM 模式需要 CSV 开关状态列；三列 CSV 请改选已知控制器重建并确认电路配置。")
    lower = a[0, 0] if options.start_s is None else options.start_s
    upper = a[-1, 0] if options.end_s is None else options.end_s
    if upper <= lower or lower < a[0, 0] - 1e-12 or upper > a[-1, 0] + 1e-12:
        raise ValueError("辨识区间必须在 CSV 时间范围内，并且结束时间大于开始时间。")
    a = a[(a[:, 0] >= lower - 1e-14) & (a[:, 0] <= upper + 1e-14)]
    if len(a) < 100 or (a[-1, 0] - a[0, 0]) * options.frequency_hz < 15:
        raise ValueError("选定区间需要至少 100 个采样点和 15 个开关周期。")
    if (a[-1, 0] - a[0, 0]) * options.frequency_hz > 3000:
        raise ValueError("单次最多分析 3000 个开关周期，请缩小辨识区间。")
    split_time = a[0, 0] + options.train_fraction * (a[-1, 0] - a[0, 0])
    split = int(np.searchsorted(a[:, 0], split_time))
    train = a[:split + 1]
    valid = a[split:]
    if min(len(train), len(valid)) < 20:
        raise ValueError("训练或留出段采样不足，请调整区间。")
    if reconstructed:
        stage("pwm_reconstruction")
        train = reconstruct_training(train)
    stage("initialization")
    negative_tolerance = max(0.02 * np.ptp(train[:, 1]), 1e-5)
    if options.method in ("weighted_integral", "pann"):
        from .pann_adapter import measurement_noise_std
        negative_tolerance = max(negative_tolerance, 5 * measurement_noise_std(train)[0])
    if np.min(train[:, 1]) < -negative_tolerance or np.mean(train[:, 1]) < 0:
        raise ValueError("检测到明显负电流：当前模型是非同步 Buck，请检查信号方向或使用适配模型。")
    if options.method == "pann" and np.any(train[:, 1] < 0):
        notes.append("保留训练测量中的负向噪声；窗口初值联合估计时约束非负，训练目标不截断。局部噪声估计不能证明信号方向正确。")
    params, diagnostics = integral_fit(train, options) if options.method == "integral" else weak_integral_seed(train, options)
    initial_state = None
    if options.method == "weighted_integral":
        diagnostics["method"] = "抗噪加权积分辨识（端点零权重，2/5/10周期窗口，无PANN微调）"
        initial_state = np.maximum(a[0, 1:3], 0).tolist()
        notes.append("直接采用加权积分参数，不进行 PANN 微调。连续验证从首个测量状态的非负投影出发，后段不重置；波形误差仍可能受初始测量噪声影响。")
    if options.method == "pann":
        stage("pann_training")
        from .pann_adapter import refine
        params, training = refine(train, params, options)
        diagnostics["pann"] = training
        diagnostics["parameter_at_bound"] |= training.get("parameter_at_bound", False)
        diagnostics["method"] += " → PANN 波形损失微调"
        initial_state = training["rollout_initial_state_A_V"]
        if training["stop_reason"] == "time_budget" or training["best_at_budget_end"]:
            notes.append("PANN 达到训练预算或最佳轮次在预算末端，不能视为已经充分收敛；请比较不同窗口长度和训练轮数。")
        if options.pann_initial_state == "measured":
            notes.append("当前为固定含噪测量初值的对照模式；Case14/15 表明该设置可能放大电容辨识偏差。")
        else:
            notes.append("PANN 联合估计窗口初值与参数；连续验证仅采用首窗口同轮次估计初值，后续不重置到测量。")
    diagnostics["rollout_initial_state_A_V"] = list(initial_state) if initial_state is not None else a[0, 1:3].tolist()
    diagnostics["rollout_initial_state_source"] = "training-only joint window estimate" if options.method == "pann" and options.pann_initial_state == "joint" else "first observed sample"
    if options.method == "weighted_integral":
        diagnostics["rollout_initial_state_source"] = "nonnegative projection of first observed sample"
    stage("validation")
    if reconstructed:
        predicted = closed_loop_rollout(a[:, 0], params, options.vin,
                                       a[0, 1:3] if initial_state is None else initial_state)
        diagnostics["controller"] = dict(PROFILE)
        diagnostics["drive_source"] = "known controller reconstructed from training voltage only"
        diagnostics["controller_reconstruction_end_s"] = float(train[-1, 0])
        diagnostics["sampling_branch_included"] = True
        diagnostics["pwm_reconstruction"] = {
            "status": "complete", "source": "training_voltage_and_known_controller",
            "start_s": float(train[0, 0]), "end_s": float(train[-1, 0]),
            "dt_s": PROFILE["dt_s"], "samples": len(train),
            "rising_edges": int(np.count_nonzero(np.diff(train[:, 3]) > 0)),
            "falling_edges": int(np.count_nonzero(np.diff(train[:, 3]) < 0)),
            "uses_heldout_measurements": False,
        }
    else:
        predicted = rollout(a, params, options.vin, initial_state)
        diagnostics["drive_source"] = "measured PWM"
    metrics = metric(valid[:, 0], valid[:, 1:3], predicted[split:])
    train_metrics = metric(a[:split+1, 0], a[:split+1, 1:3], predicted[:split+1])
    if diagnostics["parameter_at_bound"]:
        notes.append("辨识或初始化触及参数边界：模型或数据可能不匹配，不应直接采用参数。")
    if diagnostics["CR_condition_number"] > 100:
        notes.append("C/R 相关性较高，参数可能对噪声敏感；建议增加动态激励并更换初值复测。")
    rises = np.flatnonzero(np.diff(a[:, 3]) > 0.5) + 1 if not reconstructed else []
    if len(rises) > 1:
        observed_fs = 1 / np.median(np.diff(a[rises, 0]))
        if abs(observed_fs / options.frequency_hz - 1) > 0.15:
            notes.append("实际 PWM 频率与设置相差超过 15%，请核对开关频率及时间单位。")
    if np.max(np.diff(a[:, 0])) > 0.15 / options.frequency_hz:
        notes.append("部分采样间隔超过开关周期的 15%；边沿时间不确定性可能影响 L 估计。")
    quality = "需复核" if (max(metrics["current_nrmse_pct"], metrics["voltage_nrmse_pct"]) > 5 or diagnostics["parameter_at_bound"] or diagnostics["CR_condition_number"] > 100) else "模型内拟合一致"
    if quality == "需复核":
        notes.append("留出误差或可辨识性检查未通过经验阈值；检查负载突变、PWM 对齐、初始状态及模型遗漏。")
    if options.method == "pann" and (training["stop_reason"] == "time_budget" or training["best_at_budget_end"]):
        quality = "需复核"
    if reconstructed:
        notes.append("PWM 由已确认的控制电路和训练段电压重建；含噪电压、控制器初态或载波相位不匹配会影响结果。")
        notes.append("已计入反馈取样支路电流；忽略 ESR、绕组电阻、死区和器件压降。R 为用户提供的恒定负载电阻，未参与估计。")
    else:
        notes.append("本模型忽略反馈取样支路、ESR、绕组电阻、死区和器件压降；R 为选定区间的恒定等效负载电阻。")
    return {"parameters": {"L_H": float(params[0]), "C_F": float(params[1]), "R_ohm": float(params[2])},
            "R_estimated": options.mode == "LCR", "method": options.method, "quality": quality,
            "metrics": metrics, "training_metrics": train_metrics, "diagnostics": diagnostics,
            "options": options.model_dump(), "mapping": mapping.model_dump(),
            "train_start_s": float(a[0, 0]), "train_end_s": float(a[split, 0]), "validation_end_s": float(a[-1, 0]),
            "validation_kind": "同一记录后段留出；已知控制器自主闭环预测，不使用留出电压或实测 PWM 驱动，不是独立工况验证。" if reconstructed else "同一记录后段留出；从区间起点连续仿真，后段无测量状态重置，输入使用实测 PWM。不是自主闭环或独立工况验证。",
            "parameter_error": None, "parameter_error_note": "未提供真值，不能计算参数真实误差；波形误差不等于参数误差。",
            "warnings": list(dict.fromkeys(notes)), "chart": chart_data(a, predicted),
            "file_sha256": dataset.sha256, "filename": dataset.name,
            "elapsed_s": round(time.perf_counter() - started, 3)}
