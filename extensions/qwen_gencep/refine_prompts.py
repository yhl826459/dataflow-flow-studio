"""Use the existing Qwen FP8 vision runner to revise one phrase per target."""

import argparse
import json
from pathlib import Path

import av
import numpy as np
from PIL import Image
from pydantic import Field

import experiment


class Revision(experiment.Record):
    phrase: str = Field(min_length=1, max_length=512)


SYSTEM = """You revise a referring expression for a video segmentation model. The target
is the exact same visible instance throughout all rounds. Keep the identity
and all requested scope or anatomical boundaries. Use only visually grounded,
stable cues. Do not assign a left or right side unless supported by the RGB.
Write a genuinely different description from all previously used phrases.
Changing only capitalization, punctuation, or spaces does not count.
Output a single natural English noun phrase in the requested JSON schema.
Do not include a score, frame range, instruction to the model, or explanation."""


OBJECT_RULES = """Keep the same object as the head of the phrase and retain its reliable
appearance or category. When it distinguishes this instance, add one specific
relation grounded in RGB: how a hand interacts with it, its position relative
to a visible landmark, or a stable position in the frame. Use the person's
left or right hand only when anatomical handedness is clear; image-left or
image-right refers only to screen position. If handedness is unclear, describe
the visible contact without naming a side. Check the cue across the supplied
frames. Avoid inferring a new part, object class, or action from mask shape."""


def sampled_rgb(video, info, indexes):
    images = {}
    with av.open(video) as stream:
        origin = None
        count = 0
        for index, frame in enumerate(stream.decode(video=0)):
            if frame.pts is None or frame.time_base is None:
                raise ValueError("RGB frame has no presentation timestamp")
            pts = frame.pts * frame.time_base
            if origin is None:
                origin = pts
            count = index + 1
            if index in indexes:
                rgb = frame.to_image().convert("RGB")
                if rgb.size != (info["width"], info["height"]):
                    raise ValueError("Video resolution changed before Qwen refinement")
                rgb.thumbnail((320, 320))
                images[index] = (rgb, float(pts - origin))
    if count != info["frames"] or set(images) != indexes:
        raise ValueError("Refinement frame indexes no longer match the source video")
    return images


def input_frames(job):
    low = [sample["frame"] for interval in job["low_intervals"] for sample in interval["samples"]]
    return low + job.get("context_rgb_frames", [])


def build_prompt(job, info, rgb_samples):
    mask = np.load(job["mask"], mmap_mode="r", allow_pickle=False)
    expected = (info["frames"], info["height"], info["width"])
    if mask.shape != expected or mask.dtype != np.uint8 or mask.max() > 1:
        raise ValueError(f"Expected a binary GenCeption mask matching the original video: {job['mask']}")
    images, timestamps, intervals = [], [], []
    for interval in job["low_intervals"]:
        group = []
        for sample in interval["samples"]:
            frame_index = sample["frame"]
            rgb, pts = rgb_samples[frame_index]
            binary = Image.fromarray(mask[frame_index] * 255).resize(rgb.size, Image.Resampling.NEAREST)
            paired = Image.new("RGB", (2 * rgb.width, rgb.height))
            paired.paste(rgb, (0, 0))
            paired.paste(binary.convert("RGB"), (rgb.width, 0))
            group.append({"image": len(images), "source_frame": frame_index, "time_s": round(pts, 6),
                          "score": sample["score"]})
            images.append(paired)
            timestamps.append(pts)
        intervals.append(group)
    if not images:
        raise ValueError(f"No low-score frames for target {job['id']!r}")
    context = {
        "target_id": job["id"], "fixed_target_description": job["scoring_phrase"],
        "previous_segmentation_phrase": job["previous_phrase"],
        "do_not_reuse_phrases": job.get("used_phrases", [job["scoring_phrase"], job["previous_phrase"]]),
        "low_score_intervals": intervals,
    }
    references = []
    for frame in job.get("context_rgb_frames", []):
        rgb, pts = rgb_samples[frame]
        references.append({"image": len(images), "source_frame": frame, "time_s": round(pts, 6)})
        images.append(rgb)
        timestamps.append(pts)
    panel_intro = "For each image"
    if references:
        context["context_rgb_frames"] = references
        panel_intro = (
            "The images listed in context_rgb_frames are original RGB reference frames of the same clip. "
            "Use them to check the target's appearance and whether the proposed relation holds across time. "
            "For each image listed in low_score_intervals"
        )
    task = (
        f"{panel_intro}, the left panel is the original RGB frame and the right panel is the "
        "current binary mask for this target, with white foreground. Use the frames and scores "
        "to identify a better visual description of the same target. Return the revised phrase. "
        "Do not return a used phrase. Rephrase with an action, object, or other cue clearly "
        "supported by these frames while preserving the target and its boundaries.\n"
        + json.dumps(context, ensure_ascii=False)
    )
    return task, images, timestamps


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("request", type=Path)
    args = parser.parse_args()
    request = json.loads(args.request.read_text(encoding="utf-8"))
    output = args.request.parent
    jobs, info = request["jobs"], request["video_info"]
    samples = sampled_rgb(request["video"], info, {frame for job in jobs for frame in input_frames(job)})
    runner = experiment.Runner(args.model_path, max(len(input_frames(job)) for job in jobs))
    revisions = []
    for index, job in enumerate(jobs, 1):
        name = f"target_{index:03}"
        experiment.RULES = SYSTEM + ("\n" + OBJECT_RULES if job.get("task") == "objects" else "")
        task, images, timestamps = build_prompt(job, info, samples)
        experiment.save_json(output / f"{name}.input.json", {
            "system": experiment.RULES, "task": task, "timestamps_s": timestamps,
        })
        experiment.save_contact_sheet(images, timestamps, output / f"{name}.frames.jpg")
        reply = runner.ask(task, Revision, images, timestamps, output, name)
        revisions.append({"id": job["id"], "phrase": reply.phrase.strip()})
    experiment.save_json(output / "result.json", revisions)


if __name__ == "__main__":
    main()
