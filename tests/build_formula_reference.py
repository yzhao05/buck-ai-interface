"""Generate a readable formula index from the same chunks used by retrieval."""
from pathlib import Path
from posixpath import relpath
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.knowledge_base.buck import FORMULA_DOCUMENTS


def render():
    lines = ["# Buck 功率与控制电路公式知识库", "",
             "模型依据包括 PLECS 原模型、非理想模型 buck_with_parasitics、Case16A/B 与独立 Case17。非理想模型的接线、支路方程和辨识规则见 buck-nonideal 条目。",
             "简化功率级支持实测 PWM 两状态模型与已确认配置的控制器重建模式：后者计入反馈取样支路，固定已知 R 辨识 L/C，并以完整四状态自主闭环验证；控制器参数保持已知，不参与辨识。两种模式均为同一记录后段留出，不是独立工况验证。标称值不是未知 CSV 的真值。",
             "七参数开关电路拟合使用模型上电初态、固定步长及反馈取电，支持自主闭环与实测 PWM 回放。简化模型提供抗噪加权积分与 PANN：前者直接输出端点零权重积分估计，后者以此初始化再微调；旧 integral API 仅保留作实测 PWM 模式的基础积分复现。",
             "已知寄生模式使用用户填写的电感串联rL、电容串联ESR rC；默认理想模式令两者为0。新增公式由KCL/KVL推导，不表示原始PLECS样例已包含寄生。CSV电压统一为输出端vo。",
             "本文由 `tests/build_formula_reference.py` 从 `buck.py` 生成；修改 `buck.py` 后重新生成。", "",
             "各模型的连接和源文件 SHA-256 见 [模型证据](model_evidence.json)。", ""]
    for doc in FORMULA_DOCUMENTS:
        lines.append(f"- [{doc['title']}](#{doc['id']})")
    for doc in FORMULA_DOCUMENTS:
        lines.extend(["", f"<a id=\"{doc['id']}\"></a>", "", f"## {doc['title']}", "",
                      f"**适用条件：** {doc['scope']}", "", "```text", doc["equations"], "```", "",
                      doc["explanation"], "", "参考与核对依据：", ""])
        for source in doc["references"]:
            target = source.get("url") or relpath(source["path"], "backend/knowledge_base")
            lines.append(f"- [{source['title']}]({target})")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    (ROOT / "backend/knowledge_base/FORMULAS.md").write_text(render(), encoding="utf-8")
    print(f"Generated reference for {len(FORMULA_DOCUMENTS)} formula topics.")
