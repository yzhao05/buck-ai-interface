import base64
import asyncio
import io
import json
import logging
import os
import uuid

import httpx
import pytest
from PIL import Image, ImageDraw, ImageFont
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend import chat, main
from backend.archive import ArchiveStore, SavedConversation
from backend.schemas import ChatRequest
from backend.vision import ChatImage, provider_message
from backend import harness_runtime
from backend.agent_policy import Budget
from conftest import REAL_RUN


@pytest.mark.skipif(os.getenv('BUCK_LIVE_VISION_TEST') != '1', reason='requires an explicitly enabled live provider')
@pytest.mark.parametrize('case', ['text', 'probe-png', 'studio-probe-list'])
def test_fresh_harness_turn(case, monkeypatch):
    monkeypatch.setattr(harness_runtime, 'run', REAL_RUN)
    monkeypatch.setattr(main, 'TOKEN', '')
    expected = 'TEXT_OK'
    request = {'message': 'Reply with exactly TEXT_OK.'}
    if case != 'text':
        expected = 'Vout_74'
        image = Image.new('RGB', (340, 144), 'white')
        label = 'Probe: ' + expected if case == 'probe-png' else 'Probe\n1. Vout_74\n2. IL_59'
        ImageDraw.Draw(image).multiline_text((12, 10), label, fill='black', font=ImageFont.load_default(size=26))
        stream = io.BytesIO()
        image.save(stream, format='PNG')
        attachment = ChatImage(name='probe.png', data_url='data:image/png;base64,' + base64.b64encode(stream.getvalue()).decode())
        request = {'message':'Read the Probe signal name. Return only its name.', 'images':[attachment.model_dump()]}
        if case == 'studio-probe-list':
            monkeypatch.setattr(harness_runtime, 'hosted', lambda: True)
            request['message'] = '请读出当前图片 Probe 列表里的两路信号，按顺序回答。'
            request['history'] = [{'role':'user','content':'上一张参考图。','images':[picture()]}]
    response = TestClient(main.app).post('/api/chat', headers={'X-Session-ID': str(uuid.uuid4())}, json=request)
    response.raise_for_status()
    result = response.json()
    assert result['mode'] == 'deepseek' and expected in result['answer']
    if case == 'studio-probe-list':
        assert result['answer'].index(expected) < result['answer'].index('IL_59')
    assert result['agent_budget']['model_calls'] > 0


def test_rpc_diagnostic_omits_private_content():
    assert harness_runtime.rpc_diagnostic('ENOENT: cannot find module sharp at /private/input.png; key=secret data:image/png;base64,aGVsbG8=') == 'sharp, cannot find module, ENOENT'
    assert harness_runtime.rpc_diagnostic('secret prompt or provider token') == 'unclassified RPC failure'


def test_image_recovery_requires_marker_in_current_user_message():
    with harness_runtime.Gateway([], None, Budget(False), lambda *_: None) as gateway:
        gateway.image_marker = '\n[test-marker]'
        gateway.image_blocks = [{'type':'image', 'source':{'type':'base64','media_type':'image/png','data':'AA=='}}]
        gateway.require_image_marker = True
        with pytest.raises(harness_runtime.HarnessTurnError):
            gateway.restore_images({'messages':[
                {'role':'user', 'content':'older' + gateway.image_marker},
                {'role':'user', 'content':'current request without attachment marker'}]})
        message = {'role':'user', 'content':[{'type':'text','text':'probe'}, {'type':'text','text':gateway.image_marker}]}
        gateway.restore_images({'messages':[message]})
        assert message['content'] == [{'type':'text','text':'probe'}, *gateway.image_blocks]


def test_studio_access_log_omits_query_token():
    record = logging.makeLogRecord({'msg':'%s %s %s %s %s', 'args':('client','GET','/?studio_token=test-secret','1.1',200)})
    assert main.omit_access_query(record)
    assert record.getMessage() == 'client GET / 1.1 200'


@pytest.mark.parametrize('hosted', [False, True])
def test_unavailable_image_store_still_forwards_pixels(tmp_path, monkeypatch, caplog, hosted):
    home = tmp_path / 'sessions'
    monkeypatch.setattr(harness_runtime, 'ROOT', home)
    monkeypatch.setattr(harness_runtime, 'hosted', lambda: hosted)
    monkeypatch.setenv('DEEPSEEK_API_KEY', 'local-test-secret')
    monkeypatch.setenv('HARNESS_BASE_URL', 'http://127.0.0.1:1')
    session = uuid.uuid4().hex
    (home / session).mkdir(parents=True)
    (home / session / 'attachments').touch()  # Force a real attachment-admission failure.
    image = picture()
    sent = []
    def reject(request):
        messages = json.loads(request.content)['messages']
        sent.extend(block['source'] for msg in messages if isinstance(msg['content'], list)
                    for block in msg['content'] if block['type'] == 'image')
        return httpx.Response(418)
    client = httpx.Client
    monkeypatch.setattr(harness_runtime.httpx, 'Client', lambda **kw: client(transport=httpx.MockTransport(reject), **kw))
    token = harness_runtime.scope.set({'id':session})
    try:
        with pytest.raises(httpx.HTTPStatusError) as failure:
            asyncio.run(REAL_RUN([provider_message('user', 'private test prompt', [image])], [], None, Budget(False)))
    finally:
        harness_runtime.scope.reset(token)
    assert ('stage=prompt provider_stage=not_reached rpc_code=' in caplog.text) == (not hosted)
    assert failure.value.response.status_code == 418
    assert sent == [{'type':'base64', 'media_type':'image/png', 'data':image['data_url'].split(',')[1]}]
    assert all(secret not in caplog.text for secret in (image['data_url'].split(',')[1], 'private test prompt', 'local-test-secret'))


