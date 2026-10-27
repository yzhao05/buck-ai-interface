/* Completed runs belong to the conversation, independently of the current card. */
"use strict";
function mergeExperiments(...groups) {
  const runs=new Map();
  for (const group of groups) for (const run of group || []) {
    if (!run?.id || !run.result?.parameters) continue;
    const old=runs.get(run.id);
    if (!old || run.updated>old.updated || (run.updated===old.updated && JSON.stringify(run)>JSON.stringify(old))) runs.set(run.id,run);
  }
  return [...runs.values()].sort((a,b)=>a.created-b.created || a.id.localeCompare(b.id));
}
function storedExperiments(id) {
  const prefix=conversationPrefix+id+":experiment:", runs=[];
  for (const key of Object.keys(localStorage)) if (key.startsWith(prefix)) {
    try { runs.push(JSON.parse(localStorage.getItem(key))); } catch { /* Keep other runs readable. */ }
  }
  return runs;
}
function experimentSnapshot(result) {
  // Keep numerical evidence and settings; raw curves/epoch traces remain in reports.
  return JSON.parse(JSON.stringify(result,(key,value)=>["chart","epoch_history","history"].includes(key) ? undefined : value));
}
function chatExperiments() {
  // Explicit allowlist: never attach the saved result object or training traces.
  return (readConversation(activeConversation.id)?.experiments || []).map(run=>({
    id:run.id,dataset:{filename:run.dataset.filename,sha256:run.dataset.sha256},
    method:run.result.method || run.result.options?.method || null,
    pwm_source:run.result.options?.pwm_source || null,
    parameters:{L_H:run.result.parameters.L_H,C_F:run.result.parameters.C_F,R_ohm:run.result.parameters.R_ohm},
    ground_truth:run.ground_truth || null,
    comparison_context:run.result.options && run.result.mapping && Number.isFinite(run.result.train_start_s) && Number.isFinite(run.result.validation_end_s)
      ? {options:run.result.options,mapping:run.result.mapping,start_s:run.result.train_start_s,end_s:run.result.validation_end_s} : null
  }));
}
function comparingExperiments(text) {return /对比|比较|横向|纵向|\b(?:compare|comparison|versus)\b/i.test(text);}
function captureExperiment({updateTruth=false}={}) {
  if (!activeConversation || !state.resultId || !state.result) return;
  const runs=mergeExperiments(activeConversation.experiments,storedExperiments(activeConversation.id));
  const old=runs.find(r=>r.id===state.resultId), comparison=parameterComparison();
  // Restoring a stale tab/card must not turn old UI values into a new truth edit.
  // Only explicit edits/calculation may update an existing run's comparison.
  if (old && !updateTruth) {activeConversation.experiments=runs;return;}
  const run={id:state.resultId,created:old?.created || Date.now(),updated:old?.updated || Date.now(),
    dataset:state.savedRun?.dataset || {filename:state.dataset?.filename || "",sha256:state.dataset?.sha256 || ""},
    result:experimentSnapshot(state.result),
    ground_truth:comparison ? {result_id:state.resultId,...comparison.ground_truth} : null};
  if (!old || JSON.stringify({...old,updated:0})!==JSON.stringify({...run,updated:0})) run.updated=Math.max(Date.now(),(old?.updated || 0)+1);
  activeConversation.experiments=mergeExperiments(runs,[run]);
}
function saveExperimentKeys(record) {
  record.experiments=mergeExperiments(record.experiments,storedExperiments(record.id));
  for (const run of record.experiments) localStorage.setItem(conversationPrefix+record.id+":experiment:"+run.id,JSON.stringify(run));
}
async function recoverExperiments() {
  if (!state.connected || !activeConversation) return;
  const record=activeConversation;
  try {
    const response=await api("/api/experiments");
    if (activeConversation!==record) return;
    record.experiments=mergeExperiments(record.experiments,response.experiments);
    persistConversation();
  } catch (error) {
    if (error.status!==404) historyWarning("旧实验缓存暂未恢复，请检查后端连接后重试。");
  }
}
function renderExperiments() {
  renderResultLibrary();
  const list=$("experiment-runs");if (!list) return;
  list.replaceChildren();
  const runs=activeConversation?.experiments || [];
  ui($("experiment-count"),"已保存实验");$("experiment-count-number").textContent=String(runs.length);
  ui($("experiment-empty"),"完成辨识后自动保存，可让助手对比本会话的多次实验。");$("experiment-empty").hidden=Boolean(runs.length);
  for (const [index,run] of runs.entries()) {
    const row=document.createElement("li"), p=run.result.parameters, opts=run.result.options || {};
    const name=document.createElement("strong");name.textContent=`${index+1}. ${run.dataset.filename}`;
    const method={pann:"PANN",weighted_integral:tr("抗噪加权积分"),integral:tr("积分辨识")}[opts.method || run.result.method] || "—";
    const pwm={measured:tr("实测 PWM"),controller:tr("重建 PWM")}[opts.pwm_source] || "—";
    const detail=document.createElement("div");detail.textContent=`${method} · ${pwm} · L ${Number(p.L_H*1e6).toPrecision(6)} μH · C ${Number(p.C_F*1e6).toPrecision(6)} μF`;
    row.append(name,detail);list.append(row);
  }
}

