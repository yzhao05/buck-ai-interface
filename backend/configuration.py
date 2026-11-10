"""Validated UI proposals only: this module never executes a numerical job."""
import re
from .configuration_fields import FIELD_PROMPT, validate_structure
from .execution_selection import SELECTION_PROMPT, require_execution_selection

from .schemas import ConfigureIdentification, FitOptions, IdentificationPlanProposal, PlecsPortAction

PLAN_PROMPT = """当用户要求执行组合辨识时，可选择 propose_identification_plan 工具，生成两种方法的执行计划（2 次或 4 次辨识）。
你负责理解自然语言，后端只校验结构和执行边界。两种方法固定为抗噪加权积分、PANN，按此顺序执行。
只有当前请求明确要求执行两种方法，或明确回答此前等待的选择以继续该请求时才 execute=true；分析、比较既有结果、否定、举例、引用均不能授权执行。
PWM 来源：measured=使用 CSV 已有的实际开关状态；controller=根据已知控制器重建 PWM。
both=用户明确要求实际 PWM 与重建 PWM 各执行两种方法，共四组组合。不是来源矛盾，不要拒绝。
对于已有 CSV，“完成四个仿真/四组实验”搭配 PWM 来源和 PANN/加权积分表示执行数值辨识，
即使辨识面板尚未打开也应生成计划；无需 identification_enabled=true。若要求启动 PLECS 软件，应选择独立的 PLECS 工具。
“已有 PWM”“已知 PWM”“给定 PWM”“现有 PWM”“known/existing PWM”表示 measured；
“已知控制器重建 PWM”“rebuild/reconstructed PWM”才表示 controller，不能混淆“已知 PWM”和“已知控制器”。
例：请对于CASE16A_noise03_seed14.csv根据已有pwm完成两种方式参数识别
应返回 filename=CASE16A_noise03_seed14.csv, pwm_source=measured, pwm_evidence=已有pwm, execute=true。
例：根据我的CASE16A_noise03_seed13.csv，完成rebuild pwm和实际pwm；pann和加权积分四个仿真
返回 filename=CASE16A_noise03_seed13.csv, pwm_source=both, pwm_evidence=rebuild pwm和实际pwm, execute=true。
四组固定顺序：实测PWM加权积分、实测PWM PANN、重建PWM加权积分、重建PWM PANN，全部固定已知 R 的 LC。
PWM 来源未改变且此前已明确选择时可返回 unchanged；从未选择时必须 execute=false 并询问来源，不能直接沿用默认值。只要求选择一种但来源矛盾或不明确返回 unclear、execute=false，并填写 clarification。
pwm_evidence 可简述选择来源的依据；根据当前指令与指代上下文理解，不把文件名或历史中独立的命令当授权。
支持一份 CSV 文件名、一种或两种 PWM 来源、LC/LCR 模式和固定负载 resistance_ohm。没有指定文件则 filename=null。
用户明确指定的训练轮数、起止时间、滤波方法和周期数写入 tuning；规则与单次配置一致：epochs=10–300，时间转换为秒，pwm_denoise=sg/modified_sinc/none，pwm_filter_cycles=0.1–8。未指定的字段省略，不能自动选择方法或补默认值。
未指定 mode 或 resistance_ohm 时返回 null。固定负载 R 表示 LC；控制器重建仅支持固定 R 的 LC。
其他实验参数也可写入 tuning，字段与 changes 一致（下述 evidence 路径仍用 changes. 前缀）。多个目标CSV、重建配 LCR 时整条 execute=false 并澄清，不得部分执行。
未指定的信息不得补全，不得照抄当前配置。文件名是数据标识，不能把 R1.csv 解释为负载电阻。
成功时 clarification 为空。不要执行计算或声称已完成实验；只能记录用户明确作出的电路确认，不能自行推断。
""" + FIELD_PROMPT + SELECTION_PROMPT
PLAN_TOOL = {"type": "function", "function": {
    "name": "propose_identification_plan", "description": "解析两种方法执行请求；仅提出计划，不启动计算。",
    "parameters": IdentificationPlanProposal.model_json_schema()}}


