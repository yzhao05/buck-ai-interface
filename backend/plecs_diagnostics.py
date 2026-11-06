"""Bounded experiments on private copies of explicitly registered PLECS models.

No model-generated code, file paths, RPC methods or solver commands are accepted.
"""
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import threading
import time
import uuid
import xmlrpc.client

import numpy as np
from . import plecs_local

ROOT = Path(__file__).resolve().parents[1]
SLOT = threading.Lock()


def profiles(owner=None):
    if owner:
        return plecs_local.settings(owner)['profiles']
    path = Path(os.getenv('PLECS_PROFILES', str(ROOT / 'runtime/plecs_profiles.json')))
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def catalog(owner=None):
    registered = profiles(owner)
    return {'status': 'configured' if registered else 'unconfigured',
            'models': {key: {'description': p.get('description'), 'source_sha256':p.get('source_sha256'), 'parameters': p['parameters'],
                             'signals': p['signals'], 'duration_s': p['duration_s'], 'experiment_issues':p.get('experiment_issues', [])}
                       for key, p in registered.items()},
            'scope': '本地后端连接 localhost:1080；仅操作已登记电路的独立副本。参考电路不自动等于本次 CSV 电路。'}


class Transport(xmlrpc.client.Transport):
    def make_connection(self, host):
        return http.client.HTTPConnection(host, timeout=25)


def proxy():
    return xmlrpc.client.ServerProxy('http://127.0.0.1:1080/RPC2', transport=Transport(), allow_none=True)


def arrays(raw, channels):
    t = np.asarray(raw['Time'], dtype=float).reshape(-1)
    y = np.asarray(raw['Values'], dtype=float)
    # PLECS RPC returns signals x time, unlike native Octave output.
    if not channels:
        channels = y.shape[0] if y.ndim == 2 and y.shape[1] == len(t) else y.shape[1] if y.ndim == 2 else 0
    if y.shape == (channels, len(t)):
        y = y.T
    if len(t) < 2 or y.shape != (len(t), channels) or not np.isfinite(t).all() or not np.isfinite(y).all() or np.any(np.diff(t) <= 0):
        raise ValueError('PLECS 输出信号/时间不符合登记配置')
    return t, y


