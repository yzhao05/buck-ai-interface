import asyncio
import base64
import csv
import hashlib
import json
import time
from contextlib import closing
from io import BytesIO, StringIO
from uuid import uuid4

import httpx
import numpy as np
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from reportlab.pdfgen.canvas import Canvas

from backend import knowledge, manual_import
from backend.curve_digitization import candidate, geometry, render_page
from backend.main import APP_TOKEN_HEADER, TOKEN, app


PLAN = dict(
    lower_left=[0.1, 0.9],
    upper_right=[0.9, 0.1],
    x_range=[0, 10],
    y_range=[1, 1000],
    x_scale="linear",
    y_scale="log",
    anchors=[[0.15, 0.3143], [0.4, 0.3857], [0.76, 0.4886]],
)


def example_pdf():
    buffer = BytesIO()
    pdf = Canvas(buffer, pagesize=(400, 400))
    pdf.drawString(30, 100, "Fixture 876543210 rated current: 4 A")
    pdf.showPage()
    pdf.rect(40, 40, 320, 320)
    pdf.setStrokeColorRGB(0, 0.6, 0.2)
    pdf.line(40, 280, 320, 200)
    pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def upload(client, raw, headers):
    response = client.post("/api/manuals/import", files={"file": ("fixture.pdf", raw)}, headers=headers)
    assert response.status_code == 202, response.text
    return "/api/manuals/import/" + response.json()["job_id"]


def outcome(client, url, headers):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        response = client.get(url, headers=headers)
        assert response.status_code == 200
        data = response.json()
        if data["status"] != "processing":
            return data
        time.sleep(0.03)
    pytest.fail("Manual import did not finish")


def test_automatic_import_and_shared_lookup(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "offline-fixture")
    raw = example_pdf()
    doc = "pdf-" + hashlib.sha256(raw).hexdigest()
    table = dict(
        is_curve=False,
        page=1,
        title="Fixture 876543210 rated current",
        content="Rated current 4 A",
        conditions="25 C",
        axes=[],
        points=[],
    )
    curve = dict(
        is_curve=True,
        page=2,
        title="Fixture 876543210 bias",
        content="Green series",
        conditions="25 C",
        axes=["Current", "A", "L", "uH"],
        points=[],
    )
    entries = [
        table,
        curve,
        {**curve, "title": "Unconfirmed series"},
        {**curve, "title": "Unlabelled axes", "axes": []},
    ]
    calls = []

    def respond(req):
        assert req.url == "https://api.openai.com/v1/responses"
        body = json.loads(req.content)
        assert body["store"] is False
        content = body["input"][0]["content"]
        if content[1]["type"] == "input_file":
            assert base64.b64decode(content[1]["file_data"].split(",")[1]) == raw
            result = {"entries": entries}
            calls.append("extract")
        elif len(content) == 2:
            ids = [entry["id"] for entry in json.loads(content[0]["text"])]
            result = {"curves": dict.fromkeys(ids, PLAN)}
            calls.append("locate")
        else:
            assert content[1]["image_url"] != content[2]["image_url"]
            check = json.loads(content[0]["text"])
            assert check["method"] == "vector"
            result = dict(series_match=check["series"] == curve["title"], axes_match=True, samples_match=True)
            calls.append("verify")
        return httpx.Response(
            200,
            json={
                "status": "completed",
                "output": [{"content": [{"type": "output_text", "text": json.dumps(result)}]}],
            },
        )

    original_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: original_client(transport=httpx.MockTransport(respond), **kw)
    )
    headers = {"X-Session-ID": str(uuid4()), APP_TOKEN_HEADER: "Bearer " + TOKEN}
    other = {**headers, "X-Session-ID": str(uuid4())}
    with TestClient(app) as client:
        url = upload(client, raw, headers)
        assert client.get(url, headers=other).status_code == 404
        data = outcome(client, url, headers)
        assert data["status"] == "completed", data
        assert data["result"] == dict(
            entries=4,
            verified=1,
            already_saved=False,
            unreadable=[{"title": r["title"], "page": "2"} for r in entries[2:]],
        )
        repeat = outcome(client, upload(client, raw, other), other)
        assert repeat["result"]["already_saved"] is True
    assert calls == ["extract", "locate", "verify", "verify"]
    assert len(knowledge.list_knowledge("manual", manual_id=doc)["entries"]) == 4
    assert "4 A" in knowledge.read_knowledge([doc + "-1"])["documents"][0]["content"]
    curve_id = doc + "-2"
    lookup = lambda x: knowledge.read_knowledge([curve_id], x_value=x)["curve_lookup"]
    first = lookup(0)
    assert first["status"] == "sample" and first["y"] == pytest.approx(10**2.25, rel=0.001)
    assert first["locator"] == "2" and first["y_unit"] == "uH"
    assert lookup(1)["status"] == "between_samples"
    assert lookup(9)["status"] == "outside_sampled_range"
    assert set(manual_import.corpus()[1]) == {curve_id}


