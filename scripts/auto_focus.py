#!/usr/bin/env python3
"""Automatic Face & Saliency Subject Focus detector for camera framing, portrait reframing & caption_avoid.

Uses native macOS Apple Vision (VNDetectFaceRectanglesRequest + VNGenerateAttentionBasedSaliencyImageRequest,
compiled once and cached) for high-accuracy profile/tilted face & attention focus, with automatic offline
OpenCV Haar + Sobel saliency fallback on non-macOS systems.

Usage:
  auto_focus.py --project videos/my-film
  auto_focus.py --project videos/my-film --write [--apply-covers]
  auto_focus.py --file assets/interview.mp4
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

STILLS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
FACE_CASCADE_PATH = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
VISION_SRC = Path(__file__).with_name("focus_vision.m")
VISION_CACHE = Path.home() / ".cache/creator-video-vision"


def _vision_binary() -> Path | None:
    """Compile and cache the native macOS Apple Vision helper once per source hash."""
    if sys.platform != "darwin" or not VISION_SRC.is_file() or not shutil.which("clang"):
        return None
    src_hash = hashlib.sha256(VISION_SRC.read_bytes()).hexdigest()[:12]
    VISION_CACHE.mkdir(parents=True, exist_ok=True)
    binary = VISION_CACHE / f"focus-vision-{src_hash}"
    if binary.is_file():
        return binary
    with tempfile.NamedTemporaryFile(dir=VISION_CACHE, prefix=".tmp-focus-", delete=False) as handle:
        tmp_bin = Path(handle.name)
    try:
        subprocess.run([
            "clang", "-O2", "-fobjc-arc", str(VISION_SRC),
            "-framework", "Foundation", "-framework", "Vision", "-framework", "CoreVideo", "-o", str(tmp_bin),
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        tmp_bin.replace(binary)
        return binary
    except (OSError, subprocess.CalledProcessError):
        return None
    finally:
        tmp_bin.unlink(missing_ok=True)


def _run_apple_vision_rows(frames: list[np.ndarray]) -> list[dict] | None:
    """Run native Apple Vision face + objectness + attention heatmap peak per frame."""
    binary = _vision_binary()
    if binary is None:
        return None
    with tempfile.TemporaryDirectory(prefix="af-vis-") as td:
        td_p = Path(td)
        img_paths = []
        for idx, bgr in enumerate(frames):
            p = td_p / f"f{idx}.jpg"
            cv2.imwrite(str(p), bgr, [cv2.IMWRITE_JPEG_QUALITY, 92])
            img_paths.append(str(p))
        list_json = td_p / "paths.json"
        list_json.write_text(json.dumps(img_paths), encoding="utf-8")
        try:
            raw = subprocess.check_output([str(binary), str(list_json)], text=True, stderr=subprocess.DEVNULL)
            return json.loads(raw)
        except Exception:
            return None


def _run_apple_vision(frames: list[np.ndarray]) -> tuple[list[tuple], list[tuple], list[tuple]] | None:
    """Aggregate native Apple Vision detections across decoded BGR frames."""
    rows = _run_apple_vision_rows(frames)
    if rows is None:
        return None
    faces, salients, peaks = [], [], []
    for row in rows:
        for b in row.get("faces") or []:
            if len(b) == 4:
                faces.append(tuple(float(v) for v in b))
        objs = [tuple(float(v) for v in b) for b in (row.get("objects") or []) if len(b) == 4]
        sals = [tuple(float(v) for v in b) for b in (row.get("salient") or []) if len(b) == 4]
        tight_objs = [b for b in objs if (b[2] - b[0]) * (b[3] - b[1]) <= 0.52]
        if tight_objs:
            salients.extend(tight_objs)
        elif sals:
            salients.extend(sals)
        elif objs:
            salients.extend(objs)
        pk = row.get("attn_peak")
        if isinstance(pk, list) and len(pk) == 2:
            peaks.append((float(pk[0]), float(pk[1])))
    return faces, salients, peaks


def _opencv_single_focus(bgr: np.ndarray, cascade) -> tuple[float, float, str]:
    h, w = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    if cascade is not None and not cascade.empty():
        faces = cascade.detectMultiScale(gray, scaleFactor=1.12, minNeighbors=5, minSize=(int(w * 0.06), int(h * 0.06)))
        if len(faces):
            x0 = min(f[0] for f in faces) / w
            y0 = min(f[1] for f in faces) / h
            x1 = max(f[0] + f[2] for f in faces) / w
            y1 = max(f[1] + f[3] for f in faces) / h
            return (x0 + x1) * 0.5, (y0 + y1) * 0.5, "face"
    blur_s = cv2.GaussianBlur(gray.astype(np.float32), (5, 5), 0)
    gx = cv2.Sobel(blur_s, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(blur_s, cv2.CV_32F, 0, 1, ksize=3)
    mag = cv2.magnitude(gx, gy)
    sal = cv2.GaussianBlur(mag, (31, 31), 0)
    thresh = float(np.percentile(sal, 88))
    ys, xs = np.nonzero(sal >= max(thresh, 1e-3))
    if len(xs):
        wts = sal[ys, xs]
        return float(np.average(xs, weights=wts)) / w, float(np.average(ys, weights=wts)) / h, "saliency"
    return 0.5, 0.48, "fallback"


def _picture_cut_frame(path, in_s, first, last, fps):
    """Locate a dominant one-frame picture change between two distant subject detections."""
    if last <= first + 1:
        return last
    raw = subprocess.check_output([
        "ffmpeg", "-nostdin", "-v", "error", "-ss", f"{in_s:.9f}",
        "-i", str(path), "-an", "-vf",
        f"fps={fps},trim=start_frame={first}:end_frame={last + 1},setpts=PTS-STARTPTS,scale=96:54",
        "-frames:v", str(last - first + 1),
        "-pix_fmt", "gray", "-f", "rawvideo", "pipe:1",
    ])
    pictures = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 54, 96)
    if len(pictures) < 2:
        return None
    changes = np.abs(np.diff(pictures.astype(np.int16), axis=0)).mean(axis=(1, 2))
    peak = int(changes.argmax())
    if changes[peak] > max(1.0, 3 * np.median(changes)):
        return first + peak + 1
    return None


def track_media(path: Path, *, sample_interval: float = 0.5, in_s: float = 0.0,
                dur_s: float = 4.0, fps: float = 30.0, frames: int | None = None) -> dict:
    """Sample a moving video over time and produce dead-zone + damped smooth tracking keyframes."""
    if path.suffix.lower() in STILLS:
        static = analyze_media(path)
        cx, cy = static["focal_point"]
        return dict(
            backend=static["backend"], dynamic=False, x_span=0.0,
            keyframes=[{"frame": 0, "seconds": 0.0, "at": [cx, cy]}],
        )
    if not all(math.isfinite(x) for x in (sample_interval, in_s, dur_s, fps)) or min(dur_s, fps) <= 0 or in_s < 0:
        raise ValueError("Tracking requires finite timing, nonnegative in-point, and positive duration and frame rate")
    if frames is not None and (type(frames) is not int or frames < 1):
        raise ValueError("Tracking frames must be a positive integer")
    step = max(1.0, max(0.25, sample_interval) * float(fps))
    last_frame = (frames if frames is not None else math.ceil(dur_s * fps)) - 1
    sample_frames = sorted({last_frame, *(math.floor(i * step + 0.5) for i in range(int(last_frame / step) + 1))})
    # Select the same presentation frames as the portrait encoder after FPS normalization.
    choose = f'eq(n,floor(floor(n/{step:.15g}+0.5)*{step:.15g}+0.5))+eq(n,{last_frame})'
    with tempfile.TemporaryDirectory(prefix="af-frames-") as td:
        subprocess.run([
            "ffmpeg", "-nostdin", "-v", "error", "-ss", f"{in_s:.9f}", "-i", str(path),
            "-an", "-vf", f"fps={fps},trim=end_frame={last_frame + 1},select='{choose}',scale=640:-2",
            "-fps_mode", "passthrough", str(Path(td) / "%08d.png"),
        ], check=True)
        samples = [(frame, cv2.imread(str(file))) for frame, file in
                   zip(sample_frames, sorted(Path(td).glob("*.png")))]
    samples = [(frame, image) for frame, image in samples if image is not None]
    if not samples:
        static = analyze_media(path, in_s=in_s, dur_s=dur_s)
        cx, cy = static["focal_point"]
        return dict(backend=static["backend"], dynamic=False, x_span=0.0,
                    keyframes=[{"frame": 0, "seconds": 0.0, "at": [cx, cy]}])

    rows = _run_apple_vision_rows([image for _, image in samples])
    cascade = cv2.CascadeClassifier(str(FACE_CASCADE_PATH)) if FACE_CASCADE_PATH.is_file() else None
    raw_pts = []
    backend = "apple-vision" if rows is not None else "opencv"
    for idx, (frame, bgr) in enumerate(samples):
        cx, cy, method = 0.5, 0.48, "fallback"
        if rows is not None and idx < len(rows):
            row = rows[idx]
            faces = [tuple(float(v) for v in b) for b in (row.get("faces") or []) if len(b) == 4]
            objs = [tuple(float(v) for v in b) for b in (row.get("objects") or []) if len(b) == 4]
            sals = [tuple(float(v) for v in b) for b in (row.get("salient") or []) if len(b) == 4]
            pk = row.get("attn_peak")
            if faces:
                x0, y0 = min(b[0] for b in faces), min(b[1] for b in faces)
                x1, y1 = max(b[2] for b in faces), max(b[3] for b in faces)
                cx, cy, method = (x0 + x1) * 0.5, (y0 + y1) * 0.5, "face"
            else:
                tight = [b for b in objs if (b[2] - b[0]) * (b[3] - b[1]) <= 0.52]
                if tight:
                    b = tight[0]
                    cx, cy, method = (b[0] + b[2]) * 0.5, (b[1] + b[3]) * 0.5, "object"
                elif isinstance(pk, list) and len(pk) == 2:
                    cx, cy, method = float(pk[0]), float(pk[1]), "saliency"
                elif sals:
                    b = sals[0]
                    cx, cy, method = (b[0] + b[2]) * 0.5, (b[1] + b[3]) * 0.5, "saliency"
                else:
                    cx, cy, method = _opencv_single_focus(bgr, cascade)
        else:
            cx, cy, method = _opencv_single_focus(bgr, cascade)
        raw_pts.append((frame, cx, cy))

    # Bracket a real cut with adjacent frames so crop interpolation cannot anticipate it.
    smoothed = []
    sx, sy = raw_pts[0][1], raw_pts[0][2]
    smoothed.append((raw_pts[0][0], sx, sy))
    last_cut = 0
    for index, (prior, current) in enumerate(zip(raw_pts, raw_pts[1:])):
        last, rx, ry = current
        dx, dy = rx - sx, ry - sy
        first = prior[0]
        cut = None
        if abs(rx - prior[1]) > 0.38:
            cut = _picture_cut_frame(path, in_s, first, last, fps)
        if cut is not None:
            if cut - 1 > first:
                # Continue recent camera travel on the outgoing shot up to its final frame.
                # Only use velocity measured entirely on that same side of the cut.
                tail_x, tail_y = sx, sy
                if index > 0 and raw_pts[index - 1][0] >= last_cut:
                    prev_frame, prev_x, prev_y = raw_pts[index - 1]
                    fraction = min(1.0, (cut - 1 - first) / (first - prev_frame))
                    vx, vy = prior[1] - prev_x, prior[2] - prev_y
                    if abs(vx) > 0.025:
                        tail_x += vx * fraction
                    if abs(vy) > 0.03:
                        tail_y += vy * fraction
                smoothed.append((cut - 1, tail_x, tail_y))
            last_cut = cut
            sx, sy = rx, ry
            if cut < last:
                smoothed.append((cut, sx, sy))
        else:
            if abs(dx) > 0.025:
                sx += 0.75 * dx
            if abs(dy) > 0.03:
                sy += 0.45 * dy
        smoothed.append((last, sx, sy))

    xs = [p[1] for p in smoothed]
    x_span = round(max(xs) - min(xs), 4) if xs else 0.0
    keyframes = [
        {"frame": frame, "seconds": round(float(frame / fps), 3),
         "at": [round(max(0.05, min(0.95, px)), 4), round(max(0.05, min(0.95, py)), 4)]}
        for frame, px, py in smoothed
    ]
    return dict(
        backend=backend,
        sample_count=len(samples),
        x_span=x_span,
        dynamic=x_span >= 0.04,
        keyframes=keyframes,
    )


def _decode_frames(path: Path, *, sample_count: int = 3, in_s: float = 0.0, dur_s: float = 4.0) -> list[np.ndarray]:
    if not path.is_file():
        raise FileNotFoundError(f"Media not found: {path}")
    if path.suffix.lower() in STILLS:
        img = cv2.imread(str(path))
        if img is None:
            raise ValueError(f"Unreadable still: {path}")
        return [img]
    frames = []
    for i in range(sample_count):
        t = max(0.0, in_s + dur_s * (i + 0.5) / max(1, sample_count))
        raw = subprocess.check_output([
            "ffmpeg", "-nostdin", "-v", "error", "-ss", f"{t:.3f}", "-i", str(path),
            "-frames:v", "1", "-vf", "scale=640:-2", "-f", "image2pipe", "-vcodec", "png", "-",
        ])
        if raw:
            im = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
            if im is not None:
                frames.append(im)
    if not frames:
        raw = subprocess.check_output([
            "ffmpeg", "-nostdin", "-v", "error", "-i", str(path),
            "-frames:v", "1", "-vf", "scale=640:-2", "-f", "image2pipe", "-vcodec", "png", "-",
        ])
        im = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        if im is not None:
            frames.append(im)
    return frames


def analyze_media(path: Path, *, in_s: float = 0.0, dur_s: float = 4.0) -> dict:
    """Detect faces & visual saliency focus across sampled frames, returning normalized [0..1] geometry."""
    frames = _decode_frames(path, in_s=in_s, dur_s=dur_s)
    if not frames:
        raise ValueError(f"No decodable frames in {path}")

    face_boxes = []
    sal_centers = []
    sal_boxes = []
    backend = "opencv"

    # Prefer macOS Apple Vision Neural Engine face + objectness + attention heatmap peak when available.
    vision_res = _run_apple_vision(frames)
    if vision_res is not None:
        backend = "apple-vision"
        v_faces, v_sal, v_peaks = vision_res
        face_boxes.extend(v_faces)
        for (x0, y0, x1, y1) in v_sal:
            sal_boxes.append((x0, y0, x1, y1))
            box_w = x1 - x0
            # When the bounding box is tight, lock onto its center; when overly wide, prefer neural heatmap peak.
            if box_w <= 0.48 or not v_peaks:
                sal_centers.append(((x0 + x1) * 0.5, (y0 + y1) * 0.5))
        if not sal_centers and v_peaks:
            sal_centers.extend(v_peaks)
            if not sal_boxes:
                for (px, py) in v_peaks:
                    sal_boxes.append((max(0.0, px - 0.18), max(0.0, py - 0.22), min(1.0, px + 0.18), min(1.0, py + 0.22)))

    if not face_boxes and not sal_boxes:
        cascade = cv2.CascadeClassifier(str(FACE_CASCADE_PATH)) if FACE_CASCADE_PATH.is_file() else None
        for bgr in frames:
            h, w = bgr.shape[:2]
            gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            if cascade is not None and not cascade.empty():
                faces = cascade.detectMultiScale(gray, scaleFactor=1.12, minNeighbors=5, minSize=(int(w * 0.06), int(h * 0.06)))
                for (fx, fy, fw, fh) in faces:
                    face_boxes.append((fx / w, fy / h, (fx + fw) / w, (fy + fh) / h))

            blur_s = cv2.GaussianBlur(gray.astype(np.float32), (5, 5), 0)
            gx = cv2.Sobel(blur_s, cv2.CV_32F, 1, 0, ksize=3)
            gy = cv2.Sobel(blur_s, cv2.CV_32F, 0, 1, ksize=3)
            mag = cv2.magnitude(gx, gy)
            yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
            prior = np.exp(-(((xx / w - 0.5) / 0.45) ** 2 + ((yy / h - 0.48) / 0.45) ** 2))
            sal = cv2.GaussianBlur(mag * prior, (31, 31), 0)
            thresh = float(np.percentile(sal, 86))
            mask = sal >= max(thresh, 1e-3)
            ys, xs = np.nonzero(mask)
            if len(xs):
                weights = sal[ys, xs]
                cx = float(np.average(xs, weights=weights)) / w
                cy = float(np.average(ys, weights=weights)) / h
                x0, x1 = float(np.percentile(xs, 10)) / w, float(np.percentile(xs, 90)) / w
                y0, y1 = float(np.percentile(ys, 10)) / h, float(np.percentile(ys, 90)) / h
                sal_centers.append((cx, cy))
                sal_boxes.append((x0, y0, x1, y1))

    if face_boxes:
        x0 = min(b[0] for b in face_boxes)
        y0 = min(b[1] for b in face_boxes)
        x1 = max(b[2] for b in face_boxes)
        y1 = max(b[3] for b in face_boxes)
        cx, cy = (x0 + x1) * 0.5, (y0 + y1) * 0.5
        method = "face"
    elif sal_centers:
        cx = float(np.median([c[0] for c in sal_centers]))
        cy = float(np.median([c[1] for c in sal_centers]))
        x0 = float(np.median([b[0] for b in sal_boxes]))
        y0 = float(np.median([b[1] for b in sal_boxes]))
        x1 = float(np.median([b[2] for b in sal_boxes]))
        y1 = float(np.median([b[3] for b in sal_boxes]))
        method = "saliency"
    else:
        cx, cy, x0, y0, x1, y1, method = 0.5, 0.48, 0.25, 0.25, 0.75, 0.75, "fallback"

    # Recommend caption_avoid only if subject/face genuinely enters bottom caption lane (y > 0.78)
    avoid = []
    if y1 > 0.78 and (x1 - x0) < 0.75:
        avoid.append(dict(
            label="自动识别主体焦点" if method == "saliency" else "人脸主体保护区",
            box=[round(max(0.0, x0 - 0.02), 3), round(max(0.0, y0 - 0.02), 3),
                 round(min(1.0, x1 + 0.02), 3), round(min(1.0, y1 + 0.02), 3)],
        ))

    return dict(
        backend=backend,
        method=method,
        faces_detected=len(face_boxes),
        focal_point=[round(cx, 3), round(cy, 3)],
        subject_box=[round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3)],
        portrait_center_x=round(max(0.22, min(0.78, cx)), 3),
        recommended_caption_avoid=avoid,
    )


def analyze_project(project: Path, *, write: bool = False, apply_covers: bool = False) -> dict:
    project = project.resolve()
    edit_path = project / "edit.json"
    edit = json.loads(edit_path.read_text(encoding="utf-8"))
    tl_path = project / "timeline.json"
    tl = json.loads(tl_path.read_text(encoding="utf-8")) if tl_path.is_file() else {}
    fps = tl.get("fps", edit.get("fps", 30))
    dur_map = {s["id"]: s["frames"] / fps for s in tl.get("shots", [])}

    rows = []
    written_avoid = 0
    detected_backend = "apple-vision" if _vision_binary() is not None else "opencv"
    for shot in edit.get("shots", []):
        sid = shot["id"]
        vis = shot.get("visual")
        if not vis:
            continue
        p = (project / vis).resolve()
        if not p.is_file():
            rows.append(dict(shot=sid, status="missing", visual=vis))
            continue
        info = analyze_media(p, in_s=float(shot.get("in", 0.0) or 0.0), dur_s=dur_map.get(sid, 4.0))
        detected_backend = info["backend"]
        if write and info["recommended_caption_avoid"] and not shot.get("caption_avoid"):
            shot["caption_avoid"] = info["recommended_caption_avoid"]
            written_avoid += 1
        rows.append(dict(shot=sid, status="ok", **info))

    if write:
        edit_path.write_text(json.dumps(edit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    cover_updated = False
    if apply_covers:
        cov_path = project / "cover.json"
        if cov_path.is_file():
            cov = json.loads(cov_path.read_text(encoding="utf-8"))
            img_rel = cov.get("image") or (edit["shots"][0]["visual"] if edit.get("shots") else None)
            if img_rel and (project / img_rel).is_file():
                cov["focus"] = analyze_media((project / img_rel).resolve())["focal_point"]
                cov_path.write_text(json.dumps(cov, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
                cover_updated = True

    return dict(
        project=str(project), backend=detected_backend, analyzed_shots=len(rows),
        written_caption_avoid=written_avoid, cover_focus_updated=cover_updated, shots=rows,
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", type=Path)
    ap.add_argument("--file", type=Path)
    ap.add_argument("--track", action="store_true", help="Sample a moving video over time and output smooth pan-and-scan keyframes")
    ap.add_argument("--interval", type=float, default=0.5, help="Tracking sample interval in seconds (default 0.5)")
    ap.add_argument("--duration", type=float, default=4.0, help="Tracking duration in seconds for --file --track")
    ap.add_argument("--fps", type=float, default=30.0)
    ap.add_argument("--write", action="store_true", help="Write detected bottom-lane caption_avoid boxes into edit.json")
    ap.add_argument("--apply-covers", action="store_true", help="Also write detected focal_point to cover.json.focus")
    args = ap.parse_args()
    if args.file:
        if args.track:
            print(json.dumps(track_media(args.file, sample_interval=args.interval, dur_s=args.duration, fps=args.fps), ensure_ascii=False, indent=2))
        else:
            print(json.dumps(analyze_media(args.file), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(analyze_project(args.project or Path("."), write=args.write, apply_covers=args.apply_covers), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
