"""DataFlow pipeline for Qwen phrases, GenCeption masks, and optional ABS scoring."""

import argparse
import json
import os
import re
import subprocess
import time
from functools import partial
from fractions import Fraction
from pathlib import Path

from dataflow.core import OperatorABC
from dataflow.pipeline import PipelineABC
from dataflow.utils import OPERATOR_REGISTRY
from dataflow.utils.storage import FileStorage

from run_progress import monitor_run, operator_progress, report_work

HERE = Path(__file__).resolve().parent
HOME = Path.home()
QWEN = HOME / "qwen38-fp8"
GEN_PYTHON = HOME / ".venv-genception14b/bin/python"
GEN_RUNTIME = HOME / "genception14b_dexycb_141754_handonly"
TASK_SCRIPTS = {"hands": "experiment_hands.py", "objects": "experiment.py"}
QWEN_FPS = 5
ABS_BACKEND = HOME / "scoring_scripts" / "abs"
ABS_MODEL = "gpt-6.2-sol"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_logged(command, log, *, env=None, cwd=None):
    report_work(log=log)
    print(f"{log.parent.name}: {log.name}", flush=True)
    with log.open("wb") as output:
        try:
            subprocess.run(command, check=True, cwd=cwd, env=env, stdout=output, stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError as error:
            raise RuntimeError(f"Process exited with {error.returncode}. See {log}") from error


def ensure_free_memory(gpus, minimum_mib):
    deadline = time.monotonic() + 45
    while True:
        result = subprocess.check_output(
            ["nvidia-smi", "--id", gpus, "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            text=True,
        )
        free = [int(line) for line in result.splitlines()]
        if len(free) == len(gpus.split(",")) and min(free) >= minimum_mib:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(f"GPUs {gpus} have {free} MiB free; need {minimum_mib} MiB per card.")
        time.sleep(2)


def probe_video(video):
    info = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v", "-count_frames",
        "-show_entries", "stream=width,height,r_frame_rate,avg_frame_rate,nb_read_frames",
        "-of", "json", str(video),
    ]))["streams"]
    if len(info) != 1:
        raise ValueError(f"{video}: expected one video stream")
    stream = info[0]
    shape = (int(stream["height"]), int(stream["width"]))
    frames = int(stream["nb_read_frames"])
    if any(Fraction(stream[key]) != 30 for key in ("r_frame_rate", "avg_frame_rate")):
        raise ValueError(f"{video}: this pipeline expects 30 fps")
    if not 1 <= frames <= 81 or any(size <= 0 or size % 2 for size in shape):
        raise ValueError(f"{video}: expected 1–81 frames and positive even dimensions")
    return {"frames": frames, "height": shape[0], "width": shape[1], "fps": 30}


def safe_sequence_id(value):
    if (not isinstance(value, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", value)
            or re.fullmatch(r"\d+(?:\.\d*)?(?:[eE]-?\d+)?", value)):
        raise ValueError(f"Invalid sequence_id: {value!r}")
    return value


def snapshot_input(source, destination):
    rows = []
    seen = set()
    for line in source.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        sequence_id = safe_sequence_id(row["sequence_id"])
        if sequence_id in seen:
            raise ValueError(f"Repeated sequence_id: {sequence_id}")
        seen.add(sequence_id)
        task = row.get("task", "objects")
        if task not in TASK_SCRIPTS:
            raise ValueError(f"Expected task 'hands' or 'objects', got {task!r}")
        video = (source.parent / row["video_path"]).resolve(strict=True)
        if not video.is_file():
            raise ValueError(f"Expected a video file: {video}")
        rows.append(dict(sequence_id=sequence_id, video_path=str(video), task=task,
                         video_info=probe_video(video)))
    if not rows:
        raise ValueError("Input JSONL has no video rows")
    destination.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                           encoding="utf-8")


