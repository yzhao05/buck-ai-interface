"""Usage estimates only include provider-reported counts."""
import pytest

from backend.agent_policy import Budget


@pytest.fixture(autouse=True)
def default_cost_settings(monkeypatch):
    for key in ('AGENT_COST_LIMIT_USD', 'AGENT_INPUT_USD_PER_M', 'AGENT_CACHED_INPUT_USD_PER_M',
                'AGENT_OUTPUT_USD_PER_M', 'AGENT_MAX_OUTPUT_TOKENS'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('DEEPSEEK_MODEL', 'deepseek-flash')


def usage(inputs=1000, cached=9000, outputs=100):
    return {'input_tokens':inputs, 'cache_read_input_tokens':cached,
            'cache_creation_input_tokens':0, 'output_tokens':outputs}


@pytest.mark.parametrize('model,prices', [
    ('deepseek-flash', (.30, .006, 1.20)),
    ('deepseek-v4-flash', (.30, .006, 1.20)),
    ('deepseek-v4-flash-vision-exp', (.30, .006, 1.20)),
    ('deepseek-v4-pro', (1.32, .044, 3.96)),
])
def test_published_peak_rates_are_model_specific(monkeypatch, model, prices):
    monkeypatch.setenv('DEEPSEEK_MODEL', model)
    budget = Budget(True)
    assert (budget.input_price, budget.cached_input_price, budget.output_price) == prices
    assert budget.metadata()['currency'] == 'USD'
    assert budget.metadata()['pricing_model'] == model


def test_completed_calls_track_reported_usage_once_even_after_old_cost_limit(monkeypatch):
    monkeypatch.setenv('AGENT_COST_LIMIT_USD', '.000001')
    budget = Budget(True)
    first = budget.start_call()
    second = budget.start_call()
    budget.settle(second, usage(outputs=1_000_000))
    expected = (1000*.30 + 9000*.006 + 1_000_000*1.20)/1e6
    assert budget.estimated_cost == pytest.approx(expected)
    budget.settle(second, usage())
    assert budget.estimated_cost == pytest.approx(expected)
    assert budget.pending == {first}
    assert budget.start_call() == 3


@pytest.mark.parametrize('report', [None, {}, {'output_tokens': 100}, usage(outputs=-1), usage(cached=True)])
def test_incomplete_or_invalid_usage_stays_unsettled(report):
    budget = Budget(True)
    call = budget.start_call()
    budget.settle(call, report)
    assert budget.metadata()['unsettled_model_calls'] == 1
    assert budget.estimated_cost == 0


@pytest.mark.parametrize('invalid_rate', [None, '', '-1', 'nan', 'inf', 'invalid'])
def test_missing_or_invalid_rates_only_disable_cost_estimates(monkeypatch, invalid_rate):
    monkeypatch.setenv('DEEPSEEK_MODEL', 'custom-provider-model')
    if invalid_rate is not None:
        monkeypatch.setenv('AGENT_INPUT_USD_PER_M', invalid_rate)
        monkeypatch.setenv('AGENT_OUTPUT_USD_PER_M', '3')
    budget = Budget(True)
    budget.settle(budget.start_call(), usage())
    assert budget.metadata()['model_calls'] == 1
    assert budget.metadata()['unsettled_model_calls'] == 0
    assert budget.metadata()['reserved_cost_usd'] is None
    assert budget.metadata()['pricing_basis'] == 'unavailable'
    monkeypatch.setenv('AGENT_INPUT_USD_PER_M', '2')
    monkeypatch.setenv('AGENT_OUTPUT_USD_PER_M', '3')
    budget = Budget(True)
    assert (budget.input_price, budget.cached_input_price, budget.output_price) == (2, 2, 3)
