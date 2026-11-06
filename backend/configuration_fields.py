"""Validate explicit patches against the form and its private attachment catalog."""
import re
import math

from .schemas import ControllerParameters, FitOptions, Mapping


TUNING_FIELDS = {'epochs', 'start_s', 'end_s', 'pwm_denoise', 'pwm_filter_cycles'}
EDIT_FIELDS = {
    'vin':r'\bvin\b|输入电压', 'frequency_hz':r'开关频率|\bfrequency\b',
    'train_fraction':r'训练(?:比例|占比|区间占比)|train_fraction|training\s+(?:split|fraction)',
    'pann_initial_state':r'窗口初值|pann_initial_state|initial\s+state',
    'pann_window_cycles':r'窗口长度|pann_window_cycles|pann\s*(?:窗口周期|window\s*(?:length|cycles))',
    **{'controller.'+key:r'\b'+label+r'\s*(?:=|＝|改|设|调|to)' for key,label in [('R1_ohm','r1'),('R2_ohm','r2'),('R3_ohm','r3'),('R4_ohm','r4'),('C2_F','c2'),('C3_F','c3')]}}


def leaves(values, prefix):
    for key, value in values.items():
        path = prefix + key
        if isinstance(value, dict):
            yield from leaves(value, path + '.')
        else:
            yield path, value


