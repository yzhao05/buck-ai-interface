"""Durable application task envelopes around official Harness sessions."""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from . import harness_runtime
from .schemas import ChatRequest

ROOT = Path(os.getenv('AGENT_RUNTIME_DIR') or Path(__file__).resolve().parents[1] / 'runtime') / 'agent_tasks'
running = {}
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


def read(owner,identifier,after=0, *, include_binding=True):
    with database() as db:
        columns = 'state,result,request' if include_binding else 'state,result'
        db.execute('BEGIN')
        row = db.execute(f'SELECT {columns} FROM tasks WHERE id=? AND owner=?',(identifier,owner)).fetchone()
        if not row:
            raise ValueError('Task not found')
        state = row['state']
        events = db.execute('SELECT * FROM events WHERE task=? AND seq>? ORDER BY seq LIMIT 200',(identifier,after)).fetchall()
    request = json.loads(row['request']) if include_binding else {}
    return {'id':identifier,'state':state,'result':json.loads(row['result']) if row['result'] else None,
            'analysis_result_id':request.get('result_id') if request.get('current_result_analysis') else None,
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
    if len(running)>=int(os.getenv('AGENT_MAX_ACTIVE_TASKS','1' if os.getenv('RENDER') else '2')):
        raise ValueError('Agent capacity is busy; wait for an active task to finish')
    identifier = identifier or uuid.uuid4().hex
    with database() as db:
        if resume:
            row = db.execute('SELECT request,state FROM tasks WHERE owner=? AND id=?',(owner,identifier)).fetchone()
            if not row:
                raise ValueError('Task not found')
            if row['state'] not in {'interrupted','failed','cancelled'}:
                raise ValueError('Only interrupted tasks may be resumed')
            body = ChatRequest.model_validate_json(row['request'])
            db.execute("UPDATE tasks SET state='running',result=NULL WHERE id=?",(identifier,))
        else:
            db.execute('INSERT INTO tasks VALUES (?,?,?,?,?,?)',(identifier,owner,'running',body.model_dump_json(),None,time.time()))
        db.execute('INSERT INTO events(task,kind,data) VALUES (?,?,?)',
                   (identifier,'attempt_start',json.dumps({'text':'正在恢复 Agent 任务' if resume else '正在准备 Agent 任务'},ensure_ascii=False)))
    async def work():
        token = harness_runtime.scope.set({'id':identifier,'owner':owner,'resume':resume,
                                           'emit':lambda kind,data:event(identifier,kind,data)})
        try:
            result = await runner(body)
            finish(identifier, 'failed' if result.get('mode')=='provider_error' else 'completed', result)
        except asyncio.CancelledError:
            finish(identifier,'cancelled')
        except Exception:
            from .provider_errors import provider_failure
            result = provider_failure()
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