def run_qwen(script, arguments, log, *, gpus):
    ensure_free_memory(gpus, 24800)
    model = Path((QWEN / "model_path.txt").read_text().strip())
    env = dict(os.environ, CUDA_VISIBLE_DEVICES=gpus, CUDA_DEVICE_ORDER="PCI_BUS_ID",
               PYTHONPATH=os.pathsep.join([str(QWEN), os.environ.get("PYTHONPATH", "")]).rstrip(os.pathsep))
    run_logged([
        "bash", "-c", 'set -e; source ./env.sh; exec "$@"', "qwen",
        str(QWEN / ".venv/bin/python"), "-B", str(script), *arguments, "--model-path", str(model),
    ], log, cwd=QWEN, env=env)


def generate_qwen(video, task, out, *, gpus):
    run_qwen(TASK_SCRIPTS[task], ["run", "--video", video, "--fps", str(QWEN_FPS), "--output", str(out)],
             out.parent / "qwen.log", gpus=gpus)
    return out / "result.json"


def refine_qwen(video, video_info, jobs, out, *, gpus):
    out.mkdir(parents=True)
    request = out / "request.json"
    write_json(request, dict(video=video, video_info=video_info, jobs=jobs))
    run_qwen(HERE / "refine_prompts.py", [str(request)], out / "qwen.log", gpus=gpus)
    return read_json(out / "result.json")


def final_targets(result):
    targets = []
    seen = set()
    for item in result["objects"]:
        target_id, phrase = item["id"], item["phrase"]
        if (not isinstance(target_id, (str, int)) or isinstance(target_id, bool)
                or target_id == "" or target_id in seen
                or not isinstance(phrase, str) or not phrase.strip()):
            raise ValueError(f"Invalid target id or phrase: {item!r}")
        seen.add(target_id)
        targets.append({"id": target_id, "phrase": phrase})
    return targets


def embed_targets(request, archive, *, encoder_model, encoder_python, handoff):
    if handoff:
        report_work(detail="等待文本向量")
        print(f"Waiting for embeddings at {archive}. Request: {request}", flush=True)
        deadline = time.monotonic() + 900
        while not archive.is_file():
            if time.monotonic() >= deadline:
                raise TimeoutError(f"15 minutes elapsed waiting for {archive}")
            time.sleep(2)
        return
    if not (encoder_model / "config.json").is_file():
        raise FileNotFoundError(f"Missing local UMT5 encoder: {encoder_model}. Use --encoder-handoff for remote encoding.")
    env = dict(os.environ, CUDA_VISIBLE_DEVICES="", HF_HUB_OFFLINE="1",
               OMP_NUM_THREADS="8", MKL_NUM_THREADS="8")
    run_logged([
        str(encoder_python), "-B", str(HERE / "embed_phrases.py"),
        "--model", str(encoder_model), "--request", str(request), "--out", str(archive),
    ], archive.parent / "embeddings.log", env=env)


def segment_gencep(video, targets, video_info, out, *, gpus, checkpoint, encoder_model,
                   encoder_python, handoff):
    out.mkdir(parents=True)
    request = out / "targets.json"
    archive = out / "embeddings.npz"
    write_json(out / "job.json", {"video": video, "video_info": video_info,
                                  "checkpoint": str(checkpoint), "runtime": str(GEN_RUNTIME)})
    temporary_request = request.with_suffix(".partial")
    write_json(temporary_request, targets)
    temporary_request.rename(request)
    embed_targets(request, archive, encoder_model=encoder_model,
                  encoder_python=encoder_python, handoff=handoff)
    env = dict(os.environ, CUDA_VISIBLE_DEVICES="", CUDA_DEVICE_ORDER="PCI_BUS_ID", XLA_PYTHON_CLIENT_PREALLOCATE="false",
               XLA_PYTHON_CLIENT_ALLOCATOR="cuda_async", XLA_CLIENT_MEM_FRACTION="0.739",
               JAX_COMPILATION_CACHE_DIR=str(out / "jax_cache"),
               PYTHONPATH=os.pathsep.join([str(GEN_RUNTIME), str(GEN_RUNTIME / "representations4d")]))
    gpu_one = gpus.split(",")[0]
    for phase, devices, minimum in [
        ("prepare", "", 0), ("encode", gpu_one, 11000), ("forward", gpus, 24800),
        ("decode", gpu_one, 11000), ("export", "", 0),
    ]:
        if devices:
            ensure_free_memory(devices, minimum)
        run_logged([str(GEN_PYTHON), "-B", str(HERE / "genception_worker.py"), phase, str(out)],
                   out / f"{phase}.log", env=dict(env, CUDA_VISIBLE_DEVICES=devices))
    return read_json(out / "result.json")


