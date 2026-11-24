# Qwen 提词与 GenCeption 视频分割

这是 DataFlow 的视频扩展，默认运行提词和分割两个算子，可接入 ABS 评分算子。**每条记录对应一个视频及相机，每个短语对应一个独立掩码。**

## 怎么运行

在 amax 上以 yanghonglei 执行。示例选用一段 DexYCB 找手视频。

    cd /data/yanghonglei/DataFlow
    .venv/bin/python -B extensions/qwen_gencep/pipeline.py \
      --input extensions/qwen_gencep/example_dexycb.jsonl \
      --out "/data/yanghonglei/dexycb/_dataflow/$(date +%Y%m%d_%H%M%S)_hands" \
      --gpus 0,1

输入为 UTF-8 JSONL，每行一条 **sequence_id / video_path / task** 记录。推荐用“序列名-相机号”作为唯一 ID。相对视频路径以输入清单为基准。任务值 hands 为找交互手，objects 为找与手交互的物体，省略时使用 objects。当前接受一个视频流、30 fps、1–81 帧、宽高均为偶数的已裁短视频。

主进程使用 DataFlow 的 .venv。Qwen 子进程使用 ~/qwen38-fp8，读其 model_path.txt 指向的 FP8 权重。GenCeption 子进程使用 ~/.venv-genception14b 和已有 14B 分阶段运行脚本，默认权重为 ~/pipe_14b_v2.pkl。UMT5-XXL BF16 编码在 CPU 上执行，默认本机权重目录 ~/umt5-xxl-encoder-bf16-dataflow，可用 --encoder-model 和 --encoder-python 指定。Qwen 和 GenCeption 的主模型分别占用两张卡，串行运行。GenCeption 的 VAE 编码和解码各使用一张卡。

## DataFlow 如何串起这些步骤

DataFlow 提供 OperatorABC、OPERATOR_REGISTRY、PipelineABC 和 FileStorage，负责注册、检查输入输出字段、按顺序执行以及保存每步 JSONL。这条流程的三个模型任务由本扩展的自定义算子执行。

1. **QwenVideoPhraseGenerator** 读取 video_path 和 task。原脚本按 5 fps 抽帧，把有序帧一起送给 Qwen，完成生成与自复核。原始结果留在序列下的 qwen/result.json，供追溯。算子将最终候选提取为 targets 列表，每项只有 id 和 phrase。所有最终候选按顺序传递，包括仍可能有歧义的候选。
2. **GenCeptionVideoSegmenter** 读取同一个 video_path 和 targets。每条短语用已有 RefVOS 模板和 UMT5-XXL 编码。视频用完整的 RGB 帧生成一次 VAE latent，每个短语都在此 latent 上独立推理。掩码裁掉补帧，以最近邻恢复到原始尺寸。每个目标输出一个 [T,H,W] 的 uint8 二值 .npy 和一个 30 fps 的预览 MP4，三栏依次是 RGB、黑底掩码和叠加。短语列表为空时记录 no_targets 并跳过分割。

两个算子继承 OperatorABC 并通过 OPERATOR_REGISTRY 注册。VideoSegmentationPipeline.forward() 用 FileStorage.step() 声明每步的数据入口。compile() 记录调用顺序并检查 input_*/output_* 声明的列，之后 forward() 按顺序执行。FileStorage 将每步表格保存为 JSONL，视频和掩码用路径引用。ABS 按同一行的 RGB、短语和 mask 配对，可直接从 dataflow_step2.jsonl 启动。

## 输出和排错

- input.jsonl 是本次标准化的输入清单，带视频帧数和尺寸。
- dataflow_step1.jsonl 是短语清单和 Qwen 结果路径。
- dataflow_step2.jsonl 是最终清单。每个 segments 项包含原目标 ID、短语、掩码路径、预览路径和有前景的帧数。
- 每个序列子目录包含双模型的结果与日志。GenCeption 的 targets.json 和 embeddings.npz 记录短语交接，阶段日志分别为 prepare、encode、forward、decode 和 export。
- 输出必须是家目录下的新目录。子进程失败后可检查报错所指日志。已完成的阶段文件会留在本次目录。

### 编码器在另一台服务器时

运行时加 --encoder-handoff。GenCeption 算子为每个非空序列发布 genception/targets.json，并等待同目录的 embeddings.npz，时限 15 分钟。把本目录的 embed_phrases.py 和这份新 targets.json 传到有 UMT5-XXL 的机器，在兼容环境运行下面的命令。

    CUDA_VISIBLE_DEVICES= HF_HUB_OFFLINE=1 OMP_NUM_THREADS=8 MKL_NUM_THREADS=8 \
      python -B embed_phrases.py --model /path/to/umt5-xxl \
      --request targets.json --out embeddings.npz

传回时先用临时文件名，传完再重命名为 embeddings.npz。GenCeption 会核对归档中的目标 ID、短语、顺序和 embedding 形状。

## 接入服务器 ABS 局部绝对评分

ABS 入口、依赖和 API 配置统一由 /data/yanghonglei/scoring_scripts/abs/ 提供。

在完整流水线命令后加 --abs，就会在 GenCeption 后运行第三个算子 GenCeptionAbsoluteScorer，最终清单为 dataflow_step3.jsonl。默认仍运行提词和分割，可用 --abs-prepare-only 启动第三算子的离线准备。

只评已有的 DataFlow 分割产物时，可以跳过两种 GPU 推理，也不需要 UMT5 编码器。

    cd /data/yanghonglei/DataFlow
    .venv/bin/python -B extensions/qwen_gencep/pipeline.py \
      --segments /data/yanghonglei/dexycb/_dataflow/20261120_hands_smoke/dataflow_step2.jsonl \
      --abs --out "/data/yanghonglei/dexycb/_dataflow/$(date +%Y%m%d_%H%M%S)_abs"

