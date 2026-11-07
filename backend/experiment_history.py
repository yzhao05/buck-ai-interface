"""Conversation result summaries and on-demand reads of the right-hand list."""
import math
import re
import json
import hashlib
import statistics
from .schemas import ChatExperiment
from .parameter_comparison import with_ground_truth


READ_RESULT_TOOL = {'type':'function','function':{
    'name':'read_experiment_results',
    'description':'按右侧结果列表中的精确 ID 读取一条或多条已保存结果，用于分析或比较配置、参数、误差和诊断；只读，不切换界面或重新辨识。',
    'parameters':{'type':'object','properties':{'result_ids':{
        'type':'array','items':{'type':'string'},'minItems':1,'maxItems':200,'uniqueItems':True,
        'description':'从 get_experiment_history 返回的 result_catalog 选择精确结果 ID。'}},
        'required':['result_ids'],'additionalProperties':False}}}


class ResultLibrary:
    def __init__(self, runs, selected_id=None):
        # Preserve the exact UI order, including equal-time records.
        self.runs = list(runs)
        self.by_id = {run.id:run for run in self.runs}
        self.selected_id = selected_id

    def catalog(self):
        return [{'id':run.id,'position':index+1,'display_name':run.display_name or run.dataset.filename,
                 'created':run.created,'display_time':run.display_time,'updated':run.updated,'dataset':run.dataset.model_dump(),
                 'method':run.result.get('method') or run.result.get('options',{}).get('method'),
                 'pwm_source':run.result.get('options',{}).get('pwm_source'),
                 'mode':run.result.get('options',{}).get('mode'),
                 'selected':run.id==self.selected_id} for index,run in enumerate(self.runs)]

    def summaries(self):
        return summarize_experiments([ChatExperiment.model_validate({
            'id':run.id,'dataset':run.dataset.model_dump(),'parameters':run.result['parameters'],
            'method':run.result.get('method') or run.result.get('options',{}).get('method'),
            'pwm_source':run.result.get('options',{}).get('pwm_source'),
            'ground_truth':run.ground_truth.model_dump() if run.ground_truth else None,
        }) for run in self.runs])

    def read(self, arguments):
        ids = arguments.get('result_ids')
        if (set(arguments)!={'result_ids'} or not isinstance(ids,list) or not 1<=len(ids)<=200
            or any(not isinstance(identifier,str) for identifier in ids) or len(ids)!=len(set(ids))):
            raise ValueError('Provide unique result_ids from the result catalog.')
        missing = [identifier for identifier in ids if identifier not in self.by_id]
        if missing:
            raise ValueError('Result IDs are absent from this conversation result list: '+', '.join(missing))
        # No fuzzy matching, filename deduplication, or fallback to the current card.
        def snapshot(value):
            if isinstance(value,dict):
                return {key:snapshot(item) for key,item in value.items() if key not in {'chart','epoch_history','history'}}
            if isinstance(value,list):
                return [snapshot(item) for item in value]
            return value
        records=[]
        for identifier in ids:
            run=self.by_id[identifier]
            result=snapshot(run.result)
            # Legacy snapshots may lack metrics; keep that absence explicit.
            had_metrics='metrics' in result
            result=with_ground_truth({**result,'metrics':result.get('metrics',{})},run.ground_truth)
            if not had_metrics:
                result.pop('metrics',None)
            records.append({'id':identifier,'display_name':run.display_name or run.dataset.filename,
                            'created':run.created,'dataset':run.dataset.model_dump(),
                            'result':{**result,'result_id':identifier,'result_source':'saved_conversation_snapshot'}})
        return {'count':len(records),'experiments':records,
                'source':'right_hand_result_list','missing_material':'Raw CSV, full curves and epoch traces are not included; absent fields are unknown.'}


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
