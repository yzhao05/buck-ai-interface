# Buck Lab · AI 实验工作台

面向 ST4001 的 Buck 变换器分析原型：PLECS CSV → 波形检查 → L/C/R 辨识 → 留出验证 → DeepSeek 对话。采用 **GitHub Pages 静态前端 + Render FastAPI 后端**，本地也可以一条命令启动。

## 本地运行

在本文件所在文件夹双击 `启动界面.cmd`，保持窗口打开，然后浏览器访问：

**http://127.0.0.1:8000**

也可以在 PowerShell 运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\start-local.ps1
```

默认以中间对话区为主。左侧“导入波形”有折叠/展开按钮；参数辨识默认仅显示“进入参数辨识”。点击进入后，左侧展开基本信息，右侧显示波形与辨识结果；退出时保留数据、配置和结果。

右上角的 **EN / 中文** 按钮即时切换界面语言，当前浏览器会记住选择。切换保留已上传数据、输入值、波形和辨识结果；已有对话、CSV 文件名/列名及原始后端诊断内容保留原文。

左侧“实验配置”和“历史会话”标题右侧可分别折叠/展开，浏览器会记住状态。点击“新建会话”或输入框下方的重置按钮会保留旧会话并创建新会话；历史列表可随时切换，正在请求或计算时需等待完成。

对话全文、来源与工具标记、草稿、实验配置和 CSV 关联引用保存在当前浏览器的 localStorage，不自动过期；按项目路径及后端地址隔离。不同会话使用不同后端会话标识，上传、清除 CSV 不再清空对话。刷新或重新打开浏览器后可恢复并续聊，每次问答发送最近 10 条历史作为上下文（单条最多 8000 字符），完整历史仍可在界面查看。此功能不提供跨设备同步；清除网站数据、无痕模式结束或浏览器存储被清理会丢失本地记录，存储不可用或已满时会明确提示保存失败。

原始 CSV 和波形数组不写入历史存储，后端 CSV/任务仍默认 1 小时过期，服务重启也可能提前失效。恢复历史会话时会重新验证关联：可用则恢复波形和仍有效的辨识结果；不可用则保留对话并提示重新上传。断网时也可查看历史，恢复后端连接后才能继续在线问答或重新计算。

消息按独立条目保存，旧标签页保存或关闭不会用旧副本覆盖新增消息。未确认的列映射按草稿恢复，需点击“应用并检查数据”；辨识结果只有在文件指纹、映射和参数配置一致时才恢复。

先点击“加载已有实验示例”，再点“进入参数辨识”，确认基本信息后点“开始参数辨识”。示例是你已有的真实 PLECS Case16A CSV，默认分析 0～0.8 ms，避开 1 ms 的负载变化。无 Key 时也能上传、分析、辨识及使用明确标注的本地规则回答。

## 配置 DeepSeek

复制 `.env.example` 为 `.env`，在本地编辑器中填写：

```dotenv
DEEPSEEK_API_KEY=在这里填自己的密钥
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-flash
```

随后重启后端。密钥不要填进 `frontend/config.js`，也不要提交 GitHub。默认模型依据 2026-10-15 查阅的 [DeepSeek 官方文档](https://api-docs.deepseek.com/zh-cn/) 设置；可用 `DEEPSEEK_MODEL` 更改。已实现原生工具调用，不执行模型生成的任意代码。配置 Key 之前不声称已完成真实 API 联调。

模型收到问题、最近对话、计算摘要、当前配置及检索资料。原始完整 CSV 不发送给 DeepSeek。未配置 Key 或调用失败会显式标注本地规则模式，不伪装成 AI 回答。

## 任务分类 Agent

参考 PE-GPT 的“先分类、再进入对应任务”结构，现在每条消息会先经过独立的任务分类 Agent，再交给任务处理流程：

```text
用户消息 + 最近对话 + 界面状态
    → 分类 Agent（一次独立 DeepSeek 调用，只返回任务标签）
    → 主任务 + 最多两个次任务
    → 对应提示词、上下文和工具权限
    → 知识检索 / 波形分析 / 数值计算 → 回答
