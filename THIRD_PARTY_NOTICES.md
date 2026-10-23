# 来源与改编说明

## Markdown 显示

`frontend/vendor/markdown-it-15.0.3.min.js` 为 npm 发布包 `markdown-it@15.0.3` 中的浏览器 UMD 构建，源代码项目：https://github.com/markdown-it/markdown-it 。MIT 许可证保存在同目录 `markdown-it-LICENSE.txt`，随包依赖的许可证见 `markdown-it-DEPENDENCY-LICENSES.txt`。本地随 GitHub Pages 发布，不在运行时请求第三方 CDN；本项目适配代码位于 `frontend/markdown.js`。

## PDF 字体

`backend/assets/fonts/BuckReportSans-Regular.ttf` 由 Google Fonts 提供的 Noto Sans SC 可变字体固定到 `wght=400` 后生成，并将字体名称改为 Buck Report Sans。来源：`https://github.com/google/fonts/tree/main/ofl/notosanssc`，上游文件 `NotoSansSC[wght].ttf`。版权为 Adobe，使用 SIL Open Font License 1.1；完整许可证保存在同目录 `OFL-NotoSansSC.txt`。报告嵌入所用字形子集，不依赖读者电脑安装中文字体。

本项目的界面、数据接入、积分辨识、验证、DeepSeek 工具调用和部署代码为此次新实现。

## PE-GPT

- 上游：https://github.com/XinzeLee/PE-GPT
- 来源：用户提供的 `PE-GPT-main.zip`。
- 保留的上游源码：`backend/vendor/pann_net.py`，未修改，保留作者说明。
- 完整压缩包解压到本地 `reference/PE-GPT-main/`（不上传 GitHub）。
- 压缩包 SHA-256 见 `examples/provenance.json`。
- 上游许可原文保存在根目录 `LICENSE`，包含 AGPLv3 与其附加条款。
- 本项目在该许可下提供。部署时将 `SOURCE_URL` 设为实际提供完整对应源码的仓库地址，页面会显示源码链接。

## PANN 与现有 Buck 实验

PANN 论文：Temporal Modeling for Power Converters With Physics-in-Architecture Recurrent Neural Network，DOI 10.1109/TIE.2024.3352119。

`backend/pann_adapter.py` 为新的适配层，固定 Vin，训练正值 L/C/R 参数，使用训练段积分估计初始化，再按波形损失微调。默认实测 PWM 模式忽略反馈支路；已知控制器模式计入该支路并执行自主闭环验证。两种模式均不把旧 Case15/16 的误差数字视为新接口的验证结果。

`examples/plecs_buck_case16a.csv` 复制自 ST4001 的实际 PLECS 实验，未伪造或重新合成；来源及 SHA-256 见相邻 provenance.json。拟合器不读取文件名中的参数真值，也不读取 provenance.json。

`backend/controller.py` 的已知 RC 控制器方程与自主闭环离散模型改编自用户本地 `PANN-main/closed_loop_identification.py` 和 `closed_loop_buck.py`（Case09/10）。仅新增明确确认的控制器配置模式，固定用户输入的 R；原参考文件与上游 vendor 源码未修改。反馈支路修正由适配层实现，三列 CSV 的重建输入不冒充实测 PWM。
