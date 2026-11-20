"""Bounded, deterministic parameter/state ablations. No training or generated code."""
import numpy as np

from .data import extract
from .schemas import FitOptions, Mapping
from .identification import rollout, metric
from .controller import closed_loop_rollout, profile_for, validate_profile
from .parasitics import resistances, JOINT_MODE, PARAMETER_KEYS, estimated_resistances


def comparison_options(raw):
    """Legacy results without parasitic fields represent the ideal circuit."""
    result = {k: v for k, v in raw.items() if k not in
              {'method', 'epochs', 'pann_initial_state', 'pann_window_cycles'}}
    enabled = result.setdefault('include_parasitics', False)
    fixed_zero = result.pop('joint_zero_is_fixed', False)
    if result.get('mode') != 'FULL_STAGE':
        for field in ('inductance_h', 'winding_capacitance_f', 'capacitance_f', 'capacitor_esl_h', 'switch_resistance_ohm'):
            result.pop(field, None)
    for key in ('inductor_resistance_ohm', 'capacitor_esr_ohm'):
        result[key] = result.get(key) if enabled else None
        if result.get('mode') == JOINT_MODE and not fixed_zero and result[key] == 0:
            result[key] = None
    return result


def compatible(current, candidate):
    if candidate.get('method') != 'pann' or current.get('file_sha256') != candidate.get('file_sha256'):
        return False
    return (current.get('mapping') == candidate.get('mapping') and
            all(current.get(k) == candidate.get(k) for k in ('train_start_s', 'train_end_s', 'validation_end_s')) and
            comparison_options(current.get('options', {})) == comparison_options(candidate.get('options', {})))


