"""Per-answer usage telemetry from completed provider responses."""
import math
import os
import time


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
    def __init__(self, thinking, chat_model='deepseek'):
        self.chat_model = chat_model
        self.reasoning_effort = 'high' if thinking else ('low' if chat_model == 'gpt-6.1-terra' else 'off')
        self.thinking = thinking
        self.started = time.monotonic()
        if chat_model == 'gpt-6.1-terra':
            self.model = chat_model
            # Standard Responses rates. DeepSeek-specific overrides do not apply here.
            self.input_price, self.cached_input_price, self.output_price = 1.0, .05, 5.0
            self.pricing_basis = 'OpenAI Terra standard USD rates'
        else:
            self.model = os.getenv('DEEPSEEK_MODEL', 'deepseek-flash')
            defaults = DEEPSEEK_PRICES.get(self.model, (None, None, None))
            input_rate = os.getenv('AGENT_INPUT_USD_PER_M')
            cached_rate = os.getenv('AGENT_CACHED_INPUT_USD_PER_M')
            output_rate = os.getenv('AGENT_OUTPUT_USD_PER_M')
            custom = any(rate is not None for rate in (input_rate, cached_rate, output_rate))
            try:
                self.input_price = float(input_rate if input_rate is not None else defaults[0])
                self.output_price = float(output_rate if output_rate is not None else defaults[2])
                default_cached = self.input_price if custom else defaults[1]
                self.cached_input_price = float(cached_rate if cached_rate is not None else default_cached)
                if not all(math.isfinite(x) and x >= 0 for x in (self.input_price, self.cached_input_price, self.output_price)):
                    raise ValueError('Invalid token price')
            except (TypeError, ValueError):
                self.input_price = self.cached_input_price = self.output_price = None
                self.pricing_basis = 'unavailable'
            else:
                self.pricing_basis = 'configured USD rates' if custom else 'DeepSeek peak USD rates verified 2026-11-06'
        self.calls = 0
        self.pending = set()
        self.estimated_cost = 0. if self.input_price is not None else None

    def start_call(self):
        self.calls += 1
        self.pending.add(self.calls)
        return self.calls

    def settle(self, call_id, usage):
        """Account for each completed call once, using reported token counts."""
        if call_id not in self.pending or not isinstance(usage, dict):
            return
        inputs = usage.get('input_tokens')
        outputs = usage.get('output_tokens')
        cached = usage.get('cache_read_input_tokens', 0)
        created = usage.get('cache_creation_input_tokens', 0)
        if not all(type(n) is int and n >= 0 for n in (inputs, outputs, cached, created)):
            return
        if self.estimated_cost is not None:
            self.estimated_cost += ((inputs + created) * self.input_price + cached * self.cached_input_price
                                    + outputs * self.output_price) / 1e6
        self.pending.remove(call_id)

    def metadata(self):
        return {'thinking': self.reasoning_effort != 'off', 'reasoning_effort': self.reasoning_effort,
                'reasoning_mode': 'thinking' if self.thinking else 'fast', 'model_calls': self.calls,
                'model_call_limit': None, 'time_limit_s': None,
                'max_output_tokens': None,
                'unsettled_model_calls': len(self.pending),
                'currency': 'USD', 'pricing_model': self.model, 'pricing_basis': self.pricing_basis,
                'token_prices_per_million': {'input':self.input_price, 'cached_input':self.cached_input_price, 'output':self.output_price},
                'reserved_cost_usd': round(self.estimated_cost, 6) if self.estimated_cost is not None else None, 'cost_limit_usd': None,
                'elapsed_s': round(time.monotonic()-self.started, 2),
                'cost_basis': 'reported usage from completed calls at configured prices'}


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
