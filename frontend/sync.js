/* Bidirectional private archive. The recovery code is a credential, never a URL. */
"use strict";
let archiveKey = "", archiveEnabled = false, archivePersistent = true;
let archiveTimer = null, archiveRunning = false, archiveAgain = false, archiveApplying = false;
let archiveBootstrap = false;
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
  if (archiveApplying || !archiveEnabled || !state.connected) return;
  clearTimeout(archiveTimer);
  archiveTimer=setTimeout(()=>void synchronizeArchive(),1000);
}
function archiveRequest(path, options={}) {
  return api("/api/archive"+path,{...options,archiveCode:archiveKey});
}
function wireRecord(record) {
  return {id:record.id,updated:record.updated,meta_updated:record.meta_updated || record.updated,
    file_epoch:record.file_epoch || 0,draft:record.draft || "",fields:record.fields || {},attachment:record.attachment || null,
    messages:record.messages.map(m=>({id:m.id,created:m.created,role:m.role,content:m.content,mode:m.mode || "",response:m.response || null}))};
}
function saveMergedRecord(record) {
  // Persist the complete merged record before clearing any old state. A quota
  // failure leaves the original browser copy intact and is reported to the user.
  for (const message of record.messages) localStorage.setItem(conversationPrefix+record.id+":message:"+message.id,JSON.stringify(message));
  localStorage.setItem(conversationPrefix+record.id,JSON.stringify({...record,messages:[],messageStoreVersion:1}));
  conversationCache.set(record.id,record);
}
async function removeLocalArchive(id) {
  const scope=conversationPrefix+id;
  localStorage.setItem(conversationPrefix+"deleted:"+id,"1");
  await BuckAttachments.remove(scope);
  for (const key of Object.keys(localStorage)) if (key===scope || key.startsWith(scope+":message:")) localStorage.removeItem(key);
  conversationCache.delete(id);
}
async function reconcileFiles(record, remoteFiles) {
  const scope=conversationPrefix+record.id, epoch=record.file_epoch || 0;
  await BuckAttachments.remove(scope,epoch);
  const local=new Map((await BuckAttachments.list(scope)).map(file=>[file.sha256,file]));
  const remote=new Map(remoteFiles.map(file=>[file.sha256,file]));
  const wanted=new Set([...local.keys(),...remote.keys()]);
  const attached=record.attachment?.dataset?.sha256;
  if (attached) wanted.add(attached);
  let missing=false;
  for (const sha of wanted) {
    const own=local.get(sha), other=remote.get(sha);
    const valid=own && await csvDigest(own.blob)===sha;
    if (valid && !other?.valid) {
      const form=new FormData();form.append("file",own.blob,own.filename);
      await archiveRequest(`/conversations/${record.id}/files/${sha}?epoch=${epoch}`,{method:"POST",body:form});
    } else if (!valid && other?.valid) {
      const blob=await archiveRequest(`/conversations/${record.id}/files/${sha}`,{asBlob:true});
      if (await csvDigest(blob)!==sha) throw new Error("后端 CSV 校验失败，同步未完成。");
      await BuckAttachments.save(scope,sha,other.filename,blob,epoch);
    } else if (!valid) missing=true;
  }
  return missing;
}
async function synchronizeArchive({preferRemote=false}={}) {
  clearTimeout(archiveTimer);
  if (!archiveEnabled || !state.connected) return;
  if (archiveRunning) { archiveAgain=true; return; }
  if (historyLocked() || document.getElementById("circuit-dialog")?.open) { scheduleArchiveSync(); return; }
  archiveRunning=true; archiveApplying=true; state.syncBusy=true; controls();
  const previousId=activeConversation?.id;
  const before=activeConversation ? JSON.stringify(wireRecord(activeConversation)) : "";
  let missing=false, failed=false;
  syncStatus("正在核对并同步对话和 CSV…");
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
      saveMergedRecord(merged.record);
      missing=(await reconcileFiles(merged.record,merged.files)) || missing;
    }
    archiveBootstrap=false;
    syncStatus(missing ? "对话已同步；部分 CSV 两端均无有效副本，请重新上传。" : archivePersistent ? "对话和 CSV 已同步" : "已同步；Render 尚未配置持久磁盘，后端副本可能随重启丢失。");
  } catch(error) {
    failed=true;
    syncStatus("同步未完成，本地数据保留；连接恢复后会重试："+error.message);
  } finally {
    state.syncBusy=false;
    try {
      // A later file/network failure must not leave already-merged metadata
      // behind an old editor, whose next input would overwrite the newer copy.
      renderHistory();
      let active=conversationCache.get(previousId);
      if (!active) active=[...conversationCache.values()].sort((a,b)=>b.updated-a.updated)[0];
      if (active && (active.id!==previousId || JSON.stringify(wireRecord(active))!==before || (!state.dataset && active.attachment))) await openConversation(active.id,false);
      else if (!active) { activeConversation=null; resetExperiment(); clearChat(); await newConversation(); }
      renderHistory(); controls();
    } finally { archiveApplying=false;archiveRunning=false; }
    if (archiveAgain) { archiveAgain=false;scheduleArchiveSync(); }
    else if (failed) archiveTimer=setTimeout(()=>void synchronizeArchive(),30000);
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
document.addEventListener("visibilitychange",()=>{if(document.visibilityState==="visible")scheduleArchiveSync();});
