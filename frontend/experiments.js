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
    ground_truth:run.ground_truth || null
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
    dataset:{filename:state.dataset?.filename || "",sha256:state.dataset?.sha256 || ""},
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
