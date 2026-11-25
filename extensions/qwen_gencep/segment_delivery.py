"""Collect prompts, gate decisions and ABS observations before discarding segment work."""

import json
from pathlib import Path

from datasets_catalog import frame_windows
from score_feedback import frame_scores, read_jsonl


def indexed(path):
    rows = read_jsonl(path)
    by_id = {row["sequence_id"]: row for row in rows}
    if len(by_id) != len(rows):
        raise ValueError(f"Duplicate segment in {path}")
    return by_id


def remember_branches(rounds, clips):
    for rnd in rounds:
        group = rnd.get("group")
        number = rnd["number"]
        evidence = set()
        for clip in clips:
            if group and group != ("first-part" if clip["part"] == 1 else "remaining-parts"):
                continue
            if not clip.get("decisions") or number >= len(clip["rounds"]):
                continue
            if number + 1 < len(clip["rounds"]):
                evidence.add("retry")
            for idx, decision in enumerate(clip["decisions"]):
                history = [r["round"] for r in clip["rounds"] if "gateFeedback" in r["targets"][idx]]
                if number == history[-1]:
                    evidence.add("pass" if decision["status"] == "passed" else "review")
        rnd["branches"] = sorted(evidence)


def round_details(row, number, folder, work, frames, start):
    targets = row["targets"]
    segments = row["segments"]
    if [{k: s[k] for k in ("id", "phrase")} for s in segments] != targets:
        raise ValueError("Segmentation and scoring targets disagree")
    absolute = row.get("absolute", {"status": "disabled"})
    details = {"round": number, "absStatus": absolute["status"], "targets": []}
    observations = {item["id"]: [] for item in targets}
    if absolute["status"] == "scored":
        # Historical runs were moved one level down into .run after completion.
        path = Path(absolute["scores"])
        if not path.is_relative_to(work):
            path = work / path.relative_to(work.parent)
        if (path.resolve(strict=True) != path or not path.is_relative_to(folder / row["sequence_id"])):
            raise ValueError(f"Unexpected ABS result location: {path}")
        score = json.loads(path.read_text(encoding="utf-8"))
        _, every = frame_scores(path, targets, frames)
        details.update(model=score["model"], every=every)
        for record in score["records"]:
            sample = dict(frame=record["frame"], videoFrame=start + record["frame"], score=record["genception"],
                          visibility=record.get("visibility"), status=record["score_status"]["genception"])
            for key in ("reason", "rationale", "feedback"):
                if key in record:
                    sample[key] = record[key]
            observations[record["id"]].append(sample)
    elif absolute["status"] not in ("disabled", "prepared", "no_targets") or (
            absolute["status"] == "no_targets" and targets):
        raise ValueError(f"Unexpected ABS status: {absolute['status']}")
    for seg in segments:
        samples = observations[seg["id"]]
        values = [s["score"] for s in samples if s["score"] is not None]
        details["targets"].append(dict(id=seg["id"], segmentationPhrase=seg.get("segmentation_phrase", seg["phrase"]),
                                       scoringPhrase=seg["phrase"], absSamples=samples,
                                       absMean=sum(values) / len(values) if values else None))
    return details


