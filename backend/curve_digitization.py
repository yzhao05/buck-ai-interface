"""Map trace positions on a reviewed PDF page into engineering units."""

from dataclasses import dataclass
import io
import math
import re
from xml.etree import ElementTree

import numpy as np
from PIL import Image, ImageColor, ImageDraw
from scipy import ndimage
import subprocess
import tempfile
from typing import Annotated, Literal

from fastapi import HTTPException
from pydantic import BaseModel, Field, FiniteFloat, model_validator

UnitPosition = Annotated[FiniteFloat, Field(ge=0, le=1)]
Position = tuple[UnitPosition, UnitPosition]


class AxisTicks(BaseModel):
    # Pairs of (normalized image position, printed value), in increasing value order.
    x: Annotated[list[tuple[UnitPosition, FiniteFloat]], Field(min_length=2, max_length=32)]
    y: Annotated[list[tuple[UnitPosition, FiniteFloat]], Field(min_length=2, max_length=32)]


RGBChannel = Annotated[int, Field(strict=True, ge=0, le=255)]


class TraceIdentity(BaseModel):
    label: str = Field(min_length=1, max_length=250)
    color: str = Field(min_length=1, max_length=100)
    rgb: tuple[RGBChannel, RGBChannel, RGBChannel]
    neighbors: str = Field(max_length=350)
    line_style: Literal["solid", "dashed", "unknown"] = "unknown"


class StrokeStyle(BaseModel):
    rgb: tuple[RGBChannel, RGBChannel, RGBChannel]
    pattern: Literal["dashed", "solid"]
    width: Annotated[FiniteFloat, Field(gt=0)]
    dash_array: str


@dataclass
class VectorStroke:
    points: np.ndarray
    style: StrokeStyle


class AxisCheck(BaseModel):
    x: Annotated[FiniteFloat, Field(ge=0)] | None
    y: Annotated[FiniteFloat, Field(ge=0)] | None


class InkCheck(BaseModel):
    coverage: Annotated[FiniteFloat, Field(ge=0, le=1)]
    p95_distance_px: Annotated[FiniteFloat, Field(ge=0)]
    measured_columns: Annotated[int, Field(ge=3)]

    def matches_stroke(self, dashed):
        return self.coverage >= (0.65 if dashed else 0.95) and self.p95_distance_px <= (7 if dashed else 2.5)


def axis_fit(marks, size):
    """Fit the two outer ticks and measure the error on every independent interior tick."""
    pos, values = np.asarray(marks, dtype=float).T
    offsets = (pos - pos[0]) * size
    if (
        len(pos) < 3
        or not np.all(np.diff(values) > 0)
        or not (np.all(np.diff(pos) > 0) or np.all(np.diff(pos) < 0))
    ):
        raise ValueError("Need at least three ordered printed ticks per axis")
    tolerance = max(1.5, min(5.0, 0.008 * abs(offsets[-1])))
    matches = []
    for scale in ("linear", "log"):
        if scale == "log" and values[0] <= 0:
            continue
        units = np.log(values) if scale == "log" else values
        expected = offsets[-1] * (units - units[0]) / (units[-1] - units[0])
        error = float(np.max(np.abs(expected[1:-1] - offsets[1:-1])))
        if error <= tolerance:
            matches.append((scale, error))
    if len(matches) != 1:
        raise ValueError("Printed ticks do not uniquely distinguish a linear or logarithmic axis")
    return matches[0]


