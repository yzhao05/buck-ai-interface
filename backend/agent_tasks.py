"""Durable application task envelopes around official Harness sessions."""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid
from . import harness_runtime
from .schemas import ChatRequest

ROOT = Path(os.getenv('AGENT_RUNTIME_DIR') or Path(__file__).resolve().parents[1] / 'runtime') / 'agent_tasks'
running = {}


def database():
    ROOT.mkdir(parents=True,exist_ok=True)
    db = sqlite3.connect(ROOT/'tasks.sqlite',timeout=15)
    db.row_factory = sqlite3.Row
    db.executescript('''CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY,owner TEXT,state TEXT,request TEXT,result TEXT,created REAL);
        CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,task TEXT,kind TEXT,data TEXT);''')
    return db


def event(identifier,kind,data):
    with database() as db:
        db.execute('INSERT INTO events(task,kind,data) VALUES (?,?,?)',(identifier,kind,json.dumps(data,ensure_ascii=False)))


def read(owner,identifier,after=0):
    with database() as db:
        row = db.execute('SELECT * FROM tasks WHERE id=? AND owner=?',(identifier,owner)).fetchone()
        if not row:
            raise ValueError('Task not found')
        state = row['state']
        if state == 'running' and identifier not in running:
            state = 'interrupted'
            db.execute("UPDATE tasks SET state='interrupted' WHERE id=?",(identifier,))
        events = db.execute('SELECT * FROM events WHERE task=? AND seq>? ORDER BY seq LIMIT 200',(identifier,after)).fetchall()
    return {'id':identifier,'state':state,'result':json.loads(row['result']) if row['result'] else None,
            'events':[{'seq':r['seq'],'kind':r['kind'],'data':json.loads(r['data'])} for r in events]}


def latest(owner):
    with database() as db:
        row = db.execute('SELECT id FROM tasks WHERE owner=? ORDER BY created DESC LIMIT 1',(owner,)).fetchone()
    return read(owner,row['id']) if row else None


def launch(owner,body,runner,identifier=None,resume=False):
    # A resumed older task is not necessarily the most recently created row.
    with database() as db:
        candidates = db.execute("SELECT id FROM tasks WHERE owner=? AND state='running'",(owner,)).fetchall()
    if any(read(owner,row['id'])['state']=='running' for row in candidates):
        raise ValueError('This conversation already has an active Agent task')
    if len(running)>=int(os.getenv('AGENT_MAX_ACTIVE_TASKS','2')):
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
    async def work():
        token = harness_runtime.scope.set({'id':identifier,'owner':owner,'resume':resume,
                                           'emit':lambda kind,data:event(identifier,kind,data)})
        try:
            event(identifier,'attempt_start',{'text':'正在恢复 Agent 任务' if resume else '正在准备 Agent 任务'})
            result = await runner(body)
            with database() as db:
                db.execute('UPDATE tasks SET state=?,result=? WHERE id=?',
                           ('failed' if result.get('mode')=='provider_error' else 'completed',json.dumps(result,ensure_ascii=False),identifier))
            event(identifier,'result',result)
        except asyncio.CancelledError:
            with database() as db:
                db.execute("UPDATE tasks SET state='cancelled' WHERE id=?",(identifier,))
            event(identifier,'status',{'text':'任务已停止；正在执行的本机仿真可能仍在收尾，未安排重跑。'})
        except Exception:
            from .provider_errors import provider_failure
            result = provider_failure()
            with database() as db:
                db.execute("UPDATE tasks SET state='failed',result=? WHERE id=?",(json.dumps(result),identifier))
            event(identifier,'result',result)
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
                with database() as db:
                    db.execute("UPDATE tasks SET state='cancelled' WHERE id=? AND state='running'",(identifier,))
    task.add_done_callback(finished)
    return {'id':identifier,'state':'running'}


def cancel(owner,identifier):
    read(owner,identifier)
    task = running.get(identifier)
    if task:
        task.cancel()
    return {'status':'cancelling' if task else 'inactive'}