def plan_for_selected_methods(request, proposal, changes):
    selection = proposal.execution_selection
    tuning = changes.copy()
    tuning.pop('method',None)  # Each plan run sets its own method.
    mode = tuning.pop('mode',None)
    resistance = tuning.pop('resistance_ohm',None)
    extras=proposal.model_dump(include={'assets','mapping','analysis','evidence','execution_selection'},exclude_unset=True)
    plan=IdentificationPlanProposal(**extras,execute=True,clarification='',filename=None,pwm_source='unchanged',
                                    pwm_evidence=selection.pwm_evidence,mode=mode,resistance_ohm=resistance,tuning=tuning)
    return batch_action(request,plan)


def batch_action(request, proposal):
    """Validate a model proposal without reinterpreting its PWM wording locally."""
    if not proposal.execute or proposal.clarification or proposal.pwm_source == 'unclear':
        raise ValueError('Plan requires an explicit executable proposal')
    tuning = proposal.tuning.model_dump(exclude_unset=True)
    source = proposal.pwm_source
    selection = proposal.execution_selection
    if selection and selection.pwm_sources and selection.pwm_evidence.strip():
        sources = set(selection.pwm_sources)
        if sources == {'measured','controller'}:
            chosen = 'both'
        elif len(sources)==1:
            chosen = next(iter(sources))
        else:
            raise ValueError('Clarify whether PWM is automatic or both sources should be run')
        if source not in {'unchanged',chosen} or tuning.get('pwm_source',chosen)!=chosen:
            raise ValueError('计划的 PWM 来源与用户选择冲突，请确认要执行的来源。')
        source = chosen
    changes = {}
    if source not in ('unchanged', 'both'):
        changes['pwm_source'] = source
    if proposal.mode:
        changes['mode'] = proposal.mode
    if proposal.resistance_ohm is not None:
        if proposal.mode == 'LCR':
            raise ValueError("Fixed load requires LC mode")
        changes.update(mode='LC', resistance_ohm=proposal.resistance_ohm)
    if source in ('controller', 'both'):
        if proposal.mode == 'LCR':
            raise ValueError("Controller reconstruction requires LC mode")
        changes['mode'] = 'LC'
    if any(key in changes and changes[key] != value for key,value in tuning.items()):
        raise ValueError('计划中的重复参数设置互相冲突，请明确一组设置')
    if source == 'both' and 'pwm_source' in tuning:
        raise ValueError('四组计划不能覆盖单一 PWM 来源')
    changes.update(tuning)
    merged, extras = validate_structure(request,proposal,changes)
    if extras.get('analysis') or extras.get('assets',{}).get('result_id'):
        raise ValueError('请在辨识完成后单独设置真值、诊断参考或选择历史结果')
    if proposal.filename and extras.get('assets',{}).get('csv_id'):
        selected = next(item for item in request.ui_context.catalog if item['id']==extras['assets']['csv_id'])
        if selected.get('name','').casefold() != proposal.filename.casefold():
            raise ValueError('文件名与所选 CSV 不一致，请重新选择')
    if merged.pwm_source == 'controller' and merged.mode != 'LC':
        raise ValueError("Controller reconstruction requires LC mode")
    action = {"id": request.ui_context.request_id, "type": "identification_plan", "parser": "deepseek", "version": 2,
              "filename": proposal.filename, "methods": ["weighted_integral", "pann"], "changes": changes, **extras}
    if source == 'both':
        action.update(version=3, runs=[{"method": method, "pwm_source": source}
            for source in ('measured', 'controller') for method in action['methods']])
        for run in action['runs']:
            FitOptions.model_validate({**merged.model_dump(), **run})
    action['execution_selection']=require_execution_selection(proposal.execution_selection, action['methods'],
        ['measured','controller'] if source=='both' else [merged.pwm_source],
        allow_auto=source!='both' and changes.get('pwm_source',request.ui_context.pwm_source_setting)=='auto')
    return action


