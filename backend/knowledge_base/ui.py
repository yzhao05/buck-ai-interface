"""Searchable UI manual, reviewed against the shipped frontend.

Each entry owns explicit control IDs. Coverage tests detect newly added controls
so a frontend change cannot silently leave the assistant's manual incomplete.
No browser values, credentials or runtime DOM are copied into the knowledge base.
"""

UI_DOCUMENTS = []


def entry(key, title, controls, tags, content, source='frontend/app.js'):
    controls = controls.split()
    UI_DOCUMENTS.append({
        'id': 'ui-' + key, 'title': '界面说明：' + title, 'url': None,
        'tags': '界面 操作 功能 按钮 面板 ui help ' + tags + ' ' + ' '.join(controls),
        'content': f'位置/控件：{title}。\n{content}\n这是本项目界面说明，不代表当前页面状态。实现依据：frontend/index.html；{source}。',
        'controls': controls, 'implementation_sources': ['frontend/index.html', source],
    })


entry('overview', '工作台总览与区域导航', 'workspace sidebar-panel conversation-panel analysis-panel',
      '全部 每个 所有 入门 教程 布局 总览 workspace overview panels',
      '左栏“实验与历史”包含导入波形、信号与单位映射、参数辨识设置和历史会话；中间是 AI 实验助手、任务进度和提问区；右栏是波形与辨识结果，进入参数辨识后可查看。顶部连接设置管理后端、PLECS 和备份。本条是区域目录，具体控件说明保存在各自条目中。')
entry('panes', '左右栏与分区收起/展开', 'toggle-left-pane toggle-right-pane toggle-experiment toggle-import toggle-history experiment-content import-content history-content',
      '收起 展开 左栏 右栏 实验配置 导入波形 历史会话 collapse expand',
      '左右箭头折叠/展开对应侧栏；实验配置、导入波形、历史会话标题旁的减号折叠该分区。只改变显示，不删除数据、不停止后台任务。再次点击可展开。实验配置和历史会话的折叠状态会在浏览器保存。')
entry('language', '顶部语言与服务状态', 'language-toggle connection-status close-notice notice',
      'EN 中文 英文 语言 连接状态 提示 language connection status',
      'EN/中文切换界面语言；后端已连接表示后端健康检查成功，不等于模型回答或 PLECS 仿真已经成功。尚未连接时去连接设置核对地址。提示条 × 只关闭当前提示，不修复错误也不撤销操作。')
entry('csv-upload', '导入波形：拖入或选择 PLECS CSV', 'csv-file dropzone',
      '上传 导入 CSV 波形 upload import waveform',
      '点击上传区或拖入 CSV，最大 32 MB。使用本地后端时，也可在连接设置保存包含实验 CSV 的文件夹，再告诉助手文件名。助手可递归搜索该目录，找到明确匹配后直接加载，同名文件可用相对路径区分。导入后读取列、建议映射并检查数据，不会仅因上传就开始参数辨识。需要时间、电感电流、输出电压；实际 PWM 列可选。接着核对信号与单位映射。')
entry('csv-library', '已有实验示例与历史 CSV', 'load-demo empty-demo toggle-csv-library csv-library-list csv-library-menu',
      '示例 历史CSV 下拉 文件列表 demo historical CSV library',
      '加载已有实验示例（空波形区的加载实验示例也是同一入口）加载项目示例数据，不生成一次新的实测实验。旁边下拉箭头展开/收起历史 CSV 列表，点击聊天区不会自动收起；列表内按Esc可收起。点击文件后收起列表并加载到当前会话；这不等于切换到文件原来的会话。加载失败时先检查备份状态或重新上传原文件。', 'frontend/csv-library.js')
entry('csv-clear', '清除当前数据 ×', 'clear-data file-info',
      '清除 数据 删除CSV clear current data',
      '文件名旁 × 清除当前后端数据集和当前会话保存的 CSV 附件，重置当前实验显示；不是删除整个历史会话。正在处理任务时不可操作。需要再次计算时重新加载/上传 CSV。')
