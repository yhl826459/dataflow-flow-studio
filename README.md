# DataFlow Flow Studio

用于编排和运行视频分割流水线的工作台。支持算子编辑、预设任务、数据队列、逐段进度、ABS 断点续跑和整段视频掩码融合。

[工作台使用指南](WORKBENCH.md)涵盖 [Windows 总入口](open-flow-studio.cmd)、个人部署、算子介绍、任务提交和运行排队。[独立开发与 PR](WORKBENCH.md#2-独立部署和协作)说明如何在自己的 amax 账号下部署和验证改动。

| 目录 | 内容 |
| --- | --- |
| [flow-studio/dist](flow-studio/dist) | 可视化编排、运行监控和数据排队页面 |
| [flow-studio](flow-studio) | HTTP 服务、数据集接入、队列、算子 Agent 接入和掩码融合 |
| [extensions/qwen_gencep](extensions/qwen_gencep) | Qwen 提词、GenCeption 分割、ABS 评分、提示词改写和工作台批处理 |

源码来自 amax 当前部署。运行环境依赖 [OpenDCAI/DataFlow](https://github.com/OpenDCAI/DataFlow)，导出时服务器使用的上游提交为 `316322e22db0556260b7f15bd84c39eaa8727a35`。算子 Agent 使用上游 Writer 提交 `8f78a389e778bd7aee8d35b8a5f1fc136cdd3451`，工作台提供模型连接适配。

## 在 amax 使用

以自己的 amax 账号克隆代码后，运行 `bash flow-studio/install-amax.sh`，将当前分支部署到自己的 `$HOME/DataFlow`。工作台服务、编排、队列、模型接口配置和运行产物由各自的账号管理。模型、Python 环境、ABS 后端及数据集路径见[使用手册](flow-studio/LOCAL.md)和[流水线说明](extensions/qwen_gencep/README.md)。

Windows 双击 [open-flow-studio.cmd](open-flow-studio.cmd)，通过自己的 SSH 别名 `amax-workbench` 打开完整工作台。已有 `5090` 配置的原部署也可沿用。工作台服务从各账号的本地环境文件读取 ABS 密钥；需要 API 反向转发时，为个人服务单独配置。

需要反向转发时，在 amax 执行 `systemctl --user edit flow-studio`，填写以下配置。将占位地址替换成 ABS 配置中接口的 HTTP 协议、主机和端口，省略 `/v1` 等路径，并核对 Windows 启动脚本的转发目标。

```ini
[Service]
Environment=FLOW_STUDIO_ABS_HTTP_PROXY=http://127.0.0.1:18767
Environment=FLOW_STUDIO_ABS_PROXY_ORIGIN=http://api-host:12333
```

## 源码与运行数据

仓库只保存源码、服务模板、测试和说明。账号配置、API 密钥、服务器运行环境、数据集、模型权重、工作台保存状态以及运行结果由部署环境管理。更新部署时只同步明确修改过的源码文件，保留服务器现有的 `flow-studio/.local/`。

在仓库根目录可运行离线测试：

```sh
python -B -m unittest discover -s flow-studio
node --test flow-studio/test_review_logic.mjs
```
