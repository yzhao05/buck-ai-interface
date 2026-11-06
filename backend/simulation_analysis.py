"""Persist simulation artifacts and re-identify with the user's selected method."""
import hashlib
import json
from pathlib import Path
import re
import time
import asyncio
import sys
import os
import signal
import subprocess
import io
import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict, Field
from .data import read_csv
from .schemas import Mapping, FitOptions
from .identification import identify

ROOT = Path(os.getenv('AGENT_RUNTIME_DIR') or Path(__file__).resolve().parents[1] / 'runtime') / 'simulation_analysis'


class Binding(BaseModel):
    model_config = ConfigDict(extra='forbid')
    model_name: str = Field(min_length=1,max_length=200)
    current_port: int = Field(ge=1,le=64)
    voltage_port: int = Field(ge=1,le=64)
    pwm_port: int | None = Field(default=None,ge=1,le=64)
    L_parameter: str = Field(min_length=1,max_length=200)
    C_parameter: str = Field(min_length=1,max_length=200)
    confirmed: bool = False
    same_circuit_and_conditions: bool = False


def directory(owner, identifier):
    if not re.fullmatch(r'agent_[0-9a-f]{32}',identifier):
        raise ValueError('Invalid experiment ID')
    return ROOT / hashlib.sha256(owner.encode()).hexdigest()[:20] / identifier


def source_snapshot(owner, identifier, result_id):
    folder = directory(owner,identifier)
    snapshot = json.loads((folder/'source.json').read_text(encoding='utf-8'))
    if snapshot['report'].get('source_result_id') != result_id:
        raise ValueError('Experiment belongs to a different source result')
    return folder,snapshot


def confirm_binding(owner, identifier, result_id, value):
    """Attach a later explicit confirmation without replacing recorded truth/options."""
    folder,snapshot = source_snapshot(owner,identifier,result_id)
    if (folder/'evaluation.json').exists():
        return
    if not value or not value.get('confirmed'):
        return
    binding = Binding.model_validate(value)
    if Path(binding.model_name).stem != snapshot['profile'].get('description'):
        raise ValueError('Confirmed output binding belongs to a different simulation model')
    temporary = folder/'binding.tmp'
    temporary.write_text(binding.model_dump_json(),encoding='utf-8')
    temporary.replace(folder/'binding.json')


def saved_evaluation(owner, identifier, result_id):
    folder,_ = source_snapshot(owner,identifier,result_id)
    cached = folder/'evaluation.json'
    if cached.exists():
        return {**json.loads(cached.read_text(encoding='utf-8')),'cached':True}
    return {'status':'uncertain','error':'No completed evaluation was saved; the interrupted calculation has not been repeated.'}


def ingest(owner, report, options, binding, source_result):
    artifacts, profile = report.pop('_artifacts',None), report.pop('_profile',None)
    if report.get('status') != 'completed':
        return report
    if not isinstance(artifacts,list) or not 2<=len(artifacts)<=3 or not isinstance(profile,dict):
        raise ValueError('Completed simulation is missing its waveform artifacts')
    if len(artifacts) != len(report['cases']):
        raise ValueError('Simulation case/artifact count mismatch')
    folder = directory(owner,report['experiment_id'])
    folder.mkdir(parents=True,exist_ok=True)
    if (folder/'source.json').exists():
        stored = json.loads((folder/'source.json').read_text(encoding='utf-8'))
        return {**stored['report'],'artifacts_received':len(stored['report']['cases']),
                'evaluation_available':bool(stored.get('binding',{}).get('confirmed')) if stored.get('binding') else False,'cached':True}
    for i, raw in enumerate(artifacts):
        if not isinstance(raw,str) or len(raw.encode())>2*1024*1024:
            raise ValueError('Invalid simulation artifact')
        frame = pd.read_csv(io.StringIO(raw))
        values = frame.to_numpy(dtype=float)
        if not 2<=len(frame)<=100000 or not 2<=len(frame.columns)<=65 or not np.isfinite(values).all() or not np.all(np.diff(values[:,0])>0):
            raise ValueError('Invalid simulation samples or time grid')
        (folder/f'case_{i}.csv').write_text(raw,encoding='utf-8')
    snapshot = {'report':report,'options':options.model_dump(),'binding':binding,
                'profile':{k:v for k,v in profile.items() if k!='path'},
                'source_result':source_result}
    (folder/'source.json').write_text(json.dumps(snapshot,allow_nan=False),encoding='utf-8')
    return {**report,'artifacts_received':len(artifacts),'evaluation_available':bool(binding and binding.get('confirmed')),
            'scope':'已接回仿真 CSV。调用 evaluate_plecs_experiment 可按用户原方法重新辨识；仿真效应不直接证明实测物理原因。'}