entry('mapping', '信号与单位映射及应用并检查数据', 'mapping-details map-time map-current map-voltage map-pwm time-unit current-unit voltage-unit apply-mapping',
      '列映射 时间列 电流列 电压列 PWM列 单位 mapping time current voltage units apply',
      '时间、电感电流、输出电压下拉框指定 CSV 的对应列；PWM 可不选，选中时必须是实际 0/1 开关状态而不是占空比。时间单位 s/ms/us/ns，电流 A/mA，电压 V/mV，应匹配文件原始数值。修改映射后需点击“应用并检查数据”，才重新校验、换算和显示统计；未应用时映射不可用于辨识。此按钮不启动辨识。')
entry('identification-panel', '进入参数辨识 / 退出辨识', 'enter-identification leave-identification identification-content identification-form',
      '进入 退出 参数辨识 基本信息 enter leave identification',
      '进入参数辨识打开左侧参数表单和右侧分析区域；退出辨识收起辨识界面。这些按钮切换界面，不等于开始计算或取消已提交的任务。真正计算使用“开始参数辨识”。')
entry('pwm-source', 'PWM 来源', 'pwm-source controller-settings',
      'PWM来源 自动 实测 重建 pwm source measured controller auto',
      '自动：映射中有 PWM 列就用 CSV 实测状态，否则使用已知控制器重建；CSV 实际开关状态：必须有实际 0/1 PWM 列；三列 CSV·已知控制器重建：使用训练电压和已确认 RC 控制电路生成 PWM。重建仅适用于固定接线的非同步 Buck，固定已知 R 识别 L/C，CSV 从 t=0 开始、负载恒定、结束小于1 ms。重建不是测量；留出段用预测电压自主闭环。')
entry('controller-confirm', '确认电路配置', 'controller-confirmed',
      '确认 接线 参数 controller confirmed topology',
      '只有实际电路接线与默认图一致、参数与当前设置一致，才选已确认。选择尚未确认会阻止依赖控制器重建的计算。修改电路设置、输入电压、频率、负载后通常需重新确认。上传参考图不能自动确认，也不能改变计算模型的固定拓扑。')
entry('pwm-denoise', '重建电压去噪与去噪窗口', 'pwm-denoise pwm-filter-cycles',
      '去噪 SG 平滑 Modified-sinc FIR 滤波 窗口 pwm_denoise pwm_filter_cycles denoise filter',
      'SG 平滑是基线，Modified-sinc FIR 是候选，不去噪用于对照。去噪窗口以开关周期计，界面范围0.1～8，默认6。只处理训练段用于 PWM 重建的电压，原始电流/电压仍用于辨识；不修改真实控制器，不增加锁存。仅控制器重建模式生效，不保证窗口更大更准确。')
entry('pwm-quality', 'PWM 重建质量检查', 'pwm-quality-details pwm-quality-summary pwm-quality-json',
      'PWM质量 拒绝 停止 质量检查 quality reconstruction',
      '展开查看重建 PWM 的质量结论与详细数据。质量检查不通过会停止该次计算，应依据具体原因检查频率、去噪、接线和时钟等，不能把失败状态解释为成功辨识。')
entry('vin', '输入电压 Vin', 'vin circuit-vin', '输入电压 vin input voltage',
      '输入电源电压，单位 V，界面0.1～2000、默认28。用于 Buck 物理模型，应填实际已知输入，不是输出电压或待辨识量。电路编辑器中的同名值保存后同步到左栏；重建模式改动后需重新确认接线参数。')
entry('frequency', '开关频率', 'frequency circuit-frequency', '开关频率 frequency_hz switching frequency kHz',
      '界面单位 kHz，0.1～2000、默认100；后端使用 Hz。用于开关周期、PWM重建、按周期定义的训练/去噪窗口；“末10周期”视图也使用频率。不是 CSV 采样率。电路编辑器保存后同步到左栏。')
entry('fit-mode', '辨识目标 LC / LCR 与已知负载', 'fit-mode fixed-r circuit-fixed-r fixed-r-label',
      '辨识目标 LC LCR 固定负载 已知负载电阻 resistance_ohm mode load resistance',
      'LCR 联合估计电感L、电容C、负载R；LC只估计L/C，R使用填写的已知负载，单位Ω、默认6，必须大于0.01且不超过100000。R不是ESR或电感绕组电阻。控制器重建只支持LC。电路编辑器的已知负载保存后同步左栏。')
entry('method', '计算方法：PANN / 抗噪加权积分', 'fit-method method-hint pann-settings',
      '计算方法 PANN 加权积分 weighted_integral method PyTorch 灰色',
      '抗噪加权积分直接输出端点零权重积分估计，不做PANN微调，无需PyTorch。PANN以积分估计为初值继续优化参数与状态，需要后端PyTorch可用；不可用时选项禁用。PANN不保证更准确。切换方法会显示/隐藏PANN专属设置。')