def picture(fmt="PNG"):
    stream = io.BytesIO()
    Image.new("RGB", (20, 10), "white").save(stream, format=fmt)
    mime = {"PNG": "png", "JPEG": "jpeg", "GIF": "gif", "WEBP": "webp"}[fmt]
    return {"name": "circuit." + mime,
            "data_url": f"data:image/{mime};base64," + base64.b64encode(stream.getvalue()).decode()}


@pytest.mark.parametrize("fmt", ["PNG", "JPEG", "GIF", "WEBP"])
def test_image_formats(fmt):
    image = ChatImage(**picture(fmt))
    assert provider_message("user", "read", [image])["content"][2]["image_url"]["url"] == image.data_url


@pytest.mark.parametrize("url", ["https://example.com/a.png", "data:image/svg+xml;base64,PHN2Zy8+",
                                 "data:image/png;base64,@@@@", "data:image/png;base64,aGVsbG8=",
                                 "data:image/jpeg;base64," + picture()["data_url"].split(",")[1], "x"*360001],
                         ids=["external-url", "svg", "base64", "not-image", "mime-mismatch", "too-large"])
def test_invalid_images_rejected(url):
    with pytest.raises(ValidationError):
        ChatImage(name="image", data_url=url)


def test_image_count_and_role_limits():
    with pytest.raises(ValidationError):
        ChatRequest(message="read", images=[picture()]*4)
    with pytest.raises(ValidationError):
        ChatRequest(message="read", history=[{"role": "assistant", "content": "text", "images": [picture()]}])
    assert provider_message("user", "text") == {"role": "user", "content": "text"}


@pytest.mark.parametrize("cut", [8, 16])
def test_truncated_png_returns_validation_error_not_server_error(monkeypatch, cut):
    monkeypatch.setattr(main, "TOKEN", "")
    image = picture()
    raw = base64.b64decode(image["data_url"].split(",")[1])
    image["data_url"] = "data:image/png;base64," + base64.b64encode(raw[:-cut]).decode()
    response = TestClient(main.app).post("/api/chat", headers={"X-Session-ID": str(uuid.uuid4())},
                                         json={"message": "read image", "images": [image]})
    assert response.status_code == 422


@pytest.mark.parametrize("failure", [False, True])
def test_provider_receives_actual_image_parts(monkeypatch, failure):
    monkeypatch.setattr(main, "TOKEN", "")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-only")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-flash")
    seen = []
    class Provider:
        def __init__(self, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def post(self, url, **kwargs):
            payload = kwargs["json"]
            seen.append(payload)
            assert payload["model"] == "deepseek-flash"
            assert "不会传给此问答模型" not in payload["messages"][0]["content"]
            users = [m for m in payload["messages"] if m["role"] == "user"]
            assert users[-1]["content"][0] == {"type": "text", "text": "读一下电路图"}
            assert users[-1]["content"][2]["image_url"]["url"] == picture()["data_url"]
            assert users[-2]["content"][2]["type"] == "image_url"
            assert all("images" not in m for m in payload["messages"])
            assert "start_identification" not in json.dumps(payload["tools"])
            if failure:
                raise httpx.ReadTimeout("test unavailable")
            return httpx.Response(200, request=httpx.Request("POST", url), json={"choices": [{"message": {"role": "assistant", "content": "已收到图片。"}}]})
    monkeypatch.setattr(chat.httpx, "AsyncClient", Provider)
    client = TestClient(main.app)
    response = client.post("/api/chat", headers={"X-Session-ID": str(uuid.uuid4())}, json={
        "message": "读一下电路图", "images": [picture()],
        "history": [{"role": "user", "content": "之前的图", "images": [picture()]}]})
    assert response.status_code == 200
    assert len(seen) == 1
    assert not response.json().get("job_id")
    assert (response.json()["mode"] == "provider_error") == failure


@pytest.mark.parametrize("message", ["看看图片", "识别图片中的电感电容", "Identify the components in this image", "开始参数辨识"])
def test_no_key_is_explicit_about_no_vision(monkeypatch, message):
    monkeypatch.setattr(main, "TOKEN", "")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    def forbidden_fit(*args, **kwargs):
        pytest.fail("Images must never implicitly launch numerical identification")
    monkeypatch.setattr(main, "start_fit", forbidden_fit)
    response = TestClient(main.app).post("/api/chat", headers={"X-Session-ID": str(uuid.uuid4())},
                                         json={"message": message, "images": [picture()], "identification_enabled": True})
    assert response.status_code == 200
    assert response.json()["answer"] == "" and response.json()["error_code"] == "missing_api_key"


def test_images_survive_archive_roundtrip(tmp_path):
    identifier = str(uuid.uuid4())
    record = SavedConversation(id=identifier, updated=1, messages=[{
        "id": "image-message", "created": 1, "role": "user", "content": "read the diagram", "images": [picture()]}])
    store = ArchiveStore(tmp_path)
    store.merge("owner", record.model_dump(mode="json"))
    restored = store.get("owner", identifier)["record"]
    assert restored["messages"][0]["images"] == [picture()]
    assert SavedConversation.model_validate(restored).messages[0].images[0].data_url == picture()["data_url"]


def test_english_image_fallback(monkeypatch):
    monkeypatch.setattr(main, "TOKEN", "")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    response = TestClient(main.app).post("/api/chat", headers={"X-Session-ID": str(uuid.uuid4())},
                                         json={"message": "read image", "language": "en", "images": [picture()]})
    assert response.status_code == 200
    assert response.json()["provider_error"].startswith("AI service is not configured")
