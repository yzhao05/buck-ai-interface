"""Official DeepSeek Harness SDK adapter; no application-owned agent loop.

The minimal official profile calls the numerical backend over MCP. A loopback
provider gateway enforces this application's budgets on *every* model request,
including retries and compaction. Shell/file tools are disabled by profile patch.
"""
import asyncio
import contextvars
import hmac
import json
import os
from pathlib import Path
import secrets
import codecs
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import httpx
from .agent_policy import BudgetExceeded

scope = contextvars.ContextVar('harness_scope', default=None)
ROOT = Path(os.getenv('AGENT_RUNTIME_DIR') or Path(__file__).resolve().parents[1] / 'runtime') / 'harness'
active = {}


class Gateway:
    def __init__(self, tools, invoke, budget, emit):
        self.tools, self.invoke, self.budget, self.emit = tools, invoke, budget, emit
        self.secret = secrets.token_urlsafe(32)
        self.error = None
        self.lock = threading.Lock()
        gateway = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def reply(self, status, value):
                raw = json.dumps(value, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                self.reply(405, {'error':'Use POST'})

            def do_DELETE(self):
                self.reply(200, {})

            def do_POST(self):
                credential = self.headers.get('Authorization','') or 'Bearer '+self.headers.get('x-api-key','')
                if not hmac.compare_digest(credential, 'Bearer '+gateway.secret):
                    return self.reply(401, {'error':'Unauthorized'})
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 16*1024*1024:
                    return self.reply(413, {'error':'Request too large'})
                try:
                    body = json.loads(self.rfile.read(length))
                    if self.path == '/mcp':
                        return self.mcp(body)
                    if self.path.rstrip('/') != '/v1/messages':
                        return self.reply(404, {'error':'Unknown route'})
                    with gateway.lock:
                        if gateway.budget.calls >= gateway.budget.rounds:
                            raise BudgetExceeded('model step budget reached')
                        body['max_tokens'] = gateway.budget.reserve(body)
                    body['thinking'] = {'type':'enabled' if gateway.budget.thinking else 'disabled'}
                    root = os.getenv('HARNESS_BASE_URL') or os.getenv('DEEPSEEK_BASE_URL','https://api.deepseek.com').rstrip('/').removesuffix('/v1')+'/anthropic'
                    endpoint = root.rstrip('/') + ('/messages' if root.rstrip('/').endswith('/v1') else '/v1/messages')
                    with httpx.Client(timeout=gateway.budget.remaining(), trust_env=False) as client:
                        headers = {'x-api-key':os.environ['DEEPSEEK_API_KEY'],'anthropic-version':self.headers.get('anthropic-version','2023-06-01')}
                        if self.headers.get('anthropic-beta'):
                            headers['anthropic-beta'] = self.headers['anthropic-beta']
                        with client.stream('POST', endpoint, json=body, headers=headers) as upstream:
                            upstream.raise_for_status()
                            self.send_response(200)
                            self.send_header('Content-Type', upstream.headers.get('content-type','text/event-stream'))
                            self.send_header('Connection','close')
                            self.end_headers()
                            buffered = ''
                            decoder = codecs.getincrementaldecoder('utf-8')()
                            for raw in upstream.iter_bytes():
                                gateway.budget.remaining()
                                self.wfile.write(raw)
                                self.wfile.flush()
                                buffered += decoder.decode(raw)
                                while '\n' in buffered:
                                    line, buffered = buffered.split('\n',1)
                                    if line.startswith('data: ') and line[6:].strip() != '[DONE]':
                                        try:
                                            frame = json.loads(line[6:])
                                            delta = frame.get('delta',{}).get('text') if frame.get('type')=='content_block_delta' else None
                                            if delta:
                                                gateway.emit('text_delta', {'text':delta})
                                        except (ValueError,IndexError):
                                            pass
                except Exception as exc:
                    gateway.error = exc
                    try:
                        self.reply(429 if isinstance(exc,BudgetExceeded) else 502, {'error':{'message':'Harness gateway request failed','type':'budget_or_transport'}})
                    except OSError:
                        pass

            def mcp(self, body):
                method, params = body.get('method'), body.get('params') or {}
                identifier = body.get('id')
                if identifier is None:
                    self.send_response(202); self.end_headers(); return
                if method == 'initialize':
                    value = {'protocolVersion':'2025-03-26','capabilities':{'tools':{}},'serverInfo':{'name':'buck-lab','version':'1'}}
                elif method == 'tools/list':
                    value = {'tools':[{'name':t['function']['name'],'description':t['function']['description'],
                                      'inputSchema':t['function']['parameters']} for t in gateway.tools]}
                elif method == 'tools/call':
                    name, arguments = params.get('name'), params.get('arguments') or {}
                    if name not in {t['function']['name'] for t in gateway.tools}:
                        value = {'isError':True,'content':[{'type':'text','text':'Tool not admitted'}]}
                    else:
                        try:
                            gateway.budget.remaining()
                            output = gateway.invoke(name, arguments)
                            value = {'isError':bool(isinstance(output,dict) and output.get('error')),
                                     'content':[{'type':'text','text':json.dumps(output,ensure_ascii=False,allow_nan=False)}]}
                        except Exception as exc:
                            value = {'isError':True,'content':[{'type':'text','text':str(exc)}]}
                elif method in {'ping','resources/list','resources/templates/list'}:
                    value = {'resources':[]} if method=='resources/list' else {'resourceTemplates':[]} if method=='resources/templates/list' else {}
                else:
                    return self.reply(200, {'jsonrpc':'2.0','id':identifier,'error':{'code':-32601,'message':'Method not found'}})
                self.reply(200, {'jsonrpc':'2.0','id':identifier,'result':value})
        self.server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.server.daemon_threads = True
        self.url = 'http://127.0.0.1:'+str(self.server.server_port)

    def __enter__(self):
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


async def run(messages, tools, invoke, budget):
    from deepseek_harness import DeepSeekHarness
    state = scope.get() or {}
    identifier = state.get('session_id') or state.get('id') or secrets.token_hex(16)
    task_id = state.get('id') or identifier
    home = ROOT / identifier
    home.mkdir(parents=True, exist_ok=True)
    emit = state.get('emit', lambda *_:None)
    state['remaining'] = budget.remaining
    context = contextvars.copy_context()
    loop = asyncio.get_running_loop()
    pending = set()
    pending_lock = threading.Lock()
    stopping = threading.Event()
    def call(name, args):
        with pending_lock:
            if stopping.is_set():
                raise ValueError('Agent task has stopped; no further tools may run')
            future = context.copy().run(asyncio.run_coroutine_threadsafe,invoke(name,args),loop)
            pending.add(future)
        try:
            return future.result(timeout=budget.remaining())
        except BaseException:
            future.cancel()
            raise
        finally:
            with pending_lock:
                pending.discard(future)
    with Gateway(tools,call,budget,emit) as gateway:
        # JSON is a YAML subset. Patches target the pinned official minimal profile.
        patches = [{'id':name,'disabled':True} for name in ('persistent-bash','persistent-pwsh','tool-fs','tool-working-directory','llm-retry','mcp-resources')]
        patches.append({'insert':[{'id':'buck-tools','name':'@deepseek-ai/dsh-mcp-client',
            'config':{'serverName':'buck','transport':'streamable-http','url':gateway.url+'/mcp',
                      'headers':{'Authorization':'Bearer '+gateway.secret},'toolCallTimeoutMs':int(budget.seconds*1000),
                      'startupTimeoutMs':15000,'failOnStartupError':True}}]})
        system = '\n'.join(m['content'] for m in messages if m['role']=='system')
        patches.append({'id':'system-prompt','config':{'includeHarnessIdentity':False,'includeRuntimeContext':False,
            'personaPrefix':system+'\n工具以 mcp__buck__ 前缀提供。用户输入在 JSON 消息列表中。'}})
        path = home/'buck.patch.yml'
        path.write_text(json.dumps(patches),encoding='utf-8')
        # Supply prior chat as data; the official runtime owns subsequent turns.
        history, images = [], []
        for message in messages:
            if message['role']=='system':
                continue
            content = message['content']
            if isinstance(content,list):
                text = []
                for block in content:
                    if block['type']=='text':
                        text.append(block['text'])
                    elif block['type']=='image_url':
                        header,data = block['image_url']['url'].split(',',1)
                        images.append({'type':'image','data':data,'mimeType':header[5:].split(';')[0]})
                content = '\n'.join(text)
            history.append({'role':message['role'],'content':content})
        prompt = [{'type':'text','text':json.dumps(history,ensure_ascii=False)},*images]
        # Windows runtime 0.2.1rc2 resolves its packaged Koffi module against
        # the process drive (upstream #11313). Launch on the system drive;
        # the SDK's logical cwd and all durable data stay in this session home.
        runtime_cwd = os.environ.get('SystemDrive', 'C:') + '\\' if os.name == 'nt' else str(home)
        harness = DeepSeekHarness(dsh_home=str(home),cwd=str(home),runtime_cwd=runtime_cwd,
            profile='sdk-minimal',patches=(str(path),),
            model=os.getenv('DEEPSEEK_MODEL','deepseek-flash'),base_url=gateway.url,
            api_key=gateway.secret,max_tokens=8000 if budget.thinking else 3000,
            reasoning_effort='high' if budget.thinking else 'off',
            initialize_timeout_seconds=30,request_timeout_seconds=30)
        active[task_id] = (harness,identifier)
        try:
            def blocking():
                harness.start()
                session = harness.resume_session(identifier) if (home/'session.started').exists() else harness.start_session(identifier)
                emit('status',{'text':'DeepSeek Harness 已接管工具调度'})
                (home/'session.started').touch()
                return session.run(prompt)
            result = await asyncio.wait_for(asyncio.to_thread(blocking), timeout=budget.remaining())
            if gateway.error:
                raise gateway.error
            if result.finish_reason not in ('completed',None) or not result.final_response.strip():
                (home/'failure.json').write_text(json.dumps(result.events,ensure_ascii=False),encoding='utf-8')
                raise ValueError('Harness did not complete the turn')
            return result.final_response
        except asyncio.TimeoutError:
            raise BudgetExceeded('time budget reached')
        finally:
            active.pop(task_id,None)
            with pending_lock:
                stopping.set()
                outstanding = list(pending)
                for future in outstanding:
                    future.cancel()
            try:
                await asyncio.to_thread(harness.close)
            finally:
                # Cleanup also runs if shutdown fails.
                path.unlink(missing_ok=True)


def steer(identifier, message):
    current = active.get(identifier)
    if not current:
        raise ValueError('Harness is not active; resume the saved task first')
    harness,session_id = current
    harness.client.session_prompt(session_id,[{'type':'text','text':message}])
