import base64
import json
from io import BytesIO

from reportlab.pdfgen.canvas import Canvas
from uuid import uuid4

import httpx
from fastapi.testclient import TestClient

from backend import knowledge, manual_import
from backend.main import app, APP_TOKEN_HEADER, TOKEN


def test_manual_upload_review_and_shared_lookup(monkeypatch, tmp_path):
    monkeypatch.setenv("ARCHIVE_DIR", str(tmp_path))
    monkeypatch.setenv("OPENAI_API_KEY", "offline-fixture")
    entries = [{"page": 2, "title": "Fixture 876543210 rated current", "content": "Rated current 4 A",
                "conditions": "25 C", "axes": [], "points": []},
               {"page": 3, "title": "Fixture 876543210 bias", "content": "Approximate graph samples",
                "conditions": "25 C", "axes": ["Current", "A", "L", "uH"], "points": [[0, 12], [2, 10]]}]
    stream = BytesIO()
    pdf = Canvas(stream)
    for _ in range(3):
        pdf.showPage()
    pdf.save()
    raw = stream.getvalue()
    calls = []
    def respond(req):
        calls.append(req.url)
        assert req.url == "https://api.openai.com/v1/responses"
        body = json.loads(req.content)
        assert body["store"] is False
        attachment = body["input"][0]["content"][1]
        assert base64.b64decode(attachment["file_data"].split(",")[1]) == raw
        return httpx.Response(200, json={"status":"completed", "output":[{"content":[
            {"type":"output_text", "text":json.dumps({"entries":entries})}]}]})
    original_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original_client(transport=httpx.MockTransport(respond), **kw))
    client = TestClient(app)
    headers = {"X-Session-ID": str(uuid4()), APP_TOKEN_HEADER: "Bearer " + TOKEN}
    other = {**headers, "X-Session-ID": str(uuid4())}
    preview = client.post("/api/manuals/preview", files={"file":("fixture.pdf", raw)}, headers=headers)
    assert preview.status_code == 200, preview.text
    pending = preview.json()
    assert pending["entries"] == entries
    entries[0]["page"] = 4
    invalid = client.post("/api/manuals/preview", files={"file":("fixture.pdf", raw)}, headers=headers)
    assert invalid.status_code == 422 and "页码" in invalid.json()["detail"]
    entries[0]["page"] = 2
    assert not manual_import.corpus()[0]
    url = "/api/manuals/" + pending["token"] + "/save"
    assert client.post(url, headers=other).status_code == 404
    saved = client.post(url, headers=headers).json()
    assert saved["created"]
    assert not client.post(url, headers=headers).json()["created"]
    manual_import._load.cache_clear()
    docs = knowledge.list_knowledge("manual", manual_id=saved["manual_id"])["entries"]
    assert len(docs) == 2 and all(doc["manual_id"] == saved["manual_id"] for doc in docs)
    repeated = client.post("/api/manuals/preview", files={"file":("renamed.PDF", raw)}, headers=other)
    assert repeated.json() == {"already_saved": True, "manual_id": saved["manual_id"]}
    assert len(calls) == 2
    hit = knowledge.retrieve("876543210 rated current")[0]
    assert hit["id"] == docs[0]["id"] and "PDF p. 2" in hit["content"]
    curve = docs[1]["id"]
    assert knowledge.read_knowledge([curve], x_value=2)["curve_lookup"]["y"] == 10
    assert knowledge.read_knowledge([curve], x_value=1)["curve_lookup"]["status"] == "between_samples"
    assert knowledge.read_knowledge([curve], x_value=3)["curve_lookup"]["status"] == "outside_sampled_range"
