"""Readable PDF from a bounded result snapshot, including restored historical results.

No new fitting, LLM calls, file reads or remote image fetches occur during export.
The JSON remains the complete machine-readable experiment record.
"""
from datetime import datetime
import math
from io import BytesIO
from pathlib import Path
from typing import Annotated, Literal
from xml.sax.saxutils import escape

from pydantic import BaseModel, ConfigDict, Field, model_validator
from reportlab.graphics.shapes import Drawing, Line, PolyLine, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether

pdfmetrics.registerFont(TTFont("BuckReportSans", str(Path(__file__).parent / "assets/fonts/BuckReportSans-Regular.ttf")))
Number = Annotated[float, Field(allow_inf_nan=False, ge=-1e30, le=1e30)]
Points = Annotated[list[Number], Field(min_length=2, max_length=10000)]


class Snapshot(BaseModel):
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)


class Parameters(Snapshot):
    L_H: Number
    C_F: Number
    R_ohm: Number


class Metrics(Snapshot):
    current_rmse_A: Number
    voltage_rmse_V: Number
    current_nrmse_pct: Number | None = None
    voltage_nrmse_pct: Number | None = None


class ReportOptions(Snapshot):
    vin: Number | None = None
    frequency_hz: Number | None = None
    pwm_source: Literal["measured", "controller"] | None = None
    epochs: int | None = None
    pann_initial_state: Literal["joint", "measured"] | None = None
    pann_window_cycles: int | None = None
    controller: dict[Annotated[str, Field(max_length=64)], Number] | None = Field(default=None, max_length=20)


class Reason(Snapshot):
    code: str = Field(max_length=64)
    value: Number | None = None
    threshold: Number | None = None
    completed_epochs: int | None = None
    requested_epochs: int | None = None
    best_epoch: int | None = None


class Chart(Snapshot):
    time_s: Points
    current_A: Points
    voltage_V: Points
    predicted_current_A: Points | None = None
    predicted_voltage_V: Points | None = None

    @model_validator(mode="after")
    def aligned(self):
        for values in (self.current_A, self.voltage_V, self.predicted_current_A, self.predicted_voltage_V):
            if values is not None and len(values) != len(self.time_s):
                raise ValueError("Waveform arrays must have matching lengths.")
        if any(b <= a for a, b in zip(self.time_s, self.time_s[1:])):
            raise ValueError("Waveform time must increase strictly.")
        return self


class TrainingAssessment(Snapshot):
    version: str = Field(default="tail-stability-v1", max_length=64)
    status: Literal["stable", "not_stable", "insufficient_history", "not_applicable"]
    thresholds: dict[Annotated[str, Field(max_length=64)], Number] = Field(default_factory=dict, max_length=12)
    stable_checks: int = 0
    start_epoch: int | None = None
    end_epoch: int | None = None
    loss_span_pct: Number | None = None
    loss_change_pct: Number | None = None
    parameter_span_pct: dict[Annotated[str, Field(max_length=16)], Number] = Field(default_factory=dict, max_length=3)
    selected_parameter_gap_pct: dict[Annotated[str, Field(max_length=16)], Number] = Field(default_factory=dict, max_length=3)
    selected_loss_gap_pct: Number | None = None
    previous_start_epoch: int | None = None
    previous_end_epoch: int | None = None
    previous_mean_loss: Number | None = None
    current_mean_loss: Number | None = None
    loss_mean_change_pct: Number | None = None
    loss_absolute_change: Number | None = None
    loss_absolute_tolerance_used: bool = False


class Assessment(Snapshot):
    training: TrainingAssessment


class GroundTruth(Snapshot):
    L_H: float = Field(gt=0, le=1e30, allow_inf_nan=False)
    C_F: float = Field(gt=0, le=1e30, allow_inf_nan=False)


class ParameterComparison(Snapshot):
    ground_truth: GroundTruth


