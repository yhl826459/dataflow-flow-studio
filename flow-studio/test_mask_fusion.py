import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from mask_fusion import fuse_masks, match


class MaskFusionTest(unittest.TestCase):
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
