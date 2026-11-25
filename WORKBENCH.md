# Flow Studio 工作台使用指南

Flow Studio 用于编排和运行 RGB 视频里的手、物实例分割流水线。你可以拖动算子、选择服务器数据集、提交任务，也可以查看算子进度、处理队列和全视频掩码融合结果。

## 1. 在 amax 部署个人工作台

### 克隆和安装

两位开发者都克隆 `yhl826459/dataflow-flow-studio`。工作台各自在自己的 amax 账号下安装。下面以 `xuzhefeng` 为例。

```sh
ssh -p 2207 xuzhefeng@hduiipl.orz2333.fun
```

在 amax 终端克隆同一个 GitHub 仓库并安装。

```bash
git clone https://github.com/yhl826459/dataflow-flow-studio.git "$HOME/dataflow-flow-studio" &&
cd "$HOME/dataflow-flow-studio" &&
bash flow-studio/install-amax.sh
```

安装脚本部署到本账号的 `$HOME/DataFlow`，安装 DataFlow 和融合所用的 Python 依赖，并启动该账号的 systemd 用户服务。编排与草稿、算子代码快照和队列在该账号的工作台目录里，运行输出在该账号的 `DataFlow/dataflow_output/`。每个人操作自己的实例。

```bash
systemctl --user status flow-studio.service --no-pager
curl -fsS --unix-socket "$HOME/DataFlow/flow-studio/.local/workbench.sock" http://localhost/api/bridge
```

### 复用已有的模型权重

模型和运行环境都留在 amax。已有权重可以继续使用，配置时让个人账号指向自己有读取权限的模型路径。

| 用途 | 在 amax 上准备 |
| --- | --- |
| RGB 数据集 | 个人数据集默认在 `$HOME/datasets`。可通过用户服务的 `FLOW_STUDIO_DATASETS_ROOT` 指定自己有权访问的目录，或在界面下拉框选择已登记的共享数据集。 |
| Qwen 提词和改写 | `$HOME/qwen38-fp8/` 中放置 Qwen 运行脚本、Python 环境、`env.sh` 和指向可访问权重的 `model_path.txt`。 |
| GenCeption 和 UMT5 | 准备 `$HOME/.venv-genception14b/`、`$HOME/genception14b_dexycb_141754_handonly/`、`$HOME/pipe_14b_v2.pkl` 和 `$HOME/umt5-xxl-encoder-bf16-dataflow/`。 |
| ABS 评分 | 准备 `$HOME/scoring_scripts/abs/` 的评分程序和本人可用的 API 配置，在 `~/.config/flow-studio/agent.env` 设置配置指定的密钥变量。 |
| 算子 Agent（可选） | 有个人 API 配置后，在 `$HOME/DataFlow` 运行 `.venv/bin/python -B flow-studio/operator_agent.py --install "$PWD"` 安装 Writer。 |

若 GenCeption 或 UMT5 的已有权重放在其他目录，可以在 amax 建立软链接。默认位置已有文件时，下面的命令会保留原文件。

```bash
read -erp 'GenCeption 权重 .pkl 的绝对路径: ' GENCEPTION
[[ "$GENCEPTION" == /* && -f "$GENCEPTION" && -r "$GENCEPTION" ]] &&
    ln -sT -- "$GENCEPTION" "$HOME/pipe_14b_v2.pkl"

read -erp 'UMT5 模型目录的绝对路径: ' UMT5
[[ "$UMT5" == /* && -r "$UMT5/config.json" ]] &&
    ln -sT -- "$UMT5" "$HOME/umt5-xxl-encoder-bf16-dataflow"
```

用「选择数据集」先做输入预检，再提交一条短序列测试。个人环境配置变化后，用 `systemctl --user restart flow-studio.service` 重启自己的服务。

两套应用仍共用 amax 的物理显卡。当前视频流程会用到同一对 GPU，正式推理需错开执行。

