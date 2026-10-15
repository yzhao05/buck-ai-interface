"""Read local .plecs text without executing scripts; export auditable model evidence."""
import argparse
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
MODELS = {
    "original": "buck_converter_with_parameter_sweep.plecs",
    "case16a": "test/case16A_L60uH_C500uF_PWM.plecs",
    "case16b": "test/case16B_L40uH_C750uF_PWM.plecs",
    "case17": "test/case17_original_buck_L50uH_C1000uF_R3_D022.plecs",
}


def blocks(text, kind):
    for match in re.finditer(r"(?m)^\s*" + re.escape(kind) + r"\s*\{", text):
        start = index = match.end()
        depth, quoted, escaped = 1, False, False
        while index < len(text) and depth:
            char = text[index]
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
            index += 1
        if depth:
            raise ValueError(f"Unclosed {kind} block")
        yield text[start:index - 1]


def quoted_field(text, field):
    match = re.search(r"(?m)^\s*" + re.escape(field) + r'\s+("(?:[^"\\]|\\.)*")', text)
    return json.loads(match[1]) if match else None


def extract(path):
    raw = path.read_bytes()
    text = raw.decode("utf-8-sig")
    init = re.search(r"InitializationCommands([\s\S]*?)\n\s*InitialState", text)
    initialization = "".join(json.loads(s) for s in re.findall(r'"(?:[^"\\]|\\.)*"', init[1]))
    components = {}
    for block in blocks(text, "Component"):
        name = quoted_field(block, "Name")
        kind = re.search(r"Type\s+(\w+)", block)[1]
        if kind in {"Scope", "PlecsProbe", "Output", "SignalDemux", "Ground"}:
            continue
        params = {quoted_field(p, "Variable"): quoted_field(p, "Value") for p in blocks(block, "Parameter")}
        components[name] = {"type": kind, "parameters": params}
    return {"sha256": hashlib.sha256(raw).hexdigest(), "version": quoted_field(text, "Version"),
            "duration_s_expression": quoted_field(text, "TimeSpan"), "initialization_commands_not_executed": initialization,
            "components": components, "connections": [b.strip() for b in blocks(text, "Connection")]}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=ROOT.parent / "仿真" / "buck_converter_with_parameter_sweep")
    args = parser.parse_args()
    models = {}
    for name, relative in MODELS.items():
        models[name] = {"source_relative_to_ST4001": "仿真/buck_converter_with_parameter_sweep/" + relative,
                        **extract(args.source / relative)}
    result = {"reviewed_on": "2026-10-15", "purpose": "Reference model definitions only; never identification ground truth for an unknown CSV.",
              "models": models, "derivation_crosscheck": "PANN-main/closed_loop_buck.py (read only; original equations checked against KCL)",
              "upstream_rag": "reference/PE-GPT-main/core/llm/llm.py: rag_load uses SimpleDirectoryReader, chunking and VectorStoreIndex; this app uses local lexical retrieval without embeddings."}
    destination = ROOT / "backend/knowledge_base/model_evidence.json"
    destination.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Exported {len(models)} model snapshots; no simulation executed.")


if __name__ == "__main__":
    main()
