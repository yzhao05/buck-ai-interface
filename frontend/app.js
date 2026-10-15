/* Buildless frontend: safe text rendering, server-owned results, no provider key. */
"use strict";
const $ = (id) => document.getElementById(id);
const ui = window.BuckI18n.text;
const tr = window.BuckI18n.translate;
const local = ["localhost", "127.0.0.1"].includes(location.hostname);
const config = window.BUCK_CONFIG || {};
function savedValue(key) {
  try { return localStorage.getItem(key) || ""; } catch { return ""; }
}
function tokenKey(address) {
  try {
    const url = new URL(address);
    if (!["http:", "https:"].includes(url.protocol)) return null;
    return `buck-access-token:${new URL(".", location.href).pathname}:${url.href.replace(/\/$/, "")}`;
  } catch { return null; }
}
function savedToken(address) {
  const key = tokenKey(address);
  return key ? savedValue(key) : "";
}
let base = savedValue("buck-api-url") || config.apiBaseUrl || (local ? location.origin : "");
let accessToken = savedToken(base);
let sessionId = crypto.randomUUID();
const state = { dataset: null, mapping: null, summary: null, result: null, resultId: null,
  history: [], connected: false, busy: false, chatBusy: false, job: null, chart: null, zoom: null,
  identificationOpen: false };
