"""Only the explicitly approved per-experiment comparison fields leave the backend."""
import math
import re


def comparison_requested(message):
    return bool(re.search(r"对比|比较|横向|纵向|\b(?:compare|comparison|versus)\b", message, re.I))


def summarize_experiments(experiments):
    records = []
    for run in experiments:
        record = {"id": run.id, "dataset": run.dataset.model_dump(),
                  "method": run.method, "pwm_source": run.pwm_source,
                  "parameters": run.parameters.model_dump(), "parameter_comparison": None}
        if run.ground_truth:
            truth = {"L_H": run.ground_truth.L_H, "C_F": run.ground_truth.C_F}
            errors = {key: abs(record["parameters"][unit]/truth[unit]-1)*100
                      for key, unit in (("L", "L_H"), ("C", "C_F"))}
            if all(math.isfinite(v) for v in errors.values()):
                record["parameter_comparison"] = {"ground_truth": truth, "relative_error_pct": errors, "source": "user_provided"}
        records.append(record)
    return records


def local_comparison(records):
    lines = [f"本会话已保存 {len(records)} 次实验：", "",
             "| 实验 | CSV | 方法 | PWM | 预测 L (μH) | 预测 C (μF) | L / C 相对误差 |",
             "|---|---|---|---|---:|---:|---|"]
    for index, record in enumerate(records, 1):
        p = record["parameters"]
        method = {"pann": "PANN", "weighted_integral": "抗噪加权积分", "integral": "积分"}.get(record["method"], "未记录")
        pwm = {"measured": "实测", "controller": "重建"}.get(record["pwm_source"], "未记录")
        comparison = record["parameter_comparison"]
        error = "未计算" if not comparison else " / ".join(f"{comparison['relative_error_pct'][k]:.6g}%" for k in ("L", "C"))
        filename = record["dataset"]["filename"].replace("|", "&#124;").replace("\n", " ").replace("\r", " ")
        lines.append(f"| {index} | {filename} | {method} | {pwm} | {p['L_H']*1e6:.7g} | {p['C_F']*1e6:.7g} | {error} |")
    if len({r["dataset"]["sha256"] for r in records}) > 1:
        lines.extend(["", "这些实验使用了不同 CSV，比较方法与 PWM 来源时需要同时考虑数据差异。"])
    lines.extend(["", "以上为已保存结果的本地汇总，没有重新运行辨识。"])
    return "\n".join(lines)