class ReportResult(Snapshot):
    filename: str = Field(default="dataset.csv", max_length=512)
    file_sha256: str = Field(default="", max_length=64, pattern=r"^[a-fA-F0-9]*$")
    method: Literal["pann", "weighted_integral", "integral"]
    quality: Literal["需复核", "模型内拟合一致"]
    parameters: Parameters
    R_estimated: bool
    metrics: Metrics
    options: ReportOptions = Field(default_factory=ReportOptions)
    review_reasons: list[Reason] = Field(default_factory=list, max_length=12)
    train_start_s: Number | None = None
    train_end_s: Number | None = None
    validation_end_s: Number | None = None
    elapsed_s: Number | None = None
    chart: Chart | None = None
    assessment: Assessment | None = None
    parameter_comparison: ParameterComparison | None = None

    def parameter_errors(self):
        if not self.parameter_comparison:
            return None
        truth = self.parameter_comparison.ground_truth
        return {"L": abs(self.parameters.L_H/truth.L_H-1)*100,
                "C": abs(self.parameters.C_F/truth.C_F-1)*100}

    @model_validator(mode="after")
    def finite_parameter_errors(self):
        errors = self.parameter_errors()
        if errors and not all(math.isfinite(v) for v in errors.values()):
            raise ValueError("Parameter relative error exceeds the finite range.")
        return self


class ReportRequest(Snapshot):
    language: Literal["zh", "en"] = "zh"
    exported_at: datetime
    result: ReportResult
    notes: list[Annotated[str, Field(max_length=2000)]] = Field(default_factory=list, max_length=40)


