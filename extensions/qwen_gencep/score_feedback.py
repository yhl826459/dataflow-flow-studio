"""Score-based acceptance and prompt feedback for each GenCeption target."""

import json
import re
from pathlib import Path

from dataflow.core import OperatorABC
from dataflow.pipeline import PipelineABC
from dataflow.utils import OPERATOR_REGISTRY
from dataflow.utils.storage import FileStorage

from run_progress import new_round, operator_progress, report_work

MAX_RETRIES = 2
SOURCE = "genception"


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
                    encoding="utf-8")


def frame_scores(path, targets, frames):
    result = json.loads(Path(path).read_text(encoding="utf-8"))
    if (result.get("complete") is not True or result.get("protocol") != "local-absolute-black-v1"
            or result.get("candidate_sources") != [SOURCE]):
        raise ValueError(f"Expected a complete GenCeption ABS result: {path}")
    every = result["every"]
    if type(every) is not int or every <= 0:
        raise ValueError(f"Invalid ABS sampling interval: {path}")
    expected_frames = set(range(0, frames, every))
    prompts = {str(target["id"]): target["phrase"] for target in targets}
    scores = {identity: {} for identity in prompts}
    for record in result["records"]:
        identity, frame, score = record["id"], record["frame"], record[SOURCE]
        if (identity not in prompts or record["prompt"] != prompts[identity]
                or type(frame) is not int or frame not in expected_frames or frame in scores[identity]
                or (score is not None and (type(score) is not int or not 0 <= score <= 10))):
            raise ValueError(f"Unexpected ABS target, frame or score: {record!r}")
        scores[identity][frame] = score
    if any(set(series) != expected_frames for series in scores.values()):
        raise ValueError(f"ABS result omits sampled frames: {path}")
    return scores, every


def low_intervals(frames, scores, every, threshold):
    intervals = []
    for frame in frames:
        if scores[frame] > threshold:
            continue
        if not intervals or frame != intervals[-1][-1]["frame"] + every:
            intervals.append([])
        intervals[-1].append({"frame": frame, "score": scores[frame]})
    return [{"start_frame": group[0]["frame"], "end_frame": group[-1]["frame"], "samples": group}
            for group in intervals]


def context_rgb_frames(scores, intervals):
    low = {sample["frame"] for interval in intervals for sample in interval["samples"]}
    eligible = sorted(frame for frame, value in scores.items()
                      if value is not None and value > 7 and frame not in low)
    if len(eligible) <= 3:
        return eligible
    first, last = eligible[0], eligible[-1]
    middle = min(eligible[1:-1], key=lambda frame: abs(2 * frame - first - last))
    return [first, middle, last]


@OPERATOR_REGISTRY.register()
class MaskQualityGate(OperatorABC):
    def __init__(self, threshold, round_index):
        super().__init__()
        self.threshold = threshold
        self.round_index = round_index

    @operator_progress("gate")
    def run(self, storage, *, input_targets_key, input_segments_key, input_absolute_key,
            input_video_info_key, output_refinement_key):
        rows = storage.read("dict")
        for row in rows:
            targets = row[input_targets_key]
            if not targets:
                row[output_refinement_key] = []
                continue
            report_work(sequence=row["sequence_id"], detail="比较实例评分")
            absolute = row[input_absolute_key]
            if absolute["status"] != "scored":
                raise ValueError(f"ABS did not finish scoring {row['sequence_id']}")
            scores, every = frame_scores(absolute["scores"], targets, row[input_video_info_key]["frames"])
            if self.round_index == 0:
                states = [{
                    "id": target["id"], "scoring_phrase": target["phrase"], "status": "retry",
                    "reference_frames": sorted(frame for frame, value in scores[str(target["id"])].items()
                                               if value is not None),
                    "every": every, "history": [], "retained": None, "final_mask": None,
                } for target in targets]
                row[output_refinement_key] = states
            segments = row[input_segments_key]
            for state, segment in zip(row[output_refinement_key], segments, strict=True):
                if state["id"] != segment["id"] or state["scoring_phrase"] != segment["phrase"]:
                    raise ValueError(f"Refinement target mismatch: {segment['id']!r}")
                if state["status"] != "retry":
                    continue
                if every != state["every"]:
                    raise ValueError("ABS sampling interval changed during refinement")
                series, reference = scores[str(state["id"])], state["reference_frames"]
                missing = [frame for frame in reference if series[frame] is None]
                mean = (sum(series[frame] for frame in reference) / len(reference)
                        if reference and not missing else None)
                record = {
                    "round": self.round_index, "mean": mean, "segment": segment,
                    "scores": absolute["scores"],
                    "segmentation_phrase": segment.get("segmentation_phrase", segment["phrase"]),
                    "missing_reference_frames": missing,
                    "low_intervals": low_intervals(reference, series, every, self.threshold)
                    if mean is not None and mean <= self.threshold else [],
                }
                if row.get("task") == "objects" and record["low_intervals"]:
                    record["context_rgb_frames"] = context_rgb_frames(series, record["low_intervals"])
                state["history"].append(record)
                if mean is None:
                    state.update(status="needs_review",
                                 reason="reference_frame_is_na" if missing else "no_scored_baseline_frames")
                    if state["retained"] is None:
                        state["retained"] = record
                else:
                    state["retained"] = record
                    if mean > self.threshold:
                        state.update(status="passed", final_mask=segment["mask"])
                    elif self.round_index >= MAX_RETRIES:
                        state.update(status="below_threshold", reason="retry_limit",
                                     retained=max(state["history"], key=lambda item: item["mean"]))
        storage.write(rows)