def evaluate(owner, identifier, source_result_id, seconds=150):
    folder,snapshot = source_snapshot(owner,identifier,source_result_id)
    report, profile = snapshot['report'], snapshot['profile']
    cached = folder/'evaluation.json'
    if cached.exists():
        return {**json.loads(cached.read_text(encoding='utf-8')),'cached':True}
    annotation = folder/'binding.json'
    binding = Binding.model_validate(json.loads(annotation.read_text(encoding='utf-8')) if annotation.exists() else snapshot.get('binding') or {})
    if not binding.confirmed or Path(binding.model_name).stem != profile.get('description'):
        raise ValueError('请在连接设置确认此模型的输出端口和 L/C 元件绑定；不能猜测端口。')
    ports = [binding.current_port,binding.voltage_port]+([binding.pwm_port] if binding.pwm_port else [])
    if len(set(ports)) != len(ports):
        raise ValueError('Current, voltage and PWM require different output ports')
    parameters = profile['parameters']
    truth_keys = {'L_H':binding.L_parameter,'C_F':binding.C_parameter}
    for unit,key in truth_keys.items():
        if key not in parameters or parameters[key]['field'] != unit[0]:
            raise ValueError('L/C reference must bind to registered inductor/capacitor values')
        if key not in report.get('resolved_base_parameters',{}):
            raise ValueError('仿真未返回经 RPC 读取的 L/C 实际参数，不能仅凭模型文件标称值计算真值误差。')
    started = time.monotonic()
    def progress(*_):
        if time.monotonic()-started>seconds:
            raise ValueError('Simulation identification time budget reached')
    cases = []
    for i,case in enumerate(report['cases']):
        progress()
        data = read_csv((folder/f'case_{i}.csv').read_bytes(),f'case_{i}.csv')
        columns = list(data.frame.columns)
        if max(ports)>=len(columns):
            raise ValueError('Configured output port is absent from the returned waveform')
        mapping = Mapping(time=columns[0],current=columns[binding.current_port],voltage=columns[binding.voltage_port],
                          pwm=columns[binding.pwm_port] if binding.pwm_port else None)
        options = FitOptions.model_validate(snapshot['options'])
        truth = {unit:float(report['resolved_base_parameters'][key]) for unit,key in truth_keys.items()}
        if any(not np.isfinite(value) or value<=0 for value in truth.values()):
            raise ValueError('Invalid resolved L/C reference')
        changed = report['design']['parameter']
        if i:
            for unit,key in truth_keys.items():
                if key==changed:
                    truth[unit] = float(case['value'])
            field = parameters[changed]['field']
            # Known inputs must follow the actual intervention. Ambiguous components
            # require a new explicit binding, never a silent guessed configuration.
            if field in {'V','f','R'}:
                if sum(p['field']==field for p in parameters.values()) != 1:
                    raise ValueError('Intervention changes an ambiguous known input; confirm its identification configuration first')
                options = options.model_copy(update={{'V':'vin','f':'frequency_hz','R':'resistance_ohm'}[field]:float(case['value'])})
            elif changed not in truth_keys.values() and field not in {'i_init','v_init'}:
                raise ValueError('Intervention changes a controller/input not mapped to the identification model; waveform comparison remains available')
        result = identify(data,mapping,options,progress=progress)
        errors = {key:abs(result['parameters'][unit]/truth[unit]-1)*100 for key,unit in [('L','L_H'),('C','C_F')]}
        cases.append({'kind':case['kind'],'value':case['value'],'parameters':result['parameters'],
                      'ground_truth':truth,'relative_error_pct':errors,'metrics':result['metrics'],
                      'method':options.method,'options':options.model_dump()})
        (folder/f'fit_{i}.json').write_text(json.dumps(result,allow_nan=False),encoding='utf-8')
    baseline = cases[0]['relative_error_pct']
    for case in cases[1:]:
        case['error_change_percentage_points'] = {key:case['relative_error_pct'][key]-baseline[key] for key in baseline}
    source = snapshot.get('source_result') or {}
    result = {'status':'completed','experiment_id':identifier,'source_result_id':source_result_id,'cases':cases,
              'source_parameter_comparison':source.get('parameter_comparison'),
              'same_circuit_and_conditions_user_confirmed':binding.same_circuit_and_conditions,
              'scope':'仿真模型内按登记 L/C 计算的参数误差；对实测物理原因的判断仍需相符证据。',
              'method_preserved':True}
    temporary = cached.with_suffix('.tmp')
    temporary.write_text(json.dumps(result,allow_nan=False),encoding='utf-8')
    temporary.replace(cached)
    return result


TOOL = {'type':'function','function':{'name':'evaluate_plecs_experiment','description':'对已回传的仿真基线和干预 CSV 沿用用户所选方法重新辨识，比较 L/C 真值误差变化；不会修改界面配置。需用户已确认输出端口和元件绑定。','parameters':{'type':'object','properties':{'experiment_id':{'type':'string'}},'required':['experiment_id'],'additionalProperties':False}}}


async def evaluate_async(owner, identifier, result_id):
    from .harness_runtime import scope
    directory(owner,identifier)  # validate before constructing arguments
    seconds = 150
    state = scope.get() or {}
    if state.get('remaining'):
        seconds = min(seconds,state['remaining']())
    process = await asyncio.create_subprocess_exec(sys.executable,'-m','backend.simulation_analysis',owner,identifier,result_id or '',str(seconds),
        cwd=str(Path(__file__).resolve().parents[1]),stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL,
        **({'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {'start_new_session':True}))
    try:
        stdout,_ = await asyncio.wait_for(process.communicate(),timeout=seconds)
        if process.returncode:
            raise ValueError('Simulation identification failed; inspect the persisted waveform and mapping')
        return json.loads(stdout)
    finally:
        if process.returncode is None:
            if os.name=='nt':
                await asyncio.to_thread(subprocess.run,['taskkill','/PID',str(process.pid),'/T','/F'],
                                        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
            else:
                os.killpg(process.pid,signal.SIGKILL)
            await process.wait()


if __name__ == '__main__':
    try:
        response = evaluate(sys.argv[1],sys.argv[2],sys.argv[3] or None,float(sys.argv[4]))
    except Exception as exc:
        response = {'status':'unavailable','error':str(exc)}
    print(json.dumps(response,ensure_ascii=True,allow_nan=False))
