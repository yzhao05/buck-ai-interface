"""Only the explicitly approved per-experiment comparison fields leave the backend."""
import math
import re
import json
import hashlib
import statistics


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


def repeatability_summary(experiments, current_id=None):
    """Descriptive spread, not confidence intervals or independent validation.

    Match all settings, mappings and actual intervals. Filenames cannot establish
    identical hardware. Collapse fits to the same SHA to one representative.
    """
    groups, seen_ids = {}, set()
    missing, selected_key = 0, None
    for run in experiments:
        if run.id in seen_ids:
            continue
        seen_ids.add(run.id)
        ctx = run.comparison_context
        if (not ctx or not re.fullmatch(r"[a-fA-F0-9]{64}", run.dataset.sha256)
                or run.method != ctx.options.method or run.pwm_source != ctx.options.pwm_source
                or (ctx.options.mode == "LC" and not math.isclose(run.parameters.R_ohm, ctx.options.resistance_ohm, rel_tol=1e-9))):
            missing += 1
            continue
        key = json.dumps(ctx.model_dump(), sort_keys=True)
        if run.id == current_id:
            selected_key = key
        groups.setdefault(key, []).append(run)
    output = []
    selected = [(key, group) for key, group in groups.items() if current_id is None or key == selected_key]
    for key, runs in selected[:8]:
        unique = {run.dataset.sha256.lower(): run for run in runs}
        current = next((r for r in runs if r.id == current_id), None)
        if current:
            unique[current.dataset.sha256.lower()] = current
        rows, parameters = list(unique.values()), {}
        if len(rows) >= 2:
            keys = ("L_H", "C_F", "R_ohm") if runs[0].comparison_context.options.mode == "LCR" else ("L_H", "C_F")
            for name in keys:
                values = [getattr(r.parameters, name) for r in rows]
                mean, sd = statistics.mean(values), statistics.stdev(values)
                parameters[name] = {"mean": mean, "sample_std": sd, "min": min(values), "max": max(values),
                                    "cv_pct": 100*sd/mean, "span_pct": 100*(max(values)-min(values))/mean}
        output.append({"group_id": hashlib.sha256(key.encode()).hexdigest()[:12],
                       "method": runs[0].method, "pwm_source": runs[0].pwm_source,
                       "mode": runs[0].comparison_context.options.mode,
                       "runs": len(runs), "distinct_files": len(rows), "same_file_reruns_excluded": len(runs)-len(rows),
                       "representative_ids": [r.id for r in rows], "parameters": parameters,
                       "status": "descriptive_only" if len(rows) >= 2 else "insufficient_distinct_files"})
    return {"groups": output, "excluded_missing_context": missing, "omitted_groups": max(0, len(selected)-len(output)),
            "scope": "selected_result_configuration" if current_id else "matching_declared_configurations",
            "independent_acquisitions_confirmed": False, "parameter_confidence_interval": None,
            "new_record_prediction_tested": False,
            "interpretation": "Observed spread across distinct CSV files with matching settings, mapping and interval. Confirm same hardware and independent repeated acquisition before calling this repeatability. Separate refits do not test one parameter set on new data. No confidence interval or accuracy claim."}


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
