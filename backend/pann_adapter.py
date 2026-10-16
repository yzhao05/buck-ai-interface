"""Adapt the uploaded PE-GPT PANN to this application's units and fixed Vin.

Only training samples are used. PWM uses ZOH. Window states are jointly
estimated from noisy data (Case15/16 principle), with measured-state ablation.
The optional fifth training column supplies reconstructed sensing current;
controller reconstruction and autonomous validation live in controller.py.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import time

import numpy as np


def available():
    return importlib.util.find_spec("torch") is not None or bool(os.getenv("PANN_PYTHON") and Path(os.environ["PANN_PYTHON"]).is_file())


def measurement_noise_std(train):
    """Robust local residual estimate, excluding gate edges; not a noise guarantee."""
    t = train[:, 0]
    weight = (t[1:-1] - t[:-2]) / (t[2:] - t[:-2])
    residual = train[1:-1, 1:3] - ((1 - weight[:, None]) * train[:-2, 1:3] + weight[:, None] * train[2:, 1:3])
    residual /= np.sqrt(1 + weight[:, None] ** 2 + (1 - weight[:, None]) ** 2)
    same_gate = (train[:-2, 3] == train[1:-1, 3]) & (train[1:-1, 3] == train[2:, 3])
    residual = residual[same_gate]
    if len(residual) < 20:
        return np.zeros(2)
    return np.median(np.abs(residual - np.median(residual, axis=0)), axis=0) / .67448975


def training_windows(train, frequency_hz, cycles=1):
    """Bounded, disjoint windows spread across the FULL training interval.

    Interpolate only each selected window: a long trace must not be silently
    truncated to its first 50,000 samples. No held-out measurements are accepted.
    """
    if train.ndim != 2 or train.shape[1] not in (4, 5) or len(train) < 3 or not np.isfinite(train).all() or np.any(np.diff(train[:, 0]) <= 0):
        raise ValueError("PANN 训练数据必须包含递增时间、电流、电压和 PWM。")
    if not np.isin(train[:, 3], (0, 1)).all():
        raise ValueError("PANN 输入需要实测或重建的 0/1 PWM，不能直接使用开关节点电压。")
    samples_per_cycle = 500 if train.shape[1] == 5 else 200
    dt = 1 / (frequency_hz * samples_per_cycle)
    steps = cycles * samples_per_cycle
    duration = steps * dt
    span = train[-1, 0] - train[0, 0]
    count = min(40, int(np.floor(span / duration + 1e-10)))
    if count < 4:
        raise ValueError("PANN 训练窗口不足，请扩大训练区间或减小窗口长度。")
    starts = np.linspace(train[0, 0], train[-1, 0] - duration, count)
    grid = starts[:, None] + np.arange(steps + 1) * dt
    grid = np.clip(grid, train[0, 0], train[-1, 0])
    measured = np.stack([np.interp(grid, train[:, 0], train[:, c]) for c in (1, 2)], axis=-1)
    gate = train[np.clip(np.searchsorted(train[:, 0], grid[:, :-1], side="right") - 1, 0, len(train) - 1), 3]
    inputs = gate[:, :, None]
    if train.shape[1] == 5:
        sense = np.interp(grid[:, :-1], train[:, 0], train[:, 4])
        inputs = np.concatenate((inputs, sense[:, :, None]), axis=-1)
    return inputs, measured[:, 1:], measured[:, 0], starts, dt


def select_window_cycles(train, frequency_hz, requested):
    """Training-only heuristic; configurable comparison, not an optimality claim.

    Increase temporal information when estimated noise is >=0.5% of channel
    RMS. Limit length to leave at least four disjoint windows in short records.
    """
    sigma = measurement_noise_std(train)
    rms = np.sqrt(np.trapezoid(train[:, 1:3]**2, train[:, 0], axis=0) / (train[-1, 0]-train[0, 0]))
    ratio = sigma / np.maximum(rms, 1e-12)
    if requested:
        return requested, "manual", ratio
    desired = 3 if np.max(ratio) >= .005 else 1
    supported = max(1, int(np.floor((train[-1, 0]-train[0, 0])*frequency_hz/4 + 1e-10)))
    return min(desired, supported), "training noise >=0.5% RMS: prefer 3 cycles; otherwise 1; preserve >=4 windows", ratio


def refine(train, initial, options):
    # Reuse the user's existing Python/PyTorch environment without modifying it.
    # The executable is a server-side setting and cannot be chosen by the LLM.
    if importlib.util.find_spec("torch") is None and os.getenv("PANN_PYTHON"):
        executable = Path(os.environ["PANN_PYTHON"])
        if not executable.is_file():
            raise ValueError("PANN_PYTHON 指向的解释器不存在，请检查后端配置。")
        payload = {"train": train.tolist(), "initial": initial.tolist(),
                   "options": {k: getattr(options, k) for k in ("vin", "frequency_hz", "mode", "epochs", "pann_initial_state", "pann_window_cycles")}}
        environment = {k: v for k, v in os.environ.items() if k not in ("DEEPSEEK_API_KEY", "APP_ACCESS_TOKEN")}
        environment["PYTHONIOENCODING"] = "utf-8"
        try:
            response = subprocess.run([str(executable), "-m", "backend.pann_worker"],
                input=json.dumps(payload), text=True, encoding="utf-8", capture_output=True,
                cwd=Path(__file__).resolve().parents[1], env=environment, timeout=180,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except subprocess.TimeoutExpired as exc:
            raise ValueError("PANN 计算超过 180 秒，已停止；请选择更短区间或积分辨识。") from exc
        if response.returncode:
            raise ValueError("现有 PANN 环境运行失败，请确认该环境可导入 NumPy 和 PyTorch。")
        try:
            result = json.loads(response.stdout)
            return np.asarray(result["parameters"]), result["diagnostics"]
        except (ValueError, KeyError) as exc:
            raise ValueError("PANN 环境返回的结果格式异常。") from exc
    try:
        import torch
        from torch import nn
        from .vendor.pann_net import PANN, EulerCell_Buck
    except ImportError as exc:
        raise ValueError("此后端未安装 PyTorch。请选择积分辨识，或安装 requirements-pann.txt 后重启。") from exc
    # Bound CPU/memory and eliminate float32 rounding in upstream constructors.
    torch.set_num_threads(2)
    cycles, window_rule, noise_ratio = select_window_cycles(train, options.frequency_hz, options.pann_window_cycles)
    gate, measured, observed, starts, dt = training_windows(train, options.frequency_hz, cycles)
    u = torch.tensor(gate, dtype=torch.float64)
    target = torch.tensor(measured, dtype=torch.float64)
    observed_start = torch.tensor(observed, dtype=torch.float64)
    scale = target.reshape(-1, 2).std(dim=0).clamp_min(1e-3)
    joint = options.pann_initial_state == "joint"
    normalized_start = nn.Parameter((observed_start / scale).clamp_min(0), requires_grad=joint)
    cell = EulerCell_Buck(options.vin, dt, *initial).double()
    with torch.no_grad():
        for attr, val in zip(("Vin", "L", "Co", "Ro"), (options.vin, *initial)):
            getattr(cell, attr).fill_(float(val))
    cell.Vin.requires_grad_(False)
    cell.Ro.requires_grad_(options.mode == "LCR")
    class Positive(nn.Module):
        def forward(self, x):
            return torch.exp(x)
        def right_inverse(self, x):
            return torch.log(x)
    for attr in ("L", "Co", "Ro"):
        if getattr(cell, attr).requires_grad:
            nn.utils.parametrize.register_parametrization(cell, attr, Positive())
    class SensingCell(nn.Module):
        def __init__(self, base):
            super().__init__()
            self.base = base

        def forward(self, inputs, states):
            predicted = self.base(inputs[:, :1], states)
            return torch.stack((predicted[:, 0], predicted[:, 1]-dt/self.base.Co*inputs[:, 1]), dim=1)

    model = PANN(SensingCell(cell) if train.shape[1] == 5 else cell)
    parameters = [p for p in model.parameters() if p.requires_grad]
    groups = [{"params": parameters, "lr": 0.03}]
    if joint:
        groups.append({"params": [normalized_start], "lr": 0.05})
    # Settle noisy window states before allowing their transient mismatch to
    # move the shared component parameters. Start a fresh parameter optimizer
    # afterwards: zero LR alone would still accumulate biased Adam moments.
    warmup = 50 if joint else 0
    optimizer = torch.optim.Adam([normalized_start], lr=.05) if joint else torch.optim.Adam(groups)
    best_loss = float("inf")
    best = initial.copy()
    best_states = observed.copy()
    best_epoch = 0
    best_step = 0
    history = []
    began = time.monotonic()
    stop_reason = "epoch_budget"
    for step in range(warmup + options.epochs + 1):
        epoch = max(0, step-warmup)
        if joint and step == warmup:
            optimizer = torch.optim.Adam(groups)
        model.zero_grad(set_to_none=True)
        optimizer.zero_grad()
        x0 = normalized_start * scale if joint else observed_start
        prediction = model(u.clone(), x0[:, None, :])
        loss = (((prediction - target) / scale) ** 2).mean()
        if not torch.isfinite(loss):
            raise ValueError("PANN 训练发散，请检查数据或先使用积分辨识。")
        values = np.array([getattr(cell, name).item() for name in ("L", "Co", "Ro")])
        if loss.item() < best_loss:
            best_loss, best, best_epoch = loss.item(), values.copy(), epoch
            best_step = step
            best_states = x0.detach().numpy().copy()
        if step % 10 == 0 or step == warmup + options.epochs:
            history.append({"step": step, "phase": "state_warmup" if step < warmup else "joint" if joint else "measured",
                            "epoch": epoch, "loss": loss.item(), "L_H": values[0], "C_F": values[1], "R_ohm": values[2]})
        if time.monotonic() - began > 120:
            stop_reason = "time_budget"
            break
        if step < warmup + options.epochs:
            loss.backward()
            trained = [normalized_start] if step < warmup else parameters + ([normalized_start] if joint else [])
            if any(p.grad is None or not torch.isfinite(p.grad).all() for p in trained):
                raise ValueError("PANN 梯度异常，请检查数据、区间或缩短窗口。")
            torch.nn.utils.clip_grad_norm_(trained, 5)
            optimizer.step()
            with torch.no_grad():
                if joint:
                    normalized_start.clamp_(min=0)
                for name, bounds in (("L", (1e-7, .1)), ("Co", (1e-8, 1.)), ("Ro", (.01, 1e5))):
                    if nn.utils.parametrize.is_parametrized(cell, name):
                        getattr(cell.parametrizations, name).original.clamp_(np.log(bounds[0]), np.log(bounds[1]))
    bounds = np.array([[1e-7, .1], [1e-8, 1.], [.01, 1e5]])
    active = best if options.mode == "LCR" else best[:2]
    at_bound = bool(np.any(np.isclose(active, bounds[:len(active), 0], rtol=1e-5, atol=0)) or
                    np.any(np.isclose(active, bounds[:len(active), 1], rtol=1e-5, atol=0)))
    return best, {"epochs": options.epochs, "completed_epochs": epoch, "best_epoch": best_epoch,
                  "state_warmup_epochs": min(step, warmup), "best_step": best_step,
                  "parameter_at_bound": at_bound,
                  "stop_reason": stop_reason, "best_at_budget_end": best_step == step,
                  "best_training_loss": best_loss, "history": history, "initial_parameters": initial.tolist(),
                  "dt_s": dt, "windows": len(starts), "window_cycles": cycles,
                  "requested_window_cycles": options.pann_window_cycles, "window_selection_rule": window_rule,
                  "noise_to_rms_pct_A_V": (100*noise_ratio).tolist(),
                  "window_start_times_s": starts.tolist(), "training_end_s": float(train[-1, 0]),
                  "initialization": "training-only endpoint-zero weighted integral estimator", "initial_state_mode": options.pann_initial_state,
                  "estimated_state_scalars": best_states.size if joint else 0,
                  "initial_states_A_V": best_states.tolist(), "observed_initial_states_A_V": observed.tolist(),
                  "rollout_initial_state_A_V": best_states[0].tolist(),
                  "state_correction_rms_A_V": np.sqrt(np.mean((best_states - observed) ** 2, axis=0)).tolist(),
                  "noise_std_estimate_A_V": measurement_noise_std(train).tolist(),
                  "selection": "minimum noisy training loss; parameters and window states from the same epoch",
                  "note": "窗口状态仅从训练数据估计；不使用干净初值、参数真值或留出段选轮次。训练损失低不保证参数准确。" + ("当前训练输入为控制器重建 PWM 和取样支路电流，验证使用已知控制器自主闭环模型。" if train.shape[1] == 5 else "当前仍是简化两状态模型与实测 PWM 验证，不是原完整闭环实验的复现。")}
