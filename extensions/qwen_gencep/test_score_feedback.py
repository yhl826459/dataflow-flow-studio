"""Offline regression for the five-operator refinement path."""

from functools import partial
import json
from math import isclose
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pipeline import VideoSegmentationPipeline, retry_round, write_json
from run_progress import monitor_run
from score_feedback import context_rgb_frames, read_jsonl, run_refinement, write_jsonl


class FeedbackTest(unittest.TestCase):
    def test_context_selection(self):
        scores = {0: None, 6: 7, 12: 9, 18: 8, 24: 9, 30: 8, 48: 10, 66: 8, 72: 9}
        self.assertEqual(context_rgb_frames(scores, [{"samples": [{"frame": 72}]}]), [12, 30, 66])
        self.assertEqual(context_rgb_frames({0: 7, 6: None}, []), [])

    def test_hand_refinement(self):
        self.check_refinement("hands")

    def test_object_refinement(self):
        self.check_refinement("objects")

    def check_refinement(self, task):
        schedule = {
            "frozen": [[9, 9, 9, 9], [1, 1, 1, 1]],
            "improves": [[8, 7, None, 6], [8, 9, 0, 8]],
            "missing": [[8, 7, 5, None], [9, None, 10, 10]],
            "limit": [[6, 7, 7, 7], [7, 7, 7, 7], [4, 5, 6, 7]],
            "no-baseline": [[None, None, None, None]],
            "repeat": [[1, 1, 1, 1]],
        }
        segment_calls, qwen_calls = [], []
        info = dict(frames=24, height=48, width=64, fps=30)

        def generate(video, task, out):
            out.mkdir()
            result = out / "result.json"
            targets = [{"id": identity, "phrase": f"original {identity}"} for identity in schedule]
            write_json(result, {
                "input": {"video": video, "decoded_frames": 24, "requested_sampling_fps": 5},
                "objects": targets if video.endswith("sample-a.mkv") else [],
            })
            return result

        def segment(video, targets, video_info, out):
            self.assertTrue(video.endswith("sample-a.mkv"))
            self.assertEqual(video_info, info)
            segment_calls.append([target["id"] for target in targets])
            out.mkdir(parents=True)
            return [dict(target, mask=str(out / f"{target['id']}.npy")) for target in targets]

        def score(sequence, video, video_info, segments, out):
            self.assertEqual([s["id"] for s in segments], list(schedule))
            self.assertTrue(all(s["phrase"] == f"original {s['id']}" for s in segments))
            attempt = len(segment_calls) - 1
            by_id = {s["id"]: s for s in segments}
            if attempt:
                self.assertIn("revised", by_id["limit"]["segmentation_phrase"])
                self.assertIn("initial", by_id["frozen"]["mask"])
            if attempt == 2:
                self.assertIn("initial", by_id["missing"]["mask"])
                self.assertIn("round_01", by_id["improves"]["mask"])
            records = []
            for entry in segments:
                rounds = schedule[entry["id"]]
                frames = zip(range(0, 24, 6), rounds[min(attempt, len(rounds) - 1)])
                records.extend(dict(id=entry["id"], frame=frame, prompt=entry["phrase"], genception=value)
                               for frame, value in frames)
            out.mkdir(parents=True, exist_ok=True)
            result = out / "scores.json"
            write_json(result, dict(protocol="local-absolute-black-v1", complete=True, every=6,
                                    candidate_sources=["genception"], records=records))
            return dict(status="scored", scores=str(result))

        def refine(video, video_info, jobs, out):
            qwen_calls.append([job["id"] for job in jobs])
            self.assertTrue(all(job["task"] == task for job in jobs))
            contexts = {job["id"]: job["context_rgb_frames"] for job in jobs}
            if len(qwen_calls) == 1:
                expected = [0] if task == "objects" else []
                self.assertEqual(contexts, {"improves": expected, "missing": expected, "limit": [], "repeat": []})
                intervals_by_id = {job["id"]: [(part["start_frame"], part["end_frame"])
                                                for part in job["low_intervals"]] for job in jobs}
                self.assertEqual(intervals_by_id["improves"], [(6, 6), (18, 18)])
                self.assertEqual(intervals_by_id["missing"], [(6, 12)])
            else:
                self.assertEqual(contexts, {"limit": []})
                self.assertEqual(jobs[0]["used_phrases"], ["original limit", "revised limit 1"])
            return [dict(id=job["id"], phrase=" ORIGINAL, repeat! " if job["id"] == "repeat"
                         else f"revised {job['id']} {len(qwen_calls)}") for job in jobs]

        with TemporaryDirectory(prefix="df-feedback-test-") as temp:
            root = Path(temp) / "initial"
            root.mkdir()
            source = root / "input.jsonl"
            sequences = ["sample-a", "sample-empty"]
            write_jsonl(source, [dict(sequence_id=sequence, video_path=str(root / f"{sequence}.mkv"),
                                    task=task, video_info=info) for sequence in sequences])
            pipeline = VideoSegmentationPipeline(source, root, generate, segment, score)
            stages = ("qwen", "genception", "abs", "gate", "refiner")
            with monitor_run(root, stages, "demo", state_dir=Path(temp) / "status") as progress:
                pipeline.compile()
                pipeline.forward()
                result = run_refinement(root / "dataflow_step3.jsonl", root, threshold=7.0, refine=refine,
                                        retry=partial(retry_round, segment=segment, score=score))
            status = json.loads(progress.path.read_text(encoding="utf-8"))
            self.assertEqual(status["status"], "done")
            self.assertEqual([r["number"] for r in status["rounds"]], [0, 1, 2])
            for number, completed in [(0, stages), (2, ("genception", "abs", "gate"))]:
                actual = status["rounds"][number]["stages"]
                self.assertEqual({name: step["status"] for name, step in actual.items()},
                                 {name: "done" if name in completed else "skipped"
                                  for name in ("prompt", *stages)})
            rows = read_jsonl(result)
            self.assertEqual([row["sequence_id"] for row in rows], sequences)
            self.assertEqual(rows[1]["status"], "no_targets")
            states = {state["id"]: state for state in rows[0]["targets"]}
            self.assertEqual(len(states["frozen"]["history"]), 1)
            self.assertEqual(states["improves"]["status"], "passed")
            self.assertTrue(isclose(states["improves"]["retained"]["mean"], 25 / 3))
            self.assertEqual(states["improves"]["reference_frames"], [0, 6, 18])
            self.assertEqual(states["missing"]["status"], "needs_review")
            self.assertEqual(states["missing"]["history"][1]["missing_reference_frames"], [6])
            self.assertEqual(states["missing"]["retained"]["round"], 0)
            self.assertIsNone(states["missing"]["final_mask"])
            self.assertEqual(states["limit"]["status"], "below_threshold")
            self.assertEqual(states["limit"]["retained"]["round"], 1)
            self.assertEqual(states["no-baseline"]["reason"], "no_scored_baseline_frames")
            self.assertEqual(states["repeat"]["reason"], "repeated_segmentation_phrase")
            self.assertEqual(segment_calls, [list(schedule), ["improves", "missing", "limit"], ["limit"]])
            self.assertEqual(qwen_calls, [["improves", "missing", "limit", "repeat"], ["limit"]])
            self.assertTrue((root / "refinement/round_02/retry_step2.jsonl").is_file())
            self.assertTrue((root / "refinement/round_02/feedback_step2.jsonl").is_file())


if __name__ == "__main__":
    unittest.main()
