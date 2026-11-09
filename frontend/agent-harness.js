/* Durable official-Harness tasks. Streaming is text only, never inner reasoning. */
"use strict";
window.BuckHarness = (()=>{
  const key=()=>`buck-agent:${base}:${sessionId}`;
  const savedTask=()=>{try{return localStorage.getItem(key());}catch{return null;}};
  function rememberTask(id,storageKey=key()){
    try{if(id)localStorage.setItem(storageKey,id);else localStorage.removeItem(storageKey);}
    catch{try{localStorage.removeItem(storageKey);}catch{/* Backend latest-task lookup remains available. */}}
  }
  const status=text=>{document.getElementById('agent-status').textContent=text;};
  function showOutcome(text,label){
    status(tr(label));
    window.BuckMarkdown.render(document.getElementById('agent-preview'),text);
  }
  const headers=()=>({'X-Session-ID':sessionId,...(accessToken?{[appTokenHeader()]:`Bearer ${accessToken}`}:{})});
  function taskError(taskState,id){
    return Object.assign(new Error(taskState),{taskState,agentTaskId:id});
  }
  function taskNotice(error){
    const text=({cancelled:'任务已停止，可恢复。',interrupted:'任务已中断，可恢复。',disconnected:'连接中断，后台任务可能仍在运行。请点击“找回 / 恢复任务”重新连接。'})[error.taskState];
    return text?{text:tr(text),response:{agent_task_id:error.agentTaskId,task_state:error.taskState}}:null;
  }
  async function follow(id){
    try{return await followEvents(id);}
    catch(error){
      if(error.taskState)throw error;
      throw taskError('disconnected',id);
    }
  }
  async function followEvents(id){
    state.agentTask=id;
    document.getElementById('agent-progress').hidden=false;
    document.getElementById('agent-progress').open=true;
    document.getElementById('agent-preview').textContent='';
    const storageKey=key();
    let result=null,buffer='',ended=false,preview='';
    const response=await fetch(base.replace(/\/$/,'')+`/api/agent/tasks/${id}/events`,{headers:headers()});
    if(!response.ok)throw new Error('Cannot reconnect to the Agent task.');
    const reader=response.body.getReader(),decoder=new TextDecoder();
    try{while(true){
      const {value,done}=await reader.read();
      if(done)break;
      buffer+=decoder.decode(value,{stream:true});
      let newline;
      while((newline=buffer.indexOf('\n'))>=0){
        const line=buffer.slice(0,newline);buffer=buffer.slice(newline+1);if(!line)continue;
        const item=JSON.parse(line);
        if(item.kind==='attempt_start'){result=null;preview='';document.getElementById('agent-preview').textContent='';status(item.data.text);}
        if(item.kind==='status')status(item.data.text);
        if(item.kind==='tool_start')status(`正在执行：${tools[item.data.name]||item.data.name}`);
        if(item.kind==='tool_end')status(`${tools[item.data.name]||item.data.name}：${item.data.status}`);
        if(item.kind==='text_delta'){
          preview+=item.data.text;
          window.BuckMarkdown.render(document.getElementById('agent-preview'),preview,{streaming:true});
        }
        if(item.kind==='result')result=item.data;
        if(item.kind==='end'){
          ended=true;
          // The final task snapshot is authoritative, including a null result.
          // Old result events belong to previous attempts after a resume.
          result=item.data.result;
          status(({completed:'任务已完成',failed:'任务未完成，可查看已保存证据',interrupted:'任务已中断，可恢复',cancelled:'任务已停止，可恢复。'})[item.data.state]||item.data.state);
          window.BuckMarkdown.render(document.getElementById('agent-preview'),result?.ui_action?'':result?.answer||result?.provider_error||'');
          if(result?.ui_action)status(tr('等待界面应用操作'));
          if(item.data.state==='completed')rememberTask(null,storageKey);
          if(['cancelled','interrupted'].includes(item.data.state))throw taskError(item.data.state,id);
          if(!result)throw taskError('failed',id);
        }
      }
    }}finally{await reader.cancel().catch(()=>{});reader.releaseLock();}
    if(!ended||!result)throw new Error('Connection interrupted. The Agent task remains on the backend; reconnect to retrieve it.');
    return {...result,agent_task_id:id};
  }
  async function request(body){
    const task=await api('/api/agent/tasks',{method:'POST',body,timeout:30000});
    rememberTask(task.id);
    return follow(task.id);
  }
  async function recover(){
    if(historyLocked())return;
    state.chatBusy=true;controls();
    try{
    const saved=savedTask();
    let latest=null;
    if(saved){
      try{latest=await api(`/api/agent/tasks/${saved}`);}
      catch(error){if(error.status!==404)throw error;rememberTask(null);}
    }
    if(!latest)latest=(await api('/api/agent/tasks/latest')).task;
    if(!latest){status('没有可恢复的 Agent 任务');return;}
    if(latest.analysis_result_id && latest.analysis_result_id!==state.resultId){
      const text=tr('该任务分析的结果与右侧当前显示的结果不同。请在右侧点击“让助手分析结果”分析当前结果；如需恢复原任务，请先选择原来的结果。');
      status(text);message('assistant',text,'任务状态');rememberMessage('assistant',text,null,'任务状态');return;
    }
    rememberTask(latest.id);
    const savedReply=readConversation(activeConversation.id)?.messages?.find(({response})=>
      response?.agent_task_id===latest.id && !response.task_state && response.mode!=='provider_error'
      && (!response.error_code || response.mode==='validation_error' || response.ui_action_status));
    if(latest.state==='completed' && savedReply){
      rememberTask(null);
      showOutcome(savedReply.content,savedReply.response.error_code==='ui_action_failed'?'界面操作失败':'此任务已完成，回答已在当前会话中');return;
    }
    if(['interrupted','failed','cancelled'].includes(latest.state))await api(`/api/agent/tasks/${latest.id}/resume`,{method:'POST'});
      const response=await follow(latest.id);
      const text=response.ui_action?'任务已恢复。配置建议未自动应用到可能已变化的界面，请重新发送修改指令。':response.answer||response.provider_error;
      if(response.ui_action)showOutcome(text,'界面操作未重新执行');
      const mode=['provider_error','validation_error'].includes(response.mode)?'请求未完成':'Harness 恢复';
      const article=message('assistant',text,mode);decorateResponse(article,response);
      rememberMessage('assistant',text,{...response,agent_task_id:latest.id},mode);
      if(response.mode==='provider_error')window.alert(response.provider_error);
    }catch(error){
      const note=taskNotice(error);
      if(!note)throw error;
      status(note.text);message('assistant',note.text,'任务状态');
      rememberMessage('assistant',note.text,note.response,'任务状态');
    }finally{state.chatBusy=false;controls();}
  }
  function bind(){
    document.getElementById('agent-steer').onclick=async()=>{
      const input=document.getElementById('agent-instruction'),text=input.value.trim();if(!text||!state.agentTask)return;
      try{await api(`/api/agent/tasks/${state.agentTask}/steer`,{method:'POST',body:{message:text}});input.value='';status('追加指令已送达 Harness');}catch(error){status(error.message);}
    };
    document.getElementById('agent-cancel').onclick=async()=>{try{if(state.agentTask){await api(`/api/agent/tasks/${state.agentTask}/cancel`,{method:'POST'});status('正在停止任务…');}}catch(error){status(error.message);}};
    document.getElementById('agent-resume').onclick=()=>recover().catch(error=>status(error.message));
    document.getElementById('bridge-pair').onclick=async()=>{
      try{
        if(historyLocked())throw new Error('请等待当前任务结束后重新配对。');
        if(new URL(document.getElementById('api-url').value.trim()).toString().replace(/\/$/,'')!==base.replace(/\/$/,''))throw new Error('请先保存并连接新后端，再生成配对码。');
        const config=readPlecsSettings();
        const result=await api('/api/bridge/pair',{method:'POST',body:config});
        document.getElementById('bridge-instructions').textContent=`在本机项目文件夹运行：\n.venv\\Scripts\\python.exe -m backend.plecs_companion --server "${base}" --owner "${result.owner}"\n程序提示时粘贴配对码：\n${result.token}\n妥善保存此配对码，重启连接器时复用；不要分享。`;
        try{localStorage.setItem(`buck-plecs:${base}`,JSON.stringify(config));}catch{document.getElementById('settings-status').textContent='配对成功，但浏览器无法记住本机路径。';}
      }catch(error){document.getElementById('settings-status').textContent=error.message;}
    };
    document.getElementById('bridge-check').onclick=async()=>{
      const target=document.getElementById('api-url').value.trim().replace(/\/$/,'');
      const output=document.getElementById('settings-status');
      if(target!==base.replace(/\/$/,'')){ui(output,'请先保存并连接新后端。');return;}
      try{
        const direct=isLocalBackend(target);
        const result=await api(direct?'/api/plecs/status':'/api/bridge/status');
        if(target!==document.getElementById('api-url').value.trim().replace(/\/$/,''))return;
        ui(output,direct?(result.ready?'本机 PLECS RPC 已就绪。':'本机 PLECS RPC 未就绪，请确认 PLECS 已启动并启用端口 1080。'):
          result.online?'本机连接器在线':result.paired?'已配对，连接器离线':'尚未配对');
      }catch(error){output.textContent=error.message;}
    };
  }
  function reset(){state.agentTask=null;document.getElementById('bridge-instructions').textContent='';document.getElementById('agent-progress').open=false;document.getElementById('agent-preview').textContent='';document.getElementById('agent-instruction').value='';status('Agent 任务进度与恢复');}
  return {request,recover,bind,reset,taskNotice,showOutcome};
})();
