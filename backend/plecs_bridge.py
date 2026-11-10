"""Outbound, paired PLECS work queue. Claimed work is never blindly retried."""
import asyncio
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager

ROOT = Path(os.getenv('AGENT_RUNTIME_DIR') or Path(__file__).resolve().parents[1] / 'runtime') / 'bridge'
TOOLS = {'check_plecs_connection', 'connect_plecs', 'search_plecs_models', 'select_plecs_model',
         'get_plecs_models', 'open_plecs_model', 'run_plecs_experiment'}


@contextmanager
def database():
    ROOT.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(ROOT / 'queue.sqlite', timeout=15)
    try:
        db.row_factory = sqlite3.Row
        db.executescript('''
            CREATE TABLE IF NOT EXISTS peers(owner TEXT PRIMARY KEY, secret TEXT, seen REAL, settings TEXT);
            CREATE TABLE IF NOT EXISTS work(id TEXT PRIMARY KEY, owner TEXT, fingerprint TEXT,
              state TEXT, payload TEXT, result TEXT, created REAL, deadline REAL);
            CREATE UNIQUE INDEX IF NOT EXISTS work_dedupe ON work(owner, fingerprint);
        ''')
        with db:
            yield db
    finally:
        db.close()


def pair(owner, configuration=None):
    token = secrets.token_urlsafe(32)
    with database() as db:
        db.execute('BEGIN IMMEDIATE')
        previous = db.execute('SELECT settings FROM peers WHERE owner=?', (owner,)).fetchone()
        db.execute('INSERT OR REPLACE INTO peers VALUES (?,?,?,?)',
                   (owner, hashlib.sha256(token.encode()).hexdigest(), 0,
                    json.dumps(configuration) if configuration is not None else previous['settings'] if previous else '{}'))
        db.execute("UPDATE work SET state='cancelled' WHERE owner=? AND state IN ('queued','running','uncertain')", (owner,))
    return {'owner': owner, 'token': token}


class InvalidCredential(ValueError):
    pass


# Wire values also used by companions talking to older Buck Lab deployments.
RESULT_TOO_LARGE = 'Bridge result exceeds 8 MiB'
UNKNOWN_JOB = 'Unknown bridge job'
CONFLICTING_RESULT = 'Conflicting duplicate result'
JOB_CLOSED = 'Bridge job no longer accepts results; do not repeat the experiment'
RESULT_REJECTIONS = (RESULT_TOO_LARGE, UNKNOWN_JOB, CONFLICTING_RESULT, JOB_CLOSED)


def authorized_write(db, owner, token):
    # Serialize credential rotation with claiming, heartbeats and completion.
    db.execute('BEGIN IMMEDIATE')
    row = db.execute('SELECT secret FROM peers WHERE owner=?', (owner,)).fetchone()
    if not row or not hmac.compare_digest(row['secret'], hashlib.sha256(token.encode()).hexdigest()):
        raise InvalidCredential('Invalid bridge credential')


def heartbeat(owner, token):
    with database() as db:
        authorized_write(db,owner,token)
        db.execute('UPDATE peers SET seen=? WHERE owner=?',(time.time(),owner))
    return {'status':'online'}


def status(owner):
    with database() as db:
        row = db.execute('SELECT seen FROM peers WHERE owner=?', (owner,)).fetchone()
    return {'paired': bool(row), 'online': bool(row and time.time()-row['seen'] < 30)}


def settings(owner, value=None):
    with database() as db:
        row = db.execute('SELECT settings FROM peers WHERE owner=?', (owner,)).fetchone()
        if not row:
            raise ValueError('请先生成本机连接器配对码。')
        if value is not None:
            db.execute('UPDATE peers SET settings=? WHERE owner=?', (json.dumps(value), owner))
        return value if value is not None else json.loads(row['settings'])