def phrase_words(phrase):
    return re.findall(r"\w+", phrase.casefold())


@OPERATOR_REGISTRY.register()
class QwenPromptRefiner(OperatorABC):
    def __init__(self, out, refine):
        super().__init__()
        self.out = out
        self.refine = refine

    @operator_progress("refiner")
    def run(self, storage, *, input_sequence_key, input_video_key, input_video_info_key,
            input_refinement_key, output_revisions_key):
        rows = storage.read("dict")
        for row in rows:
            pending = [state for state in row[input_refinement_key] if state["status"] == "retry"]
            row[output_revisions_key] = revisions = []
            if not pending:
                continue
            jobs = [{
                "id": state["id"], "scoring_phrase": state["scoring_phrase"],
                "previous_phrase": state["retained"]["segmentation_phrase"],
                "used_phrases": [item["segmentation_phrase"] for item in state["history"]],
                "task": row.get("task"),
                "context_rgb_frames": state["retained"].get("context_rgb_frames", []),
                "mask": state["retained"]["segment"]["mask"],
                "low_intervals": state["retained"]["low_intervals"],
            } for state in pending]
            output = self.out / row[input_sequence_key] / "qwen_refinement"
            report_work(sequence=row[input_sequence_key], detail=f"改写 {len(jobs)} 个实例的提示词")
            proposed = self.refine(row[input_video_key], row[input_video_info_key], jobs, output)
            if [proposal["id"] for proposal in proposed] != [job["id"] for job in jobs]:
                raise ValueError(f"Qwen returned a different target order: {output}")
            for state, job, proposal in zip(pending, jobs, proposed, strict=True):
                phrase = proposal["phrase"]
                if not isinstance(phrase, str) or not phrase.strip():
                    raise ValueError(f"Empty Qwen refinement phrase: {output}")
                words = phrase_words(phrase)
                if not words or any(words == phrase_words(earlier) for earlier in job["used_phrases"]):
                    state.update(status="needs_review", reason="repeated_segmentation_phrase")
                    continue
                revisions.append(dict(id=state["id"], phrase=phrase))
        storage.write(rows)


class FeedbackPipeline(PipelineABC):
    def __init__(self, source, out, threshold, round_index, refine):
        super().__init__()
        self.storage = FileStorage(str(source), cache_path=str(out), file_name_prefix="feedback")
        self.gate = MaskQualityGate(threshold, round_index)
        self.refiner = QwenPromptRefiner(out, refine)

    def forward(self):
        self.gate.run(storage=self.storage.step(), input_targets_key="targets",
                      input_segments_key="segments", input_absolute_key="absolute",
                      input_video_info_key="video_info", output_refinement_key="refinement")
        self.refiner.run(storage=self.storage.step(), input_sequence_key="sequence_id",
                         input_video_key="video_path", input_video_info_key="video_info",
                         input_refinement_key="refinement", output_revisions_key="revisions")


def run_refinement(source, out, *, threshold, refine, retry, publish=None, completed_rounds=0):
    published = {}
    order = [row["sequence_id"] for row in read_jsonl(source)]
    for round_index in range(MAX_RETRIES + 1):
        round_dir = out / "refinement" / f"round_{round_index:02d}"
        if round_index >= completed_rounds:
            if round_index:
                new_round(round_index)
                source = retry(round_dir, pending)
            if publish is not None:
                publish(source, round_index)
            feedback = FeedbackPipeline(source, round_dir, threshold, round_index, refine)
            feedback.compile()
            feedback.forward()
        rows = read_jsonl(round_dir / "feedback_step2.jsonl")
        pending = []
        for row in rows:
            states = row["refinement"]
            if row["revisions"]:
                # The next ABS crop should include the retained masks, not the rejected trial masks.
                row["segments"] = [state["retained"]["segment"] for state in states]
                pending.append(row)
            else:
                published[row["sequence_id"]] = dict(
                    sequence_id=row["sequence_id"], video_path=row["video_path"],
                    threshold=threshold, max_retries=MAX_RETRIES,
                    status=("no_targets" if not states else
                            "passed" if all(state["status"] == "passed" for state in states)
                            else "needs_review"),
                    targets=states,
                )
        if not pending:
            break
    final = out / "final_masks.jsonl"
    write_jsonl(final, [published[sequence] for sequence in order])
    return final
