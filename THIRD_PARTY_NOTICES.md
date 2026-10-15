# 来源与改编说明

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

`backend/pann_adapter.py` 为新的适配层，固定 Vin，训练正值 L/C/R 参数，使用训练段积分估计初始化，再按波形损失微调。不等同于用户现有 Case15/16 中的反馈支路建模、窗口初始状态联合估计或自主闭环评价。

`examples/plecs_buck_case16a.csv` 复制自 ST4001 的实际 PLECS 实验，未伪造或重新合成；来源及 SHA-256 见相邻 provenance.json。拟合器不读取文件名中的参数真值，也不读取 provenance.json。
