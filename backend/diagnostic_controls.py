"""Bounded, deterministic parameter/state ablations. No training or generated code."""
import numpy as np
import re

from .data import extract
from .schemas import FitOptions, Mapping
from .identification import rollout, metric
from .controller import closed_loop_rollout, profile_for, validate_profile


def controls_declined(message):
    return bool(re.search(r"(?:不要|不用|别|无需|不需要|先不|暂不)[^，。；,;\n]{0,12}(?:对照|交换|运行|计算)|\b(?:do not|don't|no)\s+(?:run|compute|execute|controls|ablations)",message,re.I))


def controls_requested(message):
    if controls_declined(message) or re.search(r'是什么|什么是|原理|公式|\b(?:what|how)\b',message,re.I):
        return False
    return bool(re.search(r"(?:参数|初值|参考)[^。；\n]{0,35}(?:交换|对照)|(?:运行|执行|做).{0,8}(?:这些|这几个|上述|诊断)?对照|\b(?:ablation|swap|run (?:the )?(?:diagnostic )?controls)\b",message,re.I))


def describe_controls(report):
    if not report:
        return ''
    if report.get('status') != 'completed':
        return '已验证原因：本次未完成数值对照。'+report.get('error','证据不足。')
    lines = ['已验证的数值效应（当前固定模型，未重新训练）：']
    labels = ['固定积分参数，仅改用 PANN 初值', '固定测量初值，仅改用 PANN 参数']
    for label, effect in zip(labels, report['verified_numerical_effects'][:2]):
        d = effect['rmse_delta']['heldout_measured']
        lines.append(f"- {label}：电流 RMSE 变化 {d['current_rmse_A']:+.5g} A，电压 RMSE 变化 {d['voltage_rmse_V']:+.5g} V（负值为降低）。")
    ref = report['reference']
    if ref['status']=='evaluated_user_declared_reference':
        m = report['cases']['pann_pann_state']['heldout_reference']
        lines.append(f"- PANN 参数与初值对用户声明的干净参考：电流 RMSE {m['current_rmse_A']:.5g} A，电压 RMSE {m['voltage_rmse_V']:.5g} V。")
    else:
        lines.append('干净参考对照未执行：'+ref['reason'])
    lines.append('可能原因（待验证）：这些对照尚不能证明真实硬件的 ESR、负载变化或测量偏置；需针对残差线索做独立验证。')
    return '\n'.join(lines)


def compatible(current, candidate):
    if candidate.get('method') != 'pann' or current.get('file_sha256') != candidate.get('file_sha256'):
        return False
    ignored = {'method', 'epochs', 'pann_initial_state', 'pann_window_cycles'}
    return (current.get('mapping') == candidate.get('mapping') and
            all(current.get(k) == candidate.get(k) for k in ('train_start_s', 'train_end_s', 'validation_end_s')) and
            {k:v for k,v in current.get('options', {}).items() if k not in ignored} ==
            {k:v for k,v in candidate.get('options', {}).items() if k not in ignored})


def run_controls(data, source, *, result_id, source_id, reference=None):
    if data.sha256 != source.get('file_sha256'):
        raise ValueError('对照 CSV 与实验校验值不一致，未执行。')
    options = FitOptions.model_validate(source['options'])
    mapping = Mapping.model_validate(source['mapping'])
    if source.get('method') != 'pann':
        raise ValueError('缺少匹配的已完成 PANN 结果，不能进行参数与初值交换。')
    pann = source.get('diagnostics', {}).get('pann', {})
    seed = np.asarray(pann.get('initial_parameters', []), dtype=float)
    parameters = np.array([source['parameters'][k] for k in ('L_H','C_F','R_ohm')])
    state = np.asarray(pann.get('rollout_initial_state_A_V', []), dtype=float)
    if (seed.shape != (3,) or state.shape != (2,) or not np.isfinite(seed).all()
            or not np.isfinite(parameters).all() or not np.isfinite(state).all()
            or min(seed) <= 0 or min(parameters) <= 0 or min(state) < 0):
        raise ValueError('历史结果缺少有效的积分参数或 PANN 初始状态，未执行交换对照。')
    # Apply the same physical bounds used by the fitter before accepting a snapshot.
    if not (1e-7 <= seed[0] <= .1 and 1e-8 <= seed[1] <= 1 and .01 <= seed[2] <= 1e5
            and 1e-7 <= parameters[0] <= .1 and 1e-8 <= parameters[1] <= 1 and .01 <= parameters[2] <= 1e5):
        raise ValueError('对照参数超出支持范围。')
    if options.mode == 'LC' and not np.allclose([seed[2], parameters[2]], options.resistance_ohm, rtol=1e-9):
        raise ValueError('固定 R 与结果不一致，未执行对照。')
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
        predicted = (closed_loop_rollout(a[:,0],p,options.vin,x,profile) if controller else rollout(a,p,options.vin,x))
        if not np.isfinite(predicted).all():
            raise ValueError('对照前向预测产生非有限值，未报告结果。')
        cases[name] = {'parameters':dict(zip(('L_H','C_F','R_ohm'),map(float,p))),
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
        'cases':cases,'verified_numerical_effects':effects,'reference':reference_status,
        'physical_causes_verified':[],
        'interpretation':'Only numerical effects of parameter/state replacement are verified under this fixed model. Delta is alternative minus baseline; negative means lower RMSE. Effects can interact. These tests do not prove ESR, load changes, sensor bias, or parameter truth. A reference is user-declared; it never changes training, initial states, selected parameters or the 5% gate.'}
