#!/usr/bin/env python3
"""2.5D Depth Parallax & Text-Behind-Subject offline renderer using Apple Vision foreground matting.

Separates a foreground subject from any photo/frame (via macOS 14+ Apple Vision
VNGenerateForegroundInstanceMaskRequest with automatic OpenCV GrabCut fallback), inpaints a
clean halo-free background plate, applies shallow depth-of-field bokeh, composites optional
editorial typography strictly BEHIND the foreground subject, and renders smooth multi-layer
Z-depth parallax motion to Rec.709 MP4.

Usage:
  parallax_25d.py render photo.jpg --text-behind "算力奇点" --seconds 4 --out shots/s01.mp4
  parallax_25d.py render --project videos/my-film --shot s02
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps

import color_finish
from identity import AMBER, PAPER, face
from production_run import InputSnapshot, atomic_json, digest, install_file
from transitions import STILLS

VISION_SRC = Path(__file__).with_name("subject_vision.m")
VISION_CACHE = Path.home() / ".cache/creator-video-vision"


def _hex_rgb(code: str, default=(244, 237, 221)) -> tuple[int, int, int]:
    if not isinstance(code, str):
        return default
    s = code.strip().lstrip("#")
    if len(s) == 6:
        try:
            return (int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16))
        except ValueError:
            pass
    return default


def _vision_matte_binary() -> Path | None:
    if sys.platform != "darwin" or not VISION_SRC.is_file() or not shutil.which("clang"):
        return None
    src_hash = hashlib.sha256(VISION_SRC.read_bytes()).hexdigest()[:12]
    VISION_CACHE.mkdir(parents=True, exist_ok=True)
    binary = VISION_CACHE / f"subject-vision-{src_hash}"
    if binary.is_file():
        return binary
    with tempfile.NamedTemporaryFile(dir=VISION_CACHE, prefix=".tmp-sv-", delete=False) as handle:
        tmp = Path(handle.name)
    try:
        subprocess.run([
            "clang", "-O2", "-fobjc-arc", str(VISION_SRC),
            "-framework", "Foundation", "-framework", "Vision", "-framework", "CoreImage",
            "-framework", "CoreGraphics", "-framework", "CoreVideo", "-o", str(tmp),
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        tmp.replace(binary)
        return binary
    except (OSError, subprocess.CalledProcessError):
        return None
    finally:
        tmp.unlink(missing_ok=True)


def _extract_matte(src_rgb: np.ndarray, *, mask_box: list[float] | None = None,
                   prefer_vision: bool = True) -> tuple[np.ndarray, str]:
    """Return a clean soft-feathered uint8 foreground alpha mask (0..255) and method name."""
    h, w = src_rgb.shape[:2]
    if prefer_vision and mask_box is None:
        binary = _vision_matte_binary()
        if binary is not None:
            with tempfile.TemporaryDirectory(prefix="p25d-vis-") as td:
                td_p = Path(td)
                src_p = td_p / "0001.png"
                out_dir = td_p / "masks"
                out_dir.mkdir()
                Image.fromarray(src_rgb).save(src_p)
                lst = td_p / "files.json"
                lst.write_text(json.dumps([str(src_p)]), encoding="utf-8")
                proc = subprocess.run([str(binary), str(lst), str(out_dir)],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                mask_p = out_dir / "0001.png"
                if proc.returncode == 0 and mask_p.is_file():
                    with Image.open(mask_p) as mp:
                        alpha = np.asarray(mp.convert("L").resize((w, h), Image.Resampling.BILINEAR), dtype=np.uint8)
                    coverage = float(np.mean(alpha >= 128))
                    if 0.015 <= coverage <= 0.88:
                        return alpha, "apple-vision"

    # Fallback: OpenCV GrabCut seeded by explicit mask_box or auto_focus detected subject_box
    if mask_box is not None and len(mask_box) == 4:
        l, t, r, b = [int(round(v * s)) for v, s in zip(mask_box, (w, h, w, h))]
    else:
        try:
            import auto_focus
            with tempfile.TemporaryDirectory(prefix="p25d-af-") as td:
                p = Path(td) / "f.png"
                Image.fromarray(src_rgb).save(p)
                box = auto_focus.analyze_media(p)["subject_box"]
            l, t, r, b = [int(round(v * s)) for v, s in zip(box, (w, h, w, h))]
        except Exception:
            l, t, r, b = int(w * 0.20), int(h * 0.12), int(w * 0.80), int(h * 0.92)
    l, t = max(2, min(w - 16, l)), max(2, min(h - 16, t))
    r, b = max(l + 12, min(w - 2, r)), max(t + 12, min(h - 2, b))
    labels = np.zeros((h, w), np.uint8)
    bgr = cv2.cvtColor(src_rgb, cv2.COLOR_RGB2BGR)
    cv2.grabCut(bgr, labels, (l, t, r - l, b - t), np.zeros((1, 65), np.float64),
                np.zeros((1, 65), np.float64), 5, cv2.GC_INIT_WITH_RECT)
    fg = np.where((labels == cv2.GC_FGD) | (labels == cv2.GC_PR_FGD), 255, 0).astype(np.uint8)
    alpha = cv2.GaussianBlur(fg, (5, 5), 1.0)
    return alpha, "grabcut"


def _fit_cover_rgb(path: Path, width: int, height: int) -> np.ndarray:
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im).convert("RGB")
        sw, sh = im.size
        scale = max(width / sw, height / sh)
        nw, nh = max(width, int(round(sw * scale))), max(height, int(round(sh * scale)))
        im = im.resize((nw, nh), Image.Resampling.LANCZOS)
        x0 = (nw - width) // 2
        y0 = (nh - height) // 2
        return np.asarray(im.crop((x0, y0, x0 + width, y0 + height)), dtype=np.uint8)


def _warp_layer(arr: np.ndarray, zoom: float, dx: float, dy: float, *, border_mode=cv2.BORDER_REPLICATE) -> np.ndarray:
    h, w = arr.shape[:2]
    cx, cy = w * 0.5, h * 0.5
    M = np.array([
        [zoom, 0.0, cx * (1.0 - zoom) + dx],
        [0.0, zoom, cy * (1.0 - zoom) + dy],
    ], dtype=np.float32)
    return cv2.warpAffine(arr, M, (w, h), flags=cv2.INTER_LINEAR, borderMode=border_mode)


def _render_text_layer(width: int, height: int, text: str, *, style: str = "serif",
                       color_rgb=(244, 237, 221), y_frac: float = 0.24,
                       size_frac: float = 0.145, opacity: float = 0.94,
                       subtitle: str = "", accent_rgb=(237, 169, 97)) -> np.ndarray:
    """Render an RGBA monumental editorial text layer intended to sit BEHIND the foreground cutout."""
    layer = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    lines = [ln.strip() for ln in str(text).splitlines() if ln.strip()]
    if not lines:
        return np.asarray(layer, dtype=np.uint8)
    max_w = int(width * 0.88)
    fsize = max(36, int(height * size_frac))
    fnt = face(style, fsize)
    while fsize > 36 and max(fnt.getlength(ln) for ln in lines) > max_w:
        fsize = int(fsize * 0.92)
        fnt = face(style, fsize)

    line_gap = int(fsize * 1.14)
    total_h = line_gap * len(lines)
    y0 = int(height * y_frac - total_h * 0.35)
    y0 = max(int(height * 0.08), min(height - total_h - 40, y0))

    # Soft dark back-glow so monumental text stays crisp over midtone backgrounds
    shadow = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    sdraw = ImageDraw.Draw(shadow)
    for idx, ln in enumerate(lines):
        tw = fnt.getlength(ln)
        lx = int((width - tw) * 0.5)
        ly = y0 + idx * line_gap
        sdraw.text((lx, ly + max(4, fsize // 28)), ln, font=fnt, fill=(10, 14, 12, 165))
    shadow = shadow.filter(ImageFilter.GaussianBlur(radius=max(6, fsize // 18)))
    layer = Image.alpha_composite(layer, shadow)
    draw = ImageDraw.Draw(layer)

    alpha_val = max(0, min(255, int(round(opacity * 255))))
    for idx, ln in enumerate(lines):
        tw = fnt.getlength(ln)
        lx = int((width - tw) * 0.5)
        ly = y0 + idx * line_gap
        draw.text((lx, ly), ln, font=fnt, fill=(*color_rgb, alpha_val))

    if subtitle:
        sub_fnt = face("sans", max(20, int(fsize * 0.24)))
        stw = sub_fnt.getlength(subtitle)
        sx = int((width - stw) * 0.5)
        sy = max(24, y0 - int(fsize * 0.36))
        draw.text((sx, sy), subtitle, font=sub_fnt, fill=(*accent_rgb, 240))
    return np.asarray(layer, dtype=np.uint8)


def render_parallax(source: Path, spec: dict, out_path: Path, *,
                    width: int = 1920, height: int = 1080, fps: int = 30, frames: int = 120) -> dict:
    """Render a 2.5D multi-layer parallax + optional text-behind-subject Rec.709 MP4."""
    if min(width, height) < 32 or width % 2 or height % 2 or min(fps, frames) <= 0:
        raise ValueError("Use even dimensions of at least 32 pixels, and a positive frame rate and count")
    with tempfile.TemporaryDirectory(prefix="p25d-color-") as td:
        normalized, _ = color_finish.icc_still(source, Path(td) / "srgb.png")
        src_rgb = _fit_cover_rgb(normalized, width, height)
    mask_box = spec.get("mask_box")
    if mask_box is not None and (not isinstance(mask_box, list) or len(mask_box) != 4
                                 or not all(type(v) in (int, float) and 0 <= v <= 1 for v in mask_box)
                                 or not (mask_box[0] < mask_box[2] and mask_box[1] < mask_box[3])):
        raise ValueError("mask_box requires normalized [left, top, right, bottom] inside the framed photo")
    alpha, matte_method = _extract_matte(src_rgb, mask_box=mask_box, prefer_vision=bool(spec.get("vision", True)))
    coverage = float(np.mean(alpha >= 128))
    if not 0.001 <= coverage <= 0.95:
        raise ValueError("Foreground masking did not find a usable subject. Set mask_box around the subject or use a clearer photo.")

    # Feather matte lightly & build halo-free inpainted background plate
    fg_alpha = cv2.GaussianBlur(alpha, (3, 3), 0.7)
    dilate_r = max(7, int(min(width, height) * 0.014)) | 1
    inpaint_mask = cv2.dilate((alpha >= 90).astype(np.uint8) * 255,
                              cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (dilate_r, dilate_r)))
    # Inpaint at half-res for speed & organic background fill, then blend back smoothly
    sw, sh = max(64, width // 2), max(64, height // 2)
    bg_small = cv2.resize(src_rgb, (sw, sh), interpolation=cv2.INTER_AREA)
    mask_small = cv2.resize(inpaint_mask, (sw, sh), interpolation=cv2.INTER_NEAREST)
    inpainted_small = cv2.inpaint(bg_small, mask_small, 7, cv2.INPAINT_TELEA)
    bg_clean = cv2.resize(inpainted_small, (width, height), interpolation=cv2.INTER_CUBIC)
    keep_mask = cv2.GaussianBlur((inpaint_mask > 0).astype(np.float32), (15, 15), 4.0)
    keep_mask = np.maximum(keep_mask, alpha >= 90)[:, :, None]
    bg_plate = np.clip(src_rgb.astype(np.float32) * (1.0 - keep_mask) + bg_clean.astype(np.float32) * keep_mask, 0, 255).astype(np.uint8)

    bg_blur = float(spec.get("bg_blur", 5.5))
    if bg_blur > 0.2:
        k = max(3, int(round(bg_blur * 3.0)) | 1)
        bg_plate = cv2.GaussianBlur(bg_plate, (k, k), bg_blur)
    bg_dim = float(spec.get("bg_dim", 0.14))
    if bg_dim > 0.0:
        bg_plate = np.clip(bg_plate.astype(np.float32) * (1.0 - min(0.65, bg_dim)), 0, 255).astype(np.uint8)

    # Pre-render optional Text-Behind-Subject layer
    text_behind = spec.get("text_behind", "")
    text_rgba = None
    if text_behind:
        text_rgba = _render_text_layer(
            width, height, text_behind,
            style=spec.get("text_font", "serif"),
            color_rgb=_hex_rgb(spec.get("text_color", "#F4EDDD"), PAPER),
            y_frac=float(spec.get("text_y", 0.28)),
            size_frac=float(spec.get("text_size", 0.165)),
            opacity=float(spec.get("text_opacity", 0.94)),
            subtitle=spec.get("subtitle", ""),
            accent_rgb=_hex_rgb(spec.get("accent", "#EDA961"), AMBER),
        )

    # Foreground RGBA + soft contact drop shadow
    fg_rgba = np.dstack([src_rgb, fg_alpha])
    fg_shadow_strength = float(spec.get("fg_shadow", 0.34))
    shadow_alpha = cv2.GaussianBlur(fg_alpha.astype(np.float32) / 255.0, (31, 31), 10.0) * fg_shadow_strength

    bg_z0, bg_z1 = [float(v) for v in spec.get("bg_zoom", [1.02, 1.06])]
    fg_z0, fg_z1 = [float(v) for v in spec.get("fg_zoom", [1.03, 1.13])]
    pan_x, pan_y = [float(v) for v in spec.get("pan", [0.022, -0.008])]
    text_slide = float(spec.get("text_slide", 0.035))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    color, _ = color_finish.source_filter(dict(pix_fmt="rgb24", width=width, height=height))
    with tempfile.TemporaryDirectory(prefix="p25d-enc-") as td:
        tmp_mp4 = Path(td) / "parallax.mp4"
        cmd = [
            "ffmpeg", "-y", "-nostdin", "-v", "error",
            "-f", "rawvideo", "-vcodec", "rawvideo",
            "-s", f"{width}x{height}", "-pix_fmt", "rgb24", "-r", str(fps),
            "-i", "-",
            "-an", "-c:v", "libx264", "-preset", "medium", "-crf", "16",
            "-vf", f"{color},format=yuv420p", *color_finish.FLAGS, "-movflags", "+faststart", str(tmp_mp4),
        ]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        try:
            for i in range(frames):
                u = i / max(1, frames - 1)
                e = u * u * (3.0 - 2.0 * u)  # Smooth C1 cubic ease
                bg_z = bg_z0 + (bg_z1 - bg_z0) * e
                fg_z = fg_z0 + (fg_z1 - fg_z0) * e
                bg_dx, bg_dy = -0.35 * pan_x * width * (e - 0.5), -0.35 * pan_y * height * (e - 0.5)
                fg_dx, fg_dy = 1.00 * pan_x * width * (e - 0.5), 1.00 * pan_y * height * (e - 0.5)

                comp = _warp_layer(bg_plate, bg_z, bg_dx, bg_dy, border_mode=cv2.BORDER_REPLICATE).astype(np.float32)

                if text_rgba is not None:
                    # Text sits at intermediate Z-depth (between background and foreground)
                    t_in = min(1.0, u / 0.22)
                    t_ease = 1.0 - (1.0 - t_in) ** 3
                    tz = 0.5 * (bg_z + fg_z) * (0.97 + 0.03 * t_ease)
                    tdx = 0.25 * pan_x * width * (e - 0.5)
                    tdy = (1.0 - t_ease) * text_slide * height + 0.20 * pan_y * height * (e - 0.5)
                    tw = _warp_layer(text_rgba, tz, tdx, tdy, border_mode=cv2.BORDER_CONSTANT)
                    ta = (tw[:, :, 3:4].astype(np.float32) / 255.0) * (0.15 + 0.85 * t_ease)
                    comp = comp * (1.0 - ta) + tw[:, :, :3].astype(np.float32) * ta

                if fg_shadow_strength > 0.0:
                    sh_w = _warp_layer(shadow_alpha, fg_z, fg_dx + 6.0, fg_dy + 10.0, border_mode=cv2.BORDER_CONSTANT)
                    comp *= (1.0 - np.clip(sh_w, 0.0, 0.75)[:, :, None])

                fg_w = _warp_layer(fg_rgba, fg_z, fg_dx, fg_dy, border_mode=cv2.BORDER_CONSTANT)
                fa = fg_w[:, :, 3:4].astype(np.float32) / 255.0
                comp = comp * (1.0 - fa) + fg_w[:, :, :3].astype(np.float32) * fa
                proc.stdin.write(np.clip(comp, 0, 255).astype(np.uint8).tobytes())
        except BaseException:
            proc.kill()
            proc.wait()
            raise
        finally:
            proc.stdin.close()
        rc = proc.wait()
        if rc != 0:
            raise RuntimeError(f"FFmpeg parallax_25d encode failed with code {rc}")
        install_file(tmp_mp4, out_path)

    return dict(
        output=str(out_path), width=width, height=height, fps=fps, frames=frames,
        seconds=round(frames / fps, 3), matte_method=matte_method,
        foreground_coverage=round(coverage, 4),
        text_behind=bool(text_behind),
    )


def project_recipe(edit, shot, timing, fps):
    spec = shot["parallax_25d"]
    if not isinstance(spec, dict):
        raise ValueError(f"{shot['id']}: parallax_25d must be an object")
    source = spec.get("source") or shot.get("source")
    if not isinstance(source, str) or Path(source).suffix.lower() not in STILLS:
        raise ValueError(f"{shot['id']}: parallax_25d.source must name a still photo")
    if Path(shot["visual"]).suffix.lower() != ".mp4":
        raise ValueError(f"{shot['id']}: the parallax visual must be an MP4")
    if shot.get("in", 0) != 0 or shot.get("speed", 1.0) != 1.0:
        raise ValueError(f"{shot['id']}: a generated parallax plate uses its full prepared frame span at 1x")
    width, height = edit.get("size", [1920, 1080])
    return dict(shot=shot["id"], source=source, spec=spec, width=width, height=height,
                fps=fps, frames=timing["frames"])


def receipt_path(path):
    return Path(str(path) + ".parallax.json")


def verify_project(edit, shot, timing, fps, pins):
    recipe = project_recipe(edit, shot, timing, fps)
    rebuild = f"Run parallax_25d.py render --project . --shot {shot['id']} after preparing the current edit."
    try:
        record = json.loads(pins.pin(receipt_path(shot["visual"])).read_text())
    except (OSError, ValueError) as exc:
        raise ValueError(f"{shot['id']}: missing or unreadable parallax receipt. {rebuild}") from exc
    if not isinstance(record, dict) or record.get("schema") != 1 or record.get("recipe") != recipe:
        raise ValueError(f"{shot['id']}: parallax configuration or frame clock changed. {rebuild}")
    for name, field in ((recipe["source"], "source_sha256"), (shot["visual"], "output_sha256")):
        pins.pin(name)
        if pins.hashes[pins.key(name)] != record.get(field):
            raise ValueError(f"{shot['id']}: parallax input or plate changed ({name}). {rebuild}")
    return record


def verify_plate(video_info, receipt):
    recipe = receipt["recipe"]
    color_finish.verify(video_info)
    from fractions import Fraction

    if (video_info.get("width") != recipe["width"] or video_info.get("height") != recipe["height"]
            or int(video_info.get("nb_frames", 0)) != recipe["frames"]
            or Fraction(video_info.get("avg_frame_rate", "0")) != recipe["fps"]
            or Fraction(video_info.get("r_frame_rate", "0")) != recipe["fps"]
            or float(video_info.get("start_time", 0)) != 0.0):
        raise ValueError(f"{recipe['shot']}: parallax MP4 must match its prepared raster, frame count and rate")


def render_project(base, shot_id, output=None):
    with tempfile.TemporaryDirectory(prefix="p25d-project-") as td:
        folder = Path(td)
        pins = InputSnapshot(folder / "inputs")
        edit_path = base / "edit.json"
        edit = json.loads(pins.pin(edit_path).read_text())
        timeline = json.loads(pins.pin(base / "timeline.json").read_text())
        if timeline.get("edit_sha256") != pins.hashes[pins.key(edit_path)]:
            raise ValueError("Prepare the current edit before rendering a parallax plate")
        shot = next((s for s in edit["shots"] if s["id"] == shot_id), None)
        if not shot or "parallax_25d" not in shot:
            raise ValueError(f"Shot {shot_id} has no parallax_25d configuration")
        timing = next(t for t in timeline["shots"] if t["id"] == shot_id)
        recipe = project_recipe(edit, shot, timing, timeline["fps"])
        source = base / recipe["source"]
        path = output or (base / shot["visual"])
        if path.resolve() == source.resolve() or path.suffix.lower() != ".mp4":
            raise ValueError("Write the parallax plate to a separate MP4")
        plate = folder / "plate.mp4"
        playback = {key: recipe[key] for key in ("width", "height", "fps", "frames")}
        report = render_parallax(pins.pin(source), recipe["spec"], plate, **playback)
        from picture_roughcut import probe

        record = dict(schema=1, recipe=recipe, source_sha256=pins.hashes[pins.key(source)],
                      output_sha256=digest(plate), matte_method=report["matte_method"],
                      foreground_coverage=report["foreground_coverage"])
        verify_plate(probe(plate, "width,height,nb_frames,avg_frame_rate,r_frame_rate,start_time,"
                           + color_finish.FIELDS), record)
        if path.resolve() in {Path(name).resolve() for name in pins.hashes}:
            raise ValueError("Write the parallax plate to a separate MP4")
        pins.verify()
        install_file(plate, path)
        atomic_json(receipt_path(path), record)
    return dict(report, output=str(path), receipt=str(receipt_path(path)))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    rd = sub.add_parser("render", help="Render a 2.5D depth parallax + optional text-behind-subject MP4")
    rd.add_argument("source", type=Path, nargs="?", help="Source still photo")
    rd.add_argument("--project", type=Path)
    rd.add_argument("--shot")
    rd.add_argument("--text-behind", default="")
    rd.add_argument("--subtitle", default="")
    rd.add_argument("--text-color", default="#F4EDDD")
    rd.add_argument("--bg-blur", type=float, default=5.5)
    rd.add_argument("--seconds", type=float, default=4.0)
    rd.add_argument("--frames", type=int)
    rd.add_argument("--fps", type=int, default=30)
    rd.add_argument("--width", type=int, default=1920)
    rd.add_argument("--height", type=int, default=1080)
    rd.add_argument("--out", type=Path)
    args = ap.parse_args()

    w, h, fps = args.width, args.height, args.fps
    frames = args.frames
    out_path = args.out
    if args.project and args.shot:
        res = render_project(args.project.resolve(), args.shot, out_path)
    else:
        if not args.source or not args.out:
            raise SystemExit("Supply <source> and --out <mp4> (or --project and --shot)")
        source = args.source.resolve()
        spec = {
            "text_behind": args.text_behind,
            "subtitle": args.subtitle,
            "text_color": args.text_color,
            "bg_blur": args.bg_blur,
        }
        frames = frames if frames is not None else max(2, round(args.seconds * fps))
        res = render_parallax(source, spec, out_path, width=w, height=h, fps=fps, frames=frames)
    print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
