/* Explicit UI translations. Uploaded data, user input and conversation content are untouched. */
"use strict";
window.BuckI18n = (() => {
  const english = {
    "任务：": "Task: ", "使用指导": "Usage guidance", "公式知识": "Circuit knowledge",
    "波形分析": "Waveform analysis", "误差诊断": "Error diagnosis", "硬件寿命": "Hardware & lifetime",
    "一般问答": "General assistance", "本地规则分类": "Local rule routing",
    "分类服务暂不可用，使用本地规则": "Classifier unavailable; local rules used", "未执行": "Not executed",
    "切换为英文": "Switch to Chinese", "测量": "Measured", "预测": "Predicted",
    "Buck Lab · AI 实验工作台": "Buck Lab · AI Workspace",
    "Buck 变换器波形分析、物理参数辨识与 AI 实验助手": "Buck converter waveform analysis, parameter identification and AI assistance",
    "工作台导航": "Workspace navigation", "实验工作台": "Workspace",
    "正在连接": "Connecting", "连接设置": "Connection settings", "实验配置": "Experiment setup",
    "导入波形": "Import waveforms", "收起导入波形": "Collapse waveform import", "展开导入波形": "Expand waveform import",
    "拖入 PLECS CSV": "Drop a PLECS CSV", "或点击选择文件 · 最大 32 MB": "or click to browse · up to 32 MB",
    "加载已有实验示例": "Load example", "清除当前数据": "Clear current data",
    "信号与单位映射": "Signals & units", "时间": "Time", "时间列": "Time column",
    "电感电流": "Inductor current", "电感电流列": "Inductor current column",
    "输出电压": "Output voltage", "输出电压列": "Output voltage column",
    "PWM 开关状态": "PWM switching state", "PWM 列": "PWM column", "时间单位": "Time unit",
    "电流": "Current", "电压": "Voltage", "应用并检查数据": "Apply & check data",
    "参数辨识": "Parameter identification", "收起参数辨识": "Collapse identification", "展开参数辨识": "Expand identification",
    "进入参数辨识": "Open identification", "基本信息": "Basic settings", "退出辨识": "Exit",
    "输入电压": "Input voltage", "开关频率": "Switching frequency", "辨识目标": "Identify",
    "L / C / R · 联合辨识": "L / C / R · Joint estimation", "L / C · 固定负载 R": "L / C · Fixed load R",
    "已知负载电阻": "Known load resistance", "计算方法": "Method",
    "积分方程 · 轻量辨识": "Integral equations · Lightweight", "PANN · 需后端 PyTorch": "PANN · Requires PyTorch",
    "开始时间": "Start time", "结束时间": "End time", "起点": "Start", "终点": "End",
    "训练区间占比": "Training fraction", "前段训练": "Earlier segment: training", "后段留出验证": "Later segment: validation",
    "选择恒定负载区间。R 指负载电阻，非 ESR。示例推荐 0～0.8 ms。": "Use a constant-load interval. R is load resistance, not ESR. Suggested example interval: 0–0.8 ms.",
    "开始参数辨识": "Start identification", "等待导入实验数据": "Waiting for data",
    "物理计算 × 专业知识 × AI 对话": "Physics × Engineering × AI",
    "AI 实验助手": "AI Lab Assistant", "正在检查服务": "Checking service", "清空对话": "Clear conversation",
    "上传仿真波形，": "Upload simulation waveforms.", "识别电感、电容和电阻。": "Identify inductance, capacitance and resistance.",
    "选择 PANN 或加权积分方法计算 L、C、R，向 AI 提问，分析辨识误差并获取参数调整建议。": "Estimate L, C and R using PANN or weighted integral estimation. Ask AI to analyze identification errors and suggest parameter adjustments.",
    "理解我的仿真波形": "Understand my waveforms", "分析误差，规划下一步": "Analyze errors & plan next steps",
    "关于电容寿命，我需要什么？": "What determines capacitor life?",
    "请分析当前波形的特点和需要注意的问题": "Analyze the current waveforms and highlight issues to check.",
    "请分析当前结果的误差原因，并给出下一轮修改计划": "Analyze the errors in the current results and propose changes for the next experiment.",
    "如何根据仿真波形评估电容寿命？还缺哪些信息？": "How can simulation waveforms help assess capacitor lifetime? What information is still needed?",
    "向实验助手提问": "Ask the lab assistant", "询问波形、参数或实验计划…": "Ask about waveforms, parameters or experiment plans…",
    "未关联实验数据": "No dataset attached", "发送消息": "Send message",
    "计算摘要与对话将发送至已配置的 DeepSeek。": "Calculation summaries and conversations are sent to the configured DeepSeek service.",
    "原始 CSV 保留在分析后端，默认 1 小时过期。": "Raw CSV data stays on the analysis backend and expires after 1 hour by default.",
    "项目来源 ↗": "Project source ↗", "波形与辨识结果": "Waveforms & results",
    "查看测量波形、模型预测与验证误差。": "Inspect measurements, model predictions and validation errors.",
    "退出参数辨识": "Exit identification", "退出参数辨识，返回对话": "Exit identification and return to chat",
    "波形统计": "Waveform statistics", "平均输出电压": "Mean output voltage", "全段 · 时间加权": "Full record · Time-weighted",
    "电感电流 RMS": "Inductor current RMS", "数据时长": "Record duration", "尚未导入数据": "No data imported",
    "波形观测": "Waveform viewer", "PLECS 导出 · 电流与电压": "PLECS export · Current & voltage",
    "全段": "Full record", "末 10 周期": "Last 10 cycles", "测量波形": "Measured", "模型预测": "Predicted",
    "拖动框选放大 · 双击还原": "Drag to zoom · Double-click to reset", "等待你的第一条波形": "Ready for your first waveform",
    "上传仿真 CSV，或加载已有 PLECS 实验，": "Upload a simulation CSV or load a PLECS example",
    "开始检查电路的动态响应。": "to explore the circuit's dynamic response.", "加载实验示例 →": "Load example →",
    "电感电流测量与预测波形": "Measured and predicted inductor current", "输出电压测量与预测波形": "Measured and predicted output voltage",
    "时间 / ms": "Time / ms", "物理模型计算 · 同记录后段验证": "Physics model · Later-segment validation",
    "等待计算": "Awaiting calculation", "电感 L": "Inductance L", "电容 C": "Capacitance C", "负载电阻 R": "Load resistance R",
    "留出电流 RMSE": "Validation current RMSE", "留出电压 RMSE": "Validation voltage RMSE",
    "拟合完成后显示估计参数和留出误差。没有独立真值时，不报告参数真实误差。": "Estimated parameters and validation errors appear after fitting. True parameter errors require independent ground truth.",
    "查看计算条件与诊断": "Conditions & diagnostics", "让助手分析结果 ↗": "Ask about results ↗", "↓ 导出实验报告": "↓ Export report",
    "数值计算结果与 AI 解释分开呈现。": "Numerical results and AI explanations are presented separately.",
    "连接你的分析后端": "Connect your backend", "关闭": "Close",
    "本地启动时自动连接；部署后填写 Render 服务地址。": "Connects automatically when run locally. For deployment, enter your Render service URL.",
    "后端地址": "Backend URL", "应用访问口令": "App access code", "后端 APP_ACCESS_TOKEN；本地可留空": "Backend APP_ACCESS_TOKEN; optional locally",
    "这里填写应用口令。DeepSeek API Key 只设置在后端 .env 或 Render 环境变量中。点击保存后，此浏览器会记住该后端的口令，下次打开自动使用。共享电脑使用后可清除。": "Enter your app access code here. Set the DeepSeek API key only in the backend .env or Render environment. This browser remembers the code for this backend after saving. Clear it after using a shared computer.",
    "保存并连接": "Save & connect", "清除已记住的口令": "Forget saved access code",
    "后端已连接": "Backend connected", "尚未连接": "Not connected", "等待连接分析后端": "Waiting for backend",
    "本地规则分析 · 未配置 API Key": "Local rules · No API key configured", "PANN · 物理网络微调": "PANN · Physics network refinement",
    "本项目源码 ↗": "Source code ↗", "后端已连接。请在连接设置中输入应用访问口令后上传数据。": "Backend connected. Enter your app access code in Connection settings before uploading data.",
    "请求失败，请检查后端服务。": "Request failed. Check the backend service.",
    "请先在连接设置中填写 Render 后端地址，或本地启动后端。": "Enter your Render backend URL in Connection settings, or start the backend locally.",
    "请求超时。Render 服务可能正在唤醒，请稍后重试。": "Request timed out. Render may be waking up; please retry shortly.",
    "辨识结果与当前数据或配置不一致，请重新辨识。": "The identification result does not match the current data or settings. Run identification again.",
    "无法连接后端。请检查服务地址、启动状态及 CORS_ORIGINS 设置。": "Cannot reach the backend. Check its URL, service status and CORS_ORIGINS setting.",
    "不选择（仅分析波形）": "None (waveform analysis only)", "正在读取并检查数据…": "Reading and checking data…",
    "请选择不超过 32 MB 的 CSV 文件。": "Choose a CSV file no larger than 32 MB.",
    "已加载真实 PLECS Case16A 数据。已将辨识区间设为 0～0.8 ms，避开 1 ms 的负载突变。": "PLECS Case16A loaded. The identification interval is set to 0–0.8 ms to avoid the load step at 1 ms.",
    "数据读取或检查未完成": "Data import or validation failed", "数据就绪 · 可开始辨识": "Data ready · Identification available",
    "可分析波形 · 辨识还需要 PWM": "Waveforms ready · Identification needs PWM", "正在提交辨识任务…": "Submitting identification job…",
    "辨识未完成": "Identification incomplete", "任务已排队…": "Job queued…", "任务等待超时，请检查后端日志。": "Job timed out. Check backend logs.",
    "任务未完成，请检查提示": "Job incomplete. Check the message above.", "负载电阻 R · 估计": "Load R · Estimated",
    "负载电阻 R · 固定": "Load R · Fixed", "需复核": "Review needed", "模型内拟合一致": "Consistent model fit",
    "你": "You", "实验助手": "Lab assistant", "本地规则分析": "Local rules", "请先连接分析后端。": "Connect to the backend first.",
    "正在读取实验上下文…": "Reading experiment context…", "请求未完成": "Request incomplete", "留出验证": "Validation",
    "列映射已应用。": "Column mapping applied.", "映射已更改，请点击应用并检查数据": "Mapping changed. Click Apply & check data.",
    "请分析当前辨识结果的误差，并给出有依据的修改计划。": "Analyze errors in the current identification results and propose an evidence-based modification plan.",
    "请先填写有效的后端地址。": "Enter a valid backend URL first.",
    "已清除这个后端在当前浏览器中记住的口令。": "The saved access code for this backend has been cleared in this browser.",
    "浏览器不允许清除存储，请在浏览器设置中清除此网站的数据。": "Storage access is blocked. Clear this site's data in your browser settings.",
    "请输入有效的 http(s) 后端地址。": "Enter a valid HTTP(S) backend URL.", "HTTPS 页面需要连接 HTTPS 后端。": "An HTTPS page requires an HTTPS backend.",
    "请等待当前请求结束后切换连接。": "Wait for the current request to finish before switching connections.",
    "切换后端前请先清除当前数据。": "Clear the current dataset before switching backends.", "正在连接…": "Connecting…",
    "已连接，但浏览器禁止保存设置；关闭页面后需要重新输入访问口令。": "Connected, but browser storage is blocked. You will need to enter the access code again after closing this page.",
    "连接未成功，请检查页面提示。": "Connection failed. Check the message on the page.",
    "读取波形统计": "Read waveform statistics", "读取辨识结果": "Read identification results", "启动物理辨识": "Start identification",
    "检索专业资料": "Search engineering references", "已启动": "Started", "已执行": "Executed",
    "访问口令不正确，请在连接设置中输入后端 APP_ACCESS_TOKEN。": "Incorrect access code. Enter the backend APP_ACCESS_TOKEN in Connection settings.",
    "请先点击左侧“进入参数辨识”，确认基本信息后再执行辨识。": "Click Open identification on the left and confirm the basic settings before starting identification.",
    "未提供真值，不能计算参数真实误差；波形误差不等于参数误差。": "No ground truth was provided. True parameter errors cannot be calculated; waveform errors are not parameter errors.",
    "同一记录后段留出；从区间起点连续仿真，后段无测量状态重置，输入使用实测 PWM。不是自主闭环或独立工况验证。": "Later-segment validation within the same record. Simulation runs continuously from the interval start without resetting measured states, driven by measured PWM. This is not autonomous closed-loop or independent-condition validation.",
    "CSV 必须非空且小于 32 MB。": "The CSV must be nonempty and smaller than 32 MB.",
    "请导出 UTF-8 / GB18030 文本 CSV，不支持二进制或 UTF-16 文件。": "Export a UTF-8 or GB18030 text CSV. Binary and UTF-16 files are not supported.",
    "首行需要 3～32 个不重复的列名（至少时间、电流、电压）。": "The header must contain 3–32 unique column names, including time, current and voltage.",
    "需要 30～350000 行数据；请在 PLECS 中选择合适时间段导出。": "Use 30–350000 data rows. Select an appropriate export interval in PLECS.",
    "时间、电流、电压、PWM 必须映射到不同列。": "Time, current, voltage and PWM must map to different columns.",
    "所选列不存在，请重新映射 CSV 列。": "A selected column does not exist. Update the CSV column mapping.",
    "所选列包含缺失、非数值或无穷值，请修正 CSV 后重试。": "Selected columns contain missing, nonnumeric or infinite values. Correct the CSV and retry.",
    "时间必须单调递增；检测到倒序或拼接的多条轨迹，请分开上传。": "Time must increase monotonically. Reversed or concatenated trajectories were detected; upload them separately.",
    "有效时间点不足。": "Not enough valid time samples.",
    "PWM 列必须为 0/1 开关状态；占空比或模拟控制电压不能直接替代。": "PWM must contain binary 0/1 switching states. Duty ratios and analog control voltages cannot be used directly.",
    "无效会话，请刷新页面。": "Invalid session. Refresh the page.", "请求较频繁，请稍后再试。": "Too many requests. Please retry shortly.",
    "数据已过期或不属于当前会话，请重新上传。": "This dataset has expired or belongs to another session. Please upload it again.",
    "服务器数据容量已满，请删除不用的数据或稍后重试。": "Backend storage is full. Clear unused data or retry later.",
    "请上传 .csv 文件。": "Upload a .csv file.", "CSV 超过 32 MB。": "The CSV exceeds 32 MB.",
    "请等待当前辨识任务结束后再清除数据。": "Wait for identification to finish before clearing data.",
    "此后端未安装 PANN 依赖，请选择积分辨识。": "This backend does not have PANN dependencies. Select integral identification.",
    "当前会话已有辨识任务，请等待完成。": "An identification job is already running in this session. Wait for it to finish.",
    "辨识队列已满，请稍后重试。": "The identification queue is full. Retry later.", "任务不存在或已过期。": "The job does not exist or has expired.",
    "训练段太短或采样太稀；需要至少约 10 个开关周期及充分的周期内采样。": "Training data is too short or sparse. Provide about 10 switching cycles with sufficient samples per cycle.",
    "可用导通动态不足，无法可靠辨识 L；请选择包含电流变化的时间段。": "Insufficient conducting-state dynamics to estimate L reliably. Select an interval with current variation.",
    "电压动态不足，C/R 无法分离；请加入启动或负载扰动数据。": "Insufficient voltage dynamics to separate C and R. Include startup or load-transient data.",
    "C/R 回归病态，当前数据不能独立确定参数；需要更丰富的动态激励。": "The C/R regression is ill-conditioned. Richer dynamic excitation is needed to identify the parameters independently.",
    "前向仿真未收敛；请检查参数、工况和时间单位。": "Forward simulation did not converge. Check parameters, operating conditions and time units.",
    "前向仿真出现非有限值。": "Forward simulation produced nonfinite values.",
    "参数辨识需要 PWM 列。请从 PLECS 导出实际 0/1 开关状态。": "Identification requires a PWM column. Export actual 0/1 switching states from PLECS.",
    "辨识区间必须在 CSV 时间范围内，并且结束时间大于开始时间。": "The identification interval must be within the CSV time range, with the end after the start.",
    "选定区间需要至少 100 个采样点和 15 个开关周期。": "Select an interval with at least 100 samples and 15 switching cycles.",
    "单次最多分析 3000 个开关周期，请缩小辨识区间。": "The limit is 3000 switching cycles per analysis. Select a shorter interval.",
    "检测到明显负电流：当前模型是非同步 Buck，请检查信号方向或使用适配模型。": "Significant negative current detected. This model is an asynchronous Buck; check signal direction or use a compatible model.",
    "训练或留出段采样不足，请调整区间。": "Not enough samples in the training or validation segment. Adjust the interval."
  };
  Object.assign(english, {
    "抗噪加权积分 · 直接辨识":"Noise-aware weighted integral · Direct fit",
    "直接输出加权积分估计，不做 PANN 微调，无需 PyTorch。":"Use the weighted-integral estimate directly, without PANN refinement or PyTorch.",
    "从加权积分初值继续优化窗口状态和参数；不保证比积分结果更准确。":"Refine window states and parameters from the weighted-integral estimate; accuracy may not improve.",
    "自动（训练噪声）":"Auto (training noise)",
    "窗口初值":"Window initial states", "从含噪数据联合估计（推荐）":"Joint estimation from noisy data (recommended)",
    "固定测量初值（对照）":"Fixed measured states (comparison)", "窗口长度":"Window length", "训练轮数":"Training epochs",
    "1 个开关周期":"1 switching cycle", "2 个开关周期":"2 switching cycles", "3 个开关周期":"3 switching cycles",
    "只用训练段估计初值；留出验证连续运行，不重置状态。窗口更长不一定更准确。":"Estimate states from training data only; validation runs continuously without resets. Longer windows are not always more accurate.",
    "历史会话":"Chat history", "新会话":"New conversation", "＋ 新建会话":"＋ New conversation",
    "新建会话，保留当前历史":"New conversation, keep current history",
    "收起实验配置":"Collapse experiment settings", "展开实验配置":"Expand experiment settings",
    "收起历史会话":"Collapse chat history", "展开历史会话":"Expand chat history",
    "对话长期保存在当前浏览器，清除网站数据会删除历史；不会跨设备同步。":"Conversations stay in this browser until site data is cleared. They do not sync across devices.",
    "对话保存在此浏览器，不自动过期；后端 CSV 默认 1 小时过期。":"Conversations stay in this browser without automatic expiry; backend CSV data expires after 1 hour by default.",
    "历史保存失败：浏览器存储不可用或已满。当前对话仍可使用，请勿关闭页面。":"History could not be saved: browser storage is unavailable or full. You can keep chatting, but do not close this page.",
    "历史 CSV 需重新关联":"Historical CSV needs reconnecting",
    "历史对话已恢复；CSV 已过期或不可用。如需重新分析，请再次上传 CSV。":"Conversation restored; the CSV has expired or is unavailable. Upload it again to rerun analysis."
  });
  const patterns = [
    [/^(.+) 行 · CSV 已读取$/, "$1 rows · CSV loaded"],
    [/^已关联：(.+)$/, "Attached: $1"],
    [/^(.+) 个有效采样点$/, "$1 valid samples"],
    [/^正在拟合与验证 · (.+) 秒$/, "Fitting & validating · $1 s"],
    [/^辨识完成 · 用时 (.+) 秒$/, "Identification complete · $1 s"],
    [/^(.+) 计算点 · (.+) 显示点（显示已抽样）$/, "$1 computation points · $2 displayed (downsampled)"],
    [/^(.+) 计算点 · (.+) 显示点$/, "$1 computation points · $2 displayed"],
    [/^请检查工况输入范围：(.*)$/, "Check the operating-condition input: $1"]
  ];
  const storageKey = `buck-language:${new URL(".", location.href).pathname}`;
  let language = "zh";
  try { if (localStorage.getItem(storageKey) === "en") language = "en"; } catch { /* session-only choice */ }
  const bindings = new Map();
  const attributes = new Map();
  function translate(value) {
    const source = String(value ?? "");
    if (language === "zh") return source;
    return source.split("\n").map(line => {
      const key = line.trim();
      if (Object.hasOwn(english, key)) return line.replace(key, english[key]);
      if (key.includes(" · ")) {
        const parts = key.split(" · ");
        if (parts.every(part => Object.hasOwn(english,part))) return line.replace(key,parts.map(part => english[part]).join(" · "));
      }
      for (const [pattern, replacement] of patterns) if (pattern.test(key)) return line.replace(key,key.replace(pattern,replacement));
      return line;
    }).join("\n");
  }
  function text(element, value) {
    bindings.set(element, String(value ?? ""));
    element.textContent = translate(value);
  }
  function node(value) {
    const result = document.createTextNode(translate(value));
    bindings.set(result, String(value));
    return result;
  }
  function attr(element, name, value) {
    if (!attributes.has(element)) attributes.set(element, new Map());
    attributes.get(element).set(name,value);
    element.setAttribute(name,translate(value));
  }
  function setLanguage(next) {
    language = next === "en" ? "en" : "zh";
    try { localStorage.setItem(storageKey,language); } catch { /* keep working without storage */ }
    document.documentElement.lang = language === "en" ? "en" : "zh-CN";
    for (const [element, source] of bindings) {
      if (!element.isConnected) { bindings.delete(element); continue; }
      element.textContent = translate(source);
    }
    for (const [element, entries] of attributes) {
      if (!element.isConnected) { attributes.delete(element); continue; }
      for (const [name, source] of entries) element.setAttribute(name,translate(source));
    }
    const button = document.getElementById("language-toggle");
    button.textContent = language === "zh" ? "EN" : "中文";
    button.lang = language === "zh" ? "en" : "zh-CN";
    button.title = button.getAttribute("aria-label");
    document.dispatchEvent(new CustomEvent("buck-language-change",{detail:{language}}));
  }
  // Register only the initial application copy. Later user content is never scanned.
  const walker = document.createTreeWalker(document.documentElement,NodeFilter.SHOW_TEXT);
  for (let current = walker.nextNode(); current; current = walker.nextNode()) {
    if (/[\u4e00-\u9fff]/.test(current.textContent) && !current.parentElement.closest("script,style")) {
      bindings.set(current,current.textContent);
    }
  }
  for (const element of document.querySelectorAll("[title],[placeholder],[aria-label],[data-prompt],meta[name=description]")) {
    for (const name of ["title","placeholder","aria-label","data-prompt","content"]) {
      const value = element.getAttribute(name);
      if (value && /[\u4e00-\u9fff]/.test(value)) attr(element,name,value);
    }
  }
  document.getElementById("language-toggle").onclick = () => setLanguage(language === "zh" ? "en" : "zh");
  setLanguage(language);
  return {translate,text,node,attr,get language(){return language;}};
})();
