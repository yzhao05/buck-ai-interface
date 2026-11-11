"""Durable application task envelopes around official Harness sessions."""
import asyncio
import json
import logging
import os
from pathlib import Path
from .deployment import agent_capacity
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from . import harness_runtime
from .schemas import ChatRequest

ROOT = Path(os.getenv('AGENT_RUNTIME_DIR') or Path(__file__).resolve().parents[1] / 'runtime') / 'agent_tasks'
running = {}
# These tools only read existing evidence. Unknown tools require the original session.
REPLAYABLE_READS = frozenset({
    'read_experiment_results', 'get_experiment_history', 'get_analysis_history', 'get_identification_result',
    'get_waveform_summary', 'get_diagnostic_evidence', 'search_knowledge', 'list_knowledge', 'read_knowledge', 'search_conversation', 'search_local_csv',
    'read_conversation_message', 'read_diagnosis', 'check_plecs_connection', 'search_plecs_models', 'get_plecs_models',
})
_initialization_lock = threading.Lock()
_initialized_database = None


@contextmanager
def database():
    global _initialized_database
    ROOT.mkdir(parents=True,exist_ok=True)
    path = ROOT/'tasks.sqlite'
    db = sqlite3.connect(path,timeout=15)
    try:
        db.row_factory = sqlite3.Row
        with _initialization_lock:
            if _initialized_database != path:
                db.executescript('''CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,owner TEXT,state TEXT,request TEXT,result TEXT,created REAL);
                    CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,task TEXT,kind TEXT,data TEXT);
                    CREATE INDEX IF NOT EXISTS task_event_cursor ON events(task,seq);
                    CREATE INDEX IF NOT EXISTS task_attempt_cursor ON events(task,seq) WHERE kind='attempt_start';''')
                # The service uses one worker. Only tasks left running by the
                # previous process are interrupted, before admitting any new task.
                with db:
                    db.execute("UPDATE tasks SET state='interrupted' WHERE state='running'")
                _initialized_database = path
        with db:
            yield db
    finally:
        db.close()


def event(identifier,kind,data):
    with database() as db:
        db.execute('INSERT INTO events(task,kind,data) VALUES (?,?,?)',(identifier,kind,json.dumps(data,ensure_ascii=False)))


def finish(identifier, state, result=None):
    cancellation = {'text':'任务已停止；正在执行的本机仿真可能仍在收尾，未安排重跑。'}
    encoded = json.dumps(result,ensure_ascii=False) if result is not None else None
    with database() as db:
        db.execute('UPDATE tasks SET state=?,result=? WHERE id=?',(state,encoded,identifier))
        db.execute('INSERT INTO events(task,kind,data) VALUES (?,?,?)',
                   (identifier,'result' if encoded else 'status',encoded or json.dumps(cancellation,ensure_ascii=False)))
    logging.getLogger('uvicorn.error').info('Agent task finished: id=%s state=%s error_code=%s',
                                            identifier, state, (result or {}).get('error_code') or 'none')


def read(owner,identifier,after=0, *, include_binding=True):
    with database() as db:
        columns = 'state,result'
        if include_binding:
            columns += ", CASE WHEN json_extract(request,'$.current_result_analysis') THEN json_extract(request,'$.result_id') END AS analysis_result_id"
        db.execute('BEGIN')
        row = db.execute(f'SELECT {columns} FROM tasks WHERE id=? AND owner=?',(identifier,owner)).fetchone()
        if not row:
            raise ValueError('Task not found')
        state = row['state']
        events = db.execute('SELECT * FROM events WHERE task=? AND seq>? ORDER BY seq LIMIT 200',(identifier,after)).fetchall()
    return {'id':identifier,'state':state,'result':json.loads(row['result']) if row['result'] else None,
            'analysis_result_id':row['analysis_result_id'] if include_binding else None,
            'events':[{'seq':r['seq'],'kind':r['kind'],'data':json.loads(r['data'])} for r in events]}


def latest(owner):
    with database() as db:
        row = db.execute("""SELECT id FROM tasks WHERE owner=?
            ORDER BY CASE WHEN state='running' THEN 0 ELSE 1 END,
            (SELECT MAX(seq) FROM events WHERE task=tasks.id AND kind='attempt_start') DESC,
            created DESC LIMIT 1""",(owner,)).fetchone()
    return read(owner,row['id']) if row else None


def launch(owner,body,runner,identifier=None,resume=False):
    # A resumed older task is not necessarily the most recently created row.
    with database() as db:
        active = db.execute("SELECT 1 FROM tasks WHERE owner=? AND state='running' LIMIT 1",(owner,)).fetchone()
    if active:
        raise ValueError('This conversation already has an active Agent task')
    if len(running)>=agent_capacity():
        raise ValueError('Agent capacity is busy; wait for an active task to finish')
    identifier = identifier or uuid.uuid4().hex
    can_restart_session = True
    with database() as db:
        if resume:
            row = db.execute('SELECT request,state FROM tasks WHERE owner=? AND id=?',(owner,identifier)).fetchone()
            if not row:
                raise ValueError('Task not found')
            if row['state'] not in {'interrupted','failed','cancelled'}:
                raise ValueError('Only interrupted tasks may be resumed')
            body = ChatRequest.model_validate_json(row['request'])
            attempts = db.execute("SELECT data FROM events WHERE task=? AND kind='tool_start'",(identifier,))
            can_restart_session = all(json.loads(row['data']).get('name') in REPLAYABLE_READS for row in attempts)
            db.execute("UPDATE tasks SET state='running',result=NULL WHERE id=?",(identifier,))
        else:
            db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?)',(identifier,owner,'running',body.model_dump_json(),None,time.time()))
        db.execute('INSERT INTO events(task,kind,data) VALUES (?,?,?)',
                   (identifier,'attempt_start',json.dumps({'text':'正在恢复 Agent 任务' if resume else '正在准备 Agent 任务'},ensure_ascii=False)))
    async def work():
        logging.getLogger('uvicorn.error').info('Agent task started: id=%s resume=%s', identifier, resume)
        token = harness_runtime.scope.set({'id':identifier,'owner':owner,'resume':resume,'can_restart_session':can_restart_session,
                                           'emit':lambda kind,data:event(identifier,kind,data)})
        try:
            result = await runner(body)
            finish(identifier, 'failed' if result.get('mode')=='provider_error' else 'completed', result)
        except asyncio.CancelledError:
            finish(identifier,'cancelled')
        except Exception as exc:
            from .provider_errors import provider_failure
            result = provider_failure(exc)
            finish(identifier,'failed',result)
        finally:
            harness_runtime.scope.reset(token)
            running.pop(identifier,None)
    task = asyncio.create_task(work())
    running[identifier] = task
    def finished(completed):
        # Cancellation can happen before work() enters its try/finally.
        if running.get(identifier) is completed:
            running.pop(identifier,None)
            if completed.cancelled():
                finish(identifier,'cancelled')
    task.add_done_callback(finished)
    return {'id':identifier,'state':'running'}


def cancel(owner,identifier):
    read(owner,identifier)
    task = running.get(identifier)
    if task:
        task.cancel()
    return {'status':'cancelling' if task else 'inactive'}
