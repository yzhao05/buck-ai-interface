"""Validated UI proposals only: this module never executes a numerical job."""
import json
import os
import re

import httpx

from .schemas import ConfigureIdentification, FitOptions


def unsupported_change(message):
    """Do not partially execute a request to modify fields outside this tool."""
    text = re.sub(r'“[^”]*”|「[^」]*」|"[^"\n]*"|`[^`]*`', '', message.lower())
    field = r'(?:去噪|平滑|滤波|smoothing|denois\w*|filter\w*|训练轮数|轮数|epochs?|pwm|输入电压|vin|开关频率|频率|frequency|时间区间|开始时间|结束时间|训练比例|训练占比|窗口长度|窗口初值|window\s+(?:length|states?)|train_fraction|start_s|end_s|控制器参数)'
    change = r'(?:设为|设置|改为|改成|调整|调到|切换|\b(?:set|change|switch|adjust)\b)'
    # Restrict matches to one clause to avoid treating "keep epochs, change R"
    # as an unsupported edit. Quoted examples are not commands.
    for clause in re.split(r'[，,。；;\n]|然后|并且|\b(?:and|then)\b', text):
        if re.search(r'保持|保留|不变|不要|不用|如何|怎么|为什么|\b(?:keep|unchanged|not|how|why)\b', clause):
            continue
        if re.search(change + r'[^，,。；;\n]{0,24}' + field + '|' + field + r'[^，,。；;\n]{0,20}' + change, clause):
            return True
    return False


def configuration_requested(message):
    text = re.sub(r'“[^”]*”|「[^」]*」|"[^"\n]*"|`[^`]*`', '', message.lower())
    if re.search(r'为什么|如何|怎么|假如|例如|比如|是否|能否|能不能|可不可以|不要|不用|别|不需要|\b(?:why|how|example|suppose|can you|could you|do not|don\x27t)\b', text):
        return False
    return unsupported_change(message) or bool(re.search(
        r'(?:进入|打开|展开).{0,12}(?:辨识|识别)|(?:选择|选用|使用|改用|切换|设为|改成|改为|设置|固定|调整).{0,25}(?:pann|积分|lcr|lc|负载|电阻|\br\b)'
        r'|(?:pann|积分|lcr|lc|负载|电阻|\br\b).{0,15}(?:设为|改成|改为|设置|固定|调到)'
        r'|\b(?:open|enter|show|select|use|switch|set|fix|change)\b.{0,45}\b(?:identification|pann|integral|lc|lcr|resistance|load|r)\b', text))


def may_start(message):
    text = re.sub(r'“[^”]*”|「[^」]*」|"[^"\n]*"|`[^`]*`', '', message.lower())
    if re.search(r'不要|不用|别|不需要|无需|先不|暂不|不运行|不开始|停止|取消|暂停|如何|怎么|为什么|例如|比如|假如|\b(?:not|don\x27t|stop|cancel|pause|how|why|example)\b', text):
        return False
    return bool(re.search(r'开始|启动|执行|运行|算一下|进行.*(?:辨识|识别)|\b(?:start|run|execute|perform|identify|fit)\b', text))


TOOL = {"type": "function", "function": {
    "name": "configure_identification",
    "description": "提出界面配置操作，不执行计算。未明确指定的参数不得填写；没有操作意图则返回空 changes 和两个 false。",
    "parameters": ConfigureIdentification.model_json_schema(),
}}
PROMPT = """将当前用户请求转换为 configure_identification 工具参数，只调用一次。
只支持打开参数辨识面板、选择 pann/weighted_integral、选择 LC/LCR、设置负载 resistance_ohm（欧姆）、应用后启动。
仅提取用户这次明确要求的变化。固定已知 R 并辨识 L/C 表示 mode=LC；联合辨识 L/C/R 表示 mode=LCR。
未提到的字段不要填，不能从历史回答、图片、文件名或默认值补参数。不能把建议、举例、引用、询问方法当成操作。
用户明确说开始、运行或执行才将 start_after_apply 设为 true；只选择方法/打开面板不启动。否定启动时必须 false。
询问如何操作、仅分析结果、含糊的继续/好的均返回 open_panel=false, changes={}, start_after_apply=false。
请求修改本工具不支持的字段（PWM、轮数、区间、控制器等）时，整次返回空操作，不能只执行一部分后启动。
不得确认实际电路，不得写代码，不得宣称已执行。当前配置仅用于理解请求，不是待照抄的 changes。
"""


