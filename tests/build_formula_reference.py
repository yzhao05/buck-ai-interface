"""Generate a readable formula index from the same chunks used by retrieval."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.knowledge_base.buck import FORMULA_DOCUMENTS


def render():
    lines = ["# Buck 功率与控制电路公式知识库", "",
             "以本地 PLECS 原模型、Case16A/B 与独立 Case17 为依据。公式按 KCL/KVL 推导；外部资料用于核对适用域。",
             "知识库补充不会把当前轻量辨识器升级为四状态闭环辨识。标称值不是未知 CSV 的真值。",
             "本文由 `tests/build_formula_reference.py` 从 `buck.py` 生成；修改 `buck.py` 后重新生成。", "",
             "完整参数、连接和源文件 SHA-256 见 [模型证据](model_evidence.json)。", ""]
    for doc in FORMULA_DOCUMENTS:
        lines.append(f"- [{doc['title']}](#{doc['id']})")
    for doc in FORMULA_DOCUMENTS:
        lines.extend(["", f"<a id=\"{doc['id']}\"></a>", "", f"## {doc['title']}", "",
                      f"**适用条件：** {doc['scope']}", "", "```text", doc["equations"], "```", "",
                      doc["explanation"], "", "参考与核对依据：", ""])
        for source in doc["references"]:
            target = source.get("url") or ("model_evidence.json" if "model_evidence" in source.get("path", "") else "../identification.py")
            lines.append(f"- [{source['title']}]({target})")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    (ROOT / "backend/knowledge_base/FORMULAS.md").write_text(render(), encoding="utf-8")
    print(f"Generated reference for {len(FORMULA_DOCUMENTS)} formula topics.")
