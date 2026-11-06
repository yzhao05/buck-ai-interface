"""Public provider failures. Never expose provider bodies, credentials or prompts."""
import httpx
from .agent_policy import BudgetExceeded


class ProviderUnavailable(RuntimeError):
    pass


def provider_failure(exc=None, *, missing=False, **metadata):
    if missing:
        code = "missing_api_key"
        message = "AI service is not configured. Please set DEEPSEEK_API_KEY on the backend and try again."
    elif isinstance(exc, BudgetExceeded):
        code = "agent_budget_exceeded"
        message = "AI request reached its time, cost or tool-call limit. Completed work has been preserved. Please narrow the request and try again."
    else:
        code = "provider_failed"
        status = f" (HTTP {exc.response.status_code})" if isinstance(exc, httpx.HTTPStatusError) else ""
        message = f"AI request failed{status}. Please check the API key, model settings and network connection, then try again."
    return {**metadata, "mode": "provider_error", "answer": "", "provider_error": message,
            "error_code": code, "ui_action": None, "sources": metadata.get("sources", []),
            "tool_calls": metadata.get("tool_calls", []), "job_id": metadata.get("job_id")}