@OPERATOR_REGISTRY.register()
class QwenVideoPhraseGenerator(OperatorABC):
    def __init__(self, out, generate):
        super().__init__()
        self.out = out
        self.generate = generate

    @operator_progress("qwen")
    def run(self, storage, *, input_sequence_key, input_video_key, input_task_key,
            input_video_info_key, output_targets_key, output_qwen_result_key):
        rows = storage.read("dict")
        for row in rows:
            out = self.out / str(row[input_sequence_key])
            out.mkdir()
            report_work(sequence=row[input_sequence_key], detail="Qwen 提词")
            result_path = self.generate(row[input_video_key], row[input_task_key], out / "qwen")
            result = read_json(result_path)
            metadata = result["input"]
            if (Path(metadata["video"]).resolve() != Path(row[input_video_key])
                    or metadata["decoded_frames"] != row[input_video_info_key]["frames"]
                    or metadata["requested_sampling_fps"] != QWEN_FPS):
                raise ValueError(f"Qwen result does not match the video or sampling: {result_path}")
            row[output_targets_key] = final_targets(result)
            row[output_qwen_result_key] = str(result_path)
        storage.write(rows)


@OPERATOR_REGISTRY.register()
class ManualTarget(OperatorABC):
    def __init__(self, prompt, translation_id, out):
        super().__init__()
        self.prompt = prompt
        self.translation_id = translation_id
        self.out = out

    @operator_progress("prompt")
    def run(self, storage, *, output_targets_key):
        from manual_prompt import frozen_translation
        report_work(detail="翻译中文描述或读取本次任务已保存的译文")
        translated = frozen_translation(self.prompt, self.translation_id)
        write_json(self.out / "prompt_translation.json", translated)
        rows = storage.read("dict")
        for row in rows:
            row["prompt_zh"] = self.prompt
            row[output_targets_key] = translated["targets"]
        storage.write(rows)


@OPERATOR_REGISTRY.register()
class GenCeptionVideoSegmenter(OperatorABC):
    def __init__(self, out, segment):
        super().__init__()
        self.out = out
        self.segment = segment

    @operator_progress("genception")
    def run(self, storage, *, input_sequence_key, input_video_key, input_targets_key,
            input_video_info_key, output_segments_key, output_status_key):
        rows = storage.read("dict")
        for row in rows:
            targets = row[input_targets_key]
            if not targets:
                row[output_segments_key] = []
                row[output_status_key] = "no_targets"
                continue
            out = self.out / str(row[input_sequence_key]) / "genception"
            report_work(sequence=row[input_sequence_key], detail="GenCeption 分割")
            segments = self.segment(row[input_video_key], targets, row[input_video_info_key], out)
            returned = [{"id": item["id"], "phrase": item["phrase"]} for item in segments]
            if returned != targets:
                raise ValueError(f"GenCeption output targets differ from Qwen output: {out}")
            row[output_segments_key] = segments
            row[output_status_key] = "segmented"
        storage.write(rows)