const fmt = (value, digits = 4) => Number.isFinite(value) ? Number(value.toPrecision(digits)).toString() : "—";
const tools = {get_waveform_summary:"读取波形统计", get_identification_result:"读取辨识结果", start_identification:"启动物理辨识", search_knowledge:"检索专业资料"};
function notice(text = "", success = false) { ui($("notice"), text); $("notice").hidden = !text; $("notice").className = "notice" + (success ? " success" : ""); }
function toggleSection(buttonId, contentId, label, expanded) {
  const button = $(buttonId), content = $(contentId);
  const open = expanded ?? content.hidden;
  content.hidden = !open;
  button.setAttribute("aria-expanded", String(open));
  window.BuckI18n.attr(button,"aria-label",`${open ? "收起" : "展开"}${label}`);
  window.BuckI18n.attr(button,"title",`${open ? "收起" : "展开"}${label}`);
  button.textContent = open ? "−" : "+";
}
function setIdentificationOpen(open) {
  state.identificationOpen = open;
  $("workspace").classList.toggle("identification-open", open);
  $("analysis-panel").hidden = !open;
  $("identification-form").hidden = !open;
  $("enter-identification").hidden = open;
  $("enter-identification").setAttribute("aria-expanded", String(open));
  if (open) {
    $("vin").focus({preventScroll:true});
    requestAnimationFrame(drawCharts);
  } else {
    $("chart-tooltip").hidden = true;
    $("enter-identification").focus({preventScroll:true});
  }
  controls();
}
function errorText(body) {
  if (typeof body.detail === "string") return body.detail;
  if (Array.isArray(body.detail)) return body.detail.map(e => `${e.loc?.slice(1).join(".")}: ${e.msg}`).join("；");
  return "请求失败，请检查后端服务。";
}
async function api(path, {method = "GET", body, timeout = 90000} = {}) {
  if (!base) throw new Error("请先在连接设置中填写 Render 后端地址，或本地启动后端。");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  const headers = {"X-Session-ID": sessionId};
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  if (body && !(body instanceof FormData)) headers["Content-Type"] = "application/json";
  try {
    const response = await fetch(base.replace(/\/$/, "") + path, {method, headers,
      body: body instanceof FormData ? body : body ? JSON.stringify(body) : undefined, signal: controller.signal});
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw Object.assign(new Error(errorText(data)), {status:response.status});
    return data;
  } catch (error) {
    if (error.name === "AbortError") throw new Error("请求超时。Render 服务可能正在唤醒，请稍后重试。");
    if (error instanceof TypeError) throw new Error("无法连接后端。请检查服务地址、启动状态及 CORS_ORIGINS 设置。");
    throw error;
  } finally { clearTimeout(timer); }
}
function controls() {
  const waiting = state.busy || Boolean(state.job) || state.chatBusy;
  $("identify").disabled = !state.identificationOpen || !state.connected || !state.mapping || waiting;
  $("apply-mapping").disabled = !state.dataset || waiting;
  $("load-demo").disabled = waiting;
  $("empty-demo").disabled = waiting;
  $("csv-file").disabled = waiting;
  $("clear-data").disabled = waiting;
  $("send-chat").disabled = waiting || !state.connected || !activeConversation;
  $("clear-chat").disabled = waiting;
  $("new-chat").disabled = waiting;
  document.querySelectorAll(".history-item").forEach(button => button.disabled = waiting);
  $("leave-identification").disabled = waiting;
  $("close-analysis").disabled = waiting;
  for (const id of ["map-time","map-current","map-voltage","map-pwm","time-unit","current-unit","voltage-unit","vin","frequency","fit-mode","fixed-r","fit-method","start-ms","end-ms","train-fraction"]) $(id).disabled = waiting;
}
async function connect() {
  $("connection-status").className = "status";
  $("connection-status").replaceChildren(document.createElement("i"), window.BuckI18n.node("正在连接"));
  try {
    const health = await api("/api/health", {timeout: 60000});
    state.connected = true;
    $("connection-status").className = "status connected";
    $("connection-status").replaceChildren(document.createElement("i"), window.BuckI18n.node("后端已连接"));
    ui($("chat-mode"), health.chat_mode === "deepseek" ? `DeepSeek · ${health.model}` : "本地规则分析 · 未配置 API Key");
    const pannOption = $("fit-method").querySelector('[value="pann"]');
    pannOption.disabled = !health.pann_available;
    ui(pannOption, health.pann_available ? "PANN · 物理网络微调" : "PANN · 需后端 PyTorch");
    if (!health.pann_available) $("fit-method").value = "integral";
    const source = health.source_url || config.sourceUrl;
    if (source && /^https:\/\//.test(source)) { $("source-link").href = source; ui($("source-link"), "本项目源码 ↗"); }
    if (health.requires_token && !accessToken) notice("后端已连接。请在连接设置中输入应用访问口令后上传数据。");
    else notice();
    return true;
  } catch (error) {
    state.connected = false;
    $("connection-status").replaceChildren(document.createElement("i"), window.BuckI18n.node("尚未连接"));
    ui($("chat-mode"), "等待连接分析后端");
    notice(error.message);
    return false;
  } finally { controls(); }
}
function mappingFromForm() {
  return {time:$("map-time").value, current:$("map-current").value, voltage:$("map-voltage").value,
    pwm:$("map-pwm").value || null, time_unit:$("time-unit").value, current_unit:$("current-unit").value, voltage_unit:$("voltage-unit").value};
}
function options() {
  const numeric = (id) => {
    const el = $(id);
    if (!el.value.trim() || !el.checkValidity()) throw new Error("请检查工况输入范围：" + el.closest("label")?.textContent.trim());
    return Number(el.value);
  };
  return {vin:numeric("vin"), frequency_hz:numeric("frequency")*1000,
    start_s:$("start-ms").value === "" ? null : Number($("start-ms").value)/1000,
    end_s:$("end-ms").value === "" ? null : Number($("end-ms").value)/1000,
    train_fraction:Number($("train-fraction").value)/100, mode:$("fit-mode").value,
    resistance_ohm:numeric("fixed-r"), method:$("fit-method").value, epochs:60};
}
function invalidateResult() {
  state.result = null; state.resultId = null;
  for (const key of ["result-l", "result-c", "result-r"]) ui($(key), "—");
  ui($("result-badge"), "等待计算"); $("result-badge").className = "badge";
  ui($("result-notes"), "拟合完成后显示估计参数和留出误差。没有独立真值时，不报告参数真实误差。");
  $("validation").hidden = true; $("diagnostics").hidden = true;
  $("ask-error").disabled = true; $("download-report").disabled = true;
  if (state.summary) { state.chart = state.summary.chart; drawCharts(); }
}
function fillMappings(dataset) {
  for (const [name, id] of [["time","map-time"],["current","map-current"],["voltage","map-voltage"],["pwm","map-pwm"]]) {
    const select = $(id); select.replaceChildren();
    if (name === "pwm") { const option = new Option("", ""); ui(option,"不选择（仅分析波形）"); select.add(option); }
    dataset.columns.forEach(c => select.add(new Option(c, c)));
    select.value = dataset.suggested_mapping[name] || "";
  }
  for (const id of ["time-unit","current-unit","voltage-unit"]) $(id).selectedIndex = 0;
}
async function importData(file = null) {
  if (state.busy || state.job || state.chatBusy) return;
  state.busy = true; controls(); notice();
  ui($("job-status"), "正在读取并检查数据…");
  try {
    let uploaded;
    if (file) {
      if (file.size > 32 * 1024 * 1024 || !file.name.toLowerCase().endsWith(".csv")) throw new Error("请选择不超过 32 MB 的 CSV 文件。");
      const form = new FormData(); form.append("file", file);
      uploaded = await api("/api/datasets", {method:"POST", body:form});
    } else uploaded = await api("/api/demo", {method:"POST"});
    state.dataset = uploaded; state.mapping = null; state.summary = null; state.chart = null; state.zoom = null;
    invalidateResult(); fillMappings(uploaded);
    $("file-info").hidden = false; $("file-name").textContent = uploaded.filename;
    ui($("file-rows"), `${uploaded.rows.toLocaleString()} 行 · CSV 已读取`);
    ui($("chat-context"), `已关联：${uploaded.filename}`);
    $("start-ms").value = file ? "" : "0"; $("end-ms").value = file ? "" : "0.8";
    if (!file) { $("vin").value = "28"; $("frequency").value = "100"; }
    await applyMapping();
    $("mapping-details").open = Boolean(file);
    if (!file) notice("已加载真实 PLECS Case16A 数据。已将辨识区间设为 0～0.8 ms，避开 1 ms 的负载突变。", true);
  } catch (error) { notice(error.message); ui($("job-status"), "数据读取或检查未完成"); }
  finally { state.busy = false; $("csv-file").value = ""; persistConversation(); controls(); }
}
async function applyMapping() {
  if (!state.dataset) return;
  const selected = mappingFromForm();
  const summary = await api("/api/analyze", {method:"POST", body:{dataset_id:state.dataset.dataset_id, mapping:selected}});
  state.mapping = selected; state.summary = summary; state.chart = summary.chart; state.zoom = null;
  invalidateResult();
  function stat(id, value, unit) { $(id).replaceChildren(window.BuckI18n.node(fmt(value)), Object.assign(document.createElement("small"), {textContent:unit})); }
  stat("stat-voltage", summary.voltage.mean, "V"); stat("stat-current", summary.current.rms, "A"); stat("stat-duration", summary.duration_s*1000, "ms");
  ui($("stat-points"), `${summary.rows.toLocaleString()} 个有效采样点`);
  $("wave-subtitle").textContent = state.dataset.filename;
  $("chart-empty").hidden = true; $("chart-container").hidden = false;
  ui($("job-status"), selected.pwm ? "数据就绪 · 可开始辨识" : "可分析波形 · 辨识还需要 PWM");
  drawCharts(); controls();
}
async function startFit() {
  if (!state.identificationOpen || !state.mapping || state.busy || state.job) return;
  try {
    const fitOptions = options();
    state.busy = true; controls(); invalidateResult(); notice();
    ui($("job-status"), "正在提交辨识任务…");
    const response = await api("/api/identify", {method:"POST", body:{dataset_id:state.dataset.dataset_id,mapping:state.mapping,options:fitOptions}});
    state.busy = false;
    await watchJob(response.job_id);
  } catch (error) { notice(error.message); ui($("job-status"), "辨识未完成"); }
  finally { state.busy = false; controls(); }
}
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function watchJob(id) {
  if (state.job) return;
  state.job = id; persistConversation(); controls();
  const started = Date.now();
  try {
    for (let attempt = 0; attempt < 600; attempt++) {
      const response = await api(`/api/jobs/${id}`);
      ui($("job-status"), response.status === "queued" ? "任务已排队…" : `正在拟合与验证 · ${Math.round((Date.now()-started)/1000)} 秒`);
      if (response.status === "complete") {
        state.result = response.result; state.resultId = id; displayResult();
        ui($("job-status"), `辨识完成 · 用时 ${response.result.elapsed_s} 秒`);
        return;
      }
      if (response.status === "failed") throw new Error(response.error);
      await pause(1200);
    }
    throw new Error("任务等待超时，请检查后端日志。");
  } catch (error) { notice(error.message); ui($("job-status"), "任务未完成，请检查提示"); }
  finally { state.job = null; persistConversation(); controls(); }
}
function displayResult() {
  const r = state.result, p = r.parameters;
  ui($("result-l"), fmt(p.L_H*1e6,5)); ui($("result-c"), fmt(p.C_F*1e6,5)); ui($("result-r"), fmt(p.R_ohm,5));
  ui($("r-label"), r.R_estimated ? "负载电阻 R · 估计" : "负载电阻 R · 固定");
  ui($("result-badge"), r.quality); $("result-badge").className = "badge " + (r.quality === "需复核" ? "warning" : "good");
  $("validation").hidden = false; ui($("rmse-i"), fmt(r.metrics.current_rmse_A) + " A"); ui($("rmse-v"), fmt(r.metrics.voltage_rmse_V*1000) + " mV");
  ui($("result-notes"), `${r.validation_kind}\n${r.parameter_error_note}`);
  $("diagnostics").hidden = false;
  ui($("diagnostic-text"), JSON.stringify({method:r.diagnostics,conditions:r.options,warnings:r.warnings,validation_errors:r.metrics}, null, 2));
  $("ask-error").disabled = false; $("download-report").disabled = false;
  state.chart = r.chart; state.zoom = null; drawCharts();
}
function clearChat() {
  state.history = [];
  $("conversation-panel").classList.add("is-empty");
  $("chat-messages").querySelectorAll(".message").forEach(el => el.remove());
  $("chat-messages").querySelector(".welcome").hidden = false;
  $("chat-messages").querySelector(".suggestions").hidden = false;
}
function message(role, content, mode = "") {
  const article = document.createElement("article"); article.className = `message ${role}`;
  const label = document.createElement("div"); label.className = "message-label";
  if (role === "assistant") { const icon = document.createElement("span"); icon.className = "assistant-icon"; icon.textContent = "✳"; label.append(icon); }
  label.append(window.BuckI18n.node(role === "user" ? "你" : "实验助手"));
  if (mode) { const badge = document.createElement("span"); badge.className = "message-mode"; ui(badge, mode); label.append(badge); }
  const text = document.createElement("div"); text.className = "message-content"; text.textContent = content;
  article.append(label, text); $("chat-messages").append(article);
  $("chat-messages").scrollTop = $("chat-messages").scrollHeight;
  return article;
}
function decorateResponse(article, response) {
    if (response.route) {
      const task = document.createElement("div"); task.className = "message-tools message-task";
      task.append(window.BuckI18n.node("任务："), window.BuckI18n.node(response.route.label));
      if (response.route.mode !== "agent") task.append(document.createTextNode(" · "), window.BuckI18n.node(
        response.route.mode === "fallback" ? "分类服务暂不可用，使用本地规则" : "本地规则分类"));
      article.querySelector(".message-label").after(task);
    }
    if (response.tool_calls?.length) {
      const log = document.createElement("div"); log.className = "message-tools";
      ui(log, response.tool_calls.map(t => `${tools[t.name] || t.name} · ${t.status}`).join("\n")); article.append(log);
    }
    if (response.sources?.length) {
      const sources = document.createElement("div"); sources.className = "message-sources";
      response.sources.forEach(source => {
        const valid = source.url && /^https:\/\//.test(source.url);
        const link = document.createElement(valid ? "a" : "span"); link.textContent = source.title;
        if (valid) { link.href = source.url; link.target = "_blank"; link.rel = "noopener noreferrer"; }
        sources.append(link);
      }); article.append(sources);
    }
}
async function sendMessage(value) {
  const text = value.trim(); if (!text || historyLocked() || !activeConversation) return;
  if (!state.connected) { notice("请先连接分析后端。"); return; }
  let fitOptions;
  try { fitOptions = options(); } catch (error) {
    if (state.identificationOpen) { notice(error.message); return; }
  }
  state.chatBusy = true; controls();
  $("conversation-panel").classList.remove("is-empty");
  $("chat-input").value = "";
  $("chat-messages").querySelector(".welcome").hidden = true; $("chat-messages").querySelector(".suggestions").hidden = true;
  message("user", text);
  const previousHistory = readConversation(activeConversation.id).messages.filter(m=>m.mode!=="请求未完成")
    .slice(-10).map(m=>({role:m.role,content:m.content.slice(0,8000)}));
  rememberMessage("user", text);
  const pending = message("assistant", "正在读取实验上下文…"); pending.querySelector(".message-content").classList.add("typing"); ui(pending.querySelector(".message-content"),"正在读取实验上下文…");
  try {
    const response = await api("/api/chat", {method:"POST", timeout:220000, body:{message:text,history:previousHistory,
      dataset_id:state.mapping ? state.dataset?.dataset_id : null,mapping:state.mapping,options:fitOptions,result_id:fitOptions ? state.resultId : null,
      identification_enabled:state.identificationOpen}});
    pending.remove();
    const article = message("assistant", (response.provider_error ? response.provider_error + "\n\n" : "") + response.answer,
      response.mode === "deepseek" ? "DeepSeek" : "本地规则分析");
    decorateResponse(article, response);
    rememberMessage("assistant", article.querySelector(".message-content").textContent, {route:response.route, tool_calls:response.tool_calls, sources:response.sources}, response.mode === "deepseek" ? "DeepSeek" : "本地规则分析");
    if (response.job_id) { invalidateResult(); void watchJob(response.job_id); }
  } catch (error) { pending.remove(); message("assistant", error.message, "请求未完成"); rememberMessage("assistant",error.message,null,"请求未完成"); }
  finally { state.chatBusy = false; controls(); $("chat-messages").scrollTop = $("chat-messages").scrollHeight; }
}
function svgNode(name, attrs = {}, text = null) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", name);
  Object.entries(attrs).forEach(([k,v]) => el.setAttribute(k,v));
  if (text !== null) el.textContent = text;
  return el;
}
function drawChart(id, channel, predictedChannel) {
  const svg = $(id), chart = state.chart;
  if (!chart || !svg.clientWidth) return;
  const w = svg.clientWidth, h = 156, left = 48, right = 18, top = 14, bottom = 25;
  svg.setAttribute("viewBox", `0 0 ${w} ${h}`); svg.replaceChildren();
  const t = chart.time_s, ys = chart[channel], ps = chart[predictedChannel];
  const lo = state.zoom?.[0] ?? t[0], hi = state.zoom?.[1] ?? t[t.length-1];
  const indices = t.map((v,i) => (v >= lo && v <= hi) ? i : -1).filter(i => i >= 0);
  if (indices.length < 2) return;
  let yMin = Infinity, yMax = -Infinity;
  for (const i of indices) { yMin=Math.min(yMin,ys[i],ps?.[i]??Infinity); yMax=Math.max(yMax,ys[i],ps?.[i]??-Infinity); }
  const pad = Math.max((yMax-yMin)*.12, Math.abs(yMax)*.01, .001); yMin-=pad; yMax+=pad;
  const x = v => left+(v-lo)/(hi-lo)*(w-left-right), y = v => h-bottom-(v-yMin)/(yMax-yMin)*(h-top-bottom);
  if (state.result && state.result.train_end_s < hi) {
    const sx = x(Math.max(lo,state.result.train_end_s));
    svg.append(svgNode("rect", {x:sx,y:top,width:Math.max(0,w-right-sx),height:h-top-bottom,fill:"#f1f6f9"}));
    if (state.result.train_end_s >= lo) {
      svg.append(svgNode("line",{x1:sx,y1:top,x2:sx,y2:h-bottom,stroke:"#b6c9cf","stroke-dasharray":"3 4"}));
      svg.append(svgNode("text",{x:sx+5,y:top+9,fill:"#91a5af","font-size":"8"},tr("留出验证")));
    }
  }
  for(let k=0;k<4;k++) {
    const value=yMin+(yMax-yMin)*k/3, yy=y(value);
    svg.append(svgNode("line",{x1:left,y1:yy,x2:w-right,y2:yy,stroke:"#e8efef","stroke-dasharray":"3 4"}));
    svg.append(svgNode("text",{x:left-9,y:yy+3,"text-anchor":"end",fill:"#9caeb4","font-size":"9"},fmt(value,3)));
  }
  for(let k=0;k<6;k++) {
    const value=lo+(hi-lo)*k/5, xx=x(value);
    svg.append(svgNode("line",{x1:xx,y1:top,x2:xx,y2:h-bottom,stroke:"#eff3f4"}));
    svg.append(svgNode("text",{x:xx,y:h-7,"text-anchor":"middle",fill:"#a1b1b7","font-size":"9"},fmt(value*1000,3)));
  }
  const path = values => indices.map((i,j) => `${j?'L':'M'}${x(t[i]).toFixed(2)},${y(values[i]).toFixed(2)}`).join(" ");
  svg.append(svgNode("path",{d:path(ys),fill:"none",stroke:"#188675","stroke-width":"1.45","stroke-linejoin":"round"}));
  if(ps) svg.append(svgNode("path",{d:path(ps),fill:"none",stroke:"#d89b50","stroke-width":"1.25","stroke-dasharray":"4 3",opacity:".95"}));
  let drag = null;
  const overlay = svgNode("rect",{x:left,y:top,width:w-left-right,height:h-top-bottom,fill:"transparent"});
  const selection = svgNode("rect",{x:left,y:top,width:0,height:h-top-bottom,fill:"#107e7122",stroke:"#107e71"});
  svg.append(selection, overlay);
  const timeAt = event => { const box=svg.getBoundingClientRect(); return Math.max(lo,Math.min(hi,lo+((event.clientX-box.left)*w/box.width-left)/(w-left-right)*(hi-lo))); };
  overlay.onpointerdown = event => { drag=timeAt(event); overlay.setPointerCapture(event.pointerId); };
  overlay.onpointermove = event => {
    const time=timeAt(event);
    if(drag!==null) {selection.setAttribute("x",x(Math.min(time,drag)));selection.setAttribute("width",Math.abs(x(time)-x(drag)));}
    let l=0,r=t.length-1;while(l<r){const mid=(l+r)>>1;if(t[mid]<time)l=mid+1;else r=mid;}
    const tip=$("chart-tooltip"); tip.hidden=false;
    tip.textContent=`${fmt(t[l]*1000,5)} ms\n${tr("测量")} ${fmt(ys[l],5)} ${channel==='current_A'?'A':'V'}`+(ps?`\n${tr("预测")} ${fmt(ps[l],5)}`:"");
    tip.style.left=Math.min(event.clientX+12,window.innerWidth-150)+"px";tip.style.top=Math.max(10,event.clientY-70)+"px";
  };
  overlay.onpointerup = event => {
    if(drag!==null) {const end=timeAt(event); if(Math.abs(end-drag)>(hi-lo)*.02){state.zoom=[Math.min(end,drag),Math.max(end,drag)];drawCharts();}else selection.setAttribute("width",0);}
    drag=null;
  };
  overlay.onpointercancel=()=>{drag=null;selection.setAttribute("width",0);};
  overlay.onpointerleave=()=>{$("chart-tooltip").hidden=true;};
  svg.ondblclick=()=>{state.zoom=null;drawCharts();};
}
function drawCharts() {
  drawChart("current-chart","current_A","predicted_current_A"); drawChart("voltage-chart","voltage_V","predicted_voltage_V");
  if(state.chart) ui($("chart-caption"), `${state.chart.original_points.toLocaleString()} 计算点 · ${state.chart.display_points.toLocaleString()} 显示点${state.chart.decimated?'（显示已抽样）':''}`);
  $("view-full").classList.toggle("active",!state.zoom); $("view-tail").classList.toggle("active",Boolean(state.zoom));
}
function downloadReport() {
  if (!state.result) return;
  const report={application:"Buck Lab 0.1.0",exported_at:new Date().toISOString(),summary:state.summary,result:state.result,conversation:state.history};
  const blob=new Blob([JSON.stringify(report,null,2)],{type:"application/json;charset=utf-8"});
  const url=URL.createObjectURL(blob),a=document.createElement("a");a.href=url;a.download=`buck-report-${new Date().toISOString().slice(0,10)}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
}
$("csv-file").onchange = event => { if(event.target.files[0]) void importData(event.target.files[0]); };
$("load-demo").onclick = $("empty-demo").onclick = () => void importData();
$("dropzone").ondragover = event => {event.preventDefault();$("dropzone").classList.add("dragover");};
$("dropzone").ondragleave = () => $("dropzone").classList.remove("dragover");
$("dropzone").ondrop = event => {event.preventDefault();$("dropzone").classList.remove("dragover");if(event.dataTransfer.files[0])void importData(event.dataTransfer.files[0]);};
$("apply-mapping").onclick = async()=>{state.busy=true;controls();try{await applyMapping();notice("列映射已应用。",true);}catch(e){notice(e.message);}finally{state.busy=false;persistConversation();controls();}};
for(const id of ["map-time","map-current","map-voltage","map-pwm","time-unit","current-unit","voltage-unit"]) $(id).onchange=()=>{state.mapping=null;invalidateResult();ui($("job-status"), "映射已更改，请点击应用并检查数据");controls();};
for(const id of ["vin","frequency","fit-mode","fixed-r","fit-method","start-ms","end-ms","train-fraction"]) $(id).oninput=()=>{invalidateResult();$("fixed-r-label").hidden=$("fit-mode").value!=="LC";ui($("train-label"), $("train-fraction").value+"%");};
$("identify").onclick=()=>void startFit();
$("enter-identification").onclick=()=>setIdentificationOpen(true);
$("leave-identification").onclick=$("close-analysis").onclick=()=>setIdentificationOpen(false);
$("toggle-import").onclick=()=>toggleSection("toggle-import","import-content","导入波形");
$("view-full").onclick=()=>{state.zoom=null;drawCharts();};
$("view-tail").onclick=()=>{if(state.chart){const t=state.chart.time_s;state.zoom=[Math.max(t[0],t[t.length-1]-10/(Number($("frequency").value)*1000)),t[t.length-1]];drawCharts();}};
$("chat-form").onsubmit=event=>{event.preventDefault();void sendMessage($("chat-input").value);};
$("chat-input").onkeydown=event=>{if(event.key==="Enter"&&!event.shiftKey&&!event.isComposing){event.preventDefault();void sendMessage($("chat-input").value);}};
document.querySelectorAll("[data-prompt]").forEach(button=>button.onclick=()=>void sendMessage(button.dataset.prompt));
$("ask-error").onclick=()=>void sendMessage(tr("请分析当前辨识结果的误差，并给出有依据的修改计划。"));
$("clear-chat").onclick=$("new-chat").onclick=()=>void newConversation();$("download-report").onclick=downloadReport;
$("clear-data").onclick=async()=>{
  if (historyLocked() || !state.dataset) return;
  state.busy=true; controls();
  try {
    try { await api(`/api/datasets/${state.dataset.dataset_id}`,{method:"DELETE"}); }
    catch(error) { if(error.status!==404) throw error; }
    resetExperiment(); persistConversation();
  }catch(error){notice(error.message);}
  finally {state.busy=false;controls();}
};
$("open-settings").onclick=()=>{$("api-url").value=base;$("access-token").value=accessToken;ui($("settings-status"), "");$("settings-dialog").showModal();};
$("api-url").oninput=()=>{
  // Never carry the previous backend's credential into a different connection.
  $("access-token").value=tokenKey($("api-url").value.trim())===tokenKey(base) ? accessToken : savedToken($("api-url").value.trim());
};
$("forget-access-token").onclick=()=>{
  const key=tokenKey($("api-url").value.trim());
  if(!key){ui($("settings-status"), "请先填写有效的后端地址。");return;}
  try {
    localStorage.removeItem(key);
    $("access-token").value="";
    if(key===tokenKey(base))accessToken="";
    ui($("settings-status"), "已清除这个后端在当前浏览器中记住的口令。");
  }catch{ui($("settings-status"), "浏览器不允许清除存储，请在浏览器设置中清除此网站的数据。");}
};
$("close-settings").onclick=()=>$("settings-dialog").close();
$("settings-form").onsubmit=async event=>{
  event.preventDefault();
  try {
    const url=new URL($("api-url").value.trim());
    if(!["http:","https:"].includes(url.protocol)||url.username||url.password||url.search||url.hash)throw new Error("请输入有效的 http(s) 后端地址。");
    if(location.protocol==="https:"&&url.protocol!=="https:")throw new Error("HTTPS 页面需要连接 HTTPS 后端。");
    if(state.job||state.busy||state.chatBusy)throw new Error("请等待当前请求结束后切换连接。");
    const next=url.toString().replace(/\/$/,"");
    if(base&&next!==base&&state.dataset)throw new Error("切换后端前请先清除当前数据。");
    const backendChanged=next!==base;
    if(backendChanged)persistConversation();
    base=next;accessToken=$("access-token").value.trim();
    let remembered=true;
    try {
      localStorage.setItem("buck-api-url",base);
      if(accessToken)localStorage.setItem(tokenKey(base),accessToken);
      else localStorage.removeItem(tokenKey(base));
    }catch{remembered=false;}
    ui($("settings-status"), "正在连接…");
    if(backendChanged){state.connected=false;await initializeHistory();}
    if(await connect()){
      if(!state.dataset && activeConversation?.attachment)await restoreAttachment(activeConversation.attachment);
      $("settings-dialog").close();ui($("settings-status"), "");
      if(!remembered)notice("已连接，但浏览器禁止保存设置；关闭页面后需要重新输入访问口令。");
    }
    else ui($("settings-status"), "连接未成功，请检查页面提示。");
  }catch(e){ui($("settings-status"), e.message);}
};
$("chat-input").addEventListener("input",persistConversation);
$("experiment-content").addEventListener("change",persistConversation);
for (const [button,content,label] of [["toggle-experiment","experiment-content","实验配置"],["toggle-history","history-content","历史会话"]]) {
  $(button).onclick=()=>{
    toggleSection(button,content,label);
    try {localStorage.setItem(`buck-panel:${new URL(".",location.href).pathname}:${button}`,$(content).hidden ? "closed" : "open");} catch {}
  };
}
document.addEventListener("buck-language-change",()=>{ $("chart-tooltip").hidden=true; drawCharts(); renderHistory(); });
window.addEventListener("pagehide",persistConversation);
new ResizeObserver(()=>drawCharts()).observe($("current-chart"));
$("conversation-panel").classList.add("is-empty");
controls();void (async()=>{await initializeHistory();if(await connect())await restoreAttachment(activeConversation?.attachment);})();
