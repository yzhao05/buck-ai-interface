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
            curves[row['id']] = (xs, ys, x_name, x_unit, y_name, y_unit, row)
            content += (f"\n{x_name} [{x_unit}] → {y_name} [{y_unit}]. {row['value_source']}。"
                        f"{row['conditions']}\n{len(xs)} points, x = {xs[0]} … {xs[-1]}. "
                        'read_knowledge accepts x_value in the stated x unit. Sample pairs (x,y):\n'
                        + ' '.join(f'({x:g},{y:g})' for x, y in points))
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
    if value < xs[0] or value > xs[-1]:
        return {**result, 'status': 'outside_sampled_range'}
    i = bisect_left(xs, value)
    if xs[i] == value:
        return {**result, 'status': 'sample', 'y': ys[i]}
    if row['manual_id'] == '08' and row['locator'] == '3' and xn == 'DC bias current':
        fraction = (value - xs[i-1]) / (xs[i] - xs[i-1])
        return {**result, 'status': 'linear_interpolation', 'y': round(ys[i-1] + fraction * (ys[i] - ys[i-1]), 2),
                'neighboring_points': [[xs[i-1], ys[i-1]], [xs[i], ys[i]]]}
    return {**result, 'status': 'between_samples', 'neighboring_points': [[xs[i-1], ys[i-1]], [xs[i], ys[i]]]}
