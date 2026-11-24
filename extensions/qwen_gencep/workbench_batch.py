"""Phase-local model reuse and per-clip progress for a Workbench dataset run."""

import json
import os
from contextlib import ExitStack, contextmanager
from functools import wraps
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import pipeline as native
import score_feedback as feedback
from run_progress import CURRENT, operator_progress, report_work


def part_state(sequence, status, detail, log=None):
    progress = CURRENT.get()
    if not progress or progress.operator is None:
        return
    step = progress.operator
    part = step.setdefault("parts", {}).setdefault(str(sequence), {})
    part.update(status=status, detail=detail)
    if log:
        part["log"] = str(log)
    if status == "running":
        step.update(sequence=str(sequence), detail=detail, log=part.get("log"))
    step["worked"] = True
    progress.save()


def batch_process(command, log, *, cwd=None, env=None):
    current = None
    report_work(log=log)
    with log.open("xb") as output:
        with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, cwd=cwd, env=env) as process:
            for line in process.stdout:
                output.write(line)
                output.flush()
                if line.startswith(b"@@FLOW_PART "):
                    sequence, status, detail = json.loads(line[len(b"@@FLOW_PART "):])
                    current = sequence if status == "running" else None
                    part_state(sequence, status, detail, log)
            if process.wait():
                if current:
                    part_state(current, "failed", "执行失败", log)
                raise RuntimeError(f"模型进程退出（{process.returncode}）。日志：{log}")


def requests_file(out, stage, items):
    folder = out / ".workbench-runtime" / stage
    folder.mkdir(parents=True)
    path = folder / "requests.json"
    native.write_json(path, items)
    return path


def qwen_process(stage, requests):
    gpus = os.environ.get("FLOW_STUDIO_JOB_GPUS", "0,1")
    native.ensure_free_memory(gpus, 24800)
    model = Path((native.QWEN / "model_path.txt").read_text().strip()).resolve()
    paths = [str(native.HERE), str(native.QWEN), os.environ.get("PYTHONPATH", "")]
    env = dict(os.environ, CUDA_VISIBLE_DEVICES=gpus, CUDA_DEVICE_ORDER="PCI_BUS_ID",
               PYTHONPATH=os.pathsep.join(filter(None, paths)))
    batch_process(["bash", "-c", 'set -e; source ./env.sh; exec "$@"', "qwen",
                   str(native.QWEN / ".venv/bin/python"), "-B", str(native.HERE / "workbench_model_batch.py"),
                   stage, str(requests), str(model)], requests.parent / "qwen.log", env=env, cwd=native.QWEN)


def ready_parts(rows, sequence_key):
    progress = CURRENT.get()
    if progress and progress.operator is not None:
        progress.operator.setdefault("parts", {}).update({
            str(row[sequence_key]): {"status": "waiting"} for row in rows
        })
        progress.save()


def qwen_run(self, storage, *, input_sequence_key, input_video_key, input_task_key,
             input_video_info_key, output_targets_key, output_qwen_result_key):
    rows = storage.read("dict")
    ready_parts(rows, input_sequence_key)
    requests = []
    for row in rows:
        sequence = row[input_sequence_key]
        out = self.out / sequence
        out.mkdir()
        requests.append(dict(sequence=sequence, out=str(out / "qwen"), video=row[input_video_key],
                             task=row[input_task_key], fps=native.QWEN_FPS))
    if requests:
        qwen_process("qwen", requests_file(self.out, "qwen", requests))
    for row, item in zip(rows, requests, strict=True):
        result_path = Path(item["out"]) / "result.json"
        result = native.read_json(result_path)
        metadata = result["input"]
        if (Path(metadata["video"]).resolve() != Path(row[input_video_key]) or
                metadata["decoded_frames"] != row[input_video_info_key]["frames"] or
                metadata["requested_sampling_fps"] != native.QWEN_FPS):
            part_state(item["sequence"], "failed", "提词输出与视频或采样设置不一致")
            raise ValueError(f"Qwen result does not match the video or sampling: {result_path}")
        row[output_targets_key] = native.final_targets(result)
        row[output_qwen_result_key] = str(result_path)
    storage.write(rows)


def genception_process(requests, options):
    gpus, one = options["gpus"], options["gpus"].split(",")[0]
    paths = [str(native.GEN_RUNTIME), str(native.GEN_RUNTIME / "representations4d")]
    env = dict(os.environ, CUDA_DEVICE_ORDER="PCI_BUS_ID", XLA_PYTHON_CLIENT_PREALLOCATE="false",
               XLA_PYTHON_CLIENT_ALLOCATOR="cuda_async", XLA_CLIENT_MEM_FRACTION="0.739",
               JAX_COMPILATION_CACHE_DIR=str(requests.parent / "jax_cache"), PYTHONPATH=os.pathsep.join(paths))
    with tempfile.TemporaryDirectory(prefix="intermediate-", dir=requests.parent) as work:
        for phase, devices, memory in (("prepare", "", 0), ("encode", one, 11000),
                                        ("forward", gpus, 24800), ("decode", one, 11000), ("export", "", 0)):
            if devices:
                native.ensure_free_memory(devices, memory)
            batch_process([str(native.GEN_PYTHON), "-B", str(native.HERE / "workbench_model_batch.py"), phase,
                           str(requests), work], requests.parent / f"{phase}.log",
                          env=dict(env, CUDA_VISIBLE_DEVICES=devices))


