import copy
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from backend.controller import PROFILE, reconstruct_training
from backend.data import read_csv, extract, suggest_mapping
from backend.identification import identify
from backend.pwm_reconstruction import filter_voltage, modified_sinc_kernel, pwm_quality, PWMQualityError
from backend.schemas import FitOptions, Mapping, ChatRequest, ExperimentComparisonContext

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('method', ['sg', 'modified_sinc', 'none'])
def test_filters_preserve_linear_voltage_and_do_not_mutate_input(method):
    voltage = np.linspace(1., 5., 5001)
    original = voltage.copy()
    filtered, info = filter_voltage(voltage, method, 6)
    np.testing.assert_allclose(filtered, original, rtol=1e-8, atol=1e-8)
    np.testing.assert_array_equal(voltage, original)
    assert not info['uses_heldout_measurements']
    assert not info['fit_measurements_smoothed']
    assert info['window_samples'] == (1 if method == 'none' else 3001)


def test_modified_sinc_symmetric_normalized_kernel_and_reject_short_input():
    kernel = modified_sinc_kernel(1001)
    np.testing.assert_allclose(kernel, kernel[::-1], atol=1e-15)
    assert kernel.sum() == pytest.approx(1)
    assert np.max(np.abs(np.fft.rfft(kernel, 16384)[4000:])) < 1e-5
    for method in ['sg', 'modified_sinc']:
        with pytest.raises(ValueError, match='短于'):
            filter_voltage(np.ones(100), method, 1)
    with pytest.raises(ValueError, match='无效'):
        filter_voltage(np.ones(100), 'sg', float('nan'))


@pytest.mark.parametrize('phase', [0., .2, .123])
def test_quality_respects_carrier_phase_and_censored_cycles(phase):
    profile = {**PROFILE, 'carrier_phase_at_t0': phase}
    k = np.arange(3001)
    gate = (((k % 500)/500 + phase) % 1 < .4).astype(float)
    info = pwm_quality(gate, profile)
    assert info['status'] == 'passed', info
    assert info['complete_cycles'] == (6 if phase == 0 else 5)
    assert all(c['duty'] == pytest.approx(.4) for c in info['cycles'])


def test_quality_rejects_chatter_short_pulses_duty_and_missing_edges():
    chatter = np.tile([0., 1.], 5035)
    info = pwm_quality(chatter, PROFILE)
    assert info['status'] == 'rejected' and info['rising_edges'] > 5000
    assert info['extra_edges'] > 5000 and info['short_pulses'] > 5000
    narrow = (np.arange(3001) % 500 < 2).astype(float)
    info = pwm_quality(narrow, PROFILE)
    assert info['short_pulses'] > 0 and info['duty_violations'] == 6
    info = pwm_quality(np.ones(3001), PROFILE)
    assert info['missing_edge_cycles'] == 6
    assert pwm_quality(np.ones(3001), {**PROFILE, 'duty_max': 1})['status'] == 'passed'


@pytest.mark.parametrize('at_start', [False, True])
def test_partial_cycles_reject_known_extra_edges_without_rejecting_censoring(at_start):
    phase = .2 if at_start else 0
    profile = {**PROFILE, 'carrier_phase_at_t0': phase}
    k = np.arange(3401)
    normal = (((k % 500)/500 + phase) % 1 < .4).astype(float)
    assert pwm_quality(normal, profile)['status'] == 'passed'
    lo = 0 if at_start else 3000
    normal[lo:lo+400] = 0
    for start in (lo, lo+120, lo+240):
        normal[start:start+60] = 1  # Each pulse > short-pulse cutoff.
    quality = pwm_quality(normal, profile)
    assert quality['status'] == 'rejected'
    assert quality['short_pulses'] == 0
    assert quality['extra_edges'] >= 3
    assert quality['missing_edge_cycles'] == quality['duty_violations'] == 0
    assert any(c['rises'] > 1 for c in quality['partial_cycles'])


def fixture():
    data = read_csv((ROOT/'examples/plecs_buck_case16a.csv').read_bytes(), 'test.csv')
    mapping = Mapping(**suggest_mapping(data))
    options = FitOptions(mode='LC', method='weighted_integral', pwm_source='controller',
                         controller_confirmed=True, start_s=0, end_s=.0008)
    return data, mapping, options


