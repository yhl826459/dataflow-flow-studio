"""Discover hand-pipeline RGB inputs, validate the selected video and freeze its JSONL."""
from collections import defaultdict
import csv
from fractions import Fraction
from functools import lru_cache
from hashlib import sha256
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import tempfile


VIDEOS = {".mp4", ".mov", ".m4v", ".mkv", ".avi", ".webm"}
IMAGES = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff"}
MEDIA_DIRS = {"observation", "rgb", "color", "colour", "videos", "video", "frames", "images"}
NON_RGB = re.compile(r"(?:^|[^a-z0-9])(?:masks?|depth|overlays?|segmentations?|labels?|predictions?|preview|scores?|optical.flow|annotations?)(?:$|[^a-z0-9])", re.I)
MAX_FRAMES = 81
FUSION_OVERLAP = 8


def frame_windows(count, overlap=0):
    end = 0
    while end < count:
        start = end - overlap if end else 0
        end = min(start + MAX_FRAMES, count)
        yield start, end - start
DEMUXERS = "mov,matroska,webm,avi"
SHARED_DATASETS = Path("/data/xuzhefeng/video_object_phrases/datasets")


def registered_roots(dataflow_root):
    personal_root = os.environ.get("FLOW_STUDIO_DATASETS_ROOT") or str(Path(dataflow_root).parent / "datasets")
    return [
        dict(id="yanghonglei", label="个人数据集", path=personal_root),
        dict(id="xuzhefeng", label="xuzhefeng / video_object_phrases / datasets", path=str(SHARED_DATASETS)),
    ]


def registered_root(dataflow_root, key=None):
    for source in registered_roots(dataflow_root):
        if source["id"] == ("yanghonglei" if key is None else key):
            return Path(source["path"])
    raise ValueError("请选择已登记的数据集根目录。")


def _inside(root, path):
    # A registered tree may be shared with other users; links never extend the selected root.
    resolved = path.resolve()
    if resolved != Path(os.path.abspath(path)) or not resolved.is_relative_to(root):
        raise ValueError("输入必须是已登记数据集目录内的普通文件，不能通过符号链接引用。")
    return resolved


