import asyncio
import copy
import json

import numpy as np
import pytest

from backend import chat
from backend.measurement_diagnostics import assess_measurements, noise_proxy
from backend.experiment_history import repeatability_summary
from backend.schemas import ChatExperiment, ChatRequest


def signals(seed=12):
    rng = np.random.default_rng(seed)
    t = np.arange(20001)*1e-7
    phase = (t*1e5) % 1
    gate = (phase < .5).astype(float)
    triangle = 1-4*np.abs(phase-.5)
    clean = np.column_stack((10+triangle, 15+.1*triangle))
    measured = clean+rng.normal(size=clean.shape)*[.2, .02]
    return np.column_stack((t, measured, gate)), clean


def diagnostics():
    data, clean = signals()
    return assess_measurements(data[:14001], data[14000:], clean[14000:], 1e5,
                               training_gate=data[:14001, 3], validation_gate=data[14000:, 3])


def test_noise_proxy_preserves_switching_ripple_and_estimates_random_noise():
    report = diagnostics()
    np.testing.assert_allclose(report['noise_estimation']['sigma_A_V'], [.2, .02], rtol=.06)
    for channel in report['channels'].values():
        assert channel['noise_comparison'] == 'same_order'
        assert not channel['flags']
        assert channel['rmse_to_noise_ratio'] == pytest.approx(1, rel=.08)
    assert report['application_threshold_pct'] == 5


def test_bias_drift_edges_are_clues_not_a_new_acceptance_rule():
    data, clean = signals()
    valid = data[14000:]
    prediction = clean[14000:].copy()
    prediction[:, 0] -= .4+np.linspace(0, 1, len(valid))
    edge = np.r_[False, np.diff(valid[:, 3]) != 0]
    prediction[edge, 1] -= 1
    report = assess_measurements(data[:14001], valid, prediction, 1e5,
        training_gate=data[:14001, 3], validation_gate=valid[:, 3])
    assert {'bias', 'slow_variation', 'correlated_residual'} <= set(report['channels']['current']['flags'])
    assert 'edge_concentration' in report['channels']['voltage']['flags']
    assert report['channels']['current']['noise_comparison'] == 'above_noise'
    assert report['thresholds_are_heuristic'] is True
    assert report['application_threshold_pct'] == 5


def test_unresolved_sparse_irregular_and_missing_switch_information():
    data, clean = signals()
    noiseless = data.copy(); noiseless[:, 1:3] = clean
    assert noise_proxy(noiseless, 1e5, data[:, 3])['sigma_A_V'] == [None, None]
    assert noise_proxy(data[:20], 1e5)['status'] == 'insufficient_samples'
    assert noise_proxy(data[::20], 1e5)['status'] == 'insufficient_samples'
    irregular = data[14000:].copy(); irregular[1::2, 0] += 2e-8
    report = assess_measurements(data[:14001], irregular, clean[14000:], 1e5)
    for c in report['channels'].values():
        assert c['lag1_correlation'] is None
        assert c['edge_to_nonedge_rmse_ratio'] is None
    assert report['edge_source'].startswith('unavailable')


def run(identifier='a', sha='a', inductance=60e-6, **options):
    return ChatExperiment.model_validate(dict(id=identifier, dataset=dict(filename='same.csv', sha256=sha*64),
        method='weighted_integral', pwm_source='measured',
        parameters=dict(L_H=inductance, C_F=500e-6, R_ohm=6),
        comparison_context=dict(options=dict(method='weighted_integral', mode='LC', **options),
            mapping=dict(time='t', current='i', voltage='v', pwm='s'), start_s=0, end_s=.0008)))


def test_spread_deduplicates_csv_and_selects_current_configuration():
    rows = [run('a'), run('rerun', inductance=90e-6), run('b', sha='b', inductance=62e-6),
            run('other-setting', sha='c', vin=30)]
    summary = repeatability_summary(rows, 'a')
    assert len(summary['groups']) == 1
    group = summary['groups'][0]
    assert group['distinct_files'] == 2 and group['same_file_reruns_excluded'] == 1
    assert group['representative_ids'] == ['a', 'b']
    p = group['parameters']['L_H']
    assert p['mean'] == pytest.approx(61e-6)
    assert p['sample_std'] == pytest.approx(np.sqrt(2)*1e-6)
    assert p['min'] == 60e-6 and p['max'] == 62e-6
    assert summary['parameter_confidence_interval'] is None
    assert not summary['new_record_prediction_tested']
    assert not summary['independent_acquisitions_confirmed']


def test_repeated_fit_and_legacy_context_never_invent_uncertainty():
    old = run('old'); old.comparison_context = None
    summary = repeatability_summary([run(), run('rerun'), old])
    assert summary['excluded_missing_context'] == 1
    assert summary['groups'][0]['parameters'] == {}
    assert summary['groups'][0]['status'] == 'insufficient_distinct_files'
    assert repeatability_summary([run()], 'absent')['groups'] == []


def test_model_receives_diagnostics_and_scoped_spread_without_old_result(monkeypatch):
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'mock-only')
    seen = []
    class Provider:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            import httpx
            seen.append(kwargs['json'])
            return httpx.Response(200, request=httpx.Request('POST', url), json={'choices': [{'message': {'content': 'diagnostic analysis'}, 'finish_reason': 'stop'}]})
    monkeypatch.setattr(chat.httpx, 'AsyncClient', Provider)
    result = {'parameters': {'L_H': 60e-6, 'C_F': 500e-6, 'R_ohm': 6},
              'measurement_diagnostics': diagnostics(), 'diagnostics': {}, 'warnings': []}
    async def invoke(name):
        assert name == 'get_identification_result'
        return copy.deepcopy(result)
    request = ChatRequest(message='请分析当前辨识结果的误差，并给出有依据的修改计划。', result_id='a',
        experiments=[run(), run('b', sha='b'), run('OLD_LCR', sha='c', vin=30)])
    response = asyncio.run(chat.answer(request, invoke))
    assert response['answer'] == 'diagnostic analysis'
    payload = seen[0]
    context = json.loads(payload['messages'][1]['content'].split('：', 1)[1])
    assert context['previous_result']['measurement_diagnostics'] == result['measurement_diagnostics']
    assert context['repeatability']['groups'][0]['distinct_files'] == 2
    assert context['experiment_history'] == []
    assert 'OLD_LCR' not in json.dumps(payload)
    assert '5%' in payload['messages'][0]['content']
    assert 'start_identification' not in json.dumps(payload['tools'])
