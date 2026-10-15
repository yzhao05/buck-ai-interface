/* Local conversation archive. Store messages and attachment references, never CSV arrays or credentials. */
"use strict";
let activeConversation = null;
let conversationPrefix = "";
let pendingAttachment = null;
const conversationCache = new Map();
const experimentFields = ["vin", "frequency", "fit-mode", "fixed-r", "fit-method", "pann-state", "pann-cycles", "pann-epochs", "start-ms", "end-ms", "train-fraction"];
const experimentDefaults = {vin:"28", frequency:"100", "fit-mode":"LCR", "fixed-r":"6", "fit-method":"pann", "pann-state":"joint", "pann-cycles":"0", "pann-epochs":"100", "start-ms":"", "end-ms":"", "train-fraction":"70"};
function historyWarning(text = "") {
  ui($("history-status"), text); $("history-status").hidden = !text;
}
function historyLocked() { return state.busy || state.chatBusy || Boolean(state.job); }
function mergeMessages(id, ...groups) {
  const items=new Map();
  for (const group of groups) group.forEach((m,index)=>{
    const key=m.id || `${id}-legacy-${index}`;
    items.set(key,{...m,id:key,created:m.created ?? index});
  });
  return [...items.values()].sort((a,b)=>a.created-b.created || a.id.localeCompare(b.id));
}
function storedMessages(id) {
  const prefix=conversationPrefix+id+":message:", messages=[];
  for (let i=0;i<localStorage.length;i++) {
    const key=localStorage.key(i);
    if (!key?.startsWith(prefix)) continue;
    try {
      const m=JSON.parse(localStorage.getItem(key));
      if (m && typeof m.id === "string" && Number.isFinite(m.created) && ["user","assistant"].includes(m.role) && typeof m.content === "string") messages.push(m);
    } catch { /* One damaged entry must not hide other conversations. */ }
  }
  return messages;
}
function readConversation(id) {
  try {
    const record = conversationCache.get(id) || JSON.parse(localStorage.getItem(conversationPrefix + id));
    if (!record || record.id !== id || !Number.isFinite(new Date(record.updated).getTime()) || !Array.isArray(record.messages) || !record.messages.every(m =>
      ["user","assistant"].includes(m.role) && typeof m.content === "string")) return null;
    record.messages=mergeMessages(id,record.messages,storedMessages(id));
    record.updated=record.messages.reduce((latest,m)=>Math.max(latest,m.created),record.updated);
    conversationCache.set(id, record); return record;
  } catch { return conversationCache.get(id) || null; }
}
function persistConversation() {
  if (!activeConversation) return;
  activeConversation.draft = $("chat-input").value;
  activeConversation.fields = Object.fromEntries(experimentFields.map(id => [id, $(id).value]));
  activeConversation.attachment = state.dataset ? {dataset:state.dataset, mapping:state.mapping,
    mappingDraft:mappingFromForm(), resultId:state.resultId, jobId:state.job} : pendingAttachment;
  conversationCache.set(activeConversation.id, activeConversation);
  try {
    // Append-only message keys prevent an older tab from replacing newer messages.
    activeConversation.messages=mergeMessages(activeConversation.id,activeConversation.messages);
    for (const item of activeConversation.messages) {
      const key=conversationPrefix+activeConversation.id+":message:"+item.id;
      if (!localStorage.getItem(key)) localStorage.setItem(key,JSON.stringify(item));
    }
    activeConversation.messages=mergeMessages(activeConversation.id,activeConversation.messages,storedMessages(activeConversation.id));
    activeConversation.updated=activeConversation.messages.reduce((latest,m)=>Math.max(latest,m.created),activeConversation.updated);
    localStorage.setItem(conversationPrefix + activeConversation.id, JSON.stringify({...activeConversation,messages:[],messageStoreVersion:1}));
    localStorage.setItem(conversationPrefix + "active", activeConversation.id);
    historyWarning();
  } catch { historyWarning("历史保存失败：浏览器存储不可用或已满。当前对话仍可使用，请勿关闭页面。"); }
}
function renderHistory() {
  try {
    for (let i=0; i<localStorage.length; i++) {
      const key = localStorage.key(i);
      if (key?.startsWith(conversationPrefix) && key !== conversationPrefix + "active" && !key.slice(conversationPrefix.length).includes(":")) readConversation(key.slice(conversationPrefix.length));
    }
  } catch { historyWarning("历史保存失败：浏览器存储不可用或已满。当前对话仍可使用，请勿关闭页面。"); }
  $("history-list").replaceChildren();
  for (const record of [...conversationCache.values()].sort((a,b) => b.updated-a.updated)) {
    const button = document.createElement("button"); button.type = "button"; button.className = "history-item";
    button.dataset.conversationId = record.id; button.setAttribute("aria-current", String(record.id === activeConversation?.id));
    button.disabled = historyLocked();
    const title = document.createElement("strong");
    const first = record.messages.find(m=>m.role === "user");
    if (first) title.textContent = first.content.slice(0,80); else ui(title,"新会话");
    const date = document.createElement("time");
    date.textContent = new Date(record.updated).toLocaleString(window.BuckI18n.language === "en" ? "en-GB" : "zh-CN", {month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit"});
    date.dateTime = new Date(record.updated).toISOString();
    button.append(title,date); button.onclick = () => void openConversation(record.id);
    $("history-list").append(button);
  }
}
function rememberMessage(role, content, response = null, mode = "") {
  const created=Math.max(Date.now(),(activeConversation.messages.at(-1)?.created || 0)+1);
  activeConversation.messages.push({id:crypto.randomUUID(),created,role,content,response,mode});
  activeConversation.updated = Date.now();
  state.history = activeConversation.messages.filter(m => m.mode !== "请求未完成")
    .map(m=>({role:m.role,content:m.content.slice(0,8000)})).slice(-10);
  persistConversation(); renderHistory();
}
function resetExperiment() {
  pendingAttachment=null;
  state.dataset=null; state.mapping=null; state.summary=null; state.chart=null; state.zoom=null;
  invalidateResult();
  $("file-info").hidden=true; $("chart-empty").hidden=false; $("chart-container").hidden=true;
  for (const id of ["map-time","map-current","map-voltage","map-pwm"]) $(id).replaceChildren();
  $("mapping-details").open=false;
  ui($("chat-context"),"未关联实验数据"); ui($("job-status"),"等待导入实验数据");
  ui($("wave-subtitle"),"等待导入实验数据");
  for (const id of ["stat-voltage","stat-current","stat-duration"]) ui($(id),"—");
  ui($("stat-points"),"尚未导入数据");
  setIdentificationOpen(false);
}
async function restoreAttachment(attachment) {
  if (!attachment?.dataset) return;
  if (!state.connected) { ui($("chat-context"),"历史 CSV 需重新关联"); return; }
  state.busy=true; controls();
  try {
    state.dataset=attachment.dataset;
    fillMappings(state.dataset);
    const mapping=attachment.mappingDraft || attachment.mapping;
    if (mapping) {
      for (const [key,id] of Object.entries({time:"map-time",current:"map-current",voltage:"map-voltage",pwm:"map-pwm",time_unit:"time-unit",current_unit:"current-unit",voltage_unit:"voltage-unit"})) $(id).value=mapping[key] || "";
    }
    // Only previously confirmed mappings may be applied automatically.
    if (attachment.mapping) await applyMapping();
    else {
      $("mapping-details").open=true;
      ui($("job-status"),"映射已更改，请点击应用并检查数据");
    }
    pendingAttachment=null;
    $("file-info").hidden=false; $("file-name").textContent=state.dataset.filename;
    ui($("file-rows"),`${state.dataset.rows.toLocaleString()} 行 · CSV 已读取`);
    ui($("chat-context"),`已关联：${state.dataset.filename}`);
    const jobId=attachment.jobId || attachment.resultId;
    if (jobId && attachment.mapping) {
      try {
        const job=await api(`/api/jobs/${jobId}`);
        if (job.status === "complete" && resultMatchesCurrent(job.result)) {
          state.result=job.result; state.resultId=jobId; displayResult();
        }
        else if (["running","queued"].includes(job.status)) void watchJob(jobId);
      } catch { /* CSV can remain usable after a result expires. */ }
    }
  } catch (error) {
    resetExperiment();
    if (error.status === 404) activeConversation.attachment=null;
    else pendingAttachment=attachment;
    ui($("chat-context"),"历史 CSV 需重新关联");
    notice(error.status === 404 ? "历史对话已恢复；CSV 已过期或不可用。如需重新分析，请再次上传 CSV。" : error.message);
  } finally { state.busy=false; controls(); }
}
async function openConversation(id, saveCurrent = true) {
  if (historyLocked()) return;
  if (saveCurrent) persistConversation();
  const record=readConversation(id);
  if (!record) return;
  activeConversation=record; sessionId=record.id;
  notice(); resetExperiment(); clearChat();
  pendingAttachment=record.attachment;
  for (const field of experimentFields) $(field).value=record.fields?.[field] ?? experimentDefaults[field];
  // A legacy "integral" value is no longer in the selector. The new identifier
  // also prevents restoreAttachment from relabelling an old result as weighted.
  if (!$("fit-method").value || (state.connected && $("fit-method").selectedOptions[0]?.disabled)) $("fit-method").value="weighted_integral";
  $("fixed-r-label").hidden=$("fit-mode").value!=="LC";
  ui($("train-label"),$("train-fraction").value+"%");
  $("chat-input").value=record.draft || "";
  for (const item of record.messages) {
    const article=message(item.role,item.content,item.mode);
    if (item.response) decorateResponse(article,item.response);
  }
  const empty=!record.messages.length;
  $("conversation-panel").classList.toggle("is-empty",empty);
  $("chat-messages").querySelector(".welcome").hidden=!empty;
  $("chat-messages").querySelector(".suggestions").hidden=!empty;
  state.history=record.messages.filter(m=>m.mode!=="请求未完成").map(m=>({role:m.role,content:m.content.slice(0,8000)})).slice(-10);
  try { localStorage.setItem(conversationPrefix+"active",id); } catch { /* persist reports storage errors */ }
  renderHistory();
  await restoreAttachment(record.attachment);
  $("chat-messages").scrollTop=$("chat-messages").scrollHeight;
  controls();
}
async function newConversation() {
  if (historyLocked()) return;
  persistConversation();
  const id=crypto.randomUUID();
  conversationCache.set(id,{id,updated:Date.now(),messages:[],draft:"",attachment:null});
  await openConversation(id,false);
  persistConversation(); renderHistory();
}
async function initializeHistory() {
  conversationPrefix=`buck-history:v1:${new URL(".",location.href).pathname}:${base.replace(/\/$/,"")}:`;
  activeConversation=null; conversationCache.clear();
  const id=savedValue(conversationPrefix+"active");
  if (id && readConversation(id)) await openConversation(id,false);
  else await newConversation();
  for (const [button,content,label] of [["toggle-experiment","experiment-content","实验配置"],["toggle-history","history-content","历史会话"]]) {
    toggleSection(button,content,label,savedValue(`buck-panel:${new URL(".",location.href).pathname}:${button}`)!=="closed");
  }
}
