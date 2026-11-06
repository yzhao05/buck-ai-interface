"""Only the explicitly approved per-experiment comparison fields leave the backend."""
import math
import re
import json
import hashlib
import statistics


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