--abs 使用桌面同一 OpenAI 兼容 API 的地址、超时及请求参数，指定模型 gpt-6.2-sol。启动前须在当前服务器 shell 设置 MODEL_API_KEY。桌面密钥不写入部署文件。--abs-prepare-only 可以在无密钥时验证输入。

每个实例的 .npy 以 FFV1 无损封装为灰度 MKV，保留 RGB 对应时间戳，按逐实例非零前景格式进入服务器的 local_score.py。Qwen 最终短语原文写入 instances.json，来源为 genception。ABS 沿用原协议默认 every=6（源视频 30 fps 时是 5 fps），可用 --abs-every 覆盖。沿用原前景并集框 ×1.5 裁剪、仅裁剪 RGB 判可评性，再用同框裁剪 RGB 和黑底掩码评分。可评且非空时返回 1–10 整数，原有 0 分和 N/A 规则保持。

服务器 ABS 沿用原协议的两条系统提示词、评分请求、回复校验及输出结构，并识别 genception 来源。每个序列的 Absolute Score/ 包含 scores.json、local_absolute_scoring_3fps.mkv 和 _run/，离线准备只创建 _run/。单独评分的清单为 abs_step1.jsonl。绝对评分结果文件里的分数来源键为 genception。

## 评分反馈与提示词重试

在原有 --abs 命令后追加 --refine 即可启用第四、第五个算子。--input 和 --segments 两种入口都可用。--refine-threshold 可覆盖默认阈值 7.0。整条流程沿用服务器 ABS 的 API 配置和 MODEL_API_KEY。

    cd /data/yanghonglei/DataFlow
    .venv/bin/python -B extensions/qwen_gencep/pipeline.py \
      --segments /data/yanghonglei/dexycb/_dataflow/20261120_hands_smoke/dataflow_step2.jsonl \
      --abs --refine --refine-threshold 7 \
      --encoder-handoff --out "/data/yanghonglei/dexycb/_dataflow/$(date +%Y%m%d_%H%M%S)_refine"

第四个算子 MaskQualityGate 按实例冻结首轮有分的原始帧号。均分严格大于阈值就直接交付这一轮掩码。均分低于或等于阈值时，按同一阈值筛出低分采样帧，只合并采样步长相邻的低分帧。一旦首轮有分的帧在后续轮次变为 N/A，该实例进入 needs_review，保留上一轮可比较的候选掩码。首轮没有任何分数的实例也进入 needs_review。

第五个算子 QwenPromptRefiner 把这些帧的原始 RGB、同帧黑底二值掩码、逐帧分数和上一轮分割短语交给 Qwen FP8。找物任务额外从本轮同一目标低分区间外、ABS 严格大于 7 的采样帧中选最多三张原始 RGB，按时间取首、最接近中点、尾帧。参考图只附帧号和时间。找物短语保留物体身份和可靠外观，可加一条有区分度的手部交互或位置关系，区分人物左右手与画面左右。新短语用于失败实例的整段重分割，ABS 继续使用首轮的目标短语和原动态裁剪。已达标实例沿用通过时的掩码，仍作为原 ABS 整组裁剪的输入。每个实例最多重试两轮。Qwen 会看到该实例历轮用过的分割短语。只改大小写、空格或标点也算重复，重复短语会提前转待复核。到达上限仍低分时，保留均分最高的可比较候选，同分保留最早一轮。

DataFlow 编译的单轮依旧按顺序运行。Python 调度器在需要时重新执行第二、第三个算子，再执行第四、第五个算子。每轮的中间清单和提示词证据在 refinement/round_XX/。最终 final_masks.jsonl 按序列列出所有实例的状态、历轮证据、retained 候选和 final_mask。final_mask 只在该实例达标时指向实际分割掩码文件。使用 --encoder-handoff 时，每一轮实际重分都要为新生成的 targets.json 提供 embeddings.npz。

用 --input、--abs、--refine 跑完整流程时，可加 --publish /data/yanghonglei/DataFlow/dataflow_output。
--out 继续保存流水线工作文件和恢复证据。--publish 下每个序列按 round_00、round_01、round_02
只保存已完成轮次的掩码预览、qwen_prompts.json 和原格式 abs_scores.json。序列根目录的
result.json 记录各轮可比均分、决策和保留轮次。保留掩码以 mask.npy 输出，只有达标时
result.json 中的 final_mask 才会引用它。多实例文件按实例顺序编号。

离线回归可从 DataFlow 仓库根运行：

    .venv/bin/python -B extensions/qwen_gencep/test_score_feedback.py

## 实时运行监控

常用 pipeline.py 命令会自动记录状态。页面显示运行记录、每轮的五个节点、正在处理的序列、阶段耗时和当前日志文件名。浏览器每两秒读取一次状态。运行进程中断时，当时正在执行的节点会标为中断。运行记录从接入监控后开始积累。

在服务器上启动只读监控。

    cd /data/yanghonglei/DataFlow
    .venv/bin/python -B extensions/qwen_gencep/watch_runs.py

在本机 PowerShell 启动 SSH 转发，再打开 http://127.0.0.1:8765/。

    ssh -NT -o ExitOnForwardFailure=yes -L 127.0.0.1:8765:/data/yanghonglei/.local/state/dataflow-qwen-gencep/viewer.sock 5090

服务器仅监听个人目录里的 Unix socket。状态文件保存在 /data/yanghonglei/.local/state/dataflow-qwen-gencep/runs/，页面只读取这些进度记录。