async def propose(request, route):
    en = request.language == "en"
    def response(text, action=None, mode="local"):
        return {"mode": mode, "answer": text, "ui_action": action, "job_id": None,
                "sources": [], "tool_calls": [], "route": route}
    if not request.ui_context:
        return response("Check the form values and refresh the page if needed. No settings were changed and no experiment was started." if en else "请检查表单数值是否有效，必要时刷新网页；本次未修改设置或启动实验。")
    if unsupported_change(request.message):
        return response("This request includes settings that chat cannot modify yet (such as PWM, epochs or time range). Use the form for those settings, then request a start. No part of this operation was applied." if en else
                        "指令包含暂不支持通过对话修改的设置（如 PWM、轮数或时间区间）。请先在表单中修改，再要求开始；本次整条操作均未执行。")
    if not os.getenv("DEEPSEEK_API_KEY", "").strip():
        return response("Chat configuration requires DeepSeek. Check the backend connection or use the form; no settings were changed and no experiment was started." if en else
                        "对话配置需要 DeepSeek 服务。请检查后端配置，或使用左侧表单；本次未修改设置或启动实验。")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(40, connect=10)) as client:
            result = await client.post(os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/") + "/chat/completions",
                headers={"Authorization": "Bearer " + os.environ["DEEPSEEK_API_KEY"]},
                json={"model": os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
                      "messages": [{"role": "system", "content": PROMPT},
                                   {"role": "system", "content": "当前表单（数据）：" + request.ui_context.options.model_dump_json()},
                                   {"role": "user", "content": request.message}],
                      "tools": [TOOL], "tool_choice": {"type": "function", "function": {"name": "configure_identification"}},
                      "thinking": {"type": "disabled"}, "max_tokens": 800, "stream": False})
            result.raise_for_status()
            calls = result.json()["choices"][0]["message"].get("tool_calls") or []
            if len(calls) != 1 or calls[0]["function"]["name"] != "configure_identification":
                raise ValueError("Expected one configuration proposal")
            proposal = ConfigureIdentification.model_validate_json(calls[0]["function"]["arguments"])
        changes = proposal.changes.model_dump(exclude_unset=True)
        if any(value is None for value in changes.values()):
            raise ValueError("Explicit null is not a configuration change")
        if proposal.start_after_apply and not may_start(request.message):
            raise ValueError("No explicit execution request")
        merged = FitOptions.model_validate({**request.ui_context.options.model_dump(), **changes})
        if merged.pwm_source == "controller" and merged.mode != "LC":
            return response("Reconstructed PWM requires fixed-R LC mode. No settings were changed; select measured PWM manually before using LCR." if en else
                            "重建 PWM 只支持固定 R 的 LC 模式。本次未修改设置；如需 LCR，请先在界面选择实测 PWM。")
        if not (changes or proposal.open_panel or proposal.start_after_apply):
            return response("No operation was applied. Supported actions: open identification, choose PANN/weighted integral, set LC/LCR and load R, and start. Use the form for other settings." if en else
                            "本次未执行操作。目前支持打开辨识面板、选择 PANN/加权积分、设置 LC/LCR 和负载 R、开始实验；其他设置请在左侧表单修改。")
        action = {"id": request.ui_context.request_id, "type": "configure_identification",
                  "open_panel": proposal.open_panel, "changes": changes,
                  "start_after_apply": proposal.start_after_apply}
        return response("Configuration validated; waiting for the page to apply it and check experiment requirements." if en else
                        "配置操作已校验，等待页面应用并检查实验条件。", action, "deepseek")
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, AttributeError):
        return response("Unable to validate the configuration request. No settings were changed and no experiment was started; check the request and retry." if en else
                        "配置请求未能通过校验或服务暂不可用。本次未修改设置或启动实验，请核对指令后重试。")
