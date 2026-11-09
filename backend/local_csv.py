"""Read CSV files only from the current owner's configured local folder."""
import hashlib
import os
import stat
import time
from contextlib import contextmanager
from pathlib import Path

from . import plecs_local
from .data import MAX_BYTES

TOOL = {'type':'function','function':{'name':'search_local_csv',
    'description':'列出配置目录及子目录中的 CSV 名称、相对路径和文件 ID，不读取内容。省略过滤条件可浏览目录；query 匹配连续的文件名片段或相对路径；terms 匹配相对路径中全部片段，顺序不限。由你决定查询、翻页、比较候选和选择文件。导入用 configure_identification 的 assets.csv_id。',
    'parameters':{'type':'object','properties':{'query':{'type':'string','maxLength':255},
                  'terms':{'type':'array','maxItems':12,'items':{'type':'string','minLength':1,'maxLength':255}},
                  'offset':{'type':'integer','minimum':0}},'additionalProperties':False}}}


def root_for(owner):
    folder = plecs_local.settings(owner).get('model_directory')
    if not folder or not Path(folder).is_dir():
        raise ValueError('请在连接设置中保存包含实验 CSV 的本地文件夹。')
    return Path(folder).resolve(strict=True)


def file_id(owner, root, relative, info):
    identity = (owner,str(root),relative,info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns)
    return 'local-csv:' + hashlib.sha256(repr(identity).encode()).hexdigest()


def search(owner, query='', offset=0, terms=None):
    if not isinstance(query,str) or len(query)>255 or type(offset) is not int or offset<0:
        raise ValueError('Invalid CSV query or offset')
    root = root_for(owner)
    def normalize(name):
        return ''.join(c for c in name.casefold() if c.isalnum())
    if terms is None:
        terms = []
    if not isinstance(terms,list) or len(terms)>12 or any(not isinstance(t,str) or not 0<len(t)<=255 for t in terms):
        raise ValueError('Invalid CSV search terms')
    fragments = [normalize(term) for term in terms]
    needle = normalize(query.replace('\\','/').casefold().removesuffix('.csv'))
    matches, seen, scanned = [], 0, 0
    errors = []
    started = time.monotonic()
    for directory, dirs, files in os.walk(root, onerror=errors.append):
        dirs[:] = sorted(d for d in dirs if not Path(directory,d).is_symlink() and not Path(directory,d).is_junction())
        scanned += len(files) + len(dirs)
        if scanned > 50000 or time.monotonic()-started > 5:
            errors.append('scan limit')
            break
        for name in sorted(files):
            path = Path(directory,name)
            relative = path.relative_to(root).as_posix()
            target = relative if '/' in query or '\\' in query else name
            if path.suffix.casefold() != '.csv' or needle not in normalize(target) or path.is_symlink():
                continue
            if fragments:
                normalized_path = normalize(relative)
                if not all(fragment in normalized_path for fragment in fragments):
                    continue
            try:
                info = path.stat()
            except OSError as exc:
                errors.append(exc)
                continue
            if not stat.S_ISREG(info.st_mode):
                continue
            if offset <= seen < offset+30:
                matches.append({'id':file_id(owner,root,relative,info),'name':name,'relative_path':relative})
            seen += 1
    return {'files':matches, 'match_count':seen, 'next_offset':offset+30 if offset+30<seen else None,
            'search_complete':not errors}


@contextmanager
def selected_file(owner, relative, identifier):
    root = root_for(owner)
    path = Path(relative)
    if path.is_absolute() or path.drive or ':' in relative or '..' in path.parts or path.suffix.casefold() != '.csv':
        raise ValueError('Choose a CSV from the configured folder')
    resolved = (root/path).resolve(strict=True)
    if not resolved.is_relative_to(root) or resolved != root/path or not resolved.is_file():
        raise ValueError('Choose a regular CSV inside the configured folder')
    with resolved.open('rb') as stream:
        def check():
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or file_id(owner,root,path.as_posix(),info) != identifier:
                raise ValueError('CSV or configured folder changed; search again')
            if info.st_size > MAX_BYTES:
                raise ValueError('CSV exceeds 32 MB')
        check()
        yield stream
        check()


def read(owner, relative, identifier):
    with selected_file(owner,relative,identifier) as stream:
        raw = stream.read(MAX_BYTES+1)
    if len(raw)>MAX_BYTES:
        raise ValueError('CSV exceeds 32 MB')
    return raw


def verify_selection(owner, relative_path, id):
    """Confirm readability now; only the receipt is returned to the Agent."""
    digest = hashlib.sha256()
    size = 0
    with selected_file(owner,relative_path,id) as stream:
        while chunk := stream.read(64*1024):
            size += len(chunk)
            if size > MAX_BYTES:
                raise ValueError('CSV exceeds 32 MB')
            digest.update(chunk)
    if not size:
        raise ValueError('CSV is empty')
    return {'bytes':size, 'sha256':digest.hexdigest()}
