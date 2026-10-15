"""Small, inspectable retrieval corpus. Summaries are authored for this app."""
import math
import re
from collections import Counter

from .knowledge_base.buck import FORMULA_DOCUMENTS

DOCUMENTS = [
    {"id": "pe-gpt", "title": "PE-GPT：任务调度与计算工具", "url": "https://github.com/XinzeLee/PE-GPT",
     "tags": "pegpt pe-gpt 架构 agent 原理 模型",
     "content": "本项目借鉴 PE-GPT 的界面、任务调度、专业知识与物理模型协作方式。LLM 解释工具结果，不能代替数值算法。本项目是 Buck 专用改编，不是原 DAB 设计流程的完整复现。"},
    {"id": "buck-physics", "title": "Buck 模型与 L/C/R 定义", "url": "https://github.com/XinzeLee/PANN",
     "tags": "buck 电路 原理 方程 电感 电容 电阻 lcr 参数 辨识",
     "content": "理想非同步 Buck 导通时 L diL/dt = s Vin - vo，C dvo/dt = iL - vo/R。断续导通零电流区间使用 DCM 约束。R 是负载电阻，不是 ESR 或绕组电阻。积分辨识从电流变化估计 L，联合电压变化和积分量估计 C 与电导 1/R。需要有效动态、已知 Vin 和实际 PWM。"},
    {"id": "identification-limits", "title": "辨识与验证的边界（本项目方法说明）", "url": None,
     "tags": "误差 准确 验证 可信 噪声 结果 初值 修改 计划",
     "content": "波形拟合误差不等于参数误差。未提供独立真值不能计算 L/C/R 真实误差。当前验证是同记录后段、使用实测 PWM 的连续前向预测，不是自主闭环验证。检查非均匀采样、PWM 时间对齐、Vin、负载突变、初始状态和遗漏寄生。先做单因素对照实验；真实参数不得通过文件名进入拟合。"},
    {"id": "csv", "title": "PLECS 数据接入约定（本项目）", "url": "https://www.plexim.com/support/documentation",
     "tags": "csv 上传 数据 plecs 格式 采样 pwm 时间",
     "content": "导出同一个 Scope 的 time、iL、vo、PWM，注明时间单位。PWM 必须为实际 0/1 状态，不能用占空比代替。非均匀采样保留时间戳；重复事件时间保留最后一行。页面波形为显示抽样，数值辨识使用完整选定区间。负载有突变时分别选择恒定负载区间。"},
    {"id": "capacitor-life", "title": "TDK：铝电解电容使用寿命技术资料", "url": "https://www.tdk-electronics.tdk.com/download/185386/5f33d2619fa73419e2a4af562122e90c/pdf-generaltechnicalinformation.pdf",
     "tags": "寿命 老化 温度 纹波 电容 esr 硬件 可靠性 life lifetime",
     "content": "铝电解电容寿命估算应采用具体系列的数据手册和厂商模型，输入包括工作温度、纹波电流及工况。短时仿真波形或一个 C 估计值不能直接给出剩余寿命。缺少器件型号、温度、额定寿命、运行历史时只解释影响因素并列出需要补充的信息。不能把所有器件统一套用每降 10°C 寿命翻倍。"},
    {"id": "hardware", "title": "硬件问题的回答范围（本项目）", "url": None,
     "tags": "mosfet igbt 电感 磁芯 温升 饱和 损耗 硬件 寿命",
     "content": "硬件选型需要具体器件手册、工作点和热条件。电感需要检查饱和电流、铜损和磁芯损耗；功率器件需要检查电压电流应力、开关损耗、结温与热循环。当前知识库没有特定 MOSFET/IGBT 的寿命模型，不能编造寿命或热参数。可根据当前波形报告应力并提出所需补充资料。"},
]


DOCUMENTS.extend(FORMULA_DOCUMENTS)

_TOKEN_ALIASES = {"identify": "identification", "parameters": "parameter",
                  "formulas": "formula", "equations": "equation",
                  "poles": "pole", "zeros": "zero"}
_STOP_WORDS = {"the", "and", "or", "is", "are", "of", "to", "in", "for",
               "what", "how", "please", "explain", "only"}


def _tokens(text):
    text = text.lower()
    result = [_TOKEN_ALIASES.get(token, token) for token in re.findall(r"[a-z][a-z0-9_-]*", text)
              if token not in _STOP_WORDS]
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        result.extend(run[i:i + 2] for i in range(len(run) - 1))
    return result


_COUNTS = [Counter(_tokens(d["title"] + " " + d["tags"] + " " + d["content"])) for d in DOCUMENTS]
_LENGTHS = [sum(c.values()) for c in _COUNTS]
_AVERAGE_LENGTH = sum(_LENGTHS) / len(_LENGTHS)
_DF = Counter(token for counts in _COUNTS for token in counts)


def retrieve(query, count=4):
    """BM25 over bilingual tokens plus exact topic matches; no model/API dependency."""
    q = query.lower()[:1000]
    terms = set(_tokens(q))
    ranked = []
    for doc, counts, length in zip(DOCUMENTS, _COUNTS, _LENGTHS):
        score = 0.0
        for token in terms:
            tf = counts[token]
            if tf:
                idf = math.log(1 + (len(DOCUMENTS) - _DF[token] + .5) / (_DF[token] + .5))
                score += idf * tf * 2.2 / (tf + 1.2 * (.25 + .75 * length / _AVERAGE_LENGTH))
        for tag in doc["tags"].lower().split():
            hit = tag in q if re.search(r"[\u4e00-\u9fff]", tag) else bool(re.search(r"(?<![a-z0-9])" + re.escape(tag) + r"(?![a-z0-9])", q))
            if hit:
                score += 3.5
        if score > 0:
            ranked.append((score, doc))
    return [dict(doc) for _, doc in sorted(ranked, key=lambda item: item[0], reverse=True)[:max(0, min(count, 8))]]


def formula_question(query):
    return bool(re.search(r"公式|方程|推导|传递函数|状态空间|零点|极点|控制电路|补偿器|运放|equation|formula|derive|transfer function|state.space|compensator|op.?amp|kcl|kvl", query.lower()))
