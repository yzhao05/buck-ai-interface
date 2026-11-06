"""Per-answer USD budget; settled usage plus pending request reservations."""
import math
import json
import os
import time


class BudgetExceeded(ValueError):
    pass


# Published DeepSeek peak USD/M-token rates, checked 2026-11-06.
# https://api-docs.deepseek.com/quick_start/pricing/
# Use peak rates as an upper estimate; this is not the provider's invoice.
DEEPSEEK_PRICES = {
    'deepseek-flash': (.30, .006, 1.20),
    'deepseek-v4-flash': (.30, .006, 1.20),
    'deepseek-v4-flash-vision-exp': (.30, .006, 1.20),
    'deepseek-v4-pro': (1.32, .044, 3.96),
}


class Budget:
    def __init__(self, thinking):
        self.thinking = thinking
        self.rounds = 14 if thinking else 7
        self.started = time.monotonic()
        self.seconds = float(os.getenv('AGENT_TIME_LIMIT_S', '300'))
        self.limit = float(os.getenv('AGENT_COST_LIMIT_USD', '0.50'))
        self.model = os.getenv('DEEPSEEK_MODEL', 'deepseek-flash')
        prices = DEEPSEEK_PRICES.get(self.model)
        if prices is None and not all(os.getenv(key) for key in ('AGENT_INPUT_USD_PER_M', 'AGENT_OUTPUT_USD_PER_M')):
            raise ValueError('Configure token prices for this model')
        custom = any(os.getenv(key) for key in ('AGENT_INPUT_USD_PER_M', 'AGENT_CACHED_INPUT_USD_PER_M', 'AGENT_OUTPUT_USD_PER_M'))
        self.input_price = float(os.getenv('AGENT_INPUT_USD_PER_M', str(prices[0] if prices else 0)))
        self.output_price = float(os.getenv('AGENT_OUTPUT_USD_PER_M', str(prices[2] if prices else 0)))
        self.cached_input_price = float(os.getenv('AGENT_CACHED_INPUT_USD_PER_M', str(self.input_price if custom else prices[1])))
        self.pricing_basis = 'configured USD rates' if custom else 'DeepSeek peak USD rates verified 2026-11-06'
        self.max_output_tokens = int(os.getenv('AGENT_MAX_OUTPUT_TOKENS', '32768' if thinking else '8192'))
        if self.max_output_tokens <= 0:
            raise ValueError('Agent output token limit must be positive')
        if not all(math.isfinite(x) and x > 0 for x in (self.seconds, self.limit, self.input_price, self.output_price, self.cached_input_price)):
            raise ValueError('Agent budget settings must be positive')
        self.seconds = min(900, self.seconds)
        self.reserved = 0.
        self.calls = 0
        self.reservations = {}

    def remaining(self):
        remaining = self.seconds - (time.monotonic() - self.started)
        if remaining <= 0:
            raise BudgetExceeded('time budget reached')
        return remaining

    def reserve(self, payload):
        self.remaining()
        if self.calls >= self.rounds:
            raise BudgetExceeded('model step budget reached')
        # UTF-8 bytes upper-bound normal text token counts; include protocol overhead.
        inputs = len(json.dumps(payload, ensure_ascii=False).encode('utf-8')) + 4096
        available = self.limit - self.reserved - inputs * self.input_price / 1e6
        requested = payload.get("max_tokens", self.max_output_tokens)
        if type(requested) is not int or requested <= 0:
            raise ValueError('Model output token limit must be a positive integer')
        ceiling = min(requested, self.max_output_tokens)
        tokens = min(ceiling, int(available * 1e6 / self.output_price))
        if tokens < min(512, ceiling):
            raise BudgetExceeded('estimated cost budget reached')
        cost = (inputs * self.input_price + tokens * self.output_price) / 1e6
        self.reserved += cost
        self.calls += 1
        self.reservations[self.calls] = {'input_tokens':inputs, 'cost':cost}
        return tokens

    def settle(self, call_id, usage):
        """Release unused reservation only after a complete response with usage.

        Messages output_tokens includes thinking. Missing cache counters retain
        the conservative input estimate; missing output usage retains everything.
        """
        reservation = self.reservations.get(call_id)
        if reservation is None or not isinstance(usage, dict):
            return
        output = usage.get('output_tokens')
        if type(output) is not int or output < 0:
            return
        input_fields = ('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens')
        input_cost = reservation['input_tokens'] * self.input_price
        if all(type(usage.get(key)) is int and usage[key] >= 0 for key in input_fields):
            input_cost = ((usage['input_tokens'] + usage['cache_creation_input_tokens']) * self.input_price
                          + usage['cache_read_input_tokens'] * self.cached_input_price)
        cost = (input_cost + output * self.output_price) / 1e6
        self.reserved = max(0., self.reserved + cost - reservation['cost'])
        del self.reservations[call_id]

    def metadata(self):
        return {'thinking': self.thinking, 'model_calls': self.calls,
                'model_call_limit': self.rounds, 'time_limit_s': self.seconds,
                'max_output_tokens': self.max_output_tokens,
                'unsettled_model_calls': len(self.reservations),
                'currency': 'USD', 'pricing_model': self.model, 'pricing_basis': self.pricing_basis,
                'token_prices_per_million': {'input':self.input_price, 'cached_input':self.cached_input_price, 'output':self.output_price},
                'reserved_cost_usd': round(self.reserved, 6), 'cost_limit_usd': self.limit,
                'elapsed_s': round(time.monotonic()-self.started, 2),
                'cost_basis': 'completed usage at configured prices plus conservative unsettled reservations'}


def evidence(result):
    if not result or result.get('error'):
        return {'status': 'unavailable', 'error': '没有匹配的当前结果。'}
    diag = result.get('diagnostics', {})
    history = diag.get('pann', {}).get('epoch_history', [])
    stride = max(1, (len(history)+79)//80)
    sampled = history[::stride]
    if history and sampled[-1] != history[-1]:
        sampled.append(history[-1])
    return {'status': 'completed', 'result_id': result.get('result_id'),
            'trajectory': sampled, 'recorded_epochs': len(history), 'sample_stride': stride,
            'missing': [] if history else ['逐轮损失与参数轨迹未记录，不能推断训练过程'],
            'diagnostics': {k: v for k, v in diag.items() if k != 'pann'},
            'convergence': diag.get('pann', {}).get('convergence'),
            'residuals': result.get('measurement_diagnostics'),
            'metrics': result.get('metrics'), 'parameter_comparison': result.get('parameter_comparison')}