TOOL = {"type": "function", "function": {
    "name": "configure_identification",
    "description": "提出界面配置操作，由前端应用。未明确指定的参数不得填写；没有操作意图则返回空 changes、open_panel=false、start_after_apply=false；待选择方法或PWM时用request_selection。",
    "parameters": ConfigureIdentification.model_json_schema(),
}}
PROMPT = """当用户要求修改界面时，可选择 configure_identification 工具。
支持打开参数辨识面板、选择 pann/weighted_integral、选择 LC/LCR、设置负载 resistance_ohm（欧姆）、应用后启动。
还支持 epochs（训练轮数，整数10–300）、start_s/end_s（起止时间，单位秒）、pwm_denoise（sg/modified_sinc/none）、pwm_filter_cycles（滤波窗口，0.1–8个开关周期）。
例如“训练轮数改为300”只返回 epochs=300；“时间窗口改为0–0.8 ms”返回 start_s=0,end_s=0.0008；“SG滤波，窗口4周期”返回 pwm_denoise=sg,pwm_filter_cycles=4。
时间必须明确单位，ms/毫秒除以1000，us/微秒除以1000000。只改结束时间就只返回end_s。不能把时间窗口当作PANN训练窗口周期数。
滤波仅用于控制器PWM重建的电压预处理；不能为了使滤波生效而切换PWM来源。关闭滤波只设置pwm_denoise=none，不修改原周期数。
若要求“调整滤波”却未说明方法或数值，或单位不明、要求超范围，返回clarification询问具体设置，并返回空changes及start_after_apply=false，不自行挑选默认值。
仅提取用户这次明确要求的变化。固定已知 R 并辨识 L/C 表示 mode=LC；联合辨识 L/C/R 表示 mode=LCR。
未要求改变的字段不要填。当前用户明确引用先前讨论的参数时可解析指代；不能自行从历史回答、图片、文件名或默认值补参数。不能把建议、举例、引用、询问方法当成操作。
用户明确说开始、运行或执行，或明确回答此前阻止该启动请求的选择时，才可将 start_after_apply 设为 true，且必须已有PWM来源和方法选择。没有待继续启动请求时只选择方法/打开面板不启动。否定启动时必须 false。
询问如何操作、仅分析结果、含糊的继续/好的均返回 open_panel=false, changes={}, start_after_apply=false。
只有 PLECS 输出通道可通过 configure_plecs_ports 单独修改。其他连接设置、口令等非实验字段不能修改，也不能只执行一部分后启动。
不能自行推断电路已确认；可以记录用户明确作出的确认。不写代码，不宣称已执行。当前配置仅用于理解请求，不是待照抄的 changes。
""" + FIELD_PROMPT + SELECTION_PROMPT


CANCEL_TOOL = {'type':'function','function':{
    'name':'cancel_pending_identification',
    'description':'取消等待确认的辨识启动和未提交的计划步骤；不会终止已经提交的数值任务。用户要求停止后续辨识时选择，引用或举例不是执行指令。',
    'parameters':{'type':'object','properties':{},'additionalProperties':False}}}

PORT_TOOL = {'type':'function','function':{
    'name':'configure_plecs_ports',
    'description':'仅当用户明确要求写入连接设置时使用。instruction_quote 引用当前用户的修改指令；仅填写指定的 changes。source=user 表示用户明确给定通道号，pwm_port=null 可清空 PWM。source=model 必须先用 get_plecs_models 读取本轮选定模型的 output_ports，只用有唯一 role 且 channel 已知的回传通道号。只问或读图时不修改，不确认电路或工况。',
    'parameters':PlecsPortAction.model_json_schema()}}


