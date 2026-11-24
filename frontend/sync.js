/* Bidirectional private archive. The recovery code is a credential, never a URL. */
"use strict";
let archiveKey = "", archiveEnabled = false, archivePersistent = false;
let archiveTimer = null, archiveRunning = false, archiveAgain = false, archiveApplying = false;
let archiveBackground = false, archiveLastSuccess = 0;
let archiveBootstrap = false;
let archiveComposing = false, archiveDeferred = false, archivePreferRemote = false;
let archiveVerifyPending = false;
const archiveAuditInterval = 24*60*60*1000;
const archiveReceipts = new Map();
let archiveBatchTail = null;
const editedDuringArchive = new Set();
const archiveUncommitted = new Set();
let archivePreferredId = null, archiveBootstrapSnapshot = "";
function serverArchiveOnly() {
  // The hosted UI operates server-only even before its first health request.
  return Boolean(window.BUCK_CONFIG?.studioBackend || window.BUCK_CONFIG?.serverArchiveOnly
    || !["localhost","127.0.0.1","[::1]"].includes(location.hostname)
    || (typeof base==="string" && base && !isLocalBackend(base)));
}
function legacyRecordKeys(scope) {
  return Object.keys(localStorage).filter(k=>k===scope || k.startsWith(scope+":message:") || k.startsWith(scope+":experiment:"));
}
window.addEventListener("beforeunload",event=>{
  if (serverArchiveOnly() && archiveUncommitted.size) {event.preventDefault();event.returnValue="";}
});
// Only hashes and version receipts are stored here, never another copy of a CSV.
function archiveReceipt(id) {
  const key=conversationPrefix+"sync:"+id;
  try {return archiveReceipts.get(key) || JSON.parse(savedValue(key)) || {};} catch {return {};}
}
function archiveFileVersion(file) {return file.version || `${file.epoch || 0}:${file.blob.size}:${file.filename}`;}
function archiveFileStamp(files) {
  return JSON.stringify(files.map(f=>[f.sha256,archiveFileVersion(f)]).sort((a,b)=>a[0].localeCompare(b[0])));
}
function archiveCanonical(value) {
  const normalize=item=>Array.isArray(item) ? item.map(normalize) : item && typeof item==='object'
    ? Object.fromEntries(Object.keys(item).sort().map(key=>[key,normalize(item[key])])) : item;
  return JSON.stringify(normalize(value));
}
function archiveProgress(done,total,stage,label="") {
  syncStatus(`同步进度 ${done}/${total}：${stage}${label ? " · "+label : ""}`);
}
function archiveEditorActive() {
  const editor=document.activeElement;
  return archiveComposing || Boolean(editor?.matches("input,textarea,select") || editor?.isContentEditable);
}
function deferArchiveSync(preferRemote=false) {
  clearTimeout(archiveTimer);archiveTimer=null;
  archiveDeferred=true;archivePreferRemote ||= preferRemote;
  syncStatus("正在编辑，自动同步将在离开输入框后继续。");
}
function syncStatus(text) {
  const warning="同步恢复码无法保存，请复制保管；关闭页面后可能需要重新输入。";
  // A successful upload does not mean this browser remembered its recovery key.
  // Keep that warning visible until the browser can remember the code.
  const unremembered=archiveKey && savedValue(archiveKeyStorage())!==archiveKey;
  ui($("sync-status"),text+(unremembered && !text.includes(warning) ? "\n"+warning : ""));
}
async function csvDigest(blob) {
  return Array.from(new Uint8Array(await crypto.subtle.digest("SHA-256",await blob.arrayBuffer())),b=>b.toString(16).padStart(2,"0")).join("");
}
function archiveKeyStorage() { return `buck-sync-key:${new URL(".",location.href).pathname}:${base.replace(/\/$/,"")}`; }
async function archivePrefix(root) {
  archiveKey = savedValue(archiveKeyStorage());
  if (!/^[a-f0-9]{64}$/.test(archiveKey)) {
    archiveKey=Array.from(crypto.getRandomValues(new Uint8Array(32)),b=>b.toString(16).padStart(2,"0")).join("");
    try { localStorage.setItem(archiveKeyStorage(),archiveKey); }
    catch { syncStatus("同步恢复码无法保存，请复制保管；关闭页面后可能需要重新输入。"); }
  }
  const fingerprint=await csvDigest(new Blob([archiveKey]));
  let original=savedValue(root+"vault-owner");
  // Bind existing single-browser history to its first private vault. Selecting
  // another code uses a different local namespace, never uploads the old vault.
  if (!original) { original=fingerprint; try { localStorage.setItem(root+"vault-owner",original); } catch {} }
  return original===fingerprint ? root : root+"vault:"+fingerprint+":";
}
function scheduleArchiveSync() {
  if (!archiveEnabled || !state.connected) return;
  if (archiveApplying) { if (archiveBackground || serverArchiveOnly()) archiveAgain=true; return; }
  clearTimeout(archiveTimer);archiveTimer=null;
  if (!serverArchiveOnly() && archiveEditorActive()) {deferArchiveSync();return;}
  archiveTimer=setTimeout(()=>{archiveTimer=null;void synchronizeArchive({background:true});},1000);
}
function archiveRequest(path, options={}) {
  return api("/api/archive"+path,{timeout:30000,...options,archiveCode:archiveKey,archiveProtocol:"2"});
}
function wireMessage(m) {
  return {id:m.id,created:m.created,role:m.role,content:m.content,mode:m.mode || "",response:m.response || null,images:m.images || []};
}
function wireExperiment(run) {
  return {...run,dataset:{filename:run.dataset?.filename || "",sha256:run.dataset?.sha256 || ""},ground_truth:run.ground_truth || null};
}
function wireRecord(record, includeLegacy=true) {
  if (!record) return null;
  return {id:record.id,updated:record.updated,meta_updated:record.meta_updated || record.updated,
    file_epoch:record.file_epoch || 0,draft:record.draft || "",fields:record.fields || {},attachment:record.attachment || null,
    experiments:mergeExperiments(record.experiments,includeLegacy ? storedExperiments(record.id) : []).map(wireExperiment),
    messages:record.messages.map(wireMessage)};
}
function saveMergedRecord(record) {
  const local=readConversation(record.id);
  if (savedValue(conversationPrefix+"deleted:"+record.id)) return;
  // Another tab can append messages while this request is in flight. Merge
  // again on receipt, keeping the latest local fields and all immutable messages.
  if (local) {
    const messages=mergeMessages(record.id,local.messages,record.messages);
    const experiments=mergeExperiments(local.experiments,record.experiments);
    const file_epoch=Math.max(local.file_epoch || 0,record.file_epoch || 0);
    record={...((local.meta_updated || 0)>(record.meta_updated || 0) ? local : record),
      messages,experiments,file_epoch,updated:Math.max(local.updated,record.updated,...messages.map(m=>m.created))};
    if (record.attachment && (record.attachment.csvEpoch || 0)!==file_epoch) record.attachment=null;
  }
  // Persist the complete merged record before clearing any old state. A quota
  // failure leaves the original browser copy intact and is reported to the user.
  if (!serverArchiveOnly()) for (const message of record.messages) localStorage.setItem(conversationPrefix+record.id+":message:"+message.id,JSON.stringify(message));
  saveExperimentKeys(record);
  if (!serverArchiveOnly()) localStorage.setItem(conversationPrefix+record.id,JSON.stringify({...record,messages:[],messageStoreVersion:1}));
  conversationCache.set(record.id,record);
  return record;
}
async function removeLocalArchive(id) {
  const scope=conversationPrefix+id;
  localStorage.setItem(conversationPrefix+"deleted:"+id,"1");
  await BuckAttachments.remove(scope);
  for (const key of Object.keys(localStorage)) if (key===scope || key.startsWith(scope+":message:") || key.startsWith(scope+":experiment:")) localStorage.removeItem(key);
  conversationCache.delete(id);archiveUncommitted.delete(id);
  localStorage.removeItem(conversationPrefix+"sync:"+id);
  archiveReceipts.delete(conversationPrefix+"sync:"+id);
}
async function reconcileFiles(record, remoteFiles, {verify=false,receipt={},progress=()=>{}}={}) {
  const scope=conversationPrefix+record.id, epoch=record.file_epoch || 0;
  const current=()=>!savedValue(conversationPrefix+"deleted:"+record.id) && (readConversation(record.id)?.file_epoch || 0)===epoch;
  if (!current()) return {stale:true};
  await BuckAttachments.remove(scope,epoch);
  const local=new Map((await BuckAttachments.list(scope)).map(file=>[file.sha256,file]));
  const remote=new Map(remoteFiles.map(file=>[file.sha256,file]));
  const wanted=new Set([...local.keys(),...remote.keys()]);
  const attached=record.attachment?.dataset?.sha256;
  if (attached) wanted.add(attached);
  let missing=false;
  const verifiedFiles={};
  for (const sha of wanted) {
    if (!current()) return {stale:true};
    const own=local.get(sha), other=remote.get(sha);
    progress(verify ? "校验 CSV" : "核对文件版本",own?.filename || other?.filename || sha.slice(0,12));
    const valid=own && ((!verify && receipt.verifiedFiles?.[sha]===archiveFileVersion(own)) || await csvDigest(own.blob)===sha);
    if (valid) verifiedFiles[sha]=archiveFileVersion(own);
    if (valid && !other?.valid) {
      progress("上传 CSV",own.filename);
      const form=new FormData();form.append("file",own.blob,own.filename);
      const ack=await archiveRequest(`/conversations/${record.id}/files/${sha}?epoch=${epoch}`,{method:"POST",body:form,timeout:90000});
      if (serverArchiveOnly() && ack?.sha256!==sha) throw new Error("服务器尚未确认 CSV 校验值。");
      if (serverArchiveOnly() && archivePersistent && current()) await BuckAttachments.acknowledged(own);
    } else if (serverArchiveOnly() && other?.valid) {
      if (own && other.verified && archivePersistent && current()) await BuckAttachments.acknowledged(own);
    } else if (!valid && other?.valid) {
      progress("下载 CSV",other.filename);
      const blob=await archiveRequest(`/conversations/${record.id}/files/${sha}`,{asBlob:true,timeout:90000});
      if (await csvDigest(blob)!==sha) throw new Error("后端 CSV 校验失败，同步未完成。");
      if (!current()) return {stale:true};
      const saved=await BuckAttachments.save(scope,sha,other.filename,blob,epoch);
      if (!saved) return {stale:true};
      local.set(sha,saved);verifiedFiles[sha]=archiveFileVersion(saved);
    } else if (!valid) missing=true;
  }
  return {missing,files:archiveFileStamp([...local.values()]),verifiedFiles};
}
async function synchronizeArchive({preferRemote=false,background=false,currentFirst=false,verify=false}={}) {
  clearTimeout(archiveTimer);archiveTimer=null;
  if (!archiveEnabled || !state.connected) return;
  archiveVerifyPending ||= verify;
  verify ||= archiveVerifyPending;
  if (!serverArchiveOnly() && background && archiveEditorActive()) {deferArchiveSync(preferRemote);return;}
  if (archiveRunning) { archiveAgain=true;archivePreferRemote ||= preferRemote;return; }
  if (!(serverArchiveOnly() && background) && (historyLocked() || document.getElementById("circuit-dialog")?.open || !document.getElementById("csv-library-menu").hidden)) { archivePreferRemote ||= preferRemote;scheduleArchiveSync(); return; }
  preferRemote ||= archivePreferRemote;
  archiveDeferred=false;archivePreferRemote=false;
  editedDuringArchive.clear();
  archiveRunning=true; archiveApplying=true; archiveBackground=background;
  // Automatic replication never locks the editor or reloads the active view.
  // Explicit sync / initial recovery may still reconcile the visible UI.
  state.syncBusy=!background; controls();
  const previousId=activeConversation?.id;
  const before=activeConversation ? JSON.stringify(wireRecord(activeConversation)) : "";
  let missing=false, failed=false, reopened=false, deferred=false, retained=false;
  function pauseForActiveTask() {
    if (serverArchiveOnly() || !background || !historyLocked()) return false;
    archiveAgain=true;
    archivePreferRemote ||= preferRemote;
    syncStatus("正在执行任务，剩余会话将在任务结束后继续同步。");
    return true;
  }
  syncStatus("正在读取备份版本目录…");
  try {
    // Input/import handlers have already saved local edits. Do not synthesize a
    // new metadata edit from a stale tab's untouched DOM during background sync.
    renderHistory();
    const remote=await archiveRequest("");
    const remoteIds=new Map(remote.conversations.map(item=>[item.id,item]));
    const ids=new Set([...conversationCache.keys(),...remoteIds.keys()]);
    for (const key of Object.keys(localStorage)) if (key.startsWith(conversationPrefix+"deleted:")) ids.add(key.slice((conversationPrefix+"deleted:").length));
    // Do not publish a bootstrap blank conversation when restoring an existing vault.
    if (preferRemote && archiveBootstrap && remote.conversations.some(item=>!item.deleted) && activeConversation && !activeConversation.messages.length && !activeConversation.draft && !activeConversation.attachment
        && (!serverArchiveOnly() || archiveCanonical(wireRecord(readConversation(activeConversation.id)))===archiveBootstrapSnapshot)) {
      ids.delete(activeConversation.id);
      localStorage.removeItem(conversationPrefix+activeConversation.id);
      conversationCache.delete(activeConversation.id);
      archiveUncommitted.delete(activeConversation.id);
    }
    const preferred=(preferRemote && archivePreferredId) || previousId;
    const continuing=background && !verify && !preferRemote && archiveBatchTail;
    const batch=continuing ? [...archiveBatchTail].filter(id=>ids.has(id)) : [...ids];
    archiveBatchTail=null;
    const ordered=batch.sort((a,b)=>Number(b===preferred)-Number(a===preferred) || (remoteIds.get(b)?.updated || 0)-(remoteIds.get(a)?.updated || 0));
    let done=0, visited=0, foregroundHandled=false;
    const versioned=remote.conversations.every(item=>item.deleted || item.revision);
    for (const id of ordered) {
      // Release initial recovery after one conversation; the rest resumes with receipts.
      if ((currentFirst && foregroundHandled) || (background && versioned && !verify && done>=5)) {
        archiveBatchTail=new Set(ordered.slice(visited));
        // Edits made while this batch was in flight also need another pass.
        for (const edited of editedDuringArchive) archiveBatchTail.add(edited);
        deferred=true;archiveAgain=true;break;
      }
      visited++;
      // An analysis may have started while the previous HTTP request was in flight.
      if (pauseForActiveTask()) return;
      const deleted=savedValue(conversationPrefix+"deleted:"+id);
      if (deleted || remoteIds.get(id)?.deleted) {
        if (!remoteIds.get(id)?.deleted) await archiveRequest(`/conversations/${id}`,{method:"DELETE"});
        // A late IndexedDB write can outlive deletion and cache eviction.
        // Tombstones must clean up even when no conversation remains in memory.
        await removeLocalArchive(id);
        continue;
      }
      foregroundHandled=true;
      const local=readConversation(id);
      const receipt=archiveReceipt(id), remoteVersion=remoteIds.get(id)?.revision;
      const planKey=`buck-plan:${base}:${conversationPrefix}:${id}`;
      const legacyPlan=serverArchiveOnly() ? sessionStorage.getItem(planKey) : null;
      if (legacyPlan && local && !local.fields?.["identification-plan-state"]) {
        try {
          if (validStoredIdentificationPlan(JSON.parse(legacyPlan))) {
            local.fields={...local.fields,"identification-plan-state":legacyPlan};
            if (activeConversation?.id===id) $("identification-plan-state").value=legacyPlan;
          }
        } catch { /* Preserve unrecognized legacy plan data. */ }
      }
      const wire=local ? wireRecord(local) : null;
      const localText=archiveCanonical(wire);
      const localHash=wire ? await csvDigest(new Blob([localText])) : null;
      const localFiles=await BuckAttachments.list(conversationPrefix+id);
      const audit=verify || Boolean(receipt.checkedAt && Date.now()-receipt.checkedAt>=archiveAuditInterval);
      const legacy=serverArchiveOnly() ? legacyRecordKeys(conversationPrefix+id).map(k=>[k,localStorage.getItem(k)]) : [];
      if (!legacy.length && !legacyPlan && !(serverArchiveOnly() && localFiles.length) && !audit && (!receipt.missing || Date.now()-receipt.attemptedAt<30000) && remoteVersion && receipt.remote===remoteVersion && receipt.local===localHash && receipt.files===archiveFileStamp(localFiles)) {
        missing=receipt.missing || missing;
        if(!receipt.missing && archivePersistent && serverArchiveOnly() && archiveCanonical(wireRecord(readConversation(id)))===localText)
          archiveUncommitted.delete(id);
        continue;
      }
      done++;
      archiveProgress(visited,ordered.length,"合并会话",id.slice(0,8));
      const query=`?verify=${audit || (serverArchiveOnly() && localFiles.length>0)}`;
      const merged=local
        ? receipt.local===localHash && remoteVersion
          ? await archiveRequest(`/conversations/${id}${query}`)
          : await archiveRequest(`/conversations/${id}${query}`,{method:"POST",body:wire})
        : await archiveRequest(`/conversations/${id}${query}`);
      if (merged.deleted) { await removeLocalArchive(id);foregroundHandled=false;continue; }
      // Compare with the exact local snapshot sent/read before the await. Server
      // default normalization is not a new edit; concurrent local writes are.
      const latestLocal=readConversation(id);
      const dirty=archiveCanonical(latestLocal ? wireRecord(latestLocal) : null)!==localText;
      const saved=saveMergedRecord(merged.record);
      if (!saved) { archiveAgain=true;foregroundHandled=false;continue; }
      const savedWire=archiveCanonical(wireRecord(saved));
      if (dirty) archiveAgain=true;
      // A newer local Clear CSV must not restore files from the older response generation.
      if (pauseForActiveTask()) return;
      const files=await reconcileFiles(saved,(saved.file_epoch || 0)===(merged.record.file_epoch || 0) ? merged.files : [],
        {verify:audit,receipt,progress:(stage,label)=>archiveProgress(visited,ordered.length,stage,label)});
      missing=files.missing || missing;
      if (files.stale) {archiveAgain=true;continue;}
      const remoteWire=wireRecord(merged.record,false);
      const confirmed=archiveCanonical(remoteWire)===savedWire;
      if (serverArchiveOnly() && !confirmed && !dirty) retained=true;
      if (!dirty && (!serverArchiveOnly() || confirmed)) {
        const checkpoint={remote:merged.revision,local:await csvDigest(new Blob([savedWire])),files:files.files,
          verifiedFiles:files.verifiedFiles,missing:files.missing,attemptedAt:Date.now(),checkedAt:audit || !receipt.checkedAt ? Date.now() : receipt.checkedAt};
        archiveReceipts.set(conversationPrefix+"sync:"+id,checkpoint);
        if (serverArchiveOnly() && confirmed && archivePersistent && !files.missing && archiveCanonical(wireRecord(readConversation(id)))===savedWire) {
          archiveUncommitted.delete(id);
          const remoteMessages=new Map(remoteWire.messages.map(m=>[m.id,archiveCanonical(m)]));
          const remoteRuns=new Map(remoteWire.experiments.map(r=>[r.id,archiveCanonical(r)]));
          if (legacyPlan && remoteWire.fields["identification-plan-state"]===legacyPlan && sessionStorage.getItem(planKey)===legacyPlan)
            sessionStorage.removeItem(planKey);
          for (const [key,value] of legacy) {
            if (localStorage.getItem(key)!==value) {retained=true;continue;}
            try {
              const old=JSON.parse(value), scope=conversationPrefix+id;
              const matchesMessage=m=>remoteMessages.get(m.id)===archiveCanonical(wireMessage(m));
              const matchesRun=r=>remoteRuns.get(r.id)===archiveCanonical(wireExperiment(r));
              // Verify the old record itself. A merge may already have replaced a
              // conflicting message with the same ID or discarded old metadata.
              let known;
              if (key===scope) {
                known=old?.id===id && Array.isArray(old.messages)
                  && (old.draft || "")===remoteWire.draft
                  && Object.entries(old.fields || {}).every(([k,v])=>remoteWire.fields[k]===v)
                  && archiveCanonical(old.attachment || null)===archiveCanonical(remoteWire.attachment)
                  && old.messages.every((m,index)=>matchesMessage({...m,id:m.id || `${id}-legacy-${index}`,created:m.created ?? index}))
                  && (old.experiments || []).every(matchesRun);
              } else {
                known=key.startsWith(scope+":message:") ? matchesMessage(old) : matchesRun(old);
              }
              if (known) localStorage.removeItem(key);
              else retained=true;
            } catch {retained=true;}
          }
        }
        try {if (!serverArchiveOnly()) localStorage.setItem(conversationPrefix+"sync:"+id,JSON.stringify(checkpoint));} catch { /* Memory receipt is valid only for this tab. */ }
      }
      await new Promise(resolve=>setTimeout(resolve,0));
    }
    archiveBootstrap=false; archivePreferredId=null;
    // A new audit requested during an ordinary sync belongs to the next pass.
    if (!deferred && verify) archiveVerifyPending=false;
    if (!deferred) archiveLastSuccess=Date.now();
    syncStatus(retained ? "已同步，部分旧记录未能核对，仍保留在浏览器中。" : deferred ? "当前会话已处理，其余备份正在后台继续…" : missing ? "对话与实验记录已同步；部分 CSV 两端均无有效副本，请重新上传。" : archiveAgain ? "本轮同步完成，新修改将继续备份。" : archivePersistent ? "对话、实验记录和 CSV 已同步" : (serverArchiveOnly() ? "已写入服务器，但尚未确认持久存储。请检查服务器挂载。" : "已同步。后端尚未确认归档目录使用持久存储，重启后可能需要从浏览器恢复。"));
  } catch(error) {
    failed=true;
    syncStatus((serverArchiveOnly() ? "服务器尚未保存，请保持页面打开。连接恢复后会重试：" : "同步未完成，本地数据保留；连接恢复后会重试：")+error.message);
  } finally {
    state.syncBusy=false;
    try {
      // A later file/network failure must not leave already-merged metadata
      // behind an old editor, whose next input would overwrite the newer copy.
      renderHistory();
      if (!background || (preferRemote && previousId && !conversationCache.has(previousId) && activeConversation?.id===previousId)) {
        let active=conversationCache.get(previousId);
        if (!active) active=[...conversationCache.values()].sort((a,b)=>b.updated-a.updated)[0];
        if (active && (active.id!==previousId || JSON.stringify(wireRecord(active))!==before || (!state.dataset && active.attachment))) {
          await openConversation(active.id,false);
          reopened=true;
        }
        else if (!active) { activeConversation=null; resetExperiment(); clearChat(); await newConversation(); }
      } else if (activeConversation && savedValue(conversationPrefix+"deleted:"+activeConversation.id)) {
        // Only an explicit deletion received from another client may close a view.
        // Wait until ongoing work / typing finishes before applying it.
        if (!historyLocked() && !archiveEditorActive()) {
          activeConversation=null;resetExperiment();clearChat();await newConversation();
        } else archiveAgain=true;
      }
      renderHistory();
    } finally { archiveApplying=false;archiveRunning=false;archiveBackground=false;controls(); }
    if (failed) {
      archiveAgain=false;
      archiveTimer=setTimeout(()=>{archiveTimer=null;void synchronizeArchive({background:true,preferRemote});},30000);
    } else if (archiveAgain) { archiveAgain=false;scheduleArchiveSync(); }
  }
  return reopened;
}
async function recoverCsvFromArchive(dataset) {
  if (!archiveEnabled) return null;
  try {
    const blob=await archiveRequest(`/conversations/${activeConversation.id}/files/${dataset.sha256}`,{asBlob:true,timeout:90000});
    if (await csvDigest(blob)!==dataset.sha256) return null;
    try { if (!serverArchiveOnly()) await BuckAttachments.save(csvScope(),dataset.sha256,dataset.filename,blob,activeConversation.file_epoch || 0); } catch {}
    return blob;
  } catch (error) {
    if (error.status===404 || error.status===409) return null;
    throw error;
  }
}
window.addEventListener("online",()=>{scheduleArchiveSync();});
document.addEventListener("visibilitychange",()=>{
  if(document.visibilityState==="visible" && Date.now()-archiveLastSuccess>=60000)scheduleArchiveSync();
});
// Local input handlers save every edit. Avoid unnecessary network work while
// typing; an already-running background copy never disables or replaces the editor.
document.addEventListener("focusin",()=>{
  if (!serverArchiveOnly() && archiveTimer!==null && archiveEditorActive() && archiveEnabled && state.connected) deferArchiveSync();
});
document.addEventListener("focusout",()=>{queueMicrotask(()=>{if(archiveDeferred)scheduleArchiveSync();});});
document.addEventListener("compositionstart",()=>{archiveComposing=true;});
document.addEventListener("compositionend",()=>{archiveComposing=false;if(archiveDeferred)scheduleArchiveSync();});
