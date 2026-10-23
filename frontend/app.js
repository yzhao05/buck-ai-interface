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
  history: [], connected: false, busy: false, chatBusy: false, syncBusy: false, job: null, chart: null, zoom: null,
  identificationOpen: false, waveformOpen: false };
const fmt = (value, digits = 4) => Number.isFinite(value) ? Number(value.toPrecision(digits)).toString() : "—";
const tools = {get_waveform_summary:"读取波形统计", get_identification_result:"读取辨识结果", start_identification:"启动物理辨识", search_knowledge:"检索专业资料"};
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
  window.BuckCircuit.setBusy(waiting); window.BuckCircuit.render();
  $("chat-input").disabled = state.syncBusy;
  $("open-settings").disabled = state.syncBusy;
  $("sync-now").disabled = waiting || !state.connected;
  $("identify").disabled = !state.identificationOpen || !state.connected || !state.mapping || waiting || Boolean(state.result);
  $("identify-complete-hint").hidden = !state.result;
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
      invalidateResult();
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
function resultMatchesCurrent(result) {
  return Boolean(result && state.dataset && state.mapping
    && result.file_sha256 === state.dataset.sha256
    && Object.entries(options()).every(([key,value]) => value && typeof value === "object"
      ? Object.entries(value).every(([field,number])=>result.options?.[key]?.[field] === number)
      : (result.options?.[key] ?? null) === value)
    && Object.entries(state.mapping).every(([key,value]) => result.mapping?.[key] === value));
}
function invalidateResult() {
  clearReportDownloads();
  state.result = null; state.resultId = null;
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
  if (!state.identificationOpen || !state.mapping || historyLocked() || state.result) return;
  try {
    const fitOptions = options();
    state.busy = true; controls(); invalidateResult(); notice();
    await ensureDatasetAvailable();
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
    // A long training run has no total polling deadline; the job decides when it ends.
    while (true) {
      const response = await api(`/api/jobs/${id}`);
      const stages = {checking:"正在检查数据与电路配置…", pwm_reconstruction:"正在重建 PWM，获取训练段开关信息…",
        initialization:"正在计算参数初值…", pann_training:"正在使用开关信息训练 PANN…", validation:"正在验证预测波形…"};
      ui($("job-status"), response.status === "queued" ? "任务已排队…" : stages[response.stage] || `正在拟合与验证 · ${Math.round((Date.now()-started)/1000)} 秒`);
      if (response.status === "complete") {
        if (!resultMatchesCurrent(response.result)) {
          invalidateResult();
          throw new Error("辨识结果与当前数据或配置不一致，请重新辨识。");
        }
        state.result = response.result; state.resultId = id; displayResult();
        ui($("job-status"), `辨识完成 · 用时 ${response.result.elapsed_s} 秒`);
        return;
      }
      if (response.status === "failed") throw new Error(response.error);
      await pause(1200);
    }
  } catch (error) { notice(error.message); ui($("job-status"), "任务未完成，请检查提示"); }
  finally { state.job = null; persistConversation(); controls(); }
}
function resultReviewReasons(result) {
  if (Array.isArray(result.review_reasons)) return result.review_reasons;
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
  if (pann.best_at_budget_end) reasons.push({code:"pann_best_at_end",best_epoch:pann.best_epoch,completed_epochs:pann.completed_epochs});
  return reasons;
}
function displayReviewReasons() {
  const r=state.result, list=$("review-reason-list");list.replaceChildren();
  $("review-reasons").hidden = !r || r.quality!=="需复核";
  if ($("review-reasons").hidden) return;
  const labels={current_nrmse:"留出电流归一化 RMSE",voltage_nrmse:"留出电压归一化 RMSE",condition_number:"参数辨识条件数"};
  const reasons=resultReviewReasons(r);
  for (const reason of reasons) {
    let text;
    if (labels[reason.code]) {
      const unit=reason.code.endsWith("nrmse") ? "%" : "";
      text=`${tr(labels[reason.code])}: ${reason.value}${unit} > ${reason.threshold}${unit}`;
    } else if (reason.code==="parameter_at_bound") text=tr("初始化或参数估计触及上下限，需检查参数约束及模型与数据是否匹配。");
    else if (reason.code==="pann_time_budget") {
      text=tr("PANN 达到训练时间预算，尚不能确认充分收敛。");
      if (Number.isFinite(reason.completed_epochs) && Number.isFinite(reason.requested_epochs)) text+=` ${tr("已完成轮数 / 请求轮数")}: ${reason.completed_epochs} / ${reason.requested_epochs}`;
    } else if (reason.code==="pann_best_at_end") {
      text=tr("PANN 最佳结果出现在最后一次训练评估，需增加训练轮数或调整窗口后复核收敛。");
      if (Number.isFinite(reason.best_epoch) && Number.isFinite(reason.completed_epochs)) text+=` ${tr("最佳轮次 / 已完成轮数")}: ${reason.best_epoch} / ${reason.completed_epochs}`;
    } else text=tr("此项复核原因尚无法显示，请查看计算条件与诊断。");
    const li=document.createElement("li");li.textContent=text;list.append(li);
  }
  if (!reasons.length) {const li=document.createElement("li");ui(li,"历史结果未记录可确认的触发原因，请查看计算条件与诊断或重新辨识。");list.append(li);}
}
function displayResult() {
  clearReportDownloads();
  const r = state.result, p = r.parameters;
  ui($("result-l"), fmt(p.L_H*1e6,5)); ui($("result-c"), fmt(p.C_F*1e6,5)); ui($("result-r"), fmt(p.R_ohm,5));
  ui($("r-label"), r.R_estimated ? "负载电阻 R · 估计" : "负载电阻 R · 固定");
  ui($("result-badge"), r.quality); $("result-badge").className = "badge " + (r.quality === "需复核" ? "warning" : "good");
  displayReviewReasons();
  $("validation").hidden = false; ui($("rmse-i"), fmt(r.metrics.current_rmse_A) + " A"); ui($("rmse-v"), fmt(r.metrics.voltage_rmse_V*1000) + " mV");
  const driveNote = r.diagnostics.pwm_reconstruction?.status === "complete"
    ? "PWM 重建已完成：仅使用训练段电压与已知控制器生成 0/1 开关信息，再固定 R 识别 L、C。\n" : "";
  ui($("result-notes"), `${driveNote}${r.validation_kind}\n${r.parameter_error_note}`);
  $("diagnostics").hidden = false;
  displayDiagnostics();
  $("ask-error").disabled = false; $("download-report").disabled = false;
  state.chart = r.chart; state.zoom = null; drawCharts();
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
    if (state.mapping) await ensureDatasetAvailable();
    const response = await api("/api/chat", {method:"POST", timeout:220000, body:{message:text,history:previousHistory,
      dataset_id:state.mapping ? state.dataset?.dataset_id : null,mapping:state.mapping,options:fitOptions,result_id:fitOptions ? state.resultId : null,
      identification_enabled:state.identificationOpen}});
    pending.remove();
    const answer = (response.provider_error ? response.provider_error + "\n\n" : "") + response.answer;
    const article = message("assistant", answer,
      response.mode === "deepseek" ? "DeepSeek" : "本地规则分析");
    decorateResponse(article, response);
    rememberMessage("assistant", answer, {route:response.route, tool_calls:response.tool_calls, sources:response.sources}, response.mode === "deepseek" ? "DeepSeek" : "本地规则分析");
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
    result:{...state.result,review_reasons:resultReviewReasons(state.result)},conversation:state.history});
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
  invalidateResult();
  if(["vin","frequency","fixed-r"].includes(id) && pwmSource()==="controller") $("controller-confirmed").value="no";
  $("fixed-r-label").hidden=$("fit-mode").value!=="LC";ui($("train-label"), $("train-fraction").value+"%");controls();
  if(state.mapping)readyStatus();
};
$("pwm-source").onchange=()=>{
  prepareControllerDefaults();
  invalidateResult(); controls(); if (state.mapping) readyStatus(); persistConversation();
};
$("controller-confirmed").onchange=()=>{invalidateResult();controls();if(state.mapping)readyStatus();persistConversation();};
document.addEventListener("buck-circuit-change",()=>{
  $("controller-confirmed").value="no";invalidateResult();controls();
  if(state.mapping)readyStatus();persistConversation();
});
$("identify").onclick=()=>void startFit();
$("enter-identification").onclick=()=>setIdentificationOpen(true);
for (const side of ["left", "right"]) {
  $(`toggle-${side}-pane`).onclick=()=>setPaneCollapsed(side,!$("workspace").classList.contains(`${side}-collapsed`));
}
$("leave-identification").onclick=()=>setIdentificationOpen(false);
$("toggle-import").onclick=()=>toggleSection("toggle-import","import-content","导入波形");
$("view-full").onclick=()=>{state.zoom=null;drawCharts();};
$("view-tail").onclick=()=>{if(state.chart){const t=state.chart.time_s;state.zoom=[Math.max(t[0],t[t.length-1]-10/(Number($("frequency").value)*1000)),t[t.length-1]];drawCharts();}};
$("chat-form").onsubmit=event=>{event.preventDefault();void sendMessage($("chat-input").value);};
$("chat-input").onkeydown=event=>{if(event.key==="Enter"&&!event.shiftKey&&!event.isComposing){event.preventDefault();void sendMessage($("chat-input").value);}};
document.querySelectorAll("[data-prompt]").forEach(button=>button.onclick=()=>void sendMessage(button.dataset.prompt));
$("ask-error").onclick=()=>void sendMessage(tr("请分析当前辨识结果的误差，并给出有依据的修改计划。"));
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
    if(historyLocked())throw new Error("请等待当前请求结束后切换连接。");
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
document.addEventListener("buck-language-change",()=>{ $("chart-tooltip").hidden=true; displaySummary(); displayDiagnostics(); displayReviewReasons(); drawCharts(); renderHistory(); });
window.addEventListener("pagehide",persistConversation);
new ResizeObserver(()=>drawCharts()).observe($("current-chart"));
$("conversation-panel").classList.add("is-empty");
controls();void (async()=>{await initializeHistory();if(await connect()){
  await synchronizeArchive({preferRemote:true});
  if(!state.dataset)await restoreAttachment(activeConversation?.attachment);
  scheduleArchiveSync();
}})();