def segment_run(self, storage, *, input_sequence_key, input_video_key, input_targets_key,
                input_video_info_key, output_segments_key, output_status_key):
    rows = storage.read("dict")
    ready_parts(rows, input_sequence_key)
    options = self.segment.keywords
    requests = []
    for row in rows:
        targets = row[input_targets_key]
        sequence = row[input_sequence_key]
        if not targets:
            row[output_segments_key], row[output_status_key] = [], "no_targets"
            part_state(sequence, "skipped", "无待分割目标")
            continue
        out = self.out / sequence / "genception"
        out.mkdir(parents=True)
        part_state(sequence, "running", "编码目标短语")
        request = out / "targets.json"
        native.write_json(request, targets)
        native.embed_targets(request, out / "embeddings.npz", encoder_model=options["encoder_model"],
                             encoder_python=options["encoder_python"], handoff=options["handoff"])
        item = dict(sequence=sequence, out=str(out), targets=targets, video=row[input_video_key],
                    video_info=row[input_video_info_key], checkpoint=str(options["checkpoint"]),
                    runtime=str(native.GEN_RUNTIME))
        native.write_json(out / "job.json", {key: item[key] for key in ("video", "video_info", "checkpoint", "runtime")})
        requests.append(item)
        part_state(sequence, "waiting", "目标短语编码完成")
    if requests:
        genception_process(requests_file(self.out, "genception", requests), options)
    by_sequence = {item["sequence"]: item for item in requests}
    for row in rows:
        item = by_sequence.get(row[input_sequence_key])
        if not item:
            continue
        segments = native.read_json(Path(item["out"]) / "result.json")
        if [{"id": mask["id"], "phrase": mask["phrase"]} for mask in segments] != item["targets"]:
            part_state(item["sequence"], "failed", "分割输出目标与输入不一致")
            raise ValueError(f"GenCeption output targets differ from Qwen output: {item['out']}")
        row[output_segments_key], row[output_status_key] = segments, "segmented"
    storage.write(rows)


def capture_refinement(original):
    """Use the installed refiner to build its requests and apply its checks."""
    @wraps(original)
    def run(self, storage, **keys):
        rows = storage.read("dict")
        sequence_key = keys["input_sequence_key"]
        ready_parts(rows, sequence_key)
        requests = []
        results = {}
        for row in rows:
            sequence = row[sequence_key]
            states = [state for state in row[keys["input_refinement_key"]] if state["status"] == "retry"]
            if not states:
                part_state(sequence, "skipped", "没有待改写目标")
                continue
            jobs = [{
                "id": state["id"], "scoring_phrase": state["scoring_phrase"],
                "previous_phrase": state["retained"]["segmentation_phrase"],
                "used_phrases": [attempt["segmentation_phrase"] for attempt in state["history"]],
                "task": row.get("task"),
                "context_rgb_frames": state["retained"].get("context_rgb_frames", []),
                "mask": state["retained"]["segment"]["mask"],
                "low_intervals": state["retained"]["low_intervals"],
            } for state in states]
            output = self.out / sequence / "qwen_refinement"
            output.mkdir(parents=True, exist_ok=False)
            request = dict(video=row[keys["input_video_key"]], video_info=row[keys["input_video_info_key"]], jobs=jobs)
            native.write_json(output / "request.json", request)
            requests.append(dict(request, sequence=sequence, out=str(output)))
        if requests:
            qwen_process("refine", requests_file(self.out, "refine", requests))
            results = {item["out"]: native.read_json(Path(item["out"]) / "result.json") for item in requests}
        saved = self.refine
        try:
            self.refine = lambda video, info, jobs, output: results[str(output)]
            return original(self, storage, **keys)
        finally:
            self.refine = saved

    return run


def track_operator(original):
    """Record completion as the installed operator advances to the next clip."""
    @wraps(original)
    def run(self, storage, **keys):
        dependency = sys.modules[original.__module__]
        previous = dependency.report_work
        current = None

        def report(*, sequence=None, detail=None, log=None):
            nonlocal current
            if sequence is not None and sequence != current:
                if current:
                    part_state(current, "done", "完成")
                current = sequence
                part_state(current, "running", detail)
            if log is not None and current:
                part_state(current, "running", detail or log.stem, log)
            previous(sequence=sequence, detail=detail, log=log)

        dependency.report_work = report
        ready_parts(storage.read("dict"), keys.get("input_sequence_key", "sequence_id"))
        try:
            result = original(self, storage, **keys)
        except BaseException:
            if current:
                part_state(current, "failed", "执行失败")
            raise
        else:
            if current:
                part_state(current, "done", "完成")
            return result
        finally:
            dependency.report_work = previous

    return run