def collect_segments(work, result, source, flags, mask_sources):
    base = indexed(work / ("dataflow_step3.jsonl" if "--abs" in flags or "--abs-prepare-only" in flags
                           else "dataflow_step2.jsonl"))
    refined = "--refine" in flags
    final = indexed(work / "final_masks.jsonl") if refined else {}
    inputs = list(indexed(source))
    if set(base) != set(inputs) or (refined and set(final) != set(inputs)):
        raise ValueError("Delivery and input segment IDs disagree")
    retries = {}
    fused = {(p["sequenceId"], p["localId"]): p for track in result.get("tracks", []) for p in track["parts"]}
    clips = []
    for i, (name, (offset, frames)) in enumerate(zip(inputs, frame_windows(result["frameCount"], result["overlap"]),
                                                    strict=True), 1):
        row = base[name]
        if Path(name).name != name or name in ("", ".", "..") or row["video_info"]["frames"] != frames:
            raise ValueError("Invalid segment identity or length")
        group = work if len(inputs) == 1 else work / ("first-part" if i == 1 else "remaining-parts")
        start = result["startFrame"] + offset
        def describe(scored, number, folder):
            details = round_details(scored, number, folder, work, frames, start)
            for segment in scored["segments"]:
                path = Path(segment["mask"])
                if path.resolve(strict=True) != path or not path.is_relative_to(group) or name not in path.relative_to(group).parts[:-1]:
                    raise ValueError(f"Mask outside this segment: {path}")
                mask_sources[i, number, segment["id"]] = path
            return details

        rounds = [describe(row, 0, group)]
        clip = dict(part=i, sequenceId=name, firstFrame=start, lastFrame=start + frames - 1,
                    task=row["task"], initialTargets=row["targets"], rounds=rounds)
        if refined:
            decision = final[name]
            states = decision["targets"]
            if [(s["id"], s["scoring_phrase"]) for s in states] != [(s["id"], s["phrase"]) for s in row["targets"]]:
                raise ValueError("Final gate and initial targets disagree")
            clip.update(status=decision["status"], threshold=decision["threshold"], decisions=[])
            last = max((s["history"][-1]["round"] for s in states), default=0)
            for number in range(1, last + 1):
                folder = group / "refinement" / f"round_{number:02d}"
                if folder not in retries:
                    retries[folder] = indexed(folder / "retry_step2.jsonl")
                rounds.append(describe(retries[folder][name], number, folder))
            for idx, state in enumerate(states):
                used = []
                for number, item in enumerate(state["history"]):
                    target = rounds[number]["targets"][idx]
                    if (number != item["round"] or target["id"] != state["id"] or
                            target["segmentationPhrase"] != item["segmentation_phrase"]):
                        raise ValueError("Prompt history disagrees with the actual scored round")
                    target["gateFeedback"] = {key: item[key] for key in
                                              ("mean", "low_intervals", "missing_reference_frames", "context_rgb_frames")
                                              if key in item}
                    used.append(number)
                retained = state["retained"]["round"]
                if retained not in used:
                    raise ValueError("Retained round missing from gate history")
                part = fused.get((name, state["id"]))
                clip["decisions"].append(dict(id=state["id"], status=state["status"], reason=state.get("reason"),
                                              retainedRound=retained, referenceFrames=state["reference_frames"],
                                              acceptedForFusion=part is not None,
                                              fusionSelection=part.get("selection") if part else None))
        clips.append(clip)
    return clips


def round_files(root, clips, *, verify=False):
    """Write or verify each round's target descriptions and scores."""
    def document(path, data):
        if verify:
            if path.is_symlink() or json.loads(path.read_text(encoding="utf-8")) != data:
                raise ValueError(f"Round document failed read-back: {path}")
        else:
            with path.open("x", encoding="utf-8") as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write("\n")

    index = []
    for clip in clips:
        part = clip["part"]
        entry = {key: value for key, value in clip.items() if key not in ("initialTargets", "rounds")}
        entry["rounds"] = []
        previous = {}
        for rnd in clip["rounds"]:
            number = rnd["round"]
            relative = Path("segments") / f"part_{part:03d}" / f"round_{number:02d}"
            folder = root / relative
            if verify:
                if folder.resolve(strict=True) != folder:
                    raise ValueError(f"Unexpected round directory: {folder}")
            else:
                folder.mkdir(parents=True)
            prompts, scores = [], []
            for i, target in enumerate(rnd["targets"], 1):
                identity = target["id"]
                phrase = target["segmentationPhrase"]
                prompt = dict(id=identity, segmentationPhrase=phrase, scoringPhrase=target["scoringPhrase"],
                              preview=f"target_{i:03d}_preview.mp4")
                if number:
                    prompt["previousPhrase"] = previous[identity]
                    prompt["changed"] = previous[identity] != phrase
                previous[identity] = phrase
                prompts.append(prompt)
                scores.append({k: target[k] for k in ("id","scoringPhrase","absMean","absSamples","gateFeedback") if k in target})
            header = dict(part=part, firstFrame=clip["firstFrame"], lastFrame=clip["lastFrame"], round=number)
            document(folder / "prompts.json", dict(header, targets=prompts))
            document(folder / "abs_scores.json", {**header, **{k:v for k,v in rnd.items() if k != "targets"}, "targets":scores})
            entry["rounds"].append(dict(round=number, directory=relative.as_posix()))
        index.append(entry)
    return index
