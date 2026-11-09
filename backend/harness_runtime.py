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
from .deployment import agent_capacity, hosted
import secrets
import codecs
import threading
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import httpx
from .agent_policy import BudgetExceeded
from .provider_errors import HarnessTurnError, AgentCapacityError, AgentMemoryError, SessionRestoreError
from .memory_usage import log_memory, low_headroom, memory_readings

scope = contextvars.ContextVar('harness_scope', default=None)
ROOT = Path(os.getenv('AGENT_RUNTIME_DIR') or Path(__file__).resolve().parents[1] / 'runtime') / 'harness'
active = {}
runtime_slots = threading.BoundedSemaphore(agent_capacity())


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
                if self.path != '/mcp' and self.path.rstrip('/') != '/v1/messages':
                    return self.reply(404, {'error':'Unknown route'})
                try:
                    length = int(self.headers.get('Content-Length', '0'))
                except ValueError:
                    return self.reply(400, {'error':'Invalid Content-Length'})
                if not 0 < length <= 16*1024*1024:
                    return self.reply(413, {'error':'Request too large'})
                try:
                    body = json.loads(self.rfile.read(length))
                except (ValueError, UnicodeError):
                    return self.reply(400, {'error':'Invalid JSON'})
                if not isinstance(body, dict):
                    return self.reply(400, {'error':'Request must be an object'})
                if self.path == '/mcp':
                    if (not isinstance(body.get('method'), str)
                            or body.get('params') is not None and not isinstance(body['params'], dict)):
                        return self.reply(400, {'error':'Invalid MCP request'})
                    return self.mcp(body)
                response_started = False
                try:
                    with gateway.lock:
                        # A failed turn is terminal. SDK transport retries must
                        # neither spend again nor hide its original failure.
                        if gateway.error is not None:
                            raise gateway.error
                        if gateway.budget.calls >= gateway.budget.rounds:
                            raise BudgetExceeded('model step budget reached')
                        body['max_tokens'] = gateway.budget.reserve(body)
                        call_id = gateway.budget.calls
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
                            response_started = True
                            buffered = ''
                            usage = {}
                            output_reported = False
                            stopped = False
                            decoder = codecs.getincrementaldecoder('utf-8')()
                            for raw in upstream.iter_bytes():
                                gateway.budget.remaining()
                                buffered += decoder.decode(raw)
                                while '\n' in buffered:
                                    line, buffered = buffered.split('\n',1)
                                    if line.startswith('data:') and line[5:].strip() != '[DONE]':
                                        try:
                                            frame = json.loads(line[5:].lstrip())
                                            if frame.get('type') == 'message_start':
                                                usage.update(frame.get('message', {}).get('usage') or {})
                                            if frame.get('type') == 'message_delta':
                                                update = frame.get('usage') or {}
                                                usage.update(update)
                                                output_reported = output_reported or 'output_tokens' in update
                                            if frame.get('type') == 'message_stop':
                                                stopped = True
                                                if output_reported:
                                                    with gateway.lock:
                                                        gateway.budget.settle(call_id, usage)
                                            delta = frame.get('delta',{}).get('text') if frame.get('type')=='content_block_delta' else None
                                            if delta:
                                                gateway.emit('text_delta', {'text':delta})
                                        except (ValueError,IndexError):
                                            pass
                                # Settle before forwarding message_stop: the SDK
                                # may immediately start the next model request.
                                self.wfile.write(raw)
                                self.wfile.flush()
                            if not stopped:
                                raise HarnessTurnError('incomplete')
                except Exception as exc:
                    with gateway.lock:
                        if gateway.error is None:
                            gateway.error = exc
                        failure = gateway.error
                    status = (failure.response.status_code if isinstance(failure, httpx.HTTPStatusError)
                              else 429 if isinstance(failure, BudgetExceeded) else 502)
                    if response_started:
                        # A second HTTP status line would corrupt the SSE body.
                        # The adapter reports the stored original error.
                        self.close_connection = True
                        return
                    try:
                        self.reply(status, {'error':{'message':'Harness gateway request failed','type':'budget_or_transport'}})
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
                    if gateway.error is not None:
                        value = {'isError':True,'content':[{'type':'text','text':'Model request failed; no further tools may run in this attempt'}]}
                    elif not isinstance(name, str) or name not in {t['function']['name'] for t in gateway.tools}:
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
    # Guard the actual subprocess lifetime, including direct /api/chat calls.
    if not runtime_slots.acquire(blocking=False):
        raise AgentCapacityError('Agent capacity is busy; wait for an active task to finish')
    work = None
    try:
        if not hosted():
            return await _run(messages, tools, invoke, budget)
        next_log = 0
        loop = asyncio.get_running_loop()
        while True:
            readings = memory_readings()
            if low_headroom(readings):
                log_memory('agent_memory_pressure', readings)
                raise AgentMemoryError()
            if loop.time() >= next_log:
                log_memory('agent_running', readings)
                next_log = loop.time() + 15
            if work is None:
                work = asyncio.create_task(_run(messages, tools, invoke, budget))
            done, _ = await asyncio.wait({work}, timeout=1)
            if done:
                return work.result()
    finally:
        try:
            if work is not None and not work.done():
                work.cancel()
                await asyncio.gather(work, return_exceptions=True)
            log_memory('agent_finished')
        finally:
            runtime_slots.release()


