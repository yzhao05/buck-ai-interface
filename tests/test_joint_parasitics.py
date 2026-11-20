"""Unknown/fixed resistance selection and independently simulated circuit recovery."""
import numpy as np
import pytest
from backend.schemas import FitOptions
from backend.parasitics import estimated_resistances
from backend.identification import integral_fit, weak_integral_seed
from test_parasitics import circuit_data


@pytest.mark.parametrize('rl,rc', [(None,None),(0,0),(.25,None),(None,.12),(.25,.12)])
@pytest.mark.parametrize('fit', [integral_fit, weak_integral_seed])
def test_joint_integral_recovers_unknown_and_preserves_fixed(circuit_data, rl, rc, fit):
    options = FitOptions(mode='LC_RL_RC', inductor_resistance_ohm=rl, capacitor_esr_ohm=rc)
    assert options.include_parasitics
    assert estimated_resistances(options) == (not bool(rl), not bool(rc))
    params, diagnostic = fit(circuit_data, options)
    np.testing.assert_allclose(params, [60e-6,500e-6,6,.25,.12], rtol=.03)
    if rl: assert params[3] == rl
    if rc: assert params[4] == rc
    assert diagnostic['identifiability_condition_number'] < 100


def test_joint_sensing_branch(circuit_data):
    a = np.column_stack((circuit_data, circuit_data[:,2]/24))
    p, _ = weak_integral_seed(a, FitOptions(mode='LC_RL_RC', resistance_ohm=8))
    np.testing.assert_allclose(p, [60e-6,500e-6,8,.25,.12], rtol=.03)


@pytest.fixture(scope='module')
def joint_result(circuit_data):
    import pandas as pd
    from backend.data import read_csv, suggest_mapping
    from backend.schemas import Mapping
    from backend.identification import identify
    data = read_csv(pd.DataFrame(circuit_data,columns=['time','current','voltage','pwm']).to_csv(index=False).encode(), 'joint.csv')
    mapping = Mapping(**suggest_mapping(data))
    result = identify(data, mapping, FitOptions(mode='LC_RL_RC', method='weighted_integral'))
    return data, mapping, result


def test_joint_end_to_end_replays_fitted_resistances(joint_result):
    _, _, r = joint_result
    np.testing.assert_allclose(list(r['parameters'].values()), [60e-6,500e-6,6,.25,.12], rtol=.03)
    assert not r['R_estimated']
    assert r['metrics']['current_nrmse_pct'] < 2
    assert r['metrics']['voltage_nrmse_pct'] < 2
    assert r['diagnostics']['parasitics']['estimated'] == {'rL_ohm':True,'rC_ohm':True}


def test_joint_diagnostic_controls_replay_each_parameter_set(joint_result):
    import copy
    from backend.diagnostic_controls import run_controls
    data, _, baseline = joint_result
    r = copy.deepcopy(baseline)
    r['method'] = r['options']['method'] = 'pann'
    r['diagnostics']['pann'] = {'initial_parameters': list(r['parameters'].values()),
                              'rollout_initial_state_A_V': data.frame.iloc[0,1:3].tolist()}
    r['parameters']['rC_ohm'] *= 1.5
    controls = run_controls(data, r, result_id='joint', source_id='joint')
    assert controls['cases']['integral_measured']['heldout_measured'] == pytest.approx(baseline['metrics'])
    assert controls['cases']['pann_measured']['heldout_measured'] != controls['cases']['integral_measured']['heldout_measured']
    assert controls['cases']['pann_measured']['parameters']['rC_ohm'] == r['parameters']['rC_ohm']


def test_joint_report_and_history_keep_resistances(joint_result):
    from backend.report import ReportRequest, build_pdf
    from backend.schemas import ExperimentParameters
    from pypdf import PdfReader
    from io import BytesIO
    _, _, r = joint_result
    assert ExperimentParameters.model_validate(r['parameters']).rC_ohm > 0
    snapshot = {'result': r, 'language': 'en', 'exported_at':'2026-11-18T00:00:00Z'}
    report = ReportRequest.model_validate(snapshot)
    assert report.result.parameters.rL_ohm == r['parameters']['rL_ohm']
    text = '\n'.join(p.extract_text() for p in PdfReader(BytesIO(build_pdf(report))).pages)
    assert 'Joint series resistance estimation' in text
    assert 'rL' in text and 'rC' in text and 'Estimated' in text


@pytest.mark.parametrize('bad', [-.1, float('nan'), float('inf'),1001])
def test_joint_invalid_resistances_rejected(bad):
    from pydantic import ValidationError
    for key in ('inductor_resistance_ohm','capacitor_esr_ohm'):
        with pytest.raises(ValidationError):
            FitOptions(mode='LC_RL_RC', **{key:bad})


def test_joint_unexcited_regression_rejected():
    from backend.parasitics import fit_joint_parameters
    z, one = np.zeros(20), np.ones(20)
    with pytest.raises(ValueError, match='动态不足'):
        fit_joint_parameters(z,one,one,z,z,z,one,one,FitOptions(mode='LC_RL_RC'))


def test_joint_controller_path_uses_fitted_resistances():
    import pandas as pd
    from backend.controller import closed_loop_rollout, profile_for
    from backend.data import read_csv
    from backend.identification import identify
    from backend.schemas import Mapping
    known = FitOptions(mode='LC', method='weighted_integral', pwm_source='controller',
        controller_confirmed=True, include_parasitics=True, inductor_resistance_ohm=.05,
        capacitor_esr_ohm=.005, pwm_denoise='none', start_s=0, end_s=.0006)
    t = np.arange(30001)*20e-9
    y = closed_loop_rollout(t,[60e-6,500e-6,6],28,[0,12],profile_for(known))
    data = read_csv(pd.DataFrame(dict(t=t,i=y[:,0],v=y[:,1])).to_csv(index=False).encode(),'joint-controller.csv')
    options = FitOptions(**{**known.model_dump(), 'mode':'LC_RL_RC',
                            'inductor_resistance_ohm':None, 'capacitor_esr_ohm':None})
    r = identify(data, Mapping(time='t',current='i',voltage='v'), options)
    np.testing.assert_allclose([r['parameters']['L_H'],r['parameters']['C_F']],[60e-6,500e-6],rtol=.03)
    assert r['metrics']['current_nrmse_pct'] < 3
    assert r['metrics']['voltage_nrmse_pct'] < 3
    assert r['diagnostics']['controller']['inductor_resistance_ohm'] == r['parameters']['rL_ohm']
    assert r['diagnostics']['controller']['capacitor_esr_ohm'] == r['parameters']['rC_ohm']


def test_joint_stability_checks_unknown_resistances_too():
    from backend.convergence import assess_training
    keys=('L_H','C_F','rL_ohm','rC_ohm')
    history=[dict(epoch=k,loss=1e-3,L_H=60e-6,C_F=500e-6,R_ohm=6,rL_ohm=.05,rC_ohm=.005*(1+k/100)) for k in range(50)]
    result=assess_training(history,[60e-6,500e-6,6,.05,.005],1e-3,'LC_RL_RC',parameter_keys=keys)
    assert set(result['parameter_span_pct'])==set(keys)
    assert result['status']!='stable'
