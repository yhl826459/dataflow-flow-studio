"""Export a compact, frame-aligned RGB and mask comparison after fusion."""

import json
import os
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

import numpy as np
from PIL import Image, ImageDraw


def render(source, result, masks, destination, *, single_clip=False):
    from datasets_catalog import frame_windows

    count, fps = result["frameCount"], result["fps"]
    source_width, source_height = result["width"], result["height"]
    scale = min(1, 640 / source_width, 520 / source_height)
    width = max(2, round(source_width * scale / 2) * 2)
    height = max(2, round(source_height * scale / 2) * 2)
    header = 36
    frame = np.zeros((height + header, width * 3, 3), dtype=np.uint8)
    rows = ([{"video_path": str(source)}] if single_clip else
            [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()])
    windows = list(frame_windows(count, result["overlap"]))
    palette = [(56, 212, 149), (248, 158, 60), (58, 172, 255), (237, 106, 160), (212, 199, 69)]
    colors = [palette[i % len(palette)] for i in range(len(masks))]
    labels = Image.new("RGB", (width * 3, header), (24, 34, 34))
    draw = ImageDraw.Draw(labels)
    mask_label = "Round mask" if single_clip else "Fused masks"
    for column, label in enumerate(("Original RGB", mask_label, "RGB + masks")):
        draw.text((column * width + 8, 6), label, fill="white")
    for index, color in enumerate(colors):
        x = width + 8 + index * 42
        if x + 42 > width * 2:
            break
        label = f"#{result['tracks'][index]['trackId']}"
        draw.text((x, 20), label, fill=color)
    frame[:header] = np.asarray(labels)
    pixels = frame[header:]
    command = ["ffmpeg", "-nostdin", "-v", "error", "-xerror", "-n", "-f", "rawvideo",
               "-pix_fmt", "rgb24", "-video_size", f"{width * 3}x{height + header}",
               "-framerate", str(fps), "-i", "pipe:0", "-an", "-c:v", "libx264", "-threads", "2",
               "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
               str(destination)]
    with subprocess.Popen(command, stdin=subprocess.PIPE) as writer:
        try:
            for index, (row, (start, available)) in enumerate(zip(rows, windows, strict=True)):
                length = windows[index + 1][0] - start if index + 1 < len(windows) else available
                # Prepared clips share frames. Emit each timeline frame once.
                decode = ["ffmpeg", "-nostdin", "-v", "error", "-xerror", "-threads", "2", "-noautorotate",
                          "-i", row["video_path"], "-map", "0:v:0", "-vf", f"scale={width}:{height}",
                          "-frames:v", str(length), "-threads", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"]
                with subprocess.Popen(decode, stdout=subprocess.PIPE) as reader:
                    try:
                        for pos in range(start, start + length):
                            raw = reader.stdout.read(width * height * 3)
                            if len(raw) != width * height * 3:
                                raise ValueError(f"RGB 缓存无法完整解码，缺少第 {pos} 帧。")
                            rgb = np.frombuffer(raw, dtype=np.uint8).reshape(height, width, 3)
                            pixels[:, :width] = rgb
                            pixels[:, width:width * 2] = 0
                            pixels[:, width * 2:] = rgb
                            for mask, color in zip(masks, colors, strict=True):
                                small = Image.fromarray(mask[pos]).resize((width, height), Image.Resampling.NEAREST)
                                foreground = np.asarray(small) != 0
                                pixels[:, width:width * 2][foreground] = color
                                pixels[:, width * 2:][foreground] = rgb[foreground] * .55 + np.array(color) * .45
                            writer.stdin.write(frame.tobytes())
                    finally:
                        reader.stdout.close()
                if reader.returncode:
                    raise RuntimeError("RGB 解码失败，详见运行日志。")
        finally:
            writer.stdin.close()
    if writer.returncode:
        raise RuntimeError("整段对照视频编码失败，详见运行日志。")
    check_preview(destination, count, fps)


def check_preview(destination, count, fps):
    probe = subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0",
                                     "-count_frames", "-show_entries", "stream=nb_read_frames,r_frame_rate",
                                     "-of", "json", str(destination)])
    stream, = json.loads(probe)["streams"]
    if int(stream["nb_read_frames"]) != count or stream["r_frame_rate"] != f"{fps}/1":
        raise ValueError("对照视频未通过完整帧数与帧率校验。")