entry('pann-state', 'PANN 窗口初值', 'pann-state', '窗口初值 联合估计 固定测量初值 pann_initial_state initial state',
      '联合估计从含噪训练数据优化窗口初始电流/电压，另有50轮状态预热；固定测量初值把每个训练窗口起点的测量值作为初始条件，是对照选项。只影响PANN。留出验证连续运行，不逐窗口重置状态，也不以干净参考选初值。')
entry('pann-window', 'PANN 窗口长度', 'pann-cycles', '窗口长度 自动 训练噪声 pann_window_cycles window length',
      '自动按训练噪声选择，或明确1/2/3个开关周期。用于PANN训练窗口，不是总辨识区间，也不是去噪窗口。更长不一定更准确；加权积分方法不使用该PANN设置。')
entry('pann-epochs', 'PANN 训练轮数', 'pann-epochs', '轮数 训练 epochs training iterations',
      '界面10～300，默认200，控制PANN训练轮数；联合初值另有50轮预热。当前训练按轮数执行，没有训练墙钟时间上限。Agent回答的时间预算是另一概念。增加轮数不保证参数更准；积分方法不使用此值。')
entry('interval', '开始时间 / 结束时间', 'start-ms end-ms', '区间 起点 终点 时间 start_s end_s interval ms',
      '界面单位ms，后端为秒；留空使用可用数据的对应端点。选择恒定负载且有效的辨识区间，必须开始早于结束。重建模式要求从0开始并结束于1ms前，示例建议0～0.8ms。图上拖动缩放不是修改此处辨识区间。')
entry('train-fraction', '训练区间占比', 'train-fraction train-label', '训练 留出 验证 比例 train_fraction split heldout',
      '滑块50%～85%、步长5%、默认70%。在选定区间按时间取前段训练、后段留出验证，不是随机分样本。同记录留出不等于独立工况验证。提高训练占比会缩短留出段，不保证结果更准。')
entry('identify', '开始参数辨识与任务状态', 'identify job-status identify-complete-hint',
      '开始 计算 禁用 灰色 等待 已完成 identify start disabled',
      '提交当前CSV、已应用映射和设置进行真实数值辨识并显示任务状态。需后端连接、有效数据/配置、可用方法、控制器必要确认，且不能与进行中的任务冲突；同配置已完成时提示修改参数再运行。按钮灰色有多种原因，需结合当前状态判断，不能仅凭说明猜原因。完成后自动保存本会话实验摘要。')
entry('circuit', '参考电路预览与放大 / 编辑', 'edit-circuit circuit-preview circuit-dialog circuit-fields circuit-drawing close-circuit',
      '参考电路 放大 编辑 拓扑 circuit editor preview',
      '点击缩略图或放大/编辑打开电路图与参数窗口。默认图参数可点击聚焦右侧输入框；L、C为待辨识量，不在此输入真值。接线拓扑固定，不能拖线改变模型。关闭 × 丢弃未保存草稿，小屏可横向滑动查看图。', 'frontend/circuit.js')
entry('circuit-image', '上传电路图片 / 交给助手分析 / 恢复默认图', 'upload-circuit circuit-upload-edit circuit-file attach-circuit-image show-default-circuit circuit-default-edit',
      '电路图片 上传 识图 恢复默认图 attach circuit image default',
      '上传PNG/JPG/WebP、最大5MB，压缩后作为参考图随会话保存同步，不自动提取并写入参数。“交给助手分析”将电路图片附加到当前提问，仍需发送消息。“恢复默认图”清除当前参考图片、显示固定接线示意，不重置参数；编辑窗口内操作先改草稿。识图建议须核对。', 'frontend/circuit.js')
entry('circuit-save', '恢复默认参数 / 保存电路设置', 'reset-controller save-circuit circuit-form',
      '恢复默认参数 保存电路设置 reset save controller',
      '恢复默认参数将编辑器各数值（含Vin、频率、已知负载）恢复默认草稿，不等于恢复默认图。保存电路设置校验数值和占空比上下限，将控制器及公共参数写回当前会话，关闭窗口；随后需重新确认电路，结果需要重新辨识。', 'frontend/circuit.js')