def score_absolute(sequence, video, video_info, segments, out, *, abs_settings, every, prepare_only):
    inputs = out / "abs_inputs"
    inputs.mkdir(parents=True)
    write_json(inputs / "job.json", {
        "sequence_id": sequence, "video": video, "video_info": video_info,
        "segments": [{key: segment[key] for key in ("id", "phrase", "mask")} for segment in segments],
        "abs_settings": abs_settings, "every": every,
    })
    python = str(QWEN / ".venv/bin/python")
    run_logged([python, "-B", str(HERE / "prepare_abs_inputs.py"), str(inputs / "job.json")],
               out / "abs_inputs.log")
    result = out / "Absolute Score"
    command = [python, "-B", str(ABS_BACKEND / "local_score.py"), sequence, ABS_MODEL,
               "--config", str(inputs / "config.json"), "--out", str(result)]
    if prepare_only:
        command.append("--prepare-only")
    manifest = result / "_run/manifest.json"
    for attempt, delay in enumerate((0, 15, 35, 65)):
        log = out / ("abs.log" if not attempt else f"abs_resume{attempt:02d}.log")
        time.sleep(delay)
        try:
            run_logged(command if not attempt else [*command, "--resume"], log)
            break
        except RuntimeError:
            if (prepare_only or attempt == 3 or not manifest.is_file()
                    or not re.search(r"Error code: (?:429|5\d\d)\b|Connection error|timed out",
                                     log.read_text(encoding="utf-8", errors="replace"), re.I)):
                raise
    if not manifest.is_file():
        raise RuntimeError(f"Server ABS did not produce a manifest: {manifest}")
    if prepare_only:
        return {"status": "prepared", "manifest": str(manifest), "scores": None, "video": None}
    scores = result / "scores.json"
    visualization = result / "local_absolute_scoring_3fps.mkv"
    if not read_json(scores)["complete"] or not visualization.is_file():
        raise RuntimeError(f"Server ABS did not complete: {result}")
    return {"status": "scored", "manifest": str(manifest), "scores": str(scores),
            "video": str(visualization)}


def merge_retry_masks(segments, replacements):
    by_id = {segment["id"]: segment for segment in replacements}
    if len(by_id) != len(replacements) or set(by_id) - {segment["id"] for segment in segments}:
        raise ValueError("Unexpected target in retry segmentation")
    # The ABS prompt remains the original phrase; only the replacement mask and its prompt vary.
    return [dict(by_id[segment["id"]], phrase=segment["phrase"],
                 segmentation_phrase=by_id[segment["id"]]["phrase"])
            if segment["id"] in by_id else segment for segment in segments]


@OPERATOR_REGISTRY.register()
class GenCeptionAbsoluteScorer(OperatorABC):
    def __init__(self, out, score):
        super().__init__()
        self.out = out
        self.score = score

    @operator_progress("abs")
    def run(self, storage, *, input_sequence_key, input_video_key, input_video_info_key,
            input_targets_key, input_segments_key, output_abs_key, input_replacements_key=None):
        rows = storage.read("dict")
        seen = set()
        for row in rows:
            sequence = safe_sequence_id(row[input_sequence_key])
            if sequence in seen:
                raise ValueError(f"Repeated sequence_id in segmentation output: {sequence}")
            seen.add(sequence)
            segments = row[input_segments_key]
            if input_replacements_key is not None:
                segments = merge_retry_masks(segments, row[input_replacements_key])
                row[input_segments_key] = segments
            targets = [{"id": item["id"], "phrase": item["phrase"]} for item in segments]
            if targets != row[input_targets_key]:
                raise ValueError(f"Segmentation output does not match Qwen targets: {sequence}")
            if not targets:
                row[output_abs_key] = {"status": "no_targets", "manifest": None,
                                       "scores": None, "video": None}
                continue
            report_work(sequence=sequence, detail="ABS 评分")
            row[output_abs_key] = self.score(
                sequence, row[input_video_key], row[input_video_info_key], segments, self.out / sequence,
            )
        storage.write(rows)


def absolute_step(scoring, storage, **inputs):
    scoring.run(storage=storage, input_sequence_key="sequence_id", input_video_key="video_path",
                input_video_info_key="video_info", input_targets_key="targets",
                input_segments_key="segments", output_abs_key="absolute", **inputs)


class VideoSegmentationPipeline(PipelineABC):
    def __init__(self, source, out, generate, segment, score=None, manual=None):
        super().__init__()
        self.storage = FileStorage(str(source), cache_path=str(out), file_name_prefix="dataflow")
        self.manual = manual
        self.qwen = None if manual is not None else QwenVideoPhraseGenerator(out, generate)
        self.genception = GenCeptionVideoSegmenter(out, segment)
        self.absolute = GenCeptionAbsoluteScorer(out, score) if score is not None else None

    def forward(self):
        if self.manual is not None:
            self.manual.run(storage=self.storage.step(), output_targets_key="targets")
        else:
            self.qwen.run(storage=self.storage.step(), input_sequence_key="sequence_id",
                          input_video_key="video_path", input_task_key="task",
                          input_video_info_key="video_info",
                          output_targets_key="targets", output_qwen_result_key="qwen_result")
        self.genception.run(storage=self.storage.step(), input_sequence_key="sequence_id",
                            input_video_key="video_path", input_targets_key="targets",
                            input_video_info_key="video_info",
                            output_segments_key="segments", output_status_key="segmentation_status")
        if self.absolute is not None:
            absolute_step(self.absolute, self.storage.step())