def claim(owner, token):
    now = time.time()
    with database() as db:
        authorized_write(db,owner,token)
        db.execute('UPDATE peers SET seen=? WHERE owner=?', (now, owner))
        db.execute("UPDATE work SET state='expired' WHERE owner=? AND deadline<? AND state='queued'", (owner, now))
        db.execute("UPDATE work SET state='uncertain' WHERE owner=? AND deadline<? AND state='running'", (owner, now))
        row = db.execute("SELECT * FROM work WHERE owner=? AND state='queued' ORDER BY created LIMIT 1", (owner,)).fetchone()
        if not row:
            return {'job': None}
        db.execute("UPDATE work SET state='running' WHERE id=?", (row['id'],))
        return {'job': {'id': row['id'], 'payload': json.loads(row['payload']), 'deadline': row['deadline']}}


def complete(owner, token, identifier, result):
    with database() as db:
        authorized_write(db,owner,token)
        encoded = json.dumps(result, allow_nan=False)
        if len(encoded.encode()) > 8*1024*1024:
            raise ValueError(RESULT_TOO_LARGE)
        row = db.execute('SELECT * FROM work WHERE id=? AND owner=?', (identifier, owner)).fetchone()
        if not row:
            raise ValueError(UNKNOWN_JOB)
        if row['state'] == 'completed':
            if row['result'] != encoded:
                raise ValueError(CONFLICTING_RESULT)
            return {'status': 'completed'}
        if row['state'] not in ('running','uncertain') or time.time()-row['created']>86400:
            raise ValueError(JOB_CLOSED)
        db.execute("UPDATE work SET state='completed',result=? WHERE id=?", (encoded, identifier))
    return {'status': 'completed'}


def result(owner, identifier, context):
    with database() as db:
        row = db.execute('SELECT * FROM work WHERE owner=? AND id=?',(owner,identifier)).fetchone()
    if not row:
        raise ValueError(UNKNOWN_JOB)
    payload = json.loads(row['payload'])
    if payload['context'] != context or payload['tool'] != 'run_plecs_experiment':
        raise ValueError('Bridge result does not match the current circuit/data context')
    return {'status':row['state'],'bridge_job_id':identifier,
            'result':json.loads(row['result']) if row['result'] else None,'payload':payload}


async def dispatch(owner, payload, timeout=150):
    if payload.get('tool') not in TOOLS:
        raise ValueError('Unsupported bridge operation')
    if not status(owner)['online']:
        return {'status': 'unavailable', 'error_code':'bridge_offline', 'error': '本机 PLECS 连接器离线。请启动连接器并保持运行。'}
    identifier = uuid.uuid4().hex
    # Request ID is supplied by the durable Harness tool receipt, never the model.
    fingerprint = payload.pop('request_id', identifier)
    with database() as db:
        db.execute('INSERT OR IGNORE INTO work VALUES (?,?,?,?,?,?,?,?)',
                   (identifier, owner, fingerprint, 'queued', json.dumps(payload), None, time.time(), time.time()+timeout))
        identifier = db.execute('SELECT id FROM work WHERE owner=? AND fingerprint=?', (owner, fingerprint)).fetchone()['id']
    try:
        while True:
            with database() as db:
                row = db.execute('SELECT * FROM work WHERE id=?', (identifier,)).fetchone()
            if row['state'] == 'completed':
                return json.loads(row['result'])
            if row['state'] not in ('queued', 'running') or row['deadline'] < time.time():
                return {'status': 'unavailable', 'error': '连接器任务已中断或超时；结果未知，未自动重跑。', 'bridge_job_id': identifier}
            await asyncio.sleep(.3)
    except asyncio.CancelledError:
        # Finish only the durable receipt. The Harness turn remains cancelled;
        # a later read can collect the result without repeating the simulation.
        return {'status':'uncertain','error':'Task interrupted; retrieve the bridge job result later without rerunning.', 'bridge_job_id':identifier}
    finally:
        with database() as db:
            db.execute("UPDATE work SET state='expired' WHERE id=? AND state='queued'", (identifier,))
            db.execute("UPDATE work SET state='uncertain' WHERE id=? AND state='running'", (identifier,))
