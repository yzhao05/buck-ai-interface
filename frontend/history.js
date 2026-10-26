/* Conversation metadata in localStorage; original CSV blobs in IndexedDB. */
"use strict";
let activeConversation = null;
let conversationPrefix = "";
let pendingAttachment = null;
const conversationCache = new Map();
const experimentFields = ["vin", "frequency", "fit-mode", "fixed-r", "fit-method", "pann-state", "pann-cycles", "pann-epochs", "start-ms", "end-ms", "train-fraction", "pwm-source", "controller-confirmed", "controller-parameters", "controller-image"];
const experimentDefaults = {vin:"28", frequency:"100", "fit-mode":"LCR", "fixed-r":"6", "fit-method":"pann", "pann-state":"joint", "pann-cycles":"0", "pann-epochs":"100", "start-ms":"", "end-ms":"", "train-fraction":"70", "pwm-source":"auto", "controller-confirmed":"no"};
function historyWarning(text = "") {
  ui($("history-status"), text); $("history-status").hidden = !text;
}
function historyLocked() { return state.busy || state.chatBusy || state.syncBusy || Boolean(state.job); }
function csvScope() { return conversationPrefix + activeConversation.id; }
function csvWarning(text = "") {
  ui($("csv-storage-status"), text); $("csv-storage-status").hidden = !text;
}
async function archiveCurrentCsv(file = null) {
  if (!state.dataset) return;
  try {
    const scope = csvScope(), dataset = state.dataset;
    if (!file) {
      const stored = await window.BuckAttachments.load(scope, dataset.sha256, activeConversation.file_epoch || 0);
      if (stored && await csvDigest(stored.blob)===dataset.sha256) { state.csvFile = stored.blob; csvWarning(); return; }
      file = await api(`/api/datasets/${dataset.dataset_id}/csv`, {asBlob:true});
    }
    state.csvFile = file;
    await window.BuckAttachments.save(scope, dataset.sha256, dataset.filename, file, activeConversation.file_epoch || 0);
    csvWarning();
  } catch {
    csvWarning("CSV 未能保存到浏览器，请检查存储空间与权限。后端备份请查看同步状态；确认有有效副本前，请保留原文件。");
  }
}
async function ensureDatasetAvailable() {
  if (!state.dataset) return;
  try { await api(`/api/datasets/${state.dataset.dataset_id}`); return; }
  catch (error) { if (error.status !== 404) throw error; }
  const previous = state.dataset;
  let blob = state.csvFile;
  if (!blob) {
    try { blob = (await window.BuckAttachments.load(csvScope(), previous.sha256, activeConversation.file_epoch || 0))?.blob; }
    catch { /* Report a recoverable attachment error below. */ }
  }
  const corrupt=blob && await csvDigest(blob)!==previous.sha256;
  if (!blob || corrupt) blob=await recoverCsvFromArchive(previous);
  if (!blob) throw new Error("暂时无法从浏览器或后端恢复 CSV。请检查连接并重试同步；若两端均无有效副本，请重新上传原文件。");
  ui($("job-status"), "正在从历史会话恢复 CSV…");
  const form = new FormData(); form.append("file", blob, previous.filename);
  const uploaded = await api("/api/datasets", {method:"POST", body:form});
  if (uploaded.sha256 !== previous.sha256) throw new Error("恢复后的 CSV 与原文件不一致，请重新上传。");
  state.dataset = uploaded; state.csvFile = blob;
  invalidateResult();
  persistConversation();
}
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
    if (localStorage.getItem(conversationPrefix+"deleted:"+id)) { conversationCache.delete(id); return null; }
    const cached=conversationCache.get(id), disk=JSON.parse(localStorage.getItem(conversationPrefix + id));
    const record = disk && (!cached || (disk.meta_updated || 0)>(cached.meta_updated || 0)) ? disk : cached || disk;
    if (!record || record.id !== id || !Number.isFinite(new Date(record.updated).getTime()) || !Array.isArray(record.messages) || !record.messages.every(m =>
      ["user","assistant"].includes(m.role) && typeof m.content === "string")) return null;
    record.messages=mergeMessages(id,cached?.messages || [],record.messages,storedMessages(id));
    record.experiments=mergeExperiments(cached?.experiments,disk?.experiments,record.experiments,storedExperiments(id));
    record.updated=record.messages.reduce((latest,m)=>Math.max(latest,m.created),record.updated);
    conversationCache.set(id, record); return record;
  } catch { return conversationCache.get(id) || null; }
}
function persistConversation() {
  if (!activeConversation) return;
  if (savedValue(conversationPrefix+"deleted:"+activeConversation.id)) return;
  const previousMeta=JSON.stringify([activeConversation.draft,activeConversation.fields,activeConversation.attachment]);
  activeConversation.draft = $("chat-input").value;
  activeConversation.fields = Object.fromEntries(experimentFields.map(id => [id, $(id).value]));
  activeConversation.attachment = state.dataset ? {dataset:state.dataset, mapping:state.mapping,
    mappingDraft:mappingFromForm(), resultId:state.resultId, jobId:state.job, csvEpoch:activeConversation.file_epoch || 0,
    truthDraft:{...state.truthDraft},truthComparison:state.truthComparison} : pendingAttachment;
  if (previousMeta!==JSON.stringify([activeConversation.draft,activeConversation.fields,activeConversation.attachment])) {
    activeConversation.meta_updated=Math.max(Date.now(),(activeConversation.meta_updated || 0)+1);
  }
  conversationCache.set(activeConversation.id, activeConversation);
  try {
    captureExperiment();
    saveExperimentKeys(activeConversation);
    // Append-only message keys prevent an older tab from replacing newer messages.
    activeConversation.messages=mergeMessages(activeConversation.id,activeConversation.messages);
    for (const item of activeConversation.messages) {
      const key=conversationPrefix+activeConversation.id+":message:"+item.id;
      if (!localStorage.getItem(key)) localStorage.setItem(key,JSON.stringify(item));
    }
    activeConversation.messages=mergeMessages(activeConversation.id,activeConversation.messages,storedMessages(activeConversation.id));
    activeConversation.updated=activeConversation.messages.reduce((latest,m)=>Math.max(latest,m.created),activeConversation.updated);
    const disk=JSON.parse(localStorage.getItem(conversationPrefix+activeConversation.id));
    let saved=activeConversation;
    if (disk && (disk.meta_updated || 0)>(activeConversation.meta_updated || 0)) {
      // A background save/pagehide in an untouched old tab must not overwrite
      // newer CSV mappings or settings from another tab.
      saved={...activeConversation,draft:disk.draft,fields:disk.fields,attachment:disk.attachment,
        meta_updated:disk.meta_updated,file_epoch:Math.max(disk.file_epoch || 0,activeConversation.file_epoch || 0)};
      conversationCache.set(saved.id,saved);
    }
    localStorage.setItem(conversationPrefix + activeConversation.id, JSON.stringify({...saved,messages:[],messageStoreVersion:1}));
    localStorage.setItem(conversationPrefix + "active", activeConversation.id);
    historyWarning();
  } catch { historyWarning("历史保存失败：浏览器存储不可用或已满。当前对话仍可使用，请勿关闭页面。"); }
  scheduleArchiveSync();
  renderExperiments();
  renderAnalysisHistory();
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
    if (savedValue(conversationPrefix+"deleted:"+record.id)) { conversationCache.delete(record.id); continue; }
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
  const item={id:crypto.randomUUID(),created,role,content,response,mode};
  activeConversation.messages.push(item);
  activeConversation.updated = Date.now();
  state.history = activeConversation.messages.filter(m => m.mode !== "请求未完成")
    .map(m=>({role:m.role,content:m.content.slice(0,8000)})).slice(-10);
  persistConversation(); renderHistory();
  return item;
}
function resetExperiment() {
  closeCsvLibrary();
  pendingAttachment=null;
  state.csvFile=null; csvWarning();
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
    await ensureDatasetAvailable();
    await archiveCurrentCsv();
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
          state.result=job.result; state.resultId=jobId; restoreTruthComparison(attachment);displayResult();
        }
        else if (["running","queued"].includes(job.status)) void watchJob(jobId);
      } catch { /* CSV can remain usable after a result expires. */ }
    }
  } catch (error) {
    resetExperiment();
    pendingAttachment=attachment;
    ui($("chat-context"),"历史 CSV 需重新关联");
    notice(error.status === 404 ? "历史对话已恢复；CSV 尚未恢复，请重试同步或重新上传原文件。" : error.message);
  } finally { state.busy=false; persistConversation(); controls(); }
}
async function deleteConversation() {
  if (historyLocked() || !activeConversation) return;
  if (!window.confirm(tr("删除当前会话、实验记录及其在浏览器和后端保存的 CSV？离线时后端删除将在重连后同步。此操作无法撤销。"))) return;
  state.busy=true; controls();
  try {
    const id=activeConversation.id, scope=csvScope();
    await window.BuckAttachments.remove(scope);
    // Commit deletion only after the file transaction succeeds. Older tabs
    // observe the tombstone and cannot recreate metadata; they also clear any
    // files an in-flight upload may have saved while this tab was deleting.
    localStorage.setItem(conversationPrefix+"deleted:"+id,"1");
    const keys=[];
    for (let i=0;i<localStorage.length;i++) {
      const key=localStorage.key(i);
      if (key===scope || key?.startsWith(scope+":message:") || key?.startsWith(scope+":experiment:")) keys.push(key);
    }
    keys.forEach(key=>localStorage.removeItem(key));
    // Deletion is local-first; an offline backend cache will expire by itself.
    if (state.connected && state.dataset) {
      try { await api(`/api/datasets/${state.dataset.dataset_id}`,{method:"DELETE",timeout:5000}); } catch { /* cache TTL */ }
    }
    conversationCache.delete(id); activeConversation=null;
    resetExperiment(); clearChat();
  } catch (error) { notice(error.message); }
  finally { state.busy=false; controls(); }
  if (!activeConversation) await newConversation();
}
async function openConversation(id, saveCurrent = true) {
  if (historyLocked()) return;
  if (saveCurrent) persistConversation();
  const record=readConversation(id);
  if (!record) return;
  activeConversation=record; sessionId=record.id;
  notice(); resetExperiment(); clearChat();
  pendingAttachment=record.attachment;
  for (const field of experimentFields) $(field).value=record.fields?.[field] ?? experimentDefaults[field] ?? "";
  window.BuckCircuit.render();
  // A legacy "integral" value is no longer in the selector. The new identifier
  // also prevents restoreAttachment from relabelling an old result as weighted.
  if (!$("fit-method").value || (state.connected && $("fit-method").selectedOptions[0]?.disabled)) $("fit-method").value="weighted_integral";
  $("fixed-r-label").hidden=$("fit-mode").value!=="LC";
  ui($("train-label"),$("train-fraction").value+"%");
  $("chat-input").value=record.draft || "";
  for (const item of record.messages) {
    const article=message(item.role,item.content,item.mode);
    article.dataset.messageId=item.id;
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
  await recoverExperiments();
  renderExperiments();
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
  clearTimeout(archiveTimer);
  conversationPrefix=await archivePrefix(`buck-history:v1:${new URL(".",location.href).pathname}:${base.replace(/\/$/,"")}:`);
  activeConversation=null; conversationCache.clear();
  const id=savedValue(conversationPrefix+"active");
  archiveBootstrap=!id;
  if (id && readConversation(id)) await openConversation(id,false);
  else await newConversation();
  for (const [button,content,label] of [["toggle-experiment","experiment-content","实验配置"],["toggle-history","history-content","历史会话"]]) {
    toggleSection(button,content,label,savedValue(`buck-panel:${new URL(".",location.href).pathname}:${button}`)!=="closed");
  }
}

window.addEventListener("storage", async event => {
  const marker=conversationPrefix+"deleted:";
  if (!event.key?.startsWith(marker) || !event.newValue) return;
  const id=event.key.slice(marker.length);
  conversationCache.delete(id);
  if (activeConversation?.id!==id) { renderHistory(); return; }
  while (historyLocked()) await new Promise(resolve=>setTimeout(resolve,100));
  if (activeConversation?.id!==id) return;
  try { await window.BuckAttachments.remove(conversationPrefix+id); } catch { /* retry on explicit deletion */ }
  conversationCache.delete(id); activeConversation=null;
  resetExperiment(); clearChat(); await newConversation();
});