class Calibration(BaseModel):
    lower_left: Position
    upper_right: Position
    x_range: tuple[FiniteFloat, FiniteFloat]
    y_range: tuple[FiniteFloat, FiniteFloat]
    x_scale: Literal["linear", "log"]
    y_scale: Literal["linear", "log"]
    ticks: AxisTicks | None = None
    identity: TraceIdentity | None = None
    image_size: tuple[Annotated[int, Field(gt=1)], Annotated[int, Field(gt=1)]] | None = None
    stroke: StrokeStyle | None = None
    ink_check: InkCheck | None = None
    axis_error_px: AxisCheck | None = None
    method: Literal["vector", "image", "pixel_trace"]
    positions: list[Position] = Field(min_length=2, max_length=2048)

    @model_validator(mode="after")
    def validate_plot(self):
        left, bottom = self.lower_left
        right, top = self.upper_right
        if left >= right or top >= bottom:
            raise ValueError("Plot corners must define a positive width and height")
        for (low, high), scale in [(self.x_range, self.x_scale), (self.y_range, self.y_scale)]:
            if low >= high or (scale == "log" and low <= 0):
                raise ValueError("Invalid axis range for selected scale")
        if self.ticks:
            for pair, limits, scale, first, last in (
                (self.ticks.x, self.x_range, self.x_scale, left, right),
                (self.ticks.y, self.y_range, self.y_scale, bottom, top),
            ):
                (p0, v0), (p1, v1) = pair[0], pair[-1]
                if (
                    (p1 - p0) / (last - first) < 0.05
                    or min(p0, p1) < min(first, last) - 0.005
                    or max(p0, p1) > max(first, last) + 0.005
                    or not limits[0] <= v0 < v1 <= limits[1]
                    or (scale == "log" and v0 <= 0)
                ):
                    raise ValueError("Ticks must be ordered and separated inside the plot")
                transform = math.log if scale == "log" else float
                lo, hi = map(transform, limits)
                for pos, val in pair:
                    if abs((pos - first) / (last - first) - (transform(val) - lo) / (hi - lo)) > 0.025:
                        raise ValueError("Printed ticks disagree with axis bounds or scale")
        if self.method == "pixel_trace" and not (self.identity and self.ticks and self.image_size):
            raise ValueError("Pixel trace requires identified series, ticks and image dimensions")
        if self.method == "pixel_trace":
            width = self.image_size[0]
            columns = [x * (width - 1) for x, _ in self.positions]
            if any(
                abs(col - round(col)) > 1e-6 or round(col) != round(columns[0]) + i
                for i, col in enumerate(columns)
            ):
                raise ValueError("Every consecutive image column must have a center")
        previous = -1
        for x, y in self.positions:
            if not (left <= x <= right and top <= y <= bottom and previous < x):
                raise ValueError("Samples must be inside the plot and increase strictly in x")
            previous = x
        return self

    def has_archive_evidence(self):
        if not (self.ink_check and self.axis_error_px and self.ticks and self.image_size):
            return False
        if (self.method == "vector") != (self.stroke is not None):
            return False
        if not self.ink_check.matches_stroke(self.stroke is not None and self.stroke.pattern == "dashed"):
            return False
        for marks, size, scale, recorded in (
            (self.ticks.x, self.image_size[0], self.x_scale, self.axis_error_px.x),
            (self.ticks.y, self.image_size[1], self.y_scale, self.axis_error_px.y),
        ):
            fitted, error = axis_fit(marks, size - 1)
            if recorded is None or scale != fitted or not math.isclose(recorded, error, abs_tol=1e-7):
                return False
        return True

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
        xt = self.ticks.x if self.ticks else ((left, self.x_range[0]), (right, self.x_range[1]))
        yt = self.ticks.y if self.ticks else ((bottom, self.y_range[0]), (top, self.y_range[1]))

        def read(pos, ticks, scale):
            (p0, v0), (p1, v1) = ticks[0], ticks[-1]
            return value((pos - p0) / (p1 - p0), (v0, v1), scale)

        points = [[read(x, xt, self.x_scale), read(y, yt, self.y_scale)] for x, y in self.positions]
        if any(not math.isfinite(v) for pair in points for v in pair):
            raise ValueError("Invalid tick conversion")
        return points


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


def stroke_style(style):
    color = style.get("stroke", "")
    try:
        if color.startswith("rgb(") and "%" in color:
            rgb = [round(float(v) * 255 / 100) for v in re.findall(NUMBER, color)]
        else:
            rgb = ImageColor.getrgb(color)
        dash = style.get("stroke-dasharray", "none")
        return StrokeStyle(
            rgb=rgb,
            pattern="solid" if dash == "none" else "dashed",
            dash_array=dash,
            width=float(style.get("stroke-width", 1)),
        )
    except (ValueError, TypeError):
        return None


