/* Durable official-Harness tasks. Streaming is text only, never inner reasoning. */
"use strict";
window.BuckHarness = (()=>{
  const key=()=>`buck-agent:${base}:${sessionId}`;
  const savedTask=()=>{try{return localStorage.getItem(key());}catch{return null;}};
  function rememberTask(id,storageKey=key()){
    try{if(id)localStorage.setItem(storageKey,id);else localStorage.removeItem(storageKey);}
    catch{try{localStorage.removeItem(storageKey);}catch{/* Backend latest-task lookup remains available. */}}
  }
  const status=(...parts)=>document.getElementById('agent-status').replaceChildren(...parts.map(window.BuckI18n.node));
  function replyText(response, text) {
    return [text, response?.plecs_manual_help].filter(Boolean).join('\n\n');
  }
  function showOutcome(text,label){
    status(label);
    window.BuckMarkdown.render(document.getElementById('agent-preview'),text);
  }
  const headers=()=>({'X-Session-ID':sessionId,...(accessToken?{[appTokenHeader()]:`Bearer ${accessToken}`}:{})});
  function taskError(taskState,id){
    return Object.assign(new Error(taskState),{taskState,agentTaskId:id});
  }
  function setActiveTask(id){
    state.agentTask=id;
    document.getElementById('agent-steer').disabled=!id;
    document.getElementById('agent-cancel').disabled=!id;
  }
  function taskNotice(error){
    const text=({cancelled:terminatedText,interrupted:'任务已中断，可恢复。',disconnected:'连接中断，后台任务可能仍在运行。请点击“找回 / 恢复任务”重新连接。'})[error.taskState];
    return text?{text:tr(text),response:{agent_task_id:error.agentTaskId,task_state:error.taskState}}:null;
  }
  const terminatedText='任务已终止，可以发送新任务。';
  let activeStream=null;
  function showTerminated(){
    setActiveTask(null);
    rememberTask(null);
    document.getElementById('agent-instruction').value='';
    showOutcome('',terminatedText);
  }
  function finishTask(id,terminal,storageKey){
    if(terminal.state==='cancelled'){
      showTerminated();
      throw taskError('cancelled',id);
    }
    setActiveTask(null);
    const result=terminal.result;
    status(({completed:'任务已完成',failed:'任务未完成，可查看已保存证据',interrupted:'任务已中断，可恢复'})[terminal.state]||terminal.state);
    window.BuckMarkdown.render(document.getElementById('agent-preview'),result?.ui_action?'':replyText(result,result?.answer||result?.provider_error));
    if(result?.ui_action)status('等待界面应用操作');
    if(terminal.state==='completed')rememberTask(null,storageKey);
    if(terminal.state==='interrupted')throw taskError('interrupted',id);
    if(!result)throw taskError('failed',id);
    return {...result,agent_task_id:id};
  }
  async function follow(id){
    const controller=new AbortController();
    const storageKey=key();
    activeStream=controller;
    try{return await followEvents(id,controller.signal,storageKey);}
    catch(error){
      if(controller.signal.aborted)return finishTask(id,controller.signal.reason,storageKey);
      if(error.taskState)throw error;
      throw taskError('disconnected',id);
    }finally{if(activeStream===controller)activeStream=null;}
  }
  async function followEvents(id,signal,storageKey){
    setActiveTask(id);
    document.getElementById('agent-progress').hidden=false;
    document.getElementById('agent-progress').open=true;
    document.getElementById('agent-preview').textContent='';
    status('正在准备 Agent 任务');
    let buffer='',preview='';
    const response=await fetch(base.replace(/\/$/,'')+`/api/agent/tasks/${id}/events`,{headers:headers(),signal});
    if(!response.ok)throw new Error('Cannot reconnect to the Agent task.');
    const reader=response.body.getReader(),decoder=new TextDecoder();
    try{while(true){
      const {value,done}=await reader.read();
      signal.throwIfAborted();
      if(done)break;
      buffer+=decoder.decode(value,{stream:true});
      let newline;
      while((newline=buffer.indexOf('\n'))>=0){
        const line=buffer.slice(0,newline);buffer=buffer.slice(newline+1);if(!line)continue;
        const item=JSON.parse(line);
        if(item.kind==='attempt_start'){preview='';document.getElementById('agent-preview').textContent='';status(item.data.text);}
        if(item.kind==='status')status(item.data.text);
        if(item.kind==='tool_start')status('正在执行：',tools[item.data.name]||item.data.name);
        if(item.kind==='tool_end')status(tools[item.data.name]||item.data.name,' · ',item.data.status);
        if(item.kind==='text_delta'){
          preview+=item.data.text;
          window.BuckMarkdown.render(document.getElementById('agent-preview'),preview,{streaming:true});
        }
        if(item.kind==='end'){
          return finishTask(id,item.data,storageKey);
        }
      }
    }}finally{await reader.cancel().catch(()=>{});reader.releaseLock();}
    throw new Error('Connection interrupted. The Agent task remains on the backend; reconnect to retrieve it.');
  }
  async function request(body){
    const task=await api('/api/agent/tasks',{method:'POST',body,timeout:null});
    rememberTask(task.id);
    return follow(task.id);
  }
  async function recover(){
    if(historyLocked())return;
    let finishPending;
    state.chatBusy=true;
    try{
      controls();
      const saved=savedTask();
      let latest=null;
      if(saved){
        try{latest=await api(`/api/agent/tasks/${saved}`);}
        catch(error){if(error.status!==404)throw error;rememberTask(null);}
      }
      if(!latest)latest=(await api('/api/agent/tasks/latest')).task;
      if(!latest){setActiveTask(null);status('没有可恢复的 Agent 任务');return;}
      if(latest.state==='cancelled'){showTerminated();return;}
      if(latest.analysis_result_id && latest.analysis_result_id!==state.resultId){
        const text=tr('该任务分析的结果与右侧当前显示的结果不同。请在右侧点击“让助手分析结果”分析当前结果；如需恢复原任务，请先选择原来的结果。');
        status(text);message('assistant',text,'任务状态');rememberMessage('assistant',text,null,'任务状态');return;
      }
      rememberTask(latest.id);
      const savedReply=readConversation(activeConversation.id)?.messages?.find(({response})=>
        response?.agent_task_id===latest.id && !response.task_state && response.mode!=='provider_error'
        && (!response.error_code || response.mode==='validation_error' || response.ui_action_status
          || (!response.mode && response.error_code==='analysis_result_unavailable')));
      if(latest.state==='completed' && savedReply){
        setActiveTask(null);
        rememberTask(null);
        showOutcome(savedReply.content,savedReply.response.error_code==='ui_action_failed'?'界面操作失败':'此任务已完成，回答已在当前会话中');return;
      }
      if(latest.state!=='completed')finishPending=startPendingReply('正在恢复 Agent 任务…','本次恢复用时');
      if(['interrupted','failed'].includes(latest.state))await api(`/api/agent/tasks/${latest.id}/resume`,{method:'POST'});
      const response=latest.state==='completed' && latest.result
        ? finishTask(latest.id,latest,key()) : await follow(latest.id);
      if(finishPending)response.recovery_elapsed_s=finishPending();
      const reply=response.ui_action?'任务已恢复。配置建议未自动应用到可能已变化的界面，请重新发送修改指令。':response.answer||response.provider_error;
      const text=replyText(response,response.ui_action?tr(reply):reply);
      if(response.ui_action)showOutcome(text,'界面操作未重新执行');
      const mode=['provider_error','validation_error'].includes(response.mode)?'请求未完成':response.mode==='gpt-6.1-terra'?'GPT-6.1 Terra':'Harness 恢复';
      const article=message('assistant',text,mode);decorateResponse(article,response);
      rememberMessage('assistant',text,{...response,agent_task_id:latest.id},mode);
      if(response.mode==='provider_error')window.alert(response.provider_error);
    }catch(error){
      const note=taskNotice(error);
      if(!note)throw error;
      if(finishPending)note.response.recovery_elapsed_s=finishPending();
      status(note.text);
      const article=message('assistant',note.text,'任务状态');
      decorateResponse(article,note.response);
      rememberMessage('assistant',note.text,note.response,'任务状态');
    }finally{finishPending?.();state.chatBusy=false;controls();}
  }
  function bind(){
    setActiveTask(null);
    document.getElementById('agent-steer').onclick=async()=>{
      const input=document.getElementById('agent-instruction'),text=input.value.trim(),id=state.agentTask;
      if(!text||!id)return;
      try{
        await api(`/api/agent/tasks/${id}/steer`,{method:'POST',body:{message:text}});
        if(id===state.agentTask){if(input.value.trim()===text)input.value='';status('追加指令已送达 Harness');}
      }catch(error){if(id===state.agentTask)status(error.message);}
    };
    document.getElementById('agent-cancel').onclick=async()=>{
      const id=state.agentTask;if(!id)return;
      const button=document.getElementById('agent-cancel');
      button.disabled=true;
      status('正在终止任务…');
      try{
        const reply=await api(`/api/agent/tasks/${id}/cancel`,{method:'POST',timeout:null});
        if(id!==state.agentTask)return;
        if(reply.status==='cancelled' || reply.status==='completed'){
          if(activeStream)activeStream.abort({state:reply.status,result:reply.result});
          else if(reply.status==='cancelled')showTerminated();
          else await recover();
        }
      }catch(error){if(id===state.agentTask)status(error.message);}
      finally{if(id===state.agentTask)button.disabled=false;}
    };
    document.getElementById('agent-resume').onclick=()=>recover().catch(error=>status(error.message));
    document.getElementById('bridge-pair').onclick=async event=>{
      const button=event.currentTarget;
      const address=base, owner=sessionId;
      button.disabled=true;
      try{
        if(historyLocked())throw new Error('请等待当前任务结束后重新配对。');
        if(new URL(document.getElementById('api-url').value.trim()).toString().replace(/\/$/,'')!==base.replace(/\/$/,''))throw new Error('请先保存并连接新后端，再生成配对码。');
        const config=readPlecsSettings();
        const result=await api('/api/bridge/pair',{method:'POST',body:config});
        if (base!==address || sessionId!==owner || document.getElementById('api-url').value.trim().replace(/\/$/,'')!==address.replace(/\/$/,'')) return;
        const urlLiteral=new URL(address).href.replace(/\/$/,'').replaceAll("'", "''");
        // Older servers return only owner and token. Use the current, validated session ID.
        const command=result.command || `& '.\\.venv\\Scripts\\python.exe' -m backend.plecs_companion --server $server --owner '${owner}'`;
        const host = new URL(address).hostname;
        const studio = /^(?:[a-z0-9-]+\.ms\.show|studio-[a-z0-9-]+\.api-inference\.modelscope\.net)$/.test(host);
        ui(document.getElementById('bridge-instructions'),[
          '1. 在本机项目文件夹中打开 PowerShell，运行以下命令。',
          `$server = '${urlLiteral}'`, command, '',
          '2. 看到 PLECS pairing token: 时，粘贴下方的 Buck Lab 配对码并按回车。', result.token,
          '请保管配对码，重启连接器时复用。', '',
          ...(studio ? ['3. 如果出现 ModelScope SDK token for … 提示，请核对创空间 API 域名，粘贴有权访问该空间的魔搭账号的 SDK 访问令牌，再按回车。终端会隐藏输入。',
            '魔搭访问令牌页面', 'https://modelscope.cn/my/myaccesstoken', ''] : []),
          '看到 PLECS companion connected. 后，保持终端打开，点击“检查连接器状态”。'
        ].join('\n'));
        try{localStorage.setItem(`buck-plecs:${base}`,JSON.stringify(config));}catch{ui(document.getElementById('settings-status'),'配对成功，但浏览器无法记住本机路径。');}
      }catch(error){ui(document.getElementById('settings-status'),error.message);}
      finally{button.disabled=false;}
    };
    let statusRequest=0;
    document.getElementById('bridge-check').onclick=async()=>{
      const request=++statusRequest, owner=sessionId;
      const target=document.getElementById('api-url').value.trim().replace(/\/$/,'');
      const output=document.getElementById('settings-status');
      if(target!==base.replace(/\/$/,'')){ui(output,'请先保存并连接新后端。');return;}
      const current=()=>request===statusRequest && owner===sessionId && target===base.replace(/\/$/,'')
        && target===document.getElementById('api-url').value.trim().replace(/\/$/,'');
      try{
        const direct=isLocalBackend(target);
        const result=await api(direct?'/api/plecs/status':'/api/bridge/status',{timeout:null});
        if(!current())return;
        ui(output,direct?(result.ready?'本机 PLECS RPC 已就绪。':'本机 PLECS RPC 未就绪，请确认 PLECS 已启动并启用端口 1080。'):
          result.online?'本机连接器在线':result.paired?'已配对，连接器离线':'尚未配对');
      }catch(error){if(current())ui(output,error.message);}
    };
  }
  function reset(){setActiveTask(null);ui(document.getElementById('bridge-instructions'),'');document.getElementById('agent-progress').open=false;document.getElementById('agent-preview').textContent='';document.getElementById('agent-instruction').value='';status('Agent 任务进度与恢复');}
  return {request,recover,bind,reset,taskNotice,showOutcome,replyText};
})();