entry('controller-resistors', '控制器 R1 / R2 / R3 / R4', 'circuit-R1_ohm circuit-R2_ohm circuit-R3_ohm circuit-R4_ohm',
      'R1 R2 R3 R4 反馈 补偿 电阻 controller resistors',
      '编辑器单位kΩ，默认R1=11、R2=85、R3=120、R4=47。固定接线：vo经R1到va；va与vm间R2和C2并联；vm经R4接地；vm经C3到vb再经R3到运放输出。它们是反馈/补偿元件，不是功率负载R。分别改变反馈网络动态，其关系由控制电路KCL决定，不是任意PID系数。', 'frontend/circuit.js')
entry('controller-capacitors', '控制器 C2 / C3 与初始电压', 'circuit-C2_F circuit-C3_F circuit-initial_c2_V circuit-initial_c3_V',
      'C2 C3 初始电压 initial_c2_V initial_c3_V controller capacitors',
      'C2/C3为补偿电容，单位nF，默认1.1/2.7，不是待辨识输出电容C1。C2初始电压=va−vm，C3初始电压=vb−vm，单位V，默认均0；表示t=0控制器电容状态，不是PANN窗口电压初值。影响控制器启动与PWM重建。', 'frontend/circuit.js')
entry('controller-gain', '运放增益与参考电压', 'circuit-opamp_gain circuit-reference_V',
      '运放增益 参考电压 opamp_gain reference_V gain reference',
      '运放增益无单位，默认1e6，保留有限增益模型；参考电压单位V，默认5，是控制器参考端值，不等于功率输出电压目标的直接数值。输出关系还由反馈网络决定。', 'frontend/circuit.js')
entry('controller-pwm', '调制增益、占空比上下限、载波初始相位', 'circuit-pwm_gain circuit-duty_min circuit-duty_max circuit-carrier_phase_at_t0',
      '调制增益 占空比 限幅 载波 初始相位 pwm_gain duty_min duty_max carrier_phase_at_t0',
      '调制增益默认0.25，将运放控制输出缩放为调制量；占空比下限默认0.1、上限0.9，均为0～1比例且下限必须小于上限。载波初始相位以周期计，范围0≤相位<1、默认0，不是角度或秒。共同决定重建PWM的幅度约束和时钟对齐，应按实际仿真填写。', 'frontend/circuit.js')
entry('history', '历史会话列表 / 新建会话 / 清空图标', 'history-list new-chat clear-chat',
      '历史会话 新建 清空 重置 new chat history conversation',
      '点击历史项切换并恢复其消息、表单、实验和可恢复附件；新建会话与聊天底部↺都保存现有历史后打开新会话，不是删除历史。新会话不自动继承当前CSV。正在运行或保存失败时可能阻止切换。', 'frontend/history.js')
entry('delete-chat', '删除当前会话', 'delete-chat', '删除 会话 不可撤销 delete conversation',
      '经确认后删除当前会话、实验记录及浏览器/后端保存的CSV；离线删除将在重连后同步，不能撤销。与新建会话、清除当前数据不同。纯询问该按钮用途不授权执行删除。', 'frontend/history.js')
entry('sync', '立即同步与备份状态', 'sync-now sync-status history-status csv-storage-status',
      '同步 备份 恢复 存储 sync backup archive',
      '立即核对并补齐浏览器和后端的会话、实验记录与CSV备份。以同步状态判断是否完成；仅本地存在不代表已备份。后端存储若非持久卷，跨部署仍可能丢失。同步不是开始辨识，也不是下载报告。', 'frontend/sync.js')
entry('experiment-history', '已保存实验', 'experiment-history experiment-runs',
      '已保存实验 次数 摘要 experiment history saved runs',
      '每次完成辨识后保存实验摘要，切换方法或PWM不覆盖旧记录。记录展示文件、方法、PWM和参数，可让助手比较同会话多次实验。摘要不等于完整原始CSV、全波形或逐轮训练数据；不能把同一文件多次计算当作独立实验。', 'frontend/experiments.js')
entry('conversation-search', '会话检索 / 填写检索问题 / 查看消息来源', 'analysis-history compare-analyses',
      '会话检索 填写检索问题 展开检索来源 收起检索来源 查看消息 全文 片段 search conversation sources',
      '会话检索面板显示可检索消息，填写检索问题只在空输入框放入带省略号模板，不自动发送；替换主题后发送。Agent可检索本会话记录、分片读取长文并引用原文。回答旁来源箭头展开/收起来源，查看消息定位原消息，片段不代表全文已读。此功能与数值实验记录不同。', 'frontend/analyses.js')