async def _run(messages, tools, invoke, budget):
    from deepseek_harness import DeepSeekHarness
    from deepseek_harness.errors import JsonRpcError
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
            api_key=gateway.secret,max_tokens=budget.max_output_tokens,
            reasoning_effort='high' if budget.thinking else 'off',
            initialize_timeout_seconds=30,request_timeout_seconds=30)
        active[task_id] = (harness,identifier)
        try:
            def blocking():
                log_memory('before_harness_start')
                harness.start()
                log_memory('after_harness_start')
                marker = home/'session.started'
                if marker.exists() or state.get('resume'):
                    try:
                        session = harness.resume_session(identifier)
                    except JsonRpcError as exc:
                        if exc.message != f'session "{identifier}" not found':
                            raise SessionRestoreError('unavailable') from exc
                        if state.get('resume') and not state.get('can_restart_session'):
                            raise SessionRestoreError('missing_after_tools') from exc
                        emit('status',{'text':'先前的 Harness 会话未保存，正在用本次请求重新建立会话。'})
                        session = harness.start_session(identifier)
                else:
                    session = harness.start_session(identifier)
                emit('status',{'text':'DeepSeek Harness 已接管工具调度'})
                marker.touch()
                return session.run(prompt)
            result = await asyncio.wait_for(asyncio.to_thread(blocking), timeout=budget.remaining())
            if gateway.error:
                raise gateway.error
            if result.finish_reason not in ('completed',None) or not result.final_response.strip():
                (home/'failure.json').write_text(json.dumps(result.events,ensure_ascii=False),encoding='utf-8')
                raise HarnessTurnError(result.finish_reason if result.finish_reason not in ('completed', None) else 'empty')
            return result.final_response
        except asyncio.TimeoutError:
            # SDK/RPC timeouts are not evidence that our whole-answer deadline
            # expired. Prefer the upstream failure if its retries timed out.
            if gateway.error is not None:
                raise gateway.error
            budget.remaining()  # Raises BudgetExceeded only for an expired deadline.
            raise
        except Exception:
            if gateway.error is not None:
                raise gateway.error
            raise
        finally:
            failure_in_flight = sys.exc_info()[0] is not None
            active.pop(task_id,None)
            with pending_lock:
                stopping.set()
                outstanding = list(pending)
                for future in outstanding:
                    future.cancel()
            try:
                try:
                    await asyncio.to_thread(harness.close)
                finally:
                    path.unlink(missing_ok=True)
            except Exception:
                # Cleanup must not replace an upstream failure or cancellation.
                if not failure_in_flight:
                    raise


def steer(identifier, message):
    current = active.get(identifier)
    if not current:
        raise ValueError('Harness is not active; resume the saved task first')
    harness,session_id = current
    harness.client.session_prompt(session_id,[{'type':'text','text':message}])
