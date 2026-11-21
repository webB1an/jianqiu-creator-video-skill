#!/usr/bin/env python3
"""Two live sources share a frame, then a moving aperture follows one of them."""
import argparse
from contextlib import ExitStack, contextmanager
from functools import lru_cache
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile

from PIL import Image, ImageDraw, ImageOps
from identity import card_colors, card_face, comparison_color

STILLS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}


def crop_to(image, width, height, anchor):
    iw, ih = image.size
    scale = max(width / iw, height / ih)
    crop_w, crop_h = min(iw, width / scale), min(ih, height / scale)
    x, y = (iw - crop_w) * anchor[0], (ih - crop_h) * anchor[1]
    return image.resize((width, height), Image.Resampling.BICUBIC, (x, y, min(iw, x + crop_w), min(ih, y + crop_h)))


@contextmanager
def read_source(src, shape, fps, count, index):
    path = Path(src["file"])
    if path.suffix.lower() in STILLS:
        with Image.open(path) as original:
            still = ImageOps.exif_transpose(original).convert("RGB")
        yield lambda: still
        return
    info = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,sample_aspect_ratio:stream_side_data=rotation", "-of", "json", str(path)]))["streams"][0]
    iw, ih = info["width"], info["height"]
    sar = info.get("sample_aspect_ratio", "1:1")
    if ":" in sar:
        a, b = map(int, sar.split(":"))
        if a and b:
            iw *= a / b
    rotation = next((entry["rotation"] for entry in info.get("side_data_list", []) if "rotation" in entry), 0)
    if abs(rotation) % 180 == 90:
        iw, ih = ih, iw
    ratio = min(1, max(shape) / max(iw, ih))
    dw, dh = max(1, round(iw * ratio)), max(1, round(ih * ratio))
    filters = [f"fps={fps}"]
    if index is not None:
        filters.append(f"select=eq(n\\,{index})")
    filters.extend([f"scale={dw}:{dh}", "setsar=1"])
    command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-ss", str(src.get("in", 0)),
        "-i", str(path), "-map", "0:v:0", "-an", "-vf", ",".join(filters), "-frames:v", str(1 if index is not None else count),
        "-fps_mode", "passthrough", "-threads", "1", "-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"]
    decoder = subprocess.Popen(command, stdout=subprocess.PIPE)
    def next_frame():
        data = decoder.stdout.read(dw * dh * 3)
        if len(data) != dw * dh * 3:
            raise ValueError(f"{path}: moving source ended before the requested frame; shorten the cut or change its in-point.")
        return Image.frombytes("RGB", (dw, dh), data)
    try:
        yield next_frame
    except BaseException:
        decoder.kill()
        raise
    finally:
        status = decoder.wait()
        decoder.stdout.close()
    if status:
        raise RuntimeError(f"ffmpeg decoder exited with status {status} for {path}")


def aperture(spec, frame):
    w = spec["width"]
    t = min(1, max(0, (frame - spec["focusStart"]) / (spec["focusEnd"] - spec["focusStart"])))
    ease = t * t * t * (t * (t * 6 - 15) + 10)
    focus_left = spec["focusSide"] == "left"
    split = round(w * (0.5 + (0.5 if focus_left else -0.5) * ease))
    return split, round(max(1, w / 450) * (1 - ease))


def visible_frame_counts(spec):
    # Aperture widths are monotonic; integer rounding may hide the supporting source early.
    focused = 0 if spec["focusSide"] == "left" else 1
    final_split = spec["width"] if focused == 0 else 0
    count = spec["focusEnd"]
    while count > 0 and aperture(spec, count - 1)[0] == final_split:
        count -= 1
    return (spec["frames"], count) if focused == 0 else (count, spec["frames"])


@lru_cache(maxsize=32)
def label_image(text, color, short_edge):
    size = max(12, round(short_edge * .044))
    font = card_face("label", size, text)
    left, top, right, bottom = font.getbbox(text)
    px, py = max(3, round(size * .43)), max(2, round(size * .28))
    im = Image.new("RGBA", (right-left+px*2, bottom-top+py*2))
    draw = ImageDraw.Draw(im)
    draw.rounded_rectangle((0,0,im.width-1,im.height-1), radius=py, fill=card_colors("dark")["background"])
    draw.text((px-left,py-top), text, font=font, fill=color)
    return im


