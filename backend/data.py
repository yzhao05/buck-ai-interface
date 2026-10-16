"""CSV ingestion. Original adaptive time samples are retained for computation."""
import csv
import hashlib
import io
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .schemas import Mapping

MAX_BYTES = 32 * 1024 * 1024
MAX_ROWS = 350000


@dataclass
class Dataset:
    name: str
    frame: pd.DataFrame
    sha256: str


def read_csv(raw: bytes, name: str) -> Dataset:
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError("CSV 必须非空且小于 32 MB。")
    if b"\x00" in raw:
        raise ValueError("请导出 UTF-8 / GB18030 文本 CSV，不支持二进制或 UTF-16 文件。")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("gb18030")
    try:
        delimiter = csv.Sniffer().sniff(text[:8192], delimiters=",;\t").delimiter
    except csv.Error:
        delimiter = ","
    first = next(csv.reader(io.StringIO(text), delimiter=delimiter), [])
    if len(first) < 3 or len(first) > 32 or len(first) != len(set(first)):
        raise ValueError("首行需要 3～32 个不重复的列名（至少时间、电流、电压）。")
    frame = pd.read_csv(io.StringIO(text), sep=delimiter, nrows=MAX_ROWS + 1)
    if len(frame) > MAX_ROWS or len(frame) < 30:
        raise ValueError("需要 30～350000 行数据；请在 PLECS 中选择合适时间段导出。")
    return Dataset(name[:150], frame, hashlib.sha256(raw).hexdigest())


def suggest_mapping(dataset: Dataset) -> dict:
    cols = list(dataset.frame.columns)
    def find(words):
        return next((c for c in cols if any(w in c.lower() for w in words)), None)
    return {"time": find(["time", "时间"]) or cols[0],
            "current": find(["inductor current", "il", "i_l", "电感电流"]) or cols[1],
            "voltage": find(["capacitor voltage", "vout", "vo", "v_o", "电压"]) or cols[2],
            "pwm": find(["pwm", "gate", "relational", "开关"]),
            "time_unit": "s", "current_unit": "A", "voltage_unit": "V"}


def extract(dataset: Dataset, mapping: Mapping):
    columns = [mapping.time, mapping.current, mapping.voltage]
    if mapping.pwm:
        columns.append(mapping.pwm)
    if len(set(columns)) != len(columns):
        raise ValueError("时间、电流、电压、PWM 必须映射到不同列。")
    if any(c not in dataset.frame.columns for c in columns):
        raise ValueError("所选列不存在，请重新映射 CSV 列。")
    a = dataset.frame[columns].apply(pd.to_numeric, errors="coerce").to_numpy(float)
    if not np.isfinite(a).all():
        raise ValueError("所选列包含缺失、非数值或无穷值，请修正 CSV 后重试。")
    a[:, 0] *= {"s": 1, "ms": 1e-3, "us": 1e-6, "ns": 1e-9}[mapping.time_unit]
    a[:, 1] *= {"A": 1, "mA": 1e-3}[mapping.current_unit]
    a[:, 2] *= {"V": 1, "mV": 1e-3}[mapping.voltage_unit]
    dt = np.diff(a[:, 0])
    if (dt < 0).any():
        raise ValueError("时间必须单调递增；检测到倒序或拼接的多条轨迹，请分开上传。")
    notes = []
    duplicates = int((dt == 0).sum())
    if duplicates:
        keep = np.r_[dt != 0, True]  # PLECS event convention: retain post-event sample.
        a = a[keep]
        notes.append(f"合并 {duplicates} 个重复时间点，保留事件后的最后一行。")
    if len(a) < 30 or a[-1, 0] <= a[0, 0]:
        raise ValueError("有效时间点不足。")
    if mapping.pwm:
        if np.max(np.abs(a[:, 3] - np.round(a[:, 3]))) > 1e-5 or a[:, 3].min() < -1e-5 or a[:, 3].max() > 1.00001:
            raise ValueError("PWM 列必须为 0/1 开关状态；占空比或模拟控制电压不能直接替代。")
        a[:, 3] = np.round(a[:, 3])
    dt = np.diff(a[:, 0])
    if np.max(dt) > 1.05 * np.min(dt):
        notes.append("非均匀时间采样：统计使用时间加权；积分法保留原时间轴，PWM 按前值保持。")
    if not mapping.pwm:
        notes.append("未选择 PWM 列：可分析波形；固定 R 的 L/C 辨识可选择已知控制器重建，需确认电路配置。")
    return a, notes


def weighted_stats(t, y):
    duration = t[-1] - t[0]
    mean = float(np.trapezoid(y, t) / duration)
    return {"mean": mean, "rms": float(np.sqrt(np.trapezoid(y * y, t) / duration)),
            "min": float(y.min()), "max": float(y.max()), "peak_to_peak": float(np.ptp(y))}


def chart_data(a, predicted=None, limit=2400):
    # Min/max envelope for analog traces; preserve representative transitions.
    if len(a) <= limit:
        idx = np.arange(len(a))
    else:
        selected = {0, len(a) - 1}
        for group in np.array_split(np.arange(len(a)), limit // 6):
            for col in (1, 2):
                selected.update([int(group[np.argmin(a[group, col])]), int(group[np.argmax(a[group, col])])])
            selected.add(int(group[0]))
            selected.add(int(group[-1]))
        idx = np.array(sorted(selected))
    result = {"time_s": a[idx, 0].tolist(), "current_A": a[idx, 1].tolist(),
              "voltage_V": a[idx, 2].tolist(), "display_points": len(idx),
              "original_points": len(a), "decimated": len(idx) < len(a)}
    if a.shape[1] == 4:
        result["pwm"] = a[idx, 3].tolist()
    if predicted is not None:
        result["predicted_current_A"] = predicted[idx, 0].tolist()
        result["predicted_voltage_V"] = predicted[idx, 1].tolist()
    return result


def analyze(dataset: Dataset, mapping: Mapping):
    a, notes = extract(dataset, mapping)
    t = a[:, 0]
    tail = a[t >= t[0] + 0.8 * (t[-1] - t[0])]
    result = {"filename": dataset.name, "sha256": dataset.sha256, "rows": len(a),
              "start_s": float(t[0]), "end_s": float(t[-1]), "duration_s": float(t[-1] - t[0]),
              "dt_min_s": float(np.diff(t).min()), "dt_max_s": float(np.diff(t).max()),
              "current": weighted_stats(t, a[:, 1]), "voltage": weighted_stats(t, a[:, 2]),
              "tail_current": weighted_stats(tail[:, 0], tail[:, 1]) if len(tail) > 1 else None,
              "tail_voltage": weighted_stats(tail[:, 0], tail[:, 2]) if len(tail) > 1 else None,
              "tail_note": "最后 20% 时间段的统计，未自动认定为稳态。", "warnings": notes,
              "chart": chart_data(a)}
    if a.shape[1] == 4:
        rises = t[1:][np.diff(a[:, 3]) > 0.5]
        result["pwm"] = {"rising_edges": len(rises),
                         "frequency_hz": float(1 / np.median(np.diff(rises))) if len(rises) > 1 else None,
                         "mean_duty": float(np.sum(a[:-1, 3] * np.diff(t)) / (t[-1] - t[0]))}
    return result
