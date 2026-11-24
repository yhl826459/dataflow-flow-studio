# DataFlow Flow Studio

用于编排和运行视频分割流水线的工作台。支持算子编辑、预设任务、数据队列、逐段进度、ABS 断点续跑和整段视频掩码融合。

| 目录 | 内容 |
| --- | --- |
| [flow-studio/dist](flow-studio/dist) | 可视化编排、运行监控和数据排队页面 |
| [flow-studio](flow-studio) | HTTP 服务、数据集接入、队列、算子 Agent 接入和掩码融合 |
| [extensions/qwen_gencep](extensions/qwen_gencep) | Qwen 提词、GenCeption 分割、ABS 评分、提示词改写和工作台批处理 |

源码来自 amax 当前部署。运行环境依赖 [OpenDCAI/DataFlow](https://github.com/OpenDCAI/DataFlow)，导出时服务器使用的上游提交为 `316322e22db0556260b7f15bd84c39eaa8727a35`。算子 Agent 使用上游 Writer 提交 `8f78a389e778bd7aee8d35b8a5f1fc136cdd3451`，工作台提供模型连接适配。

## 在 amax 使用

把 `flow-studio/` 和 `extensions/qwen_gencep/` 放到服务器现有 DataFlow 工作目录的对应位置。仓库中的 systemd 用户服务按 amax 的实际部署路径配置，迁到别处时要同步调整服务文件和 Windows 启动脚本里的路径。模型、Python 环境、ABS 后端及数据集路径见[使用手册](flow-studio/LOCAL.md)和[流水线说明](extensions/qwen_gencep/README.md)。

Windows 上打开 [open-amax.cmd](flow-studio/open-amax.cmd) 会使用 SSH 别名 `5090` 连接 amax。工作台服务从服务器用户的本地环境文件读取 ABS 密钥。API 反向转发由 Windows 启动入口按需建立，服务器上的转发策略由 systemd 用户服务配置。

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