entry('chat', 'AI 实验助手：输入、发送与快捷提问', 'chat-input send-chat chat-form chat-context chat-mode',
      '提问 发送 快捷 理解我的仿真波形 分析误差 规划下一步 电容寿命 chat send prompt',
      '输入最多4000字符；发送箭头或Enter提交，Shift+Enter换行。欢迎区三个快捷问题会直接发送相应问题，不只是填入输入框。上下文标签表示关联数据，服务模式标签反映当前服务。工具由Agent按问题与已有证据选择，不强制检索；执行辨识、改参数、仿真须依据明确请求。')
entry('reasoning', '回答模式：自动 / 快速 / 思考', 'reasoning-mode',
      '自动模式 快速模式 思考模式 reasoning thinking fast auto',
      '自动当前默认启用thinking；快速关闭thinking；思考启用thinking。都可使用同一工具目录，按预算限制模型调用，快速最多7次、思考最多14次。不是计算方法，也不会切换PANN/积分。模式随会话保存。费用元数据保留在会话记录中，不在回答下方展示；它是本轮本地USD估算（含未完成请求的预留），不是DeepSeek账户账单或每日花费，不能与平台显示的人民币金额直接比较。完整响应按usage结算并释放未使用额度；默认采用DeepSeek对应模型高峰价格，缓存输入单独计价。')
entry('chat-collapse', '聊天输入框：收起 / 展开', 'toggle-chat-compose compact-chat-images',
      '输入框 回复框 收起 缩小 展开 箭头 单行 草稿 collapse expand composer compact',
      '左上角箭头把聊天输入区域收成一行，保留文字输入和发送按钮，再点可展开。只改变显示，不清空草稿、图片或回答模式，不停止Agent。收起时图片工具、回答模式和上下文标签隐藏；若已添加图片，图片数量按钮仍可见，点击展开检查附件。Enter发送；Shift+Enter展开并换行。浏览器记住收起状态。')
entry('chat-images', '添加图片 / 添加电路图 / 图片预览复用', 'attach-chat-image chat-use-circuit chat-image-file chat-image-list chat-image-dialog close-chat-image reuse-chat-image chat-image-preview',
      '添加图片 添加电路图 粘贴 拖入 移除 附加到当前提问 image attachment preview reuse',
      '添加图片、添加电路图按钮与回答模式同一行，位于模式选择左侧；窄聊天区仍保留该顺序，服务信息另行显示。选择、粘贴或拖入PNG/JPEG/GIF/WebP，每次最多3张、单张最大8MB；压缩后发送，GIF只取首帧。添加电路图优先使用已保存参考图，没有上传图时把当前默认图及参数标注转成图片；输入框为空时填入分析提示，仍需发送。待发缩略图的移除按钮只移除本次附件；点击图片可放大，关闭只关闭预览，附加到当前提问复用预览图。历史提到图片不等于本轮重新附带原图。', 'frontend/chat-images.js')
entry('agent-progress', 'Agent 任务进度与恢复面板', 'agent-progress agent-status agent-preview',
      '任务进度 状态 流式 回答 Harness agent progress',
      '显示Agent工具执行状态和逐步到达的回答文本，不显示内部推理。任务完成、失败、中断、停止含义不同；连接断开时后台可能继续运行。折叠面板不停止任务。', 'frontend/agent-harness.js')
entry('agent-steer', '追加指令 / 发送追加指令', 'agent-instruction agent-steer',
      '追加指令 补充 方向 停止条件 steer instruction',
      '向当前活跃Harness任务补充排查方向或停止条件，最多4000字符；空内容或没有当前任务不发送。不是发起独立新聊天，也不是修改正在执行的数值算法参数。', 'frontend/agent-harness.js')
entry('agent-stop', '停止任务 / 找回或恢复任务', 'agent-cancel agent-resume',
      '停止 取消 中断 找回 恢复 agent cancel stop resume recover',
      '停止任务请求取消当前Agent运行，不回滚已经完成的修改或计算。找回/恢复先查询当前会话已保存或最新任务：运行中重新连接，已完成取回回答，中断/失败/取消任务尝试恢复。已执行实验复用证据，状态未知的操作不应盲目重做；恢复产生的配置建议不会自动应用到可能已变更的界面。结果分析任务绑定提交时的快照；若与右侧当前结果不同，会在主对话提示重新选择，不混用结果。', 'frontend/agent-harness.js')
