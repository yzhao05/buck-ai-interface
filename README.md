# Buck Lab · AI 实验工作台

面向 ST4001 的 Buck 变换器分析原型：PLECS CSV → 波形检查 → L/C/R 辨识 → 留出验证 → DeepSeek 对话。采用 **GitHub Pages 静态前端 + Render FastAPI 后端**，本地也可以一条命令启动。

## 本地运行

在本文件所在文件夹双击 `启动界面.cmd`，保持窗口打开，然后浏览器访问：

**http://127.0.0.1:8000**

也可以在 PowerShell 运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\start-local.ps1
```

默认以中间对话区为主。左侧“导入波形”和“参数辨识”各有折叠/展开按钮；参数辨识默认仅显示“进入参数辨识”。点击进入后，左侧展开基本信息，右侧显示波形与辨识结果；退出时保留数据、配置和结果。

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

## 从零部署 GitHub Pages + Render

**只将 `pe-ai-interface` 文件夹作为新仓库根目录；不要上传整个 ST4001、虚拟环境、reference 或 .env。**

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
7. 打开 Pages 地址，点“连接设置”，输入 Render 中的 `APP_ACCESS_TOKEN`。这是你的应用口令，不是 DeepSeek Key。浏览器仅在当前页面内存保存口令，刷新后需要重新输入。
8. 加载示例、辨识、提问并导出报告，确认整条链路。

也可直接在页面“连接设置”填写后端地址；地址保存在当前浏览器中，会覆盖 config.js 的默认地址。换后端前先清除当前数据。Render 的资源配额或休眠可能导致首次请求等待，页面会显示连接/超时状态。

## 已实现

- 中文工作台：拖放上传、列映射、单位选择、波形显示、框选缩放、模型对比、JSON 报告导出。
- CSV：UTF-8/GB18030、逗号/分号/Tab；检查列、有限数值、时间顺序与 PWM；重复时间保留事件后的最后一行。
- 时间加权平均值/RMS；显示数据按包络抽样，计算使用完整选定区间。
- 积分辨识：L/C/R 联合拟合，或固定负载 R 的 L/C 拟合；非均匀采样；排除电感辨识中的零电流关断区间；正值边界与条件数检查。
- 验证：按时间分训练/留出。使用实测 PWM，从所选区间起点连续前向仿真，后段不重置到测量状态。
- 可选 PANN：使用压缩包里的物理递归网络，训练段积分估计初始化，再用波形损失微调。
- DeepSeek：波形摘要、读取辨识结果、启动真实辨识任务、检索小型专业知识库；调用失败显式降级。
- 数据/结果按随机会话隔离；数据默认 1 小时过期；任务单工作线程排队；上传 32 MB / 350000 行上限；生产环境强制应用口令。

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
backend/knowledge.py      可检查的检索资料与来源
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