def geometry(svg):
    root = ElementTree.fromstring(svg)
    ox, oy, width, height = map(float, root.attrib["viewBox"].split())
    curves, grid = [], []

    inherited_properties = {"stroke", "stroke-width", "stroke-dasharray", "stroke-opacity", "visibility"}

    def visit(node, matrix, inherited):
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
        style = {**inherited, **node.attrib}
        style.update(
            {
                k.strip(): v.strip()
                for k, v in (s.split(":", 1) for s in node.get("style", "").split(";") if ":" in s)
            }
        )
        if style.get("display") == "none" or float(style.get("opacity", 1)) == 0:
            return
        if tag == "path":
            if (
                style.get("stroke", "none") == "none"
                or style.get("visibility") in ("hidden", "collapse")
                or float(style.get("stroke-opacity", 1)) == 0
            ):
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
                    stroke = stroke_style(style)
                    if stroke:
                        curves.append(VectorStroke(points, stroke))
        child_style = {k: v for k, v in style.items() if k in inherited_properties}
        for child in node:
            visit(child, matrix, child_style)

    visit(root, np.eye(3), {})
    return curves, grid


def refine_tick_positions(image, frame, grid, bounds):
    """Refine a reported tick only if it aligns with a nearby grid stroke."""
    left, bottom, right, top = bounds
    ticks = {}
    if not grid:
        pixels = np.asarray(image.convert("RGB"))
        neutral_ink = (np.ptp(pixels, axis=2) < 18) & (pixels.max(axis=2) < 245)
    for axis, (name, pair, start, stop) in enumerate(
        (("x", frame.ticks.x, top, bottom), ("y", frame.ticks.y, left, right))
    ):
        size = image.size[axis] - 1
        if grid:
            # A long vector grid line is distinguishable from a tick on a nearby plot.
            options = [
                pos
                for direction, pos, lo, hi in grid
                if direction == axis and min(hi, stop) - max(lo, start) >= 0.6 * (stop - start)
            ]
        else:
            cross_size = image.size[1 - axis] - 1
            ink = neutral_ink if axis == 0 else neutral_ink.T
            coverage = ink[round(start * cross_size) : round(stop * cross_size) + 1].mean(axis=0)
            marks = np.flatnonzero(coverage >= 0.55)
            strokes = np.split(marks, np.where(np.diff(marks) > 1)[0] + 1)
            options = [stroke.mean() / size for stroke in strokes if 0 < len(stroke) <= 5]
        ticks[name] = []
        for pos, value in pair:
            nearby = {round(float(v), 10) for v in options if abs(v - pos) * size <= 5}
            # Keep vector precision at the frame edges (including x = 0).
            refined = min(options, key=lambda v: abs(v - pos)) if len(nearby) == 1 else pos
            ticks[name].append((float(refined), value))
    return ticks


def stroke_mask(pixels, rgb):
    # Chroma direction is stable when antialiasing blends a stroke with white/gray.
    chroma = pixels - pixels.mean(axis=2, keepdims=True)
    target = np.asarray(rgb, dtype=float)
    target -= target.mean()
    if np.ptp(rgb) >= 20:
        magnitude = np.linalg.norm(chroma, axis=2)
        ink = (magnitude >= 12) & ((chroma @ (target / np.linalg.norm(target))) >= 0.94 * magnitude)
    else:
        ink = (pixels.max(axis=2) < min(210, max(rgb) + 65)) & (np.ptp(pixels, axis=2) < 25)
    return ink


