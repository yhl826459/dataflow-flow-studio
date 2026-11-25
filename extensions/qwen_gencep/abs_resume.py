"""Continue built-in Workbench runs with a verified initial ABS checkpoint."""

import copy
from functools import partial
from hashlib import sha256
from pathlib import Path
import re
import shutil
import sys
from contextlib import contextmanager

import pipeline as native
import run_progress
import score_feedback as feedback
import workbench_batch as batch


TRANSIENT = r"Error code: (?:429|5\d\d)\b|Connection error|timed out"
VERIFY_COMPLETE = """
import json, sys
from pathlib import Path
import local_score as scorer
config_path, output, model = sys.argv[1:]
output=Path(output)
config=scorer.read_json(Path(config_path))
config={**config, "api": {**config.get("api", {}), "model": model}, "every": scorer.q.sampling_every(config, None)}
manifest=scorer.read_json(output/"_run/manifest.json")
scorer.verify(output, manifest, config)
score=output/"scores.json"
if scorer.digest(score)!=scorer.read_json(output/"_run/scores_receipt.json")["sha256"]:
    raise ValueError("ABS scores changed")
if not scorer.read_json(score)["complete"]:
    raise ValueError("ABS score receipt is incomplete")
"""


def score_resume(sequence, video, info, segments, out, *, settings, every, attempt):
    inputs, result = out / "abs_inputs", out / "Absolute Score"
    if not inputs.exists() and not result.exists():
        return native.score_absolute(sequence, video, info, segments, out, abs_settings=settings,
                                     every=every, prepare_only=False)
    job, config, manifest = inputs / "job.json", inputs / "config.json", result / "_run/manifest.json"
    if not all(path.is_file() for path in (job, config, manifest)):
        raise ValueError("ABS 检查点不完整，保留原文件。")
    frozen = native.read_json(job)
    targets = [{key: mask[key] for key in ("id", "phrase", "mask")} for mask in segments]
    expected = dict(sequence_id=sequence, video=video, video_info=info, segments=targets, every=every)
    if any(frozen.get(key) != value for key, value in expected.items()):
        raise ValueError("ABS 检查点输入与分割结果不一致，保留原文件。")
    meta = native.read_json(manifest)
    model = meta["settings"]["model"]
    if model != native.ABS_MODEL:
        raise ValueError("原 ABS 模型与当前运行版本不一致。")
    scores, movie = result / "scores.json", result / "local_absolute_scoring_3fps.mkv"
    python = str(native.QWEN / ".venv/bin/python")
    completed = scores.is_file() and movie.is_file() and native.read_json(scores).get("complete")
    if completed:
        native.run_logged([python, "-B", "-c", VERIFY_COMPLETE, str(config), str(result), model],
                          out / f"abs_verify_{attempt}.log", cwd=native.ABS_BACKEND)
    else:
        command = [python, "-B", str(native.ABS_BACKEND / "local_score.py"), sequence, model,
                   "--config", str(config), "--out", str(result), "--resume"]
        for index, delay in enumerate((0, 15, 35, 65)):
            native.time.sleep(delay)
            log = out / f"abs_continue_{attempt}_{index:02d}.log"
            try:
                native.run_logged(command, log)
                break
            except RuntimeError:
                if index == 3 or not re.search(TRANSIENT, log.read_text(encoding="utf-8", errors="replace"), re.I):
                    raise
    if not scores.is_file() or not movie.is_file() or not native.read_json(scores)["complete"]:
        raise ValueError("ABS 续跑未生成完整的评分结果。")
    return {"status": "scored", "manifest": str(manifest), "scores": str(scores), "video": str(movie)}


def prepared_manifest(dataset):
    sys.path.insert(0, str(native.HERE.parents[1] / "flow-studio"))
    from datasets_catalog import _cached

    fingerprint = dataset["fingerprint"]
    if not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
        raise ValueError("原序列的输入缓存标识无效。")
    cache = native.HERE.parents[1] / "flow-studio/.local/dataset-inputs" / fingerprint
    return _cached(cache, fingerprint)


def tail_manifest(out, prepared):
    rows = feedback.read_jsonl(prepared)
    if len(rows) < 2 or (out / "remaining-parts").exists():
        raise ValueError("未运行的片段范围与原始检查点不一致。")
    path = out / "remaining-parts.input.jsonl"
    if path.exists():
        if feedback.read_jsonl(path) != rows[1:]:
            raise ValueError("余下片段清单与已验证缓存不一致。")
    else:
        feedback.write_jsonl(path, rows[1:])
    return path


