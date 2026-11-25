import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from mask_fusion import fuse_masks, match


class MaskFusionTest(unittest.TestCase):
    def test_low_score_round_bridges_both_seams_and_exposes_empty_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            names = ["a", "b", "c"]
            source = root / "input.jsonl"
            source.write_text("".join(json.dumps({"sequence_id": n})+"\n" for n in names))
            (root/"preparation.json").write_text(json.dumps({
                "plan":{"overlap":8,"first":20,"count":160}, "sourceInfo":{"width":4,"height":2}}))
            rows = []
            for i, (name, n) in enumerate(zip(names, (81,81,14))):
                mask = np.ones((n,2,4), dtype=np.uint8)
                if i == 1:
                    mask[30] = 0
                path = root / (name+".npy")
                np.save(path, mask)
                best = dict(round=1, mean=6.9 if i==1 else 8, segmentation_phrase="selected "+name,
                            segment={"mask":str(path)})
                last = dict(best,round=2,mean=6,segmentation_phrase="rejected",segment={"mask":"must-not-read.npy"})
                item = dict(id=1,scoring_phrase="original",status="below_threshold" if i==1 else "passed",
                            reason="retry_limit" if i==1 else None, retained=best,
                            history=[best,last] if i==1 else [best], final_mask=None if i==1 else str(path))
                rows.append(dict(sequence_id=name,targets=[item]))
            rows[1]["targets"].append(dict(id=2,status="needs_review",retained={"mean":None},history=[{"mean":None}]))
            (root/"final_masks.jsonl").write_text("".join(json.dumps(r)+"\n" for r in rows))
            summary = fuse_masks(root,source,refined=True)
            self.assertEqual(summary["tracks"],1)
            self.assertEqual(summary["reviewSeams"],[])
            self.assertEqual(summary["emptyFrameRanges"],[[123,124]])
            info = json.loads((Path(summary["path"])/"tracks.json").read_text())
            parts = info["tracks"][0]["parts"]
            self.assertEqual([p["part"] for p in parts],[1,2,3])
            self.assertEqual((parts[1]["phrase"],parts[1]["selection"],parts[1]["selectedRound"]),
                             ("selected b","best_mean",1))
            self.assertEqual([n["localId"] for n in summary["reviewNotes"]],[1,2])
            self.assertIn("无有效",summary["reviewNotes"][1]["reason"])

    def test_tied_identity_candidates_are_not_assigned(self):
        with tempfile.TemporaryDirectory() as temp:
            mask = Path(temp) / "mask.npy"
            np.save(mask, np.ones((8, 2, 2), dtype=np.uint8))
            candidate = {"mask": mask, "frames": 8}
            self.assertEqual(match([candidate], [candidate, candidate], 8, 2, 2), {})

    def test_reordered_targets_and_unconfirmed_seam(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "input.jsonl"
            names = ("first", "second", "third")
            source.write_text("".join(json.dumps({"sequence_id": name}) + "\n" for name in names))
            (root / "preparation.json").write_text(json.dumps({
                "plan": {"overlap": 8, "first": 100, "count": 155},
                "sourceInfo": {"width": 8, "height": 4},
            }))
            rows = []
            for part, order, n in zip(names, ((1, 5), (5, 1), ()), (81, 81, 9)):
                segments = []
                for local_id, x in enumerate(order, 1):
                    mask = np.zeros((n, 4, 8), dtype=np.uint8)
                    mask[:, :, x:x+2] = 1
                    path = root / f"{part}-{local_id}.npy"
                    np.save(path, mask)
                    segments.append({"id": local_id, "phrase": "same phrase", "mask": str(path)})
                rows.append({"sequence_id": part, "segments": segments})
            (root / "dataflow_step2.jsonl").write_text("".join(json.dumps(row)+"\n" for row in rows))
            summary = fuse_masks(root, source, refined=False)
            output = Path(summary["path"])
            info = json.loads((output / "tracks.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["tracks"], 2)
            self.assertEqual([p["localId"] for p in info["tracks"][0]["parts"]], [1, 2])
            self.assertEqual(info["tracks"][0]["withoutMask"], [[254, 255]])
            self.assertEqual([(s["left"], s["right"], s["firstFrame"]) for s in summary["reviewSeams"]],
                             [(2, 3, 246)])
            mask = np.load(output / "track_0001.npy")
            self.assertEqual(mask.shape, (155, 4, 8))
            self.assertTrue(mask[:154, :, 1:3].all())
            self.assertFalse(mask[:, :, 5:].any())
            self.assertFalse(mask[-1].any())
