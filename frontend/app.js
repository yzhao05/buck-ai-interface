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
const state = { dataset: null, csvFile: null, mapping: null, summary: null, result: null, resultId: null, savedRun: null,
  truthDraft: {l:"",c:""}, truthComparison: null, truthError: "",
  history: [], connected: false, busy: false, chatBusy: false, syncBusy: false, job: null, chart: null, zoom: null,
  identificationOpen: false, waveformOpen: false };
const fmt = (value, digits = 4) => Number.isFinite(value) ? Number(value.toPrecision(digits)).toString() : "—";
const tools = {search_conversation:"检索会话记录", read_conversation_message:"读取历史消息", get_waveform_summary:"读取波形统计", get_identification_result:"读取辨识结果", get_experiment_history:"读取实验历史", get_analysis_history:"读取历史分析", start_identification:"启动物理辨识", search_knowledge:"检索专业资料", list_knowledge:"浏览知识目录", read_knowledge:"读取知识全文"};
tools.run_diagnostic_controls="运行参数与初值对照";
tools.read_experiment_results="读取所选历史辨识结果";
Object.assign(tools,{get_diagnostic_evidence:'读取训练与残差证据',connect_plecs:'连接或唤醒 PLECS',
  open_plecs_model:'打开电路模型',search_plecs_models:'搜索电路模型文件夹',select_plecs_model:'选择实验电路',get_plecs_models:'读取电路参数和端口',configure_plecs_ports:'修改仿真输出通道',run_plecs_experiment:'运行 PLECS 对比实验',
  get_plecs_job_result:'接回仿真结果',evaluate_plecs_experiment:'比较仿真辨识误差',read_diagnosis:'读取诊断记录',record_diagnosis:'更新诊断证据'});