def _shared_pack_entries(root, pack):
    pack = _inside(root, pack)
    table = _inside(pack, pack / "video-timing.csv")
    entries = {}
    try:
        with table.open(encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            fields = {"dataset", "sequence", "view", "path", "media_semantics",
                      "frame_count_declared", "source_fps_declared"}
            if not fields.issubset(reader.fieldnames or ()):
                raise ValueError("数据集的 video-timing.csv 缺少素材路径或时间信息。")
            for row in reader:
                rel = PurePosixPath(row["path"] or "")
                if (rel.is_absolute() or ".." in rel.parts or ":" in str(rel) or "\\" in str(rel)
                        or rel.suffix.lower() not in VIDEOS or any(NON_RGB.search(p) for p in rel.parts)):
                    raise ValueError(f"video-timing.csv 的第 {reader.line_num} 行没有有效的原始 RGB 路径。")
                key = "video:" + (PurePosixPath(pack.name) / rel).as_posix()
                if key in entries:
                    raise ValueError(f"video-timing.csv 的第 {reader.line_num} 行重复登记了同一份素材。")
                group = row["dataset"] or rel.parts[0]
                label = " · ".join(s for s in (group, row["sequence"], row["view"]) if s)
                entries[key] = dict(id=key, label=label, group=group, files=[pack / rel], timing=row)
    except (OSError, UnicodeError, csv.Error) as exc:
        raise ValueError("无法读取共享数据集的 video-timing.csv，请检查清单及其读取权限。") from exc
    return entries


def _index(root):
    configured = Path(root)
    if configured.is_symlink() or not configured.is_dir() or not os.access(configured, os.R_OK | os.X_OK):
        raise ValueError("服务器数据集根目录不可用。")
    root = configured.resolve(strict=True)
    entries = {}
    pack_dirs = set()
    if root == SHARED_DATASETS.absolute():
        for pack in root.iterdir():
            if not re.fullmatch(r"random-shared-[0-9]{8}", pack.name) or pack.is_symlink() or not pack.is_dir():
                continue
            # The sibling -rgb directory is a flat mirror of the same assets.
            pack_dirs.update((pack.name, pack.name + "-rgb"))
            try:
                entries.update(_shared_pack_entries(root, pack))
            except ValueError as exc:
                key = "folder:" + pack.name
                entries[key] = dict(id=key, label=pack.name, group=pack.name, problem=str(exc))
    folders = defaultdict(lambda: {"files": [], "images": False, "excluded": False})

    def group(path):
        return path.parts[0] if len(path.parts) > 1 else root.name

    def add(key, label, rel, **details):
        entries[key] = dict(id=key, label=label, group=group(rel), **details)

    def owner(path):
        while path != root and path.name.casefold() in MEDIA_DIRS:
            path = path.parent
        return path

    def manifest(path):
        rel = path.relative_to(root)
        named = any(k in path.stem.lower() for k in ("input", "manifest", "sequences"))
        start_count = len(entries)
        def problem(line, reason):
            add(f"manifest:{rel.as_posix()}:{line}", f"{rel.as_posix()} · 第 {line} 行", rel, problem=reason)
        if path.stat().st_size > 8 * 1024 * 1024:
            problem(1, "此 JSONL 超过 8 MiB，请提供仅含输入路径、序列 ID 和任务的精简清单。")
            return True
        try:
            with path.open(encoding="utf-8-sig") as stream:
                for line_no, line in enumerate(stream, 1):
                    if not line.strip():
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        if named:
                            problem(line_no, "此行不是有效 JSON，请修正清单后重新选择。")
                            continue
                        return False
                    if not isinstance(row, dict) or not ("sequence_id" in row or "video_path" in row):
                        if named:
                            problem(line_no, "请补充此行的 sequence_id 和 video_path。")
                            continue
                        return False
                    named = True
                    key = f"manifest:{rel.as_posix()}:{line_no}"
                    name = str(row.get("sequence_id", ""))[:160] or f"第 {line_no} 行"
                    fields = {k: row.get(k) for k in ("sequence_id", "video_path", "task")}
                    add(key, f"{name} · {rel.as_posix()}", rel, manifest=fields, parent=path.parent)
        except (UnicodeError, OSError):
            if named:
                problem(1, "输入清单无法完整读取，请确认文件权限及 UTF-8 编码。")
        if named and len(entries) == start_count:
            problem(1, "清单为空，请至少添加一条含 sequence_id 和 video_path 的记录。")
        return len(entries) != start_count

    for directory, subdirs, names in os.walk(root, followlinks=False):
        here = Path(directory)
        if here == root and pack_dirs:
            subdirs[:] = [n for n in subdirs if n not in pack_dirs]
        subdirs[:] = sorted(n for n in subdirs if not n.startswith(".") and not (here/n).is_symlink()
                            and not NON_RGB.search(n))
        container = owner(here)
        has_media = False
        has_manifest = False
        for name in names:
            if name.startswith("."):
                continue
            path = here / name
            ext = path.suffix.lower()
            if ext not in VIDEOS and ext not in IMAGES and ext != ".jsonl":
                continue
            if ext in IMAGES and folders.get(container, {}).get("images"):
                continue
            if path.is_symlink() or not path.is_file():
                continue
            if NON_RGB.search(path.stem):
                if container != root:
                    folders[container]["excluded"] = True
                continue
            if ext == ".jsonl":
                has_manifest = manifest(path) or has_manifest
                continue
            has_media = True
            if container == root and ext in VIDEOS:
                rel = path.relative_to(root)
                add("video:"+rel.as_posix(), rel.as_posix(), rel, files=[path])
            elif ext in VIDEOS:
                folders[container]["files"].append(path)
            else:
                folders[container]["images"] = True
        if not has_media and not has_manifest and not subdirs and here != root:
            folders[container]
    for directory, details in folders.items():
        rel = directory.relative_to(root)
        add("folder:"+rel.as_posix(), rel.as_posix() if directory != root else root.name, rel, **details)
    return root, entries


def catalog(root):
    root, entries = _index(root)
    public = []
    for entry in entries.values():
        item = {k: entry[k] for k in ("id", "label", "group")}
        if "timing" in entry:
            item["mediaSemantics"] = entry["timing"]["media_semantics"]
        public.append(item)
    return {"root": str(root), "sequences": sorted(public, key=lambda e: (e["group"], e["label"], e["id"]))}


def _command(args, timeout):
    try:
        proc = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise ValueError(f"服务器缺少 {args[0]}，无法检查或准备 RGB 视频。") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValueError(f"{args[0]} 处理超时，请提供较短的视频或已准备好的清单。") from exc
    if proc.returncode or (args[0] == "ffprobe" and proc.stderr.strip()):
        detail = proc.stderr.decode("utf-8", errors="replace").strip().splitlines()
        raise ValueError("无法完整读取或转换 RGB 视频，请检查文件是否损坏。"
                         + (" "+detail[-1][:260] if detail else ""))
    return proc.stdout


def _probe(path):
    args = ["ffprobe", "-v", "error", "-protocol_whitelist", "file", "-format_whitelist", DEMUXERS,
            "-select_streams", "v", "-count_frames", "-show_entries",
            "stream=width,height,pix_fmt,avg_frame_rate,r_frame_rate,nb_read_frames,duration,duration_ts,time_base",
            "-of", "json", str(path)]
    streams = json.loads(_command(args, 180)).get("streams", [])
    if len(streams) != 1:
        raise ValueError("需要恰好一个 RGB 视频流，请先从多路文件中导出一个视角。")
    stream = streams[0]
    if stream.get("pix_fmt", "").startswith(("gray", "mono", "ya8", "ya16")):
        raise ValueError("视频为单通道图像，请补充原始彩色 RGB 视频。")
    try:
        avg, declared = (Fraction(stream[k]) for k in ("avg_frame_rate", "r_frame_rate"))
        frames, width, height = (int(stream[k]) for k in ("nb_read_frames", "width", "height"))
        if min(avg, declared, frames, width, height) <= 0:
            raise ValueError
        if stream.get("duration_ts") not in (None, "N/A"):
            duration = int(stream["duration_ts"]) * Fraction(stream["time_base"])
        elif stream.get("duration") not in (None, "N/A"):
            duration = Fraction(stream["duration"])
        else:
            duration = frames / avg
        if duration <= 0:
            raise ValueError
    except (KeyError, ValueError, ZeroDivisionError, TypeError, OverflowError) as exc:
        raise ValueError("缺少可校验的 RGB 帧数、帧率或画面尺寸，请补充完整视频。") from exc
    info = dict(width=width, height=height, fps=float(avg), frames=frames, duration=float(duration))
    # For CFR 30 the actual decoded frame count is authoritative even with rounded container times.
    exact_rate = avg == declared == 30
    expected = frames if exact_rate else max(1, math.floor(duration*30 + Fraction(1, 2)))
    return info, exact_rate, expected


def _stamp(path):
    st = path.stat()
    return [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns]


@lru_cache(maxsize=128)
def _probe_selected(path, stamp):
    # Repeated preflight and launch checks share the result only for identical file metadata.
    return _probe(path)


def _shared_clock(row, info):
    try:
        frames = int(row["frame_count_declared"])
        raw = row["source_fps_declared"]
        rate = Fraction(raw) if raw else None
        if frames != info["frames"] or (rate is not None and rate <= 0):
            raise ValueError
    except (TypeError, ValueError, ZeroDivisionError) as exc:
        raise ValueError("RGB 的实际帧数与清单不符，或原始帧率无效，请核对 video-timing.csv。") from exc
    return rate


def _sequence_name(entry):
    if "manifest" in entry:
        name = entry["manifest"]["sequence_id"]
        if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,159}", name)
                or re.fullmatch(r"\d+(?:\.\d*)?(?:[eE]-?\d+)?", name)):
            raise ValueError("请在清单中补充合法的 sequence_id（不能是纯数字）。")
        return name
    raw = entry["id"].split(":", 1)[1]
    raw = str(Path(raw).with_suffix("")) if entry["id"].startswith("video:") else raw
    name = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw).strip(".-")[:128]
    suffix = sha256(entry["id"].encode()).hexdigest()[:10]
    if (not name or not name[0].isalnum() or raw != name
            or re.fullmatch(r"\d+(?:\.\d*)?(?:[eE]-?\d+)?", name)):
        name = "seq-" + (name+"-" if name else "") + suffix
    return name


