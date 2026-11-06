"""Per-answer model budget; prices are configurable conservative USD / M tokens."""
import math
import json
import os
import time


class BudgetExceeded(ValueError):
    pass


class Budget:
    def __init__(self, thinking):
        self.thinking = thinking
        self.rounds = 6 if thinking else 5
        self.started = time.monotonic()
        self.seconds = min(180, float(os.getenv('AGENT_TIME_LIMIT_S', '180')))
        self.limit = float(os.getenv('AGENT_COST_LIMIT_USD', '0.50'))
        self.input_price = float(os.getenv('AGENT_INPUT_USD_PER_M', '1'))
        self.output_price = float(os.getenv('AGENT_OUTPUT_USD_PER_M', '5'))
        if not all(math.isfinite(x) and x > 0 for x in (self.seconds, self.limit, self.input_price, self.output_price)):
            raise ValueError('Agent budget settings must be positive')
        self.reserved = 0.
        self.calls = 0

    def remaining(self):
        remaining = self.seconds - (time.monotonic() - self.started)
        if remaining <= 0:
            raise BudgetExceeded('time budget reached')
        return remaining

    def reserve(self, payload):
        self.remaining()
        # UTF-8 bytes upper-bound normal text token counts; include protocol overhead.
        inputs = len(json.dumps(payload, ensure_ascii=False).encode('utf-8')) + 4096
        available = self.limit - self.reserved - inputs * self.input_price / 1e6
        ceiling = payload.get("max_tokens", 8000 if self.thinking else 3000)
        tokens = min(ceiling, int(available * 1e6 / self.output_price))
        if tokens < min(512, ceiling):
            raise BudgetExceeded('estimated cost budget reached')
        self.reserved += (inputs * self.input_price + tokens * self.output_price) / 1e6
        self.calls += 1
        return tokens

    def metadata(self):
        return {'thinking': self.thinking, 'model_calls': self.calls,
                'reserved_cost_usd': round(self.reserved, 6), 'cost_limit_usd': self.limit,
                'elapsed_s': round(time.monotonic()-self.started, 2),
                'cost_basis': 'conservative reservation at configured token prices'}


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
