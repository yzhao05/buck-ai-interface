/* Explicit, bounded chat plans. Numerical jobs still use the normal validated API. */
"use strict";
const identificationPlans=new Map();
function validStoredIdentificationPlan(plan){
  return plan && typeof plan==='object' && ['queued','running','paused','complete','cancelled'].includes(plan.status)
    && Array.isArray(plan.methods) && plan.methods.length===2 && new Set(plan.methods).size===2
    && plan.methods.every(m=>['weighted_integral','pann'].includes(m))
    && Array.isArray(plan.done) && plan.done.length<=2 && plan.done.every(r=>r && plan.methods.includes(r.method) && typeof r.job_id==='string')
    && plan.changes && typeof plan.changes==='object' && !Array.isArray(plan.changes)
    && (plan.filename===null || typeof plan.filename==='string');
}
function identificationPlanKey(){return activeConversation ? `buck-plan:${base}:${conversationPrefix}:${activeConversation.id}` : null;}
function currentIdentificationPlan(){
  const key=identificationPlanKey();if(!key)return null;
  if(!identificationPlans.has(key)){
    let plan=null;
    try{plan=JSON.parse(sessionStorage.getItem(key));}catch{}
    if(!validStoredIdentificationPlan(plan))plan=null;
    if(plan && !["complete","cancelled"].includes(plan.status)){
      plan.status="paused";plan.note="页面已恢复，请检查当前设置后继续计划。";
    }
    identificationPlans.set(key,plan);
  }
  return identificationPlans.get(key);
}
function saveIdentificationPlan(plan){
  const key=identificationPlanKey();
  // Persist before starting a job, so refreshing cannot silently start it twice.
  try{sessionStorage.setItem(key,JSON.stringify(plan));}
  catch{throw new Error("无法保存计划进度。请检查浏览器存储；已提交的任务请在实验历史中核对后再继续。");}
  identificationPlans.set(key,plan);
}
function renderIdentificationPlan(){
  const box=$("identification-plan");if(!box)return;
  const plan=currentIdentificationPlan();box.hidden=!plan;if(!plan)return;
  $("plan-progress").textContent=`${tr("两种方法辨识计划")} · ${plan.done.length} / ${plan.methods.length}`;
  $("plan-file").textContent=plan.filename || state.dataset?.filename || tr("当前 CSV");
  $("plan-note").textContent=tr(plan.note || "正在检查 CSV 与实验条件…");
  $("plan-runs").replaceChildren();
  for(const item of plan.done){
    const row=document.createElement("li");
    row.textContent=`${item.method==='pann'?'PANN':tr('抗噪加权积分')} · ${tr('已完成')}${item.report_name ? ' · '+item.report_name : ''}`;
    $("plan-runs").append(row);
  }
  $("plan-continue").hidden=plan.status!=="paused";
  $("plan-continue").disabled=historyLocked() || !state.connected;
  $("plan-cancel").hidden=["complete","cancelled"].includes(plan.status);
}
async function acceptIdentificationPlan(action,context){
  if(!Array.isArray(action.methods) || action.methods.length!==2 || new Set(action.methods).size!==2 || action.methods.some(m=>!["pann","weighted_integral"].includes(m))
      || !(action.filename===null || (typeof action.filename==='string' && action.filename.length<=255 && /\.csv$/i.test(action.filename) && !/[\\/]/.test(action.filename))))throw new Error("配置操作格式无效。");
  const c=action.changes;
  if(!c || Object.keys(c).some(k=>!["mode","pwm_source","resistance_ohm"].includes(k)) || ('pwm_source' in c && !["measured","controller"].includes(c.pwm_source))
      || (c.mode && !["LC","LCR"].includes(c.mode)) || (c.pwm_source==='controller' && c.mode!=='LC')
      || ('resistance_ohm' in c && !(typeof c.resistance_ohm==='number' && Number.isFinite(c.resistance_ohm) && c.resistance_ohm>.01 && c.resistance_ohm<=100000)))throw new Error("配置操作格式无效。");
  const key=`buck-ui-action:${sessionId}:${action.id}`;
  if(sessionStorage.getItem(key))return tr("此操作已处理，未重复应用或启动。");
  const previous=currentIdentificationPlan();
  if(previous && !["complete","cancelled"].includes(previous.status))return tr("已有辨识计划，请先继续或取消该计划。");
  if(state.busy || state.syncBusy || state.job)return tr("已有任务或同步正在进行，本次未修改配置或启动新实验。");
  const active=await api('/api/jobs/active',{timeout:10000});
  if(configurationFingerprint()!==context.fingerprint)return tr("会话、数据或设置已变化，本次操作未应用。请按当前界面重新发送指令。");
  if(active.job || state.busy || state.syncBusy || state.job)return tr("已有任务或同步正在进行，本次未修改配置或启动新实验。");
  saveIdentificationPlan({...action,status:'queued',done:[],jobId:null,note:"正在检查 CSV 与实验条件…"});
  sessionStorage.setItem(key,'processed');
  toggleSection("toggle-experiment","experiment-content","实验配置",true);setIdentificationOpen(true);
  return tr("已建立两种方法的执行计划：先加权积分，再 PANN。正在查找 CSV；缺少条件会显示在计划卡片中，补齐后点击继续。完成的结果将分别保存在本会话。");
}
function startQueuedIdentificationPlan(){
  if(currentIdentificationPlan()?.status==='queued' && !historyLocked())void continueIdentificationPlan();
}
function planInputs(){
  return JSON.stringify({sha256:state.dataset?.sha256,mapping:state.mapping,
    fields:experimentFields.filter(id=>!['fit-method','selected-result','chat-images','controller-image'].includes(id)).map(id=>[id,$(id).value])});
}
async function continueIdentificationPlan(){
  const plan=currentIdentificationPlan();
  if(!plan || !['queued','paused'].includes(plan.status) || historyLocked())return;
  const owner=identificationPlanKey();
  state.planRunning=true;plan.status='running';plan.note="正在检查 CSV 与实验条件…";
  try{
    saveIdentificationPlan(plan);controls();
    toggleSection('toggle-experiment','experiment-content','实验配置',true);setIdentificationOpen(true);
    if(!state.connected)throw new Error("请先连接分析后端。");
    if(plan.filename && state.dataset?.filename.toLowerCase()!==plan.filename.toLowerCase()){
      if(plan.done.length || plan.jobId)throw new Error("计划的数据或参数已更改。请恢复原设置，或取消计划后重新发出指令。");
      const {entries,incomplete}=await historicalCsvEntries();
      const candidates=entries.filter(e=>e.filename.toLowerCase()===plan.filename.toLowerCase());
      const hashes=new Set(candidates.map(e=>e.sha256));
      if(hashes.size>1)throw new Error("找到同名但内容不同的 CSV。请在历史 CSV 列表中明确选择文件，再继续计划。");
      if(incomplete)throw new Error("历史 CSV 查询不完整。请手动选择目标 CSV，再继续计划。");
      if(!candidates.length)throw new Error("没有找到指定 CSV。请上传或从历史列表选择计划中的文件，再点击继续。");
      // Loading a different file requires the user to confirm its circuit again.
      const range=[$('start-ms').value,$('end-ms').value];
      const loaded=await importData(null,()=>historicalCsvFile(candidates[0]),{fromPlan:true});
      if(loaded?.error)throw new Error(loaded.error);
      if(state.dataset?.sha256!==candidates[0].sha256)throw new Error("没有找到指定 CSV。请上传或从历史列表选择计划中的文件，再点击继续。");
      if(range[0])$('start-ms').value=range[0];if(range[1])$('end-ms').value=range[1];
    }
    if(!state.dataset){toggleSection('toggle-import','import-content','导入波形',true);throw new Error("请先上传 CSV；本次未启动实验。");}
    const c=plan.changes;
    if(!plan.done.length && !plan.jobId){
      if(c.pwm_source)$('pwm-source').value=c.pwm_source;if(c.mode)$('fit-mode').value=c.mode;
      if('resistance_ohm' in c){
        if(Number($('fixed-r').value)!==c.resistance_ohm)$('controller-confirmed').value='no';
        $('fixed-r').value=String(c.resistance_ohm);
      }
      prepareControllerDefaults();controls();persistConversation();
    }
    if(!state.mapping){$('mapping-details').open=true;throw new Error("请补齐并应用时间、电流、电压列映射；本次未启动实验。");}
    if(pwmSource()==='controller' && $('controller-confirmed').value!=='yes')throw new Error("请核对并确认控制电路与当前参数一致；本次未启动实验。");
    if(pwmSource()==='measured' && !state.mapping.pwm)throw new Error("请映射实际 PWM 列，或手动选择并确认控制器重建；本次未启动实验。");
    if($('fit-method').querySelector('[value="pann"]').disabled)throw new Error("后端未安装 PyTorch，未切换方法或启动实验。");
    const previousMethod=$('fit-method').value;
    try{$('fit-method').value='pann';options();}finally{$('fit-method').value=previousMethod;}
    if((plan.done.length || plan.jobId) && plan.inputs!==planInputs())throw new Error("计划的数据或参数已更改。请恢复原设置，或取消计划后重新发出指令。");
    const active=await api('/api/jobs/active',{timeout:10000});
    if(active.job && active.job.job_id!==plan.jobId)throw new Error("已有任务或同步正在进行，本次未修改配置或启动新实验。");
    if(plan.uncertain)throw new Error("上次提交状态未确认。请先查看实验历史与当前任务，取消此计划后再决定是否重试，避免重复计算。");
    plan.inputs=planInputs();saveIdentificationPlan(plan);
    while(plan.done.length<plan.methods.length && plan.status!=='cancelled'){
      if(owner!==identificationPlanKey())throw new Error("计划的数据或参数已更改。请恢复原设置，或取消计划后重新发出指令。");
      const method=plan.methods[plan.done.length];$('fit-method').value=method;
      plan.note=method==='pann'?"正在运行 PANN，完成后将分别保留两份结果。":"正在运行抗噪加权积分，完成后继续 PANN。";saveIdentificationPlan(plan);controls();
      let outcome;
      if(plan.jobId){
        const job=await api(`/api/jobs/${plan.jobId}`);
        if(job.status==='failed')throw new Error(job.error || tr("任务未完成，请检查提示"));
        outcome=await watchJob(plan.jobId);
      }else if(resultMatchesCurrent(state.result))outcome={status:'complete',job_id:state.resultId};
      else outcome=await startFit({fromChat:true,waitForCompletion:true,onSubmitted:id=>{plan.jobId=id;saveIdentificationPlan(plan);}});
      if(outcome?.status!=='complete'){
        plan.uncertain=Boolean(outcome?.uncertain);
        throw new Error(outcome?.error || tr("未能启动实验：")+tr("请检查当前任务和输入设置。"));
      }
      // Save the completed snapshot before a later job replaces the result card.
      if(!persistConversation())throw new Error("当前会话尚未完整保存，已保留在本页；请先重试保存或同步。");
      const run=activeConversation.experiments?.find(r=>r.id===outcome.job_id);
      plan.done.push({method,job_id:outcome.job_id,report_name:run?experimentReportName(run):null,parameters:{...state.result.parameters}});
      plan.jobId=null;saveIdentificationPlan(plan);
    }
    if(plan.status!=='cancelled'){
      plan.status='complete';plan.note="两种方法均已完成。可从参数辨识下拉列表查看和导出各自结果。";
      const text=tr(plan.note)+'\n\n'+plan.done.map(r=>`- **${r.method==='pann'?'PANN':tr('抗噪加权积分')}**: L = ${fmt(r.parameters.L_H*1e6,6)} μH, C = ${fmt(r.parameters.C_F*1e6,6)} μF, R = ${fmt(r.parameters.R_ohm,6)} Ω`).join('\n');
      message('assistant',text,'本地规则分析');rememberMessage('assistant',text,null,'本地规则分析');
    }
  }catch(error){if(plan.status!=='cancelled'){plan.status='paused';plan.note=error.message;}}
  finally{
    state.planRunning=false;
    try{saveIdentificationPlan(plan);}catch(error){
      if(!['complete','cancelled'].includes(plan.status))plan.status='paused';
      plan.note=error.message;identificationPlans.set(owner,plan);
    }
    try{persistConversation();}finally{controls();}
  }
}
document.addEventListener('buck-language-change',()=>renderIdentificationPlan());
document.addEventListener('click',event=>{
  if(event.target.closest('#plan-continue'))void continueIdentificationPlan();
  if(event.target.closest('#plan-cancel')){
    const plan=currentIdentificationPlan();if(!plan)return;
    plan.status='cancelled';plan.note="已取消后续任务；已提交的计算仍会完成并保存。";
    try{saveIdentificationPlan(plan);}catch(error){notice(error.message);}
    renderIdentificationPlan();
  }
});
