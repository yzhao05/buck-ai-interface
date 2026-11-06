/* Searchable conversation material. Legacy prompt matching only labels old stored records; it never routes requests. */
"use strict";
const resultAnalysisPrompts=new Set([
  "请分析当前辨识结果的误差，并给出有依据的修改计划。",
  "Analyze errors in the current identification results and propose an evidence-based modification plan.",
  "让助手分析结果"
]);
function analysisDescriptor() {
  return {kind:"identification_analysis",filename:state.savedRun?.dataset.filename || state.dataset?.filename || null,method:state.result?.method || state.result?.options?.method || null,
    pwm_source:state.result?.options?.pwm_source || null,result_id:state.resultId || null};
}
function findHistoricalAnalyses(record) {
  const found=[];
  const requests=new Map((record?.messages || []).filter(m=>m.role==="user").map(m=>[m.id,m]));
  const seen=new Set();
  let request=null;
  for (const message of record?.messages || []) {
    if (message.role==="user") {
      request=message;continue;
    }
    if (message.role!=="assistant") continue;
    const marker=message.response?.analysis;
    // New records are linked by ID, so replies from concurrent tabs may interleave.
    // Old records have no link and can only be paired with the preceding request.
    const requestId=marker?.request_id || message.response?.request_message_id;
    const paired=requestId ? requests.get(requestId) : request;
    if (!paired) continue;
    const explicit=marker?.request_id===paired.id;
    const recognized=paired.response?.analysis_request || resultAnalysisPrompts.has(paired.content.trim());
    if ((explicit || recognized) && !seen.has(paired.id) && message.mode!=="请求未完成" && message.content.trim()) {
      const meta=explicit ? marker : paired.response?.analysis_request;
      seen.add(paired.id);
      found.push({id:message.id,request_id:paired.id,content:message.content,
        method:["pann","weighted_integral","integral"].includes(meta?.method) ? meta.method : null,
        pwm_source:["measured","controller"].includes(meta?.pwm_source) ? meta.pwm_source : null,
        result_id:typeof meta?.result_id==="string" ? meta.result_id : null});
    }
    // A reply (including a failed request) consumes its own preceding request.
    if (request?.id===paired.id) request=null;
  }
  return found;
}
function renderAnalysisHistory() {
  if (!$("analysis-count")) return;
  const records=conversationMessages(activeConversation);
  $("analysis-count").textContent=String(records.length);
  $("compare-analyses").disabled=!records.length || historyLocked() || !state.connected;
}

function conversationMessages(record) {
  const analyses=new Map(findHistoricalAnalyses(record).map(item=>[item.id,item]));
  return (record?.messages || []).filter(m=>["user","assistant"].includes(m.role) && m.mode!=="请求未完成" && m.content?.trim()).map(m=>{
    const analysis=analyses.get(m.id), marker=m.response?.analysis;
    return {id:m.id,role:m.role,content:m.content,created:m.created,
      request_message_id:m.response?.request_message_id || analysis?.request_id || null,
      ...(analysis ? {analysis:{kind:"identification_analysis",method:analysis.method,pwm_source:analysis.pwm_source,
        result_id:analysis.result_id,filename:marker?.filename || null}} : {})};
  });
}
function decorateConversationRetrieval(article, retrieval) {
  if (!retrieval?.searches?.length && !retrieval?.read_count) return;
  const box=document.createElement("div"); box.className="message-tools conversation-retrieval";
  const heading=document.createElement("div");heading.className="retrieval-heading";
  const label=document.createElement("span");
  label.append(window.BuckI18n.node("本次检索会话消息："),document.createTextNode(String(retrieval.available_count)),
    window.BuckI18n.node("；已读取材料："),document.createTextNode(String(retrieval.read_count)));
  const sources=document.createElement("div"); sources.className="message-sources";
  sources.id=`retrieval-sources-${crypto.randomUUID()}`;sources.hidden=true;
  const toggle=document.createElement("button");toggle.type="button";toggle.className="section-toggle retrieval-toggle";
  toggle.setAttribute("aria-controls",sources.id);
  function updateToggle(){
    toggle.textContent=sources.hidden?"⌄":"⌃";
    toggle.setAttribute("aria-expanded",String(!sources.hidden));
    const title=sources.hidden?"展开检索来源":"收起检索来源";
    window.BuckI18n.attr(toggle,"aria-label",title);window.BuckI18n.attr(toggle,"title",title);
  }
  toggle.onclick=()=>{sources.hidden=!sources.hidden;updateToggle();};updateToggle();
  heading.append(label,toggle);box.append(heading);
  for (const source of retrieval.sources || []) {
    const button=document.createElement("button"); button.type="button"; button.className="button ghost";
    button.append(window.BuckI18n.node("查看消息"),document.createTextNode(` #${source.ordinal} · `),
      window.BuckI18n.node(source.partial ? "片段" : "全文"));
    button.onclick=()=>{
      const target=[...$("chat-messages").querySelectorAll(".message")].find(m=>m.dataset.messageId===source.message_id);
      if (target) { target.scrollIntoView({behavior:"smooth",block:"center"}); target.tabIndex=-1; target.focus({preventScroll:true}); }
      else notice("原消息暂未加载，请重新打开本会话。");
    };
    sources.append(button);
  }
  box.append(sources); article.querySelector(".message-label").after(box);
}
