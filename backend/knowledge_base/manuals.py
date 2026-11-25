"""Load the bundled reference notes or an explicitly configured manual archive."""
import csv
import json
import math
import os
from bisect import bisect_left
from pathlib import Path


PUBLIC_CSV = Path(__file__).with_name('manual_references.csv')


def load(path):
    with PUBLIC_CSV.open(encoding='utf-8-sig', newline='') as stream:
        editions = {r['manual_id']: r['edition_url'] for r in csv.DictReader(stream) if r['edition_url']}
    with path.open(encoding='utf-8-sig', newline='') as stream:
        return parse(csv.DictReader(stream), editions)


def parse(rows, editions):
    documents = []
    curves = {}
    for row in rows:
        origin = row['source_file']
        locator = row['locator']
        location = f'PDF p. {locator}' if (origin.lower().endswith('.pdf') or row['manual_id'].startswith('pdf-')) and locator.isdigit() else locator
        content = f"{origin} · {location}\n{row['content']}"
        if row["kind"] == "curve_pending":
            content += "\n此曲线尚未数字化，仅有图例定位信息，无已核对的数值采样点。"
        if row['kind'] == 'curve':
            x_name, x_unit, y_name, y_unit = json.loads(row['axes_json'])
            points = json.loads(row['points_json'])
            xs, ys = zip(*points)
            if not all(math.isfinite(v) for pair in points for v in pair) or any(a >= b for a, b in zip(xs, xs[1:])):
                raise ValueError(f"Invalid curve samples in {row['id']}")
            if row.get("series_scope") == "part" and row.get("part_reference_json"):
                ref = json.loads(row["part_reference_json"])
                content += f"\n{ref['label']} → {ref['part_number']} (PDF p. {ref['page']})"
            curves[row['id']] = (xs, ys, x_name, x_unit, y_name, y_unit, row)
            content += (f"\n{x_name} [{x_unit}] → {y_name} [{y_unit}]. {row['value_source']}。"
                        f"{row['conditions']}\n{len(xs)} points, x = {xs[0]} … {xs[-1]}. "
                        'read_knowledge accepts x_value in the stated x unit. Sample pairs (x,y):\n'
                        + (' '.join(f'({x:g},{y:g})' for x, y in points) if len(points)<=81
                           else 'Dense trace stored; use read_knowledge with x_value for an individual reading.'))
        edition = editions.get(row['manual_id'])
        references = [{'title': '厂家来源', 'url': row['url']}] if row['url'] else []
        if edition:
            references.append({'title': '归档版本 PDF', 'url': edition})
        documents.append({'id': row['id'], 'title': row['title'], 'kind': 'manual',
                          'manual_id': row['manual_id'], 'source_file': origin, 'locator': locator,
                          'summary': row['content'][:180] if row['kind'] == 'overview' else f"{location} · {row['title']}",
                          'tags': f"{origin} {row['kind']} {row['title']}",
                          'url': edition or row['url'], 'references': references, 'content': content})
    return documents, curves


DOCUMENTS, CURVES = load(Path(os.environ.get('BUCK_MANUAL_ARCHIVE') or PUBLIC_CSV))


def lookup(key, value, curves=None):
    curves = CURVES if curves is None else curves
    if key not in curves or type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('Select one manual curve document and a finite x_value in its stated unit')
    xs, ys, xn, xu, yn, yu, row = curves[key]
    result = {'source_file': row['source_file'], 'locator': row['locator'], 'series': row['title'],
              'x_quantity': xn, 'x_unit': xu, 'x': value, 'y_quantity': yn, 'y_unit': yu,
              'y': None, 'range': [xs[0], xs[-1]], 'value_source': row['value_source'],
              'conditions': row['conditions']}
    calibration = json.loads(row["calibration_json"]) if row.get("digitization_version") in ("2", "3") and row.get("calibration_json") else {}
    if row.get("series_scope") == "part" and row.get("part_reference_json"):
        result["part_reference"] = json.loads(row["part_reference_json"])
    if calibration:
        if calibration.get("ink_check"):
            result.update(method=calibration["method"], ink_check=calibration["ink_check"], axis_error_px=calibration.get("axis_error_px"), stroke=calibration.get("stroke"))
        result["tick_pixels"] = {
            axis: [[round(pos * (size - 1), 2), mark] for pos, mark in calibration["ticks"][axis]]
            for axis, size in zip(("x", "y"), calibration["image_size"])
        }
        result.update(ticks=calibration["ticks"], x_scale=calibration["x_scale"], y_scale=calibration["y_scale"],
                      identity=calibration["identity"], image_size=calibration["image_size"])
    if value < xs[0] or value > xs[-1]:
        return {**result, 'status': 'outside_sampled_range'}
    i = bisect_left(xs, value)
    if calibration.get("method") == "pixel_trace":
        # Match the nearest column in image coordinates, including on logarithmic x axes.
        transform = math.log if calibration["x_scale"] == "log" else float
        marks = calibration["ticks"]["x"]
        (p0, v0), (p1, v1) = marks[0], marks[-1]
        fraction = (transform(value) - transform(v0)) / (transform(v1) - transform(v0))
        w, h = calibration["image_size"]
        column = math.floor((p0 + fraction * (p1 - p0)) * (w - 1) + 0.5 + 1e-9)
        first = round(calibration["positions"][0][0] * (w - 1))
        i = max(0, min(column - first, len(xs) - 1))
        x, y = calibration["positions"][i]
        return {**result, 'status':'pixel_column', 'y':float(format(ys[i],'.3g')), 'sample_x':xs[i],
                'pixel_center':[round(x*(w-1)),round(y*(h-1),2)]}
    if xs[i] == value:
        y = float(format(ys[i], '.3g')) if calibration.get('ink_check') else ys[i]
        return {**result, 'status': 'sample', 'y': y}
    if calibration.get("method") == "vector":
        coord = math.log if calibration["x_scale"]=="log" else float
        t=(coord(value)-coord(xs[i-1]))/(coord(xs[i])-coord(xs[i-1]))
        a,b=ys[i-1],ys[i]
        y=math.exp((1-t)*math.log(a)+t*math.log(b)) if calibration["y_scale"]=="log" else (1-t)*a+t*b
        return {**result,"status":"axis_interpolation","y":float(format(y,".3g")),
                "neighboring_points":[[xs[i-1],a],[xs[i],b]]}
    if row['manual_id'] == '08' and row['locator'] == '3' and xn == 'DC bias current':
        fraction = (value - xs[i-1]) / (xs[i] - xs[i-1])
        return {**result, 'status': 'linear_interpolation', 'y': round(ys[i-1] + fraction * (ys[i] - ys[i-1]), 2),
                'neighboring_points': [[xs[i-1], ys[i-1]], [xs[i], ys[i]]]}
    return {**result, 'status': 'between_samples', 'neighboring_points': [[xs[i-1], ys[i-1]], [xs[i], ys[i]]]}