def install_abs_transport():
    if os.environ.get("FLOW_STUDIO_ABS_HTTP_PROXY"):
        sys.path.insert(0, str(Path.cwd() / "flow-studio"))
        from abs_transport import route_scorer
        native.run_logged = route_scorer(native.run_logged, native.ABS_BACKEND / "local_score.py")


def install(overrides):
    install_abs_transport()
    stages = [
        ("qwen", "qwen", native.QwenVideoPhraseGenerator, lambda original: qwen_run),
        ("segment", "genception", native.GenCeptionVideoSegmenter, lambda original: segment_run),
        ("refine", "refiner", feedback.QwenPromptRefiner, capture_refinement),
        ("score", "abs", native.GenCeptionAbsoluteScorer, track_operator),
        ("gate", "gate", feedback.MaskQualityGate, track_operator),
    ]
    for key, stage, operator, prepare in stages:
        original = operator.run.__wrapped__
        operator.run = operator_progress(stage)(track_operator(original) if key in overrides else prepare(original))


def merge_group_manifests(groups, out):
    for name in ("input.jsonl", "dataflow_step1.jsonl", "dataflow_step2.jsonl", "dataflow_step3.jsonl", "final_masks.jsonl"):
        files = [group / name for group in groups]
        if not all(file.is_file() for file in files):
            continue
        tmp = out / (name + ".tmp")
        with tmp.open("wb") as merged:
            for file in files:
                with file.open("rb") as stream:
                    shutil.copyfileobj(stream, merged)
        tmp.replace(out / name)


def finish_fusion(progress, out, source, flags):
    progress.data["rounds"][-1]["stages"]["fusion"] = {"status": "pending"}

    @operator_progress("fusion")
    def run():
        sys.path.insert(0, str(Path.cwd() / "flow-studio"))
        from mask_fusion import fuse_masks
        report_work(detail="匹配相邻窗口并导出全序列掩码")
        result = fuse_masks(out, source, refined="--refine" in flags)
        progress.data["fusion"] = result
        report_work(detail=f"{result['tracks']} 条轨迹 · {len(result['reviewSeams'])} 处接缝待复核")

    run()


def workbench_main():
    args = list(sys.argv)
    fuse = "--workbench-fuse" in args
    if fuse:
        args.remove("--workbench-fuse")
        sys.argv = args.copy()
    source_index, output_index = args.index("--input") + 1, args.index("--out") + 1
    source = Path(args[source_index]).resolve(strict=True)
    rows = feedback.read_jsonl(source)
    if len(rows) <= 1 and not fuse:
        return native.main()
    out = Path(args[output_index]).resolve()
    out.mkdir(parents=True, mode=0o700)
    groups = [out / "first-part", out / "remaining-parts"]
    saved_monitor, saved_new_round = native.monitor_run, feedback.new_round
    with ExitStack() as stack:
        progress = None

        def tag_round(group):
            progress.data["rounds"][-1]["group"] = group
            progress.save()

        @contextmanager
        def group_monitor(folder, stages, mode):
            nonlocal progress
            if progress is None:
                progress = stack.enter_context(saved_monitor(out, stages, mode))
                progress.data["workbenchGroups"] = {}
            else:
                progress.add_round(0, stages)
            if folder != out:
                tag_round(folder.name)
            yield progress

        def new_round(number):
            group = progress.data["rounds"][-1].get("group")
            saved_new_round(number)
            if group:
                tag_round(group)

        native.monitor_run, feedback.new_round = group_monitor, new_round
        try:
            if len(rows) <= 1:
                native.main()
            else:
                for group, subset in zip(groups, (rows[:1], rows[1:]), strict=True):
                    manifest = out / (group.name + ".input.jsonl")
                    for row in subset:
                        row["video_path"] = str((source.parent / row["video_path"]).resolve())
                    feedback.write_jsonl(manifest, subset)
                    sys.argv = args.copy()
                    sys.argv[source_index], sys.argv[output_index] = str(manifest), str(group)
                    native.main()
                    progress.data["workbenchGroups"][group.name] = "done"
                    progress.save()
                merge_group_manifests(groups, out)
            if fuse:
                finish_fusion(progress, out, source, args)
        finally:
            native.monitor_run, feedback.new_round = saved_monitor, saved_new_round
            sys.argv = args


if __name__ == "__main__":
    if "--workbench-manifest" in sys.argv:
        sys.argv.remove("--workbench-manifest")
        install_abs_transport()
        native.main()
        sys.exit()
    # Keep the installed CLI and existing edited operator bodies intact.
    install(set(os.environ.get("FLOW_STUDIO_NATIVE_OVERRIDES", "").split(",")))
    gpus = next((sys.argv[i + 1] for i, arg in enumerate(sys.argv[:-1]) if arg == "--gpus"), "0,1")
    os.environ["FLOW_STUDIO_JOB_GPUS"] = gpus
    workbench_main()