def _plan(root, selection, task, overlap=0):
    blank = dict(ready=False, fingerprint="", source="", info=None, issues=[], candidates=[],
                 needsPreparation=False, summary="", parts=0)
    if not isinstance(selection, dict) or set(selection)-{"id", "candidate", "mode", "start", "sourceRoot"}:
        raise ValueError("请选择已登记的数据序列。")
    if "sourceRoot" in selection:
        expected = "xuzhefeng" if Path(root).absolute() == SHARED_DATASETS.absolute() else "yanghonglei"
        if selection["sourceRoot"] != expected:
            raise ValueError("选中的序列与数据集根目录不匹配。")
    root, entries = _index(root)
    id_ = selection.get("id")
    entry = entries.get(id_) if isinstance(id_, str) else None
    if entry is None:
        blank["issues"].append("选中的序列已不存在，请刷新数据集列表。")
        return blank, None
    try:
        if entry.get("problem"):
            raise ValueError(entry["problem"])
        if "manifest" in entry:
            row = entry["manifest"]
            raw = row["video_path"]
            if not isinstance(raw, str) or not raw.strip():
                raise ValueError("清单缺少 video_path，请补充 RGB 视频路径。")
            if row["task"] not in (None, task):
                raise ValueError(f"清单标记的目标是 {row['task']}，请选择对应任务或使用 task={task} 的输入清单。")
            source = _inside(root, entry["parent"] / raw)
            if source.suffix.lower() not in VIDEOS or any(NON_RGB.search(p) for p in source.relative_to(root).parts):
                raise ValueError("清单应指向原始 RGB 视频，请补充有效的 video_path。")
            paths = [source] if source.is_file() else []
        else:
            paths = sorted(entry.get("files", []))
        if not paths:
            raise ValueError("检测到图像帧，请补充具有明确时间顺序与帧率的 RGB 视频。"
                             if entry.get("images") else "缺少原始 RGB 视频，请补充序列视频或修正清单中的 video_path。")
        opts = {p.relative_to(root).as_posix(): p for p in paths}
        if len(opts) > 1:
            blank["candidates"] = [dict(id=k, label=k) for k in opts]
        choice = selection.get("candidate")
        if choice is None and len(opts) > 1:
            raise ValueError("发现多路视频，请选择本次使用的 RGB 视角。")
        if choice is not None and (not isinstance(choice, str) or choice not in opts):
            raise ValueError("选择的视频已不在该序列中，请重新选择。")
        path = _inside(root, opts[choice] if choice is not None else paths[0])
        name = _sequence_name(entry)
        blank["source"] = str(path)
        if not path.is_file():
            raise ValueError("清单登记的 RGB 视频缺失，请补充原始视频或修正素材清单。")
        timing = entry.get("timing")
        if timing and timing["media_semantics"] != "dense_video":
            if timing["media_semantics"] == "independent_images":
                raise ValueError("这条素材由独立图片组成。此流水线需要连续 RGB 视频，请补充对应的原始视频。")
            if timing["media_semantics"] == "sparse_ordered_frames_unknown_fps":
                raise ValueError("这条素材是有序稀疏帧，原始帧率未知。请补充连续 RGB 视频或可还原时间轴的采样时间戳。")
            raise ValueError("清单未确认此素材为连续视频，请补充原始视频的时序信息。")
        stamp = _stamp(path)
        info, rate_ok, total = _probe_selected(path, tuple(stamp))
        rate = _shared_clock(timing, info) if timing else None
        input_rate = None
        if rate and float(rate) != info["fps"]:
            input_rate = str(rate)
            duration = info["frames"] / rate
            info = dict(info, containerFps=info["fps"], fps=float(rate), duration=float(duration))
            total = max(1, math.floor(duration*30 + Fraction(1, 2)))
            rate_ok = False
            blank["timingNote"] = f"按素材清单登记的原始 {float(rate):g} fps 还原时间轴（封装帧率 {info['containerFps']:g} fps）。"
        if _stamp(path) != stamp:
            raise ValueError("素材在检查期间已变化，请重新检查。")
        blank["info"] = info
        if info["width"] % 2 or info["height"] % 2:
            raise ValueError("分割入口要求正偶数宽高，请提供宽和高均为偶数的 RGB 视频。")
        mode = selection.get("mode")
        if mode is not None and mode not in ("whole", "clip"):
            raise ValueError("输入准备方式无效。")
        needs = not rate_ok or info["frames"] > MAX_FRAMES
        blank["needsPreparation"] = needs or mode == "clip"
        if needs and mode is None:
            raise ValueError("此视频需要按 30 fps、每段至多 81 帧准备。请选择整条分段或指定起点的片段。")
        start = selection.get("start", 0)
        if (type(start) not in (int,float) or not 0 <= start < info["duration"]
                or (mode != "clip" and start != 0)):
            raise ValueError("片段起点必须在视频时长内；整条输入从 0 秒开始。")
        first = math.ceil(Fraction(str(start))*30)
        if first >= total:
            raise ValueError("所选起点后没有可用的 30 fps 帧，请提前起点。")
        count = min(MAX_FRAMES, total-first) if mode == "clip" else total
        parts = sum(1 for _ in frame_windows(count, overlap))
        prepare = needs or mode == "clip"
        plan = dict(path=str(path), stamp=stamp, name=name, mode=mode or "whole", first=first,
                    count=count, parts=parts, prepare=prepare, selection=id_, schema=1)
        if overlap:
            plan.update(overlap=overlap, schema=2)
        if input_rate:
            plan["inputRate"] = input_rate
        if task != "hands":
            plan["task"] = task
        fingerprint = sha256(json.dumps(plan, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
        if not prepare:
            summary = f"使用完整 RGB 视频，共 {count} 帧。"
        elif mode == "clip":
            summary = f"从 {first/30:.3f} 秒起取 {count} 帧，以 30 fps 作为一条输入。"
        else:
            summary = f"完整覆盖视频，以 30 fps 准备 {count} 帧、{parts} 段。"
            if overlap:
                summary += f"相邻窗口共享 {overlap} 帧。"
        if timing:
            title = timing["sequence"] or name
        elif "manifest" in entry:
            title = name
        else:
            leaf = PurePosixPath(id_.split(":", 1)[1])
            title = leaf.stem if id_.startswith("video:") else leaf.name
        sequence_name = re.sub(r"[^\w.-]+", "-", title)
        sequence_name = sequence_name.encode("utf-8")[:150].decode("utf-8", "ignore").strip(".-") or "sequence"
        blank.update(ready=True, fingerprint=fingerprint, parts=parts, sequenceName=sequence_name,
                     clipPlan={"startFrame": first, "frameCount": count, "parts": parts},
                     summary=summary + blank.get("timingNote", ""))
        if overlap:
            blank["clipPlan"]["overlap"] = overlap
        return blank, plan
    except (ValueError, OSError) as exc:
        blank["issues"].append(str(exc))
        return blank, None


def inspect_selection(root, selection, *, task="hands", overlap=0):
    return _plan(root, selection, task, overlap)[0]


def _hash_file(path):
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _cached(directory, fingerprint):
    try:
        meta = json.loads((directory / "preparation.json").read_text(encoding="utf-8"))
        hashes = meta["sha256"]
        if (meta["fingerprint"] != fingerprint or not isinstance(hashes, dict)
                or "input.jsonl" not in hashes or len(hashes) < 2):
            raise ValueError
        for name, expected in hashes.items():
            if Path(name).name != name or name in (".", ".."):
                raise ValueError
            file = _inside(directory, directory/name)
            if _hash_file(file) != expected:
                raise ValueError
    except (OSError, KeyError, TypeError, ValueError) as exc:
        raise ValueError("已准备的输入缓存不完整或有改动，请检查服务器工作台的输入缓存。") from exc
    return directory / "input.jsonl"


def materialize(root, selection, private_root, expected_fingerprint, *, task="hands", overlap=0):
    """Freeze validated media and manifest only after an explicit launch confirmation."""
    report, plan = _plan(root, selection, task, overlap)
    if not plan:
        raise ValueError("；".join(report["issues"]))
    fingerprint = report["fingerprint"]
    if expected_fingerprint != fingerprint:
        raise ValueError("输入素材或准备方式已变化，请重新检查并确认。")
    cache = Path(private_root).resolve()
    if cache.is_relative_to(Path(root).resolve()):
        raise ValueError("输入准备目录必须位于数据集根目录之外。")
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    final = cache / fingerprint
    if final.exists():
        return _cached(_inside(cache, final), fingerprint)
    source = Path(plan["path"])
    with tempfile.TemporaryDirectory(prefix=".preparing-", dir=cache) as staging:
        temp = Path(staging) / "inputs"
        temp.mkdir(mode=0o700)
        windows = list(frame_windows(plan["count"], overlap))
        if plan["prepare"]:
            filters = ["setpts=PTS-STARTPTS"]
            if "inputRate" in plan:
                # The shared MKV uses one timestamp per frame index, not capture time.
                time_base = 1 / Fraction(plan["inputRate"])
                filters = [f"settb=expr={time_base}", "setpts=N"]
            filters.append("fps=fps=30:start_time=0")
            if plan["mode"] == "clip":
                first = plan["first"]
                filters += [f"trim=start_frame={first}:end_frame={first+plan['count']}", "setpts=PTS-STARTPTS"]
            stride = MAX_FRAMES - overlap if len(windows) > 1 else MAX_FRAMES
            base = Path(staging) if stride < MAX_FRAMES else temp
            # Matroska's millisecond clock can round the boundary frame slightly down.
            delta = ["-segment_time_delta", "0.001"] if stride < MAX_FRAMES else []
            args = ["ffmpeg", "-nostdin", "-v", "error", "-xerror", "-protocol_whitelist", "file",
                    "-format_whitelist", DEMUXERS, "-noautorotate", "-i", str(source), "-map", "0:v:0",
                    "-an", "-sn", "-dn", "-map_metadata", "-1", "-vf", ",".join(filters),
                    "-c:v", "ffv1", "-threads", "2", "-g", "1", "-f", "segment",
                    "-segment_time", f"{stride / 30:.6f}", *delta,
                    "-reset_timestamps", "1", str(base/"part-%08d.mkv")]
            _command(args, 1800)
            pieces = sorted(base.glob("part-*.mkv"))
            if stride < MAX_FRAMES:
                if len(pieces) != (plan["count"] + stride - 1) // stride:
                    raise ValueError("重叠切段的基础片段数与输入计划不一致。")
                videos = []
                for index, (_, frames) in enumerate(windows):
                    video = temp / pieces[index].name
                    shared = frames - stride
                    if shared > 0:
                        _command(["ffmpeg", "-nostdin", "-v", "error", "-xerror",
                                  "-i", str(pieces[index]), "-i", str(pieces[index+1]),
                                  "-filter_complex", f"[1:v]trim=end_frame={shared}[head];[0:v][head]concat=n=2:v=1:a=0",
                                  "-an", "-c:v", "ffv1", "-threads", "2", str(video)], 1800)
                    else:
                        pieces[index].rename(video)
                    videos.append(video)
            else:
                videos = pieces
        else:
            copy = temp / ("rgb"+source.suffix.lower())
            shutil.copyfile(source, copy)
            videos = [copy]
        if _stamp(_inside(Path(root).resolve(), source)) != plan["stamp"]:
            raise ValueError("素材在准备期间已变化，请重新检查并确认。")
        if len(videos) != plan["parts"]:
            raise ValueError("生成的片段数与源视频检查结果不一致，请核对视频时间戳。")
        rows = []
        for index, (video, (_, expected)) in enumerate(zip(videos, windows, strict=True)):
            info, rate_ok, _ = _probe(video)
            if (not rate_ok or info["frames"] != expected or
                    any(info[k] != report["info"][k] for k in ("width", "height"))):
                raise ValueError("片段的帧率、帧数或分辨率与输入计划不一致，请核对源视频。")
            name = plan["name"]
            if len(videos) > 1:
                name = f"{name[:146]}-part-{index+1:05d}"
            rows.append({"sequence_id": name, "video_path": str(final/video.name), "task": task})
        manifest = temp / "input.jsonl"
        manifest.write_text("".join(json.dumps(row, ensure_ascii=False)+"\n" for row in rows), encoding="utf-8")
        meta = dict(fingerprint=fingerprint, plan=plan, sourceInfo=report["info"],
                    sha256={p.name:_hash_file(p) for p in [manifest,*videos]})
        (temp/"preparation.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
        try:
            temp.rename(final)
        except OSError:
            if not final.is_dir():
                raise
            # Another confirmation may finish the same input before this one.
            return _cached(_inside(cache, final), fingerprint)
    return final / "input.jsonl"
