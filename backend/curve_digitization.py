"""Map trace positions on a reviewed PDF page into engineering units."""

import io
import math
import re
from xml.etree import ElementTree

import numpy as np
from PIL import Image, ImageDraw
import subprocess
import tempfile
from typing import Annotated, Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, FiniteFloat, model_validator

UnitPosition = Annotated[FiniteFloat, Field(ge=0, le=1)]
Position = tuple[UnitPosition, UnitPosition]


class Calibration(BaseModel):
    lower_left: Position
    upper_right: Position
    x_range: tuple[FiniteFloat, FiniteFloat]
    y_range: tuple[FiniteFloat, FiniteFloat]
    x_scale: Literal["linear", "log"]
    y_scale: Literal["linear", "log"]
    method: Literal["vector", "image"]
    positions: list[Position] = Field(min_length=2, max_length=256)

    @model_validator(mode="after")
    def validate_plot(self):
        left, bottom = self.lower_left
        right, top = self.upper_right
        if left >= right or top >= bottom:
            raise ValueError("Plot corners must define a positive width and height")
        for (low, high), scale in [(self.x_range, self.x_scale), (self.y_range, self.y_scale)]:
            if low >= high or (scale == "log" and low <= 0):
                raise ValueError("Invalid axis range for selected scale")
        previous = -1
        for x, y in self.positions:
            if not (left <= x <= right and top <= y <= bottom and previous < x):
                raise ValueError("Samples must be inside the plot and increase strictly in x")
            previous = x
        return self

    def samples(self):
        def value(t, limits, scale):
            low, high = limits
            if scale == "log":
                result = math.exp((1 - t) * math.log(low) + t * math.log(high))
            else:
                result = (1 - t) * low + t * high
            return float(format(result, ".12g"))

        left, bottom = self.lower_left
        right, top = self.upper_right
        return [
            [
                value((x - left) / (right - left), self.x_range, self.x_scale),
                value((bottom - y) / (bottom - top), self.y_range, self.y_scale),
            ]
            for x, y in self.positions
        ]


def render_page(raw, page, raster=False):
    # Cairo retains vector centerlines and embeds scanned images in the same page coordinate system.
    with tempfile.TemporaryFile() as output:
        try:
            fmt = ["-png", "-singlefile", "-cropbox", "-scale-to", "1600"] if raster else ["-svg"]
            subprocess.run(
                ["pdftocairo", *fmt, "-f", str(page), "-l", str(page), "-", "-"],
                input=raw,
                stdout=output,
                stderr=subprocess.DEVNULL,
                check=True,
                timeout=30,
            )
        except FileNotFoundError as exc:
            raise HTTPException(503, "Install Poppler pdftocairo on the backend.") from exc
        except (subprocess.SubprocessError, OSError) as exc:
            raise HTTPException(422, "Could not render the selected PDF page.") from exc
        if output.tell() > 20 * 1024 * 1024:
            raise HTTPException(413, "This page is too large to review. Upload a cropped PDF page.")
        output.seek(0)
        return output.read()


# Cairo emits absolute M/L/C paths with affine matrix transforms. Other shapes,
# clipped paths and embedded scans are handled using their rendered pixels.
NUMBER = r"[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[Ee][-+]?\d+)?"


def _vertices(d):
    tokens = re.findall(rf"{NUMBER}|[A-Za-z]", d)
    pts, start, cmd, i = [], None, "", 0
    while i < len(tokens):
        if tokens[i].isalpha():
            cmd = tokens[i]
            i += 1
        if cmd == "Z" and start is not None:
            pts.append(start)
            break
        if cmd not in ("M", "L", "C") or (cmd == "M" and pts):
            return None
        n = 6 if cmd == "C" else 2
        coords = np.array(tokens[i : i + n], dtype=float).reshape(-1, 2)
        if coords.size != n:
            return None
        i += n
        if cmd == "C":
            if not pts:
                return None
            t = np.arange(1, 25)[:, None] / 24
            pts.extend(
                (1 - t) ** 3 * pts[-1]
                + 3 * (1 - t) ** 2 * t * coords[0]
                + 3 * (1 - t) * t * t * coords[1]
                + t**3 * coords[2]
            )
        else:
            pts.append(coords[0])
            if cmd == "M":
                start = pts[0]
                cmd = "L"
    return np.array(pts) if len(pts) > 1 else None


