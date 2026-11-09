"""Public provider failures. Never expose provider bodies, credentials or prompts."""
import logging
import httpx
from .agent_policy import BudgetExceeded


class HarnessTurnError(RuntimeError):
    def __init__(self, reason):
        self.reason = reason if reason in {'max-tokens', 'empty', 'error', 'aborted', 'refusal'} else 'incomplete'
        super().__init__('Harness turn did not complete')


class AgentCapacityError(RuntimeError):
    pass


class AgentMemoryError(RuntimeError):
    pass


class SessionRestoreError(RuntimeError):
    pass


class CitationValidationError(ValueError):
    pass


def provider_failure(exc=None, *, missing=False, **metadata):
    if missing:
        code = "missing_api_key"
        message = "AI service is not configured. Please set DEEPSEEK_API_KEY on the backend and try again."
    elif isinstance(exc, AgentMemoryError):
        code = 'agent_memory_pressure'
        message = 'The backend has too little memory available for this analysis. The task can be resumed when memory is available.'
    elif isinstance(exc, AgentCapacityError):
        code = 'agent_capacity_busy'
        message = 'The backend Agent capacity is busy. Wait for the active task to finish and retry.'
    elif isinstance(exc, SessionRestoreError):
        code = 'harness_session_unavailable'
        message = ('The saved Agent session is missing. This task already attempted tools. Check the saved results before starting another task.'
                   if str(exc)=='missing_after_tools' else 'The backend could not restore the saved Harness session. Check the backend logs before retrying.')
    elif isinstance(exc, HarnessTurnError):
        metadata['finish_reason'] = exc.reason
        code = 'output_token_limit' if exc.reason == 'max-tokens' else 'harness_incomplete'
        message = ('AI generation reached its output-token limit, including thinking tokens, before completing the answer.'
                   if exc.reason == 'max-tokens' else 'The Harness did not return a complete answer. The task can be retried.')
    elif isinstance(exc, CitationValidationError):
        code = 'citation_validation_failed'
        message = 'The generated answer cited knowledge sources without saved retrieval evidence in this session. '
        message += 'The answer could not be verified; this is not an API-key or network error.'
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
    elif isinstance(exc, httpx.HTTPStatusError):
        code = "provider_failed"
        status = exc.response.status_code
        detail = ('Check the backend API key and model access.' if status in (401,403) else
                  'The provider rate limit was reached. Retry later.' if status == 429 else 'Check the provider status and backend logs.')
        message = f"AI request failed (HTTP {status}). {detail}"
    elif isinstance(exc, httpx.TransportError):
        code = 'provider_connection_failed'
        message = 'The backend could not communicate with the AI service. Check the backend network and provider endpoint.'
    else:
        code = "provider_failed"
        message = "The backend could not finish the Agent task. Check the backend logs for the Agent failure."
    logging.getLogger('uvicorn.error').warning(
        'Agent failure: code=%s exception=%s cause=%s http_status=%s', code,
        type(exc).__name__ if exc else 'none',
        type(exc.__cause__).__name__ if exc and exc.__cause__ else 'none',
        exc.response.status_code if isinstance(exc,httpx.HTTPStatusError) else 'none')
    return {**metadata, "mode": "provider_error", "answer": "", "provider_error": message,
            "error_code": code, "ui_action": None, "sources": metadata.get("sources", []),
            "tool_calls": metadata.get("tool_calls", []), "job_id": metadata.get("job_id")}
