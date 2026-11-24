import asyncio
import base64
import io
import json
import socket
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest
from PIL import Image

from backend.agent_policy import Budget
from backend.provider_errors import HarnessTurnError, provider_failure
from backend import chat, harness_runtime, main, manual_import, terra_provider
from backend.schemas import ChatRequest
from conftest import REAL_RUN


@pytest.mark.parametrize(("mode", "effort"), [("fast", "low"), ("thinking", "high")])
def test_terra_picker_routes_real_harness_tools_and_text(monkeypatch, mode, effort):
    requests = []
    answer = "Verified [kb:ui-overview]"
    # The simulated Responses endpoint is a loopback server, even on hosts with a system proxy.
    monkeypatch.setenv("no_proxy", "localhost,127.0.0.1")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("DEEPSEEK_MODEL", "")
    monkeypatch.setenv("OPENAI_API_KEY", "local-test")
    monkeypatch.setattr(harness_runtime, "run", REAL_RUN)
    assert main.health()["chat_models"] == ["gpt-6.1-terra"]
    assert main.health()["model"] == "gpt-6.1-terra"

    # Two valid attachments total over 500 kB on the wire. The old USD 0.50
    # reservation rejected this request before the provider saw either image.
    images = []
    if mode == "thinking":
        monkeypatch.setattr(harness_runtime, "hosted", lambda: True)
        for colour in ("red", "blue"):
            png = io.BytesIO()
            Image.new("RGB", (256, 256), colour).save(png, format="PNG", compress_level=0)
            url = "data:image/png;base64," + base64.b64encode(png.getvalue()).decode("ascii")
            images.append({"name": colour + ".png", "data_url": url})

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            assert self.path == "/v1/responses"
            assert self.headers["Authorization"] == "Bearer local-test"
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            frames = [{"type": "response.created", "response": {"id": "resp_fixture"}}]
            if len(requests) == 1:
                frames.extend(
                    [
                        {
                            "type": "response.output_item.done",
                            "item": {
                                "type": "reasoning",
                                "id": "rs_fixture",
                                "summary": [],
                                "encrypted_content": "opaque",
                            },
                        },
                        {
                            "type": "response.output_item.done",
                            "item": {
                                "type": "function_call",
                                "call_id": "call_probe",
                                "name": "mcp__buck__read_knowledge",
                                "arguments": json.dumps({"ids": ["ui-overview"]}),
                            },
                        },
                    ]
                )
            else:
                frames.extend(
                    [
                        {"type": "response.content_part.added", "part": {"type": "output_text"}},
                        {"type": "response.output_text.delta", "delta": answer},
                        {"type": "response.content_part.done", "part": {"type": "output_text"}},
                    ]
                )
            frames.append(
                {
                    "type": "response.completed",
                    "response": {
                        "usage": {
                            "input_tokens": 100,
                            "input_tokens_details": {"cached_tokens": 20},
                            "output_tokens": 10,
                        }
                    },
                }
            )
            if mode == "fast" and len(requests) == 1:
                # An incomplete usage report must not borrow the SDK's placeholder zero.
                del frames[-1]["response"]["usage"]["input_tokens"]
            data = "".join("data: " + json.dumps(frame) + "\n\n" for frame in frames).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    async def no_experiment(*args):
        raise AssertionError("Knowledge lookup should not start experiments")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    monkeypatch.setattr(terra_provider, "ENDPOINT", f"http://127.0.0.1:{server.server_port}/v1/responses")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = asyncio.run(
            chat.answer(
                ChatRequest(message="Read the UI overview", chat_model="gpt-6.1-terra", reasoning_mode=mode, images=images),
                no_experiment
            )
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert result["mode"] == "gpt-6.1-terra", result
    assert result["answer"] == answer and result["sources"][0]["id"] == "ui-overview"
    assert result["agent_budget"]["unsettled_model_calls"] == (1 if mode == "fast" else 0)
    assert result["agent_budget"]["thinking"] is True
    assert result["agent_budget"]["reasoning_effort"] == effort
    assert result["agent_budget"]["reasoning_mode"] == mode
    assert len(requests) == 2 and all(r["model"] == "gpt-6.1-terra" and r["store"] is False for r in requests)
    assert all("max_output_tokens" not in r for r in requests)
    assert all(r["reasoning"]["effort"] == effort for r in requests)
    assert any(t["name"] == "mcp__buck__read_knowledge" for t in requests[0]["tools"])
    if images:
        urls = [part["image_url"] for item in requests[0]["input"] if isinstance(item.get("content"), list)
                for part in item["content"] if part["type"] == "input_image"]
        assert urls == [image["data_url"] for image in images]
        assert sum(map(len, urls)) > 500_000
    items = requests[-1]["input"]
    assert any(i.get("encrypted_content") == "opaque" for i in items)
    assert any(i.get("type") == "function_call_output" and "ui-overview" in i["output"] for i in items)


def test_terra_image_and_incomplete_response():
    body = {
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "read the graph"},
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": "image/png", "data": "image-bytes"},
                    },
                ],
            }
        ],
        "thinking": {"type": "disabled"},
        "max_tokens": 1024,
    }
    payload = terra_provider.payload(body, {}, "low")
    assert payload["input"][1]["content"][0]["image_url"] == "data:image/png;base64,image-bytes"
    assert "max_output_tokens" not in payload
    assert payload["reasoning"] == {"effort": "low"}
    error = {
        "type": "response.incomplete",
        "response": {"incomplete_details": {"reason": "max_output_tokens"}},
    }
    response = httpx.Response(200, text="data: " + json.dumps(error))
    with pytest.raises(HarnessTurnError) as failure:
        list(terra_provider.messages_stream(response, {}))
    assert failure.value.reason == "max-tokens"


