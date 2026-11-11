"""Small, inspectable retrieval corpus. Summaries are authored for this app."""
import math
import re
from copy import deepcopy
from collections import Counter

from .knowledge_base.buck import FORMULA_DOCUMENTS
from .knowledge_base.ui import UI_DOCUMENTS

DOCUMENTS = [
    {"id": "offline-pwm-denoising", "title": "离线抗噪 PWM 重建与质量检查（本项目）", "url": None,
     "tags": "PWM 重建 去噪 SG modified sinc FIR 短脉冲 边沿 controller reconstruction denoise",
     "content": "先分训练/留出，仅对训练电压重建支路去噪，原始电流、电压用于拟合。二阶SG是基线（默认6周期），MS n=2、alpha=4是候选；都为离线非因果滤波，不能直接当实时算法。RC参数和锯齿比较器按真实配置，禁止人工锁存或修边。完整载波周期检查额外/缺少边沿、短脉冲及占空比；首尾残缺周期仍检查已观察到的额外边沿，失败停止初始化与训练。通过只是结构可信性筛查，非时刻准确保证；剧烈瞬态真实多次交叉也可能被保守拒绝，需核对波形。旧结果无filter/quality字段时没有此检查。Case16A/B、1%/3%噪声、种子12/13/14首轮154组开发对照见tests/evidence/pwm_reconstruction_case16.json，默认只经此范围初验，非普遍最优。真实PWM与参数真值只用于开发评估，最终运行不依赖。MS核参考Schmid等(2022), doi:10.1021/acsmeasuresciau.1c00054，边界线性外推为本项目选择。"},
    {"id": "measurement-noise-residuals", "title": "无干净参考时：噪声、残差与重复实验", "url": "https://www.mathworks.com/help/ident/ug/what-is-residual-analysis.html",
     "tags": "噪声 残差 偏置 漂移 置信区间 不确定性 重复采集 noise residual uncertainty repeatability",
     "content": (
         "实际部署不要求干净CSV。优先用传感器标定或同步重复采集估计噪声和偏置，不能把开关纹波全部当噪声。"
         "本项目noise-residual-v1只从原始训练段的密集采样、非开关边沿三点局部线性残差估计MAD噪声代理量；假设局部线性、噪声独立且方差相近。曲率、相关噪声、采样及训练/留出噪声差异会影响估计，零或不足显示未知。"
         "留出残差=测量减连续预测。程序记录均值、规则采样的相邻相关、分块均值跨度，存在实测留出PWM时还记录边沿与非边沿RMSE比。偏置、慢变、相关或边沿集中只是诊断线索，不是因果结论或正式白噪声检验。"
         "RMSE与噪声比0.5至1.5称同量级，其他形状阈值也为待校准启发式。保留5% NRMSE应用门槛，噪声大不自动通过；噪声高于要求应改善测量。"
         "重复实验按完整配置、映射和区间分组、按CSV校验值去除重复拟合，显示观察范围、样本标准差和变异系数。需用户确认同一电路且独立采集；文件不同不证明独立。观察范围不是95%置信区间，同一参数对新记录前向预测才是新记录预测验证，分别重新辨识不能替代。"
         "无独立真值不报告参数真实误差。没有重复采集或统计假设时应明确不确定性尚未量化，不编造区间。")},
    {"id": "pann-noisy-window-states", "title": "含噪 PLECS 窗口初值：Case14–16 的结论与当前适配", "url": None,
     "tags": "pann 噪声 初值 窗口 电容 偏差 联合估计 noise window initial states",
     "content": (
         "本地 Case14 用干净窗口状态作原因诊断：原 Case13B 在3%噪声下电容最大误差6725.72%，替换状态后1.96%；干净状态是诊断特权信息，不能用于部署。"
         "Case15 仅从含噪数据联合估计窗口初始电流、电压与L/C，3%噪声三种子电容最大误差6.94%；Case16A/B相应为3.50%/4.50%。这是每组独立辨识、同记录闭环留出评价，不证明硬件或任意数据可靠。"
         "界面提供抗噪加权积分与PANN两种方法：weighted_integral直接输出端点零权重积分参数，无需PyTorch；pann才继续微调，不保证更准确。"
         "PANN借鉴联合估计机制，以训练段端点零权重积分估计参数初值，先固定参数估计50轮窗口状态，再默认联合训练200轮；固定含噪初值仅作对照。"
         "自动窗口只用训练噪声估计：任一通道噪声达到信号RMS的0.5%时优先3周期，否则1周期，短记录缩短窗口保留至少4个。此为经验规则，非最优保证；可手选1/2/3周期比较。"
         "使用实测或已确认控制器重建的PWM、训练段全范围分布的非重叠窗口、非负可训练状态、同一轮次保存参数与状态。连续验证只取首窗口估计初值，不在留出段重置。"
         "实测PWM模式为不含反馈支路的两状态模型；已知控制器重建模式计入取样支路，固定已知R识别L/C，并作自主闭环验证。两种模式均不可套用原实验误差。低训练损失不等于参数准确，PANN微调可能比积分初值更偏；预热或加轮数不能保证消除短窗C偏差。训练稳定性按末段连续窗口的损失变化和参数波动判断，不能仅凭最优轮次是否在末轮决定复核。"
         "Case17噪声辨识尚未完成，不作为成功证据。来源：本项目 noise_evidence.json 中记录的本地实验汇总与SHA256；当前适配器成对重测见tests/evidence。")},
    {"id": "pe-gpt", "title": "PE-GPT：任务调度与计算工具", "url": "https://github.com/XinzeLee/PE-GPT",
     "tags": "pegpt pe-gpt 架构 agent 原理 模型",
     "content": "本项目借鉴 PE-GPT 的界面、任务调度、专业知识与物理模型协作方式。LLM 解释工具结果，不能代替数值算法。本项目是 Buck 专用改编，不是原 DAB 设计流程的完整复现。"},
    {"id": "buck-physics", "title": "Buck 模型与 L/C/R 定义", "url": "https://github.com/XinzeLee/PANN",
     "tags": "buck 电路 原理 方程 电感 电容 电阻 lcr 参数 辨识",
     "content": "理想非同步 Buck 导通时 L diL/dt = s Vin - vo，C dvo/dt = iL - vo/R。断续导通零电流区间使用 DCM 约束。R 是负载电阻，不是 ESR 或绕组电阻。积分辨识从电流变化估计 L，联合电压变化和积分量估计 C 与电导 1/R。需要有效动态、已知 Vin 和开关信息；可使用实际 PWM，也可在确认指定控制器配置后重建 PWM。重建模式固定已知 R 识别 L/C，电容方程还需扣除反馈取样支路电流。"},
    {"id": "identification-limits", "title": "辨识与验证的边界（本项目方法说明）", "url": None,
     "tags": "误差 准确 验证 可信 噪声 结果 初值 修改 计划",
     "content": "波形拟合误差不等于参数误差。未提供独立真值不能计算 L/C/R 真实误差。实测 PWM 模式使用同记录后段的实测 PWM 连续前向预测，不是自主闭环验证；已知控制器重建模式在训练段重建开关信息，验证时控制器由预测电压驱动，不使用留出段电压或实测 PWM。两者都不是独立工况验证。检查非均匀采样、PWM 时间对齐、Vin、负载突变、初始状态和遗漏寄生。先做单因素对照实验；真实参数不得通过文件名进入拟合。"},
    {"id": "csv", "title": "PLECS 数据接入约定（本项目）", "url": "https://docs.plexim.com/plecs/latest/using-plecs/using-scope/",
     "tags": "csv 上传 数据 plecs 格式 采样 pwm 时间",
     "content": "导出同一个 Scope 的 time、iL、vo，注明时间单位；PWM 列为可选项，可一起放在同一 CSV 中上传。上传并映射 PWM 时必须为实际 0/1 状态，不能用占空比代替。没有 PWM 列时，须确认与默认图的非同步 Buck 和 RC 控制器接线相同，元件、增益、相位和初态可在电路图编辑器修改，数值须匹配实际仿真；固定已知负载 R 识别 L/C，由训练段电压重建 PWM。要求从 t=0 开始、负载恒定、结束于 1 ms 前；频率默认 100 kHz，可修改。上传电路图片只作参考，不会自动识别参数或改变模型。不能对任意三列 CSV 无条件重建。非均匀采样保留时间戳；重复事件时间保留最后一行。页面波形为显示抽样，数值辨识使用完整选定区间。负载有突变时分别选择恒定负载区间。"},
    {"id": "capacitor-life", "title": "TDK：铝电解电容使用寿命技术资料", "url": "https://www.tdk-electronics.tdk.com/download/185386/31a5416e653dd6e4e428b8208d65cc2e/pdf-generaltechnicalinformation.pdf",
     "tags": "寿命 老化 温度 纹波 电容 esr 硬件 可靠性 life lifetime",
     "content": "铝电解电容寿命估算应采用具体系列的数据手册和厂商模型，输入包括工作温度、纹波电流及工况。短时仿真波形或一个 C 估计值不能直接给出剩余寿命。缺少器件型号、温度、额定寿命、运行历史时只解释影响因素并列出需要补充的信息。不能把所有器件统一套用每降 10°C 寿命翻倍。"},
    {"id": "hardware", "title": "硬件问题的回答范围（本项目）", "url": None,
     "tags": "mosfet igbt 电感 磁芯 温升 饱和 损耗 硬件 寿命",
     "content": "硬件选型需要具体器件手册、工作点和热条件。电感需要检查饱和电流、铜损和磁芯损耗；功率器件需要检查电压电流应力、开关损耗、结温与热循环。当前知识库没有特定 MOSFET/IGBT 的寿命模型，不能编造寿命或热参数。可根据当前波形报告应力并提出所需补充资料。"},
]