function experimentReportName(run) {
  return reportFilename({...run.result,filename:run.result.filename || run.dataset.filename},new Date(run.created));
}
function closeResultLibrary() {
  $("result-library-menu").hidden=true;$("toggle-result-library").setAttribute("aria-expanded","false");
}
function renderResultLibrary() {
  const runs=mergeExperiments(activeConversation?.experiments,conversationCache.get(activeConversation?.id)?.experiments).sort((a,b)=>b.created-a.created || b.id.localeCompare(a.id));
  const list=$("result-library-list");list.replaceChildren();
  ui($("result-library-status"),runs.length ? "按完成时间从新到旧排列" : "本对话暂无已完成的辨识记录。");
  const selected=runs.find(run=>run.id===state.resultId);
  $("selected-result-name").hidden=!selected;
  $("selected-result-name").textContent=selected ? experimentReportName(selected) : "";
  for(const run of runs){
    const button=document.createElement("button");button.type="button";button.className="result-library-item";
    button.dataset.resultId=run.id;button.setAttribute("aria-current",String(run.id===state.resultId));button.disabled=historyLocked();
    const name=document.createElement("strong");name.textContent=experimentReportName(run);
    const detail=document.createElement("small"),opts=run.result.options || {};
    detail.textContent=`${new Date(run.created).toLocaleString(window.BuckI18n.language==="en"?"en-GB":"zh-CN")} · ${opts.mode || "—"} · R ${fmt(run.result.parameters.R_ohm)} Ω (${tr(run.result.R_estimated?"估计":"固定已知")}) · ${run.id.slice(0,8)}`;
    button.append(name,detail);button.onclick=()=>void selectExperiment(run.id);list.append(button);
  }
}
async function selectExperiment(id,{fetchFull=true,persist=true}={}) {
  if(historyLocked() || !activeConversation)return;
  if(persist && !persistConversation()){notice("当前会话尚未完整保存，已保留在本页；请先重试保存或同步。");return;}
  const run=readConversation(activeConversation.id)?.experiments?.find(item=>item.id===id);
  if(!run)return;
  closeResultLibrary();closeAnalysisMenu();
  state.busy=true;controls();
  try{
    let result=structuredClone(run.result);
    if(fetchFull && state.connected){
      try{const job=await api(`/api/jobs/${id}`,{timeout:5000});
        if(job.status==="complete" && job.result.file_sha256===run.dataset.sha256 && JSON.stringify(job.result.parameters)===JSON.stringify(run.result.parameters))result=job.result;
      }catch{/* A durable snapshot remains viewable after the job cache expires. */}
    }
    state.savedRun=structuredClone(run);state.resultId=run.id;
    state.result={...result,filename:result.filename || run.dataset.filename,file_sha256:result.file_sha256 || run.dataset.sha256};
    state.truthDraft={l:"",c:""};state.truthComparison=null;state.truthError="";
    if(run.ground_truth){
      const truth=run.ground_truth;
      state.truthDraft={l:String(truth.L_H*1e6),c:String(truth.C_F*1e6)};
      state.truthComparison={result_id:run.id,parameters:{L_H:result.parameters.L_H,C_F:result.parameters.C_F},ground_truth:{L_H:truth.L_H,C_F:truth.C_F}};
    }
    $("selected-result").value=run.id;
    setIdentificationOpen(true);displayResult();renderResultLibrary();
  }catch(error){notice(error.message);}
  finally{state.busy=false;if(persist)persistConversation();controls();}
}
function toggleResultLibrary(){
  if(historyLocked())return;
  if(!$("result-library-menu").hidden){closeResultLibrary();return;}
  renderHistory();renderResultLibrary();$("result-library-menu").hidden=false;$("toggle-result-library").setAttribute("aria-expanded","true");
  $("result-library-list").querySelector("button")?.focus();
}
document.addEventListener("click",event=>{if(!event.target.closest("#result-library-menu,#toggle-result-library"))closeResultLibrary();});
document.addEventListener("keydown",event=>{
  if($("result-library-menu").hidden)return;
  if(event.key==="Escape"){event.preventDefault();closeResultLibrary();$("toggle-result-library").focus();}
  if(["ArrowDown","ArrowUp"].includes(event.key)){
    const buttons=[...$("result-library-list").querySelectorAll("button:not(:disabled)")];if(!buttons.length)return;
    event.preventDefault();const index=buttons.indexOf(document.activeElement);buttons[(index+(event.key==="ArrowDown"?1:-1)+buttons.length)%buttons.length].focus();
  }
});