def run_controls(data, source, *, result_id, source_id, reference=None):
    if data.sha256 != source.get('file_sha256'):
        raise ValueError('对照 CSV 与实验校验值不一致，未执行。')
    options = FitOptions.model_validate(source['options'])
    mapping = Mapping.model_validate(source['mapping'])
    if source.get('method') != 'pann':
        raise ValueError('缺少匹配的已完成 PANN 结果，不能进行参数与初值交换。')
    pann = source.get('diagnostics', {}).get('pann', {})
    seed = np.asarray(pann.get('initial_parameters', []), dtype=float)
    joint = options.mode == JOINT_MODE
    keys = PARAMETER_KEYS if joint else PARAMETER_KEYS[:3]
    parameters = np.array([source['parameters'].get(k, np.nan) for k in keys], dtype=float)
    state = np.asarray(pann.get('rollout_initial_state_A_V', []), dtype=float)
    if (seed.shape != (len(keys),) or state.shape != (2,) or not np.isfinite(seed).all()
            or not np.isfinite(parameters).all() or not np.isfinite(state).all()
            or min(seed[:3]) <= 0 or min(parameters[:3]) <= 0 or min(state) < 0):
        raise ValueError('历史结果缺少有效的积分参数或 PANN 初始状态，未执行交换对照。')
    # Apply the same physical bounds used by the fitter before accepting a snapshot.
    if not (1e-7 <= seed[0] <= .1 and 1e-8 <= seed[1] <= 1 and .01 <= seed[2] <= 1e5
            and 1e-7 <= parameters[0] <= .1 and 1e-8 <= parameters[1] <= 1 and .01 <= parameters[2] <= 1e5):
        raise ValueError('对照参数超出支持范围。')
    if options.mode != 'LCR' and not np.allclose([seed[2], parameters[2]], options.resistance_ohm, rtol=1e-9):
        raise ValueError('固定 R 与结果不一致，未执行对照。')
    if joint:
        if np.any(seed[3:] < 0) or np.any(parameters[3:] < 0) or np.any(seed[3:] > 1000) or np.any(parameters[3:] > 1000):
            raise ValueError('对照参数超出支持范围。')
        for index, (estimated, value) in enumerate(zip(estimated_resistances(options), resistances(options)), 3):
            if not estimated and not np.allclose([seed[index], parameters[index]], value, rtol=1e-9, atol=1e-12):
                raise ValueError('Fixed parasitic resistance does not match the saved result.')
    a, _ = extract(data, mapping)
    start, end = source['train_start_s'], source['validation_end_s']
    a = a[(a[:,0] >= start-1e-14) & (a[:,0] <= end+1e-14)]
    if (len(a) < 100 or len(a) > 500000 or (end-start)*options.frequency_hz > 3000
            or not np.isclose(a[0,0], start, atol=1e-12, rtol=0)
            or not np.isclose(a[-1,0], end, atol=1e-12, rtol=0)):
        raise ValueError('对照数据区间不完整或超出支持规模。')
    split = int(np.searchsorted(a[:,0], source['train_end_s']))
    if split < 1 or len(a)-split < 20 or not np.isclose(a[split,0],source['train_end_s'],atol=1e-12,rtol=0):
        raise ValueError('训练与留出分界不匹配。')
    controller = options.pwm_source == 'controller'
    if controller:
        validate_profile(a, options)
        profile = profile_for(options)
    elif a.shape[1] != 4:
        raise ValueError('缺少实测 PWM，无法重放当前模型。')
    clean = None
    reference_status = {'status':'not_provided', 'reason':'未提供并确认干净参考；交换对照仍可使用含噪测量评价。'}
    if reference is not None:
        try:
            if reference.sha256 == data.sha256:
                raise ValueError('参考与待分析 CSV 完全相同，不能视为独立干净参考。')
            ref, _ = extract(reference, mapping)
            ref = ref[(ref[:,0]>=start-1e-14)&(ref[:,0]<=end+1e-14)]
            if len(ref)!=len(a) or not np.allclose(ref[:,0],a[:,0],rtol=0,atol=1e-12):
                raise ValueError('参考与原数据时间网格不一致；未插值或自动对齐。')
            if a.shape[1]==4 and not np.array_equal(ref[:,3],a[:,3]):
                raise ValueError('参考与原数据 PWM 不一致，不能作为同一轨迹的干净参考。')
            clean = ref[:,1:3]
            reference_status = {'status':'evaluated_user_declared_reference', 'filename':reference.name,
                'sha256':reference.sha256, 'cleanliness_independently_verified':False,
                'used_for_training_or_initial_state':False}
        except ValueError as exc:
            reference_status = {'status':'rejected', 'reason':str(exc)}
    measured_state = np.maximum(a[0,1:3],0)
    cases = {}
    for name, p, x in [('integral_measured',seed,measured_state), ('integral_pann_state',seed,state),
                       ('pann_measured',parameters,measured_state), ('pann_pann_state',parameters,state)]:
        rl, rc = p[3:5] if joint else resistances(options)
        case_profile = {**profile, "inductor_resistance_ohm": float(rl), "capacitor_esr_ohm": float(rc)} if controller else None
        predicted = (closed_loop_rollout(a[:,0],p[:3],options.vin,x,case_profile) if controller else
                     rollout(a,p[:3],options.vin,x,series_resistances=(rl,rc)))
        if not np.isfinite(predicted).all():
            raise ValueError('对照前向预测产生非有限值，未报告结果。')
        cases[name] = {'parameters':dict(zip(keys,map(float,p))),
            'initial_state_A_V':x.tolist(), 'heldout_measured':metric(a[split:,0],a[split:,1:3],predicted[split:]),
            'heldout_reference':metric(a[split:,0],clean[split:],predicted[split:]) if clean is not None else None}
    effects = []
    for factor, baseline, alternative in [('initial_state_at_integral','integral_measured','integral_pann_state'),
            ('parameters_at_measured_state','integral_measured','pann_measured'),
            ('initial_state_at_pann','pann_measured','pann_pann_state'),
            ('parameters_at_pann_state','integral_pann_state','pann_pann_state')]:
        deltas = {}
        for target in ('heldout_measured','heldout_reference'):
            if cases[baseline][target] is not None:
                deltas[target] = {key:cases[alternative][target][key]-cases[baseline][target][key]
                    for key in ('current_rmse_A','voltage_rmse_V')}
        effects.append({'factor':factor,'baseline':baseline,'alternative':alternative,'rmse_delta':deltas})
    return {'status':'completed','version':'parameter-state-controls-v1','result_id':result_id,
        'pann_source_result_id':source_id,'dataset_sha256':data.sha256,
        'scope':'fixed_model_same_record_forward_ablation','retrained':False,
        'parameter_source':source.get('result_source','server_completed_result'),
        'measured_initial_state_policy':'nonnegative_projection_of_first_measurement',
        'pwm_source':options.pwm_source,'train_end_s':source['train_end_s'],'validation_end_s':end,
        'power_stage_model':'joint_series_resistances' if joint else 'known_series_resistances' if options.include_parasitics else 'ideal',
        'parasitics' if joint else 'known_parasitics':dict(zip(('inductor_resistance_ohm','capacitor_esr_ohm'),map(float,parameters[3:5]) if joint else resistances(options))),
        'cases':cases,'verified_numerical_effects':effects,'reference':reference_status,
        'physical_causes_verified':[],
        'interpretation':'Only numerical effects of parameter/state replacement are verified under this fixed model. Delta is alternative minus baseline; negative means lower RMSE. Effects can interact. These tests do not prove ESR, load changes, sensor bias, or parameter truth. A reference is user-declared; it never changes training, initial states, selected parameters or the 5% gate.'}