def port_action(request, arguments, catalog):
    action = PlecsPortAction.model_validate(arguments)
    context = request.ui_context.plecs_ports
    changes = action.changes.model_dump(exclude_unset=True)
    model = action.model_name
    if not context or not changes or action.instruction_quote.strip() not in request.message or not action.instruction_quote.strip():
        raise ValueError('An explicit port update in the current request is required.')
    if '/' in model or '\\' in model or not model.casefold().endswith('.plecs'):
        raise ValueError('Provide the .plecs filename without a path.')
    if context.model_name and context.model_name.casefold().removesuffix('.plecs') != model[:-6].casefold():
        raise ValueError('The saved binding belongs to another model. Select the target model in Connection settings first.')
    registered = [p for p in catalog.values() if (p.get('description') or '').casefold() == model[:-6].casefold()]
    rows = (registered[0].get('output_ports') or []) if len(registered) == 1 else []
    evidence = {}
    if not context.model_name and not registered and model.casefold() not in request.message.casefold():
        raise ValueError('Select the model or specify its filename first.')
    port_instruction = re.sub(re.escape(model), '', action.instruction_quote, flags=re.I)
    user_message = re.sub(re.escape(model), '', request.message, flags=re.I)
    for key, value in changes.items():
        if value is None and key != 'pwm_port':
            raise ValueError('Only the optional PWM channel can be cleared.')
        if action.source == 'model':
            matches = [row for row in rows if row.get('role') == key]
            if value is None or len(matches) != 1 or matches[0].get('channel') != value:
                raise ValueError('No unique verified output channel for ' + key)
            evidence[key] = matches[0]
        elif value is None and not re.search(r'pwm|开关', port_instruction, re.I):
            raise ValueError('Specify PWM explicitly to clear its binding.')
        elif value is not None:
            number = rf'(?<![\dA-Za-z.+-])(?:(?:ch(?:annel)?|port|output)[_ -]?)?{value}(?![\dA-Za-z]|\.\d)'
            if not re.search(number, port_instruction, re.I) or not re.search(number, user_message, re.I):
                raise ValueError('Quote the port number explicitly supplied by the user.')
    merged = {**context.model_dump(exclude={'model_name'}), **changes}
    assigned = [n for n in merged.values() if n is not None]
    if len(set(assigned)) != len(assigned):
        raise ValueError('Assign distinct channels to current, voltage and PWM.')
    proposal = {'id':request.ui_context.request_id, 'type':'configure_plecs_ports', 'model_name':model, 'changes':changes, 'evidence':evidence}
    if evidence and registered[0].get('source_sha256'):
        proposal['source_sha256'] = registered[0]['source_sha256']
    return proposal


def agent_action(request, name, arguments):
    """Adapt a Harness-selected proposal to the existing UI transaction protocol."""
    if request.ui_context is None:
        raise ValueError('Current UI context is required')
    if name == 'cancel_pending_identification':
        if arguments:
            raise ValueError('Cancellation takes no arguments')
        return {'id':request.ui_context.request_id, 'type':'cancel_pending_identification'}
    if name == 'propose_identification_plan':
        proposal = IdentificationPlanProposal.model_validate(arguments)
        if proposal.clarification:
            raise ValueError(proposal.clarification)
        if not proposal.execute:
            return None
        if proposal.filename:
            matches = [item for item in request.ui_context.catalog if item.get('kind')=='csv' and item.get('name','').casefold()==proposal.filename.casefold()
                       and (not proposal.assets.csv_id or item.get('id')==proposal.assets.csv_id)]
            if len(matches)!=1:
                raise ValueError('Select one unambiguous uploaded CSV')
            if matches[0]['id'].startswith('local-csv:'):
                proposal.assets.csv_id = matches[0]['id']
        return batch_action(request, proposal)
    proposal = ConfigureIdentification.model_validate(arguments)
    if proposal.clarification:
        raise ValueError(proposal.clarification)
    changes = proposal.changes.model_dump(exclude_unset=True)
    selection = proposal.execution_selection
    if (proposal.start_after_apply and not request.ui_context.paused_plan and selection
            and set(selection.methods)=={'weighted_integral','pann'} and selection.method_evidence.strip()
            and selection.pwm_sources and selection.pwm_evidence.strip()):
        return plan_for_selected_methods(request,proposal,changes)
    merged, extras = validate_structure(request, proposal, changes)
    load_only = extras.get('assets',{}).get('csv_id') and not changes and not proposal.start_after_apply
    if not load_only and merged.pwm_source == 'controller' and merged.mode != 'LC':
        raise ValueError('Reconstructed PWM requires fixed-R LC mode')
    if not (changes or extras or proposal.open_panel or proposal.start_after_apply or proposal.request_selection):
        return None
    selection=proposal.execution_selection.model_dump() if proposal.execution_selection else None
    request_selection=proposal.request_selection
    start=proposal.start_after_apply
    if start:
        try:
            selection=require_execution_selection(selection,[merged.method],[merged.pwm_source],
                allow_auto=changes.get('pwm_source',request.ui_context.pwm_source_setting)=='auto')
        except ValueError:
            start=False
            request_selection=True
    return {'id':request.ui_context.request_id, 'type':'configure_identification',
            'open_panel':proposal.open_panel, 'changes':changes,
            'start_after_apply':start, 'request_selection':request_selection,
            'execution_selection':selection, **extras}