def experiment(arguments, owner, result_id=None):
    allowed = {'model', 'parameter', 'values', 'hypothesis', 'expected_observation'}
    if not isinstance(arguments, dict) or set(arguments)-{'output_points'} != allowed:
        raise ValueError('实验需要 model、parameter、values、hypothesis、expected_observation')
    p = profiles(owner).get(arguments['model'])
    if not p:
        raise ValueError('未登记的电路；先读取 PLECS 模型目录')
    if p.get('experiment_issues'):
        return {'status':'unavailable', 'error':' '.join(p['experiment_issues'])}
    parameter = p['parameters'].get(arguments['parameter'])
    if not parameter:
        raise ValueError('不允许修改该参数')
    values = arguments['values']
    if not isinstance(values, list) or not 1 <= len(values) <= 2:
        raise ValueError('每次实验仅允许 1–2 个单因素对照值')
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or not parameter['min'] <= v <= parameter['max'] for v in values):
        raise ValueError('实验参数超出登记范围')
    for key in ('hypothesis', 'expected_observation'):
        if not isinstance(arguments[key], str) or not 1 <= len(arguments[key]) <= 1000:
            raise ValueError('需要简短假设和可区分原因的预期观测')
    points = arguments.get('output_points',2001)
    if type(points) is not int or not 201<=points<=20001:
        raise ValueError('输出网格应为 201–20001 个点')
    if not SLOT.acquire(blocking=False):
        return {'status': 'unavailable', 'error': 'PLECS 实验正在运行'}
    name = 'agent_' + uuid.uuid4().hex
    deadline = time.monotonic()+120
    directory = ROOT / 'runtime/plecs' / hashlib.sha256(owner.encode()).hexdigest()[:20] / name
    client = None
    loaded = False
    try:
        source = Path(p['path']).read_bytes()
        if p.get('source_sha256') and hashlib.sha256(source).hexdigest() != p['source_sha256']:
            raise ValueError('电路文件已变化，请重新搜索或选择模型后再实验')
        text = source.decode('utf-8-sig')
        text, count = re.subn(r'(\bName\s+")[^"]*(")', lambda m: m[1]+name+m[2], text, count=1)
        if count != 1:
            raise ValueError('无法识别 PLECS 模型名称')
        directory.mkdir(parents=True)
        copy = directory / (name+'.plecs')
        copy.write_text(text, encoding='utf-8')
        client = proxy()
        client.plecs.load(str(copy))
        loaded = True
        component = name + '/' + parameter['component']
        original = client.plecs.get(component, parameter['field'])
        resolved_parameters = {}
        for key, spec in p['parameters'].items():
            if time.monotonic()>deadline:
                raise ValueError('PLECS execution time budget reached')
            if spec['field'] in {'L','C'}:
                try:
                    actual = float(client.plecs.get(name+'/'+spec['component'],spec['field']))
                    if math.isfinite(actual) and actual>0:
                        resolved_parameters[key] = actual
                except (ValueError,TypeError):
                    pass
                except xmlrpc.client.Error:
                    break
        duration = float(p['duration_s'])
        if not 0 < duration <= .1:
            raise ValueError('仿真时段必须在 0–0.1 秒内')
        times = np.linspace(0, duration, points).tolist()
        options = {'SolverOpts': {'StartTime': 0., 'TimeSpan': duration, 'Timeout': 12.,
                                  'OutputTimes': times, 'OutputTimesOption': 'specified'}}
        cases = []
        baseline = None
        for i, value in enumerate([None, *values]):
            if time.monotonic()>deadline:
                raise ValueError('PLECS execution time budget reached')
            if value is not None:
                client.plecs.set(component, parameter['field'], str(value))
            t, y = arrays(client.plecs.simulate(name, options), len(p['signals']))
            signals = p['signals'] or [f'output_{j+1}' for j in range(y.shape[1])]
            if baseline is None:
                baseline = (t, y)
            if not np.allclose(t, baseline[0], rtol=0, atol=1e-12):
                raise ValueError('对照时间网格不一致')
            np.savetxt(directory / f'case_{i}.csv', np.column_stack((t,y)), delimiter=',',
                       header=','.join(['time_s', *signals]), comments='')
            cases.append({'kind': 'baseline' if i == 0 else 'intervention',
                          'value': original if value is None else value,
                          'signals': {signal: {'mean': float(np.mean(y[:,j])),
                                      'peak_to_peak': float(np.ptp(y[:,j])),
                                      'rms_change_from_baseline': float(np.sqrt(np.mean((y[:,j]-baseline[1][:,j])**2)))}
                                      for j, signal in enumerate(signals)}})
        report = {'status': 'completed', 'experiment_id': name, 'source_result_id': result_id,
                  'source_sha256': hashlib.sha256(source).hexdigest(), 'design': arguments,
                  'cases': cases, 'sample_count': len(times),
                  'resolved_base_parameters':resolved_parameters,
                  'output_step_s': duration/(points-1), 'duration_s':duration,
                  'sampling_note':'固定输出网格用于对照；未验证其足以解析开关边沿，不据此判断高频纹波或延迟。',
                  'scope': '单因素仿真干预，仅验证登记模型内的数值效应；未证明实测误差的物理原因，也未重新辨识。'}
        (directory/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        return report
    except (OSError, ValueError, KeyError, TypeError, xmlrpc.client.Error) as exc:
        return {'status': 'failed', 'error': str(exc), 'experiment_id': name,
                'note': '未完成实验，不得引用为已验证结果。检查本地 PLECS RPC 1080 和模型输出端口。'}
    finally:
        if loaded:
            try:
                client.plecs.close(name)
            except Exception:
                pass
        SLOT.release()


def open_model(owner, model):
    """Open a private copy; never overwrite or close the user's original model."""
    profile = profiles(owner).get(model)
    if not profile:
        raise ValueError('请先选择已登记的模型。')
    if not SLOT.acquire(blocking=False):
        return {'status':'unavailable', 'error':'PLECS 正在执行另一项操作。'}
    try:
        raw = Path(profile['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest() != profile['source_sha256']:
            raise ValueError('电路文件已变化，请重新选择模型。')
        name = 'view_' + uuid.uuid4().hex
        text, count = re.subn(r'(\bName\s+")[^"]*(")', lambda m:m[1]+name+m[2], raw.decode('utf-8-sig'), count=1)
        if count != 1:
            raise ValueError('无法识别模型名称。')
        directory = ROOT / 'runtime/plecs' / hashlib.sha256(owner.encode()).hexdigest()[:20] / name
        directory.mkdir(parents=True)
        filename = directory / (name+'.plecs')
        filename.write_text(text, encoding='utf-8')
        with proxy() as rpc:
            rpc.plecs.load(str(filename))
        return {'status':'opened', 'model':model, 'opened_model':name, 'copy_path':str(filename), 'simulated':False}
    except (OSError, ValueError, xmlrpc.client.Error) as exc:
        return {'status':'failed', 'error':str(exc)}
    finally:
        SLOT.release()


TOOLS = [
    {'type':'function','function':{'name':'get_plecs_job_result','description':'读取已发出的云端 PLECS 任务结果，包括预算结束后迟到的真实结果；不会重新执行实验。','parameters':{'type':'object','properties':{'job_id':{'type':'string'}},'required':['job_id'],'additionalProperties':False}}},
    {"type":"function","function":{"name":"open_plecs_model","description":"在 PLECS 打开已选择电路的独立副本，不执行仿真、不修改原文件。","parameters":{"type":"object","properties":{"model":{"type":"string"}},"required":["model"],"additionalProperties":False}}},
    {'type':'function','function':{'name':'search_plecs_models','description':'递归列出已配置模型文件夹中的名称与路径，不读取文件内容、不登记模型、不启动程序。可由 Agent 提供精确名称/路径用于匹配；省略 hints 则列出候选；通过 next_offset 翻页，search_complete=false 表示扫描不完整。','parameters':{'type':'object','properties':{'hints':{'type':'array','items':{'type':'string','maxLength':1000},'maxItems':30},'offset':{'type':'integer','minimum':0,'default':0},'limit':{'type':'integer','minimum':1,'maximum':100,'default':30}},'additionalProperties':False}}},
    {'type':'function','function':{'name':'check_plecs_connection','description':'只检查本机 PLECS RPC 1080 是否响应，不启动程序、不读取或打开模型、不仿真。','parameters':{'type':'object','properties':{},'additionalProperties':False}}},
    {'type':'function','function':{'name':'connect_plecs','description':'检查本机 RPC 1080，必要时启动用户在连接设置保存的 PLECS 程序。','parameters':{'type':'object','properties':{},'additionalProperties':False}}},
    {'type':'function','function':{'name':'select_plecs_model','description':'读取并登记已配置模型文件夹内的 .plecs 文件，解析可调参数，不在 PLECS 中打开、不仿真。路径来自搜索结果或用户上下文，不能猜测；用户禁止读取文件时不要调用。','parameters':{'type':'object','properties':{'path':{'type':'string'}},'required':['path'],'additionalProperties':False}}},
    {'type':'function','function':{'name':'get_plecs_models','description':'读取已登记本地电路、可调参数范围及输出信号；不是当前 CSV 电路匹配证明。','parameters':{'type':'object','properties':{},'additionalProperties':False}}},
    {'type':'function','function':{'name':'run_plecs_experiment','description':'在登记电路的独立副本上运行基线及最多两个单因素对照，返回实际数值差异。先读取目录、说明假设及预期观测。','parameters':{
        'type':'object','properties':{'model':{'type':'string'},'parameter':{'type':'string'},
        'values':{'type':'array','items':{'type':'number'},'minItems':1,'maxItems':2},
        'hypothesis':{'type':'string','maxLength':1000},'expected_observation':{'type':'string','maxLength':1000},
        'output_points':{'type':'integer','minimum':201,'maximum':20001,'description':'可选输出采样点数，默认 2001；可提高分辨率检查数值/采样误差，所有对照共享网格。'}},
        'required':['model','parameter','values','hypothesis','expected_observation'],'additionalProperties':False}}}
]