def build_pdf(request: ReportRequest) -> bytes:
    r = request.result
    parameter_errors = r.parameter_errors()
    zh = request.language == "zh"
    t = lambda cn, en: cn if zh else en
    # Embedded OFL font preserves Chinese filenames and SI units in both languages.
    font = "BuckReportSans"
    ink, teal, muted = colors.HexColor("#213b43"), colors.HexColor("#107e71"), colors.HexColor("#61747c")
    body = ParagraphStyle("body", fontName=font, fontSize=10, leading=16, textColor=ink, wordWrap="CJK", spaceAfter=7)
    heading = ParagraphStyle("heading", parent=body, fontSize=15, leading=21, textColor=teal, spaceBefore=12, spaceAfter=12, keepWithNext=True)
    title = ParagraphStyle("title", parent=heading, fontSize=23, leading=30)
    small = ParagraphStyle("small", parent=body, fontSize=8, leading=12, textColor=muted)
    p = lambda text, style=body: Paragraph(escape(str(text)), style)
    n = lambda value, scale=1: "-" if value is None else f"{value * scale:.6g}"
    stream = BytesIO()
    doc = SimpleDocTemplate(stream, pagesize=A4, rightMargin=42, leftMargin=42, topMargin=44, bottomMargin=44,
                            title=t("Buck 电路参数辨识实验报告", "Buck parameter identification report"), author="Buck Lab")
    story = []

    def section(cn, en):
        story.append(p(t(cn, en), heading))

    def table(rows, widths=(175, 336)):
        grid = Table([[p(a), p(b)] for a, b in rows], colWidths=widths, hAlign="LEFT")
        grid.setStyle(TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"), ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#edf5f3")),
            ("LINEBELOW", (0, 0), (-1, -1), .4, colors.HexColor("#dce6e6")),
            ("LEFTPADDING", (0, 0), (-1, -1), 10), ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(grid)

    story += [p("BUCK LAB / EXPERIMENT REPORT", small), p(t("电路参数辨识实验报告", "Circuit parameter identification"), title),
              p(r.filename), p(t("导出时间：", "Exported: ") + request.exported_at.isoformat(timespec="seconds").replace("T", " "), small)]
    method = {"pann": "PANN", "weighted_integral": t("加权积分", "Weighted integral"), "integral": t("积分", "Integral")}[r.method]
    drive = {"controller": t("由已知控制器重建 PWM", "PWM reconstructed from known controller"),
             "measured": t("CSV 实测 PWM", "Measured PWM from CSV")}.get(r.options.pwm_source, t("未记录", "Not recorded"))
    section("01  数据与方法", "01  Data and method")
    table([(t("辨识方法", "Method"), method), (t("开关信息来源", "Switching information"), drive),
           (t("输入电压 / 开关频率", "Input / switching frequency"), f"{n(r.options.vin)} V / {n(r.options.frequency_hz, .001)} kHz"),
           (t("训练区间", "Training interval"), f"{n(r.train_start_s, 1000)} - {n(r.train_end_s, 1000)} ms"),
           (t("同记录留出区间", "Same-record holdout"), f"{n(r.train_end_s, 1000)} - {n(r.validation_end_s, 1000)} ms"),
           (t("计算耗时", "Computation time"), n(r.elapsed_s) + " s")])
    section("02  参数辨识结果", "02  Identified parameters")
    table([(t("预测电感 L", "Predicted inductance L"), n(r.parameters.L_H, 1e6) + " µH"),
           (t("预测电容 C", "Predicted capacitance C"), n(r.parameters.C_F, 1e6) + " µF"),
           (t("负载电阻 R", "Load resistance R"), n(r.parameters.R_ohm) + " Ω / " +
            (t("估计值", "Estimated") if r.R_estimated else t("固定已知值，未参与辨识", "Fixed known value, not fitted")))])
    if parameter_errors is not None:
        truth=r.parameter_comparison.ground_truth
        section("参数真值对照", "Parameter ground-truth comparison")
        table([(t("真实电感 L / 相对误差", "True inductance L / relative error"), f"{n(truth.L_H,1e6)} µH / {n(parameter_errors['L'])}%"),
               (t("真实电容 C / 相对误差", "True capacitance C / relative error"), f"{n(truth.C_F,1e6)} µF / {n(parameter_errors['C'])}%")])
        story.append(p(t("相对误差 = |预测值 - 真实值| / 真实值 × 100%。真值由用户提供，未独立核验，未参与训练。", "Relative error = |predicted - true| / true × 100%. Ground truth is user-provided, not independently verified, and not used in training."), small))
    section("03  留出验证表现", "03  Holdout validation")
    table([(t("电流 RMSE / 归一化 RMSE", "Current RMSE / NRMSE"), f"{n(r.metrics.current_rmse_A, 1000)} mA / {n(r.metrics.current_nrmse_pct)}%"),
           (t("电压 RMSE / 归一化 RMSE", "Voltage RMSE / NRMSE"), f"{n(r.metrics.voltage_rmse_V, 1000)} mV / {n(r.metrics.voltage_nrmse_pct)}%")])
    story.append(p(t("R 为负载电阻，非 ESR。波形误差不等于参数误差；未提供真值时不能计算参数真实误差。",
                     "R is load resistance, not ESR. Waveform error is not parameter error; true parameter error requires ground-truth values."), small))
    story.append(PageBreak())
    section("04  测量波形与模型预测", "04  Measurements and predictions")
    story.append(p(t("绿色实线：测量；橙色虚线：预测；灰色区域：同记录留出验证区间。",
                     "Solid teal: measured; dashed orange: predicted; shaded area: same-record holdout.")))

    def plot(channel, predicted, label):
        chart = r.chart
        if chart is None:
            story.append(p(t("历史结果未保存波形，完整诊断请参阅 JSON。", "Waveforms were not saved with this result. See JSON for the available diagnostics.")))
            return
        times, ys, ps = chart.time_s, getattr(chart, channel), getattr(chart, predicted)
        values = ys + (ps or [])
        low, high = min(values), max(values)
        pad = max((high-low)*.1, abs(high)*.01, .001)
        low, high = low-pad, high+pad
        x = lambda value: 52 + (value-times[0])/(times[-1]-times[0])*440
        y = lambda value: 35 + (value-low)/(high-low)*175
        drawing = Drawing(511, 250)
        drawing.add(String(0, 234, label, fontName=font, fontSize=11, fillColor=ink))
        if r.train_end_s is not None and r.train_end_s < times[-1]:
            start = x(max(times[0], r.train_end_s))
            drawing.add(Rect(start, 35, 492-start, 175, fillColor=colors.HexColor("#eef2f5"), strokeColor=None))
        for k in range(5):
            value = low+(high-low)*k/4
            drawing.add(Line(52, y(value), 492, y(value), strokeColor=colors.HexColor("#dae4e6"), strokeWidth=.4))
            drawing.add(String(46, y(value)-3, f"{value:.4g}", textAnchor="end", fontName=font, fontSize=8, fillColor=muted))
        for k in range(6):
            value = times[0]+(times[-1]-times[0])*k/5
            drawing.add(String(x(value), 19, f"{value*1000:.4g}", textAnchor="middle", fontName=font, fontSize=8, fillColor=muted))
        drawing.add(String(492, 2, t("时间 (ms)", "Time (ms)"), textAnchor="end", fontName=font, fontSize=9, fillColor=muted))
        for data, color, dash in ((ys, teal, None), (ps, colors.HexColor("#ce8737"), [4, 3])):
            if data is not None:
                points = [coord for time, value in zip(times, data) for coord in (x(time), y(value))]
                drawing.add(PolyLine(points, strokeColor=color, strokeWidth=.8, strokeDashArray=dash))
        story.append(drawing)

    plot("current_A", "predicted_current_A", t("电感电流 (A)", "Inductor current (A)"))
    plot("voltage_V", "predicted_voltage_V", t("输出电压 (V)", "Output voltage (V)"))
    story.append(p(t("图形采用结果中保存的显示采样点；RMSE 来自完整计算数据，不从图上重新计算。",
                     "Plots use saved display samples. RMSE comes from the full computation data, not these plotted samples."), small))
    story.append(p(t("这是同一记录后段的留出验证，不是独立工况验证。", "This is a holdout from the same recording, not an independent operating-condition test.")))
    if r.options.pwm_source == "controller":
        story.append(p(t("重建只使用训练段电压与已知控制器；留出段采用自主闭环预测，不使用留出实测电压或 PWM 驱动。",
                         "Reconstruction uses training voltage and the known controller. Holdout prediction runs in closed loop without held-out voltage or measured PWM as input.")))
    story.append(PageBreak())
    section("05  质量标记、触发原因与下一步", "05  Quality flags and next steps")
    review = bool(r.review_reasons) or r.quality == "需复核"
    story.append(p(t("质量状态：需复核。辨识已完成，以下提醒不等同于辨识失败。", "Status: review required. Identification completed; these flags do not mean the fit failed.")
                   if review else t("质量状态：模型内拟合一致。当前检查未触发提醒，但不能据此认定参数真值或跨工况有效性。",
                                    "Status: consistent within the model. No current check triggered a flag; this does not establish true parameter accuracy or validity across conditions.")))
    for reason in r.review_reasons:
        details, action = reason_text(reason, zh)
        story.append(KeepTogether([p(details), p(t("建议：", "Next step: ") + action, small), Spacer(1, 7)]))
    if review and not r.review_reasons:
        story.append(p(t("历史记录未保存可确认的触发原因。请查看 JSON 诊断或重新辨识，不推测原因。",
                         "This historical result has no identifiable reason recorded. Inspect JSON diagnostics or rerun identification.")))
    story.append(p(t("建议保留本次 PDF 和 JSON，使用相同数据对照另一种辨识方法；再用独立工况或已知真值检查结果。",
                     "Keep both PDF and JSON. Compare another identification method on the same data, then validate against an independent condition or known ground truth.")))
    if r.method == "pann":
        section("PANN 配置", "PANN settings")
        window = r.options.pann_window_cycles
        table([(t("请求训练轮数", "Requested epochs"), str(r.options.epochs) if r.options.epochs is not None else "-"),
               (t("窗口周期数", "Window cycles"), t("自动选择", "Automatic") if window == 0 else str(window) if window is not None else "-"),
               (t("初始状态", "Initial state"), {"joint": t("联合优化", "Joint optimization"), "measured": t("测量值", "Measured")}.get(r.options.pann_initial_state, "-"))])
    section("06  分项判断", "06  Separate assessments")
    waveform = all(v is not None and v <= 5 for v in (r.metrics.current_nrmse_pct, r.metrics.voltage_nrmse_pct))
    training = r.assessment.training if r.assessment else TrainingAssessment(status="insufficient_history" if r.method == "pann" else "not_applicable")
    statuses = {
        "stable": t("末段稳定（经验判据，非收敛证明）", "Stable training tail (heuristic, not proof of convergence)"),
        "not_stable": t("尚未达到末段稳定判据", "Training-tail stability criteria not met"),
        "insufficient_history": t("证据不足：轮数过少或逐轮数据未记录", "Insufficient epochs or per-epoch history unavailable"),
        "not_applicable": t("不适用：积分方法无迭代训练", "Not applicable: no iterative training")}
    if training.version == "two-window-stability-v2":
        statuses["stable"] = t("训练已稳定（试用判据，待实验校准）", "Training is stable (trial rule, pending experimental calibration)")
    if not waveform:
        statuses["stable"] = t("训练已稳定，但拟合未达标（经验判据）", "Training is stable, but the waveform fit has not passed (heuristic)")
    table([(t("波形拟合", "Waveform fit"), t("达标：留出电流、电压 NRMSE 均不超过 5%", "Passed: both holdout NRMSE values at most 5%") if waveform else t("未达标或证据不足", "Not passed or insufficient evidence")),
           (t("训练稳定性", "Training stability"), statuses[training.status]),
           (t("参数准确性", "Parameter accuracy"), t("已对照用户提供的真值；真值未独立核验", "Compared with user-provided ground truth; reference not independently verified") if parameter_errors is not None else t("未验证：未提供真值", "Unverified: no ground truth provided"))])
    story.append(p(t("标签颜色不代表精度提升。训练稳定、波形拟合达标都不能证明参数准确。", "Badge color does not imply improved accuracy. Training stability and waveform fit do not prove parameter accuracy."), small))
    if training.thresholds and training.version == "two-window-stability-v2":
        th = training.thresholds
        table([(t("判据版本", "Rule version"), training.version),
               (t("窗口轮数 / 连续稳定次数要求", "Window size / checks required"), f"{n(th.get('window_epochs'))} / {n(th.get('required_stable_checks'))}"),
               (t("末段连续稳定次数 / 最少训练轮数", "Stable checks / min. epochs"), f"{training.stable_checks} / {n(th.get('minimum_epochs'))}"),
               (t("前窗口 / 后窗口轮次", "Previous / recent window epochs"), f"{n(training.previous_start_epoch)}-{n(training.previous_end_epoch)} / {n(training.start_epoch)}-{n(training.end_epoch)}"),
               (t("前窗口 / 后窗口平均损失", "Previous / recent mean loss"), f"{n(training.previous_mean_loss)} / {n(training.current_mean_loss)}"),
               (t("平均损失相对变化 / 要求", "Mean loss change / limit"), f"{n(training.loss_mean_change_pct)}% / < {n(th.get('loss_mean_change_pct'))}%"),
               (t("平均损失绝对变化 / 容差", "Absolute change / tolerance"), f"{n(training.loss_absolute_change)} / < {n(th.get('loss_absolute_tolerance'))}"),
               (t("绝对容差是否生效", "Absolute tolerance applied"), t("是", "Yes") if training.loss_absolute_tolerance_used else t("否", "No")),
               (t("参数峰峰变化 / 要求", "Parameter span / requirement"), "; ".join(f"{k}: {n(v)}%" for k,v in training.parameter_span_pct.items()) + f" / < {n(th.get('parameter_span_pct'))}%"),
               (t("损失归一化分母下限", "Loss denominator floor"), n(th.get('loss_scale_floor')))])
    elif training.thresholds:
        th = training.thresholds
        table([(t("窗口轮数 / 连续稳定次数要求", "Window epochs / required stable checks"), f"{n(th.get('window_epochs'))} / {n(th.get('required_stable_checks'))}"),
               (t("末段连续稳定次数", "Consecutive stable checks at end"), str(training.stable_checks)),
               (t("评估轮次", "Evaluated epochs"), f"{n(training.start_epoch)} - {n(training.end_epoch)}"),
               (t("损失峰峰变化 / 阈值", "Loss span / threshold"), f"{n(training.loss_span_pct)}% / {n(th.get('loss_span_pct'))}%"),
               (t("损失首尾变化（负值为下降）", "Loss change (negative: decreasing)"), f"{n(training.loss_change_pct)}%"),
               (t("参数峰峰变化 / 阈值", "Parameter span / threshold"), "; ".join(f"{k}: {n(v)}%" for k,v in training.parameter_span_pct.items()) + f" / {n(th.get('parameter_span_pct'))}%"),
               (t("返回参数与末段最大差异 / 阈值", "Returned-parameter gap / threshold"), "; ".join(f"{k}: {n(v)}%" for k,v in training.selected_parameter_gap_pct.items()) + f" / {n(th.get('selected_parameter_gap_pct'))}%"),
               (t("末段平均损失与最佳损失差异 / 阈值", "Tail-to-best loss gap / threshold"), f"{n(training.selected_loss_gap_pct)}% / {n(th.get('selected_loss_gap_pct'))}%"),
               (t("损失归一化分母下限", "Loss denominator floor"), n(th.get('loss_scale_floor')))])
    if r.options.pwm_source == "controller" and r.options.controller:
        section("重建控制器参数", "Reconstruction controller parameters")
        story.append(p("; ".join(f"{key} = {n(value)}" for key, value in r.options.controller.items()), small))
    if request.notes:
        section("补充计算说明", "Additional computation notes")
        story.extend(p(note, small) for note in request.notes)
    section("记录与适用边界", "Record and limitations")
    story.append(p(t("本报告整理自已保存的辨识结果快照，导出时未重新计算。完整设置、训练诊断、波形数据和对话见配套 JSON。",
                     "This report summarizes the saved result snapshot; export does not rerun identification. The accompanying JSON retains full settings, training diagnostics, waveform data and conversation."), small))
    if r.file_sha256:
        story.append(p("CSV SHA-256: " + r.file_sha256, small))

    def footer(canvas, document):
        canvas.setStrokeColor(colors.HexColor("#dce6e6")); canvas.line(42, 35, A4[0]-42, 35)
        canvas.setFont(font, 8); canvas.setFillColor(muted)
        canvas.drawString(42, 23, "BUCK LAB / " + t("参数辨识", "PARAMETER IDENTIFICATION"))
        canvas.drawRightString(A4[0]-42, 23, str(document.page))

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return stream.getvalue()


