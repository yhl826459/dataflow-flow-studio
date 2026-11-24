import csv
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import datasets_catalog as ds

SHARED_PACK = "random-shared-20261102"


class CatalogTests(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.base = Path(workspace.name).resolve()
        self.root = self.base / "datasets"
        self.cache = self.base / "cache"
        self.root.mkdir()

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "requires FFmpeg")
    def test_overlap_materializes_shared_frames_and_short_tail(self):
        source = self.root / "take.mkv"
        subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=64x48:r=30",
                        "-frames:v", "148", "-c:v", "ffv1", "-threads", "1", str(source)], check=True)
        selection = {"id": "video:take.mkv", "mode": "whole"}
        report = ds.inspect_selection(self.root, selection, overlap=8)
        self.assertEqual(report["clipPlan"], dict(startFrame=0, frameCount=148, parts=2, overlap=8))
        path = ds.materialize(self.root, selection, self.cache, report["fingerprint"], overlap=8)
        rows = [json.loads(line) for line in path.read_text().splitlines()]

        def frames(video):
            return subprocess.check_output(["ffmpeg", "-v", "error", "-i", video, "-f", "framemd5", "-"]
                                           ).splitlines()[10:]

        hashes = [[line.split(b",")[-1] for line in frames(row["video_path"])] for row in rows]
        original = [line.split(b",")[-1] for line in frames(str(source))]
        self.assertEqual(len(original), 148)
        self.assertEqual(hashes, [original[:81], original[73:]])

    def test_discovery_and_incomplete_sources(self):
        folder = self.root / "set" / "take" / "observation"
        folder.mkdir(parents=True)
        for name in ("front.mp4", "side.mp4", "depth.mp4", "overlay.mkv"):
            (folder/name).touch()
        empty = self.root / "empty"
        empty.mkdir()
        (self.root/"fish.mp4").touch()
        (self.root/"frames").mkdir()
        (self.root/"frames"/"0001.png").touch()
        entries = {e["id"] for e in ds.catalog(self.root)["sequences"]}
        self.assertIn("video:fish.mp4",entries)
        self.assertIn("folder:set/take",entries)
        report = ds.inspect_selection(self.root, {"id":"folder:set/take"})
        self.assertFalse(report["ready"])
        self.assertEqual({c["id"] for c in report["candidates"]},
                         {"set/take/observation/front.mp4","set/take/observation/side.mp4"})
        self.assertIn("缺少", ds.inspect_selection(self.root, {"id":"folder:empty"})["issues"][0])
        self.assertIn("图像帧",ds.inspect_selection(self.root, {"id":"folder:."})["issues"][0])
        self.assertFalse(ds.inspect_selection(self.root, {"id":"folder:../../secret"})["ready"])

    def test_manifest_paths_and_validation(self):
        self.root.joinpath("video.mp4").touch()
        self.root.joinpath("lists").mkdir()
        manifest = self.root/"lists"/"manifest.jsonl"
        rows = ['{"sequence_id":"A","video_path":"../video.mp4"}','{broken',
                '{"sequence_id":"B","video_path":"../../outside.mp4"}',
                '{"sequence_id":"C","video_path":"../video.mp4","task":"objects"}']
        manifest.write_text("\n".join(rows),encoding="utf-8")
        info = dict(width=80,height=64,fps=30,frames=30,duration=1)
        with patch.object(ds,"_probe",return_value=(info,True,30)):
            def check(line):
                return ds.inspect_selection(self.root,{"id":f"manifest:lists/manifest.jsonl:{line}"})
            self.assertTrue(check(1)["ready"])
            self.assertEqual(check(1)["sequenceName"], "A")
            self.assertIn("JSON",check(2)["issues"][0])
            self.assertFalse(check(3)["ready"])
            self.assertFalse(check(4)["ready"])
            selection = {"id": "manifest:lists/manifest.jsonl:4"}
            self.assertTrue(ds.inspect_selection(self.root, selection, task="objects")["ready"])

    def test_hand_and_object_manifests_use_distinct_input_caches(self):
        self.root.joinpath("take.mp4").write_bytes(b"synthetic RGB bytes")
        selection = {"id": "video:take.mp4"}
        info = dict(width=80, height=64, fps=30, frames=30, duration=1)
        with patch.object(ds, "_probe", return_value=(info, True, 30)):
            manifests = {}
            for task in ("hands", "objects"):
                result = ds.inspect_selection(self.root, selection, task=task)
                self.assertTrue(result["ready"])
                manifests[task] = ds.materialize(self.root, selection, self.cache, result["fingerprint"], task=task)
                self.assertEqual(json.loads(manifests[task].read_text(encoding="utf-8"))["task"], task)
            self.assertNotEqual(manifests["hands"], manifests["objects"])
            old = ds.inspect_selection(self.root, selection)
            with self.assertRaises(ValueError):
                ds.materialize(self.root, selection, self.cache, old["fingerprint"], task="objects")

    def test_missing_video_and_aligned_source_roots(self):
        (self.root/"empty").mkdir()
        (self.root/"empty"/"input.jsonl").touch()
        result = ds.inspect_selection(self.root,{"id":"manifest:empty/input.jsonl:1"})
        self.assertFalse(result["ready"])
        self.assertIn("清单为空",result["issues"][0])
        if os.name == "posix":
            outside = self.base/"external.mp4"
            outside.touch()
            (self.root/"linked.mp4").symlink_to(outside)
            self.root.joinpath("input.jsonl").write_text(
                '{"sequence_id":"A","video_path":"linked.mp4"}',encoding="utf8")
            self.assertFalse(ds.inspect_selection(self.root,{"id":"manifest:input.jsonl:1"})["ready"])
            link = self.base/"linked-root"
            link.symlink_to(self.root,target_is_directory=True)
            with self.assertRaises(ValueError):
                ds.catalog(link)

    def test_root_registry_and_old_selection_identity(self):
        dataflow = self.base / "DataFlow"
        self.assertEqual(ds.registered_root(dataflow), self.root)
        self.assertEqual(ds.registered_root(dataflow, "xuzhefeng"), ds.SHARED_DATASETS)
        self.assertEqual([s["id"] for s in ds.registered_roots(dataflow)], ["yanghonglei", "xuzhefeng"])
        for unknown in ("", "../../external", "other", ["xuzhefeng"]):
            with self.assertRaises(ValueError):
                ds.registered_root(dataflow, unknown)
        (self.root / "take.mp4").touch()
        info = dict(width=80, height=64, fps=30, frames=30, duration=1)
        with patch.object(ds, "_probe", return_value=(info, True, 30)) as probe:
            selection = dict(id="video:take.mp4")
            old = ds.inspect_selection(self.root, selection)
            new = ds.inspect_selection(self.root, dict(selection, sourceRoot="yanghonglei"))
            self.assertTrue(old["ready"], old)
            self.assertEqual(old["sequenceName"], "take")
            self.assertEqual(old["fingerprint"], new["fingerprint"])
            self.assertEqual(probe.call_count, 1)
            with self.assertRaises(ValueError):
                ds.inspect_selection(self.root, dict(selection, sourceRoot="xuzhefeng"))

    def shared_pack(self, semantics):
        shared = self.base / "shared-datasets"
        pack = shared / SHARED_PACK
        pack.mkdir(parents=True)
        (shared / (SHARED_PACK + "-rgb")).mkdir()
        rows = []
        videos = []
        for index, kind in enumerate(semantics):
            rel = f"samples/data/take{index}/front/observation/rgb.mkv"
            path = pack / rel
            path.parent.mkdir(parents=True)
            rows.append(dict(dataset="Samples", sequence=f"take{index}", view="front", path=rel,
                             media_semantics=kind, frame_count_declared="86", source_fps_declared="30"))
            videos.append(path)
        with (pack / "video-timing.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        return shared, videos

    def test_shared_sequences_use_catalog_semantics(self):
        kinds = ("dense_video", "sparse_ordered_frames_unknown_fps", "independent_images")
        shared, paths = self.shared_pack(kinds)
        for path in paths:
            path.touch()
        if os.name == "posix":
            for index, path in enumerate(paths):
                (shared / (SHARED_PACK + "-rgb") / f"sample{index}.mkv").symlink_to(path)
        with patch.object(ds, "SHARED_DATASETS", shared), patch.object(ds, "_probe") as probe:
            entries = ds.catalog(shared)["sequences"]
            self.assertEqual([entry["mediaSemantics"] for entry in entries], list(kinds))
            self.assertTrue(all(entry["group"] == "Samples" for entry in entries))
            for index, issue in ((1, "稀疏帧"), (2, "独立图片")):
                report = ds.inspect_selection(shared, dict(id=entries[index]["id"], sourceRoot="xuzhefeng"))
                self.assertFalse(report["ready"])
                self.assertIn(issue, report["issues"][0])
            probe.assert_not_called()
            paths[0].unlink()
            report = ds.inspect_selection(shared, dict(id=entries[0]["id"]))
            self.assertIn("缺失", report["issues"][0])
            probe.assert_not_called()
            (shared / "random-shared-20270101").mkdir()
            broken = ds.catalog(shared)["sequences"]
            self.assertEqual(len(broken), len(entries)+1)
            report = ds.inspect_selection(shared, dict(id="folder:random-shared-20270101"))
            self.assertIn("video-timing.csv", report["issues"][0])

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg and ffprobe required")
    def test_shared_index_clock(self):
        shared, (video,) = self.shared_pack(["dense_video"])
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=80x64:rate=1",
                        "-frames:v", "86", "-c:v", "ffv1", "-threads", "1", str(video)], check=True)
        def frame_hashes(path):
            result = subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(path), "-vsync", "0",
                                              "-f", "framemd5", "-"], text=True)
            return [line.split(",")[-1].strip() for line in result.splitlines() if not line.startswith("#")]
        originals = frame_hashes(video)
        with patch.object(ds, "SHARED_DATASETS", shared):
            entry, = ds.catalog(shared)["sequences"]
            for mode, start, counts in (("whole", 0, [81, 5]), ("clip", 2.7, [5])):
                selection = dict(id=entry["id"], sourceRoot="xuzhefeng", mode=mode, start=start)
                report = ds.inspect_selection(shared, selection)
                self.assertTrue(report["ready"], report)
                self.assertEqual(report["info"]["containerFps"], 1)
                self.assertEqual(report["info"]["fps"], 30)
                self.assertAlmostEqual(report["info"]["duration"], 86/30)
                manifest = ds.materialize(shared, selection, self.cache, report["fingerprint"])
                rows = [json.loads(line) for line in manifest.read_text().splitlines()]
                self.assertEqual([ds._probe(Path(row["video_path"]))[0]["frames"] for row in rows], counts)
                actual = [digest for row in rows for digest in frame_hashes(row["video_path"])]
                self.assertEqual(actual, originals if mode == "whole" else originals[81:])

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"),"ffmpeg and ffprobe required")
    def test_real_media_preparation(self):
        for rate, frames, expected in ((50,989,593),(30,351,351),(30,14,14),(30,82,82)):
            with self.subTest(rate=rate,frames=frames):
                source=self.root/f"seq-{frames}.mp4"
                subprocess.run(["ffmpeg","-nostdin","-v","error","-f","lavfi","-i",
                                f"testsrc2=size=80x64:rate={rate}","-frames:v",str(frames),
                                "-c:v","mpeg4","-threads","1",str(source)],check=True)
                sel={"id":"video:"+source.name}
                cached_before = list(self.cache.iterdir()) if self.cache.is_dir() else []
                initial=ds.inspect_selection(self.root,sel)
                self.assertEqual(initial["ready"],frames<=81)
                self.assertEqual(list(self.cache.iterdir()) if self.cache.is_dir() else [],cached_before)
                if frames>81:
                    sel["mode"]="whole"
                report=ds.inspect_selection(self.root,sel)
                self.assertTrue(report["ready"],report)
                manifest=ds.materialize(self.root,sel,self.cache,report["fingerprint"])
                manifest_rows=[json.loads(line) for line in manifest.read_text().splitlines()]
                counts=[ds._probe(Path(row["video_path"]))[0]["frames"] for row in manifest_rows]
                self.assertEqual(sum(counts),expected)
                self.assertTrue(all(1<=count<=81 for count in counts))
                self.assertEqual(len({r["sequence_id"] for r in manifest_rows}),len(manifest_rows))
                self.assertEqual(manifest,ds.materialize(self.root,sel,self.cache,report["fingerprint"]))
                clip={"id":sel["id"],"mode":"clip","start":(expected-3)/30}
                clipped=ds.inspect_selection(self.root,clip)
                self.assertTrue(clipped["ready"],clipped)
                path=ds.materialize(self.root,clip,self.cache,clipped["fingerprint"])
                info,_,_=ds._probe(Path(json.loads(path.read_text())["video_path"]))
                self.assertLessEqual(info["frames"],3)
                self.assertGreaterEqual(info["frames"],2)
                self.assertFalse(ds.inspect_selection(self.root,dict(clip,start=100))["ready"])
                with self.assertRaises(ValueError):
                    ds.materialize(self.root,sel,self.cache,"0"*64)
                old=source.stat()
                os.utime(source,ns=(old.st_atime_ns,old.st_mtime_ns+1000000000))
                with self.assertRaises(ValueError):
                    ds.materialize(self.root,sel,self.cache,report["fingerprint"])


if __name__=="__main__":
    unittest.main()