def test_bad_page_fails_without_partial_import(monkeypatch):
    raw = example_pdf()

    async def wrong_page(*args):
        return {
            "entries": [
                {
                    "is_curve": False,
                    "page": 20,
                    "title": "Invalid page",
                    "content": "fixture",
                    "conditions": "",
                    "axes": [],
                    "points": [],
                }
            ]
        }

    monkeypatch.setattr(manual_import, "request_json", wrong_page)
    headers = {"X-Session-ID": str(uuid4()), APP_TOKEN_HEADER: "Bearer " + TOKEN}
    with TestClient(app) as client:
        data = outcome(client, upload(client, raw, headers), headers)
        assert data["status"] == "failed" and "页码" in data["error"]
        assert not manual_import.corpus()[0]

        async def corrected(*args):
            response = await wrong_page()
            response["entries"][0]["page"] = 1
            return response

        monkeypatch.setattr(manual_import, "request_json", corrected)
        assert outcome(client, upload(client, raw, headers), headers)["status"] == "completed"


def test_pixel_fallback_requires_visible_ink():
    image = Image.new("RGB", (1001, 1001), "white")
    assert candidate(image, [], [], PLAN) is None
    ImageDraw.Draw(image).line([(100, 300), (800, 500)], fill="green", width=3)
    sampled = candidate(image, [], [], PLAN)
    assert sampled.method == "image" and len(sampled.samples()) == len(PLAN["anchors"])


@pytest.mark.parametrize("malformed", ["location", "verification"])
def test_legacy_samples_hidden_until_original_pdf_passes_review(monkeypatch, malformed):
    raw = example_pdf()
    digest = hashlib.sha256(raw).hexdigest()
    key = "pdf-" + digest + "-1"
    row = dict(
        id=key,
        manual_id="pdf-" + digest,
        source_file="old manual.pdf",
        locator="2",
        title="Fixture 876543210 bias",
        kind="curve",
        points_json="[[0,9999],[4,9999]]",
        axes_json='["current","A","inductance","uH"]',
        content="old estimate 9999 uH",
        conditions="25 C",
        value_source="importer review",
    )
    stored = StringIO()
    with manual_import.manuals.PUBLIC_CSV.open(encoding="utf-8-sig", newline="") as source:
        writer = csv.DictWriter(stored, fieldnames=next(csv.reader(source)))
        writer.writeheader()
        writer.writerow(row)
    with closing(manual_import.connect()) as db, db:
        db.execute("INSERT INTO imports VALUES(?,?)", (digest, stored.getvalue()))

    def verify_unavailable():
        assert key not in manual_import.corpus()[1]
        doc = knowledge.read_knowledge([key])["documents"][0]
        assert "9999" not in doc["content"] and "PDF p. 2" in doc["content"]
        with pytest.raises(ValueError):
            knowledge.read_knowledge([key], x_value=0)

    verify_unavailable()

    async def provider(prompt, content):
        assert content[1]["type"] == "input_image"
        assert "9999" not in content[0]["text"]
        if len(content) == 2:
            series_id = json.loads(content[0]["text"])[0]["id"]
            return {} if malformed == "location" else {"curves": {series_id: PLAN}}
        return (
            {"series_match": True, "axes_match": True}
            if malformed == "verification"
            else {"series_match": True, "axes_match": True, "samples_match": True}
        )

    monkeypatch.setattr(manual_import, "request_json", provider)
    with pytest.raises(HTTPException) as failure:
        asyncio.run(manual_import.process(raw, "renamed.pdf", lambda page: None))
    assert failure.value.status_code == 422
    verify_unavailable()
    malformed = None
    result = asyncio.run(manual_import.process(raw, "renamed.pdf", lambda page: None))
    assert result["verified"] == 1 and result["entries"] == 1
    assert "9999" not in knowledge.read_knowledge([key])["documents"][0]["content"]
    assert knowledge.read_knowledge([key], x_value=0)["curve_lookup"]["y"] == pytest.approx(
        10**2.25, rel=0.001
    )