DOCUMENTS.extend(FORMULA_DOCUMENTS)
DOCUMENTS.extend(UI_DOCUMENTS)

# Browsing and direct reads deliberately bypass relevance ranking.
_BY_ID = {doc['id']: doc for doc in DOCUMENTS}
if len(_BY_ID) != len(DOCUMENTS):
    raise ValueError('Knowledge document IDs must be unique')


def _category(doc):
    return 'formula' if doc.get('kind') == 'formula' else 'ui' if doc['id'].startswith('ui-') else 'method'


def list_knowledge(category='all', offset=0, limit=30):
    """Return a stable, paginated catalogue, not citable full-text evidence."""
    if category not in ('all', 'method', 'formula', 'ui'):
        raise ValueError('Unknown knowledge category')
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError('offset must be nonnegative and limit must be 1–100')
    selected = [doc for doc in DOCUMENTS if category == 'all' or _category(doc) == category]
    entries = []
    for doc in selected[offset:offset + limit]:
        summary = doc.get('summary') or doc.get('scope') or re.split(r'[。\n]', doc['content'])[0]
        entries.append({'id': doc['id'], 'title': doc['title'], 'category': _category(doc),
                        'summary': summary[:240], 'tags': doc['tags']})
    end = offset + len(entries)
    return {'category': category, 'total': len(selected), 'offset': offset, 'count': len(entries),
            'next_offset': end if end < len(selected) else None, 'entries': entries,
            'note': '目录仅用于选读；引用前请用 read_knowledge 读取全文。'}