class AbsoluteScoringPipeline(PipelineABC):
    def __init__(self, source, out, score):
        super().__init__()
        self.storage = FileStorage(str(source), cache_path=str(out), file_name_prefix="abs")
        self.absolute = GenCeptionAbsoluteScorer(out, score)

    def forward(self):
        absolute_step(self.absolute, self.storage.step())


class RefinementRetryPipeline(PipelineABC):
    def __init__(self, source, out, segment, score):
        super().__init__()
        self.storage = FileStorage(str(source), cache_path=str(out), file_name_prefix="retry")
        self.genception = GenCeptionVideoSegmenter(out, segment)
        self.absolute = GenCeptionAbsoluteScorer(out, score)

    def forward(self):
        self.genception.run(storage=self.storage.step(), input_sequence_key="sequence_id",
                            input_video_key="video_path", input_targets_key="revisions",
                            input_video_info_key="video_info", output_segments_key="replacements",
                            output_status_key="retry_segmentation_status")
        absolute_step(self.absolute, self.storage.step(), input_replacements_key="replacements")


def retry_round(out, rows, *, segment, score):
    from score_feedback import write_jsonl

    out.mkdir(parents=True)
    source = out / "retry_input.jsonl"
    write_jsonl(source, rows)
    pipeline = RefinementRetryPipeline(source, out, segment, score)
    pipeline.compile()
    pipeline.forward()
    return out / "retry_step2.jsonl"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source_option = parser.add_mutually_exclusive_group(required=True)
    source_option.add_argument("--input", type=Path, help="JSONL with one row per video")
    source_option.add_argument("--segments", type=Path,
                               help="Start scoring from an existing dataflow_step2.jsonl")
    parser.add_argument("--out", type=Path, required=True, help="A new path under your home directory")
    parser.add_argument("--publish", type=Path, help="Publish compact per-round results separately from --out")
    parser.add_argument("--gpus", default="0,1", help="Two distinct nvidia-smi GPU indices")
    parser.add_argument("--checkpoint", type=Path, default=HOME / "pipe_14b_v2.pkl")
    parser.add_argument("--encoder-python", type=Path, default=QWEN / ".venv/bin/python")
    parser.add_argument("--encoder-model", type=Path, default=HOME / "umt5-xxl-encoder-bf16-dataflow")
    parser.add_argument("--encoder-handoff", action="store_true",
                        help="Wait for an embedding archive computed by embed_phrases.py on another host")
    abs_option = parser.add_mutually_exclusive_group()
    abs_option.add_argument("--abs", action="store_true", help="Run server ABS with the configured API")
    abs_option.add_argument("--abs-prepare-only", action="store_true", help="Prepare server ABS inputs offline")
    parser.add_argument("--abs-every", type=int, help="Override server ABS sampling interval in source frames")
    parser.add_argument("--refine", action="store_true",
                        help="Run score-based mask acceptance and up to two Qwen prompt revisions per target")
    parser.add_argument("--refine-threshold", type=float, help="Pass only above this ABS score (default: 7.0)")
    parser.add_argument("--prompt-zh", help="Segment the target described by this Chinese phrase")
    parser.add_argument("--translation-id", help="Reuse the frozen translation for this workbench submission")
    args = parser.parse_args()
    if args.prompt_zh is not None and (not args.prompt_zh.strip() or not args.input or args.refine):
        parser.error("--prompt-zh requires a non-empty phrase and --input without --refine")
    threshold = 7.0 if args.refine_threshold is None else args.refine_threshold
    if args.refine and not args.abs:
        parser.error("--refine requires --abs")
    if args.refine_threshold is not None and (not args.refine or not 0 <= threshold <= 10):
        parser.error("--refine-threshold must be between 0 and 10 and requires --refine")

    score = None
    if args.abs or args.abs_prepare_only:
        if not all((ABS_BACKEND / name).is_file() for name in ("local_score.py", "api_config.json")):
            parser.error(f"ABS entry or API configuration is missing under {ABS_BACKEND}")
        abs_settings = read_json(ABS_BACKEND / "api_config.json")
        every = abs_settings["every"] if args.abs_every is None else args.abs_every
        if every < 1:
            parser.error("--abs-every must be positive")
        key_env = abs_settings["api"]["key_env"]
        if args.abs and not os.environ.get(key_env):
            parser.error(f"Set {key_env} in this shell to run the server ABS API.")
        score = partial(score_absolute, abs_settings=abs_settings, every=every,
                        prepare_only=args.abs_prepare_only)
    elif args.segments is not None or args.abs_every is not None:
        parser.error("--segments and --abs-every require --abs or --abs-prepare-only")

    out = args.out.resolve()
    if out == HOME.resolve() or not out.is_relative_to(HOME.resolve()):
        parser.error("--out must be under your home directory")
    publish = None
    if args.publish is not None:
        destination = args.publish.resolve()
        if (args.input is None or not args.refine or destination == HOME.resolve()
                or not destination.is_relative_to(HOME.resolve())
                or destination.is_relative_to(out) or out.is_relative_to(destination)):
            parser.error("--publish requires --input and --refine, with separate directories under your home")
        from compact_outputs import publish_round, publish_result

        publish = partial(publish_round, destination)
    segment = None
    if args.input is not None or args.refine:
        if not re.fullmatch(r"\d+,\d+", args.gpus) or len(set(map(int, args.gpus.split(",")))) != 2:
            parser.error("--gpus needs two distinct indices, such as 0,1")
        checkpoint = args.checkpoint.resolve()
        if not checkpoint.is_file():
            parser.error(f"Missing GenCeption checkpoint: {checkpoint}")
        if (args.input is not None and not args.encoder_handoff
                and not (args.encoder_model / "config.json").is_file()):
            parser.error(f"Missing local UMT5 encoder: {args.encoder_model}. Use --encoder-handoff when encoding on another host.")
        segment = partial(segment_gencep, gpus=args.gpus, checkpoint=checkpoint,
                          encoder_model=args.encoder_model.resolve(), encoder_python=args.encoder_python,
                          handoff=args.encoder_handoff)
    stages = [] if args.segments is not None else ["prompt" if args.prompt_zh is not None else "qwen", "genception"]
    if score is not None:
        stages.append("abs")
    if args.refine:
        stages.extend(("gate", "refiner"))
    mode = "refine" if args.refine else "prepare" if args.abs_prepare_only else "abs" if args.abs else "segment"
    out.mkdir(parents=True, mode=0o700)
    with monitor_run(out, stages, mode):
        if args.segments is not None:
            source = args.segments.resolve(strict=True)
            pipeline = AbsoluteScoringPipeline(source, out, score)
            result = out / "abs_step1.jsonl"
        else:
            source = out / "input.jsonl"
            snapshot_input(args.input.resolve(strict=True), source)
            manual = ManualTarget(args.prompt_zh, args.translation_id, out) if args.prompt_zh is not None else None
            pipeline = VideoSegmentationPipeline(
                source, out, partial(generate_qwen, gpus=args.gpus), segment, score=score, manual=manual,
            )
            result = out / ("dataflow_step3.jsonl" if score is not None else "dataflow_step2.jsonl")
        pipeline.compile()
        pipeline.forward()
        if args.refine:
            from score_feedback import run_refinement

            result = run_refinement(result, out, threshold=threshold,
                                    refine=partial(refine_qwen, gpus=args.gpus),
                                    retry=partial(retry_round, segment=segment, score=score),
                                    publish=publish)
            if publish is not None:
                publish_result(destination, result)
        print(result)


if __name__ == "__main__":
    main()
