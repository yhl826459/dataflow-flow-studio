"""Private, revisioned descriptions and content-addressed images for each pipeline."""

from datetime import datetime, timezone
from hashlib import blake2s, sha256
from io import BytesIO
import json
import os
from pathlib import Path
import re
import struct
import tempfile
from urllib.parse import urlencode


MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_TEXT_BYTES = 1024 * 1024
IMAGE_TYPES = {"PNG": ("png", "image/png"), "JPEG": ("jpg", "image/jpeg"),
               "WEBP": ("webp", "image/webp"), "GIF": ("gif", "image/gif")}


class RevisionConflict(ValueError):
    def __init__(self, revision):
        self.revision = revision
        super().__init__("其他窗口已保存这份说明。请重新载入后合并改动。")


def _directory(base, pipeline_id):
    if (not isinstance(pipeline_id, str) or not pipeline_id.strip() or len(pipeline_id) > 256
            or any(ord(c) < 32 for c in pipeline_id)):
        raise ValueError("流水线 ID 无效。")
    try:
        key = blake2s(pipeline_id.encode("utf-8"), digest_size=16).hexdigest()
    except UnicodeEncodeError as exc:
        raise ValueError("流水线 ID 编码无效。") from exc
    path = Path(base) / key
    if path.is_symlink() or path.parent.is_symlink():
        raise OSError("流水线说明存储目录异常。")
    return path


def _write(path, content):
    fd, tmp = tempfile.mkstemp(prefix=".saving-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        Path(tmp).unlink(missing_ok=True)


def read_doc(base, pipeline_id):
    path = _directory(base, pipeline_id) / "document.json"
    if path.is_symlink():
        raise OSError("流水线说明存储文件异常。")
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(record, dict) or set(record) != {"revision", "updatedAt", "text"}
                or type(record["revision"]) is not int or record["revision"] < 1
                or not isinstance(record["text"], str) or not isinstance(record["updatedAt"], str)):
            raise ValueError
        return record
    except FileNotFoundError:
        return {"text": "", "revision": 0, "updatedAt": None}
    except (ValueError, RecursionError) as exc:
        raise OSError("已保存的流水线说明无法读取，原文件已保留。") from exc


def save_doc(base, pipeline_id, body, write_lock):
    if not isinstance(body, dict) or set(body) != {"text", "expectedRevision"}:
        raise ValueError("说明保存请求需要正文和修订号。")
    text, expected = body["text"], body["expectedRevision"]
    if type(expected) is not int or expected < 0:
        raise ValueError("说明修订号无效。")
    if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise ValueError("说明正文应为 1 MiB 以内的文本。")
    directory = _directory(base, pipeline_id)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / "document.json"
    with write_lock(path):
        revision = read_doc(base, pipeline_id)["revision"]
        if revision != expected:
            raise RevisionConflict(revision)
        record = dict(text=text, revision=revision+1, updatedAt=datetime.now(timezone.utc).isoformat())
        _write(path, json.dumps(record, ensure_ascii=False).encode("utf-8"))
        return record


def _image_type(data):
    from PIL import Image, ImageSequence

    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_IMAGE_BYTES:
        raise ValueError("请选择 10 MiB 以内的图片。")
    # Decode actual pixels, with a bounded working set even for a small compressed animation.
    try:
        with Image.open(BytesIO(data), formats=list(IMAGE_TYPES)) as image:
            kind = image.format
            image.verify()
        with Image.open(BytesIO(data), formats=[kind]) as image:
            total = 0
            for frame in ImageSequence.Iterator(image):
                size = frame.width * frame.height
                total += size
                if size > 48_000_000 or total > 160_000_000:
                    raise ValueError("图片分辨率或动画总像素过大，请缩小后再插入。")
                frame.load()
    except (OSError, SyntaxError, EOFError, struct.error, OverflowError, Image.DecompressionBombError) as exc:
        raise ValueError("图片无法完整解码。请选择有效的 PNG、JPEG、WebP 或 GIF 图片。") from exc
    return IMAGE_TYPES[kind]


def save_image(base, pipeline_id, data):
    directory = _directory(base, pipeline_id)
    extension, _ = _image_type(data)
    filename = sha256(data).hexdigest() + "." + extension
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = directory / filename
    if path.is_symlink():
        raise OSError("流水线说明图片存储文件异常。")
    _write(path, data)
    return {"url": "/api/pipeline-doc/image?" + urlencode({"id": pipeline_id, "file": filename})}


def read_image(base, pipeline_id, filename):
    directory = _directory(base, pipeline_id)
    if not isinstance(filename, str) or not re.fullmatch(r"[0-9a-f]{64}\.(?:png|jpg|webp|gif)", filename):
        raise ValueError("图片文件名无效。")
    path = directory / filename
    if path.is_symlink() or not path.is_file():
        raise FileNotFoundError("此流水线没有这张图片。")
    with path.open("rb") as stream:
        data = stream.read(MAX_IMAGE_BYTES + 1)
    digest, extension = filename.split(".")
    if len(data) > MAX_IMAGE_BYTES or sha256(data).hexdigest() != digest:
        raise OSError("说明图片内容已变化，请重新上传。")
    mime = next(mime for ext, mime in IMAGE_TYPES.values() if ext == extension)
    return data, mime
