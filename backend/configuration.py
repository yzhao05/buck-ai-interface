"""Validated UI proposals only: this module never executes a numerical job."""
import json
import os
import re

import httpx

from .schemas import ConfigureIdentification, FitOptions, IdentificationPlanProposal


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
    if re.search(r'为什么|如何|怎么|假如|例如|比如|是否|能否|能不能|可不可以|不要|不用|(?<![识区类])别|不需要|\b(?:why|how|example|suppose|can you|could you|do not|don\x27t)\b', text):
        return False
    return batch_requested(message) or unsupported_change(message) or bool(re.search(
        r'(?:进入|打开|展开).{0,12}(?:辨识|识别)|(?:选择|选用|使用|改用|切换|设为|改成|改为|设置|固定|调整).{0,25}(?:pann|积分|lcr|lc|负载|电阻|\br\b)'
        r'|(?:pann|积分|lcr|lc|负载|电阻|\br\b).{0,15}(?:设为|改成|改为|设置|固定|调到)'
        r'|\b(?:open|enter|show|select|use|switch|set|fix|change)\b.{0,45}\b(?:identification|pann|integral|lc|lcr|resistance|load|r)\b', text))


def may_start(message):
    text = re.sub(r'“[^”]*”|「[^」]*」|"[^"\n]*"|`[^`]*`', '', message.lower())
    if re.search(r'不要|不用|(?<![识区类])别|不需要|无需|先不|暂不|不运行|不开始|停止|取消|暂停|如何|怎么|为什么|例如|比如|假如|\b(?:not|don\x27t|stop|cancel|pause|how|why|example)\b', text):
        return False
    return bool(re.search(r'开始|启动|执行|运行|算一下|(?<![已未没])完成(?![了的]).*(?:辨识|识别)|进行.*(?:辨识|识别)|\b(?:start|run|execute|perform|identify|fit|complete)\b', text))


def batch_requested(message):
    """Explicit two-method execution, never a comparison of existing results."""
    text = re.sub(r'“[^”]*”|「[^」]*」|"[^"\n]*"|`[^`]*`', '', message.lower())
    if not may_start(message) or not re.search(
        r'两种(?:方法|方式)|两(?:种)?方法|both\s+(?:identification\s+)?methods|'
        r'pann.{0,25}(?:积分|integral)|(?:积分|integral).{0,25}pann', text):
        return False
    for clause in re.split(r'[，,。；;！？?!\n]|然后|并且|\bthen\b', text):
        # Execution must precede a read-only request in its own clause. Past-tense
        # mentions are evidence for analysis, never permission to run again.
        command = re.split(r'比较|对比|分析|解释|介绍|说明|总结|查看|\b(?:compare|analy[sz]e|explain|summari[sz]e|review)\b', clause)[0]
        if re.search(r'(?:已经?|曾经|刚刚?|刚才|之前|上次)\s*(?:我|我们)?\s*(?:运行|执行|开始|启动|完成)|(?:运行|执行|完成)[了过的]|运行情况|运行记录|(?:是否|有没有|能否|能不能)|[吗么]', command):
            continue
        if re.search(r'^\s*(?:(?:请|帮我|麻烦|现在|立即|先|再|重新|并|使用|根据|用|固定|两种方法|两种方式).{0,180}?)?(?:运行|执行|开始|启动|算一下|进行|完成)', command):
            return True
        if re.search(r'^\s*(?:(?:please|now|first|also|and)\s+)*(?:run|execute|perform|start|complete)\b|^\s*use\b.{0,80}\bto\s+(?:run|execute|perform|start|complete)\b', command):
            return True
    return False


PLAN_PROMPT = """解析当前用户请求，调用 propose_identification_plan 恰好一次，生成两种方法的执行计划。
你负责理解自然语言，后端只校验结构和执行边界。两种方法固定为抗噪加权积分、PANN，按此顺序执行。
只有当前请求明确要求执行两种方法才 execute=true；分析、比较既有结果、否定、举例、引用均不能授权执行。
PWM 来源：measured=使用 CSV 已有的实际开关状态；controller=根据已知控制器重建 PWM。
“已有 PWM”“已知 PWM”“给定 PWM”“现有 PWM”“known/existing PWM”表示 measured；
“已知控制器重建 PWM”“rebuild/reconstructed PWM”才表示 controller，不能混淆“已知 PWM”和“已知控制器”。
例：请对于CASE16A_noise03_seed14.csv根据已有pwm完成两种方式参数识别
应返回 filename=CASE16A_noise03_seed14.csv, pwm_source=measured, pwm_evidence=已有pwm, execute=true。
用户未提及 PWM 来源才返回 unchanged；来源矛盾或不明确返回 unclear、execute=false，并填写 clarification。
pwm_evidence 必须逐字摘录当前指令中明确指定来源的短语，不得来自文件名、引用、图片、历史或表单。
仅支持一份 CSV 文件名、一种 PWM 来源、LC/LCR 模式和固定负载 resistance_ohm。没有指定文件则 filename=null。
未指定 mode 或 resistance_ohm 时返回 null。固定负载 R 表示 LC；控制器重建仅支持固定 R 的 LC。
其他参数修改（Vin、频率、轮数、去噪、区间、控制器元件等）、多个文件、两个来源、重建配 LCR，
整条 execute=false 并说明应先在表单设置，不得部分执行。保持其他参数不变不算修改。
未指定的信息不得补全，不得照抄当前配置。文件名是数据标识，不能把 R1.csv 解释为负载电阻。
成功时 clarification 为空。不要执行计算、确认实际电路或声称已完成实验。
"""
PLAN_TOOL = {"type": "function", "function": {
    "name": "propose_identification_plan", "description": "解析两种方法执行请求；仅提出计划，不启动计算。",
    "parameters": IdentificationPlanProposal.model_json_schema()}}