def geometry(svg):
    root = ElementTree.fromstring(svg)
    ox, oy, width, height = map(float, root.attrib["viewBox"].split())
    curves, grid = [], []

    def visit(node, matrix):
        tag = node.tag.split("}")[-1]
        if tag in {"defs", "symbol", "clipPath", "mask"} or any(
            k in node.attrib for k in ("clip-path", "mask")
        ):
            return
        transform = node.get("transform")
        if transform:
            if not re.fullmatch(r"matrix\([^()]+\)", transform):
                return
            a, b, c, d, e, f = map(float, re.findall(NUMBER, transform))
            matrix = matrix @ np.array([[a, c, e], [b, d, f], [0, 0, 1]])
        if tag == "path":
            style = dict(v.split(":", 1) for v in node.get("style", "").split(";") if ":" in v)
            if style.get("stroke", node.get("stroke", "none")).strip() == "none":
                return
            path = node.get("d", "")
            # Cairo sometimes repeats the initial moveto after closing a border.
            simple = path.split("Z", 1)[0] + "Z" if "Z" in path else path
            points = _vertices(simple)
            if points is None:
                return
            points = (points @ matrix[:2, :2].T + matrix[:2, 2] - [ox, oy]) / [width, height]
            if "C" not in simple:
                for p, q in zip(points, points[1:]):
                    for axis in (0, 1):
                        if abs(p[axis] - q[axis]) < 1e-7 and abs(p[1 - axis] - q[1 - axis]) > 0.025:
                            grid.append(
                                (axis, p[axis], min(p[1 - axis], q[1 - axis]), max(p[1 - axis], q[1 - axis]))
                            )
            if "Z" not in path and abs(points[-1, 0] - points[0, 0]) > 0.005:
                if points[0, 0] > points[-1, 0]:
                    points = points[::-1]
                if np.all(np.diff(points[:, 0]) > 0):
                    curves.append(points)
        for child in node:
            visit(child, matrix)

    visit(root, np.eye(3))
    return curves, grid


def candidate(image, paths, grid, plan):
    # The AI supplies normalized page locations. Axis crossing positions and
    # samples are refined against the original vector geometry wherever possible.
    frame = Calibration.model_validate({**plan, "method": "image", "positions": plan["anchors"]})
    anchors = np.array(frame.positions)

    def snap(p):
        found = list(p)
        for axis in (0, 1):
            options = [
                pos
                for i, pos, lo, hi in grid
                if i == axis and lo - 0.012 <= p[1 - i] <= hi + 0.012 and abs(pos - p[i]) < 0.008
            ]
            if options:
                found[axis] = min(options, key=lambda v: abs(v - p[axis]))
        return found

    left, bottom = snap(frame.lower_left)
    right, top = snap(frame.upper_right)
    if not (right > left and bottom > top):
        return None
    ranked = []
    for trace in paths:
        x, y = trace.T
        if x[0] > anchors[0, 0] + 0.008 or x[-1] < anchors[-1, 0] - 0.008:
            continue
        residual = np.abs(np.interp(anchors[:, 0], x, y) - anchors[:, 1])
        if residual.max() <= 0.012:
            ranked.append((float(np.mean(residual)), trace))
    ranked.sort(key=lambda pair: pair[0])
    if ranked and (len(ranked) == 1 or ranked[1][0] - ranked[0][0] > 0.001):
        x, y = ranked[0][1].T
        a, b = max(left, x[0]), min(right, x[-1])
        if a >= b:
            return None
        xs = np.linspace(a, b, 81)
        pts = [[float(u), float(v)] for u, v in zip(xs, np.interp(xs, x, y)) if top <= v <= bottom]
        if len(pts) < 3:
            return None
        interior = y[(x > pts[0][0]) & (x < pts[-1][0])]
        if np.any((interior < top) | (interior > bottom)):
            return None  # The visible trace has a gap; a single lookup range would bridge it.
        method = "vector"
    else:
        # On raster pages keep sparse, evidence-backed locations only. A close
        # match to an ambiguous vector is not evidence for choosing one series.
        if ranked:
            return None
        pts = []
        pixels = np.asarray(image)
        w, h = image.size
        for x, y in anchors:
            ix, iy = round(x * (w - 1)), round(y * (h - 1))
            line = pixels[max(0, iy - 5) : min(h, iy + 6), ix, :3].min(axis=1)
            ink = np.flatnonzero(line < 155)
            if not len(ink):
                return None
            py = (max(0, iy - 5) + ink[np.argmin(abs(max(0, iy - 5) + ink - iy))]) / (h - 1)
            if not (left <= x <= right and top <= py <= bottom):
                return None
            pts.append([float(x), float(py)])
        method = "image"
    if len(pts) < 3:
        return None
    return Calibration.model_validate(
        {
            **frame.model_dump(),
            "lower_left": [left, bottom],
            "upper_right": [right, top],
            "method": method,
            "positions": pts,
        }
    )


def overlay(image, calibration):
    annotated = image.copy()
    draw = ImageDraw.Draw(annotated)

    def pixel(p):
        return tuple(round(v * (n - 1)) for v, n in zip(p, image.size))

    l, b = pixel(calibration.lower_left)
    r, t = pixel(calibration.upper_right)
    draw.rectangle((l, t, r, b), outline="#009cbe", width=2)
    for p in calibration.positions:
        x, y = pixel(p)
        draw.ellipse((x - 3, y - 3, x + 3, y + 3), outline="#ee006a", width=2)
    out = io.BytesIO()
    annotated.save(out, format="PNG")
    return out.getvalue()