def reason_text(reason: Reason, zh: bool) -> tuple[str, str]:
    t = lambda cn, en: cn if zh else en
    n = lambda value: "-" if value is None else f"{value:.6g}"
    if reason.code in ("pann_loss_mean_change", "pann_parameter_window_change"):
        label = t("前后窗口平均损失相对变化", "Relative change between window mean losses") if reason.code == "pann_loss_mean_change" else t("最近窗口参数最大峰峰变化", "Maximum parameter span in recent window")
        return (f"{label}: {n(reason.value)}% >= {n(reason.threshold)}%.", t("增加训练轮数并对照损失趋势和参数波动；训练稳定与拟合达标分开判断。", "Increase epochs and inspect loss trends and parameter variation. Assess training stability separately from waveform fit."))
    labels = {"pann_loss_unstable": t("末段损失峰峰变化", "Peak-to-peak tail loss change"),
              "pann_parameters_unstable": t("末段参数最大峰峰变化", "Maximum peak-to-peak parameter change"),
              "pann_selected_parameters_gap": t("返回参数与末段最大差异", "Maximum returned-parameter gap from tail"),
              "pann_selected_loss_gap": t("末段平均损失与最佳损失差异", "Mean tail loss gap from best loss")}
    action = t("保持其他设置不变，增加训练轮数，检查末段损失、参数变化及留出误差；若持续波动，再对照窗口与初值。", "Keep other settings fixed and increase epochs. Check tail loss, parameters and holdout errors; if oscillations persist, compare windows and initial states.")
    if reason.code in labels:
        return (f"{labels[reason.code]}: {n(reason.value)}% > {n(reason.threshold)}%.", action)
    if reason.code == "pann_stability_short":
        return (t("末段连续稳定次数不足：", "Too few consecutive stable checks at end: ") + f"{n(reason.value)} < {n(reason.threshold)}.", action)
    if reason.code == "pann_insufficient_history":
        return (t("训练稳定性证据不足。", "Insufficient training stability evidence."), t("逐轮记录不足，无法完成连续窗口检查；请增加训练轮数或重新辨识。", "Insufficient per-epoch records for consecutive window checks. Increase epochs or rerun identification."))
    if reason.code in ("current_nrmse", "voltage_nrmse"):
        channel = t("电流", "Current") if reason.code == "current_nrmse" else t("电压", "Voltage")
        return (f"{channel} {t('留出归一化 RMSE', 'holdout NRMSE')}: {n(reason.value)}% > {n(reason.threshold)}%.",
                t("检查列映射、单位、PWM 来源及所选区间是否为恒定负载；核对模型假设后重新验证。",
                  "Check mapping, units, PWM source and constant load over the selected interval. Revalidate after checking model assumptions."))
    if reason.code == "condition_number":
        return (t("参数辨识条件数", "Identification condition number") + f": {n(reason.value)} > {n(reason.threshold)}.",
                t("选择包含充分动态变化的恒定负载区间，检查采样与噪声，并对照不同窗口的结果。",
                  "Select a constant-load interval with sufficient dynamics. Check sampling and noise; compare window settings."))
    if reason.code == "parameter_at_bound":
        return (t("初始化或参数估计触及上下限。", "Initialization or estimated parameters reached a bound."),
                t("核对单位、已知 R 和模型适用性，再检查参数约束；不要只为消除提醒而放宽边界。",
                  "Verify units, known R and model applicability before reviewing constraints. Do not widen bounds merely to remove a flag."))
    if reason.code == "pann_time_budget":
        return (t("PANN 达到时间预算，尚不能确认充分收敛。已完成 / 请求轮数：", "PANN reached its time budget; convergence is not confirmed. Completed / requested epochs: ") + f"{n(reason.completed_epochs)} / {n(reason.requested_epochs)}.",
                t("这是旧版本时间限制留下的记录。更新后的训练不设时间上限，请使用更新后的后端按所需轮数重新辨识。",
                  "This records a time limit from an older version. Updated training has no time limit; rerun with the updated backend and the desired epoch count."))
    if reason.code == "pann_best_at_end":
        return (t("最佳结果出现在最后一次训练评估。最佳轮次 / 已完成轮数：", "The best result was at the final training evaluation. Best / completed epochs: ") + f"{n(reason.best_epoch)} / {n(reason.completed_epochs)}.",
                t("这是旧版本规则的记录，末轮最佳本身不能判断收敛；请重新辨识以获得末段稳定性证据。",
                  "This records an older rule. A final-epoch best does not establish convergence; rerun to obtain tail-stability evidence."))
    return (t("未识别的复核原因：", "Unrecognized review reason: ") + reason.code,
            t("请查看配套 JSON 的计算条件与诊断。", "Inspect conditions and diagnostics in the accompanying JSON."))
