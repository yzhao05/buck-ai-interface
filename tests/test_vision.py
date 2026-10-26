import base64
import io
import json
import uuid

import httpx
import pytest
from PIL import Image
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend import chat, main
from backend.archive import ArchiveStore, SavedConversation
from backend.schemas import ChatRequest
from backend.vision import ChatImage, provider_message


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
    assert ("图片尚未识别" in response.json()["answer"]) == failure


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
    assert "图片尚未识别" in response.json()["answer"]


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
    assert response.json()["answer"].startswith("Images have not been analyzed")