```

| 主任务 | 用途 | 可启动辨识 |
|---|---|---|
| 使用指导 | CSV、连接、界面和操作步骤 | 否 |
| 公式知识 | 功率级、控制级、原理与公式 | 否 |
| 波形分析 | 实测统计与波形现象 | 否 |
| 参数辨识 | 明确要求执行 L/C/R 估计 | 是，仍检查入口、数据和配置 |
| 误差诊断 | 读取已有结果、解释误差、提出修改计划 | 否 |
| 硬件寿命 | 器件应力、选型、损耗和寿命条件 | 否 |
| 一般问答 | 其他问题与系统能力边界 | 否 |

分类 Agent 只拿到当前消息、最近 6 条历史和数据/结果/辨识入口的状态标记，不接收原始 CSV，也不能执行工具或更改参数。它使用 [DeepSeek 工具调用接口](https://api-docs.deepseek.com/zh-cn/api/create-chat-completion/)返回 `route_task`，服务器验证分类枚举后分发；后续回答模型只能调用当前任务允许的工具。

“先辨识参数，再解释公式”会进入参数辨识主任务并兼顾公式知识；查看辨识结果、询问按钮位置、报告已完成、只问原理或明确拒绝执行不会启动新辨识。单独回复“继续”“好的。”及仅引用“开始参数辨识”也不作为执行请求。公式任务不依赖已上传数据仍然有效。混合任务以主任务统一决定执行权限，次任务只补充处理内容。回答旁显示任务类别；未配置 Key 显示“本地规则分类”，分类服务失败显示明确降级提示。

默认沿用 `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL` 和 `DEEPSEEK_MODEL`，无需新增必填环境变量。可选设置 `DEEPSEEK_ROUTER_MODEL` 为分类专用模型。配置 Key 后每次问答通常增加一次短分类请求，因此会增加少量延迟和 API 用量；分类请求设置 15 秒读超时，失败时使用规则分类继续处理。这里实现的是一个分类 Agent 加各任务处理流程，并未训练多个独立模型。

## 从零部署 GitHub Pages + Render

**只将 `buck-ai-interface` 文件夹作为新仓库根目录；不要上传整个 ST4001、虚拟环境、reference 或 .env。**

1. 在 GitHub 新建仓库，例如 `buck-ai-interface`。把本项目文件提交到其 `main` 分支。`.github`、`.gitignore` 等隐藏文件也要保留。
2. 在 Render 选择 **New → Blueprint**，连接这个仓库，使用根目录 `render.yaml`。也可手动建立 Python Web Service：构建命令 `pip install -r requirements-lock.txt`，启动命令 `uvicorn backend.main:app --host 0.0.0.0 --port $PORT --workers 1`，健康检查 `/api/health`。
3. Render 环境变量设置：

   | 变量 | 值 |
   |---|---|
   | `DEEPSEEK_API_KEY` | 自己的 DeepSeek Key；留空只提供本地规则分析 |
   | `DEEPSEEK_MODEL` | `deepseek-flash`，或账户支持的模型 |
   | `APP_ENV` | `production` |
   | `APP_ACCESS_TOKEN` | Blueprint 自动生成；手动部署则设置至少 20 字符的应用口令 |
   | `CORS_ORIGINS` | `https://你的用户名.github.io`，**不包含仓库路径和结尾斜杠** |
   | `SOURCE_URL` | `https://github.com/你的用户名/buck-ai-interface`，应提供对应版本完整源码 |
   | `PYTHON_VERSION` | `3.13.5` |