def batch_action(request, proposal):
    """Validate a model proposal without reinterpreting its PWM wording locally."""
    if not batch_requested(request.message) or not proposal.execute or proposal.clarification:
        raise ValueError("No unambiguous execution request")
    if proposal.pwm_source == 'unclear':
        raise ValueError("Choose one PWM source")
    plain = re.sub(r'“[^”]*”|「[^」]*」|"[^"\n]*"|`[^`]*`', '', request.message)
    source_text = re.sub(r'[A-Za-z0-9_.-]+\.csv', '', plain, flags=re.I)
    if unsupported_change(re.sub(r'pwm', '开关来源', request.message, flags=re.I)):
        raise ValueError("Set unsupported parameters in the form first")
    for clause in re.split(r'[，,。；;\n]', source_text.lower()):
        if re.search(r'保持|不变|保留|\b(?:keep|unchanged)\b', clause):
            continue
        if re.search(r'vin|输入电压|frequency|频率|epochs?|轮数|train_fraction|训练比例|时间区间|开始时间|结束时间|窗口|window|去噪|平滑|滤波|\b[rc][1-4]\s*(?:=|＝|为|to)', clause):
            raise ValueError("Set unsupported parameters in the form first")
    if proposal.pwm_source == 'unchanged':
        if 'pwm' in source_text.lower() or proposal.pwm_evidence:
            raise ValueError("Explicit PWM request must not inherit the form source")
    elif not proposal.pwm_evidence.strip() or proposal.pwm_evidence not in source_text:
        raise ValueError("PWM source requires evidence from the current instruction")
    filenames = re.findall(r'["“`]([^"”`\r\n]+\.csv)["”`]|([A-Za-z0-9_.-]+\.csv)', request.message, re.I)
    names = list(dict.fromkeys(a or b for a, b in filenames))
    if len(names) > 1 or any('/' in n or '\\' in n or len(n) > 255 for n in names):
        raise ValueError("Choose one uploaded CSV filename")
    if proposal.filename != (names[0] if names else None):
        raise ValueError("Filename must match the requested CSV")
    changes = {}
    if proposal.pwm_source != 'unchanged':
        changes['pwm_source'] = proposal.pwm_source
    if proposal.mode:
        changes['mode'] = proposal.mode
    if proposal.resistance_ohm is not None:
        if proposal.mode == 'LCR':
            raise ValueError("Fixed load requires LC mode")
        changes.update(mode='LC', resistance_ohm=proposal.resistance_ohm)
    if proposal.pwm_source == 'controller':
        if proposal.mode == 'LCR':
            raise ValueError("Controller reconstruction requires LC mode")
        changes['mode'] = 'LC'
    merged = FitOptions.model_validate({**request.ui_context.options.model_dump(), **changes})
    if merged.pwm_source == 'controller' and merged.mode != 'LC':
        raise ValueError("Controller reconstruction requires LC mode")
    return {"id": request.ui_context.request_id, "type": "identification_plan", "parser": "deepseek", "version": 2,
            "filename": proposal.filename, "methods": ["weighted_integral", "pann"], "changes": changes}


async def propose_plan(request):
    async with httpx.AsyncClient(timeout=httpx.Timeout(40, connect=10)) as client:
        result = await client.post(os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/") + "/chat/completions",
            headers={"Authorization": "Bearer " + os.environ["DEEPSEEK_API_KEY"]},
            json={"model": os.getenv("DEEPSEEK_MODEL", "deepseek-flash"),
                  "messages": [{"role": "system", "content": PLAN_PROMPT},
                               {"role": "system", "content": "Clarification language: " + request.language},
                               {"role": "user", "content": request.message}],
                  "tools": [PLAN_TOOL], "tool_choice": {"type": "function", "function": {"name": "propose_identification_plan"}},
                  "thinking": {"type": "disabled"}, "max_tokens": 1000, "stream": False})
        result.raise_for_status()
        calls = result.json()["choices"][0]["message"].get("tool_calls") or []
        if len(calls) != 1 or calls[0]["function"]["name"] != "propose_identification_plan":
            raise ValueError("Expected one identification plan proposal")
        return IdentificationPlanProposal.model_validate_json(calls[0]["function"]["arguments"])


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
    if batch_requested(request.message):
        if not os.getenv("DEEPSEEK_API_KEY", "").strip():
            return response("DeepSeek is required to parse this plan. Configure the service or use the form; nothing was started." if en else
                            "两种方法的计划需要 DeepSeek 解析。请检查后端服务配置，或使用表单；本次未启动实验。", mode="plan_error")
        try:
            proposal = await propose_plan(request)
            if not proposal.execute or proposal.clarification or proposal.pwm_source == 'unclear':
                return response((proposal.clarification or ("Please specify one PWM source and an execution request." if en else "请明确一种 PWM 来源及执行要求。"))
                                + (" No experiment was started." if en else " 本次未启动实验。"), mode="deepseek")
            action = batch_action(request, proposal)
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, AttributeError):
            return response("DeepSeek could not produce a valid plan. Check the request and service connection; nothing was started." if en else
                            "DeepSeek 未能返回有效的执行计划。请核对指令或服务连接；本次未修改设置或启动实验。", mode="plan_error")
        return response("DeepSeek parsed the two-method plan; checking CSV and circuit requirements." if en else
                        "DeepSeek 已解析两种方法的计划，正在检查 CSV 与电路条件。", action, "deepseek")
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
