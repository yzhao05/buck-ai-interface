/* Bidirectional private archive. The recovery code is a credential, never a URL. */
"use strict";
let archiveKey = "", archiveEnabled = false, archivePersistent = true;
let archiveTimer = null, archiveRunning = false, archiveAgain = false, archiveApplying = false;
let archiveBackground = false, archiveLastSuccess = 0;
let archiveBootstrap = false;
let archiveComposing = false, archiveDeferred = false, archivePreferRemote = false;
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
  if (archiveApplying) { if (archiveBackground) archiveAgain=true; return; }
  clearTimeout(archiveTimer);archiveTimer=null;
  if (archiveEditorActive()) {deferArchiveSync();return;}
  archiveTimer=setTimeout(()=>{archiveTimer=null;void synchronizeArchive({background:true});},1000);
}
function archiveRequest(path, options={}) {
  return api("/api/archive"+path,{...options,archiveCode:archiveKey});
}
function wireRecord(record) {
  return {id:record.id,updated:record.updated,meta_updated:record.meta_updated || record.updated,
    file_epoch:record.file_epoch || 0,draft:record.draft || "",fields:record.fields || {},attachment:record.attachment || null,
    experiments:mergeExperiments(record.experiments,storedExperiments(record.id)),
    messages:record.messages.map(m=>({id:m.id,created:m.created,role:m.role,content:m.content,mode:m.mode || "",response:m.response || null,images:m.images || []}))};
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
  for (const message of record.messages) localStorage.setItem(conversationPrefix+record.id+":message:"+message.id,JSON.stringify(message));
  saveExperimentKeys(record);
  localStorage.setItem(conversationPrefix+record.id,JSON.stringify({...record,messages:[],messageStoreVersion:1}));
  conversationCache.set(record.id,record);
  return record;
}
async function removeLocalArchive(id) {
  const scope=conversationPrefix+id;
  localStorage.setItem(conversationPrefix+"deleted:"+id,"1");
  await BuckAttachments.remove(scope);
  for (const key of Object.keys(localStorage)) if (key===scope || key.startsWith(scope+":message:") || key.startsWith(scope+":experiment:")) localStorage.removeItem(key);
  conversationCache.delete(id);
}
async function reconcileFiles(record, remoteFiles) {
  const scope=conversationPrefix+record.id, epoch=record.file_epoch || 0;
  const current=()=>!savedValue(conversationPrefix+"deleted:"+record.id) && (readConversation(record.id)?.file_epoch || 0)===epoch;
  if (!current()) return false;
  await BuckAttachments.remove(scope,epoch);
  const local=new Map((await BuckAttachments.list(scope)).map(file=>[file.sha256,file]));
  const remote=new Map(remoteFiles.map(file=>[file.sha256,file]));
  const wanted=new Set([...local.keys(),...remote.keys()]);
  const attached=record.attachment?.dataset?.sha256;
  if (attached) wanted.add(attached);
  let missing=false;
  for (const sha of wanted) {
    if (!current()) return false;
    const own=local.get(sha), other=remote.get(sha);
    const valid=own && await csvDigest(own.blob)===sha;
    if (valid && !other?.valid) {
      const form=new FormData();form.append("file",own.blob,own.filename);
      await archiveRequest(`/conversations/${record.id}/files/${sha}?epoch=${epoch}`,{method:"POST",body:form});
    } else if (!valid && other?.valid) {
      const blob=await archiveRequest(`/conversations/${record.id}/files/${sha}`,{asBlob:true});
      if (await csvDigest(blob)!==sha) throw new Error("后端 CSV 校验失败，同步未完成。");
      if (!current()) return false;
      await BuckAttachments.save(scope,sha,other.filename,blob,epoch);
    } else if (!valid) missing=true;
  }
  return missing;
}
async function synchronizeArchive({preferRemote=false,background=false}={}) {
  clearTimeout(archiveTimer);archiveTimer=null;
  if (!archiveEnabled || !state.connected) return;
  if (background && archiveEditorActive()) {deferArchiveSync(preferRemote);return;}
  if (archiveRunning) { archiveAgain=true;archivePreferRemote ||= preferRemote;return; }
  if (historyLocked() || document.getElementById("circuit-dialog")?.open || !document.getElementById("csv-library-menu").hidden) { archivePreferRemote ||= preferRemote;scheduleArchiveSync(); return; }
  preferRemote ||= archivePreferRemote;
  archiveDeferred=false;archivePreferRemote=false;
  archiveRunning=true; archiveApplying=true; archiveBackground=background;
  // Automatic replication never locks the editor or reloads the active view.
  // Explicit sync / initial recovery may still reconcile the visible UI.
  state.syncBusy=!background; controls();
  const previousId=activeConversation?.id;
  const before=activeConversation ? JSON.stringify(wireRecord(activeConversation)) : "";
  let missing=false, failed=false;
  syncStatus("正在核对并同步对话、实验记录和 CSV…");
  try {
    // Input/import handlers have already saved local edits. Do not synthesize a
    // new metadata edit from a stale tab's untouched DOM during background sync.
    renderHistory();
    const remote=await archiveRequest("");
    const remoteIds=new Map(remote.conversations.map(item=>[item.id,item]));
    const ids=new Set([...conversationCache.keys(),...remoteIds.keys()]);
    for (const key of Object.keys(localStorage)) if (key.startsWith(conversationPrefix+"deleted:")) ids.add(key.slice((conversationPrefix+"deleted:").length));
    // Do not publish a bootstrap blank conversation when restoring an existing vault.
    if (preferRemote && archiveBootstrap && remote.conversations.some(item=>!item.deleted) && activeConversation && !activeConversation.messages.length && !activeConversation.draft && !activeConversation.attachment) {
      ids.delete(activeConversation.id);
      localStorage.removeItem(conversationPrefix+activeConversation.id);
      conversationCache.delete(activeConversation.id);
    }
    for (const id of ids) {
      const deleted=savedValue(conversationPrefix+"deleted:"+id);
      if (deleted || remoteIds.get(id)?.deleted) {
        if (!remoteIds.get(id)?.deleted) await archiveRequest(`/conversations/${id}`,{method:"DELETE"});
        await removeLocalArchive(id);continue;
      }
      const local=readConversation(id);
      const merged=local
        ? await archiveRequest(`/conversations/${id}`,{method:"POST",body:wireRecord(local)})
        : await archiveRequest(`/conversations/${id}`);
      if (merged.deleted) { await removeLocalArchive(id);continue; }
      const saved=saveMergedRecord(merged.record);
      if (!saved) { archiveAgain=true;continue; }
      if (JSON.stringify(wireRecord(saved))!==JSON.stringify(wireRecord(merged.record))) archiveAgain=true;
      // A newer local Clear CSV must not restore files from the older response generation.
      missing=(await reconcileFiles(saved,(saved.file_epoch || 0)===(merged.record.file_epoch || 0) ? merged.files : [])) || missing;
    }
    archiveBootstrap=false; archiveLastSuccess=Date.now();
    syncStatus(missing ? "对话与实验记录已同步；部分 CSV 两端均无有效副本，请重新上传。" : archiveAgain ? "本轮同步完成，新修改将继续备份。" : archivePersistent ? "对话、实验记录和 CSV 已同步" : "已同步；Render 尚未配置持久磁盘，后端副本可能随重启丢失。");
  } catch(error) {
    failed=true;
    syncStatus("同步未完成，本地数据保留；连接恢复后会重试："+error.message);
  } finally {
    state.syncBusy=false;
    try {
      // A later file/network failure must not leave already-merged metadata
      // behind an old editor, whose next input would overwrite the newer copy.
      renderHistory();
      if (!background) {
        let active=conversationCache.get(previousId);
        if (!active) active=[...conversationCache.values()].sort((a,b)=>b.updated-a.updated)[0];
        if (active && (active.id!==previousId || JSON.stringify(wireRecord(active))!==before || (!state.dataset && active.attachment))) await openConversation(active.id,false);
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
    if (archiveAgain) { archiveAgain=false;scheduleArchiveSync(); }
    else if (failed) archiveTimer=setTimeout(()=>{archiveTimer=null;void synchronizeArchive({background:true,preferRemote});},30000);
  }
}
async function recoverCsvFromArchive(dataset) {
  if (!archiveEnabled) return null;
  try {
    const blob=await archiveRequest(`/conversations/${activeConversation.id}/files/${dataset.sha256}`,{asBlob:true});
    if (await csvDigest(blob)!==dataset.sha256) return null;
    try { await BuckAttachments.save(csvScope(),dataset.sha256,dataset.filename,blob,activeConversation.file_epoch || 0); } catch {}
    return blob;
  } catch { return null; }
}
window.addEventListener("online",()=>{scheduleArchiveSync();});
document.addEventListener("visibilitychange",()=>{
  if(document.visibilityState==="visible" && Date.now()-archiveLastSuccess>=60000)scheduleArchiveSync();
});
// Local input handlers save every edit. Avoid unnecessary network work while
// typing; an already-running background copy never disables or replaces the editor.
document.addEventListener("focusin",()=>{
  if (archiveTimer!==null && archiveEditorActive() && archiveEnabled && state.connected) deferArchiveSync();
});
document.addEventListener("focusout",()=>{queueMicrotask(()=>{if(archiveDeferred)scheduleArchiveSync();});});
document.addEventListener("compositionstart",()=>{archiveComposing=true;});
document.addEventListener("compositionend",()=>{archiveComposing=false;if(archiveDeferred)scheduleArchiveSync();});
