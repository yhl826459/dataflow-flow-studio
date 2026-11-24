"""Publish per-round deliverables while the normal DataFlow work directory stays private."""

import json
from pathlib import Path
from shutil import copyfile
from tempfile import TemporaryDirectory

from score_feedback import read_jsonl


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sequence_folder(root, row):
    name = row["sequence_id"]
    folder = root / name
    if folder.parent != root or name in ("", ".", "..") or folder.is_symlink():
        raise ValueError(f"Invalid publication directory: {folder}")
    return folder


def publish_round(root, source, number):
    for row in read_jsonl(source):
        folder = sequence_folder(root, row)
        folder.mkdir(parents=True, exist_ok=True)
        destination = folder / f"round_{number:02d}"
        if destination.exists():
            raise FileExistsError(destination)
        segments = row["segments"]
        with TemporaryDirectory(dir=folder, prefix=".publishing-") as temp:
            stage = Path(temp) / destination.name
            stage.mkdir()
            prompts = []
            for index, segment in enumerate(segments, 1):
                prefix = "mask" if len(segments) == 1 else f"target_{index:03d}"
                preview = f"{prefix}_preview.mp4"
                copyfile(segment["preview"], stage / preview)
                prompts.append({
                    "id": segment["id"],
                    "segmentation_phrase": segment.get("segmentation_phrase", segment["phrase"]),
                    "scoring_phrase": segment["phrase"],
                    "preview": preview,
                })
            write_json(stage / "qwen_prompts.json", prompts)
            score = row["absolute"]
            if score["status"] == "no_targets":
                write_json(stage / "abs_scores.json", {"status": "no_targets"})
            else:
                source_scores = Path(score["scores"])
                if score["status"] != "scored" or not json.loads(source_scores.read_text())["complete"]:
                    raise ValueError(f"Incomplete ABS result: {source_scores}")
                copyfile(score["scores"], stage / "abs_scores.json")
            stage.rename(destination)


def publish_result(root, result):
    for row in read_jsonl(result):
        folder = sequence_folder(root, row)
        targets = []
        with TemporaryDirectory(dir=folder, prefix=".publishing-") as temp:
            stage = Path(temp)
            for index, state in enumerate(row["targets"], 1):
                retained = state["retained"]
                real_mask = Path(retained["segment"]["mask"])
                mask_name = "mask.npy" if len(row["targets"]) == 1 else f"target_{index:03d}_mask.npy"
                copyfile(real_mask, stage / mask_name)
                (stage / mask_name).rename(folder / mask_name)
                targets.append({
                    "id": state["id"], "status": state["status"], "reason": state.get("reason"),
                    "reference_frames": state["reference_frames"], "scoring_phrase": state["scoring_phrase"],
                    "retained_round": retained["round"], "retained_mask": mask_name,
                    "final_mask": mask_name if state["final_mask"] else None,
                    "rounds": [{key: item[key] for key in (
                        "round", "mean", "segmentation_phrase", "missing_reference_frames", "low_intervals"
                    )} for item in state["history"]],
                })
            write_json(stage / "result.json", {
                "sequence_id": row["sequence_id"], "status": row["status"], "threshold": row["threshold"],
                "max_retries": row["max_retries"], "targets": targets,
            })
            (stage / "result.json").rename(folder / "result.json")
