"""Public provider failures. Never expose provider bodies, credentials or prompts."""
import httpx
from .agent_policy import BudgetExceeded


def provider_failure(exc=None, *, missing=False, **metadata):
    if missing:
        code = "missing_api_key"
        message = "AI service is not configured. Please set DEEPSEEK_API_KEY on the backend and try again."
    elif isinstance(exc, BudgetExceeded):
        code = "agent_budget_exceeded"
        reason = {'time budget reached': 'time', 'model step budget reached': 'model_calls',
                  'estimated cost budget reached': 'estimated_cost'}.get(str(exc), 'unknown')
        metadata['budget_reason'] = reason
        detail = {
            'time': 'AI request reached the backend time limit (AGENT_TIME_LIMIT_S).',
            'model_calls': 'AI request reached the model-request limit, including retries and compaction.',
            'estimated_cost': 'AI request exceeded the estimated cost reservation (AGENT_COST_LIMIT_USD). '
                              'This is a local estimate, not a provider charge or account-balance error.',
        }.get(reason, 'AI request reached a backend resource limit.')
        budget = metadata.get('agent_budget', {})
        progress = (f" Model requests admitted: {budget['model_calls']}; elapsed: {budget.get('elapsed_s', 0)}s."
                    if 'model_calls' in budget else '')
        message = detail + progress + ' Available task evidence has been retained. Check backend limits and retry.'
    elif isinstance(exc, (TimeoutError, httpx.TimeoutException)):
        code = 'provider_timeout'
        message = 'AI provider or Harness communication timed out before completion. Check backend connectivity and retry.'
    else:
        code = "provider_failed"
        status = f" (HTTP {exc.response.status_code})" if isinstance(exc, httpx.HTTPStatusError) else ""
        message = f"AI request failed{status}. Please check the API key, model settings and network connection, then try again."
    return {**metadata, "mode": "provider_error", "answer": "", "provider_error": message,
            "error_code": code, "ui_action": None, "sources": metadata.get("sources", []),
            "tool_calls": metadata.get("tool_calls", []), "job_id": metadata.get("job_id")}