def read_knowledge(ids):
    """Read exact IDs in requested order, without BM25 or hidden substitution."""
    if not isinstance(ids, list) or not 1 <= len(ids) <= 8 or any(not isinstance(key, str) or not key for key in ids):
        raise ValueError('ids must contain 1–8 nonempty knowledge IDs')
    unique = list(dict.fromkeys(ids))
    docs = [deepcopy(_BY_ID[key]) for key in unique if key in _BY_ID]
    return {'count': len(docs), 'documents': docs, 'missing_ids': [key for key in unique if key not in _BY_ID]}


BROWSE_TOOLS = [
    {'type': 'function', 'function': {
        'name': 'list_knowledge',
        'description': '浏览完整知识目录的标题、摘要和标签，不经过关键词排名。可按项目方法 method、公式 formula、界面 ui 分类并分页；用 read_knowledge 按 ID 选读全文。目录不是全文引用依据。',
        'parameters': {'type': 'object', 'properties': {
            'category': {'type': 'string', 'enum': ['all', 'method', 'formula', 'ui'], 'default': 'all'},
            'offset': {'type': 'integer', 'minimum': 0, 'default': 0},
            'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100, 'default': 30}},
            'additionalProperties': False}}},
    {'type': 'function', 'function': {
        'name': 'read_knowledge',
        'description': '按精确条目 ID 读取知识全文、适用条件和来源，不经过搜索排名。ID 可来自目录或已读资料；可多次读取以继续查阅，未知 ID 明确返回 missing_ids。',
        'parameters': {'type': 'object', 'properties': {
            'ids': {'type': 'array', 'items': {'type': 'string', 'minLength': 1}, 'minItems': 1, 'maxItems': 8}},
            'required': ['ids'], 'additionalProperties': False}}},
]

def _tokens(text):
    text = text.lower()
    result = re.findall(r"[a-z][a-z0-9_-]*", text)
    for run in re.findall(r"[\u4e00-\u9fff]+", text):
        result.extend(run[i:i + 2] for i in range(len(run) - 1))
    return result


_COUNTS = [Counter(_tokens(d["title"] + " " + d["tags"] + " " + d["content"])) for d in DOCUMENTS]
_LENGTHS = [sum(c.values()) for c in _COUNTS]
_AVERAGE_LENGTH = sum(_LENGTHS) / len(_LENGTHS)
_DF = Counter(token for counts in _COUNTS for token in counts)


def retrieve(query, count=4):
    """BM25 over bilingual tokens; no query rewriting or hand-set topic boosts."""
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
        if score > 0:
            ranked.append((score, doc))
    return [deepcopy(doc) for _, doc in sorted(ranked, key=lambda item: item[0], reverse=True)[:max(0, min(count, 8))]]
