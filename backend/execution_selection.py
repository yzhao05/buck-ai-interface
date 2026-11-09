"""Check structured user choices, without interpreting user prose locally."""
from .schemas import ExecutionSelection


SELECTION_PROMPT = """
启动辨识前必须让用户明确选择 PWM 来源和计算方法；两项都已在当前指令或明确延续的对话中指定时不重复询问。
PWM 来源可选 CSV 实测、控制器重建，或用户明确要求自动判断；计算方法可选抗噪加权积分、PANN，或明确要求两种都运行。
仅说“直接开始/数据就绪就启动/确认设置”不代表选择了界面默认的 PWM 来源和方法；不能从文件名、是否有PWM列、默认值或旧实验推断用户选择。
缺哪项就在主回复中询问哪项，等回复后才允许启动；可先完成已授权的导入和检查，不得把未启动说成已完成实验。
若使用 configure_identification 先加载数据或修改配置并等待选择，设置 request_selection=true、start_after_apply=false，让前端把选择问题显示在主回复中。
每次提出启动或执行计划时填写 execution_selection：methods、pwm_sources 及对应的 method_evidence、pwm_evidence，记录用户选择的依据；没有依据时保留为空，不编造确认。
execution_selection 与最终执行设置必须一致；用户明确选择当前设置时，先在对话中列出具体方法和PWM来源，再得到用户明确确认。
用户已请求启动、仅因等待这两项选择而暂停时，用户回答具体选项可以继续原启动请求；不能把参考CSV或电路接线的“确认”当作这两项选择。
多次计划同样必须覆盖计划中的全部方法和PWM来源。读取、分析或对比已有结果不受此要求影响；工具检索和调用顺序仍由你决定。
选择两种方法时优先使用 propose_identification_plan；PWM 自动判断是每种方法各执行一次，实际与重建两种来源都选时才是四组。
"""


def require_execution_selection(selection, methods, sources, *, allow_auto=False):
    selection = ExecutionSelection.model_validate(selection or {})
    missing=[]
    if not selection.method_evidence.strip() or not set(methods)<=set(selection.methods):
        missing.append('计算方法：抗噪加权积分、PANN，还是两种都运行？')
    if not selection.pwm_evidence.strip() or not (set(sources)<=set(selection.pwm_sources) or (allow_auto and 'auto' in selection.pwm_sources)):
        missing.append('PWM 来源：CSV 实测 PWM、控制器重建，还是明确采用自动判断？')
    if missing:
        raise ValueError('开始前请先向用户询问并确认以下选择：\n'+'\n'.join(missing)+'\n尚未启动辨识。')
    return selection.model_dump()
