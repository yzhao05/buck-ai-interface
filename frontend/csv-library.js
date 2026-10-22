/* Reuse retained CSVs from this private history without switching conversations. */
"use strict";
let csvLibraryGeneration = 0;
function closeCsvLibrary() {
  csvLibraryGeneration++;
  $("csv-library-menu").hidden = true;
  $("toggle-csv-library").setAttribute("aria-expanded", "false");
}
async function toggleCsvLibrary() {
  if (!$("csv-library-menu").hidden) { closeCsvLibrary(); return; }
  if (historyLocked() || !activeConversation) return;
  const generation = ++csvLibraryGeneration, prefix = conversationPrefix;
  $("csv-library-menu").hidden = false;
  $("toggle-csv-library").setAttribute("aria-expanded", "true");
  $("csv-library-list").replaceChildren();
  ui($("csv-library-status"), "正在读取历史 CSV…");
  state.busy = true; controls();
  try {
    renderHistory();
    const records = [...conversationCache.values()].filter(r=>!savedValue(prefix+"deleted:"+r.id)).sort((a,b)=>b.updated-a.updated);
    let incomplete = false;
    const groups = await Promise.all(records.map(async record=>{
      const epoch = record.file_epoch || 0, files = new Map();
      const add = file=>{
        if (/^[a-f0-9]{64}$/.test(file?.sha256) && typeof file.filename === "string") files.set(file.sha256, file);
      };
      try {
        for (const file of await BuckAttachments.list(prefix+record.id)) if ((file.epoch || 0)===epoch) add(file);
      } catch { incomplete = true; }
      const attached = record.attachment;
      if (attached && (attached.csvEpoch || 0)===epoch) add(attached.dataset);
      if (archiveEnabled && state.connected) {
        try {
          const remote = await archiveRequest(`/conversations/${record.id}`, {timeout:5000});
          // A remote deletion/clear wins over stale local files, even before sync.
          if (remote.deleted || (remote.record.file_epoch || 0)>epoch) return [];
          if ((remote.record.file_epoch || 0)===epoch) for (const file of remote.files) add(file);
        } catch(error) { if (error.status!==404) incomplete = true; }
      }
      return [...files.values()].map(file=>({conversationId:record.id, epoch, prefix,
        sha256:file.sha256, filename:file.filename, updated:record.updated}));
    }));
    if (generation!==csvLibraryGeneration || prefix!==conversationPrefix) return;
    const entries = groups.flat();
    for (const entry of entries) {
      const button = document.createElement("button"); button.type="button";button.className="csv-library-item";
      button.dataset.sha256=entry.sha256;button.dataset.conversationId=entry.conversationId;
      const name = document.createElement("strong");name.textContent=entry.filename;
      const detail = document.createElement("small");
      detail.textContent=new Date(entry.updated).toLocaleString(window.BuckI18n.language==="en" ? "en-GB" : "zh-CN")+" · "+entry.conversationId.slice(0,8);
      button.append(name,detail);
      button.onclick=()=>{
        if(historyLocked())return;
        closeCsvLibrary();void importData(null,()=>historicalCsvFile(entry));
      };
      $("csv-library-list").append(button);
    }
    ui($("csv-library-status"), incomplete
      ? "部分历史文件暂时无法查询，已显示可找到的 CSV；请检查连接后重试。"
      : entries.length ? "同一会话中内容相同的 CSV 只显示一次。" : "暂无历史 CSV，上传后会自动加入此列表。");
  } catch { ui($("csv-library-status"), "历史 CSV 列表读取失败，请稍后重试。"); }
  finally { state.busy=false;controls(); }
}
async function historicalCsvFile(entry) {
  const stillAvailable = ()=> {
    const record=readConversation(entry.conversationId);
    return entry.prefix===conversationPrefix && record && (record.file_epoch || 0)===entry.epoch
      && !savedValue(entry.prefix+"deleted:"+entry.conversationId);
  };
  if (!stillAvailable()) throw new Error("此 CSV 所属会话已删除或数据已清除，请重新选择。");
  let blob=null;
  try { blob=(await BuckAttachments.load(entry.prefix+entry.conversationId,entry.sha256,entry.epoch))?.blob; } catch {}
  if (blob && await csvDigest(blob)!==entry.sha256) blob=null;
  if (archiveEnabled && state.connected) {
    try {
      const remote=await archiveRequest(`/conversations/${entry.conversationId}`,{timeout:5000});
      if (remote.deleted || (remote.record.file_epoch || 0)>entry.epoch) throw new Error("deleted");
    } catch(error) {
      if(error.message==="deleted") throw new Error("此 CSV 所属会话已删除或数据已清除，请重新选择。");
      // A failed metadata check must not prevent use of a valid browser copy.
    }
  }
  if (!blob && archiveEnabled && state.connected) {
    try {
      blob=await archiveRequest(`/conversations/${entry.conversationId}/files/${entry.sha256}`,{asBlob:true});
      if(await csvDigest(blob)!==entry.sha256)blob=null;
    } catch { blob=null; }
  }
  if (!stillAvailable()) throw new Error("此 CSV 所属会话已删除或数据已清除，请重新选择。");
  if (!blob) throw new Error("暂时无法从浏览器或后端恢复 CSV。请检查连接并重试同步；若两端均无有效副本，请重新上传原文件。");
  return new File([blob],entry.filename,{type:"text/csv"});
}
document.addEventListener("click",event=>{if(!event.target.closest(".csv-library"))closeCsvLibrary();});
document.addEventListener("keydown",event=>{if(event.key==="Escape" && !$("csv-library-menu").hidden){closeCsvLibrary();$("toggle-csv-library").focus();}});
document.addEventListener("buck-language-change",closeCsvLibrary);
