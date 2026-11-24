# amax 个人工作台部署步骤

下面以 amax 账号 `xuzhefeng` 为例。工作台和模型推理都在 amax 上运行。Windows 负责建立 SSH 转发和打开网页。你的源代码、编排、队列和结果保存在自己的账号下。

## 1. 在 GitHub Fork 仓库

打开 [dataflow-flow-studio](https://github.com/yhl826459/dataflow-flow-studio)，点击右上角的 **Fork**，把仓库复制到自己的 GitHub 账号下。下面克隆时填的是 **GitHub 用户名**，与 amax 登录名可以不同。

## 2. 在 amax 克隆并启动工作台

在本机终端登录 amax。

```powershell
ssh -p 2207 xuzhefeng@hduiipl.orz2333.fun
```

登录后执行下面这段 Bash 命令。终端会提示输入你的 GitHub 用户名。

```bash
read -rp '你的 GitHub 用户名: ' GITHUB_USER
git clone "https://github.com/${GITHUB_USER}/dataflow-flow-studio.git" "$HOME/dataflow-flow-studio" &&
cd "$HOME/dataflow-flow-studio" &&
bash flow-studio/install-amax.sh
```

- `~/dataflow-flow-studio/` 是你自己的 Git 源码目录，开发和提交都在这里。
- `~/DataFlow/` 是个人运行目录。安装脚本把当前源码同步到这里，安装基础依赖并启动你的 systemd 用户服务。

安装后检查服务器上的工作台服务和访问入口。

```bash
systemctl --user status flow-studio.service --no-pager
curl -fsS --unix-socket "$HOME/DataFlow/flow-studio/.local/workbench.sock" http://localhost/api/bridge
printf '\n'
```

服务通常应显示 `active (running)`，接口返回 `"connected": true` 和你自己的输出根目录。

## 3. 复用 amax 上已有的模型权重

**先检查已有配置。** 工作台会按下面的路径查找模型和运行环境。若这些检查都显示 `OK`，可以直接用已有文件。

```bash
check_path() {
    if test "$1" "$2"; then
        printf 'OK       %s\n' "$2"
    else
        printf '缺少或无法读取 %s\n' "$2"
    fi
}

check_path -x "$HOME/qwen38-fp8/.venv/bin/python"
check_path -r "$HOME/qwen38-fp8/env.sh"
check_path -r "$HOME/qwen38-fp8/experiment.py"
check_path -r "$HOME/qwen38-fp8/experiment_hands.py"
check_path -r "$HOME/qwen38-fp8/model_path.txt"
if test -r "$HOME/qwen38-fp8/model_path.txt"; then
    check_path -d "$(cat "$HOME/qwen38-fp8/model_path.txt")"
fi
check_path -x "$HOME/.venv-genception14b/bin/python"
check_path -d "$HOME/genception14b_dexycb_141754_handonly"
check_path -r "$HOME/pipe_14b_v2.pkl"
check_path -r "$HOME/umt5-xxl-encoder-bf16-dataflow/config.json"
```

如果权重已经在 amax 的其他目录里，可以引用你有读取权限的现成路径。

- **Qwen3.8-27B-FP8**：让你自己的 `~/qwen38-fp8/model_path.txt` 指向已有权重目录的**绝对路径**。保留你原来的文件备份后再调整。
- **GenCeption 14B**：默认从 `~/pipe_14b_v2.pkl` 找权重。如果这个入口还没有，可以把已有权重文件链接过去。
- **UMT5-XXL**：默认从 `~/umt5-xxl-encoder-bf16-dataflow/` 找权重。如果这个入口还没有，可以链接到已有模型目录。

后面两条命令会提示你输入已有权重的位置。如果默认位置已经存在，`ln` 会报错并保留原文件。

```bash
read -erp 'GenCeption 权重 .pkl 的绝对路径: ' GENCEPTION
[[ "$GENCEPTION" == /* && -f "$GENCEPTION" && -r "$GENCEPTION" ]] &&
    ln -sT -- "$GENCEPTION" "$HOME/pipe_14b_v2.pkl"

read -erp 'UMT5 模型目录的绝对路径: ' UMT5
[[ "$UMT5" == /* && -r "$UMT5/config.json" ]] &&
    ln -sT -- "$UMT5" "$HOME/umt5-xxl-encoder-bf16-dataflow"
```

权重可以复用。Qwen、GenCeption 的运行脚本和 Python 环境需要在你的账号下可用，方便你独立修改和测试。**ABS 评分另走 API**，启用它时需要你自己的 `~/scoring_scripts/abs/` 和服务密钥配置。配置步骤见 [ABS 和 Agent 使用说明](../flow-studio/LOCAL.md#让-dataflow-agent-帮写代码)。

## 4. Windows 配置 SSH，再打开界面

在 Windows PowerShell 检查 Python 3 和 OpenSSH 是否可用。

```powershell
python --version
ssh -V
```

打开自己的 SSH 配置文件。

```powershell
$sshDir = Join-Path $env:USERPROFILE '.ssh'
New-Item -ItemType Directory -Path $sshDir -Force | Out-Null
$sshConfig = Join-Path $sshDir 'config'
if (-not (Test-Path -LiteralPath $sshConfig)) {
    New-Item -ItemType File -Path $sshConfig | Out-Null
}
notepad.exe $sshConfig
```

在文件里添加下面这段。如果已经有 `Host amax-workbench`，更新那一段即可。

```sshconfig
Host amax-workbench
    HostName hduiipl.orz2333.fun
    Port 2207
    User xuzhefeng
```

保存后可在 PowerShell 试一次 `ssh amax-workbench`，确认登录的是自己的账号。确认后输入 `exit` 退出测试连接。

接下来在 Windows 下载并解压 [仓库 ZIP](https://github.com/yhl826459/dataflow-flow-studio/archive/refs/heads/main.zip)，双击解压目录里的 `open-flow-studio.cmd`。转发就绪后会自动打开 [自己的工作台](http://127.0.0.1:18766/)，使用时保留终端窗口。

## 5. 提交第一条任务

1. 在工作台打开「选择数据集」或「启动任务 → 服务器视频分割」，选择找手或找物。
2. 数据目录可选已登记的共享目录，也可以使用自己的数据目录。先选一条短序列，核对 RGB 素材和运行条件。
3. 先验证提词和分割。启用 ABS 和质量门控的编排，还需要先配好自己的评分接口。
4. 到「运行监控」查看算子进度，到「数据排队」看序列顺序。结果按目标保存到你的 `~/DataFlow/dataflow_output/hands/` 或 `~/DataFlow/dataflow_output/objects/`。

目前 amax 的视频流程会用到两张共享显卡。两边可以同时改代码和编排，GPU 推理任务请错开。改动验证完成后，推到自己的 Fork 再提 PR。 [编排、算子和 PR 说明](../WORKBENCH.md)