def check_numeric_evidence(path, value, snippet):
    """SI conversions must agree with the user's quoted value, not just its presence."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return
    text = snippet.lower().replace('μ','u').replace('µ','u')
    scale = 1
    if path.endswith('frequency_hz'):
        scale = 1e6 if 'mhz' in text else 1e3 if 'khz' in text or '千赫' in text else 1
    elif path.endswith('_ohm'):
        scale = 1e3 if re.search(r'k(?:Ω|ω|ohm)|千欧',text) else 1
    elif path.endswith(('_F','_H')):
        unit = 'f' if path.endswith('_F') else 'h'
        if re.search(r'n'+unit+'|纳',text): scale=1e-9
        elif re.search(r'u'+unit+'|微',text): scale=1e-6
        elif re.search(r'm'+unit+'|毫',text): scale=1e-3
    elif path.endswith(('_V','vin')):
        scale = .001 if 'mv' in text or '毫伏' in text else 1
    elif path.endswith(('train_fraction','duty_min','duty_max','carrier_phase_at_t0')) and re.search(r'%|％|百分',text):
        scale = .01
    numbers = [float(n)*scale for n in re.findall(r'(?<![a-z0-9_.])[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:e[+-]?\d+)?',text)]
    if not any(math.isclose(value,n,rel_tol=1e-9,abs_tol=1e-15) for n in numbers):
        raise ValueError('设置值与指令中的数值或单位不一致：'+path)


def validate_fields(request, proposal, changes):
    extras = {key: getattr(proposal, key).model_dump(exclude_unset=True) for key in ('mapping', 'assets', 'analysis')}
    explicit = re.sub(r'“[^”]*”|「[^」]*」|`[^`]*`', '', request.message)
    paths = dict(leaves(changes,''))
    active_clauses=[clause for clause in re.split(r'[，,。;；\n]|然后|\band\b',explicit.lower())
                    if not re.search(r'保持|不变|保留|不要改|\b(?:keep|unchanged)\b',clause)]
    active='，'.join(active_clauses)
    if 'method' in changes:
        pattern=r'(?<![a-z])pann(?![a-z])' if changes['method']=='pann' else r'积分|\bintegral\b'
        if not re.search(pattern,active): raise ValueError('未明确指定的方法不能自动修改')
    if 'mode' in changes:
        pattern=r'(?<![a-z])lcr(?![a-z])|联合辨识|联合识别' if changes['mode']=='LCR' else r'(?<![a-z])lc(?![a-z])|固定|已知.*(?:负载|电阻)|\b(?:fix|fixed|known)\b'
        inferred_plan=getattr(proposal,'pwm_source',None) in ('controller','both')
        if not inferred_plan and not re.search(pattern,active): raise ValueError('请明确 LC/LCR 辨识目标')
    if 'resistance_ohm' in changes:
        clauses=[c for c in active_clauses if re.search(r'负载|电阻|(?<![a-z0-9])r(?![a-z0-9])|resistance|load',c)]
        if not clauses: raise ValueError('未明确指定的负载电阻不能修改')
        check_numeric_evidence('changes.resistance_ohm',changes['resistance_ohm'],'，'.join(clauses))
    for clause in re.split(r'[，,。;；\n]|然后|\band\b',explicit.lower()):
        if re.search(r'保持|不变|保留|不要改|\b(?:keep|unchanged)\b',clause): continue
        editing = re.search(r'改|设|调|切换|=|＝|\b(?:set|change|adjust|switch)\b',clause)
        if not editing: continue
        for key,pattern in EDIT_FIELDS.items():
            if key=='pann_window_cycles' and re.search(r'滤波|去噪|平滑|filter|smooth',clause) and 'pann' not in clause: continue
            if re.search(pattern,clause) and key not in paths:
                raise ValueError('未完整解析要求修改的设置：'+key)
        if re.search(r'pwm\s*(?:来源|source|切换|改为)',clause) and 'pwm_source' not in changes:
            raise ValueError('未完整解析 PWM 来源设置')
    for path, value in leaves({'changes': changes, **extras}, ''):
        if path.startswith('changes.') and path[8:] in TUNING_FIELDS|{'method','mode','resistance_ohm'}:
            continue
        if path == 'changes.pwm_source' and getattr(proposal, 'pwm_source', None) == value and getattr(proposal, 'pwm_evidence', '') in explicit and proposal.pwm_evidence:
            continue
        snippet = proposal.evidence.get(path, '')
        evidence_text = request.message if path.startswith(('assets.', 'analysis.reference_csv_id', 'mapping.')) else explicit
        if not snippet.strip() or snippet not in evidence_text or re.search(r'保持|不变|不要改|\b(?:keep|unchanged|do not change)\b',snippet,re.I):
            raise ValueError('请明确指定要修改的字段，本次未应用：' + path)
        check_numeric_evidence(path,value,snippet)
        # A factual confirmation must come from the user, not inferred from a picture or defaults.
        if path in ('changes.controller_confirmed', 'analysis.reference_confirmed') and value is True:
            awaiting = request.ui_context.pending_start if path == 'changes.controller_confirmed' else request.ui_context.pending_reference_confirmation
            affirmative = awaiting and re.fullmatch(r'是的|是|好的|可以|yes|ok',snippet.strip(),re.I)
            if (not affirmative and not re.search(r'确认|核对.*一致|\b(?:confirm(?:ed)?|verified)\b', snippet, re.I)) or re.search(r'不确认|未确认|没有确认|不要|尚未|怎么|如何|是否|什么意思|[?？]|\b(?:not|unconfirmed|how|why)\b', explicit, re.I):
                raise ValueError('需要用户明确确认电路或参考数据条件，本次未应用')
        if value is None and path not in ('mapping.pwm', 'assets.circuit_id', 'analysis.reference_csv_id', 'analysis.truth_L_H', 'analysis.truth_C_F'):
            raise ValueError('不能清空该字段：' + path)
    catalog = request.ui_context.catalog
    if extras['assets'].get('result_id') and (extras['assets'].get('csv_id') or extras['mapping'] or getattr(proposal,'start_after_apply',False) or getattr(proposal,'execute',False)):
        raise ValueError('查看历史结果与更换输入或开始新辨识请分别发送指令')
    for group, key, kind in [('assets','csv_id','csv'), ('assets','circuit_id','circuit'), ('assets','result_id','result'), ('analysis','reference_csv_id','csv')]:
        value = extras[group].get(key)
        if value is not None:
            matches = [item for item in catalog if item.get('id') == value and item.get('kind') == kind]
            if len(matches) != 1:
                raise ValueError('所选文件不在当前上传文件列表中，请重新指定文件')
    patch = {**changes}
    current = request.ui_context.options.model_dump()
    if 'controller' in patch:
        patch['controller'] = ControllerParameters.model_validate({**(current['controller'] or ControllerParameters().model_dump()), **patch['controller']}).model_dump()
    if patch.get('pwm_source', request.ui_context.pwm_source_setting) == 'auto':
        mapping = {**(request.ui_context.mapping.model_dump() if request.ui_context.mapping else {}), **extras['mapping']}
        patch['pwm_source'] = 'controller' if mapping and not mapping.get('pwm') else 'measured'
    merged = FitOptions.model_validate({**current, **patch})
    if 'train_fraction' in changes and abs(changes['train_fraction'] * 20 - round(changes['train_fraction'] * 20)) > 1e-8:
        raise ValueError('训练占比与界面一致，仅支持 50%–85%，每档 5%')
    if merged.start_s is not None and merged.end_s is not None and merged.start_s >= merged.end_s:
        raise ValueError('开始时间必须早于结束时间，本次设置未应用')
    if extras['mapping'] and not extras['assets'].get('csv_id'):
        mapping = Mapping.model_validate({**(request.ui_context.mapping.model_dump() if request.ui_context.mapping else {}), **extras['mapping']})
        if any(getattr(mapping, key) not in request.ui_context.columns for key in ('time','current','voltage','pwm') if getattr(mapping,key) is not None):
            raise ValueError('列映射必须使用当前 CSV 中存在的列名')
    return merged, {key: value for key, value in extras.items() if value}


FIELD_PROMPT = """
支持全部实验/分析表单字段；不改连接设置、口令、回答模式。
changes 可设置 vin(V)、frequency_hz(Hz)、train_fraction(0.50–0.85，每档0.05)、pwm_source(auto/measured/controller)、
pann_initial_state(joint/measured)、pann_window_cycles(0自动/1/2/3)、controller_confirmed。
controller 是局部补丁，仅写指定元件：R1_ohm/R2_ohm/R3_ohm/R4_ohm(Ω)、C2_F/C3_F(F)、opamp_gain、reference_V、pwm_gain、
duty_min/duty_max(0–1)、carrier_phase_at_t0(0–1)、initial_c2_V/initial_c3_V。kΩ×1000,nF×1e-9,kHz×1000,百分比÷100。
未指定字段省略，包含嵌套 controller；start_s/end_s=null 仅表示用户明确要求清空时间边界。
mapping 可局部修改 time/current/voltage/pwm 列名、time_unit(s/ms/us/ns)、current_unit(A/mA)、voltage_unit(V/mV)；pwm=null 清除 PWM 映射。
assets.csv_id 切换已上传 CSV，assets.circuit_id 切换已上传电路参考图(null恢复默认图)。
assets.result_id 选择当前会话已保存辨识结果供查看/分析，不修改实验输入配置，不能和切换CSV或列映射混用。
两方法/四组执行计划不同时设置 analysis 或 assets.result_id；先辨识完成，再单独指定真值或参考。
只能从 ui_context.catalog 根据用户明确指定的文件选择 id；同名多份或不明确时 clarification，不能猜。
换电路参考图不等于更换 CSV 或自动识别元件参数；需要这些变化则分别填写。
analysis.truth_L_H/truth_C_F 设置真实L/C(SI单位，可null清空)，calculate_error=true 仅用户要求计算真值误差时；
analysis.reference_csv_id 从已上传CSV选择诊断干净参考(null清除)，reference_confirmed=true 只在用户明确确认同一实验同一工况时。
controller_confirmed=true 仅用户明确确认实际接线与参数一致时；改电路/参数会使旧确认失效。
ui_context.pending_start=true 表示上次明确请求启动但正在等电路确认；本次明确回答确认（包括“是的”）时可设置 controller_confirmed=true 并 start_after_apply=true，继续原任务。未有pending_start时“是的”不能授权启动。
pending_reference_confirmation=true 且 pending_start=false 时，简短的“确认/是的”回答的是参考数据同工况问题，只设置 analysis.reference_confirmed=true，不启动辨识。
除方法/LC/LCR/R/轮数/时间/滤波外，每个修改字段都必须提供 evidence：键为 changes.vin、changes.controller.R1_ohm、
mapping.time、assets.csv_id、analysis.truth_L_H 等完整路径，值逐字摘录当前用户指令中授权该字段修改的短语。
evidence 不是从表单、历史、附件文字中获取的授权。保持某字段不变不是修改授权。
任何修改要求不明确、未支持或缺少值时，整条返回 clarification，不部分执行后开始。
"""