def trace_pixels(image, bounds, anchors, rgb):
    """Follow a single 8-connected stroke inside the plot; never join separate pieces."""
    w, h = image.size
    left, bottom, right, top = bounds
    x0, x1 = math.ceil(left * (w - 1)), math.floor(right * (w - 1))
    y0, y1 = math.ceil(top * (h - 1)), math.floor(bottom * (h - 1))
    if x1 <= x0 or y1 <= y0 or x1 - x0 + 1 > 2048:
        return None
    pixels = np.asarray(image.crop((x0, y0, x1 + 1, y1 + 1)), dtype=np.float32)
    ink = stroke_mask(pixels, rgb)
    labels, count = ndimage.label(ink, structure=np.ones((3, 3), dtype=bool))
    if not count:
        return None
    radius = max(3, round(0.005 * max(w, h)))
    common = None
    for x, y in anchors:
        ix, iy = round(x * (w - 1)) - x0, round(y * (h - 1)) - y0
        nearby = set(
            np.unique(labels[max(0, iy - radius) : iy + radius + 1, max(0, ix - radius) : ix + radius + 1])
        ) - {0}
        common = nearby if common is None else common & nearby
        if not common:
            return None
    if len(common) != 1:
        return None
    ys, xs = np.nonzero(labels == common.pop())
    first, last = xs.min(), xs.max()
    columns = xs - first
    low = np.full(last - first + 1, y1 - y0 + 1)
    high = np.full(last - first + 1, -1)
    np.minimum.at(low, columns, ys)
    np.maximum.at(high, columns, ys)
    thickness = high - low + 1
    # Forks, lettering and merged grid lines cannot define one center y per column.
    if not np.array_equal(thickness, np.bincount(columns)) or thickness.max() > max(
        8, 4 * np.median(thickness)
    ):
        return None
    return np.column_stack(
        ((np.arange(first, last + 1) + x0) / (w - 1), ((high + low) / 2 + y0) / (h - 1))
    ).tolist()


def measure_ink(image, positions, rgb, dashed=False):
    """Measure distance from the reconstructed line to actual matching ink in the source raster."""
    w, h = image.size
    px, py = np.asarray(positions).T * np.array([[w - 1], [h - 1]])
    columns = np.arange(math.ceil(px[0]), math.floor(px[-1]) + 1)
    if len(columns) < 3:
        return None
    ys = np.interp(columns, px, py)
    margin = 8
    left, right = max(0, columns[0] - margin), min(w, columns[-1] + margin + 1)
    top, bottom = max(0, int(ys.min()) - margin), min(h, math.ceil(ys.max()) + margin + 1)
    pixels = np.asarray(image.crop((left, top, right, bottom)), dtype=np.float32)
    ink = stroke_mask(pixels, rgb)
    if not ink.any():
        return None
    distances = ndimage.distance_transform_edt(~ink)
    errors = ndimage.map_coordinates(distances, [ys - top, columns - left], order=1, mode="nearest")
    coverage = float(np.mean(errors <= 2))
    limit = 7 if dashed else 2.5
    p95 = float(np.percentile(errors, 95))
    unsupported = errors > limit
    gaps = np.diff(np.flatnonzero(np.diff(np.r_[False, unsupported, False])))[::2]
    if not dashed and np.any(gaps > 4):
        return None
    check = InkCheck(coverage=coverage, p95_distance_px=p95, measured_columns=len(columns))
    return check if check.matches_stroke(dashed) and max(errors[0], errors[-1]) <= limit else None