def compose(spec, readers, frame):
    w, h = spec["width"], spec["height"]
    split, line_w = aperture(spec, frame)
    canvas = Image.new("RGB", (w, h))
    for src, read, x, width in zip(spec["sources"], readers, (0, split), (split, w - split)):
        if width:
            canvas.paste(crop_to(read(), width, h, src["anchor"]), (x, 0))
            if "legend" in src:
                sticker = label_image(src["legend"]["text"], src["legend"]["color"], min(w,h))
                pad = spec["labelInset"]
                if sticker.width + 2*pad <= width:
                    canvas.paste(sticker, (x+pad,pad), sticker)
    if 0 < split < w and line_w:
        edge = ImageDraw.Draw(canvas)
        edge.rectangle((split - line_w // 2, 0, split - line_w // 2 + line_w - 1, h - 1), fill="#e2dac6")
    return canvas


def validate(spec, parent):
    for key in ("width", "height", "fps", "frames", "focusStart", "focusEnd"):
        if type(spec.get(key)) is not int:
            raise ValueError(f"{key} requires an integer")
    if any(spec[k] <= 0 for k in ("width", "height", "frames", "fps")) or spec["width"] % 2 or spec["height"] % 2:
        raise ValueError("Use positive dimensions, fps and frames; dimensions must be even for H.264.")
    if not 0 <= spec["focusStart"] < spec["focusEnd"] < spec["frames"]:
        raise ValueError("Expected 0 <= focusStart < focusEnd < frames")
    if spec.get("focusSide") not in ("left", "right") or len(spec.get("sources", [])) != 2:
        raise ValueError("Provide two sources ordered left to right and a focusSide of left or right")
    legends = [src["legend"] for src in spec["sources"] if "legend" in src]
    if any(not isinstance(legend, dict) for legend in legends):
        raise ValueError("Source legend must contain text and an optional comparison entity")
    roles = {}
    if any(legend.get("entity") is not None for legend in legends):
        project_edit = parent / spec.get("edit", "edit.json")
        roles = json.loads(project_edit.read_text()).get("comparison_roles", {})
    if legends:
        edge = min(spec["width"], spec["height"])
        spec["labelInset"] = max(2, round(edge*.025))
        for legend in legends:
            label = legend.get("text")
            if not isinstance(label, str) or not label.strip() or len(label.splitlines()) != 1:
                raise ValueError("Supply one visible line of text for each labeled evidence source")
            legend["color"] = comparison_color(roles, legend.get("entity"))
            if label_image(label,legend["color"],edge).width + 2*spec["labelInset"] > spec["width"]//2:
                raise ValueError(f"Shorten the label for the opening half-frame: {label}")
    for src in spec["sources"]:
        src["file"] = (parent / src["file"]).resolve()
        if not src["file"].is_file():
            raise FileNotFoundError(src["file"])
        src.setdefault("anchor", [0.5, 0.5])
        if len(src["anchor"]) != 2 or any(not 0 <= n <= 1 for n in src["anchor"]):
            raise ValueError("anchor requires horizontal and vertical fractions in [0,1]")
        if not math.isfinite(src.get("in", 0)) or src.get("in", 0) < 0:
            raise ValueError("Source in-point must be nonnegative seconds")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("spec", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--frame", type=int, help="Render one zero-based frame to PNG.")
    args = parser.parse_args()
    spec = json.loads(args.spec.read_text())
    validate(spec, args.spec.resolve().parent)
    frames = spec["frames"]
    if args.frame is not None and not 0 <= args.frame < frames:
        parser.error("--frame outside clip")
    if args.out.suffix.lower() != (".png" if args.frame is not None else ".mp4"):
        parser.error("Use --out .mp4 for a clip or --frame N --out .png for a still")
    shape = spec["width"], spec["height"]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=args.out.parent) as tmp, ExitStack() as stack:
        target = Path(tmp) / args.out.name
        readers = []
        for src, count in zip(spec["sources"], visible_frame_counts(spec)):
            if args.frame is not None and args.frame >= count:
                readers.append(None)
            else:
                readers.append(stack.enter_context(read_source(src, shape, spec["fps"], count, args.frame)))
        if args.frame is not None:
            compose(spec, readers, args.frame).save(target)
        else:
            command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "rgb24",
                "-s", f"{shape[0]}x{shape[1]}", "-r", str(spec["fps"]), "-i", "pipe:0", "-an",
                "-c:v", "libx264", "-crf", "17", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(target)]
            encoder = subprocess.Popen(command, stdin=subprocess.PIPE)
            try:
                for frame in range(frames):
                    encoder.stdin.write(compose(spec, readers, frame).tobytes())
                encoder.stdin.close()
                if encoder.wait():
                    raise RuntimeError("ffmpeg encoder failed")
            finally:
                if encoder.poll() is None:
                    encoder.kill()
                    encoder.wait()
        os.replace(target, args.out)


if __name__ == "__main__":
    main()