4. Render 成功后记下 `https://你的服务.onrender.com`。先访问它的 `/api/health` 确认 `status: ok`。
5. GitHub 仓库 **Settings → Secrets and variables → Actions → Variables** 新建 `RENDER_API_URL`，值为上述服务地址。它是公开地址，不是 Secret。不要在 GitHub Variables 中放 DeepSeek Key。
6. GitHub **Settings → Pages → Source** 选择 **GitHub Actions**。在 Actions 手动运行 `Deploy interface to GitHub Pages`。该流程只发布 `frontend/`。
7. 打开 Pages 地址，点“连接设置”，输入 Render 中的 `APP_ACCESS_TOKEN`，点击“保存并连接”。这是你的应用口令，不是 DeepSeek Key。当前浏览器会自动记住口令，刷新或下次打开时自动使用；换浏览器、无痕模式或清除网站数据后需要重新输入。口令按当前项目路径和后端地址分别保存，切换后端不会沿用另一个后端的口令。在“连接设置”点击“清除已记住的口令”可以删除记忆；Render 口令变更后在此重新填写并保存。
8. 加载示例、辨识、提问并导出报告，确认整条链路。

也可直接在页面“连接设置”填写后端地址；地址保存在当前浏览器中，会覆盖 config.js 的默认地址。换后端前先清除当前数据。Render 的资源配额或休眠可能导致首次请求等待，页面会显示连接/超时状态。

## 已实现

- 中文工作台：拖放上传、列映射、单位选择、波形显示、框选缩放、模型对比、JSON 报告导出。
- CSV：UTF-8/GB18030、逗号/分号/Tab；检查列、有限数值、时间顺序与 PWM；重复时间保留事件后的最后一行。
- 时间加权平均值/RMS；显示数据按包络抽样，计算使用完整选定区间。
- 积分辨识：L/C/R 联合拟合，或固定负载 R 的 L/C 拟合；非均匀采样；排除电感辨识中的零电流关断区间；正值边界与条件数检查。
- 验证：按时间分训练/留出。使用实测 PWM，从所选区间起点连续前向仿真，后段不重置到测量状态。
- 可选 PANN：使用压缩包里的物理递归网络，训练段积分估计初始化，再用波形损失微调。
- DeepSeek：波形摘要、读取辨识结果、启动真实辨识任务、检索专业知识库；调用失败显式降级。
- 数据/结果按随机会话隔离；数据默认 1 小时过期；任务单工作线程排队；上传 32 MB / 350000 行上限；生产环境强制应用口令。

## Buck 公式知识层

[完整公式手册](backend/knowledge_base/FORMULAS.md)包含 24 个主题，每项注明适用条件、符号和来源。依据本地 PLECS 参数扫描原模型、Case16A/B 和独立 Case17 的连接与参数整理，[模型证据](backend/knowledge_base/model_evidence.json)保留源文件 SHA-256、元件参数和连接记录。未执行新的 PLECS 仿真。

- 功率级：开关分段方程、CCM/DCM、负载跳变、纹波与选值、RMS 应力、寄生与损耗、平均状态空间、小信号传递函数。
- 控制级：实际 R1～R4、C2/C3 与有限增益运放的节点方程、完整四状态方程、补偿器传函及零极点、PWM/限幅、直流目标、含反馈取电的闭环传函。
- 辨识与验证：L/C/R 积分回归、可辨识性、噪声偏差和时间加权误差。离散控制与硬件热模型明确标为扩展知识。

原电路的输出理论目标约为 **15.2128 V**（理想 CCM、未限幅），12 V 是电容初值；参考元件值不是任意上传 CSV 的参数真值。