entry('plan', '辨识计划面板：折叠、继续计划 / 取消后续任务', 'identification-plan identification-plan-details plan-continue plan-cancel plan-progress plan-runs',
      '计划 排队 多组 继续计划 取消后续任务 identification plan continue cancel',
      '点击标题左侧箭头可折叠详情，标题上的完成进度仍会更新，计划继续运行。展开可查看文件、每组进度和暂停原因。继续计划按原配置接续未完成步骤，须满足数据、任务状态和必要确认。取消后续任务会保留已完成结果，当前正在执行的数值辨识仍会完成。', 'frontend/identification-plan.js')
entry('statistics', '右侧波形统计卡片', 'stat-voltage stat-current stat-duration stat-points',
      '平均输出电压 电感电流RMS 数据时长 数据点 时间加权 statistics RMS duration',
      '平均输出电压单位V、电感电流RMS单位A，均为当前已分析数据全段的时间加权统计；数据时长单位ms，另显示数据点数。不是参数真值、不是留出RMSE，也不随图形局部缩放变成该局部统计。')
entry('waveform', '波形观测：全段 / 末10周期 / 框选缩放', 'view-full view-tail current-chart voltage-chart chart-container chart-tooltip',
      '全段 末10周期 波形 缩放 双击 测量 模型预测 waveform zoom full tail',
      '全段清除缩放；末10周期按当前波形终点和使用的开关频率显示末段。拖动框选放大、双击还原，悬停显示读数。青色为测量、橙色虚线为预测，横轴ms，电流A、电压V。显示波形会抽样，辨识使用完整所选数据；缩放不修改训练区间或重新计算。')
entry('result-library', '参数辨识结果卡片与历史结果下拉', 'results-card toggle-result-library result-library-menu result-library-list result-archive-note',
      '历史结果 下拉 已保存结果 snapshot result library',
      '标题旁下拉箭头展开/收起本会话辨识记录，点击其他区域保持展开；列表内按Esc可收起。点击记录后收起列表并切换右侧查看的结果及其真值对照，不启动新计算，不把该结果配置自动覆盖到左侧。若后端完整结果过期，仍可显示持久摘要；缺少完整波形/诊断时应说明。', 'frontend/experiments.js')
entry('result-parameters', '预测电感、电容与负载电阻', 'result-l result-c result-r r-label result-badge result-notes',
      '预测电感 预测电容 负载 结果 标签 L C R parameters result badge',
      'L显示μH，C显示μF，R显示Ω。LC模式R是已知固定输入，不是预测；LCR才估计R。结果标签是应用规则判断，不是参数准确性的证明。需要真值对照、拟合/稳定性细项分别判断，不能仅凭绿色标签下结论。')
entry('truth', '参数真值对照与计算相对误差', 'truth-comparison truth-l truth-c calculate-parameter-error parameter-error-values',
      '真实电感 真实电容 真值 相对误差 truth relative error',
      '先有选中辨识结果，再填正的真实L(μH)与C(μF)，点计算相对误差；两者均需提供。公式为|预测−真值|/真值×100%。仅用于当前结果评价并随记录保存，不参与训练、不改变估计参数。修改真值后需重新计算，不能凭文件名补造真值。')
entry('assessment', '留出误差与结果分项判断', 'validation rmse-i rmse-v result-assessment waveform-assessment training-assessment accuracy-assessment',
      '波形拟合 训练稳定性 参数准确性 留出 RMSE NRMSE assessment validation',
      '留出电流RMSE单位A、电压RMSE单位V，是预测对同记录后段测量的偏差。波形拟合、训练稳定性、参数准确性分别评价；应用拟合门槛5%，低波形误差不证明参数准确，无独立真值则准确性未验证。训练稳定但拟合未达标应分别描述。')
entry('training-evidence', '查看训练稳定性依据', 'training-evidence training-evidence-list',
      '训练稳定性 依据 收敛 two-window stability evidence',
      '展开读取该结果记录的规则与训练证据。当前two-window-stability-v2比较前后各20轮平均损失及最近20轮参数波动，连续通过3次才确认；最早第42轮，预热不计。稳定是经验判据，不是数学证明或参数准确证明；历史版本按原记录解释，缺证据不猜测。')
