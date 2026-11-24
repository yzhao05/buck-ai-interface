import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import threading
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
import httpx
import pytest

from backend import manual_import, openai_companion, openai_relay as relay, terra_provider


@pytest.fixture
def studio(monkeypatch):
    monkeypatch.setenv("OPENAI_RELAY_TOKEN", "a" * 32)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(relay, "worker", None)
    monkeypatch.setattr(relay, "last_contact", 0.)
    monkeypatch.setattr(relay, "lease_seconds", 5)
    app = FastAPI()
    app.include_router(relay.router)
    with TestClient(app, headers={"X-Buck-Relay-Token": "a" * 32, "X-Relay-Instance": "b" * 32}) as client:
        yield client
    assert not relay.jobs


def claim(studio):
    for _ in range(100):
        job = studio.post(openai_companion.PREFIX + "/claim").json()
        if job:
            return job
        time.sleep(.01)
    pytest.fail("Request never reached the connector")


@pytest.mark.parametrize("kind", ["pdf", "image"])
def test_connector_turn(studio, kind, monkeypatch):
    assert studio.post(openai_companion.PREFIX + "/claim").json() == {}
    calls = []
    attachment = {"type": "input_file", "filename": "test.pdf", "file_data": "data:application/pdf;base64,eA=="}
    image = {"type": "input_image", "image_url": "data:image/png;base64,eA=="}
    def upstream(request):
        calls.append(json.loads(request.content))
        assert str(request.url) == openai_companion.ENDPOINT
        assert request.headers["Authorization"] == "Bearer local-test-key"
        assert "X-Buck-Relay-Token" not in request.headers
        assert calls[0]["store"] is False
        assert calls[0]["input"][0]["content"] == [attachment if kind == "pdf" else image]
        if kind == "pdf":
            return httpx.Response(200, json={"status":"completed","output":[{"content":[
                {"type":"output_text","text":json.dumps({"manual":"x" * 400000})}]}]})
        return httpx.Response(200, content=b'data: {"type":"response.completed","response":{"usage":{}}}\n\n')

    # Lose the first frame acknowledgement after the Studio has accepted the frame.
    original_post = studio.post
    attempts = []
    def post(path, **kwargs):
        result = original_post(path, **kwargs)
        if path.endswith("/reply") and kwargs["params"]["seq"] == 0:
            attempts.append(0)
            if len(attempts) == 1:
                raise httpx.ReadError("lost frame acknowledgement")
        return result
    monkeypatch.setattr(studio, "post", post)

    def submit():
        if kind == "pdf":
            return asyncio.run(manual_import.request_json("extract", [attachment]))
        with terra_provider.stream(None, {"store":False,"stream":True,"input":[{"role":"user","content":[image]}]}, {}) as result:
            return result.read()
    with ThreadPoolExecutor(1) as pool, httpx.Client(transport=httpx.MockTransport(upstream)) as provider:
        pending = pool.submit(submit)
        openai_companion.forward(studio, provider, "local-test-key", claim(studio))
        value = pending.result(5)
    if kind == "pdf":
        assert value == {"manual":"x" * 400000}
    else:
        assert b"message_stop" in value
    assert len(calls) == 1
    assert len(attempts) == 2


def test_worker_auth_disconnect_and_no_replay(studio):
    path = openai_companion.PREFIX
    with pytest.raises(relay.RelayUnavailable):
        relay.request_json({"store": False})
    assert studio.post(path+"/claim", headers={"X-Buck-Relay-Token":"incorrect"}).status_code == 403
    assert studio.post(path+"/claim").json() == {}
    assert studio.post(path+"/claim", headers={"X-Relay-Instance":"c"*32}).status_code == 409
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(relay.request_json, {"store":False})
        job = claim(studio)
        url = path + "/" + job["id"]
        assert studio.post(url+"/reply?status=200&seq=2",content=b"gap").status_code == 409
        assert studio.post(url+"/reply?status=200&seq=0",content=b"partial").is_success
        assert studio.post(url+"/failed").is_success
        with pytest.raises(relay.RelayUnavailable):
            future.result(5)
        assert studio.post(url+"/heartbeat").status_code == 410
        assert studio.post(path+"/claim").json() == {}


def test_invalid_relay_configuration_masks_direct_key(studio, monkeypatch):
    from backend.main import health
    monkeypatch.setenv("OPENAI_RELAY_TOKEN", "short")
    monkeypatch.setenv("OPENAI_API_KEY", "direct-test-key")
    assert health()["openai_connector"] == "invalid_token"
    assert "gpt-6.1-terra" not in health()["chat_models"]
    with pytest.raises(relay.RelayUnavailable, match="32 characters"):
        relay.request_json({"store": False})


def test_completion_survives_late_failure(studio):
    path = openai_companion.PREFIX
    studio.post(path + "/claim")
    can_read = threading.Event()

    def consume():
        with relay.response({"store": False}) as response:
            assert can_read.wait(5)
            return response.read()

    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(consume)
        url = path + "/" + claim(studio)["id"]
        try:
            assert studio.post(url + "/reply?status=200&seq=0&end=true", content=b"done").is_success
            assert studio.post(url + "/failed").is_success
        finally:
            can_read.set()
        assert future.result(3) == b"done"


def test_queued_request_can_be_cancelled(studio):
    studio.post(openai_companion.PREFIX+"/claim")
    closed = threading.Event()
    def wait():
        with relay.response({"store": False}, closed):
            pytest.fail("Cancelled request was accepted")
    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(wait)
        job = claim(studio)
        closed.set()
        with pytest.raises(relay.RelayUnavailable):
            future.result(3)
        assert studio.post(openai_companion.PREFIX+"/"+job["id"]+"/heartbeat").status_code == 410


def test_stream_arrives_before_completion_and_duplicate_frames_are_ignored(studio):
    assert relay.status() == "offline"
    studio.post(openai_companion.PREFIX+"/claim")
    assert relay.status() == "online"
    seen = threading.Event()

    def consume():
        with relay.response({"store":False}) as response:
            chunks = response.iter_bytes()
            assert next(chunks) == b"first"
            seen.set()
            return b"".join(chunks)

    with ThreadPoolExecutor(1) as pool:
        future = pool.submit(consume)
        url = openai_companion.PREFIX + "/" + claim(studio)["id"] + "/reply?status=200"
        assert studio.post(url+"&seq=0",content=b"first").is_success
        assert seen.wait(2)
        assert studio.post(url+"&seq=0",content=b"first").is_success
        assert studio.post(url+"&seq=1&end=true",content=b"last").is_success
        assert future.result(3) == b"last"


def test_upstream_unreachable_and_http_errors(studio):
    studio.post(openai_companion.PREFIX+"/claim")
    def denied(request):
        return httpx.Response(401, text="sensitive upstream error body")

    def offline(request):
        raise httpx.ConnectError("fixture", request=request)

    for handler in (denied, offline):
        with ThreadPoolExecutor(1) as pool, httpx.Client(transport=httpx.MockTransport(handler)) as upstream:
            future = pool.submit(relay.request_json, {"store":False})
            job = claim(studio)
            if handler is denied:
                openai_companion.forward(studio, upstream, "test", job)
                with pytest.raises(httpx.HTTPStatusError) as err:
                    future.result(3)
                assert err.value.response.status_code == 401
                assert "sensitive upstream" not in str(err.value)
            else:
                with pytest.raises(httpx.ConnectError):
                    openai_companion.forward(studio, upstream, "test", job)
                with pytest.raises(relay.RelayUnavailable, match="reach OpenAI"):
                    future.result(3)