借鉴 [PE-GPT](https://github.com/XinzeLee/PE-GPT) 的文档分块、检索、工具问答结构，本项目使用本地中英文 BM25 检索，无需向量数据库或额外 embedding API。DeepSeek 可通过 `search_knowledge(query)` 查询具体主题；未配置 Key 时，公式问题也能直接显示相关条目，已有 CSV 不会覆盖公式回答。仅询问公式或原理不会启动辨识；明确要求“先辨识参数，再解释公式”时，会在已进入辨识模式且数据配置有效后执行。

可直接提问：“推导 R1、R2、R3、R4、C2、C3 控制电路的节点方程”“模拟补偿器传递函数和零极点是什么？”“Buck 的 CCM/DCM 临界条件是什么？”“LCR 积分辨识公式及适用条件是什么？”

维护时修改 `backend/knowledge_base/buck.py`，再运行：

```powershell
.\.venv\Scripts\python.exe tests/build_formula_reference.py
.\.venv\Scripts\python.exe -m pytest -q
```

仅当本地 PLECS 源模型有变化时，重新核对电路并运行 `tests/build_plecs_evidence.py` 更新证据；它只读取模型文本，不执行初始化命令。知识层随后端部署更新，上传 GitHub 后需要等待 Render 重新部署。

## 计算边界

当前是**理想非同步 Buck**，R 表示**恒定等效负载电阻**，不表示电容 ESR 或绕组电阻。默认模型忽略反馈取样支路、死区、器件压降及寄生，与你 Case15/16 的完整闭环算法不是同一验证协议。积分回归是新实现的轻量基线，PANN 是上游代码的新适配；没有把旧实验数值冒充新结果。

选择恒定负载且动态足够的区间。跨负载突变的记录可能给出“需复核”；不要把输出参数直接视为真实元件值。可辨识性和 5% 归一化波形误差阈值只作工程筛查，并非统计置信度。含噪情况下积分回归也可能有偏，需要另做噪声、多初值及独立工况验证。

当前验证属于**同一记录后段留出、实测 PWM 驱动**，不是自主闭环或独立工况验证。未提供独立真值，不计算参数真实误差。没有实现真实硬件剩余寿命预测；寿命问答说明需要的型号、热条件、工况与适用厂商模型。

服务使用内存暂存数据/任务，重启或休眠后需重新上传；必须保持 `--workers 1`，多实例部署需要引入共享存储和任务队列。当前适合个人课题与受控演示。

## 可选 PANN

默认 Render 只安装轻量依赖。若需要 PANN，在具备足够内存的后端环境运行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-pann.txt
```

重启后端，页面会启用 PANN 选项。它固定 Vin、使用正值参数和训练测量窗口初值；当前未接入原 Case15/16 的联合初始状态辨识和反馈支路，具体说明见 `backend/pann_adapter.py`。

这台电脑已有 `PANN-main/.venv`，本地 `.env` 已用 `PANN_PYTHON` 指向该解释器。后端通过隐藏子进程调用原有 PyTorch 环境，无需改动其依赖；不传递 DeepSeek Key 或应用口令。若迁移电脑，更新或清空这个本地路径。Render 不设置该变量，可选安装 torch 后在服务进程内运行。

## 项目结构

```text
frontend/                 GitHub Pages 发布内容
backend/main.py           API、会话、任务、生产鉴权
backend/data.py           CSV 与时间加权统计
backend/identification.py 积分辨识、前向验证
backend/pann_adapter.py   上游 PANN 的新适配层
backend/vendor/           保留作者说明的上游模型
backend/chat.py           DeepSeek 受控工具调用
backend/task_router.py    独立任务分类 Agent、规则降级、任务提示词与工具权限
frontend/history.js       浏览器会话归档、切换与 CSV 关联恢复
backend/knowledge.py      可检查的检索资料与来源
backend/knowledge_base/   功率/控制公式、完整手册与 PLECS 证据
examples/                 真实 PLECS CSV 与来源记录
tests/                    数值、数据泄漏、API 与隔离测试
render.yaml               Render 部署配置
.github/workflows/        Pages 发布与后端测试
reference/                本地解压的 PE-GPT 参考源码，不提交
```

## 测试

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
node --check frontend/app.js
```

来源与许可证见 `THIRD_PARTY_NOTICES.md` 和 `LICENSE`。部署步骤参照 [GitHub Pages 官方文档](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages) 和 [Render FastAPI 文档](https://render.com/docs/deploy-fastapi)。