def test_cropped_page_uses_same_axes_for_pixels_and_vectors():
    buf = BytesIO()
    pdf = Canvas(buf, pagesize=(600, 600))
    pdf.setCropBox((100, 100, 500, 400))
    pdf.line(150, 150, 440, 380)
    pdf.showPage()
    pdf.save()
    raw = buf.getvalue()
    image = Image.open(BytesIO(render_page(raw, 1, raster=True))).convert("RGB")
    paths, _ = geometry(render_page(raw, 1))
    assert len(paths) == 1
    pixels = np.asarray(image)
    width, height = image.size
    for fraction in [0.1, 0.5, 0.9]:
        x, y = paths[0][0] * (1 - fraction) + paths[0][-1] * fraction
        col, row = round(x * (width - 1)), round(y * (height - 1))
        assert pixels[row - 2 : row + 3, col - 2 : col + 3].min() < 155


def test_disconnected_visible_segments_do_not_form_a_lookup_range():
    line = np.array([[0.15, 0.3143], [0.25, 0.04], [0.4, 0.3857], [0.76, 0.4886]])
    assert candidate(Image.new("RGB", (100, 100)), [line], [], PLAN) is None


@pytest.mark.parametrize(("review","expected"), [("skipped","skipped"), ("verified","")])
def test_unsubstantiated_import_cannot_publish_stale_samples(review, expected):
    data = StringIO()
    writer = csv.DictWriter(data, fieldnames=["kind","title","content","auto_review","points_json","calibration_json"])
    writer.writeheader()
    writer.writerow(dict(kind="curve", title="470M", content="old estimate 9999", auto_review=review,
                         points_json="[[0,9999],[1,9999]]", calibration_json=""))
    row, = manual_import.imported_rows(data.getvalue())
    assert row["kind"] == "curve_pending" and row["points_json"] == "[]"
    assert row["auto_review"] == expected and "9999" not in row["content"]


@pytest.mark.parametrize("damage", ["calibration", "samples", "axes"])
def test_broken_curve_record_keeps_source_but_cannot_break_other_lookups(damage):
    from backend.curve_digitization import Calibration
    calibration = Calibration.model_validate({**PLAN, "method": "image", "positions": PLAN["anchors"]})
    row = dict(id="curve-ok", manual_id="pdf-fixture", locator="2", source_file="fixture.pdf",
               title="Fixture A", kind="curve", content="Verified series", url="", conditions="",
               axes_json='["Current","A","Inductance","uH"]', points_json=json.dumps(calibration.samples()),
               calibration_json=calibration.model_dump_json(), value_source="PDF", auto_review="verified")
    broken_field, bad_value = {
        "calibration": ("calibration_json", "{}"),
        "samples": ("points_json", "[[0,9999]]"),
        "axes": ("axes_json", "{}"),
    }[damage]
    broken = {**row, "id": "curve-broken", "content": "9999", broken_field: bad_value}
    data = StringIO()
    writer = csv.DictWriter(data, fieldnames=row)
    writer.writeheader()
    writer.writerows([row, broken])
    docs, curves = manual_import.manuals.parse(manual_import.imported_rows(data.getvalue()), {})
    assert set(curves) == {"curve-ok"}
    assert docs[1]["id"] == "curve-broken" and "PDF p. 2" in docs[1]["content"]
    assert "9999" not in docs[1]["content"]
