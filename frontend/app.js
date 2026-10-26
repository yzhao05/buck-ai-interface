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
const state = { dataset: null, csvFile: null, mapping: null, summary: null, result: null, resultId: null,
  truthDraft: {l:"",c:""}, truthComparison: null, truthError: "",
  history: [], connected: false, busy: false, chatBusy: false, syncBusy: false, job: null, chart: null, zoom: null,
  identificationOpen: false, waveformOpen: false };
const fmt = (value, digits = 4) => Number.isFinite(value) ? Number(value.toPrecision(digits)).toString() : "—";
const tools = {search_conversation:"检索会话记录", read_conversation_message:"读取历史消息", get_waveform_summary:"读取波形统计", get_identification_result:"读取辨识结果", get_experiment_history:"读取实验历史", get_analysis_history:"读取历史分析", start_identification:"启动物理辨识", search_knowledge:"检索专业资料"};
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
  if (!base) throw new Error("请先在连接设置中填写 Render 后端地址，或本地启动后端。");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  const headers = {"X-Session-ID": sessionId};
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  if (archiveCode) headers["X-Archive-Key"] = archiveCode;
  if (body && !(body instanceof FormData)) headers["Content-Type"] = "application/json";
  try {
    const response = await fetch(base.replace(/\/$/, "") + path, {method, headers,
      body: body instanceof FormData ? body : body ? JSON.stringify(body) : undefined, signal: controller.signal});
    if (response.ok && asBlob) return await response.blob();
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw Object.assign(new Error(errorText(data)), {status:response.status});
    return data;
  } catch (error) {
    if (error.name === "AbortError") throw new Error("请求超时。Render 服务可能正在唤醒，请稍后重试。");
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
  $("ask-error").disabled = waiting || !state.result;
  if (waiting || !state.result) closeAnalysisMenu();
  renderAnalysisHistory();
  for (const id of ["truth-l","truth-c","calculate-parameter-error"]) $(id).disabled=waiting || !state.result;
  window.BuckCircuit.setBusy(waiting); window.BuckCircuit.render();
  $("chat-input").disabled = state.syncBusy;
  $("open-settings").disabled = state.syncBusy || archiveRunning;
  $("sync-now").disabled = waiting || !state.connected || archiveRunning;
  $("apply-mapping").disabled = !state.dataset || waiting;
  $("load-demo").disabled = waiting;
  $("toggle-csv-library").disabled = waiting || !activeConversation;
  document.querySelectorAll(".csv-library-item").forEach(button=>button.disabled=waiting);
  $("empty-demo").disabled = waiting;
  $("csv-file").disabled = waiting;
  $("clear-data").disabled = waiting;
  $("send-chat").disabled = waiting || !state.connected || !activeConversation;
  $("clear-chat").disabled = waiting;
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
  ui($("method-hint"), $("fit-method").value === "pann"
    ? "从加权积分初值继续优化窗口状态和参数；不保证比积分结果更准确。"
    : "直接输出加权积分估计，不做 PANN 微调，无需 PyTorch。");
  for (const id of ["map-time","map-current","map-voltage","map-pwm","time-unit","current-unit","voltage-unit","vin","frequency","fit-mode","fixed-r","fit-method","pann-state","pann-cycles","pann-epochs","start-ms","end-ms","train-fraction","pwm-source","controller-confirmed"]) $(id).disabled = waiting;
}
async function connect() {
  $("connection-status").className = "status";
  $("connection-status").replaceChildren(document.createElement("i"), window.BuckI18n.node("正在连接"));
  try {
    const health = await api("/api/health", {timeout: 60000});
    archiveEnabled=Boolean(health.archive_enabled); archivePersistent=Boolean(health.archive_persistent);
    if (!archiveEnabled) syncStatus("此后端尚未启用双向同步，请更新后端。");
    state.connected = true;
    $("connection-status").className = "status connected";
    $("connection-status").replaceChildren(document.createElement("i"), window.BuckI18n.node("后端已连接"));
    ui($("chat-mode"), health.chat_mode === "deepseek" ? `DeepSeek · ${health.model}` : "本地规则分析 · 未配置 API Key");
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
function options() {
  const numeric = (id) => {
    const el = $(id);
    if (!el.value.trim() || !el.checkValidity()) throw new Error("请检查工况输入范围：" + el.closest("label")?.textContent.trim());
    return Number(el.value);
  };
  return {vin:numeric("vin"), frequency_hz:numeric("frequency")*1000,
    pwm_source:pwmSource(), controller_confirmed:$("controller-confirmed").value === "yes",
    controller:pwmSource() === "controller" ? window.BuckCircuit.read() : null,
    start_s:$("start-ms").value === "" ? null : Number($("start-ms").value)/1000,
    end_s:$("end-ms").value === "" ? null : Number($("end-ms").value)/1000,
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
  } catch (error) {
    if (state.dataset && !state.mapping) {
      $("mapping-details").open=true;
      ui($("stat-points"), "数据读取或检查未完成");
    }
    notice(error.message); ui($("job-status"), "数据读取或检查未完成");
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
  ui($("stat-points"), `${summary.rows.toLocaleString()} 个有效采样点`);
  $("wave-subtitle").textContent = state.dataset.filename;
  $("chart-empty").hidden = true; $("chart-container").hidden = false;
}
async function startFit() {
  if (!state.identificationOpen || !state.mapping || historyLocked() || resultMatchesCurrent(state.result)) return;
  let submitted=false;
  try {
    const fitOptions = options();
    state.busy = true; controls(); invalidateResult(); notice();
    await ensureDatasetAvailable();
    ui($("job-status"), "正在提交辨识任务…");
    submitted=true;
    const response = await api("/api/identify", {method:"POST", body:{dataset_id:state.dataset.dataset_id,mapping:state.mapping,options:fitOptions}});
    if (response.resumed) notice("已找回正在运行的辨识任务，正在恢复进度。",true);
    state.busy = false;
    await watchJob(response.job_id);
  } catch (error) {
    // The POST may have reached the server even when its response was lost.
    if (submitted && (!error.status || error.status===409 || error.status>=500)) {
      try { if (await resumeActiveJob()) return; } catch { /* Keep the original error visible. */ }
    }
    notice(error.message);
    ui($("job-status"), submitted && (!error.status || error.status>=500) ? "提交状态暂未确认，请检查连接后重试。" : "辨识未完成");
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
      const stages = {checking:"正在检查数据与电路配置…", pwm_reconstruction:"正在重建 PWM，获取训练段开关信息…",
        initialization:"正在计算参数初值…", pann_training:"正在使用开关信息训练 PANN…", validation:"正在验证预测波形…"};
      ui($("job-status"), response.status === "queued" ? "任务已排队…" : stages[response.stage] || `正在拟合与验证 · ${Math.round((Date.now()-started)/1000)} 秒`);
      if (response.status === "complete") {
        let matches=false;
        try { matches=resultMatchesCurrent(response.result); } catch { /* The user may have edited fields before recovery. */ }
        if (!matches) {
          refreshExperiments=true;
          notice("原辨识任务已完成，结果将恢复到实验历史；当前配置已更改，可重新开始辨识。");
          ui($("job-status"),"原任务已完成，当前配置尚未辨识");
          return;
        }
        state.result = response.result; state.resultId = id; displayResult();
        ui($("job-status"), `辨识完成 · 用时 ${response.result.elapsed_s} 秒`);
        return;
      }
      if (response.status === "failed") throw new Error(response.error);
      await pause(1200);
    }
  } catch (error) { notice(error.message); ui($("job-status"), "任务未完成，请检查提示"); }
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
function calculateParameterError() {
  if (!state.result || historyLocked())return;
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
  const r=state.result;$("result-assessment").hidden=!r;if(!r)return;
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
  state.chart = r.chart; if (!preserveZoom) state.zoom = null; drawCharts();
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
function message(role, content, mode = "") {
  const article = document.createElement("article"); article.className = `message ${role}`;
  const label = document.createElement("div"); label.className = "message-label";
  if (role === "assistant") { const icon = document.createElement("span"); icon.className = "assistant-icon"; icon.textContent = "✳"; label.append(icon); }
  label.append(window.BuckI18n.node(role === "user" ? "你" : "实验助手"));
  if (mode) { const badge = document.createElement("span"); badge.className = "message-mode"; ui(badge, mode); label.append(badge); }
  const text = document.createElement("div"); text.className = "message-content"; text.textContent = content;
  if (role === "assistant" && window.BuckMarkdown) window.BuckMarkdown.render(text,content);
  article.append(label, text); $("chat-messages").append(article);
  $("chat-messages").scrollTop = $("chat-messages").scrollHeight;
  return article;
}
function decorateResponse(article, response) {
    decorateConversationRetrieval(article,response.conversation_retrieval);
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
async function sendMessage(value, {analysisRequest=false}={}) {
  const text = value.trim(); if (!text || historyLocked() || !activeConversation) return;
  if (!state.connected) { notice("请先连接分析后端。"); return; }
  const corpus=conversationMessages(readConversation(activeConversation.id));
  const useConversation=wantsConversationRetrieval(text) || (comparingExperiments(text) && corpus.length>0 && chatExperiments().length===0);
  const compareSaved=!requestsIdentificationExecution(text) && (useConversation || (comparingExperiments(text) && chatExperiments().length>0));
  const analysisMeta=analysisRequest || resultAnalysisPrompts.has(text) ? analysisDescriptor() : null;
  // Read-only analysis belongs to the displayed completed run, not unsubmitted form edits.
  const analyzeResult = Boolean(state.result) && !requestsIdentificationExecution(text) && !compareSaved;
  const analysisMapping = analyzeResult ? state.result.mapping : state.mapping;
  let fitOptions;
  try { fitOptions = analyzeResult ? structuredClone(state.result.options) : options(); } catch (error) {
    if (state.identificationOpen && !compareSaved) { notice(error.message); return; }
  }
  state.chatBusy = true; controls();
  $("conversation-panel").classList.remove("is-empty");
  $("chat-input").value = "";
  $("chat-messages").querySelector(".welcome").hidden = true; $("chat-messages").querySelector(".suggestions").hidden = true;
  const userArticle=message("user", text);
  const previousHistory = readConversation(activeConversation.id).messages.filter(m=>m.mode!=="请求未完成")
    .slice(-10).map(m=>({role:m.role,content:m.content.slice(0,8000)}));
  const requestMessage=rememberMessage("user", text, analysisMeta ? {analysis_request:analysisMeta} : null);
  userArticle.dataset.messageId=requestMessage.id;
  const pending = message("assistant", "正在读取实验上下文…"); pending.querySelector(".message-content").classList.add("typing"); ui(pending.querySelector(".message-content"),"正在读取实验上下文…");
  try {
    if (state.mapping && !compareSaved) await ensureDatasetAvailable();
    const comparison=fitOptions && !compareSaved ? parameterComparison() : null;
    const response = await api("/api/chat", {method:"POST", timeout:220000, body:{message:text,history:previousHistory,
      dataset_id:state.mapping ? state.dataset?.dataset_id : null,mapping:analysisMapping,
      options:fitOptions,result_id:fitOptions && !compareSaved ? state.resultId : null,
      ground_truth:comparison ? {result_id:state.resultId,...comparison.ground_truth} : undefined,
      conversation_messages:corpus,conversation_retrieval_requested:useConversation,
      // Keep execution context for explicit mixed requests (run, then compare).
      // The backend classifier enforces read-only tools for comparison alone.
      experiments:chatExperiments(),identification_enabled:state.identificationOpen && Boolean(fitOptions) && !analyzeResult}});
    pending.remove();
    const answer = (response.provider_error ? response.provider_error + "\n\n" : "") + response.answer;
    const article = message("assistant", answer,
      response.mode === "deepseek" ? "DeepSeek" : "本地规则分析");
    decorateResponse(article, response);
    const savedReply=rememberMessage("assistant", answer, {route:response.route, tool_calls:response.tool_calls, sources:response.sources,
      request_message_id:requestMessage.id,conversation_retrieval:response.conversation_retrieval,
      ...(analysisMeta && !response.job_id ? {analysis:{...analysisMeta,request_id:requestMessage.id}} : {})}, response.mode === "deepseek" ? "DeepSeek" : "本地规则分析");
    article.dataset.messageId=savedReply.id;
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
let reportExportAttempt = 0;
let reportExportBusy = false;
const reportDownloadUrls = [];
function clearReportDownloads() {
  reportExportAttempt++;
  reportExportBusy = false;
  for (const url of reportDownloadUrls) URL.revokeObjectURL(url);
  reportDownloadUrls.length = 0;
  $("report-downloads").hidden = true;
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
async function exportReports() {
  if (!state.result || reportExportBusy) return;
  clearReportDownloads();
  const attempt = reportExportAttempt;
  reportExportBusy = true; $("download-report").disabled = true;
  // Capture once: editing inputs or switching conversations must never mix runs.
  const exportedAt = new Date(), language = window.BuckI18n.language;
  const report = structuredClone({application:"Buck Lab 0.1.0",exported_at:exportedAt.toISOString(),summary:state.summary,
    result:{...state.result,quality:resultQuality(state.result),parameter_comparison:parameterComparison(),parameter_error:parameterComparison(),parameter_error_note:parameterErrorNote(),assessment:resultAssessment(state.result),review_reasons:resultReviewReasons(state.result)},conversation:state.history});
  const filename = reportFilename(report.result, exportedAt);
  const notes = (report.result.warnings || []).map(note=>tr(note));
  $("report-downloads").hidden = false;
  ui($("report-export-status"), "JSON 已生成，正在生成 PDF…");
  prepareReportDownload(new Blob([JSON.stringify(report,null,2)],{type:"application/json;charset=utf-8"}),filename,"report-json-link").click();
  try {
    const pdf = await api("/api/reports/pdf", {method:"POST",asBlob:true,body:{language,exported_at:report.exported_at,result:report.result,notes}});
    if (attempt !== reportExportAttempt) return;
    if (pdf.type !== "application/pdf" || await pdf.slice(0,5).text() !== "%PDF-") throw new Error("后端未返回有效的 PDF，请更新后端后重试。");
    prepareReportDownload(pdf,filename.replace(/\.json$/,".pdf"),"report-pdf-link").click();
    ui($("report-export-status"), "PDF 和 JSON 已生成。如浏览器拦截了连续下载，请点击下方链接分别保存。");
  } catch (error) {
    if (attempt !== reportExportAttempt) return;
    ui($("report-export-status"), "PDF 生成失败，JSON 仍可下载。请检查后端连接与版本后重新导出。");
    notice(error.message);
  } finally {
    if (attempt === reportExportAttempt) {
      reportExportBusy = false; $("download-report").disabled = !state.result;
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
$("csv-file").onchange = event => { if(event.target.files[0]) void importData(event.target.files[0]); };
$("close-notice").onclick = () => notice();
$("load-demo").onclick = $("empty-demo").onclick = () => void importData();
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
$("view-tail").onclick=()=>{if(state.chart){const t=state.chart.time_s;const frequency=state.result?.options.frequency_hz || Number($("frequency").value)*1000;state.zoom=[Math.max(t[0],t[t.length-1]-10/frequency),t[t.length-1]];drawCharts();}};
$("chat-form").onsubmit=event=>{event.preventDefault();void sendMessage($("chat-input").value);};
$("chat-input").onkeydown=event=>{if(event.key==="Enter"&&!event.shiftKey&&!event.isComposing){event.preventDefault();void sendMessage($("chat-input").value);}};
document.querySelectorAll("[data-prompt]").forEach(button=>button.onclick=()=>void sendMessage(button.dataset.prompt));
function closeAnalysisMenu() {
  $("analysis-menu").hidden=true;$("ask-error").setAttribute("aria-expanded","false");
}
$("ask-error").onclick=()=>{
  if (historyLocked() || !state.result) return;
  const open=$("analysis-menu").hidden;
  $("analysis-menu").hidden=!open;$("ask-error").setAttribute("aria-expanded",String(open));
  if(open)$("analyze-here").focus();
};
$("analyze-here").onclick=()=>{closeAnalysisMenu();void sendMessage(tr("请分析当前辨识结果的误差，并给出有依据的修改计划。"),{analysisRequest:true});};
$("analyze-new").onclick=()=>{closeAnalysisMenu();void analyzeInNewConversation();};
document.addEventListener("click",event=>{if(!event.target.closest(".analysis-action"))closeAnalysisMenu();});
document.addEventListener("keydown",event=>{
  if($("analysis-menu").hidden)return;
  if(event.key==="Escape"){event.preventDefault();closeAnalysisMenu();$("ask-error").focus();}
  if(["ArrowDown","ArrowUp"].includes(event.key)){
    event.preventDefault();(document.activeElement===$("analyze-here")?$("analyze-new"):$("analyze-here")).focus();
  }
});
document.addEventListener("buck-language-change",closeAnalysisMenu);
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
$("clear-chat").onclick=$("new-chat").onclick=()=>void newConversation();$("download-report").onclick=downloadReport;
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
$("open-settings").onclick=()=>{$("api-url").value=base;$("access-token").value=accessToken;$("sync-code").value=archiveKey;ui($("settings-status"), "");$("settings-dialog").showModal();};
$("sync-now").onclick=()=>void synchronizeArchive();
$("copy-sync-code").onclick=async()=>{
  try { await navigator.clipboard.writeText($("sync-code").value);ui($("settings-status"),"同步恢复码已复制，请妥善保管。"); }
  catch { ui($("settings-status"),"复制失败，请手动复制同步恢复码。"); }
};
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
      await synchronizeArchive({preferRemote:true});
      if(!state.dataset && activeConversation?.attachment)await restoreAttachment(activeConversation.attachment);
      scheduleArchiveSync();
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
document.addEventListener("buck-language-change",()=>{ $("chart-tooltip").hidden=true; displaySummary(); displayDiagnostics(); displayReviewReasons(); displayAssessment(); drawCharts(); renderHistory(); renderExperiments(); });
window.addEventListener("pagehide",persistConversation);
new ResizeObserver(()=>drawCharts()).observe($("current-chart"));
$("conversation-panel").classList.add("is-empty");
controls();void (async()=>{await initializeHistory();if(await connect()){
  await synchronizeArchive({preferRemote:true});
  if(!state.dataset)await restoreAttachment(activeConversation?.attachment);
  await recoverExperiments();
  scheduleArchiveSync();
}})();
