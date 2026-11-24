"""Run one process per GenCeption phase to release GPU memory between phases."""

import argparse
import json
import os
from pathlib import Path
from types import SimpleNamespace

import mediapy as media
import numpy as np
from PIL import Image

MODEL_FRAMES = 81
MODEL_WIDTH, MODEL_HEIGHT = 832, 480
DATASET = "dex-ycb"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def load_frames(job):
    frames = media.read_video(job["video"])
    info = job["video_info"]
    if (frames.shape != (info["frames"], info["height"], info["width"], 3)
            or frames.metadata.fps != info["fps"] or frames.dtype != np.uint8):
        raise ValueError(f"Decoded RGB differs from the probed video: {job['video']}")
    return frames


def resize(frames, size, method):
    return np.stack([np.asarray(Image.fromarray(frame).resize(size, method)) for frame in frames])


def prepare(work, job, targets, names):
    with np.load(work / "embeddings.npz", allow_pickle=False) as archive:
        if json.loads(archive["targets"].item()) != targets:
            raise ValueError("Embedding archive does not match the Qwen target list")
        embeddings = archive["embeddings"]
    if (embeddings.shape != (len(targets), 1, 226, 4096) or embeddings.dtype != np.float32
            or not np.isfinite(embeddings).all()
            or not np.any(embeddings != 0, axis=(1, 2, 3)).all()):
        raise ValueError("Unexpected GenCeption embedding array")
    embedding_dir = work / "hand_obj_embeds"
    embedding_dir.mkdir()
    for name, embedding in zip(names, embeddings, strict=True):
        np.save(embedding_dir / f"{name}.npy", embedding)

    frames = resize(load_frames(job), (MODEL_WIDTH, MODEL_HEIGHT), Image.Resampling.BILINEAR)
    padded = np.pad(frames, ((0, MODEL_FRAMES - len(frames)), (0, 0), (0, 0), (0, 0)), mode="edge")
    rgb_cache = work / "hand_obj_caches"
    rgb_cache.mkdir()
    np.save(rgb_cache / "source.npy", padded)


def infer(phase, work, job, names):
    import run_hand14b_twophase as runtime

    runtime.BASE = str(work)
    runtime.EMBEDS = str(work / "hand_obj_embeds")
    runtime.LAT_DIR = str(work / "hand_obj_latents")
    runtime.PRED_DIR = str(work / "hand_obj_pred")
    runtime.OUT_ROOT = str(work / "model_outputs")
    jobs = ["source"] if phase == "encode" else names
    runtime.JOBS = [(DATASET, name) for name in jobs]
    args = SimpleNamespace(start_at=0, max_jobs=len(jobs), embed_name=None, pred_suffix="")
    getattr(runtime, f"phase_{phase}")(runtime.load_pipe(job["checkpoint"]), args)
    if phase == "encode":
        latent = Path(runtime.LAT_DIR) / "source.npy"
        values = np.load(latent, mmap_mode="r", allow_pickle=False)
        if values.shape != (1, 16, 21, 60, 104) or values.dtype != np.uint16:
            raise ValueError(f"Unexpected video latent at {latent}")
        for name in names:
            os.link(latent, latent.with_name(f"{name}.npy"))


def export(work, job, targets, names):
    frames = load_frames(job)
    info = job["video_info"]
    segments = []
    for target, name in zip(targets, names, strict=True):
        raw_path = work / "model_outputs/group2_handobj" / DATASET / f"{name}_mask.npy"
        raw = np.load(raw_path, mmap_mode="r", allow_pickle=False)
        if (raw.shape != (MODEL_FRAMES, MODEL_HEIGHT, MODEL_WIDTH) or raw.dtype != np.uint8
                or raw.max() > 1):
            raise ValueError(f"Unexpected GenCeption mask at {raw_path}")
        mask = resize(raw[:len(frames)], (info["width"], info["height"]), Image.Resampling.NEAREST)
        mask_path = work / f"{name}_mask.npy"
        np.save(mask_path, mask)
        preview = work / f"{name}_preview.mp4"
        with media.VideoWriter(str(preview), shape=(info["height"], 3 * info["width"]),
                               fps=info["fps"], codec="h264") as writer:
            for frame, binary in zip(frames, mask, strict=True):
                selected = binary != 0
                overlay = frame.copy()
                overlay[selected] = 0.55 * frame[selected] + 0.45 * np.array([40, 235, 95])
                mono = np.repeat((binary * 255)[:, :, None], 3, axis=2)
                writer.add_image(np.concatenate([frame, mono, overlay], axis=1))
        segments.append(dict(target, mask=str(mask_path), preview=str(preview),
                             frames_with_mask=int(mask.any(axis=(1, 2)).sum())))
    (work / "result.json").write_text(json.dumps(segments, ensure_ascii=False, indent=2) + "\n",
                                      encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["prepare", "encode", "forward", "decode", "export"])
    parser.add_argument("work", type=Path)
    args = parser.parse_args()
    phase, work = args.phase, args.work
    targets = read_json(work / "targets.json")
    job = read_json(work / "job.json")
    names = [f"target_{index:03d}" for index in range(1, len(targets) + 1)]
    if phase == "prepare":
        prepare(work, job, targets, names)
    elif phase == "export":
        export(work, job, targets, names)
    else:
        infer(phase, work, job, names)


if __name__ == "__main__":
    main()
