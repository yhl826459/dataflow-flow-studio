"""Associate instance masks across overlapping Workbench video windows."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
from scipy.optimize import linear_sum_assignment

from datasets_catalog import frame_windows


MIN_IOU = 0.5
MIN_SHARED_FRAMES = 3
MIN_MARGIN = 0.15


def read_rows(path):
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def load_mask(path, shape):
    mask = np.load(path, mmap_mode="r", allow_pickle=False)
    if mask.shape != shape or mask.dtype not in (np.dtype("uint8"), np.dtype("bool")):
        raise ValueError(f"融合掩码的帧数、分辨率或类型不匹配：{path}")
    return mask


def compare(left, right):
    """A frame with two empty predictions supplies no identity evidence."""
    both = left.any(axis=(1, 2)) & right.any(axis=(1, 2))
    scores = np.zeros(len(left))
    for i in np.flatnonzero(both):
        a, b = left[i] != 0, right[i] != 0
        scores[i] = np.count_nonzero(a & b) / np.count_nonzero(a | b)
    score = float(np.median(scores[both])) if both.sum() >= MIN_SHARED_FRAMES else 0.
    return score, scores


def match(left, right, overlap, width, height):
    if not left or not right:
        return {}
    a = [load_mask(x["mask"], (x["frames"], height, width))[-overlap:] for x in left]
    b = [load_mask(x["mask"], (x["frames"], height, width))[:overlap] for x in right]
    return match_shared_frames(a, b)


def match_shared_frames(a, b):
    """Run the same one-to-one assignment on already aligned shared-frame masks."""
    if not a or not b:
        return {}
    pairs = [[compare(x, y) for y in b] for x in a]
    scores = np.array([[value for value, _ in row] for row in pairs])
    weights = np.zeros((len(a), len(b) + len(a)))
    for i, j in np.argwhere(scores >= MIN_IOU):
        alternatives = np.r_[scores[i, :j], scores[i, j+1:], scores[:i, j], scores[i+1:, j]]
        if scores[i, j] - alternatives.max(initial=0.) >= MIN_MARGIN:
            weights[i, j] = scores[i, j]
    rows, cols = linear_sum_assignment(weights, maximize=True)
    return {int(j): (int(i), pairs[i][j]) for i, j in zip(rows, cols) if weights[i, j] > 0}


def seam_offset(per_frame):
    overlap = len(per_frame)
    return max(range(1, overlap), key=lambda k: (min(per_frame[k-1:k+1]), -abs(k-overlap//2)))


def frame_ranges(flags, offset):
    """Return zero-based half-open ranges on the video timeline."""
    changes = np.flatnonzero(np.diff(np.r_[False, flags, False]))
    return [[int(a)+offset, int(b)+offset] for a, b in changes.reshape(-1, 2)]


def fuse_masks(out, prepared_input, *, refined):
    out, prepared_input = Path(out), Path(prepared_input)
    meta = json.loads((prepared_input.parent / "preparation.json").read_text(encoding="utf-8"))
    plan, info = meta["plan"], meta["sourceInfo"]
    overlap, count, first = plan["overlap"], plan["count"], plan["first"]
    windows = list(frame_windows(count, overlap))
    width, height = info["width"], info["height"]
    inputs = read_rows(prepared_input)
    result = read_rows(out / ("final_masks.jsonl" if refined else "dataflow_step2.jsonl"))
    result_by_id = {row["sequence_id"]: row for row in result}
    input_ids = [row["sequence_id"] for row in inputs]
    if (len(input_ids) != len(windows) or len(result) != len(result_by_id)
            or len(set(input_ids)) != len(input_ids) or set(input_ids) != result_by_id.keys()):
        raise ValueError("融合所需的片段结果与输入清单不一致。")
    tracks, reviews, review_notes = [], [], []
    left = []
    for index, (sequence, (start, frames)) in enumerate(zip(input_ids, windows, strict=True), 1):
        row = result_by_id[sequence]
        right = []
        if refined and not row["targets"]:
            review_notes.append(dict(part=index, localId="—", firstFrame=first+start,
                                     lastFrame=first+start+frames-1, reason="未检出目标，无可评分掩码"))
        for item in row["targets" if refined else "segments"]:
            selection = {}
            note = dict(part=index, localId=item["id"], firstFrame=first+start, lastFrame=first+start+frames-1)
            if refined:
                # The gate retains the first highest mean at the retry limit, including its
                # actual mask and rewritten phrase. ABS continues to use the original phrase.
                best = item["retained"]
                fallback = item["status"] == "below_threshold" and item.get("reason") == "retry_limit"
                if (item["status"] != "passed" and not fallback) or not best or best["mean"] is None:
                    reason = ("无有效 ABS 均分" if not any(r["mean"] is not None for r in item["history"])
                              else "评分或改词中止")
                    review_notes.append(dict(note, reason=reason+"，该目标待复核"))
                    continue
                mask, phrase = best["segment"]["mask"], best["segmentation_phrase"]
                selection = dict(selectedRound=best["round"], selectedMean=best["mean"],
                                 selection="best_mean" if fallback else "passed")
            else:
                mask, phrase = item["mask"], item["phrase"]
            if not load_mask(mask, (frames, height, width)).any():
                review_notes.append(dict(note, reason="所选掩码全段为空"))
                continue
            if selection.get("selection") == "best_mean":
                review_notes.append(dict(note, reason=f"未过评分门槛，采用 round_{best['round']:02d}，均分 {best['mean']:.2f}"))
            right.append(dict(part=index, sequenceId=sequence, localId=item["id"], phrase=phrase, mask=mask,
                              start=start, frames=frames, **selection))
        pairs = match(left, right, overlap, width, height) if index > 1 else {}
        for j, item in enumerate(right):
            if j in pairs:
                i, (score, per_frame) = pairs[j]
                seam = seam_offset(per_frame)
                track = left[i]["track"]
                item.update(seam=start+seam, matchIoU=round(score, 4))
            else:
                track = len(tracks)
                tracks.append([])
            item["track"] = track
            tracks[track].append(item)
        if index > 1 and (not pairs or len(pairs) != len(left) or len(pairs) != len(right)):
            matched_left = {i for i, _ in pairs.values()}
            reviews.append({
                "left": index - 1, "right": index, "firstFrame": first + start, "frames": overlap,
                "reason": ("至少一侧无可用掩码" if not left or not right else "部分实例身份无法确认"),
                "leftTracks": [i["track"] + 1 for j, i in enumerate(left) if j not in matched_left],
                "rightTracks": [i["track"] + 1 for j, i in enumerate(right) if j not in pairs],
            })
        left = right

    folder = out / "whole_video_masks"
    with TemporaryDirectory(prefix=".fusion-", dir=out) as temporary:
        stage = Path(temporary) / "whole_video_masks"
        stage.mkdir()
        exported = []
        covered = np.zeros(count, dtype=bool)
        for track_id, parts in enumerate(tracks, 1):
            filename = f"track_{track_id:04d}.npy"
            dest = np.lib.format.open_memmap(stage / filename, mode="w+", dtype=np.uint8,
                                            shape=(count, height, width))
            visible = np.zeros(count, dtype=bool)
            for part in parts:
                start, frames = part["start"], part["frames"]
                mask = load_mask(part["mask"], (frames, height, width)) != 0
                keep = mask.any(axis=(1, 2))
                begin = part.get("seam", start) - start
                # Retain the previous contour before the seam where it exists.
                prior = visible[start:start+frames]
                keep[:begin] &= ~prior[:begin]
                dest[start + np.flatnonzero(keep)] = mask[keep]
                prior |= keep
            dest.flush()
            del dest
            covered |= visible
            exported.append({"trackId": track_id, "mask": filename,
                             "withoutMask": frame_ranges(~visible, first),
                             "parts": [{**{k: p[k] for k in ("part", "sequenceId", "localId", "phrase", "mask",
                                                             "selectedRound", "selectedMean", "selection") if k in p},
                                        **({"seamFrame": p["seam"]+first, "matchIoU": p["matchIoU"]}
                                           if "seam" in p else {})} for p in parts]})
        quality = dict(emptyFrameRanges=frame_ranges(~covered, first), reviewNotes=review_notes)
        (stage / "tracks.json").write_text(json.dumps(
            {"startFrame": first, "frameCount": count, "fps": 30, "width": width, "height": height,
             "overlap": overlap, "withoutMaskRangeFormat": "[start,end)",
             "tracks": exported, "reviewSeams": reviews, **quality}, ensure_ascii=False,
            indent=2) + "\n", encoding="utf-8")
        stage.rename(folder)
    return {"tracks": len(tracks), "reviewSeams": reviews, "path": str(folder), **quality}
