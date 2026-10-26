/* Searchable conversation material and backward-compatible analysis markers. */
"use strict";
const resultAnalysisPrompts=new Set([
  "请分析当前辨识结果的误差，并给出有依据的修改计划。",
  "Analyze errors in the current identification results and propose an evidence-based modification plan.",
  "让助手分析结果"
]);
function analysisDescriptor() {
  return {kind:"identification_analysis",filename:state.dataset?.filename || null,method:state.result?.method || state.result?.options?.method || null,
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
    if ((explicit || recognized) && !seen.has(paired.id) && message.mode!=="请求未完成" && message.content.trim() && !comparingExperiments(paired.content)) {
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

function wantsConversationRetrieval(text) {
  return /历史|之前|前面|此前|上次|早先|最早|最近|当时|刚才|刚刚|(?:本|当前|整个|这次)会话|这段对话|聊天记录|我们.*(?:聊|讨论|说过)|横向|纵向|(?:总结|回顾|汇总).*(?:对话|讨论|聊天)|\b(?:previous|earlier|history|historical|last|latest|oldest|conversation|chat|discussed|said before)\b/i.test(text);
}
function continuesResultAnalysis(text,corpus) {
  if(/历史|之前|上次|此前|前面|最早|早先|\b(?:previous|earlier|historical|oldest|last\s+time)\b/i.test(text))return false;
  if(!state.resultId || requestsIdentificationExecution(text) || /检索|搜索|找出|对比|比较|横向|纵向|\b(?:search|find|compare|comparison|versus)\b/i.test(text))return false;
  if(!/^(?:请|那|那么|please\s+)?(?:为什么|为何|还有.*(?:可能|原因|解释)|复查|再检查|重新检查|重新分析|详细解释|展开|继续解释|why\b|any other\b|what else\b|recheck\b|double.check\b|explain (?:more|further)\b)/i.test(text.trim()))return false;
  const index=corpus.findLastIndex(m=>m.role==="assistant" && m.analysis);
  return index>=0 && corpus[index].analysis.result_id===state.resultId;
}
function requestsIdentificationExecution(text) {
  // This only decides whether CSV restoration is needed. The backend remains
  // authoritative about execution permission and numerical configuration.
  const unquoted=text.toLowerCase().replace(/“[^”]*”|「[^」]*」|"[^"\n]*"|`[^`]*`/g,"");
  if (/(?:停止|取消|暂停|别再).{0,20}(?:辨识|识别|拟合)|(?:不要|不用|别|无需|不需要)\s*(?:(?:开始|执行|进行|启动|再)\s*)*(?:参数|l\/?c\/?r)?\s*(?:辨识|识别|拟合)|\b(?:do not|don't|stop|cancel|pause)\b.*\b(?:identify|fit|run|start|identification|fitting)\b/.test(unquoted)) return false;
  return unquoted.split(/[，,。；;！？?!\n]|并且|然后|同时|并|\b(?:and|then)\b/).some(clause=>{
    if (/原理|如何|怎么|为什么|什么|方法|解释|误差|对比|比较|结果|报告|公式|\b(?:how|why|what|explain|compare|comparison|results?|errors?|formula)\b/.test(clause)) return false;
    return /^\s*(?:(?:请|帮我|麻烦|现在|立即|先|再|开始|执行|进行|启动|重新)\s*)*(?:参数|l\/?c\/?r)?\s*(?:辨识|识别|拟合)|^\s*(?:please\s+)?(?:first\s+)?(?:identify\b|fit\b|(?:start|run|perform)\s+(?:the\s+)?(?:parameter\s+)?(?:identification|fit|fitting)\b)/.test(clause);
  });
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
  box.append(window.BuckI18n.node("本次检索会话消息："),document.createTextNode(String(retrieval.available_count)),
    window.BuckI18n.node("；已读取材料："),document.createTextNode(String(retrieval.read_count)));
  const sources=document.createElement("div"); sources.className="message-sources";
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
