"""Verify per-round previews use the scored masks, including rejected retries."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "extensions/qwen_gencep"))
from segment_delivery import collect_segments, round_files
from score_feedback import write_jsonl
from video_delivery import round_media


class TestRoundDelivery(unittest.TestCase):
    def test_short_sequence_lets_native_create_its_work_directory(self):
        import workbench_batch as batch
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            source, public = root/"input.jsonl", root/"output"
            write_jsonl(source, [{"sequence_id":"only"}])
            def native_main():
                work = Path(sys.argv[sys.argv.index("--out")+1])
                self.assertEqual(work, public/".run")
                work.mkdir()
            argv = ["worker","--input",str(source),"--out",str(public),"--workbench-compact","--workbench-fuse"]
            with (patch.object(sys,"argv",argv), patch.object(batch.native,"main",native_main),
                  patch.object(batch,"finish_fusion"), patch("video_delivery.verify_delivered_work")):
                batch.workbench_main()
            self.assertTrue((public/".run").is_dir())

    def test_round_rgb_and_comparisons_follow_the_scored_masks(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            src = root / "input.mkv"
            subprocess.run(["ffmpeg","-loglevel","error","-f","lavfi","-i","color=red:s=64x64:r=30",
                            "-frames:v","3","-c:v","ffv1",str(src)],check=True)
            manifest = root / "input.jsonl"
            write_jsonl(manifest,[dict(sequence_id="a",video_path=str(src))])
            rounds, sources = [], {}
            for n in range(2):
                folder = root / f"round_{n:02d}"
                folder.mkdir()
                rounds.append(dict(round=n,directory=folder.name))
                arr = np.zeros((3,64,64),dtype=np.uint8)
                arr[:,:,32*n:32*(n+1)] = 1
                sources[1,n,"a"] = root/f"scored_{n}.npy"
                np.save(sources[1,n,"a"],arr)
                (folder/"prompts.json").write_text(json.dumps(dict(targets=[dict(id="a")])))
            index = [dict(part=1,sequenceId="a",firstFrame=73,lastFrame=75,rounds=rounds)]
            result = dict(fps=30,width=64,height=64)
            round_media(root,index,result,manifest,sources)
            round_media(root,index,result,manifest,sources,verify=True)
            for n,rnd in enumerate(rounds):
                folder = root/rnd["directory"]
                self.assertNotIn("rgb", rnd)
                self.assertEqual({p.name for p in folder.iterdir()}, {"prompts.json", "target_001_preview.mp4"})
                video = folder/rnd["previews"][0]["file"]
                raw = subprocess.check_output(["ffmpeg","-loglevel","error","-i",str(video),"-frames:v","1",
                                               "-f","rawvideo","-pix_fmt","rgb24","pipe:1"])
                frame = np.frombuffer(raw,dtype=np.uint8).reshape(100,192,3)
                self.assertGreater(frame[68,16,0],220)
                self.assertGreater(frame[68,64+16+32*n,1],150)
                self.assertLess(frame[68,64+48-32*n,1],20)

    def test_rejected_retries_and_unchanged_second_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            work = root / ".run"
            targets = [dict(id=k, phrase=k) for k in ("cup", "lid")]
            states = [dict(id=t["id"], scoring_phrase=t["phrase"], history=[], reference_frames=[0],
                           final_mask=None, status="below_threshold", retained={"round": 1}) for t in targets]
            states[1].update(status="passed", retained={"round": 0})
            for n in range(3):
                folder = work if n == 0 else work / "refinement" / f"round_{n:02d}"
                seq = folder / "seq"
                seq.mkdir(parents=True)
                segments, records = [], []
                for idx, target in enumerate(targets):
                    phrase = ("cup", "red cup", "cup with handle")[n] if idx == 0 else "lid"
                    mask = (seq if idx == 0 else work / "seq") / (target["id"] + ".npy")
                    if not mask.exists():
                        arr = np.zeros((1, 2, 3), dtype=np.uint8)
                        arr[0, idx, n] = 1
                        np.save(mask, arr)
                    segments.append(dict(target, segmentation_phrase=phrase, mask=str(mask)))
                    records.append(dict(id=target["id"], prompt=target["phrase"], frame=0, genception=6+n,
                                        visibility="visible", score_status={"genception":"scored"}))
                    if idx == 0 or n == 0:
                        states[idx]["history"].append(dict(round=n, segmentation_phrase=phrase, mean=6+n))
                scores = seq / "scores.json"
                scores.write_text(json.dumps(dict(complete=True,protocol="local-absolute-black-v1",
                                                  model="test",candidate_sources=["genception"],every=6,records=records)))
                row = dict(sequence_id="seq", task="objects", video_info={"frames":1}, targets=targets,
                           absolute={"status":"scored","scores":str(scores)}, segments=segments)
                write_jsonl(folder / ("retry_step2.jsonl" if n else "dataflow_step3.jsonl"), [row])
            states[1]["final_mask"] = str(work / "seq/lid.npy")
            write_jsonl(work / "final_masks.jsonl", [dict(sequence_id="seq", status="needs_review", threshold=7, targets=states)])
            source = root / "input.jsonl"
            write_jsonl(source, [{"sequence_id":"seq"}])
            result = dict(startFrame=5,frameCount=1,overlap=8,width=3,height=2)
            sources = {}
            clips = collect_segments(work, result, source, ["--abs","--refine"], sources)
            idx = round_files(root, clips)
            self.assertEqual(round_files(root, clips, verify=True), idx)
            for n in range(3):
                folder = root / idx[0]["rounds"][n]["directory"]
                a, b = json.loads((folder/"prompts.json").read_text())["targets"]
                self.assertEqual(a["segmentationPhrase"], ("cup","red cup","cup with handle")[n])
                self.assertEqual(a["preview"],"target_001_preview.mp4")
                self.assertNotIn("mask", a)
                self.assertEqual(np.load(sources[1,n,"cup"])[0,0,n], 1)
                if n:
                    self.assertTrue(a["changed"])
                    self.assertFalse(b["changed"])
                self.assertEqual(json.loads((folder/"abs_scores.json").read_text())["targets"][0]["absMean"],6+n)
            (folder/"prompts.json").write_text("{}")
            with self.assertRaises(ValueError):
                round_files(root, clips, verify=True)
