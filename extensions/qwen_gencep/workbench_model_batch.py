"""Reuse one model process for a stage of a single Workbench sequence."""

import argparse
import json
import os
from pathlib import Path
from types import SimpleNamespace


def progress(sequence, status, detail):
    print("@@FLOW_PART " + json.dumps([sequence, status, detail]), flush=True)


def describe_parts(requests, model_path):
    import experiment
    import experiment_hands

    objects = experiment.RULES, experiment.GENERATE, experiment.REVIEW
    # The canonical inputs are 30 fps and at most 81 frames. At 5 fps a
    # clip has at most 14 sampled positions, with one extra slot for timing.
    runner = None
    for item in requests:
        progress(item["sequence"], "running", "Qwen 提词")
        experiment.RULES, experiment.GENERATE, experiment.REVIEW = (
            (experiment_hands.RULES, experiment_hands.GENERATE, experiment_hands.REVIEW)
            if item["task"] == "hands" else objects
        )
        out = Path(item["out"])
        out.mkdir()
        images, metadata = experiment.sample_video(Path(item["video"]), item["fps"])
        timestamps = metadata["timestamps_s"]
        experiment.save_json(out / "input.json", metadata)
        experiment.save_contact_sheet(images, timestamps, out / "frames.jpg")
        if runner is None:
            runner = experiment.Runner(model_path, 15)
        result = experiment.describe(runner, images, timestamps, out)
        result.update(model_path=str(model_path), input=metadata)
        experiment.save_json(out / "result.json", result)
        progress(item["sequence"], "done", "提词和复核完成")


def refine_parts(requests, model_path):
    import experiment
    import refine_prompts as original

    limit = max(len(original.input_frames(job)) for item in requests for job in item["jobs"])
    runner = None
    for item in requests:
        progress(item["sequence"], "running", "Qwen 改写提示词")
        output = Path(item["out"])
        jobs, info = item["jobs"], item["video_info"]
        frames = {frame for job in jobs for frame in original.input_frames(job)}
        samples = original.sampled_rgb(item["video"], info, frames)
        if runner is None:
            runner = experiment.Runner(model_path, limit)
        revisions = []
        for index, job in enumerate(jobs, 1):
            name = f"target_{index:03d}"
            experiment.RULES = original.SYSTEM + ("\n" + original.OBJECT_RULES if job.get("task") == "objects" else "")
            prompt, images, times = original.build_prompt(job, info, samples)
            experiment.save_json(output / f"{name}.input.json", {
                "system": experiment.RULES, "task": prompt, "timestamps_s": times,
            })
            experiment.save_contact_sheet(images, times, output / f"{name}.frames.jpg")
            answer = runner.ask(prompt, original.Revision, images, times, output, name)
            revisions.append({"id": job["id"], "phrase": answer.phrase.strip()})
        experiment.save_json(output / "result.json", revisions)
        progress(item["sequence"], "done", "提示词改写完成")


class PhaseJobs:
    """Report clip changes without editing the installed GenCeption runtime."""

    def __init__(self, jobs, label):
        self.jobs = jobs
        self.label = label
        self.current = None

    def __len__(self):
        return len(self.jobs)

    def __getitem__(self, index):
        dataset, token, sequence = self.jobs[index]
        if sequence != self.current:
            if self.current:
                progress(self.current, "waiting", self.label + "完成")
            self.current = sequence
            progress(sequence, "running", self.label)
        return dataset, token

    def finish(self):
        if self.current:
            progress(self.current, "waiting", self.label + "完成")


def generation_phase(phase, requests, staging):
    import genception_worker as original

    clips = []
    for index, item in enumerate(requests, 1):
        work = Path(item["out"])
        names = [f"target_{i:03d}" for i in range(1, len(item["targets"]) + 1)]
        clip = f"part_{index:05d}"
        targets = [(f"{clip}_{name}", name) for name in names]
        clips.append((item, work, clip, targets))
    if phase in ("prepare", "export"):
        for item, work, _, targets in clips:
            names = [name for _, name in targets]
            label = "准备分割输入" if phase == "prepare" else "导出掩码"
            progress(item["sequence"], "running", label)
            getattr(original, phase)(work, item, item["targets"], names)
            progress(item["sequence"], "done" if phase == "export" else "waiting", label + "完成")
        return

    import numpy as np
    import run_hand14b_twophase as runtime

    runtime.BASE = str(staging)
    runtime.EMBEDS = str(staging / "embeddings")
    runtime.LAT_DIR = str(staging / "latents")
    runtime.PRED_DIR = str(staging / "predictions")
    runtime.OUT_ROOT = str(staging / "decoded")
    dataset = original.DATASET
    jobs = []
    for item, work, clip, targets in clips:
        if phase == "encode":
            cache = staging / "hand_obj_caches"
            cache.mkdir(exist_ok=True)
            (cache / f"{clip}.npy").symlink_to(work / "hand_obj_caches/source.npy")
            jobs.append((dataset, clip, item["sequence"]))
        else:
            jobs.extend((dataset, token, item["sequence"]) for token, _ in targets)
    labels = {"encode": "GenCeption 编码", "forward": "GenCeption 分割推理", "decode": "GenCeption 解码"}
    runtime.JOBS = PhaseJobs(jobs, labels[phase])
    args = SimpleNamespace(start_at=0, max_jobs=len(jobs), embed_name=None, pred_suffix="")
    getattr(runtime, f"phase_{phase}")(runtime.load_pipe(requests[0]["checkpoint"]), args)
    runtime.JOBS.finish()
    for item, work, clip, targets in clips:
        if phase == "encode":
            latent = Path(runtime.LAT_DIR) / f"{clip}.npy"
            values = np.load(latent, mmap_mode="r", allow_pickle=False)
            if values.shape != (1, 16, 21, 60, 104) or values.dtype != np.uint16:
                raise ValueError(f"Unexpected video latent at {latent}")
            latents = work / "hand_obj_latents"
            latents.mkdir()
            os.link(latent, latents / "source.npy")
            for token, name in targets:
                os.link(latent, latents / f"{name}.npy")
                os.link(latent, Path(runtime.LAT_DIR) / f"{token}.npy")
                embeds = Path(runtime.EMBEDS)
                embeds.mkdir(exist_ok=True)
                (embeds / f"{token}.npy").symlink_to(work / "hand_obj_embeds" / f"{name}.npy")
        elif phase == "forward":
            predictions = work / "hand_obj_pred"
            predictions.mkdir()
            for token, name in targets:
                os.link(Path(runtime.PRED_DIR) / f"{token}.npy", predictions / f"{name}.npy")
        else:
            decoded = Path(runtime.OUT_ROOT) / "group2_handobj" / dataset
            output = work / "model_outputs/group2_handobj" / dataset
            output.mkdir(parents=True)
            for token, name in targets:
                for suffix in ("_seg.mp4", "_seg.npy", "_mask.npy"):
                    os.link(decoded / (token + suffix), output / (name + suffix))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("qwen", "refine", "prepare", "encode", "forward", "decode", "export"))
    parser.add_argument("requests", type=Path)
    parser.add_argument("resource", type=Path)
    args = parser.parse_args()
    requests = json.loads(args.requests.read_text(encoding="utf-8"))
    if args.phase == "qwen":
        describe_parts(requests, args.resource)
    elif args.phase == "refine":
        refine_parts(requests, args.resource)
    else:
        generation_phase(args.phase, requests, args.resource)


if __name__ == "__main__":
    main()
