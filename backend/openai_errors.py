"""Project safe diagnostics from Responses errors; upstream messages can echo user input."""
import json
import re


MAX_ERROR_BYTES = 16384
JSON_MODE_ERROR = "JSON object mode requires an explicit JSON instruction in the input messages."
_PARAM = re.compile(r"(?:input|messages|text|model|reasoning|tools|max_output_tokens|temperature|store)"
                    r"(?:(?:\[\d{1,4}\])|(?:\.(?:\d{1,4}|content|text|type|image_url|detail|format|effort|name|schema)))*")


def bounded_body(response):
    data = bytearray()
    for chunk in response.iter_bytes(chunk_size=4096):
        data.extend(chunk)
        if len(data) >= MAX_ERROR_BYTES:
            break
    return bytes(data[:MAX_ERROR_BYTES])


def safe_error(data, request_id=None):
    try:
        record = json.loads(data[:MAX_ERROR_BYTES])
    except (ValueError, UnicodeError, RecursionError):
        record = {}
    error = record.get("error") if isinstance(record, dict) else None
    error = error if isinstance(error, dict) else {}
    safe = {}
    for field in ("code", "type"):
        value = error.get(field)
        if isinstance(value, str) and re.fullmatch(r"[a-z][a-z_]{0,63}", value):
            safe[field] = value
    param = error.get("param")
    if isinstance(param, str) and len(param) <= 120 and _PARAM.fullmatch(param):
        safe["param"] = param
    message = str(error.get("message", "")).lower()
    if "json" in message and ("must contain" in message or JSON_MODE_ERROR.lower() == message):
        safe["message"] = JSON_MODE_ERROR
    for phrase, summary in (
        ("unsupported parameter", "Unsupported parameter."),
        ("invalid image", "Invalid image."),
        ("invalid base64", "Invalid base64 image data."),
        ("context length", "Context length limit exceeded."),
    ):
        if phrase in message:
            safe["message"] = summary
            break
    rid = request_id or error.get("request_id")
    if isinstance(rid, str) and re.fullmatch(r"req[-_][a-fA-F0-9-]{8,80}", rid):
        safe["request_id"] = rid
    return safe


def public_http_error(response):
    error = safe_error(response.content, response.headers.get("x-request-id"))
    message = error.pop("message", "")
    fields = " ".join(f"{key}={value}" for key, value in error.items())
    return " ".join(filter(None, (f"OpenAI HTTP {response.status_code}", message, fields)))
