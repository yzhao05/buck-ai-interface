"""Adapt the uploaded PE-GPT PANN to this application's units and fixed Vin.

Only training samples are used. Uniform resampling is explicit, PWM uses ZOH,
and window initial states come from training measurements. This is a new
adapter, NOT a reproduction of the full Case15/16 controller-aware method.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess

import numpy as np


def available():
    return importlib.util.find_spec("torch") is not None or bool(os.getenv("PANN_PYTHON") and Path(os.environ["PANN_PYTHON"]).is_file())


def refine(train, initial, options):
    # Reuse the user's existing Python/PyTorch environment without modifying it.
    # The executable is a server-side setting and cannot be chosen by the LLM.
    if importlib.util.find_spec("torch") is None and os.getenv("PANN_PYTHON"):
        executable = Path(os.environ["PANN_PYTHON"])
        if not executable.is_file():
            raise ValueError("PANN_PYTHON 指向的解释器不存在，请检查后端配置。")
        payload = {"train": train.tolist(), "initial": initial.tolist(),
                   "options": {k: getattr(options, k) for k in ("vin", "frequency_hz", "mode", "epochs")}}
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
    dt = 1 / (options.frequency_hz * 100)
    count = min(50000, int((train[-1, 0] - train[0, 0]) / dt) + 1)
    grid = train[0, 0] + np.arange(count) * dt
    measured = np.column_stack([np.interp(grid, train[:, 0], train[:, c]) for c in (1, 2)])
    gate = train[np.clip(np.searchsorted(train[:, 0], grid, side="right") - 1, 0, len(train) - 1), 3]
    steps = 100
    starts = np.unique(np.linspace(0, len(grid) - steps - 1, min(36, len(grid) // steps), dtype=int))
    if len(starts) < 4:
        raise ValueError("PANN 训练窗口不足，请扩大训练区间。")
    u = torch.tensor(np.stack([gate[s:s + steps] for s in starts])[:, :, None], dtype=torch.float64)
    target = torch.tensor(np.stack([measured[s + 1:s + steps + 1] for s in starts]), dtype=torch.float64)
    x0 = torch.tensor(measured[starts, None, :], dtype=torch.float64)
    scale = target.reshape(-1, 2).std(dim=0).clamp_min(1e-3)
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
    model = PANN(cell)
    optimizer = torch.optim.Adam([p for p in model.parameters() if p.requires_grad], lr=0.008)
    best_loss = float("inf")
    best = initial.copy()
    history = []
    for epoch in range(options.epochs + 1):
        optimizer.zero_grad()
        prediction = model(u.clone(), x0)
        loss = (((prediction - target) / scale) ** 2).mean()
        if not torch.isfinite(loss):
            raise ValueError("PANN 训练发散，请检查数据或先使用积分辨识。")
        values = np.array([getattr(cell, name).item() for name in ("L", "Co", "Ro")])
        if loss.item() < best_loss:
            best_loss, best = loss.item(), values
        if epoch % 10 == 0 or epoch == options.epochs:
            history.append({"epoch": epoch, "loss": loss.item(), "L_H": values[0], "C_F": values[1], "R_ohm": values[2]})
        if epoch < options.epochs:
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 5)
            optimizer.step()
    return best, {"epochs": options.epochs, "best_training_loss": best_loss, "history": history,
                  "dt_s": dt, "windows": len(starts), "initialization": "training-only integral estimator",
                  "note": "上游 PANN 微调；固定输入电压，正值参数，训练窗口初值取自测量。未复现 Case15/16 的反馈支路及联合初值估计。"}