def test_model_switch_reuses_receipts_and_checks_old_manual_citation(monkeypatch):
    for key in ("OPENAI_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.setenv(key, "offline-fixture")
    calls = []
    key = "pdf-" + "d"*64 + "-1"
    doc = {"id":key, "title":"Inductor", "content":"PDF p. 2: 50 uH"}

    async def invoke(name, args):
        if name == "get_plecs_models":
            return {"models":{"buck":{"source_sha256":"v1"}}}
        calls.append(name)
        return {"status":"completed"}

    async def run(messages, tools, execute, budget):
        if budget.chat_model == "deepseek":
            await execute("read_knowledge", {"ids":[key]})
        await execute("get_plecs_models", {})
        await execute("run_plecs_experiment", {"model":"buck"})
        await execute("evaluate_plecs_experiment", {"experiment_id":"exp1"})
        return "[kb:"+key+"]"

    monkeypatch.setattr(harness_runtime, "run", run)
    monkeypatch.setattr(manual_import, "corpus", lambda: ([doc.copy()], {}))
    token = harness_runtime.scope.set({"owner":"owner-A"})
    try:
        def ask(model):
            return asyncio.run(chat.answer(ChatRequest(message="Check", chat_model=model), invoke))
        assert ask("deepseek")["mode"] == "deepseek"
        result = ask("gpt-6.1-terra")
        assert result["mode"] == "gpt-6.1-terra" and result["sources"] == [doc]
        assert result["plecs_experiments"][0]["deduplicated"] and result["simulation_evaluations"][0]["cached"]
        doc["content"] = "PDF p. 2: no verified samples"
        assert ask("gpt-6.1-terra")["error_code"] == "citation_validation_failed"
        assert calls == ["run_plecs_experiment", "evaluate_plecs_experiment"]
    finally:
        harness_runtime.scope.reset(token)


def test_terra_chat_uses_deployment_https_proxy(monkeypatch, caplog):
    connections = []

    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_CONNECT(self):
            connections.append((self.path, self.headers.get("Authorization")))
            self.send_error(502, "proxy-test-detail")

    # Both endpoints are local. Only the CONNECT request indicates proxy routing.
    with ThreadingHTTPServer(("127.0.0.1", 0), Proxy) as server:
        target = f"127.0.0.1:{server.server_port}"
        monkeypatch.setenv("OPENAI_API_KEY", "test-key")
        monkeypatch.delenv("https_proxy", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)
        monkeypatch.setenv("NO_PROXY", "")
        monkeypatch.setenv("HTTPS_PROXY", "http://" + target)
        monkeypatch.setattr(terra_provider, "ENDPOINT", f"https://{target}/v1/responses")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        budget = Budget(False, "gpt-6.1-terra")
        try:
            with harness_runtime.Gateway([], None, budget, lambda *_: None) as gateway, httpx.Client(trust_env=False) as local:
                response = local.post(gateway.url + "/v1/messages", headers={"Authorization": "Bearer " + gateway.secret},
                                      json={"messages": [{"role": "user", "content": "Hello"}]})
                assert response.status_code == 502
                assert isinstance(gateway.error, httpx.ProxyError)
                failure = provider_failure(gateway.error, agent_budget=budget.metadata())
        finally:
            server.shutdown()
            thread.join()
    assert connections == [(target, None)]
    assert failure["error_code"] == "provider_connection_failed"
    assert "provider=openai network_reason=proxy" in caplog.text
    assert "proxy-test-detail" not in caplog.text and "test-key" not in caplog.text


@pytest.mark.parametrize(("cause", "reason"), [
    (socket.gaierror(-2, "private-diagnostic"), "dns"),
    (ssl.SSLCertVerificationError(1, "private-diagnostic"), "tls_certificate"),
])
def test_connection_log_classifies_nested_causes_without_logging_text(caplog, cause, reason):
    wrapped = RuntimeError("private-diagnostic")
    wrapped.__cause__ = cause
    error = httpx.ConnectError("private-diagnostic")
    error.__cause__ = wrapped
    provider_failure(error, agent_budget=Budget(False, "gpt-6.1-terra").metadata())
    assert "provider=openai network_reason=" + reason in caplog.text
    assert "private-diagnostic" not in caplog.text