@pytest.mark.parametrize('method', ['sg', 'modified_sinc'])
def test_raw_channels_preserved_and_reference_pwm_is_not_a_reconstruction_input(method):
    data, mapping, _ = fixture()
    a, _ = extract(data, mapping)
    train = a[a[:, 0] < .00056].copy()
    train[:, 2] += np.random.default_rng(12).normal(0, .1, len(train))
    original = train.copy()
    smooth, info = reconstruct_training(train, denoise=method, return_diagnostics=True)
    raw = reconstruct_training(train, denoise='none')
    np.testing.assert_array_equal(smooth[:, :3], raw[:, :3])
    assert np.any(smooth[:, 3] != raw[:, 3])
    np.testing.assert_array_equal(train, original)
    assert info['raw_fit_channels_preserved'] and not info['reference_used']
    train[:, 3] = 1-train[:, 3]
    np.testing.assert_array_equal(smooth, reconstruct_training(train, denoise=method))


@pytest.mark.parametrize('method', ['sg', 'modified_sinc'])
def test_split_precedes_filtering_and_pann_receives_original_voltage(monkeypatch, method):
    data, mapping, opts = fixture()
    opts = opts.model_copy(update={'method': 'pann', 'pwm_denoise': method})
    captured = []
    def refine(train, initial, options):
        captured.append(train.copy())
        return initial, {'rollout_initial_state_A_V': train[0, 1:3].tolist()}
    monkeypatch.setattr('backend.pann_adapter.refine', refine)
    before = identify(data, mapping, opts)
    end = before['train_end_s']
    data.frame.loc[data.frame.iloc[:,0] > end, mapping.voltage] += 100
    after = identify(data, mapping, opts)
    np.testing.assert_array_equal(captured[0], captured[1])
    assert before['parameters'] == after['parameters']
    assert before['diagnostics']['pwm_reconstruction'] == after['diagnostics']['pwm_reconstruction']
    raw, _ = extract(data, mapping)
    raw = raw[raw[:,0] <= end]
    np.testing.assert_array_equal(captured[0][:,2], np.interp(captured[0][:,0], raw[:,0], raw[:,2]))


def test_noisy_unfiltered_drive_is_blocked_before_any_fitter(monkeypatch):
    data, mapping, opts = fixture()
    data.frame[mapping.voltage] += np.random.default_rng(12).normal(0, .4, len(data.frame))
    opts = opts.model_copy(update={'method': 'pann', 'pwm_denoise': 'none'})
    def forbidden(*args, **kwargs):
        pytest.fail('Invalid PWM must not enter initialization or training')
    monkeypatch.setattr('backend.identification.weak_integral_seed', forbidden)
    monkeypatch.setattr('backend.pann_adapter.refine', forbidden)
    stages = []
    with pytest.raises(PWMQualityError) as error:
        identify(data, mapping, opts, progress=stages.append)
    assert stages == ['checking', 'pwm_reconstruction']
    assert error.value.diagnostics['quality']['status'] == 'rejected'


def test_old_result_analysis_is_not_relabelled_as_denoised():
    _, mapping, opts = fixture()
    old = opts.model_dump()
    del old['pwm_denoise']; del old['pwm_filter_cycles']
    saved = dict(id='old', created=1, updated=1, dataset={'filename': 'old.csv'},
                 result={'options': old, 'mapping': mapping.model_dump(),
                         'parameters': {'L_H': 60e-6, 'C_F': 500e-6, 'R_ohm': 6}})
    request = dict(message='分析', result_id='old', saved_result=saved, options=old, mapping=mapping.model_dump())
    parsed = ChatRequest.model_validate(request)
    assert parsed.options.pwm_denoise == 'none'
    assert parsed.saved_result.result['options']['pwm_denoise'] == 'none'
    assert 'pwm_denoise' not in old  # Input is not mutated.
    changed = copy.deepcopy(request)
    changed['options'] = {**changed['options'], 'pwm_denoise': 'sg'}
    with pytest.raises(ValidationError, match='match the selected result'):
        ChatRequest.model_validate(changed)
    context = ExperimentComparisonContext(options=old, mapping=mapping, start_s=0, end_s=.0008)
    assert context.options.pwm_denoise == 'none'


def test_failed_job_exposes_quality_evidence_only_to_owner(monkeypatch):
    from fastapi.testclient import TestClient
    from backend import main
    import uuid
    import time
    owner = str(uuid.uuid4())
    monkeypatch.setattr(main, 'TOKEN', '')
    evidence = {'quality': pwm_quality(np.tile([0.,1.], 2000), PROFILE)}
    monkeypatch.setattr(main, 'jobs', {'bad': {'owner':owner, 'status':'failed', 'result':None, 'created':time.time(),
        'error':str(PWMQualityError(evidence)), 'pwm_reconstruction':evidence}})
    with TestClient(main.app) as client:
        response = client.get('/api/jobs/bad', headers={'X-Session-ID':owner})
        assert response.status_code == 200
        assert response.json()['result'] is None
        assert response.json()['pwm_reconstruction'] == evidence
        assert client.get('/api/jobs/bad',headers={'X-Session-ID':str(uuid.uuid4())}).status_code == 404
