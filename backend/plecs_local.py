"""Local-only, user-selected PLECS installation and model registration."""
import math
import http.client
import xmlrpc.client
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time

ROOT = Path(__file__).resolve().parents[1] / 'runtime/plecs_settings'
LOCK = threading.Lock()
PROCESS = None


def config_path(owner):
    return ROOT / (hashlib.sha256(owner.encode()).hexdigest()+'.json')


def settings(owner):
    path = config_path(owner)
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'executable':'', 'profiles':{}}


def save(owner, value):
    ROOT.mkdir(parents=True, exist_ok=True)
    path = config_path(owner)
    temp = path.with_suffix('.tmp')
    with LOCK:
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(path)


def configure(owner, executable, model_directory=''):
    path = Path(executable.strip().strip('"'))
    if executable.strip() and (not path.is_absolute() or path.name.lower() != 'plecs.exe' or not path.is_file()):
        raise ValueError('请选择本机存在的 PLECS.exe 完整路径')
    state = settings(owner)
    old_directory = state.get('model_directory', '')
    state['executable'] = str(path.resolve()) if executable.strip() else ''
    if model_directory:
        directory = Path(model_directory.strip().strip('"'))
        if not directory.is_absolute() or not directory.is_dir():
            raise ValueError('电路模型文件夹不存在，请填写本机完整路径')
        state['model_directory'] = str(directory.resolve())
    else:
        state['model_directory'] = ''
    if old_directory != state['model_directory']:
        state['profiles'] = {}
    save(owner, state)
    return {'status':'configured', 'executable':state['executable']}


def search_models(owner, hints, offset=0, limit=30):
    """List paths only. Reading/registering a model is a separate Agent action."""
    if not isinstance(hints, list) or len(hints)>30 or any(not isinstance(h,str) or len(h)>1000 for h in hints):
        raise ValueError('hints must be an array of at most 30 names or paths')
    if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=100:
        raise ValueError('offset must be nonnegative and limit must be 1–100')
    state = settings(owner)
    root = state.get('model_directory')
    if not root or not Path(root).is_dir():
        return {'status':'unconfigured','error':'请在连接设置中填写电路模型文件夹。'}
    def stem(value):
        return Path(value.replace('\\','/')).stem.casefold().strip()
    explicit_paths = {str(Path(h).resolve()).casefold() for h in hints if isinstance(h, str) and Path(h).is_absolute() and Path(h).suffix.lower() == '.plecs'}
    names = {stem(s) for s in hints if isinstance(s,str) and s.strip()}
    matches, candidates = [], []
    candidate_count = 0
    count = 0
    complete = True
    started = time.monotonic()
    errors = []
    for directory, dirs, files in os.walk(root, followlinks=False, onerror=lambda exc: errors.append(str(exc))):
        dirs[:] = sorted(d for d in dirs if not Path(directory,d).is_symlink() and not Path(directory,d).is_junction())
        count += len(files) + len(dirs)
        if count > 10000 or time.monotonic()-started > 4:
            complete = False
            break
        for file in sorted(files):
            path = Path(directory,file)
            if path.suffix.lower() != '.plecs' or path.is_symlink():
                continue
            entry = {'name':path.stem,'path':str(path)}
            if offset <= candidate_count < offset+limit:
                candidates.append(entry)
            candidate_count += 1
            if (str(path.resolve()).casefold() in explicit_paths if explicit_paths else stem(file) in names):
                matches.append(entry)
    if errors:
        complete = False
    page = {'candidates':candidates, 'candidate_count':candidate_count, 'offset':offset,
            'next_offset':offset+len(candidates) if offset+len(candidates)<candidate_count else None,
            'search_complete':complete}
    if len(matches)==1 and complete:
        return {'status':'matched','match':matches[0],'matches':matches,**page}
    return {'status':('incomplete' if not complete else 'listed' if not names else
                      'ambiguous' if len(matches)>1 else 'not_found'),
            'matches':matches,**page,
            'note':'仅列出文件名与路径，未读取或登记模型。由 Agent 根据用户指令选择；匹配名称不是电路一致性证明。'}


class ProbeTransport(xmlrpc.client.Transport):
    def make_connection(self, host):
        return http.client.HTTPConnection(host, timeout=1)


