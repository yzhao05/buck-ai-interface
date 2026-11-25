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

from backend import chat, harness_runtime, knowledge, manual_import
from backend.schemas import ChatRequest
from backend.curve_digitization import StrokeStyle, VectorStroke, axis_fit, candidate, geometry, render_page
from backend.main import APP_TOKEN_HEADER, TOKEN, app


PLAN = dict(
    identity={"label":"Fixture","color":"green","rgb":[0,128,0],"neighbors":"only curve"},
    axes=["Current","A","Inductance","uH"],
    series_scope="general",
    ticks={"x":[[.1,0],[.5,5],[.9,10]],"y":[[.9,1],[.9-.8/3,10],[.1,1000]]},
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
        series_scope="part", part_reference=dict(label="Fixture",part_number="876543210",page=1),
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
        *content, format_instruction = body["input"][0]["content"]
        assert "JSON" in format_instruction["text"]
        if content[1]["type"] == "input_file":
            assert base64.b64decode(content[1]["file_data"].split(",")[1]) == raw
            result = {"entries": entries}
            calls.append("extract")
        elif len(content) == 2:
            result = {"curves": {entry["id"]: PLAN if entry["title"] != "Unlabelled axes" else {"axes": []}
                                 for entry in json.loads(content[0]["text"])}}
            calls.append("locate")
        else:
            assert content[1]["image_url"] != content[2]["image_url"]
            check = json.loads(content[0]["text"])
            assert check["method"] == "vector" and check["ink_check"]["coverage"] >= .95
            assert len(content) == 4 and check["part_reference"]["page"] == 1
            assert content[3] != content[1]
            result = dict(series_match=check["series"] == curve["title"], axes_match=True, samples_match=True, part_match=True)
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
    with closing(manual_import.connect()) as db:
        assert db.execute("SELECT digest,source FROM manual_files").fetchall() == [(doc[4:], raw)]
    assert calls == ["extract", "locate", "verify", "verify"]
    assert len(knowledge.list_knowledge("manual", manual_id=doc)["entries"]) == 4
    assert "4 A" in knowledge.read_knowledge([doc + "-1"])["documents"][0]["content"]
    curve_id = doc + "-2"
    lookup = lambda x: knowledge.read_knowledge([curve_id], x_value=x)["curve_lookup"]
    first = lookup(0)
    assert first["status"] == "sample" and first["y"] == pytest.approx(10**2.25, rel=0.001)
    assert first["part_reference"]["part_number"] == "876543210" and first["part_reference"]["page"] == 1
    assert first["locator"] == "2" and first["y_unit"] == "uH"
    assert lookup(1)["status"] == "axis_interpolation"
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
    assert sampled.method == "pixel_trace" and len(sampled.samples()) >= 700


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
        if content[-1]["type"] == "input_file":
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
        x, y = paths[0].points[0] * (1 - fraction) + paths[0].points[-1] * fraction
        col, row = round(x * (width - 1)), round(y * (height - 1))
        assert pixels[row - 2 : row + 3, col - 2 : col + 3].min() < 155


def test_disconnected_visible_segments_do_not_form_a_lookup_range():
    line = np.array([[0.15, 0.3143], [0.25, 0.04], [0.4, 0.3857], [0.76, 0.4886]])
    stroke = StrokeStyle(rgb=(0,128,0), pattern="solid", width=1, dash_array="none")
    assert candidate(Image.new("RGB", (100, 100)), [VectorStroke(line, stroke)], [], PLAN) is None


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


@pytest.mark.parametrize("damage", ["calibration", "samples", "axes", "legacy_unversioned", "legacy_v2"])
def test_broken_curve_record_keeps_source_but_cannot_break_other_lookups(damage):
    image, plan = tdk_raster_fixture()
    calibration = candidate(image, [], [], plan)
    row = dict(id="curve-ok", manual_id="pdf-fixture", locator="2", source_file="fixture.pdf",
               title="Fixture A", kind="curve", content="Verified series", url="", conditions="",
               axes_json='["Current","A","Inductance","uH"]', points_json=json.dumps(calibration.samples()),
               calibration_json=calibration.model_dump_json(), value_source="PDF", auto_review="verified",
               digitization_version="3", series_scope="general")
    broken_field, bad_value = {
        "calibration": ("calibration_json", "{}"),
        "samples": ("points_json", "[[0,9999]]"),
        "axes": ("axes_json", "{}"),
        "legacy_unversioned": ("digitization_version", ""),
        "legacy_v2": ("digitization_version", "2"),
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


def tdk_raster_fixture():
    # Use the example's pixel coordinates, with a second identically colored stroke.
    image = Image.new("RGB", (1201, 1201), "white")
    draw = ImageDraw.Draw(image)
    for x in (89, 1125):
        draw.line((x, 65, x, 1133), fill="gray")
    for y in (421, 777):
        draw.line((89, y, 1125, y), fill="gray")
    teal = (62, 147, 125)
    for x in range(89, 508):
        y = round(np.interp(x, [89, 280, 400, 478, 507], [546, 546, 552, 570, 590]))
        draw.line((x, y, x, y + 1), fill=teal)
    draw.line((89, 803, 900, 815), fill=teal, width=2)
    plan = {
        "identity": {"label": "470M", "color": "teal", "rgb": teal, "neighbors": "below 680M, above 330M"},
        "axes": ["DC bias current", "A", "Inductance", "µH"],
        "lower_left": [89 / 1200, 1133 / 1200],
        "upper_right": [1125 / 1200, 65 / 1200],
        "x_range": [0, 10],
        "y_range": [1, 1000],
        "x_scale": "linear",
        "y_scale": "log",
        "ticks": {"x": [[89 / 1200, 0], [607 / 1200, 5], [1125 / 1200, 10]],
                 "y": [[1133 / 1200, 1], [779 / 1200, 10], [419 / 1200, 100], [65 / 1200, 1000]]},
        "anchors": [[150 / 1200, 546 / 1200], [400 / 1200, 552 / 1200], [495 / 1200, 582 / 1200]],
    }
    return image, plan


@pytest.mark.parametrize("stage", ["曲线定位", "曲线复核"])
def test_curve_tool_returns_failed_stage_and_safe_provider_detail(monkeypatch, stage):
    image, plan = tdk_raster_fixture()
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    data_url = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
    request = ChatRequest(message="read curve", images=[dict(name="test.png", data_url=data_url)])
    monkeypatch.delenv("OPENAI_RELAY_TOKEN", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")

    async def post(self, url, *, json, **kwargs):
        content = json["input"][0]["content"]
        assert "JSON" in content[-1]["text"]
        images = sum(block["type"] == "input_image" for block in content)
        if stage == "曲线复核" and images == 1:
            from json import dumps
            return httpx.Response(200, request=httpx.Request("POST", url), json={
                "status": "completed", "output": [{"content": [
                    {"type": "output_text", "text": dumps({"curves": {"1": plan}})}
                ]}]
            })
        return httpx.Response(400, request=httpx.Request("POST", url), json={
            "error": {"param": "input", "code": "invalid_image", "message": "Invalid image: sk-private"}
        })

    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    value = asyncio.run(manual_import.read_image_curve(request.images, 1, "470M", [3], "A"))
    assert all(part in value["error"] for part in (stage, "HTTP 400", "invalid_image", "param=input"))
    assert "sk-private" not in value["error"]
    async def run(messages, tools, execute, budget):
        await execute("read_image_curve", dict(image_index=1, series="470M", x_values=[3], x_unit="A"))
        invalid = await execute("read_image_curve", dict(image_index=2, series="470M", x_values=[3], x_unit="A"))
        ledger = await execute("read_diagnosis", {})
        assert ledger["evidence"][invalid["evidence_id"]]["output"]["error"] == invalid["error"]
        return "No calibrated reading."

    monkeypatch.setattr(harness_runtime, "run", run)
    result = asyncio.run(chat.answer(request.model_copy(update={"chat_model": "gpt-6.1-terra"}), None))
    trace = result["tool_calls"][0]
    assert trace["status"] == "读图失败" and trace["diagnostic"] == value["error"]
    assert result["tool_calls"][1]["diagnostic"] == "Select a current image attachment by its 1-based index"


def test_terra_chat_traces_same_color_by_connectivity_and_uses_printed_ticks(monkeypatch):
    image, plan = tdk_raster_fixture()
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    data_url = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()
    monkeypatch.setenv("OPENAI_API_KEY", "offline-fixture")
    monkeypatch.setenv("OPENAI_MANUAL_MODEL", "other-pdf-model")
    calls = []

    async def recognition(prompt, content, *, model):
        calls.append(model)
        if len(content) == 2:
            return {"curves": {"1": plan}}
        checked = json.loads(content[0]["text"])
        assert checked["method"] == "pixel_trace"
        assert checked["identity"]["label"] == "470M"
        assert content[1] != content[2]
        return dict(series_match=True, axes_match=True, samples_match=True)

    readings = []

    async def agent(messages, tools, execute, budget):
        assert "read_image_curve" in [tool["function"]["name"] for tool in tools]
        response = await execute(
            "read_image_curve", dict(image_index=1, series="470M", x_values=[3, 3.75, 5], x_unit="A")
        )
        readings.extend(response["readings"])
        return "Read the reviewed trace."

    monkeypatch.setattr(manual_import, "request_json", recognition)
    monkeypatch.setattr(harness_runtime, "run", agent)
    request = ChatRequest(
        message="Read 470M at 3, 3.75 and 5 A",
        chat_model="gpt-6.1-terra",
        images=[dict(name="chart.png", data_url=data_url)],
    )
    asyncio.run(chat.answer(request, None))
    assert calls == ["gpt-6.1-terra", "gpt-6.1-terra"]
    a, b, c = readings
    assert (a["status"], a["pixel_center"], a["y"], a["y_unit"]) == ("pixel_column", [400, 552.5], 42.7, "µH")
    assert a["tick_pixels"]["y"] == [[1133, 1], [777, 10], [421, 100], [65, 1000]]
    assert b["pixel_center"] == [478, 570.5] and b["y"] == pytest.approx(38.0, abs=0.1)
    assert c["status"] == "outside_sampled_range" and c["y"] is None
    assert c["range"][1] == pytest.approx(4.03, abs=0.01)


@pytest.mark.parametrize("damage", ["gap", "merged"])
def test_ambiguous_or_disconnected_raster_cannot_publish_samples(damage):
    image, plan = tdk_raster_fixture()
    draw = ImageDraw.Draw(image)
    if damage == "gap":
        draw.rectangle((350, 500, 359, 600), fill="white")
    else:
        draw.line((385, 551, 425, 610), fill=(62, 147, 125), width=2)
    assert candidate(image, [], [], plan) is None


def test_scanned_manual_publishes_calculated_columns_to_shared_csv(monkeypatch):
    image, plan = tdk_raster_fixture()
    png = BytesIO()
    image.save(png, format="PNG")
    raw = example_pdf()
    doc = "pdf-" + hashlib.sha256(raw).hexdigest()

    async def extract(raw, filename):
        return manual_import.Extraction(
            entries=[
                dict(
                    is_curve=True,
                    page=2,
                    title="470M", series_scope="general",
                    axes=[],
                    content="470M DC bias characteristic",
                    conditions="typical",
                )
            ]
        )

    async def recognize(prompt, content):
        if len(content) == 2:
            return {"curves": {"1": plan}}
        return dict(series_match=True, axes_match=True, samples_match=True)

    monkeypatch.setattr(manual_import, "extract", extract)
    monkeypatch.setattr(manual_import, "request_json", recognize)
    monkeypatch.setattr(
        manual_import,
        "render_page",
        lambda raw, page, raster=False: png.getvalue() if raster else b'<svg viewBox="0 0 1201 1201"/>',
    )
    result = asyncio.run(manual_import.process(raw, "scanned.pdf", lambda page: None))
    assert result["verified"] == 1
    _, stored = manual_import.saved_rows(raw)
    (row,) = csv.DictReader(StringIO(stored))
    calibration = json.loads(row["calibration_json"])
    assert row["digitization_version"] == "3" and calibration["identity"]["label"] == "470M"
    assert len(json.loads(row["points_json"])) == 419
    reading = knowledge.read_knowledge([doc + "-1"], x_value=3)["curve_lookup"]
    assert (reading["y"], reading["y_unit"], reading["locator"]) == (42.7, "µH", "2")
    outside = knowledge.read_knowledge([doc + "-1"], x_value=5)["curve_lookup"]
    assert outside["status"] == "outside_sampled_range" and outside["y"] is None


def test_independent_ticks_select_scale_and_reject_conflicting_evidence():
    assert axis_fit([[0.1, 1], [0.5, 10], [0.9, 100]], 1000)[0] == "log"
    assert axis_fit([[0.1, 1], [0.5, 6], [0.9, 11]], 1000)[0] == "linear"
    for ticks in (
        [[0.1, 0], [0.9, 10]],
        [[0.1, 1], [0.5, 30], [0.9, 100]],
        [[0.1, 1000], [0.5, 1001], [0.9, 1002]],
    ):
        with pytest.raises(ValueError):
            axis_fit(ticks, 1000)
    image, plan = tdk_raster_fixture()
    plan["y_scale"] = "linear"
    result = candidate(image, [], [], plan)
    assert result.y_scale == "log" and result.axis_error_px.y < 1
    plan["ticks"]["y"] = plan["ticks"]["y"][1:3]
    with pytest.raises(ValueError):
        candidate(image, [], [], plan)
    plan["y_scale"] = "log"
    assert candidate(image, [], [], plan, allow_two=True).axis_error_px.y is None


def test_vector_style_selects_matching_path_and_rejects_absent_ink():
    svg = b"""<svg viewBox="0 0 1000 1000">
        <g style="stroke:green;stroke-dasharray:3,2;stroke-width:2"><path d="M100 300 L800 500"/></g>
        <path stroke="green" stroke-width="2" d="M100 300 L800 500"/>
        <path stroke="red" d="M100 300 L800 500"/></svg>"""
    paths, grid = geometry(svg)
    image = Image.new("RGB", (1001, 1001), "white")
    plan = {**PLAN, "identity": {**PLAN["identity"], "line_style": "dashed"}}
    assert candidate(image, paths, grid, plan) is None
    draw = ImageDraw.Draw(image)
    for x in range(100, 800, 5):
        y = 300 + (x - 100) * 2 / 7
        draw.line((x, round(y), x + 3, round(y + 6 / 7)), fill="green", width=2)
    calibration = candidate(image, paths, grid, plan)
    assert calibration.method == "vector" and calibration.stroke.dash_array == "3,2"
    assert calibration.ink_check.measured_columns == 701


@pytest.mark.parametrize("failure", ["wrong_number", "ambiguous", "duplicate", "previous", "valid"])
def test_scanned_part_mapping_requires_original_evidence_and_unique_assignment(failure):
    image, plan = tdk_raster_fixture()
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    data = buffer.getvalue()
    labels = ["470M", "680M"] if failure == "duplicate" else ["470M"]
    rows = [
        dict(
            title=label,
            content=label,
            conditions="",
            axes_json="[]",
            series_scope="part",
            part_reference_json=json.dumps(dict(label=label, part_number="ABC-" + label, page=1)),
        )
        for label in labels
    ]

    prior = []
    if failure == "previous":
        prior_calibration = candidate(
            image, [], [], {**plan, "identity": {**plan["identity"], "label": "680M"}}
        )
        prior.append(
            dict(
                title="680M",
                kind="curve",
                calibration_json=prior_calibration.model_dump_json(),
                part_reference_json=json.dumps(dict(label="680M", part_number="ABC-680M", page=1)),
            )
        )

    async def respond(prompt, content):
        if "part_numbers" in prompt:
            label = content[0]["text"]
            assert label in labels and "ABC" not in label
            values = {"wrong_number": ["ABC-other"], "ambiguous": ["ABC-" + label, "ABC-other"]}
            return {"part_numbers": values.get(failure, ["ABC-" + label])}
        if len(content) == 2:
            return {
                "curves": {
                    str(i + 1): {**plan, "identity": {**plan["identity"], "label": label}}
                    for i, label in enumerate(labels)
                }
            }
        assert len(content) == 4
        return dict(series_match=True, axes_match=True, samples_match=True, part_match=True)

    asyncio.run(
        manual_import.analyze_curves(
            image,
            data,
            rows,
            ask=respond,
            reference_page=lambda page: (data, "scanned table header"),
            previous=prior,
        )
    )
    assert all(row["kind"] == ("curve" if failure == "valid" else "curve_pending") for row in rows)
    if prior:
        assert prior[0]["kind"] == "curve_pending" and prior[0]["points_json"] == "[]"
    if failure != "valid":
        assert all(row["points_json"] == "[]" for row in rows)


@pytest.mark.parametrize("damage", ["reference", "marks", "ink", "version", "ticks", "identity"])
def test_invalid_archived_evidence_isolated_and_rediscovered(damage):
    image, plan = tdk_raster_fixture()
    cal = candidate(image, [], [], plan)
    raw = json.loads(cal.model_dump_json())
    if damage in ("ticks", "identity"):
        del raw[damage]
        with pytest.raises(ValueError):
            type(cal).model_validate(raw)
    if damage == "marks":
        raw["axis_error_px"]["x"] += 1
    if damage == "ink":
        raw["ink_check"]["coverage"] = 0.1
    row = dict(
        id="bad",
        title="470M",
        kind="curve",
        content="stale",
        axes_json=json.dumps(plan["axes"]),
        digitization_version="4" if damage == "version" else "3",
        series_scope="part",
        part_reference_json=(
            "{" if damage == "reference" else json.dumps(dict(label="470M", part_number="ABC-470M", page=1))
        ),
        calibration_json=json.dumps(raw),
        points_json=json.dumps(cal.samples()),
        auto_review="verified",
    )
    buf = StringIO()
    writer = csv.DictWriter(buf, fieldnames=row)
    writer.writeheader()
    writer.writerow(row)
    (restored,) = manual_import.imported_rows(buf.getvalue())
    assert restored["kind"] == "curve_pending" and restored["points_json"] == "[]"
    assert (
        not restored["series_scope"] and not restored["part_reference_json"] and not restored["auto_review"]
    )


def test_vector_vertex_almost_on_sampling_grid_is_kept_once():
    image = Image.new("RGB", (1001, 1001), "white")
    ImageDraw.Draw(image).line([(100, 300), (800, 500)], fill="green", width=3)
    vertex_x = 0.45 + 1e-15
    trace = VectorStroke(
        np.array([[0.1, 0.3], [vertex_x, 0.4], [0.8, 0.5]]),
        StrokeStyle(rgb=(0, 128, 0), pattern="solid", dash_array="none", width=1),
    )
    calibration = candidate(image, [trace], [], PLAN)
    assert (vertex_x, 0.4) in calibration.positions
    samples = calibration.samples()
    assert all(a[0] < b[0] for a, b in zip(samples, samples[1:]))
