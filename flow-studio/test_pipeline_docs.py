from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit

from PIL import Image

from local_server import library_write_lock
import pipeline_docs as docs


class DocumentTests(unittest.TestCase):
    def setUp(self):
        parent = tempfile.TemporaryDirectory()
        self.addCleanup(parent.cleanup)
        self.root = Path(parent.name) / "pipeline-docs"

    def save(self, text, revision, key="hand-seg"):
        return docs.save_doc(self.root, key, dict(text=text, expectedRevision=revision), library_write_lock)

    def test_isolated_revisions_and_atomic_writes(self):
        self.assertEqual(docs.read_doc(self.root, "hand-seg"), dict(text="", revision=0, updatedAt=None))
        self.assertFalse(self.root.exists())
        record = self.save("初版\n- 说明", 0)
        self.assertEqual(record, docs.read_doc(self.root, "hand-seg"))
        self.assertTrue(record["updatedAt"])
        self.assertEqual(docs.read_doc(self.root, "other")["revision"], 0)
        def concurrent(text):
            try:
                self.save(text, 1)
            except docs.RevisionConflict as exc:
                return exc.revision
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(concurrent, ("第二版", "另一个窗口")))
        self.assertCountEqual(results, [None, 2])
        self.assertEqual(docs.read_doc(self.root, "hand-seg")["revision"], 2)
        self.assertEqual(self.save("", 2)["text"], "")
        self.assertEqual(self.save("独立说明", 0, "../另外一条")["revision"], 1)
        self.assertEqual(sorted(len(p.name) for p in self.root.iterdir()), [32, 32])

    def test_invalid_document_preserves_saved_version(self):
        record = self.save("原文", 0)
        for body in (dict(text=[], expectedRevision=1), dict(text="x", expectedRevision=True),
                     dict(text="x"), dict(text="数" * (docs.MAX_TEXT_BYTES//3+1), expectedRevision=1)):
            with self.assertRaises(ValueError):
                docs.save_doc(self.root, "hand-seg", body, library_write_lock)
        self.assertEqual(docs.read_doc(self.root, "hand-seg"), record)

    def test_upload_and_read_all_formats_with_pipeline_isolation(self):
        key = "笔记 A&B"
        for kind, (ext, mime) in docs.IMAGE_TYPES.items():
            with self.subTest(kind=kind):
                buffer = BytesIO()
                Image.new("RGB", (20, 40), "green").save(buffer, format=kind)
                data = buffer.getvalue()
                result = docs.save_image(self.root, key, data)
                self.assertEqual(docs.save_image(self.root, key, data), result)
                query = parse_qs(urlsplit(result["url"]).query)
                self.assertEqual(query["id"], [key])
                filename, = query["file"]
                self.assertTrue(filename.endswith("."+ext))
                self.assertEqual(docs.read_image(self.root, key, filename), (data, mime))
                with self.assertRaises(FileNotFoundError):
                    docs.read_image(self.root, "other", filename)
        self.assertEqual(docs.read_doc(self.root, key)["revision"], 0)

    def test_rejects_bogus_images_and_path_traversal(self):
        image = BytesIO()
        Image.new("RGB", (50, 50), "red").save(image, format="PNG")
        for data in (b"", b"<svg></svg>", image.getvalue()[:35], b"x"*(docs.MAX_IMAGE_BYTES+1)):
            with self.assertRaises(ValueError):
                docs.save_image(self.root, "hand-seg", data)
        for filename in ("../document.json", "a"*64+".svg"):
            with self.assertRaises(ValueError):
                docs.read_image(self.root, "hand-seg", filename)
        self.assertFalse(self.root.exists())


if __name__ == "__main__":
    unittest.main()