def listening():
    # An open TCP port might be a proxy or another application, not PLECS.
    try:
        with xmlrpc.client.ServerProxy('http://127.0.0.1:1080/RPC2', transport=ProbeTransport()) as rpc:
            methods = rpc.system.listMethods()
            return isinstance(methods, list) and {'plecs.load', 'plecs.simulate', 'plecs.get'} <= set(methods)
    except (OSError, xmlrpc.client.Error, ValueError, http.client.HTTPException):
        return False


def ensure_running(owner):
    global PROCESS
    if listening():
        return {'status':'ready','port':1080,'started':False}
    state = settings(owner)
    if not state['executable']:
        return {'status':'unconfigured','error':'请在连接设置中填写本机 PLECS.exe 路径。'}
    with LOCK:
        if PROCESS is None or PROCESS.poll() is not None:
            info = subprocess.STARTUPINFO() if os.name == 'nt' else None
            if info:
                info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                info.wShowWindow = 0
            PROCESS = subprocess.Popen([state['executable']], shell=False, startupinfo=info,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        if listening():
            return {'status':'ready','port':1080,'started':True}
        time.sleep(.5)
    return {'status':'unavailable','error':'已启动 PLECS，但 1080 未就绪。请检查许可窗口及 Preferences 中的 RPC 开关与端口。'}


def read_model(path):
    limit = 10 * 1024 * 1024
    with Path(path).open('rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('模型文件超过 10 MiB')
    return raw


def register(owner, filename):
    path = Path(filename.strip().strip('"'))
    if not path.is_absolute() or path.suffix.lower() != '.plecs' or not path.is_file():
        raise ValueError('需要本机存在的 .plecs 电路文件完整路径')
    raw = read_model(path)
    text = raw.decode('utf-8-sig').replace('\r\n','\n').replace('\r','\n')
    if not re.match(r'\s*Plecs\s*\{', text):
        raise ValueError('文件不是有效的 PLECS 模型')
    parameters = {}
    fields = {'Inductor': {'L', 'i_init'}, 'Capacitor': {'C','v_init'},
              'Resistor': {'R'}, 'DCVoltageSource': {'V'}, 'TriangleGenerator': {'f','Delay'},
              'Constant': {'Value'}}
    for block in re.findall(r'^    Component \{\n(.*?)^    \}', text, re.M|re.S):
        kind = re.search(r'^\s*Type\s+(\w+)',block,re.M)
        name = re.search(r'^\s*Name\s+"([^"]+)"',block,re.M)
        if not kind or not name or kind[1] not in fields:
            continue
        for field, expression in re.findall(r'Variable\s+"([^"]+)"\s+Value\s+"([^"]*)"',block):
            if field not in fields[kind[1]]:
                continue
            try:
                value = float(expression)
            except ValueError:
                continue
            if not (-1e9 < value < 1e9):
                continue
            positive = field in {'L','C','R','f'}
            radius = max(abs(value)*.5, .01 if not positive else 1e-12)
            parameters[name[1]+'.'+field] = {'component': name[1].replace('\\n','\n'), 'field':field,
                'nominal':value,'min':max(1e-15,value-radius) if positive else value-radius,'max':value+radius}
    issues = []
    if not parameters:
        issues.append('模型没有可调节的数值元件参数。')
    if not re.search(r'^    Component \{\n(?:(?!^    \}).)*?^\s+Type\s+Output\b', text, re.M | re.S):
        issues.append('模型缺少顶层 Output 输出端口。')
    match = re.search(r'\bTimeSpan\s+"([^"]+)"',text)
    try:
        duration = float(match[1])
        if not math.isfinite(duration):
            raise ValueError("Non-finite TimeSpan")
    except (ValueError, TypeError):
        duration = None
    if duration is None or duration <= 0:
        issues.append('TimeSpan 必须是有限正数。')
        duration = None
    elif duration > .1:
        issues.append('TimeSpan 超过自动实验支持的 0.1 秒；未截短原时段。可打开模型，但不能直接执行此自动实验。')
    key = hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:12]
    profile = {'path':str(path.resolve()),'description':path.stem,'parameters':parameters,
               'source_sha256':hashlib.sha256(raw).hexdigest(),
               'signals':[], 'duration_s':duration, 'experiment_issues':issues}
    state = settings(owner)
    # Explicit selection replaces the previous circuit for this session.
    state['profiles'] = {key:profile}
    save(owner,state)
    return {'status':'registered','model':key,'description':path.stem,
            'parameters':parameters,'experiment_issues':issues,'note':'输出信号按端口顺序编号；需要核对各通道物理量。'}