entry('measurement', '噪声与残差诊断 / 需复核的触发原因 / 计算条件', 'measurement-evidence measurement-evidence-list review-reasons diagnostics diagnostic-text',
      '噪声 残差 复核 触发原因 计算条件 诊断 noise residual diagnostics',
      '噪声与残差诊断给出测量/模型差异线索，训练局部噪声估计不是传感器标定，也不改变5%门槛。需复核原因列出当前结果实际触发项；查看计算条件与诊断展开详细记录。缺失旧记录不得编造原因；不能凭最优轮次恰好在末轮否定稳定性。')
entry('reference', '诊断对照与可选参考：干净 CSV', 'choose-diagnostic-reference diagnostic-reference-file diagnostic-reference-confirm diagnostic-reference-status',
      '干净参考 参考CSV 确认 初值交换 诊断对照 clean reference diagnostic controls',
      '可选上传干净参考CSV，与原数据须同列名、单位、时间网格；确认框声明同实验同工况、只去掉测量噪声，未确认不能用于参考对照。只用于评价，不参与训练，不替换主CSV。参考仅在当前后端缓存，刷新/过期需重选。助手分析可尝试四组参数/初值交换，需匹配PANN结果与原CSV，不重新训练。')
entry('analyze', '让助手分析结果：本对话 / 新对话', 'ask-error analysis-menu analyze-here analyze-new',
      '让助手分析结果 在本对话中分析 新对话分析 analyze result',
      '让助手分析结果按钮展开/收起菜单，点击聊天区保持展开，菜单内按Esc可收起；在本对话中分析发送右侧显示结果的完整数值快照，包括刚完成的结果；新对话分析先保存原会话并携带该结果建立新会话再分析，原任务缓存过期仍可复制快照。分析范围以右侧选中结果为准，使用该次计算参数、映射与真值对照，不用左栏待提交配置。快照随任务保存，后端缓存丢失不要求重新辨识；缺少原始波形或训练轨迹时明确说明对应限制。旧版未携带快照的失败任务可重新点击此按钮提交。更换历史结果后分析对象随选中项变化；在主对话要求比较两次结果时，Agent 可从同源目录按名称、显示时间、序号或精确ID定位，再读取各自快照的参数、配置、误差和诊断。同名无法唯一定位时询问，不猜选；读取不自动切换右侧或启动辨识。需要完成结果且当前无冲突任务。')
entry('reports', '导出报告文件夹 / 分别下载 PDF 和 JSON', 'download-report report-downloads download-report-files report-pdf-link report-json-link report-location-dialog close-report-location confirm-report-files',
      '导出 报告 保存位置 下载 PDF JSON 文件夹 export report folder download',
      '导出当前选中辨识结果。浏览器支持目录选择时，选保存位置后生成同名文件夹、PDF与JSON。否则报告保存位置窗口说明限制，可确认分别下载同名两文件，不生成ZIP；具体下载位置由浏览器决定，可在浏览器设置开启下载前询问。关闭窗口取消此选择，不删除已有文件。PDF用于阅读，JSON保留结构化结果；不自动产生新的辨识。', 'frontend/report-files.js')
entry('settings', '连接设置：后端地址 / 保存并连接 / 关闭', 'open-settings settings-dialog settings-form close-settings api-url settings-status',
      '连接设置 后端地址 保存并连接 localhost Render server backend settings',
      '填写分析服务http(s)地址，本地启动可自动连接；HTTPS页面需HTTPS后端。保存并连接校验设置、连接服务并同步备份。切换后端前需清除当前数据，忙碌时禁止切换。关闭×只关窗口。这里不是填写DeepSeek模型API地址；连接后端不等于PLECS已在线。')
entry('access-token', '应用访问口令 / 清除已记住的口令', 'access-token forget-access-token',
      '应用口令 访问口令 APIKey 密钥 token password forget',
      '填写后端APP_ACCESS_TOKEN，本地未配置可留空；不是DeepSeek API Key，后者仅在后端环境变量配置。保存后浏览器按后端地址记住口令；清除已记住口令仅删除当前浏览器对应后端凭据，不改服务器口令、不删除历史或恢复码。解释时不需要用户提供密钥内容。')