function localPlecsConfig(){try{return JSON.parse(savedValue(`buck-plecs:${base}`)||'null');}catch{return null;}}
function isLocalBackend(url){
  try{return ['localhost','127.0.0.1','[::1]'].includes(new URL(url).hostname);}catch{return false;}
}
const backendAuthHeaders = new Map();
function appTokenHeader(){
  const fallback = config.studioBackend && base === config.apiBaseUrl ? 'X-Buck-Authorization' : 'Authorization';
  return backendAuthHeaders.get(base) || fallback;
}
function updatePlecsControls(){
  const direct=isLocalBackend($("api-url").value.trim());
  $("plecs-direct-note").hidden=!direct;
  $("bridge-pair").hidden=direct;
  $("bridge-instructions").hidden=direct;
  ui($("bridge-check"),direct?"检查本机 PLECS 连接":"检查连接器状态");
}
async function syncPlecsConfig(config=localPlecsConfig()){
  if(!config)return false;
  const local=isLocalBackend(base);
  if(!local && !(await api('/api/bridge/status')).paired)return false;
  await api(local?'/api/plecs/settings':'/api/bridge/settings',{method:'POST',body:config,timeout:5000});
  return true;
}
function notice(text = "", success = false) { ui($("notice-text"), text); $("notice").hidden = !text; $("notice").className = "notice" + (success ? " success" : ""); }
function toggleSection(buttonId, contentId, label, expanded) {
  const button = $(buttonId), content = $(contentId);
  const open = expanded ?? content.hidden;
  content.hidden = !open;
  button.setAttribute("aria-expanded", String(open));
  window.BuckI18n.attr(button,"aria-label",`${open ? "收起" : "展开"}${label}`);
  window.BuckI18n.attr(button,"title",`${open ? "收起" : "展开"}${label}`);
  button.textContent = open ? "−" : "+";
}
function setComposerCollapsed(collapsed) {
  $("chat-form").classList.toggle("compose-collapsed", collapsed);
  const button = $("toggle-chat-compose");
  button.setAttribute("aria-expanded", String(!collapsed));
  for (const attribute of ["aria-label", "title"]) window.BuckI18n.attr(button, attribute, collapsed ? "展开输入框" : "收起输入框");
  button.textContent = collapsed ? "▶" : "▼";
  $("chat-input").rows = collapsed ? 1 : 3;
  $("chat-input").wrap = collapsed ? "off" : "soft";
  try { localStorage.setItem("buck-compose-collapsed", String(collapsed)); } catch { /* Keep the current view without storage. */ }
}
function setPaneCollapsed(side, collapsed) {
  const panel = $(side === "left" ? "sidebar-panel" : "analysis-panel");
  const button = $(`toggle-${side}-pane`);
  $("workspace").classList.toggle(`${side}-collapsed`, collapsed);
  panel.classList.toggle("pane-collapsed", collapsed);
  button.setAttribute("aria-expanded", String(!collapsed));
  const label = side === "left" ? (collapsed ? "展开左栏" : "收起左栏") : (collapsed ? "展开右栏" : "收起右栏");
  for (const attr of ["aria-label", "title"]) window.BuckI18n.attr(button, attr, label);
  button.textContent = (side === "left") !== collapsed ? "‹" : "›";
  if (collapsed) {
    if (side === "left") closeCsvLibrary();
    $("chart-tooltip").hidden = true;
    button.focus({preventScroll:true});
  }
  requestAnimationFrame(drawCharts);
}
function setIdentificationOpen(open) {
  state.identificationOpen = open;
  state.waveformOpen = open;
  $("workspace").classList.toggle("identification-open", open);
  $("workspace").classList.toggle("waveform-open", open);
  $("analysis-panel").hidden = !open;
  $("results-card").hidden = !open;
  $("identification-form").hidden = !open;
  $("enter-identification").hidden = open;
  $("enter-identification").setAttribute("aria-expanded", String(open));
  if (open) {
    setPaneCollapsed("left", false);
    setPaneCollapsed("right", false);
    $("vin").focus({preventScroll:true});
    requestAnimationFrame(drawCharts);
  } else {
    $("chart-tooltip").hidden = true;
    $("enter-identification").focus({preventScroll:true});
  }
  controls();
}
function showWaveform() {
  state.waveformOpen = true;
  $("workspace").classList.add("waveform-open");
  $("analysis-panel").hidden = false;
  setPaneCollapsed("right", false);
  requestAnimationFrame(drawCharts);
}
function errorText(body) {
  if (typeof body.detail === "string") return body.detail;
  if (Array.isArray(body.detail)) return body.detail.map(e => `${e.loc?.slice(1).join(".")}: ${e.msg}`).join("；");
  return "请求失败，请检查后端服务。";
}
async function api(path, {method = "GET", body, timeout = 90000, asBlob = false, archiveCode = ""} = {}) {
  if (!base) throw new Error("请在连接设置中填写魔搭创空间的应用地址，或启动本地后端。");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  const headers = {"X-Session-ID": sessionId};
  if (accessToken && path !== "/api/health") headers[appTokenHeader()] = `Bearer ${accessToken}`;
  if (archiveCode) headers["X-Archive-Key"] = archiveCode;
  if (body && !(body instanceof FormData)) headers["Content-Type"] = "application/json";
  try {
    const response = await fetch(base.replace(/\/$/, "") + path, {method, headers,
      body: body instanceof FormData ? body : body ? JSON.stringify(body) : undefined, signal: controller.signal});
    if (response.ok && asBlob) return await response.blob();
    if (response.status === 204) return {};
    let data;
    try {
      data = await response.json();
    } catch (error) {
      if (!(error instanceof SyntaxError)) throw error;
      if (response.ok) throw new Error("后端返回的数据格式无效，请重试。");
      data = {};
    }
    if (!response.ok) throw Object.assign(new Error(errorText(data)), {status:response.status});
    return data;
  } catch (error) {
    if (controller.signal.aborted) {
      const route = path.split(/[?#]/, 1)[0];
      throw new Error(`请求超时（${timeout / 1000} 秒）：${method} ${route}`);
    }
    if (error instanceof TypeError) throw new Error("无法连接后端。请检查服务地址、启动状态及 CORS_ORIGINS 设置。");
    throw error;
  } finally { clearTimeout(timer); }
}
function pwmSource() {
  const selected = $("pwm-source").value;
  return selected === "auto" ? (state.mapping && !state.mapping.pwm ? "controller" : "measured") : selected;
}
function prepareControllerDefaults() {
  if (pwmSource() !== "controller") return;
  if (!$("start-ms").value) $("start-ms").value = "0";
  if (!$("end-ms").value) $("end-ms").value = "0.8";
}
function controls() {
  const waiting = historyLocked();
  $("diagnostic-reference-file").disabled=waiting || !state.result;
  $("choose-diagnostic-reference").disabled=waiting || !state.result;
  $("diagnostic-reference-confirm").disabled=waiting || !selectedDiagnosticReference();
  $("pwm-denoise").disabled=waiting;$("pwm-filter-cycles").disabled=waiting || $("pwm-denoise").value==='none';
  for(const id of ["attach-chat-image","chat-use-circuit","attach-circuit-image","chat-image-file","reuse-chat-image"]) $(id).disabled=waiting;
  $("toggle-result-library").disabled=!activeConversation || (waiting && $("result-library-menu").hidden);
  document.querySelectorAll(".result-library-item").forEach(button=>button.disabled=waiting);
  $("ask-error").disabled = !state.result || (waiting && $("analysis-menu").hidden);
  for(const id of ["analyze-here","analyze-new"]) $(id).disabled=waiting || !state.result;
  if (!state.result) closeAnalysisMenu();
  renderAnalysisHistory();
  for (const id of ["truth-l","truth-c","calculate-parameter-error"]) $(id).disabled=waiting || !state.result;
  window.BuckCircuit.setBusy(waiting); window.BuckCircuit.render();
  $("chat-input").disabled = state.syncBusy;
  $("open-settings").disabled = state.syncBusy || archiveRunning;
  $("sync-now").disabled = waiting || !state.connected || archiveRunning;
  $("apply-mapping").disabled = !state.dataset || waiting;
  $("load-demo").disabled = waiting;
  $("toggle-csv-library").disabled = !activeConversation || (waiting && $("csv-library-menu").hidden);
  document.querySelectorAll(".csv-library-item").forEach(button=>button.disabled=waiting);
  $("empty-demo").disabled = waiting;
  $("csv-file").disabled = waiting;
  $("clear-data").disabled = waiting;
  $("send-chat").disabled = waiting || !state.connected || !activeConversation;
  $("delete-chat").disabled = waiting;
  $("new-chat").disabled = waiting;
  document.querySelectorAll(".history-item").forEach(button => button.disabled = waiting);
  $("leave-identification").disabled = waiting;
  $("pann-settings").hidden = $("fit-method").value !== "pann";
  const reconstructed = pwmSource() === "controller";
  $("controller-settings").hidden = !reconstructed;
  $("fit-mode").querySelector('[value="LCR"]').disabled = reconstructed;
  if (reconstructed) $("fit-mode").value = "LC";
  const sameResult = resultMatchesCurrent(state.result);
  $("identify").disabled = !state.identificationOpen || !state.connected || !state.mapping || waiting || sameResult;
  $("identify-complete-hint").hidden = !state.result;
  ui($("identify-complete-hint"), sameResult
    ? "当前配置已完成辨识。修改上方参数后可重新辨识。"
    : "左侧设置已更改。右侧保留上次结果，点击开始参数辨识后更新。");
  $("fixed-r-label").hidden = $("fit-mode").value !== "LC";
  $("method-hint").hidden = $("fit-method").value !== "weighted_integral";
  for (const id of ["map-time","map-current","map-voltage","map-pwm","time-unit","current-unit","voltage-unit","vin","frequency","fit-mode","fixed-r","fit-method","pann-state","pann-cycles","pann-epochs","start-ms","end-ms","train-fraction","pwm-source","controller-confirmed"]) $(id).disabled = waiting;
  renderIdentificationPlan();
}
async function connect() {
  const address = base;
  $("connection-status").className = "status";
  $("connection-status").replaceChildren(document.createElement("i"), window.BuckI18n.node("正在连接"));
  try {
    const health = await api("/api/health", {timeout: 60000});
    if (address !== base) return false;
    if (typeof health?.chat_mode !== "string") throw new Error("该地址未返回 Buck Lab 服务状态，请填写实际运行应用的地址。");
    if (['Authorization','X-Buck-Authorization'].includes(health.app_auth_header)) {
      backendAuthHeaders.set(address,health.app_auth_header);
    } else {
      backendAuthHeaders.delete(address);
    }
    archiveEnabled=Boolean(health.archive_enabled); archivePersistent=Boolean(health.archive_persistent);
    if (!archiveEnabled) syncStatus("此后端尚未启用双向同步，请更新后端。");
    state.connected = true;
    $("connection-status").className = "status connected";
    $("connection-status").replaceChildren(document.createElement("i"), window.BuckI18n.node("后端已连接"));
    ui($("chat-mode"), health.chat_mode === "deepseek" ? `DeepSeek · ${health.model}` : "AI unavailable · API key not configured");
    const pannOption = $("fit-method").querySelector('[value="pann"]');
    pannOption.disabled = !health.pann_available;
    ui(pannOption, health.pann_available ? "PANN · 物理网络微调" : "PANN · 需后端 PyTorch");
    if (!health.pann_available && $("fit-method").value !== "weighted_integral") {
      $("fit-method").value = "weighted_integral";
    }
    const source = health.source_url || config.sourceUrl;
    if (source && /^https:\/\//.test(source)) { $("source-link").href = source; ui($("source-link"), "本项目源码 ↗"); }
    if (health.requires_token && !accessToken) notice("后端已连接。请在连接设置中输入应用访问口令后上传数据。");
    else notice();
    return true;
  } catch (error) {
    if (address !== base) return false;
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
function readyStatus() {
  ui($("job-status"), pwmSource() === "controller"
    ? ($("controller-confirmed").value === "yes" ? "三列数据就绪 · 固定 R 辨识 L/C" : "请确认已知控制电路配置后开始辨识")
    : state.mapping?.pwm ? "数据就绪 · 可开始辨识" : "无 PWM 列：请在基本信息选择已知控制器重建并确认配置，或补充实际 PWM。");
}
function validNumericInput(el) {
  // Disabled controls are excluded from browser constraint validation. Chat
  // temporarily disables the form, but its draft still needs the same checks.
  const probe=el.cloneNode();probe.disabled=false;probe.value=el.value;
  return Boolean(el.value.trim()) && !el.validity.badInput && Number.isFinite(Number(el.value)) && probe.checkValidity();
}
function options() {
  const numeric = (id) => {
    const el = $(id);
    if (!validNumericInput(el) || (id==="fixed-r" && Number(el.value)<=0.01)) throw new Error("请检查工况输入范围：" + el.closest("label")?.textContent.trim());
    return Number(el.value);
  };
  return {vin:numeric("vin"), frequency_hz:numeric("frequency")*1000,
    pwm_source:pwmSource(), controller_confirmed:$("controller-confirmed").value === "yes",
    controller:pwmSource() === "controller" ? window.BuckCircuit.read() : null,
    pwm_denoise:pwmSource()==='controller' ? $("pwm-denoise").value : 'sg',
    pwm_filter_cycles:pwmSource()==='controller' ? ($("pwm-denoise").value==='none' ? 1 : numeric('pwm-filter-cycles')) : 6,
    start_s:$("start-ms").value === "" && !$("start-ms").validity.badInput ? null : numeric("start-ms")/1000,
    end_s:$("end-ms").value === "" && !$("end-ms").validity.badInput ? null : numeric("end-ms")/1000,
    train_fraction:Number($("train-fraction").value)/100, mode:$("fit-mode").value,
    resistance_ohm:numeric("fixed-r"), method:$("fit-method").value,
    epochs:$("fit-method").value === "pann" ? numeric("pann-epochs") : 100,
    pann_initial_state:$("pann-state").value, pann_window_cycles:Number($("pann-cycles").value)};
}
function resultMatchesData(result) {
  return Boolean(result && state.dataset && state.mapping
    && result.file_sha256 === state.dataset.sha256
    && Object.entries(state.mapping).every(([key,value]) => result.mapping?.[key] === value));
}
function resultMatchesCurrent(result) {
  // Drafts may be temporarily blank/invalid while typing. They never invalidate a completed run.
  if (!resultMatchesData(result)) return false;
  try {
    return Object.entries(options()).every(([key,value]) => value && typeof value === "object"
      ? Object.entries(value).every(([field,number])=>result.options?.[key]?.[field] === number)
      : (result.options?.[key] ?? null) === value);
  } catch { return false; }
}
function invalidateResult() {
  displayPWMQuality(null);
  closeResultLibrary();state.savedRun=null;$("selected-result").value="";
  $("selected-result-name").hidden=true;$("result-archive-note").hidden=true;
  closeAnalysisMenu();
  clearReportDownloads();
  state.result = null; state.resultId = null;
  state.truthDraft={l:"",c:""};state.truthComparison=null;state.truthError="";displayTruthComparison();
  $("result-assessment").hidden = true;
  $("review-reasons").hidden = true; $("review-reason-list").replaceChildren();
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
    if (name === "pwm") { const option = new Option("", ""); ui(option,"不选择（无 PWM 列）"); select.add(option); }
    dataset.columns.forEach(c => select.add(new Option(c, c)));
    select.value = dataset.suggested_mapping[name] || "";
  }
  for (const id of ["time-unit","current-unit","voltage-unit"]) $(id).selectedIndex = 0;
}
async function importData(file = null, resolveFile = null) {
  if (historyLocked()) return;
  // An explicit import belongs to the latest clear generation already received
  // locally. A later clear during this request must still invalidate this file.
  const uploadEpoch=Math.max(activeConversation.file_epoch || 0,readConversation(activeConversation.id)?.file_epoch || 0);
  state.busy = true; controls(); notice();
  ui($("job-status"), "正在读取并检查数据…");
  try {
    if (resolveFile) file = await resolveFile();
    let uploaded;
    if (file) {
      if (file.size > 32 * 1024 * 1024 || !file.name.toLowerCase().endsWith(".csv")) throw new Error("请选择不超过 32 MB 的 CSV 文件。");
      const form = new FormData(); form.append("file", file);
      uploaded = await api("/api/datasets", {method:"POST", body:form});
    } else uploaded = await api("/api/demo", {method:"POST"});
    activeConversation.file_epoch=uploadEpoch;
    state.dataset = uploaded; state.mapping = null; state.summary = null; state.chart = null; state.zoom = null;
    $("chart-container").hidden=true; $("chart-empty").hidden=false;
    for (const id of ["stat-voltage","stat-current","stat-duration"]) ui($(id),"—");
    ui($("stat-points"),"正在读取并检查数据…");
    $("wave-subtitle").textContent=uploaded.filename;
    state.csvFile = null;
    await archiveCurrentCsv(file);
    invalidateResult(); fillMappings(uploaded);
    $("controller-confirmed").value = "no";
    $("file-info").hidden = false; $("file-name").textContent = uploaded.filename;
    ui($("file-rows"), `${uploaded.rows.toLocaleString()} 行 · CSV 已读取`);
    ui($("chat-context"), `已关联：${uploaded.filename}`);
    $("start-ms").value = file ? "" : "0"; $("end-ms").value = file ? "" : "0.8";
    if (!file) { $("vin").value = "28"; $("frequency").value = "100"; }
    await applyMapping();
    $("mapping-details").open = Boolean(file);
    if (!file) notice("已加载真实 PLECS Case16A 数据。已将辨识区间设为 0～0.8 ms，避开 1 ms 的负载突变。", true);
    else notice("CSV 已自动加载并显示波形，请核对信号列与单位。", true);
    return {dataset:uploaded};
  } catch (error) {
    if (state.dataset && !state.mapping) {
      $("mapping-details").open=true;
      ui($("stat-points"), "数据读取或检查未完成");
    }
    notice(error.message); ui($("job-status"), "数据读取或检查未完成");
    return {error:error.message};
  }
  finally { state.busy = false; $("csv-file").value = ""; persistConversation(); controls(); }
}
async function applyMapping() {
  if (!state.dataset) return;
  await ensureDatasetAvailable();
  const selected = mappingFromForm();
  const summary = await api("/api/analyze", {method:"POST", body:{dataset_id:state.dataset.dataset_id, mapping:selected}});
  const preserveResult = state.result && Object.entries(selected).every(([key,value])=>state.mapping?.[key] === value);
  state.mapping = selected; state.summary = summary; state.chart = summary.chart; state.zoom = null;
  prepareControllerDefaults(); controls();
  if (preserveResult) displayResult();
  else invalidateResult();
  displaySummary();
  if (!preserveResult) readyStatus();
  showWaveform();
  drawCharts(); controls();
}
function displaySummary() {
  if (!state.summary || !state.dataset) return;
  const summary = state.summary;
  function stat(id, value, unit) { $(id).replaceChildren(window.BuckI18n.node(fmt(value)), Object.assign(document.createElement("small"), {textContent:unit})); }
  stat("stat-voltage", summary.voltage.mean, "V"); stat("stat-current", summary.current.rms, "A"); stat("stat-duration", summary.duration_s*1000, "ms");
  ui($("stat-points"), "");
  $("wave-subtitle").textContent = state.dataset.filename;
  $("chart-empty").hidden = true; $("chart-container").hidden = false;
}
async function startFit({fromChat=false,waitForCompletion=false,onSubmitted=null,executionSelection=null}={}) {
  if (!state.identificationOpen || !state.mapping || (fromChat ? state.busy || state.syncBusy || state.job : historyLocked()) || resultMatchesCurrent(state.result)) return;
  let submitted=false;
  try {
    const fitOptions = options();
    if(fromChat){
      const question=executionSelectionQuestion(executionSelection,[fitOptions.method],[fitOptions.pwm_source],{allowAuto:$('pwm-source').value==='auto'});
      if(question)throw new Error(question);
      if(fitOptions.pwm_source==='measured'&&!state.mapping.pwm)throw new Error(tr('当前未映射实际 PWM 列，请补齐，或选择并确认控制器重建。'));
    }
    state.busy = true; controls(); invalidateResult(); notice();
    await ensureDatasetAvailable();
    ui($("job-status"), "正在提交辨识任务…");
    submitted=true;
    const response = await api("/api/identify", {method:"POST", body:{dataset_id:state.dataset.dataset_id,mapping:state.mapping,options:fitOptions}});
    if(onSubmitted)onSubmitted(response.job_id);
    if (response.resumed) notice("已找回正在运行的辨识任务，正在恢复进度。",true);
    state.busy = false;
    if(waitForCompletion)return await watchJob(response.job_id);
    if (fromChat) { void watchJob(response.job_id); return {job_id:response.job_id}; }
    await watchJob(response.job_id);
  } catch (error) {
    // The POST may have reached the server even when its response was lost.
    if (!waitForCompletion && submitted && (!error.status || error.status===409 || error.status>=500)) {
      try { if (await resumeActiveJob()) return fromChat ? {job_id:state.job,resumed:true} : undefined; } catch { /* Keep the original error visible. */ }
    }
    notice(error.message);
    ui($("job-status"), submitted && (!error.status || error.status>=500) ? "提交状态暂未确认，请检查连接后重试。" : "辨识未完成");
    if (fromChat) return {error:error.message,uncertain:submitted && (!error.status || error.status>=500 || error.status===409)};
  }
  finally { state.busy = false; controls(); }
}
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function resumeActiveJob() {
  if (state.job) return true;
  const conversationId=activeConversation?.id;
  let response;
  try { response=await api("/api/jobs/active",{timeout:10000}); }
  catch (error) { if (error.status===404) return false; throw error; }
  if (activeConversation?.id!==conversationId || !response.job?.job_id) return false;
  notice("已找回正在运行的辨识任务，正在恢复进度。",true);
  void watchJob(response.job.job_id);
  return true;
}
async function watchJob(id) {
  if (state.job) return;
  state.job = id; persistConversation(); controls();
  const started = Date.now();
  let retries=0, refreshExperiments=false;
  try {
    // A long training run has no total polling deadline; the job decides when it ends.
    while (true) {
      let response;
      try {
        response = await api(`/api/jobs/${id}`);
        if (!["queued","running","complete","failed"].includes(response.status)) throw new Error("任务进度响应不完整。");
      } catch (error) {
        if (error.status && ![408,429].includes(error.status) && error.status<500) throw error;
        retries++;
        // Keep the ID and lock: a failed GET does not cancel server training.
        ui($("job-status"),"暂时无法读取进度，正在自动重连；后端任务可能仍在运行。");
        await pause(Math.min(10000,1200*2**Math.min(retries-1,3)));
        continue;
      }
      if (retries) notice();
      retries=0;
      const stages = {checking:"正在检查数据与电路配置…", pwm_reconstruction:"正在对训练电压去噪、重建 PWM 并检查脉冲质量…",
        initialization:"正在计算参数初值…", pann_training:"正在使用开关信息训练 PANN…", validation:"正在验证预测波形…"};
      ui($("job-status"), response.status === "queued" ? "任务已排队…" : stages[response.stage] || `正在拟合与验证 · ${Math.round((Date.now()-started)/1000)} 秒`);
      if (response.status === "complete") {
        let matches=false;
        try { matches=resultMatchesCurrent(response.result); } catch { /* The user may have edited fields before recovery. */ }
        if (!matches) {
          refreshExperiments=true;
          notice("原辨识任务已完成，结果将恢复到实验历史；当前配置已更改，可重新开始辨识。");
          ui($("job-status"),"原任务已完成，当前配置尚未辨识");
          return {error:tr("原任务已完成，当前配置尚未辨识")};
        }
        state.result = response.result; state.resultId = id; displayResult();
        ui($("job-status"), `辨识完成 · 用时 ${response.result.elapsed_s} 秒`);
        return {status:"complete",job_id:id};
      }
      if (response.status === "failed") {
        displayPWMQuality(response.pwm_reconstruction);notice(response.error);
        ui($("job-status"),"任务未完成，请检查提示");
        return {status:"failed",job_id:id,error:response.error};
      }
      await pause(1200);
    }
  } catch (error) { notice(error.message); ui($("job-status"), "任务未完成，请检查提示"); return {error:error.message}; }
  finally {
    try { if (refreshExperiments) await recoverExperiments(); }
    finally { state.job = null; persistConversation(); controls(); }
  }
}
function resultReviewReasons(result) {
  if (Array.isArray(result.review_reasons)) {
    const reasons=result.review_reasons.filter(r=>r.code!=="pann_best_at_end");
    if ((result.method || result.options?.method)==="pann" && !result.diagnostics?.pann?.convergence && !reasons.some(r=>r.code==="pann_insufficient_history")) reasons.push({code:"pann_insufficient_history"});
    return reasons;
  }
  // Older backends and saved jobs contain the same evidence but no reason list.
  const reasons=[], metrics=result.metrics || {}, d=result.diagnostics || {};
  for (const channel of ["current","voltage"]) {
    const value=metrics[`${channel}_nrmse_pct`];
    if (value>5) reasons.push({code:`${channel}_nrmse`,value,threshold:5});
  }
  if (d.parameter_at_bound) reasons.push({code:"parameter_at_bound"});
  if (d.CR_condition_number>100) reasons.push({code:"condition_number",value:d.CR_condition_number,threshold:100});
  const pann=(result.method || result.options?.method)==="pann" ? d.pann || {} : {};
  if (pann.stop_reason==="time_budget") reasons.push({code:"pann_time_budget",completed_epochs:pann.completed_epochs,requested_epochs:pann.epochs});
  if ((result.method || result.options?.method)==="pann") reasons.push(...(pann.convergence?.reasons || [{code:"pann_insufficient_history"}]));
  return reasons;
}
function parameterComparison(r=state.result) {
  const saved=state.truthComparison;
  if (!r || !saved || saved.result_id!==state.resultId || saved.parameters?.L_H!==r.parameters.L_H || saved.parameters?.C_F!==r.parameters.C_F) return null;
  const truth=saved.ground_truth;
  if (![truth?.L_H,truth?.C_F].every(v=>Number.isFinite(v)&&v>0) || Number(state.truthDraft.l)/1e6!==truth.L_H || Number(state.truthDraft.c)/1e6!==truth.C_F) return null;
  const errors={L:Math.abs(r.parameters.L_H/truth.L_H-1)*100,C:Math.abs(r.parameters.C_F/truth.C_F-1)*100};
  if (!Object.values(errors).every(Number.isFinite))return null;
  return {ground_truth:{...truth},relative_error_pct:errors,source:"user_provided"};
}
function parameterErrorNote() {
  return parameterComparison() ? "已按用户提供的 L、C 真值计算相对误差；真值未独立核验，波形误差与参数误差分开评价。" : "未计算参数真值误差；请填写真实电感、电容并点击计算。波形误差不等于参数误差。";
}
function displayTruthComparison() {
  if($("truth-l").value!==state.truthDraft.l)$("truth-l").value=state.truthDraft.l;
  if($("truth-c").value!==state.truthDraft.c)$("truth-c").value=state.truthDraft.c;
  const comparison=parameterComparison();
  $("parameter-error-values").hidden=!comparison;
  ui($("parameter-error-l"),comparison ? `${fmt(comparison.relative_error_pct.L,6)}%` : "—");
  ui($("parameter-error-c"),comparison ? `${fmt(comparison.relative_error_pct.C,6)}%` : "—");
  $("truth-error").hidden=!state.truthError;ui($("truth-error"),state.truthError);
}
function calculateParameterError({fromChat=false}={}) {
  if (!state.result || (fromChat ? state.busy||state.syncBusy||state.job : historyLocked()))return;
  const L_H=Number(state.truthDraft.l)/1e6,C_F=Number(state.truthDraft.c)/1e6;
  state.truthComparison=null;state.truthError="";
  if (![L_H,C_F].every(v=>Number.isFinite(v)&&v>0&&v<=1e30)) state.truthError="真实电感和真实电容必须是大于 0 的有限数值，请检查单位。";
  else {
    state.truthComparison={result_id:state.resultId,parameters:{L_H:state.result.parameters.L_H,C_F:state.result.parameters.C_F},ground_truth:{L_H,C_F}};
    if (!parameterComparison()) {state.truthComparison=null;state.truthError="数值超出可计算范围，请检查真实值及单位。";}
  }
  captureExperiment({updateTruth:true});
  displayResult({preserveZoom:true});controls();persistConversation();
}
function restoreTruthComparison(attachment) {
  state.truthDraft={l:String(attachment.truthDraft?.l ?? ""),c:String(attachment.truthDraft?.c ?? "")};
  state.truthComparison=attachment.truthComparison || null;state.truthError="";
  const saved=readConversation(activeConversation.id)?.experiments?.find(run=>run.id===state.resultId);
  if (saved) {
    const truth=saved.ground_truth;
    if (!truth) state.truthComparison=null;
    else if (saved.result.parameters.L_H===state.result.parameters.L_H && saved.result.parameters.C_F===state.result.parameters.C_F) {
      if (state.truthComparison?.ground_truth?.L_H!==truth.L_H || state.truthComparison?.ground_truth?.C_F!==truth.C_F) {
        state.truthDraft={l:String(truth.L_H*1e6),c:String(truth.C_F*1e6)};
      }
      state.truthComparison={result_id:state.resultId,parameters:{L_H:state.result.parameters.L_H,C_F:state.result.parameters.C_F},
        ground_truth:{L_H:truth.L_H,C_F:truth.C_F}};
    }
  }
  if (!parameterComparison())state.truthComparison=null;
}
function resultAssessment(r) {
  const c=r.diagnostics?.pann?.convergence;
  return {waveform:{status:[r.metrics.current_nrmse_pct,r.metrics.voltage_nrmse_pct].every(v=>Number.isFinite(v)&&v<=5) ? "passed" : "review",threshold_pct:5},
    training:(r.method || r.options?.method)==="pann" ? c || {status:"insufficient_history"} : {status:"not_applicable"},
    parameter_accuracy:parameterComparison(r) ? {status:"compared",...parameterComparison(r)} : {status:"unverified",reason:"no_ground_truth_comparison"}};
}
function displayAssessment() {
  refreshDiagnosticReference();
  const r=state.result;$("result-assessment").hidden=!r;if(!r)return;
  displayPWMQuality(r.diagnostics?.pwm_reconstruction);
  $("measurement-evidence-list").replaceChildren();
  for(const text of measurementLines(r)){const li=document.createElement("li");li.textContent=text;$("measurement-evidence-list").append(li);}
  const a=resultAssessment(r), c=a.training;
  ui($("waveform-assessment"),a.waveform.status==="passed" ? "达标：电流、电压留出 NRMSE 均不超过 5%" : "未达标或缺少有效留出误差");
  const labels={stable:"末段稳定（经验判据，非收敛证明）",not_stable:"尚未达到末段稳定判据",insufficient_history:"证据不足：轮数过少或历史未记录逐轮数据",not_applicable:"不适用：积分方法无迭代训练"};
  ui($("training-assessment"), c.status==="stable" && a.waveform.status!=="passed" ? "训练已稳定，但拟合未达标（经验判据）" : c.status==="stable" && c.version==="two-window-stability-v2" ? "训练已稳定（试用判据，待实验校准）" : labels[c.status] || labels.insufficient_history);
  ui($("accuracy-assessment"),parameterComparison(r) ? "已对照用户提供的真值，详见上方相对误差（真值未独立核验）" : "未验证：尚未计算真值相对误差");
  const list=$("training-evidence-list");list.replaceChildren();
  $("training-evidence").hidden=!c.thresholds;
  if (!c.thresholds)return;
  const add=(label,value)=>{const li=document.createElement("li");li.textContent=`${tr(label)}: ${value}`;list.append(li);};
  const t=c.thresholds;
  add("判据版本",c.version || "tail-stability-v1");
  add("窗口轮数 / 要求连续稳定次数",`${t.window_epochs} / ${t.required_stable_checks}`);
  add("末段连续稳定次数",`${c.stable_checks} / ${t.required_stable_checks}`);
  if (c.version==="two-window-stability-v2") {
    add("最少参数训练轮数",`${t.minimum_epochs}`);
    if (!Number.isFinite(c.loss_mean_change_pct))return;
    add("前窗口 / 后窗口轮次",`${c.previous_start_epoch}–${c.previous_end_epoch} / ${c.start_epoch}–${c.end_epoch}`);
    add("前窗口 / 后窗口平均损失",`${c.previous_mean_loss.toExponential(6)} / ${c.current_mean_loss.toExponential(6)}`);
    add("平均损失相对变化 / 要求",`${fmt(c.loss_mean_change_pct,6)}% / < ${t.loss_mean_change_pct}%`);
    add("平均损失绝对变化 / 容差",`${c.loss_absolute_change.toExponential(6)} / < ${t.loss_absolute_tolerance}`);
    add("损失绝对容差是否生效",tr(c.loss_absolute_tolerance_used ? "是" : "否"));
    for (const [key,value] of Object.entries(c.parameter_span_pct || {})) add("参数峰峰变化 / 阈值",`${key}: ${fmt(value,6)}% / < ${t.parameter_span_pct}%`);
    add("损失归一化分母下限",`${t.loss_scale_floor}`);
    return;
  }
  if (!Number.isFinite(c.loss_span_pct))return;
  add("评估轮次",`${c.start_epoch}–${c.end_epoch}`);
  add("损失峰峰变化 / 阈值",`${fmt(c.loss_span_pct,5)}% / ${t.loss_span_pct}%`);
  add("损失首尾变化（负值表示下降）",`${fmt(c.loss_change_pct,5)}%`);
  for (const [key,value] of Object.entries(c.parameter_span_pct || {})) add("参数峰峰变化 / 阈值",`${key}: ${fmt(value,5)}% / ${t.parameter_span_pct}%`);
  for (const [key,value] of Object.entries(c.selected_parameter_gap_pct || {})) add("返回参数与末段最大差异 / 阈值",`${key}: ${fmt(value,5)}% / ${t.selected_parameter_gap_pct}%`);
  add("末段平均损失与最佳损失差异 / 阈值",`${fmt(c.selected_loss_gap_pct,5)}% / ${t.selected_loss_gap_pct}%`);
  add("损失归一化分母下限",`${t.loss_scale_floor}`);
}
function displayReviewReasons() {
  const r=state.result, list=$("review-reason-list");list.replaceChildren();
  $("review-reasons").hidden = !r || (r.quality!=="需复核" && !resultReviewReasons(r).length);
  if ($("review-reasons").hidden) return;
  const labels={current_nrmse:"留出电流归一化 RMSE",voltage_nrmse:"留出电压归一化 RMSE",condition_number:"参数辨识条件数",
    pann_loss_unstable:"末段损失峰峰变化",pann_parameters_unstable:"末段参数最大峰峰变化",pann_selected_parameters_gap:"返回参数与末段最大差异",pann_selected_loss_gap:"末段平均损失与最佳损失差异",
    pann_loss_mean_change:"前后窗口平均损失相对变化",pann_parameter_window_change:"最近窗口参数最大峰峰变化"};
  const reasons=resultReviewReasons(r);
  for (const reason of reasons) {
    let text;
    if (labels[reason.code]) {
      const unit=reason.code!=="condition_number" ? "%" : "";
      const operator=["pann_loss_mean_change","pann_parameter_window_change"].includes(reason.code) ? "≥" : ">";
      text=`${tr(labels[reason.code])}: ${fmt(reason.value,6)}${unit} ${operator} ${reason.threshold}${unit}`;
    } else if (reason.code==="parameter_at_bound") text=tr("初始化或参数估计触及上下限，需检查参数约束及模型与数据是否匹配。");
    else if (reason.code==="pann_time_budget") {
      text=tr("PANN 达到训练时间预算，尚不能确认充分收敛。");
      if (Number.isFinite(reason.completed_epochs) && Number.isFinite(reason.requested_epochs)) text+=` ${tr("已完成轮数 / 请求轮数")}: ${reason.completed_epochs} / ${reason.requested_epochs}`;
    } else if (reason.code==="pann_insufficient_history") {
      text=tr("逐轮记录不足，无法完成连续窗口检查；请增加训练轮数或重新辨识。");
    } else if (reason.code==="pann_stability_short") {
      text=`${tr("末段连续稳定次数不足")}: ${reason.value} < ${reason.threshold}`;
    } else text=tr("此项复核原因尚无法显示，请查看计算条件与诊断。");
    const li=document.createElement("li");li.textContent=text;list.append(li);
  }
  if (!reasons.length) {const li=document.createElement("li");ui(li,"历史结果未记录可确认的触发原因，请查看计算条件与诊断或重新辨识。");list.append(li);}
}
function resultQuality(r) {
  return r.quality==="需复核" || resultReviewReasons(r).length>0 ? "需复核" : r.quality;
}
function displayResult({preserveZoom=false}={}) {
  $("result-archive-note").hidden=!state.savedRun;
  if(state.savedRun)ui($("result-archive-note"),state.result.chart
    ? "正在查看已保存实验。左侧设置与上方波形仍对应当前导入的数据。"
    : "正在查看已保存实验摘要，未包含完整波形和逐轮训练记录。左侧设置与上方波形仍对应当前导入的数据。");
  clearReportDownloads();
  displayTruthComparison();
  const r = state.result, p = r.parameters;
  ui($("result-l"), fmt(p.L_H*1e6,5)); ui($("result-c"), fmt(p.C_F*1e6,5)); ui($("result-r"), fmt(p.R_ohm,5));
  ui($("r-label"), r.R_estimated ? "负载电阻 R · 估计" : "负载电阻 R · 固定");
  const quality=resultQuality(r);
  ui($("result-badge"), quality); $("result-badge").className = "badge " + (quality==="需复核" ? "warning" : "good");
  displayAssessment();
  displayReviewReasons();
  $("validation").hidden = false; ui($("rmse-i"), fmt(r.metrics.current_rmse_A) + " A"); ui($("rmse-v"), fmt(r.metrics.voltage_rmse_V*1000) + " mV");
  const driveNote = r.diagnostics.pwm_reconstruction?.status === "complete"
    ? "PWM 重建已完成：仅使用训练段电压与已知控制器生成 0/1 开关信息，再固定 R 识别 L、C。\n" : "";
  ui($("result-notes"), `${driveNote}${r.validation_kind}\n${parameterErrorNote()}`);
  $("diagnostics").hidden = false;
  displayDiagnostics();
  $("ask-error").disabled = false; $("download-report").disabled = false;
  state.chart = state.savedRun ? state.summary?.chart || null : r.chart; if (!preserveZoom) state.zoom = null; drawCharts();
}
function displayDiagnostics() {
  if (!state.result) return;
  const r = state.result;
  // Translate display strings only. Keep the result and exported report intact.
  $("diagnostic-text").textContent = JSON.stringify(
    {method:r.diagnostics,conditions:r.options,warnings:r.warnings,validation_errors:r.metrics,review_reasons:resultReviewReasons(r)},
    (_key,value)=>typeof value === "string" ? tr(value) : value, 2);
}
function clearChat() {
  state.history = [];
  $("conversation-panel").classList.add("is-empty");
  $("chat-messages").querySelectorAll(".message").forEach(el => el.remove());
  $("chat-messages").querySelector(".welcome").hidden = false;
  $("chat-messages").querySelector(".suggestions").hidden = false;
}
function message(role, content, mode = "", images = []) {
  const article = document.createElement("article"); article.className = `message ${role}`;
  const label = document.createElement("div"); label.className = "message-label";
  if (role === "assistant") { const icon = document.createElement("span"); icon.className = "assistant-icon"; icon.textContent = "✳"; label.append(icon); }
  label.append(window.BuckI18n.node(role === "user" ? "你" : "实验助手"));
  if (mode) { const badge = document.createElement("span"); badge.className = "message-mode"; ui(badge, mode); label.append(badge); }
  const text = document.createElement("div"); text.className = "message-content"; text.textContent = content;
  if (role === "assistant" && window.BuckMarkdown) window.BuckMarkdown.render(text,content);
  article.append(label, text); renderMessageImages(article,images); $("chat-messages").append(article);
  $("chat-messages").scrollTop = $("chat-messages").scrollHeight;
  return article;
}
function replyTimeLabel(seconds, label = "本次回复用时") {
  return `${label} ${seconds.toFixed(2)} 秒`;
}
function startPendingReply(content, label) {
  const pending = message("assistant", content);
  const started = performance.now();
  const text = pending.querySelector(".message-content");
  text.classList.add("typing");
  ui(text, content);
  const timer = document.createElement("span");
  timer.className = "message-mode reply-timer";
  timer.setAttribute("role", "timer");
  timer.setAttribute("aria-live", "off");
  ui(timer, replyTimeLabel(0, label));
  pending.querySelector(".message-label").append(timer);
  const interval = setInterval(() => {
    ui(timer, replyTimeLabel((performance.now() - started) / 1000, label));
  }, 100);
  let elapsed;
  return () => {
    if (elapsed === undefined) {
      elapsed = (performance.now() - started) / 1000;
      clearInterval(interval);
      pending.remove();
    }
    return elapsed;
  };
}
function measurementLines(result, {forExport = false} = {}) {
  const report=result?.measurement_diagnostics;
  if(!report)return [tr("此历史结果未记录噪声与残差诊断，重新辨识后可获得。")];
  const flags={bias:"残差均值偏置",slow_variation:"残差缓慢变化",correlated_residual:"相邻残差相关",edge_concentration:"残差在开关边沿集中"};
  const comparisons={same_order:"与估计噪声同量级",above_noise:"高于估计噪声",below_noise:"低于估计噪声，需核对估计假设",unavailable:"噪声量级证据不足"};
  const lines=[];
  const pwm=result?.diagnostics?.pwm_reconstruction;
  if(pwm?.quality)lines.push(`${tr('PWM 重建质量检查')}: ${tr(pwm.quality.status==='passed'?'结构检查通过，不代表开关时刻已验证。':'重建不可信，已停止辨识。')} ${pwm.filter.method}, ${pwm.filter.window_cycles} ${tr('开关周期')}`);
  for(const [key,label] of [["current","电流"],["voltage","电压"]]){
    const c=report.channels[key];
    let text=`${tr(label)}: ${tr(comparisons[c.noise_comparison] || comparisons.unavailable)}`;
    if(Number.isFinite(c.noise_sigma))text+=` · ${tr("估计噪声标准差")} ${fmt(c.noise_sigma,5)} ${c.unit} · RMSE / σ = ${fmt(c.rmse_to_noise_ratio,4)}`;
    text+=` · ${tr("残差均值")} ${fmt(c.residual_mean,5)} ${c.unit}`;
    if(c.flags.length)text+=" · "+c.flags.map(flag=>tr(flags[flag] || flag)).join(" / ");
    else if(forExport)text+=" · "+tr("未触发形状筛查，不代表已通过白噪声检验");
    if(c.noise_above_application_threshold)text+=" · "+tr("估计噪声高于 5% 门槛，优先改善测量条件");
    lines.push(text);
  }
  if(report.edge_source!=="measured_heldout_PWM")lines.push(tr("没有实测留出 PWM，未评价开关边沿残差。"));
  if(forExport)lines.push(tr("当前仅评价同记录后段；参数区间与新记录预测尚未验证。"));
  return lines;
}
function displayPWMQuality(info){
  const valid=Boolean(info?.quality);$("pwm-quality-details").hidden=!valid;
  $("pwm-quality-details")._diagnostics=info;
  if(!valid)return;
  ui($("pwm-quality-summary"),info.quality.status==='passed'?'结构检查通过，不代表开关时刻已验证。':'重建不可信，已停止辨识。');
  $("pwm-quality-json").textContent=JSON.stringify(info,null,2);
}
let diagnosticReference=null, diagnosticReferenceUpload=0;
function selectedDiagnosticReference(){
  return diagnosticReference?.owner===sessionId && diagnosticReference.resultId===state.resultId && diagnosticReference.base===base ? diagnosticReference : null;
}
function refreshDiagnosticReference(){
  const ref=selectedDiagnosticReference();
  $("diagnostic-reference-file").disabled=!state.result;
  $("choose-diagnostic-reference").disabled=!state.result;
  $("diagnostic-reference-confirm").disabled=!ref;
  if(!ref)$("diagnostic-reference-confirm").checked=false;
  $("diagnostic-reference-status").textContent=ref ? tr("已选择参考：")+ref.filename : tr("未选择参考，仍可运行参数与初值交换对照。");
}
$("choose-diagnostic-reference").onclick=()=>$("diagnostic-reference-file").click();
$("diagnostic-reference-file").addEventListener('change',async event=>{
  const file=event.target.files[0];event.target.value='';
  if(!file || !state.result || state.chatBusy)return;
  const attempt=++diagnosticReferenceUpload;
  const context={owner:sessionId,resultId:state.resultId,base};
  diagnosticReference=null;refreshDiagnosticReference();
  try{
    const form=new FormData();form.append('file',file);
    const uploaded=await api('/api/datasets?reference=true',{method:'POST',body:form});
    if(attempt!==diagnosticReferenceUpload || context.owner!==sessionId || context.resultId!==state.resultId || context.base!==base)return;
    diagnosticReference={...context,dataset_id:uploaded.dataset_id,filename:uploaded.filename};
    refreshDiagnosticReference();
  }catch(error){notice(error.message);}
});
function decorateResponse(article, response) {
    if(response.diagnosis?.hypotheses?.length){
      const box=document.createElement('details');box.className='message-tools';
      const heading=document.createElement('summary');heading.textContent='误差假设与证据';box.append(heading);
      const statuses={open:'待检查',supported:'有证据支持',rejected:'已排除',unresolved:'尚不能区分',systematic_floor:'系统性误差下限'};
      for(const hypothesis of response.diagnosis.hypotheses){
        const p=document.createElement('p');p.textContent=`${hypothesis.cause} · ${statuses[hypothesis.status]||hypothesis.status}：${hypothesis.assessment}`;box.append(p);
      }
      const next=document.createElement('p');next.textContent=response.diagnosis.rationale||response.diagnosis.next_step||'';box.append(next);article.append(box);
    }
    if(response.simulation_evaluations?.length){
      const box=document.createElement('details');box.className='message-tools';
      const heading=document.createElement('summary');heading.textContent='仿真重新辨识与误差比较';box.append(heading);
      const pre=document.createElement('pre');pre.textContent=JSON.stringify(response.simulation_evaluations,null,2);box.append(pre);article.append(box);
    }
    if(response.plecs_experiments?.length){
      const box=document.createElement('details');box.className='message-tools';
      const title=document.createElement('summary');title.textContent='PLECS 对比实验记录';box.append(title);
      const pre=document.createElement('pre');pre.textContent=JSON.stringify(response.plecs_experiments,null,2);box.append(pre);article.append(box);
    }
    if(response.control_checks){
      const box=document.createElement('details');box.className='message-tools diagnostic-controls-evidence';
      const summary=document.createElement('summary');ui(summary,'诊断对照执行记录');box.append(summary);
      const report=response.control_checks;
      const p=document.createElement('p');
      ui(p,report.status==='completed'?'已完成四组前向对照，未重新训练；数值效应不等于硬件原因已证实。':'对照未完成，不能据此声称原因已验证。');box.append(p);
      if(report.error){const error=document.createElement('p');ui(error,report.error);box.append(error);}
      const labels={integral_measured:'积分参数 + 测量初值',integral_pann_state:'积分参数 + PANN 初值',pann_measured:'PANN 参数 + 测量初值',pann_pann_state:'PANN 参数 + PANN 初值'};
      for(const [name,row] of Object.entries(report.cases || {})){
        const item=document.createElement('p');item.append(window.BuckI18n.node(labels[name] || name),document.createTextNode(`: RMSE ${fmt(row.heldout_measured.current_rmse_A,5)} A / ${fmt(row.heldout_measured.voltage_rmse_V,5)} V`));box.append(item);
      }
      if(report.reference){const ref=document.createElement('p');ref.append(window.BuckI18n.node(report.reference.status==='evaluated_user_declared_reference'?'已与用户声明的干净参考比较。':'干净参考对照未执行。'));if(report.reference.reason)ref.append(document.createTextNode(' '),window.BuckI18n.node(report.reference.reason));box.append(ref);}
      article.append(box);
    }
    decorateConversationRetrieval(article,response.conversation_retrieval);
    if(response.repeatability && (response.repeatability.groups?.length || response.repeatability.excluded_missing_context)){
      const box=document.createElement("details");box.className="message-tools repeatability-evidence";
      const title=document.createElement("summary");ui(title,"多次实验一致性依据");box.append(title);
      const list=document.createElement("ul");box.append(list);
      const add=(...parts)=>{const li=document.createElement("li");li.append(...parts.map(text=>window.BuckI18n.node(text)));list.append(li);};
      const groups=response.repeatability.groups || [];
      if(!groups.length)add("缺少可比较的完整实验配置，暂不计算离散程度。");
      for(const group of groups){
        add(`${group.method} / ${group.pwm_source} / ${group.mode}: `,"不同 CSV 数",`: ${group.distinct_files}; `,"排除同文件重复运行",`: ${group.same_file_reruns_excluded}`);
        if(group.distinct_files<2)add("至少需要两份不同 CSV 才能计算观察到的离散程度。");
        for(const [key,p] of Object.entries(group.parameters)){
          const factor=key==='R_ohm'?1:1e6,unit=key==='L_H'?'μH':key==='C_F'?'μF':'Ω';
          add(`${key[0]}: `,"观察范围",` ${fmt(p.min*factor,6)}–${fmt(p.max*factor,6)} ${unit}; CV = ${fmt(p.cv_pct,4)}%`);
        }
      }
      add("需确认同一电路与独立采集；观察范围不是置信区间，也不是新工况预测验证。");
      if(response.repeatability.omitted_groups)add("未展示分组数",`: ${response.repeatability.omitted_groups}`);
      article.append(box);
    }
    const timing = [
      [response.reply_elapsed_s, response.task_state ? "本次等待用时" : "本次回复用时"],
      [response.recovery_elapsed_s, "本次恢复用时"],
      [response.agent_budget?.elapsed_s, "后台处理用时"],
    ].find(([seconds]) => Number.isFinite(seconds) && seconds >= 0);
    if (response.route?.label || timing) {
      const task = document.createElement("div"); task.className = "message-tools message-task";
      if (response.route?.label) task.append(window.BuckI18n.node("任务："), window.BuckI18n.node(response.route.label));
      if (timing) {
        if (response.route?.label) task.append(" · ");
        task.append(window.BuckI18n.node(replyTimeLabel(...timing)));
      }
      if(response.ui_action?.parser==='deepseek')task.append(document.createTextNode(" · "),window.BuckI18n.node("DeepSeek 计划解析"));
      article.querySelector(".message-label").after(task);
    }
    if (response.tool_calls?.length) {
      const log = document.createElement("div"); log.className = "message-tools";
      ui(log, response.tool_calls.map(t => `${tools[t.name] || t.name} · ${t.status}${t.query ? ` · ${t.query} (${t.count})` : ""}`).join("\n")); article.append(log);
    }
}
function configurationFingerprint() {
  return JSON.stringify({conversation:activeConversation?.id,session:sessionId,base,
    dataset:state.dataset?.dataset_id,sha256:state.dataset?.sha256,mapping:state.mapping,result:state.resultId,
    truth:state.truthDraft,reference:selectedDiagnosticReference(),referenceConfirmed:$('diagnostic-reference-confirm').checked,
    fields:experimentFields.filter(id=>id!=="chat-images").map(id=>[$(id).id,$(id).value]),
    mappingFields:["map-time","map-current","map-voltage","map-pwm","time-unit","current-unit","voltage-unit"].map(id=>$(id).value)});
}
function validTuningInterval(changes){
  const start='start_s' in changes?changes.start_s:($('start-ms').value===''?null:Number($('start-ms').value)/1000);
  const end='end_s' in changes?changes.end_s:($('end-ms').value===''?null:Number($('end-ms').value)/1000);
  return start===null||end===null||start<end;
}
async function applyIdentificationAction(action, context) {
  const stale="会话、数据或设置已变化，本次操作未应用。请按当前界面重新发送指令。";
  if (!context || action.id!==context.request_id || configurationFingerprint()!==context.fingerprint) return tr(stale);
  if(action.type==='configure_plecs_ports')return applyPlecsPorts(action,context);
  if(action.type==="cancel_pending_identification"){
    const key=`buck-ui-action:${sessionId}:${action.id}`;
    if(sessionStorage.getItem(key))return tr("此操作已处理，未重复应用或启动。");
    sessionStorage.setItem(key,"processed");
    pendingConfigurationStart=null;
    const plan=currentIdentificationPlan();
    if(plan&&!['complete','cancelled'].includes(plan.status)){
      plan.status='cancelled';plan.note="已取消后续任务；已提交的计算仍会完成并保存。";
      saveIdentificationPlan(plan);renderIdentificationPlan();
    }
    return tr("已取消后续任务；已提交的计算仍会完成并保存。");
  }
  if(action.type==="identification_plan")return acceptIdentificationPlan(action,context);
  if (action.type!=="configure_identification" || typeof action.open_panel!=="boolean" || typeof action.start_after_apply!=="boolean") throw new Error("配置操作格式无效。");
  const changes=action.changes;
  if (!validConfigurationChanges(changes) || !validConfigurationExtras(action)
      || ("method" in changes && !["pann","weighted_integral"].includes(changes.method))
      || ("mode" in changes && !["LC","LCR"].includes(changes.mode))
      || ("resistance_ohm" in changes && !(typeof changes.resistance_ohm==="number" && Number.isFinite(changes.resistance_ohm) && changes.resistance_ohm>.01 && changes.resistance_ohm<=100000))) throw new Error("配置操作格式无效。");
  if(action.start_after_apply && currentIdentificationPlan()?.status!=='paused' &&
      (new Set(action.execution_selection?.methods).size>1 || new Set(action.execution_selection?.pwm_sources).size>1))
    throw new Error('多个执行选项需要辨识计划，请重新提交计划。尚未启动辨识。');
  if(action.start_after_apply && currentIdentificationPlan()?.status==='paused'){
    const plan=currentIdentificationPlan(), selected=action.execution_selection?.pwm_sources || [];
    const expected=plan.version===3 ? ['measured','controller'] :
      [plan.changes?.pwm_source || plan.execution_selection?.pwm_sources?.[0] || $('pwm-source').value];
    const missing=executionSelectionQuestion(action.execution_selection,plan.methods,expected);
    if(missing)return missing;
    if(selected.length && (selected.some(source=>!expected.includes(source)) || expected.some(source=>!selected.includes(source))))
      throw new Error('本次选择的 PWM 来源与暂停计划不同。请先取消原计划，再启动新的计划。');
  }
  const key=`buck-ui-action:${sessionId}:${action.id}`;
  if (sessionStorage.getItem(key))return tr("此操作已处理，未重复应用或启动。");
  if (state.job || state.busy || state.syncBusy)return tr("已有任务或同步正在进行，本次未修改配置或启动新实验。");
  // Check the backend too: a submission from a previous page may still be running.
  const active=await api("/api/jobs/active",{timeout:10000});
  if (configurationFingerprint()!==context.fingerprint)return tr(stale);
  if(active.job?.job_id){void watchJob(active.job.job_id);return tr("已有任务或同步正在进行，本次未修改配置或启动新实验。");}
  if (state.busy || state.syncBusy || state.job)return tr("已有任务或同步正在进行，本次未修改配置或启动新实验。");
  if (changes.method==="pann" && $("fit-method").querySelector('[value="pann"]').disabled)return tr("后端未安装 PyTorch，未切换方法或启动实验。");
  const staged=await prepareConfigurationExtras(action,context);
  const selectedSource=changes.pwm_source||$('pwm-source').value;
  const targetMapping=staged.mapping||state.mapping;
  const targetSource=selectedSource==='auto'?(targetMapping&&!targetMapping.pwm?'controller':'measured'):selectedSource;
  const loadOnly=staged.dataset && !Object.keys(changes).length && !action.start_after_apply;
  if(!loadOnly && targetSource==='controller'&&(changes.mode||$('fit-mode').value)!=='LC')return tr('重建 PWM 只支持固定 R 的 LC 模式，本次未修改设置。');
  // Mark before applying; replaying stored replies/retrying the same response cannot run twice.
  sessionStorage.setItem(key,"processed");
  pendingConfigurationStart=null;
  const applied=await applyConfigurationExtras(action,staged);
  if (action.open_panel || Object.keys(changes).length || action.start_after_apply) {
    toggleSection("toggle-experiment","experiment-content","实验配置",true);
    setIdentificationOpen(true);
  }
  applied.push(...applyConfigurationChanges(changes,{resetConfirmation:Boolean(staged.dataset||staged.mappingChanged||staged.imageChanged)}));
  controls();persistConversation();
  const prefix=tr(applied.length ? "辨识设置已应用。" : "参数辨识面板已打开。")+(applied.length ? ` ${applied.join(" · ")}。` : "");
  if(action.start_after_apply || action.request_selection){
    const question=executionSelectionQuestion(action.execution_selection,[$('fit-method').value],[pwmSource()],{allowAuto:$('pwm-source').value==='auto'});
    if(question)return prefix+'\n\n'+question;
  }
  if (!action.start_after_apply){
    const review=action.open_panel||Boolean(staged.dataset||staged.mapping)||Object.keys(changes).length;
    return prefix+" "+tr(state.result?"未启动实验，右侧保留已有结果。":"未启动实验。")+(review?identificationReviewFeedback():"");
  }
  const plan=currentIdentificationPlan();
  if(plan&&plan.status==='paused'){
    plan.execution_selection=action.execution_selection;
    plan.status='queued';saveIdentificationPlan(plan);return prefix+' '+tr('将继续已有辨识计划，并重新检查实验条件。');
  }
  if (!state.dataset) {toggleSection("toggle-import","import-content","导入波形",true);return prefix+" "+tr("请先上传 CSV；本次未启动实验。");}
  if (!state.mapping) {$("mapping-details").open=true;return prefix+" "+tr("请补齐并应用时间、电流、电压列映射；本次未启动实验。");}
  if (pwmSource()==="controller" && $("controller-confirmed").value!=="yes") {
    pendingConfigurationStart={fingerprint:configurationFingerprint()};
    $("controller-settings").hidden=false;return prefix+' '+tr('请核对并确认：当前电路接线与控制图一致，参数也与当前设置一致吗？回复“确认”后继续本次辨识；确认前未启动实验。');
  }
  if (pwmSource()==="measured" && !state.mapping.pwm)return prefix+" "+tr("请映射实际 PWM 列，或手动选择并确认控制器重建；本次未启动实验。");
  if (resultMatchesCurrent(state.result))return prefix+" "+tr("当前配置已有完成结果，未重复启动实验。");
  const started=await startFit({fromChat:true,executionSelection:action.execution_selection});
  return prefix+" "+(started?.job_id ? tr("辨识任务已提交，进度与结果将在右侧显示。") : tr("未能启动实验：")+tr(started?.error || "请检查当前任务和输入设置。"));
}
async function sendMessage(value, {analysisRequest=false}={}) {
  const images=chatImages();
  const text = value.trim() || (images.length ? tr("请分析这些图片。") : ""); if (!text || historyLocked() || !activeConversation) return;
  if (!state.connected) { window.alert("Backend is not connected. Please connect to the backend and try again."); return; }
  const currentOnly=analysisRequest;
  const targetResultId=state.resultId;
  if(currentOnly && (!state.result || !targetResultId)){notice("当前显示结果已变化或不可用，请在右侧重新选择要分析的已保存结果后再试。");return;}
  const corpus=currentOnly ? [] : conversationMessages(readConversation(activeConversation.id));
  const analysisMeta=currentOnly ? analysisDescriptor() : null;
  // Read-only analysis belongs to the displayed completed run, not unsubmitted form edits.
  const analyzeResult = Boolean(state.result);
  const analysisMapping = analyzeResult ? state.result.mapping : state.mapping;
  let fitOptions;
  try { fitOptions = analyzeResult ? structuredClone(state.result.options) : options(); } catch { /* Invalid form drafts do not block chat. */ }
  state.chatBusy = true;
  let finishPending;
  try {
    controls();
    $("conversation-panel").classList.remove("is-empty");
    $("chat-input").value = "";
    $("chat-messages").querySelector(".welcome").hidden = true; $("chat-messages").querySelector(".suggestions").hidden = true;
    const userArticle=message("user", text,"",images);
    const previousHistory = currentOnly ? [] : readConversation(activeConversation.id).messages.filter(m=>m.mode!=="请求未完成")
      .slice(-10).map(m=>({role:m.role,content:m.content.slice(0,8000),...(m.images?.length?{images:m.images}:{})}));
    const requestMessage=rememberMessage("user", text, analysisMeta ? {analysis_request:analysisMeta} : null,"",images);
    setChatImages([]);persistConversation();
    userArticle.dataset.messageId=requestMessage.id;
    finishPending = startPendingReply("正在读取实验上下文…");
    // Restore unanalysed input from UI state. A chat must not discard a displayed
    // result because its source cache expired; execution restores inputs in startFit.
    let datasetAvailable=Boolean(state.dataset);
    if (state.mapping && !analyzeResult) {
      try { await ensureDatasetAvailable(); } catch (error) { datasetAvailable=false;notice(error.message); }
    }
    if(currentOnly && (!state.result || state.resultId!==targetResultId))throw new Error("当前显示结果已变化或不可用，请在右侧重新选择要分析的已保存结果后再试。");
    const comparison=fitOptions ? parameterComparison() : null;
    // Persist the displayed result in the task request even for a freshly
    // completed run; backend job/dataset caches can disappear after a restart.
    const savedResult=analyzeResult ? displayedExperiment() : null;
    let actionContext=null;
    if (!currentOnly) {
      try {
        const draftOptions=options(), epochInput=$("pann-epochs");
        // Integral mode does not use the hidden epoch field. An unfinished
        // epoch edit must not make every unrelated chat request fail with 422.
        if (validNumericInput(epochInput)) draftOptions.epochs=Number(epochInput.value);
        draftOptions.pwm_denoise=$('pwm-denoise').value;
        if(validNumericInput($('pwm-filter-cycles')))draftOptions.pwm_filter_cycles=Number($('pwm-filter-cycles').value);
        draftOptions.controller=BuckCircuit.read();
        const catalog=await configurationCatalog({includeRemote:true});
        actionContext={request_id:requestMessage.id,options:draftOptions,fingerprint:configurationFingerprint(),catalog,plecs:localPlecsConfig()};
      } catch { /* Invalid drafts remain a form validation error. */ }
    }
    try { await syncPlecsConfig(); } catch (error) { notice(error.message); }
    const response = await window.BuckHarness.request({message:text,reasoning_mode:$("reasoning-mode").value,language:window.BuckI18n.language,images,history:previousHistory,current_result_analysis:currentOnly,circuit_filename:$("controller-image-name").value,
      ui_context:actionContext ? {request_id:actionContext.request_id,options:actionContext.options,pwm_source_setting:$('pwm-source').value,
        plecs_ports:actionContext.plecs ? plecsPortContext(actionContext.plecs) : null,
        mapping:state.dataset?mappingFromForm():null,columns:state.dataset?.columns||[],catalog:publicConfigurationCatalog(actionContext.catalog),
        previous_local_csv_action:isLocalBackend(base)?previousLocalCsvAction(activeConversation):null,
        pending_start:pendingConfigurationStart?.fingerprint===actionContext.fingerprint,
        paused_plan:currentIdentificationPlan()?.status==='paused',
        pending_reference_confirmation:Boolean(selectedDiagnosticReference()&&!$('diagnostic-reference-confirm').checked)} : undefined,
      dataset_id:!datasetAvailable || state.savedRun ? null : savedResult
        ? (savedResult.dataset.sha256 && savedResult.dataset.sha256===state.dataset?.sha256 ? state.dataset.dataset_id : null)
        : state.mapping ? state.dataset?.dataset_id : null,mapping:analysisMapping,
      saved_result:savedResult || undefined,
      options:fitOptions,result_id:fitOptions ? state.resultId : null,
      enable_diagnostic_controls:analyzeResult,
      diagnostic_reference:analyzeResult && selectedDiagnosticReference() && $("diagnostic-reference-confirm").checked ? {dataset_id:diagnosticReference.dataset_id,result_id:state.resultId,confirmed_same_experiment:true} : undefined,
      ground_truth:comparison ? {result_id:state.resultId,...comparison.ground_truth} : undefined,
      conversation_messages:corpus,
      // Displayed result and editable form are separate contexts. The Agent chooses tools.
      experiments:currentOnly ? [] : chatExperiments(),result_library:currentOnly ? [] : chatResultLibrary(),
      identification_enabled:!currentOnly && !savedResult && state.identificationOpen && Boolean(fitOptions)});
    if (response.mode === "provider_error") {
      response.reply_elapsed_s = finishPending();
      const failureText = window.BuckHarness.replyText(response,response.provider_error);
      const article = message("assistant", failureText, "请求未完成");
      decorateResponse(article, response);
      const failedReply = rememberMessage("assistant", failureText, {
        request_message_id:requestMessage.id, route:response.route, reply_elapsed_s:response.reply_elapsed_s, tool_calls:response.tool_calls,
        agent_budget:response.agent_budget, plecs_experiments:response.plecs_experiments,diagnosis:response.diagnosis,simulation_evaluations:response.simulation_evaluations,
        control_checks:response.control_checks, retrieved_sources:response.retrieved_sources,
        error_code:response.error_code,budget_reason:response.budget_reason,finish_reason:response.finish_reason,
        agent_task_id:response.agent_task_id,conversation_retrieval:response.conversation_retrieval
      }, "请求未完成");
      article.dataset.messageId=failedReply.id;
      window.alert(response.provider_error);
      if (response.job_id) { invalidateResult(); void watchJob(response.job_id); }
      return;
    }
    if (response.ui_action) {
      try {response.answer=await applyIdentificationAction(response.ui_action,actionContext);}
      catch(error){response.error_code='ui_action_failed';response.answer=tr("未能应用配置操作：")+tr(error.message);}
      response.ui_action_status=response.answer;
      window.BuckHarness.showOutcome(window.BuckHarness.replyText(response,response.answer),response.error_code?'界面操作失败':'界面处理结果');
    }
    response.reply_elapsed_s = finishPending();
    const answer = window.BuckHarness.replyText(response,response.answer);
    const replyMode=response.mode === "deepseek" && !response.error_code ? "DeepSeek" : "请求未完成";
    const article = message("assistant", answer, replyMode);
    decorateResponse(article, response);
    const savedReply=rememberMessage("assistant", answer, {route:response.route, reply_elapsed_s:response.reply_elapsed_s, tool_calls:response.tool_calls, sources:response.sources,retrieved_sources:response.retrieved_sources,
      error_code:response.error_code,
      agent_task_id:response.agent_task_id,diagnosis:response.diagnosis,request_message_id:requestMessage.id,conversation_retrieval:response.conversation_retrieval,
      repeatability:response.repeatability,
      control_checks:response.control_checks,
      agent_budget:response.agent_budget,plecs_experiments:response.plecs_experiments,simulation_evaluations:response.simulation_evaluations,
      ...(response.ui_action ? {ui_action:response.ui_action,ui_action_status:response.ui_action_status} : {}),
      ...(analysisMeta && !response.job_id ? {analysis:{...analysisMeta,request_id:requestMessage.id}} : {})}, replyMode);
    article.dataset.messageId=savedReply.id;
    if (response.job_id) { invalidateResult(); void watchJob(response.job_id); }
  } catch (error) {
    const elapsed = finishPending?.();
    const note = window.BuckHarness.taskNotice(error);
    const text = note?.text || error.message;
    const mode = note ? "任务状态" : "请求未完成";
    const metadata = {...note?.response, reply_elapsed_s:elapsed};
    const article = message("assistant",text,mode);
    decorateResponse(article,metadata);
    rememberMessage("assistant",text,metadata,mode);
    if (!note) window.alert("Chat request failed. Please check the backend connection and try again.");
  } finally {
    finishPending?.();
    state.chatBusy = false;
    controls();
    $("chat-messages").scrollTop = $("chat-messages").scrollHeight;
    startQueuedIdentificationPlan();
  }
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
  if (state.result && !state.savedRun && state.result.train_end_s < hi) {
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
let reportExportAttempt = 0;
let reportExportBusy = false;
const reportDownloadUrls = [];
function clearReportDownloads() {
  reportExportAttempt++;
  reportExportBusy = false;
  $("report-location-dialog").close();
  for (const url of reportDownloadUrls) URL.revokeObjectURL(url);
  reportDownloadUrls.length = 0;
  $("report-downloads").hidden = true;
  $("download-report-files").hidden=false;
  for (const id of ["report-pdf-link", "report-json-link"]) {
    $(id).hidden = true; $(id).removeAttribute("href"); $(id).removeAttribute("download");
  }
}
function prepareReportDownload(blob, filename, id) {
  const link = $(id), url = URL.createObjectURL(blob);
  reportDownloadUrls.push(url);
  link.href = url; link.download = filename; link.hidden = false;
  return link;
}
function downloadReportPair() {
  $("report-json-link").click();
  $("report-pdf-link").click();
}
async function exportReports({filesOnly=false}={}) {
  if (!state.result || reportExportBusy) return;
  if(!filesOnly){
    const reason=reportFolderUnavailableReason();
    if(reason){
      ui($("report-location-reason"),reason);
      $("report-location-dialog").showModal();
      return;
    }
  }
  clearReportDownloads();
  const attempt = reportExportAttempt;
  reportExportBusy = true; $("download-report").disabled = true;$("download-report-files").disabled=true;
  // Start the native picker before potentially expensive snapshot serialization.
  // Keep the original click activation; consume the promise inside try below.
  let picker;
  if(!filesOnly){
    try{picker=window.showDirectoryPicker({id:'buck-report-export',mode:'readwrite'});}
    catch(error){picker=Promise.reject(error);}
  }
  // Capture once: editing inputs or switching conversations must never mix runs.
  const exportedAt = new Date(), language = window.BuckI18n.language;
  const report = structuredClone({application:"Buck Lab 0.1.0",exported_at:exportedAt.toISOString(),summary:state.savedRun ? null : state.summary,
    result:{...state.result,quality:resultQuality(state.result),parameter_comparison:parameterComparison(),parameter_error:parameterComparison(),parameter_error_note:parameterErrorNote(),assessment:resultAssessment(state.result),review_reasons:resultReviewReasons(state.result)},conversation:state.history});
  const run=activeConversation?.experiments?.find(item=>item.id===state.resultId);
  const filename = run ? experimentReportName(run) : reportFilename(report.result, exportedAt);
  const notes = (report.result.warnings || []).map(note=>tr(note));
  if(report.result.measurement_diagnostics)notes.push(...measurementLines(report.result, {forExport: true}),tr("训练段局部噪声估计不是传感器标定；残差线索不改变 5% 应用门槛。"));
  $("report-downloads").hidden = false;
  const stem=filename.replace(/\.json$/i,''),json=new Blob([JSON.stringify(report,null,2)],{type:"application/json;charset=utf-8"});
  let parent=null,pdfReady=false;
  try {
    // The picker must run in the original click gesture, before any network await.
    if(!filesOnly){
      ui($("report-export-status"),"请选择保存位置，将在其中创建同名报告文件夹。");
      parent=await picker;
      if(attempt!==reportExportAttempt)return;
    }
    ui($("report-export-status"), "正在生成 PDF 和 JSON…");
    prepareReportDownload(json,filename,"report-json-link");
    const pdf = await api("/api/reports/pdf", {method:"POST",asBlob:true,body:{language,exported_at:report.exported_at,result:report.result,notes}});
    if (attempt !== reportExportAttempt) return;
    if (pdf.type !== "application/pdf" || await pdf.slice(0,5).text() !== "%PDF-") throw new Error("后端未返回有效的 PDF，请更新后端后重试。");
    if(attempt!==reportExportAttempt)return;
    pdfReady=true;prepareReportDownload(pdf,filename.replace(/\.json$/,".pdf"),"report-pdf-link");
    $("download-report-files").hidden=true;
    if(parent){
      const savedName=await saveReportFolder(parent,stem,json,pdf,()=>attempt===reportExportAttempt);
      if(attempt!==reportExportAttempt)return;
      const status=$("report-export-status");status.replaceChildren(window.BuckI18n.node("报告已保存到："),document.createTextNode(`${parent.name}/${savedName}/`));
    }else{
      downloadReportPair();
      ui($("report-export-status"),"PDF 和 JSON 已生成，保存位置由浏览器下载设置决定。如需每次选择位置，请在浏览器下载设置中启用保存前询问；如下载被拦截，请点击下方链接分别保存。");
    }
  } catch (error) {
    if (attempt !== reportExportAttempt) return;
    if(error.name==='AbortError' && !pdfReady && $('report-json-link').hidden)ui($("report-export-status"),"已取消导出。");
    else if(pdfReady){
      downloadReportPair();
      ui($("report-export-status"),"文件夹保存未完成，目标位置可能已有部分文件。已改为下载 PDF 和 JSON；如下载被拦截，请点击下方链接分别保存。");
    }else if($('report-json-link').hidden){
      ui($("report-export-status"),"无法访问所选位置。请重新选择，或改为分别下载 PDF 和 JSON。");
    }else{
      ui($("report-export-status"), "PDF 生成失败，JSON 仍可下载。请检查后端连接与版本后重新导出。");notice(error.message);
    }
  } finally {
    if (attempt === reportExportAttempt) {
      reportExportBusy = false; $("download-report").disabled = !state.result;$("download-report-files").disabled=!state.result;
    }
  }
}
function reportFilename(result, date = new Date()) {
  const day = `${String(date.getMonth()+1).padStart(2,"0")}${String(date.getDate()).padStart(2,"0")}`;
  const stem = String(result.filename || "dataset").replace(/\.csv$/i, "");
  const safeStem = stem.normalize("NFKC").replace(/[^\p{L}\p{N}-]+/gu,"-").slice(0,48).replace(/^-+|-+$/g,"") || "dataset";
  const condition = stem.match(/(?:^|[^a-z0-9])case\s*\d+([ab])(?=[^a-z0-9]|$)/i)?.[1]?.toUpperCase() || safeStem;
  // Noise and seed labels describe the input filename, never an estimate from the waveform.
  const noiseMatch = stem.match(/(?:^|[_ -])noise(\d{1,2})(?=[_ -]|$)/i);
  const pctMatch = stem.match(/(?:^|[_ -])(\d+(?:\.\d+)?)pct(?=[_ -]|$)/i);
  const noise = noiseMatch ? `${Number(noiseMatch[1])}pct` : pctMatch ? `${Number(pctMatch[1])}pct`
    : /(?:^|[_ -])clean(?=[_ -]|$)/i.test(stem) ? "clean" : "noiseNA";
  const seed = stem.match(/(?:^|[_ -])(?:seed|s)(\+?\d+)(?=[_ -]|$)/i)?.[1];
  const opts = result.options || {};
  const drive = result.diagnostics?.pwm_reconstruction?.status === "complete" || opts.pwm_source === "controller" ? "rebuilt"
    : opts.pwm_source === "measured" || result.diagnostics?.drive_source === "measured PWM" ? "measured" : "pwmNA";
  const method = {pann:"PANN",weighted_integral:"WIntegral",integral:"Integral"}[result.method || opts.method] || "methodNA";
  const window = method !== "PANN" ? "NA" : opts.pann_window_cycles === 0 ? "auto"
    : [1,2,3].includes(opts.pann_window_cycles) ? `${opts.pann_window_cycles}cycle` : "windowNA";
  return `${day}_${condition}_${noise}_${seed === undefined ? "seedNA" : "s"+Number(seed)}_${drive}_${method}_${window}.json`;
}
function downloadReport() {
  void exportReports();
}
$("download-report-files").onclick=()=>void exportReports({filesOnly:true});
$("close-report-location").onclick=()=>$("report-location-dialog").close();
$("confirm-report-files").onclick=()=>{$("report-location-dialog").close();void exportReports({filesOnly:true});};
ui($("download-report"),reportFolderUnavailableReason() ? "↓ 导出报告（PDF + JSON）" : "↓ 导出报告文件夹");
setupChatImages();
$("csv-file").onchange = event => { if(event.target.files[0]) void importData(event.target.files[0]); };
$("close-notice").onclick = () => notice();
$("load-demo").onclick = $("empty-demo").onclick = () => void importData();
$("toggle-result-library").onclick=toggleResultLibrary;
document.addEventListener("buck-language-change",renderResultLibrary);
$("toggle-csv-library").onclick = () => void toggleCsvLibrary();
$("dropzone").ondragover = event => {event.preventDefault();$("dropzone").classList.add("dragover");};
$("dropzone").ondragleave = () => $("dropzone").classList.remove("dragover");
$("dropzone").ondrop = event => {event.preventDefault();$("dropzone").classList.remove("dragover");if(event.dataTransfer.files[0])void importData(event.dataTransfer.files[0]);};
$("apply-mapping").onclick = async()=>{state.busy=true;controls();try{await applyMapping();notice("列映射已应用。",true);}catch(e){notice(e.message);}finally{state.busy=false;persistConversation();controls();}};
for(const id of ["map-time","map-current","map-voltage","map-pwm","time-unit","current-unit","voltage-unit"]) $(id).onchange=()=>{state.mapping=null;invalidateResult();ui($("job-status"), "映射已更改，请点击应用并检查数据");controls();};
for(const id of ["vin","frequency","fit-mode","fixed-r","fit-method","pann-state","pann-cycles","pann-epochs","start-ms","end-ms","train-fraction"]) $(id).oninput=()=>{
  if(["vin","frequency","fixed-r"].includes(id) && pwmSource()==="controller") $("controller-confirmed").value="no";
  $("fixed-r-label").hidden=$("fit-mode").value!=="LC";ui($("train-label"), $("train-fraction").value+"%");controls();
  if(state.mapping)readyStatus();
  persistConversation();
};
$("pwm-source").onchange=()=>{
  prepareControllerDefaults();
  controls(); if (state.mapping) readyStatus(); persistConversation();
};
$("controller-confirmed").onchange=()=>{controls();if(state.mapping)readyStatus();persistConversation();};
for(const id of ['pwm-denoise','pwm-filter-cycles'])$(id).addEventListener('change',()=>{controls();persistConversation();});
document.addEventListener("buck-circuit-change",()=>{
  $("controller-confirmed").value="no";controls();
  if(state.mapping)readyStatus();persistConversation();
});
$("identify").onclick=()=>void startFit();
$("calculate-parameter-error").onclick=calculateParameterError;
for (const [id,key] of [["truth-l","l"],["truth-c","c"]]) $(id).oninput=()=>{
  state.truthDraft[key]=$(id).value;state.truthComparison=null;state.truthError="";
  captureExperiment({updateTruth:true});
  if(state.result)displayResult({preserveZoom:true});else displayTruthComparison();
  persistConversation();
};
$("enter-identification").onclick=()=>setIdentificationOpen(true);
for (const side of ["left", "right"]) {
  $(`toggle-${side}-pane`).onclick=()=>setPaneCollapsed(side,!$("workspace").classList.contains(`${side}-collapsed`));
}
$("leave-identification").onclick=()=>setIdentificationOpen(false);
$("toggle-import").onclick=()=>toggleSection("toggle-import","import-content","导入波形");
$("view-full").onclick=()=>{state.zoom=null;drawCharts();};
$("view-tail").onclick=()=>{if(state.chart){const t=state.chart.time_s;const frequency=(!state.savedRun && state.result?.options.frequency_hz) || Number($("frequency").value)*1000;state.zoom=[Math.max(t[0],t[t.length-1]-10/frequency),t[t.length-1]];drawCharts();}};
$("chat-form").onsubmit=event=>{event.preventDefault();void sendMessage($("chat-input").value);};
$("toggle-chat-compose").onclick=()=>setComposerCollapsed(!$("chat-form").classList.contains("compose-collapsed"));
$("compact-chat-images").onclick=()=>setComposerCollapsed(false);
setComposerCollapsed(savedValue("buck-compose-collapsed")==="true");
$("chat-input").onkeydown=event=>{
  if(event.key!=="Enter" || event.isComposing)return;
  if(event.shiftKey){if($("chat-form").classList.contains("compose-collapsed"))setComposerCollapsed(false);}
  else{event.preventDefault();void sendMessage($("chat-input").value);}
};
document.querySelectorAll("[data-prompt]").forEach(button=>button.onclick=()=>void sendMessage(button.dataset.prompt));
function closeAnalysisMenu() {
  $("analysis-menu").hidden=true;$("ask-error").setAttribute("aria-expanded","false");
  $("ask-error").disabled=historyLocked() || !state.result;
}
$("ask-error").onclick=()=>{
  if(!$("analysis-menu").hidden){closeAnalysisMenu();return;}
  if (historyLocked() || !state.result) return;
  $("analysis-menu").hidden=false;$("ask-error").setAttribute("aria-expanded","true");
  $("analyze-here").focus();
};
$("analyze-here").onclick=()=>{closeAnalysisMenu();void sendMessage(tr("请分析当前辨识结果的误差，并给出有依据的修改计划。"),{analysisRequest:true});};
$("analyze-new").onclick=()=>{closeAnalysisMenu();void analyzeInNewConversation();};
document.addEventListener("keydown",event=>{
  if($("analysis-menu").hidden || !event.target.closest(".analysis-action"))return;
  if(event.key==="Escape"){event.preventDefault();closeAnalysisMenu();$("ask-error").focus();}
  if(["ArrowDown","ArrowUp"].includes(event.key)){
    event.preventDefault();(document.activeElement===$("analyze-here")?$("analyze-new"):$("analyze-here")).focus();
  }
});
$("compare-analyses").onclick=()=>{
  const input=$("chat-input");
  if (!input.value.trim()) {
    input.value=tr("请检索本会话中关于……的讨论，并根据原文总结结论与后续安排。");
    persistConversation();
    input.focus();
    const placeholder=/…+/.exec(input.value);
    if (placeholder) input.setSelectionRange(placeholder.index,placeholder.index+placeholder[0].length);
  } else input.focus();
};
$("new-chat").onclick=()=>void newConversation();$("download-report").onclick=downloadReport;
$("delete-chat").onclick=()=>void deleteConversation();
$("clear-data").onclick=async()=>{
  if (historyLocked() || !state.dataset) return;
  state.busy=true; controls();
  try {
    try { await api(`/api/datasets/${state.dataset.dataset_id}`,{method:"DELETE"}); }
    catch(error) { if(error.status!==404) throw error; }
    await window.BuckAttachments.remove(csvScope());
    activeConversation.file_epoch=Math.max(Date.now(),(activeConversation.file_epoch || 0)+1);
    resetExperiment(); persistConversation();
  }catch(error){notice(error.message);}
  finally {state.busy=false;controls();}
};
$("open-settings").onclick=async()=>{$("api-url").value=base;$("access-token").value=accessToken;$("sync-code").value=archiveKey;$("plecs-executable").value='';ui($("settings-status"), "");$("settings-dialog").showModal();
  $("plecs-model-directory").value='';
  updatePlecsControls();
  const rememberedPlecs=localPlecsConfig();
  fillPlecsBinding(rememberedPlecs?.analysis_binding);
  if(rememberedPlecs){$("plecs-executable").value=rememberedPlecs.executable;$("plecs-model-directory").value=rememberedPlecs.model_directory||'';}
  try{if(['localhost','127.0.0.1','[::1]'].includes(new URL(base).hostname)){
    const selectedBase=base, owner=sessionId;const p=await api('/api/plecs/settings',{timeout:5000});
    if(base!==selectedBase || sessionId!==owner || !$("settings-dialog").open || $("api-url").value!==selectedBase)return;
    if(!$("plecs-executable").value)$("plecs-executable").value=p.executable || '';
    if(!$("plecs-model-directory").value)$("plecs-model-directory").value=p.model_directory || '';
    if(!rememberedPlecs && p.analysis_binding && !Array.from($('plecs-binding-model').closest('details').querySelectorAll('input'))
      .some(input=>input.type==='checkbox'?input.checked:Boolean(input.value)))fillPlecsBinding(p.analysis_binding);
  }}catch{/* PLECS configuration is optional. */}
};
$("sync-now").onclick=()=>void synchronizeArchive();
$("copy-sync-code").onclick=async()=>{
  try { await navigator.clipboard.writeText($("sync-code").value);ui($("settings-status"),"同步恢复码已复制，请妥善保管。"); }
  catch { ui($("settings-status"),"复制失败，请手动复制同步恢复码。"); }
};
$("api-url").oninput=()=>{
  updatePlecsControls();ui($("settings-status"),"");
  $("plecs-executable").value='';
  $("plecs-model-directory").value='';
  fillPlecsBinding(null);
  ui($('bridge-instructions'),'');
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
    if(historyLocked() || archiveRunning)throw new Error("请等待当前请求结束后切换连接。");
    const next=url.toString().replace(/\/$/,"");
    if(base&&next!==base&&state.dataset)throw new Error("切换后端前请先清除当前数据。");
    const backendChanged=next!==base;
    const nextCode=$("sync-code").value.trim().toLowerCase();
    if (nextCode && !/^[a-f0-9]{64}$/.test(nextCode)) throw new Error("同步恢复码应为 64 位十六进制字符。");
    const codeChanged=!backendChanged && nextCode && nextCode!==archiveKey;
    if(backendChanged || codeChanged)persistConversation();
    base=next;accessToken=$("access-token").value.trim();
    let remembered=true;
    try {
      localStorage.setItem("buck-api-url",base);
      if(accessToken)localStorage.setItem(tokenKey(base),accessToken);
      else localStorage.removeItem(tokenKey(base));
    }catch{remembered=false;}
    if (codeChanged || (backendChanged && nextCode && nextCode!==archiveKey)) localStorage.setItem(archiveKeyStorage(),nextCode);
    ui($("settings-status"), "正在连接…");
    if(backendChanged || codeChanged){state.connected=false;resetExperiment();await initializeHistory();}
    if(await connect()){
      const plecsConfig=readPlecsSettings();
      await syncPlecsConfig(plecsConfig);
      localStorage.setItem(`buck-plecs:${base}`,JSON.stringify(plecsConfig));
      const reopened=await synchronizeArchive({preferRemote:true});
      if(!reopened && !state.dataset && activeConversation?.attachment)await restoreAttachment(activeConversation.attachment);
      scheduleArchiveSync();
      $("settings-dialog").close();ui($("settings-status"), "");
      if(!remembered)notice("已连接，但浏览器禁止保存设置；关闭页面后需要重新输入访问口令。");
    }
    else ui($("settings-status"), "连接未成功，请检查页面提示。");
  }catch(e){ui($("settings-status"), e.message);}
};
$("chat-input").addEventListener("input",persistConversation);
$("reasoning-mode").addEventListener("change",persistConversation);
$("experiment-content").addEventListener("change",persistConversation);
for (const [button,content,label] of [["toggle-experiment","experiment-content","实验配置"],["toggle-history","history-content","历史会话"]]) {
  $(button).onclick=()=>{
    toggleSection(button,content,label);
    try {localStorage.setItem(`buck-panel:${new URL(".",location.href).pathname}:${button}`,$(content).hidden ? "closed" : "open");} catch {}
  };
}
document.addEventListener("buck-language-change",()=>{ $("chart-tooltip").hidden=true; displaySummary(); displayDiagnostics(); displayReviewReasons(); displayAssessment(); drawCharts(); renderHistory(); renderExperiments(); });
window.addEventListener("pagehide",persistConversation);
new ResizeObserver(()=>drawCharts()).observe($("current-chart"));
$("conversation-panel").classList.add("is-empty");
controls();void (async()=>{await initializeHistory();if(await connect()){
  const reopened=await synchronizeArchive({preferRemote:true});
  if(!reopened){
    if(!state.dataset)await restoreAttachment(activeConversation?.attachment);
    await recoverExperiments();
  }
  scheduleArchiveSync();
}})();

function plecsPortContext(config){
  const b=config.analysis_binding || {};
  return {model_name:b.model_name || '',current_port:b.current_port ?? null,voltage_port:b.voltage_port ?? null,pwm_port:b.pwm_port ?? null};
}
async function applyPlecsPorts(action,context){
  const saved=localPlecsConfig();
  if(!saved || JSON.stringify(saved)!==JSON.stringify(context.plecs))throw new Error('连接设置已变化，请重新发送端口指令。');
  if($('settings-dialog').open){
    const draft=readPlecsSettings();
    const keys=['model_name','current_port','voltage_port','pwm_port','L_parameter','C_parameter','confirmed','same_circuit_and_conditions'];
    if(draft.model_directory!==(saved.model_directory || '') || draft.executable!==(saved.executable || '') ||
       keys.some(key=>(draft.analysis_binding?.[key] ?? '')!==(saved.analysis_binding?.[key] ?? ''))){
      throw new Error('连接设置已变化，请重新发送端口指令。');
    }
  }
  const old=plecsPortContext(saved);
  const model=action.model_name;
  if(typeof model!=='string' || !/^[^/\\\r\n]+\.plecs$/i.test(model) ||
     (old.model_name && old.model_name.toLowerCase().replace(/\.plecs$/,'')!==model.toLowerCase().slice(0,-6)))throw new Error('模型文件名与当前端口绑定不符。');
  const changes=Object.entries(action.changes || {});
  for(const [key,value] of changes){
    if(!['current_port','voltage_port','pwm_port'].includes(key) ||
       !(key==='pwm_port' && value===null) && !(Number.isInteger(value) && value>=1 && value<=64))throw new Error('配置操作格式无效。');
  }
  if(!changes.length)throw new Error('配置操作格式无效。');
  const binding={...old,...saved.analysis_binding,...action.changes,model_name:old.model_name || model};
  if(action.source_sha256){
    if(!/^[0-9a-f]{64}$/.test(action.source_sha256))throw new Error('配置操作格式无效。');
    binding.source_sha256=action.source_sha256;
  }
  const channels=[binding.current_port,binding.voltage_port,binding.pwm_port].filter(n=>n!=null);
  if(new Set(channels).size!==channels.length)throw new Error('电流、电压和 PWM 需要不同的通道号。');
  let status=tr('端口设置已与指令一致。');
  if(!old.model_name || binding.source_sha256!==saved.analysis_binding?.source_sha256 || changes.some(([key,value])=>old[key]!==value)){
    binding.confirmed=false;
    binding.same_circuit_and_conditions=false;
    localStorage.setItem(`buck-plecs:${base}`,JSON.stringify({...saved,analysis_binding:binding}));
    fillPlecsBinding(binding);
    status=tr('端口设置已保存，更改后需重新核对绑定。');
    try{
      if(!await syncPlecsConfig())status=tr('端口已保存在本机，配对连接器后可同步到后端。');
    }catch{status=tr('端口已保存在本机，后端同步待重试。');}
  }
  const labels={current_port:'电流输出端口',voltage_port:'电压输出端口',pwm_port:'PWM 输出端口（可留空）'};
  const lines=changes.map(([key,value])=>{
    const row=action.evidence?.[key];
    const trace=Array.isArray(row?.path) ? [...row.path,row.component].filter(Boolean).join(' ← ') : '';
    return tr(labels[key])+' = '+(value ?? '—')+(trace?' ('+trace+')':'');
  });
  return status+' '+binding.model_name+'\n\n'+lines.join('\n\n');
}
function readPlecsSettings(){
  const model=$('plecs-binding-model').value.trim();
  const binding=model?{model_name:model,source_sha256:$('plecs-binding-model').dataset.sourceSha256 || null,current_port:$('plecs-binding-current').value?Number($('plecs-binding-current').value):null,voltage_port:$('plecs-binding-voltage').value?Number($('plecs-binding-voltage').value):null,
      pwm_port:$('plecs-binding-pwm').value?Number($('plecs-binding-pwm').value):null,L_parameter:$('plecs-binding-l').value.trim(),C_parameter:$('plecs-binding-c').value.trim(),
      confirmed:$('plecs-binding-confirmed').checked,same_circuit_and_conditions:$('plecs-binding-same').checked}:null;
  if($('plecs-binding-confirmed').checked){
    if(!binding?.current_port || !binding.voltage_port || !binding.L_parameter || !binding.C_parameter)
      throw new Error('请填写模型文件名、电流和电压通道及 L/C 参数键后再确认。');
    const ports=[binding.current_port,binding.voltage_port,binding.pwm_port].filter(n=>n!=null);
    if(new Set(ports).size!==ports.length)throw new Error('电流、电压和 PWM 需要不同的通道号。');
  }
  return {executable:$('plecs-executable').value.trim(),model_directory:$('plecs-model-directory').value.trim(),analysis_binding:binding};
}
function fillPlecsBinding(binding){
  $('plecs-binding-model').dataset.sourceSha256=binding?.source_sha256 || '';
  for(const [id,key] of [['model','model_name'],['current','current_port'],['voltage','voltage_port'],['pwm','pwm_port'],['l','L_parameter'],['c','C_parameter']])$('plecs-binding-'+id).value=binding?.[key]??'';
  $('plecs-binding-confirmed').checked=Boolean(binding?.confirmed);$('plecs-binding-same').checked=Boolean(binding?.same_circuit_and_conditions);
}
for(const id of ['plecs-model-directory','plecs-binding-model','plecs-binding-current','plecs-binding-voltage','plecs-binding-pwm','plecs-binding-l','plecs-binding-c']){
  $(id).addEventListener('input',()=>{
    if(id==='plecs-model-directory' || id==='plecs-binding-model')delete $('plecs-binding-model').dataset.sourceSha256;
    $('plecs-binding-confirmed').checked=false;
    $('plecs-binding-same').checked=false;
  });
}
window.BuckHarness.bind();
