"""Read the digitized 470M curve in the TDK SLF12565 data sheet."""
import csv
import math
from bisect import bisect_left
from pathlib import Path

PART_NUMBER = 'SLF12565T-470M2R4-PF'
CURVE_ID = 'tdk-slf12565t-470m2r4-pf-dc-bias'
with Path(__file__).with_name(f'TDK_{PART_NUMBER}_p3.csv').open(encoding='utf-8', newline='') as stream:
    POINTS = [(float(row['dc_current_A']), float(row['inductance_uH'])) for row in csv.DictReader(stream)]
CURRENTS = [point[0] for point in POINTS]

DOCUMENT = {
    'id': CURVE_ID, 'title': f'TDK {PART_NUMBER} 电感量与直流偏置电流（470M）',
    'tags': f'TDK SLF12565 {PART_NUMBER} 470M 直流偏置 电感 电流 查表 DC bias inductance current',
    'url': 'https://product.tdk.com/system/files/dam/doc/product/inductor/inductor/smd/catalog/inductor_commercial_power_slf12565_en.pdf',
    'content': ('TDK SLF12565 手册（20240527），PDF 第 3 页，Inductance vs. DC Bias Characteristics，'
                f'图中 470M 对应第 1 页料号 {PART_NUMBER}。横轴直流电流 A 为线性刻度，纵轴电感量 µH 为对数刻度。'
                f'从原图矢量中心线提取 {len(POINTS)} 点，范围 {CURRENTS[0]:g} 至 {CURRENTS[-1]:g} A。'
                '按电流查值时用 read_knowledge 传入此条目 ID 和 dc_current_A。返回读图近似值，点间按电流线性插值，'
                '整张图横轴到 10 A，这一料号的线本身在约 4.03 A 结束。5 A 处原图也没有该曲线读数。'
                '超出范围时应返回无数据。该曲线用于比较偏流趋势，元件故障或安全工作限值需另外验证。')
}


def lookup(current):
    if type(current) not in (int, float) or not math.isfinite(current):
        raise ValueError('dc_current_A must be a finite number in amperes')
    result = {'part_number': PART_NUMBER, 'pdf_page': 3, 'dc_current_A': current,
              'range_A': [CURRENTS[0], CURRENTS[-1]], 'value_source': 'digitized_pdf_vector'}
    if not CURRENTS[0] <= current <= CURRENTS[-1]:
        return {**result, 'status': 'outside_digitized_range', 'inductance_uH': None}
    index = bisect_left(CURRENTS, current)
    right, right_L = POINTS[index]
    if current == right:
        return {**result, 'status': 'digitized_point', 'inductance_uH': right_L}
    left, left_L = POINTS[index - 1]
    estimate = left_L + (right_L - left_L) * ((current - left) / (right - left))
    return {**result, 'status': 'linear_interpolation', 'inductance_uH': round(estimate, 1),
            'bracket_A': [left, right]}
