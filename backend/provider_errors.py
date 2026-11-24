"""Public provider failures. Never expose provider bodies, credentials or prompts."""
import errno
import logging
import socket
import ssl
import httpx


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


def network_reason(exc):
    """Use typed causes only: exception text can contain proxy credentials."""
    while exc is not None:
        if isinstance(exc, httpx.ProxyError):
            return 'proxy'
        if isinstance(exc, socket.gaierror):
            return 'dns'
        if isinstance(exc, ssl.SSLCertVerificationError):
            return 'tls_certificate'
        if isinstance(exc, ssl.SSLError):
            return 'tls'
        if isinstance(exc, OSError) and exc.errno in errno.errorcode:
            return errno.errorcode[exc.errno]
        exc = exc.__cause__
    return 'unspecified'


def provider_failure(exc=None, *, missing=False, key_name="DEEPSEEK_API_KEY", **metadata):
    if missing:
        code = "missing_api_key"
        message = f"AI service is not configured. Please set {key_name} on the backend and try again."
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
        message = 'The generated answer cited knowledge sources without saved full-text search or read evidence in this session. '
        message += 'The answer could not be verified; this is not an API-key or network error.'
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
        'Agent failure: code=%s exception=%s cause=%s http_status=%s provider=%s network_reason=%s', code,
        type(exc).__name__ if exc else 'none',
        type(exc.__cause__).__name__ if exc and exc.__cause__ else 'none',
        exc.response.status_code if isinstance(exc,httpx.HTTPStatusError) else 'none',
        ('openai' if metadata.get('agent_budget', {}).get('pricing_model') == 'gpt-6.1-terra'
         else 'deepseek' if metadata.get('agent_budget') else 'unknown'),
        network_reason(exc) if isinstance(exc, httpx.TransportError) else 'none')
    return {**metadata, "mode": "provider_error", "answer": "", "provider_error": message,
            "error_code": code, "ui_action": None, "sources": metadata.get("sources", []),
            "tool_calls": metadata.get("tool_calls", []), "job_id": metadata.get("job_id")}