def continue_run(plan):
    public, source = Path(plan["out"]), Path(plan["input"])
    compact = (public / ".run").is_dir()
    out = public / ".run" if compact else public
    attempt, checkpoint = plan["resumeAttempt"], plan["checkpoint"]
    prior = plan["prior"]
    group = out / checkpoint["group"] if checkpoint["group"] else out
    fusion = "--workbench-fuse" in plan["flags"]
    flags = [flag for flag in plan["flags"] if flag != "--workbench-fuse"]
    with run_progress.monitor_run(public, ["abs"], prior["mode"]) as progress:
        progress.data["resumeAttempt"] = attempt
        progress.data["rounds"] = copy.deepcopy(prior["rounds"])
        progress.data["rounds"][-1]["stages"]["abs"] = {"status": "pending"}
        progress.data["workbenchGroups"] = dict(prior.get("workbenchGroups", {}))
        progress.save()
        if sha256(source.read_bytes()).hexdigest() != checkpoint["sha256"]:
            raise ValueError("已完成的分割检查点有改动，停止续跑。")
        failed_job = native.read_json(group / checkpoint["sequence"] / "abs_inputs/job.json")
        every = failed_job["every"]
        if "--abs-every" in flags and every != int(flags[flags.index("--abs-every")+1]):
            raise ValueError("原评分间隔与保存的流水线不一致。")
        prepared = prepared_manifest(plan["dataset"]) if fusion or checkpoint["group"] == "first-part" else None
        remaining = tail_manifest(out, prepared) if checkpoint["group"] == "first-part" else None
        if (group / "refinement").exists():
            raise ValueError("已存在反馈轮次结果。初轮 ABS 续跑不会覆盖这些结果。")
        settings = failed_job["abs_settings"]
        score = partial(score_resume, settings=settings, every=every, attempt=attempt)
        storage_folder = group / ".workbench-resume" / attempt
        storage_folder.mkdir(parents=True)
        pipeline = native.AbsoluteScoringPipeline(source, group, score)
        pipeline.storage = native.FileStorage(str(source), cache_path=str(storage_folder), file_name_prefix="abs")
        pipeline.compile()
        pipeline.forward()
        scored = group / "dataflow_step3.jsonl"
        new_scored = storage_folder / "abs_step1.jsonl"
        if scored.exists() and scored.read_bytes() != new_scored.read_bytes():
            raise ValueError("已存在不同的初轮评分结果，保留原文件。")
        if not scored.exists():
            temp = scored.with_suffix(".resume.tmp")
            shutil.copyfile(new_scored, temp)
            temp.replace(scored)
        group_name = checkpoint["group"]
        original_round, original_monitor = feedback.new_round, native.monitor_run

        def tag():
            if group_name:
                progress.data["rounds"][-1]["group"] = group_name
            progress.save()

        def begin_round(number):
            original_round(number)
            tag()

        @contextmanager
        def continue_monitor(folder, stages, mode):
            progress.add_round(0, stages)
            tag()
            yield progress

        feedback.new_round, native.monitor_run = begin_round, continue_monitor
        argv = sys.argv
        try:
            if "--refine" in flags:
                threshold = float(flags[flags.index("--refine-threshold")+1])
                segment = partial(native.segment_gencep, gpus="0,1", checkpoint=native.HOME / "pipe_14b_v2.pkl",
                                  encoder_model=native.HOME / "umt5-xxl-encoder-bf16-dataflow",
                                  encoder_python=native.QWEN / ".venv/bin/python", handoff=False)
                feedback.run_refinement(scored, group, threshold=threshold,
                                        refine=partial(native.refine_qwen, gpus="0,1"),
                                        retry=partial(native.retry_round, segment=segment, score=score))
            if group_name:
                progress.data["workbenchGroups"][group_name] = "done"
                progress.save()
            if remaining:
                group_name = "remaining-parts"
                sys.argv = [argv[0], "--input", str(remaining), "--out", str(out / group_name), *flags]
                native.main()
                progress.data["workbenchGroups"][group_name] = "done"
                progress.save()
            if group_name:
                batch.merge_group_manifests([out / "first-part", out / "remaining-parts"], out)
            if fusion:
                report = batch.finish_fusion(progress, out, prepared, flags, delivery=public if compact else None)
                if compact:
                    from video_delivery import verify_delivered_work
                    verify_delivered_work(public, report)
        finally:
            feedback.new_round, native.monitor_run = original_round, original_monitor
            sys.argv = argv


if __name__ == "__main__":
    batch.install(set())
    continue_run(native.read_json(Path(sys.argv[1])))