def candidate(image, paths, grid, plan, *, allow_two=False):
    # The AI supplies normalized page locations. Axis crossing positions and
    # samples are refined against the original vector geometry wherever possible.
    marks = AxisTicks.model_validate(plan["ticks"])

    def scale_for(axis, ticks, size):
        if len(ticks) == 2 and allow_two:
            return plan[axis + "_scale"]
        return axis_fit(ticks, size - 1)[0]

    scales = {
        axis + "_scale": scale_for(axis, getattr(marks, axis), size)
        for axis, size in zip(("x", "y"), image.size)
    }
    frame = Calibration.model_validate({**plan, **scales, "method": "image", "positions": plan["anchors"]})
    if not frame.ticks or not frame.identity:
        return None
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
    ticks = refine_tick_positions(image, frame, grid, (left, bottom, right, top))
    if not grid:
        # A printed tick at a plot boundary also locates that boundary in the raster.
        for original, refined in zip(frame.ticks.x, ticks["x"]):
            if original[1] == frame.x_range[0]:
                left = refined[0]
            if original[1] == frame.x_range[1]:
                right = refined[0]
        for original, refined in zip(frame.ticks.y, ticks["y"]):
            if original[1] == frame.y_range[0]:
                bottom = refined[0]
            if original[1] == frame.y_range[1]:
                top = refined[0]
    ranked = []
    stroke = None
    for trace in paths:
        style = trace.style
        rgb = np.array(style.rgb, dtype=float)
        target = np.array(frame.identity.rgb, dtype=float)
        color_distance = np.linalg.norm(rgb - target)
        if color_distance > 100 or frame.identity.line_style not in ("unknown", style.pattern):
            continue
        x, y = trace.points.T
        if x[0] > anchors[0, 0] + 0.008 or x[-1] < anchors[-1, 0] - 0.008:
            continue
        residual = np.abs(np.interp(anchors[:, 0], x, y) - anchors[:, 1])
        if residual.max() <= 0.012:
            # Between nearby paths, prefer the stroke whose PDF color matches the identified ink.
            ranked.append((float(np.mean(residual)) + 0.003 * color_distance / 100, trace))
    ranked.sort(key=lambda pair: pair[0])
    if ranked and (len(ranked) == 1 or ranked[1][0] - ranked[0][0] > 0.001):
        best = ranked[0][1]
        stroke = best.style
        x, y = best.points.T
        a, b = max(left, x[0]), min(right, x[-1])
        if a >= b:
            return None
        # Include the original vertices so a short knee cannot disappear between regular samples.
        vertices = np.r_[a, x[(a < x) & (x < b)], b]
        regular = np.linspace(a, b, 81)[1:-1]
        following = np.searchsorted(vertices, regular)
        separation = np.minimum(regular - vertices[following - 1], vertices[following] - regular)
        # Sub-pixel coincidences can round to the same engineering x when persisted.
        xs = np.sort(np.r_[vertices, regular[separation * (image.width - 1) > 0.001]])
        if len(xs) > 2048:
            xs = xs[np.linspace(0, len(xs) - 1, 2048, dtype=int)]
        pts = [[float(u), float(v)] for u, v in zip(xs, np.interp(xs, x, y)) if top <= v <= bottom]
        if len(pts) < 3:
            return None
        interior = y[(x > pts[0][0]) & (x < pts[-1][0])]
        if np.any((interior < top) | (interior > bottom)):
            return None  # The visible trace has a gap; a single lookup range would bridge it.
        method = "vector"
    else:
        if ranked or frame.identity.line_style == "dashed":
            return None
        pts = trace_pixels(image, (left, bottom, right, top), anchors, frame.identity.rgb)
        if pts is None:
            return None
        method = "pixel_trace"
    if len(pts) < 3:
        return None
    axis_error = {}
    for axis, size in zip(("x", "y"), image.size):
        if len(ticks[axis]) > 2:
            scale, error = axis_fit(ticks[axis], size - 1)
            if scale != scales[axis + "_scale"]:
                return None
            axis_error[axis] = error
        else:
            axis_error[axis] = None
    ink_check = measure_ink(
        image,
        pts,
        stroke.rgb if stroke else frame.identity.rgb,
        dashed=bool(stroke and stroke.pattern == "dashed"),
    )
    if ink_check is None:
        return None
    return Calibration.model_validate(
        {
            **frame.model_dump(),
            "lower_left": [left, bottom],
            "upper_right": [right, top],
            "method": method,
            "ticks": ticks,
            "stroke": stroke,
            "ink_check": ink_check,
            "axis_error_px": axis_error,
            "image_size": image.size,
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
    # Leave the line between markers visible to the independent reviewer.
    indexes = np.linspace(0, len(calibration.positions) - 1, min(40, len(calibration.positions)), dtype=int)
    if calibration.ticks:
        for pos, _ in calibration.ticks.x:
            x, _ = pixel((pos, calibration.lower_left[1]))
            draw.line((x, b - 5, x, b + 5), fill="#003aff", width=2)
        for pos, _ in calibration.ticks.y:
            _, y = pixel((calibration.lower_left[0], pos))
            draw.line((l - 5, y, l + 5, y), fill="#003aff", width=2)
    for i in indexes:
        p = calibration.positions[i]
        x, y = pixel(p)
        draw.ellipse((x - 3, y - 3, x + 3, y + 3), outline="#ee006a", width=2)
    out = io.BytesIO()
    annotated.save(out, format="PNG")
    return out.getvalue()
