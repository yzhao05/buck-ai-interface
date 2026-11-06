"""Domain-policy tests use scripted turns; the SDK has a separate real-runtime test.

Existing fixtures speak Chat Completions. This adapter is test-only and keeps
their policy assertions independent of the official runtime's Messages wire API.
"""
import json
import os
import pytest
import httpx
from backend import harness_runtime
from backend.agent_policy import BudgetExceeded

REAL_CLIENT = httpx.AsyncClient
REAL_RUN = harness_runtime.run


@pytest.fixture(autouse=True)
def scripted_harness(monkeypatch, tmp_path):
    monkeypatch.setattr(harness_runtime,'ROOT',tmp_path/'harness')
    async def run(messages,tools,invoke,budget):
        if httpx.AsyncClient is REAL_CLIENT:
            raise AssertionError('A domain test must install a provider stub; use REAL_RUN for official SDK tests')
        async with httpx.AsyncClient() as client:
            for index in range(budget.rounds):
                payload = {'model':os.getenv('DEEPSEEK_MODEL','deepseek-flash'),'messages':messages,'tools':tools,'tool_choice':'auto',
                           'thinking':{'type':'enabled' if budget.thinking else 'disabled'}}
                payload['max_tokens'] = budget.reserve(payload)
                response = await client.post('http://scripted-provider/chat/completions',headers={},json=payload)
                response.raise_for_status()
                message = response.json()['choices'][0]['message']
                # Scripted fixtures have no provider usage. Model their completed
                # output conservatively so reservation settlement is exercised.
                budget.settle(budget.calls, {'output_tokens':len(json.dumps(message, ensure_ascii=False).encode('utf-8'))})
                calls = message.get('tool_calls') or []
                if not calls:
                    return message.get('content') or ''
                messages.append(message)
                for call in calls:
                    try:
                        output = await invoke(call['function']['name'],json.loads(call['function'].get('arguments') or '{}'))
                    except (ValueError,TypeError) as exc:
                        output = {'error':str(exc)}
                    messages.append({'role':'tool','tool_call_id':call['id'],'content':json.dumps(output,ensure_ascii=False)})
        raise BudgetExceeded('model step budget reached')
    monkeypatch.setattr(harness_runtime,'run',run)