def round_media(root, entries, result, source, mask_sources, *, verify=False):
    """Render each comparison directly from the input RGB and the mask scored in this round."""
    from mask_fusion import load_mask

    rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
    for entry, row in zip(entries, rows, strict=True):
        if entry["sequenceId"] != row["sequence_id"]:
            raise ValueError("RGB clip and round segment IDs disagree")
        rgb = (source.parent / row["video_path"]).resolve(strict=True)
        frames = entry["lastFrame"] - entry["firstFrame"] + 1
        shape = (frames, result["height"], result["width"])
        for rnd in entry["rounds"]:
            folder = root / rnd["directory"]
            if folder.resolve(strict=True) != folder or not folder.is_relative_to(root):
                raise ValueError("Invalid round delivery folder")
            rnd["previews"] = []
            prompts = json.loads((folder / "prompts.json").read_text(encoding="utf-8"))["targets"]
            # Individual views keep overlapping objects independently visible. A no-target
            # round still gets a comparison with a blank mask pane.
            for i, target in enumerate(prompts or [None], 1):
                filename = f"target_{i:03d}_preview.mp4" if target else "no_targets_preview.mp4"
                preview = folder / filename
                if verify:
                    if preview.is_symlink() or not preview.is_file():
                        raise ValueError(f"Missing round preview: {preview}")
                    check_preview(preview, frames, result["fps"])
                else:
                    masks = [load_mask(mask_sources[entry["part"], rnd["round"], target["id"]], shape)] if target else []
                    view = dict(result, frameCount=frames, overlap=0, tracks=[{"trackId": i}] if target else [])
                    render(rgb, view, masks, preview, single_clip=True)
                rnd["previews"].append(dict(id=target["id"] if target else None, file=filename))


def publish_video(root, fused, source, sequence, flags):
    sys.path.insert(0, str(Path.cwd() / "flow-studio"))
    from mask_fusion import load_mask
    from segment_delivery import collect_segments, round_files

    result = json.loads((fused / "tracks.json").read_text(encoding="utf-8"))
    tracks = result["tracks"]
    names = [track["mask"] for track in tracks]
    if (len(set(names)) != len(names) or any(Path(name).name != name or not name.endswith(".npy") for name in names)):
        raise ValueError("无效的融合掩码文件名。")
    result.update(sequence=sequence, deliveryDirectory=root.name, preview="mask_preview.mp4")
    mask_sources = {}
    clips = collect_segments(fused.parent, result, source, flags, mask_sources)
    shape = (result["frameCount"], result["height"], result["width"])
    masks = [load_mask(fused / name, shape) for name in names]
    for track in tracks:
        for part in track["parts"]:
            part.pop("mask", None)
    final_names = [*names, result["preview"], "result.json"]
    if any((root / name).exists() or (root / name).is_symlink() for name in [*final_names, "segments"]):
        raise FileExistsError("交付目录已有同名文件。")
    with TemporaryDirectory(prefix=".publishing-", dir=root) as temporary:
        stage = Path(temporary)
        result["segments"] = round_files(stage, clips)
        round_files(stage, clips, verify=True)
        round_media(stage, result["segments"], result, source, mask_sources)
        render(source, result, masks, stage / result["preview"])
        for name in names:
            os.link(fused / name, stage / name)
        (stage / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        published = []
        try:
            (stage / "segments").rename(root / "segments")
            published.append(root / "segments")
            for name in final_names:
                os.link(stage / name, root / name)
                published.append(root / name)
        except OSError:
            for path in reversed(published):
                if path.name == "segments":
                    path.rename(stage / "segments")
                else:
                    path.unlink()
            raise
    return result, clips, mask_sources, source


def verify_delivered_work(root, delivery):
    """Validate the delivery and retain all run files for user-confirmed cleanup."""
    from mask_fusion import load_mask
    from segment_delivery import round_files

    expected, clips, mask_sources, source = delivery
    work = root / ".run"
    if root.resolve(strict=True) != root or work.resolve(strict=True) != work:
        raise ValueError("Unsafe work directory")
    saved = json.loads((root / "result.json").read_text(encoding="utf-8"))
    if saved != expected or not saved["segments"] or saved["deliveryDirectory"] != root.name:
        raise ValueError("Segment report failed read-back")
    entries = round_files(root, clips, verify=True)
    round_media(root, entries, saved, source, mask_sources, verify=True)
    if entries != saved["segments"]:
        raise ValueError("Round directory index failed read-back")
    check_preview(root / "mask_preview.mp4", saved["frameCount"], saved["fps"])
    for track in saved["tracks"]:
        name = track["mask"]
        mask = root / name
        if (Path(name).name != name or mask.is_symlink() or
                not mask.samefile(work / "whole_video_masks" / name)):
            raise ValueError("Published mask does not match the fused source")
        load_mask(mask, (saved["frameCount"], saved["height"], saved["width"]))
