"""Convert per-target binary arrays to the server ABS per-instance input format."""

import argparse
from fractions import Fraction
import json
from pathlib import Path

import av
import numpy as np

SOURCE = "genception"


def write_json(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_mask_video(path, masks, timing, fps):
    _, height, width = masks.shape
    with av.open(str(path), "w", format="matroska") as video:
        stream = video.add_stream("ffv1", rate=fps)
        stream.width, stream.height = width, height
        stream.pix_fmt = "gray"
        stream.codec_context.time_base = Fraction(1, 1000)
        for values, (pts, time_base) in zip(masks, timing, strict=True):
            frame = av.VideoFrame.from_ndarray(values, format="gray")
            frame.pts, frame.time_base = pts, time_base
            video.mux(stream.encode(frame))
        video.mux(stream.encode())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", type=Path)
    args = parser.parse_args()
    job = json.loads(args.job.read_text(encoding="utf-8"))
    folder = args.job.parent
    video_info = job["video_info"]
    shape = (video_info["frames"], video_info["height"], video_info["width"])
    with av.open(job["video"]) as source:
        timing = [(frame.pts, frame.time_base) for frame in source.decode(video=0)]
    if len(timing) != shape[0] or any(pts is None or tb is None for pts, tb in timing):
        raise ValueError("RGB frame count or frame timestamps differ from the pipeline input")

    labels, masks = [], {}
    for number, target in enumerate(job["segments"], 1):
        identity = str(target["id"])
        if not identity.strip() or identity in masks:
            raise ValueError(f"Invalid or repeated target ID: {identity!r}")
        array = np.load(target["mask"], mmap_mode="r", allow_pickle=False)
        if array.shape != shape or array.dtype != np.uint8 or array.max() > 1:
            raise ValueError(f"Invalid source-size binary mask: {target['mask']}")
        path = folder / f"target_{number:03d}.mkv"
        write_mask_video(path, array, timing, video_info["fps"])
        labels.append({"instance_id": identity, "prompt": target["phrase"], "mask_value": number})
        masks[identity] = str(path)

    instances = folder / "instances.json"
    write_json(instances, {
        "labels": labels, "provenance": {
            "source": "GenCeption", "prompt_source": "Qwen final phrases",
            "mask_value_role": "canonical_identity_and_display_only",
        },
    })
    settings = job.get("abs_settings") or job["desktop_api"]
    write_json(folder / "config.json", {
        "sequence": job["sequence_id"], "api": settings["api"], "every": job["every"],
        "inputs": {"rgb": job["video"]}, "instances_json": str(instances),
        "candidate_sources": [SOURCE], "instance_masks": {SOURCE: masks},
        "instance_foreground": "nonzero",
    })


if __name__ == "__main__":
    main()