## 2. 通过 SSH 转发访问界面

### 打开自己的工作台

在本机终端执行下面的命令。命令中的账号和套接字路径对应 `xuzhefeng` 的个人部署。

```sh
ssh -N -o ExitOnForwardFailure=yes -p 2207 -L 127.0.0.1:18766:/home/xuzhefeng/DataFlow/flow-studio/.local/workbench.sock xuzhefeng@hduiipl.orz2333.fun
```

保留终端窗口，然后在浏览器打开 [自己的工作台](http://127.0.0.1:18766/)。可在同一页面编辑编排和算子、提交任务并查看监控。代码、模型、数据和任务状态都保存在 amax 上。

### 查看授权共享的只读监控

已开通只读权限时，可以用自己的 amax 账号建立另一个转发，再打开 [共享只读工作台](http://127.0.0.1:18768/)。

```sh
ssh -N -o ExitOnForwardFailure=yes -p 2207 -L 127.0.0.1:18768:/tmp/flow-studio-yanghonglei-monitor/monitor.sock xuzhefeng@hduiipl.orz2333.fun
```

**运行监控**显示当前算子、执行路径和失败摘要。**数据排队**显示正在处理的序列和待执行顺序。展开序列可查看分段进度和待复核的融合接缝。

### 通过 PR 共享改动

接受 GitHub 协作者邀请后，用自己的 GitHub 账号把改动推到本仓库的功能分支。若是在网页里试改算子，先把验证过的代码整理回自己克隆的源码目录，再提交。编排草稿、API 配置、数据集和模型输出留在各自的 amax 环境。

```bash
cd "$HOME/dataflow-flow-studio"
git switch main && git pull --ff-only && git switch -c "work/$(id -un)/update-operator"
# 修改并验证后，只暂存要分享的源文件
git add -p
git commit -m "Update video operator"
git push -u origin HEAD
```

在 GitHub 向 `yhl826459/dataflow-flow-studio` 的 `main` 提交 PR，由仓库所有者 `yhl826459` 审阅并决定是否合并。合并后，各自选择何时拉取并用安装脚本更新自己的工作台。

## 3. 认识视频分割算子

| 算子 | 输入与输出 | 调用模型 |
| --- | --- | --- |
| **视频输入** | 读取 RGB 视频及目标任务命令，按序列准备片段，交给下游提词。 | 未调用模型 |
| **Qwen 提词** | 对视频抽帧，识别目标并为每个目标输出一个实例 ID 和描述短语。服务器按 5 fps 抽样。 | Qwen3.8-27B-FP8 |
| **中文目标描述** | 在手动指定目标的编排中，把启动任务时填写的中文译成英文短语。同一次提交复用同一份译文。 | 工作台配置的模型 API |
| **GenCeption 分割** | 读取视频片段和目标短语，为每个目标分别输出逐帧二值掩码。 | GenCeption 14B，目标短语由 UMT5-XXL 编码 |
| **ABS 评分** | 在采样帧上评估各实例的局部掩码质量，输出每个实例的分数和评分状态。默认每 6 帧采样。 | 通过 API 调用 gpt-6.2-sol |
| **质量门控** | 根据分数选择通过、重试或待复核。默认通过条件是分数严格大于 7，每个实例最多重试 2 次。 | 未调用模型 |
| **提示词改写** | 利用低分帧、掩码和已有描述，改写同一目标的提示词，再交给分割算子。 | Qwen3.8-27B-FP8 |
| **全视频掩码融合** | 对齐相邻片段的共享帧，为同一实例保留统一轨迹 ID，并输出整条序列的掩码。匹配不确定的接缝会标成待复核。 | 未调用模型 |
| **结果输出** | 汇总本次任务的掩码路径、状态和运行结果。 | 未调用模型 |

工作台也提供 **Python 记录算子**、**数据转换**和**记录筛选**，用于读取或写入 JSONL 记录。Python 记录算子调用的模型由其代码决定，内置转换和筛选无需模型。

## 4. 提交任务和查看结果

### 启动服务器任务

1. 在已连接 amax 的手物分割工作台点「启动任务」，核对执行方式为「服务器视频分割」。也可以选择演练来检查路径和分支。
2. 选择「找手」或「找物」，再选服务器数据集和序列。工作台会检查可用的 RGB 输入和处理范围。
3. 可以只跑一个片段，也可以选择整条序列。多选时将序列加入处理队列，核对顺序和输出目录后确认启动。
4. 常用组合可以保存为「预设任务」。之后从「启动任务」进入「使用预设任务」，核对目标、序列和参数后加入队列。启动时使用当前已保存的编排和算子版本。

使用「中文目标描述」时，在启动窗口的「要分割什么？」填写目标，例如「右手拿着的黑色笔记本」。画布上，视频输入接 GenCeption 的「RGB 视频」端口，中文目标描述接「目标描述」端口。找手或找物决定交付目录。

对支持手动清单的编排，每行 JSONL 对应一条视频记录，`task` 应与启动窗口选定的目标一致。下面是格式示例。

```json
{"sequence_id":"example-sequence","video_path":"/path/to/rgb.mkv","task":"objects"}
```

### 理解队列和分段

- **序列之间逐条排队。** 同一批次有多条序列时，服务器跑完当前序列再准备下一条。
- **序列内部按窗口处理。** 工作台使用 30 fps 时间轴，每个窗口最多 81 帧。只有一段时直接跑完片段流程。有多段时，首段先跑完，余下片段按算子分批推进。
- **融合在序列末尾执行。** 含融合算子的编排默认让相邻窗口共享 8 帧。目标达到改词上限仍未通过时，融合算子采用有效 ABS 均分最高轮次的掩码和实际分割提示词参与匹配。「数据排队」标出需复核的接缝、低分回退和全视频无掩码的帧号。
- **序列交付按整段和轮次保存。** 在手物分割的序列输出目录查看 `mask_preview.mp4`（全视频 RGB 与掩码对照）、`track_*.npy`（逐轨迹全视频掩码）和 `result.json`。在 `segments/part_*/round_*/` 查看该轮的 `prompts.json`、`abs_scores.json` 及逐目标的 RGB、掩码、叠加三视图。隐藏的 `.run/` 保存运行中间件和检查点。

### 查看进度和处理失败

运行监控里的「首段」和「其余片段」对应多段任务的两个执行阶段。队列里可以展开每条序列查看片段进度。拥有编辑权限的账号可以调整仍在等待的条目。

在「运行监控」选择运行，可以按片段、轮次和目标展开 ABS 采样分数及均分。完成全视频交付后，可在任务详情打开 RGB 与融合掩码的对照视频。

如果初轮 ABS 因暂时性的 API 问题失败，且任务详情显示「继续执行」，可以从原检查点续跑。符合条件的续跑会复用已经保存的提词和分割成果，沿用当时选择的目标与流水线。

## 5. 编辑流水线和算子

1. 在「编排设计」中，从算子库拖入已有算子，连接输入、输出和分支。
2. 点击算子或画布节点，可以查看输入、输出、处理逻辑和模型，并编辑节点说明或参数。支持自定义代码的节点可以直接编辑 Python。
3. 保存后会按设置进行编排审查。可以取消本次审查，也可以在关闭自动审查后手动检查。
4. 要让 Agent 辅助编写算子，在编辑器打开「Agent 设计」，描述期望的输入、处理规则和输出。生成的 Python 草案可以先核对、检查，再保存到算子库或当前编排。

「说明文档」用于维护当前流水线说明，也可插入本机图片。左侧「GPU 占用」会显示服务器的显存和利用率。

更详细的连接、配置和排错步骤见 [操作手册](flow-studio/LOCAL.md) 和 [流水线扩展说明](extensions/qwen_gencep/README.md)。