entry('plecs-path', 'PLECS 程序路径与电路模型文件夹', 'plecs-executable plecs-model-directory',
      'PLECS 路径 模型 文件夹 RPC executable model directory',
      '程序路径是本机PLECS可执行文件，用于需要时启动。模型文件夹用于递归搜索.plecs模型；本地后端也可按用户指令从该目录搜索并加载实验CSV。搜索CSV无需启动PLECS。操作模型时通过本机RPC端口1080；云端操作PLECS需本机连接器。文件名匹配不保证同电路同工况，歧义时应确认。')
entry('plecs-binding', '仿真结果自动辨识绑定', 'plecs-binding-model plecs-binding-current plecs-binding-voltage plecs-binding-pwm plecs-binding-l plecs-binding-c plecs-binding-confirmed plecs-binding-same',
      '绑定 模型文件名 电流输出端口 电压输出端口 PWM输出端口 电感参数键 电容参数键 binding output port',
      '模型文件名指定被绑定的PLECS模型；电流/电压端口填顶层Output顺序（从1起、最多64），单位A/V；PWM端口可空，填写时须0/1状态。电感/电容参数键如L1.L、C1.C用于元件绑定。第一确认框核对物理量、单位、元件和辨识配置，第二框确认模型与数据同电路同工况。绑定只用于仿真对照自动重辨识，不改左栏设置；不猜测端口意义。')
entry('bridge', '本机连接器配对码 / 检查连接器状态', 'bridge-pair bridge-check bridge-instructions',
      '配对码 连接器 在线 离线 bridge pairing companion',
      '本地后端直接连接PLECS，无需配对；检查本机PLECS连接会探测RPC是否就绪。使用云端后端时，先保存后端和本机路径，再生成配对码，在本机按提示运行连接器。云端的检查按钮查询连接器配对及在线状态；重新配对可能撤销旧连接器凭据。', 'frontend/agent-harness.js')
entry('sync-code', '同步恢复码与复制', 'sync-code copy-sync-code',
      '同步恢复码 复制 换浏览器 找回 backup recovery code',
      '自动生成并保存的64位十六进制码，用于访问对应后端备份；复制按钮只复制到剪贴板。换浏览器或清空本地后，在同一后端填入原码并保存连接才能找回仍存在的后端副本。保管好且不要分享；不是DeepSeek密钥或连接器配对码。备份没有完成或服务器数据丢失时恢复码不能凭空还原数据。')
entry('message-evidence', '回答下方工具、来源与证据面板', '',
      '误差假设与证据 仿真重新辨识与误差比较 PLECS对比实验记录 诊断对照执行记录 多次实验一致性依据 来源 工具记录',
      '回答下方按实际返回内容展示工具执行记录、误差假设、仿真对比、诊断对照和多次实验一致性依据；展开查看证据。工具已执行不代表假设已证实，历史摘要不代表新计算，缺证据应明确。知识来源标签和费用栏不在回答下方展示。新回复、历史回复与任务预览隐藏内部知识库引用标记及仅列出这些标记的依据列、来源清单；主对话以自然语言说明状态，不附加程序字段转储，前端隐藏括号内 identification_enabled、controller_confirmed、result_loaded 的布尔赋值。实际分析理由、单位、公式、条件、普通网页链接与代码示例保留；明确要求的调试字段在代码块中给出。需要用户确认、补充或操作的内容必须在主回复中逐项反馈，不只放在任务进度中；加载数据并进入辨识后的回执根据实际应用状态列出列与单位映射、实验设置及后续操作，已应用项不重复要求应用，不自动启动辨识。Agent 启动前必须有用户明确选择的PWM来源和计算方法；未选时在主对话询问缺失项，不能使用默认值训练。电路或参考数据确认不能代替方法及来源选择；已明确选择且设置未变化时无需重复询问，多次计划也须覆盖全部组合。原始回答、来源与预算元数据仍保存，会话检索的原文查看入口保留。')
entry('project-links', 'Buck Lab 标志、实验工作台与项目来源链接', 'source-link',
      '标志 首页 导航 项目来源 Inspired by PE-GPT source link',
      'Buck Lab标志链接当前站点首页，实验工作台是当前导航标签；页脚Inspired by PE-GPT和项目来源链接打开参考项目仓库，不执行分析或保存报告。')
