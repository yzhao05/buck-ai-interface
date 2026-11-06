"""DeepSeek token accounting must not confuse reservations with billed spend."""
import pytest

from backend.agent_policy import Budget, BudgetExceeded


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


def test_completed_usage_releases_unused_output_and_discounts_cached_input():
    budget = Budget(True)
    budget.reserve({'messages': [{'content':'test'}]})
    reserved = budget.reserved
    budget.settle(1, usage())
    assert budget.reserved == pytest.approx((1000*.30 + 9000*.006 + 100*1.20)/1e6)
    assert budget.reserved < reserved / 10
    settled = budget.reserved
    budget.settle(1, usage())
    assert budget.reserved == settled  # Duplicate terminal frames cannot refund twice.


def test_many_short_tool_rounds_do_not_consume_full_output_reservations():
    budget = Budget(True)
    for call in range(1, 15):
        assert budget.reserve({'messages':[{'content':'工具描述和上下文'*6000}]}) == 32768
        budget.settle(call, usage(inputs=2000, cached=50000, outputs=150))
    assert budget.reserved < .02
    assert budget.metadata()['unsettled_model_calls'] == 0
    with pytest.raises(BudgetExceeded, match='step budget'):
        budget.reserve({})


def test_missing_cache_usage_keeps_conservative_input_estimate():
    budget = Budget(True)
    output_cap = budget.reserve({'messages':[]})
    reserved = budget.reserved
    budget.settle(1, {'output_tokens':100})
    assert budget.reserved == pytest.approx(reserved-(output_cap-100)*budget.output_price/1e6)


@pytest.mark.parametrize('report', [None, {}, {'output_tokens':-1}, {'output_tokens':True}, {'output_tokens':'100'}])
def test_missing_or_invalid_usage_never_releases_a_reservation(report):
    budget = Budget(True)
    budget.reserve({})
    reserved = budget.reserved
    budget.settle(1, report)
    assert budget.reserved == reserved
    assert budget.metadata()['unsettled_model_calls'] == 1


def test_inflight_requests_keep_separate_reservations():
    budget = Budget(True)
    budget.reserve({})
    first = budget.reserved
    budget.reserve({})
    budget.settle(1, usage())
    assert budget.reserved >= first
    assert budget.metadata()['unsettled_model_calls'] == 1


def test_provider_usage_above_reservation_is_accounted_not_silently_clamped():
    budget = Budget(True)
    budget.reserve({})
    budget.settle(1, usage(outputs=1_000_000))
    assert budget.reserved > budget.limit
    with pytest.raises(BudgetExceeded, match='cost budget'):
        budget.reserve({})


def test_unknown_model_requires_explicit_rates(monkeypatch):
    monkeypatch.setenv('DEEPSEEK_MODEL', 'custom-provider-model')
    with pytest.raises(ValueError, match='Configure token prices'):
        Budget(True)
    monkeypatch.setenv('AGENT_INPUT_USD_PER_M', '2')
    monkeypatch.setenv('AGENT_OUTPUT_USD_PER_M', '3')
    budget = Budget(True)
    assert (budget.input_price, budget.cached_input_price, budget.output_price) == (2, 2, 3)


@pytest.mark.parametrize('thinking,limit', [(True,32768),(False,8192)])
def test_output_cap_includes_thinking_and_is_enforced_on_wire_requests(thinking, limit):
    budget = Budget(thinking)
    assert budget.reserve({'max_tokens':1_000_000}) == limit


def test_custom_output_limit_and_cost_limit_both_apply(monkeypatch):
    monkeypatch.setenv('AGENT_MAX_OUTPUT_TOKENS', '16384')
    monkeypatch.setenv('AGENT_COST_LIMIT_USD', '.01')
    budget = Budget(True)
    cap = budget.reserve({})
    assert 512 <= cap < 16384
    assert budget.reserved <= .01
