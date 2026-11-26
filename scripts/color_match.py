#!/usr/bin/env python3
"""Hero-Frame automatic cross-shot color/exposure/temperature matching across edit.json.

Samples representative frames from a chosen hero shot (or image) and every live-action / archive / AI
video shot in edit.json, computes CIE Lab & saturation deltas, and produces restrained broadcast-safe
per-shot `grade` parameters compatible with `color_finish.py` (automatically skipping brand graphics).

Usage:
  color_match.py --project videos/my-film --hero s01
  color_match.py --project videos/my-film --hero assets/hero.jpg --write
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import subprocess

import cv2
import numpy as np

from color_finish import GENERATED_SHOT_KEYS

STILLS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
SKIP_KEYS = (*GENERATED_SHOT_KEYS, "math_motion", "geo_map")


def _sample_frame_bgr(path: Path, seek_s: float = 0.4) -> np.ndarray:
    """Decode one representative upright RGB/BGR frame from any image or video."""
    if not path.is_file():
        raise FileNotFoundError(f"Visual source not found: {path}")
    if path.suffix.lower() in STILLS:
        img = cv2.imread(str(path))
        if img is None:
            raise ValueError(f"Unreadable image: {path}")
        return cv2.resize(img, (480, 270), interpolation=cv2.INTER_AREA)
    cmd = [
        "ffmpeg", "-nostdin", "-v", "error", "-ss", f"{max(0.0, seek_s):.3f}",
        "-i", str(path), "-frames:v", "1",
        "-vf", "scale=480:270:force_original_aspect_ratio=decrease",
        "-f", "image2pipe", "-vcodec", "png", "-",
    ]
    raw = subprocess.check_output(cmd)
    if not raw:
        # Retry at t=0 for very short clips
        cmd[5] = "0.000"
        raw = subprocess.check_output(cmd)
    arr = np.frombuffer(raw, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"Could not decode video frame from {path}")
    return img


def _color_stats(bgr: np.ndarray) -> dict:
    """Measure middle-88% CIE Lab luminance/chroma, HSV saturation and YCrCb human skin ratio."""
    h, w = bgr.shape[:2]
    crop = bgr[int(h * 0.06):int(h * 0.94), int(w * 0.06):int(w * 0.94)]
    lab = cv2.cvtColor(crop, cv2.COLOR_BGR2LAB).astype(np.float32)
    L = lab[:, :, 0] * (100.0 / 255.0)
    a = lab[:, :, 1] - 128.0
    b = lab[:, :, 2] - 128.0
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV).astype(np.float32)
    sat = hsv[:, :, 1] / 255.0
    # Standard broadcast YCrCb skin-tone detector protects faces against heavy white-balance shifts.
    ycrcb = cv2.cvtColor(crop, cv2.COLOR_BGR2YCrCb)
    Y, Cr, Cb = ycrcb[:, :, 0], ycrcb[:, :, 1], ycrcb[:, :, 2]
    skin_mask = (Y > 45) & (Cr >= 133) & (Cr <= 173) & (Cb >= 77) & (Cb <= 127)
    skin_ratio = float(np.mean(skin_mask))
    return dict(
        L_mean=float(np.mean(L)),
        L_std=float(np.std(L)),
        a_mean=float(np.mean(a)),
        b_mean=float(np.mean(b)),
        sat_mean=float(np.mean(sat)),
        skin_ratio=round(skin_ratio, 4),
    )


def _delta_e(s1: dict, s2: dict) -> float:
    return math.sqrt(
        (s1["L_mean"] - s2["L_mean"]) ** 2
        + (s1["a_mean"] - s2["a_mean"]) ** 2
        + (s1["b_mean"] - s2["b_mean"]) ** 2
    )


def _simulate_after_stats(bgr: np.ndarray, grade: dict) -> dict:
    """Apply real color_finish FFmpeg 10-bit Rec.709 grade filter to sampled frame to measure exact post-match ΔE."""
    if not grade:
        return _color_stats(bgr)
    import color_finish
    import tempfile
    h, w = bgr.shape[:2]
    info = {"width": w, "height": h, "pix_fmt": "rgb24", "codec_name": "png"}
    with tempfile.TemporaryDirectory(prefix="cm-ver-") as td:
        src_p = Path(td) / "in.png"
        dst_p = Path(td) / "out.png"
        cv2.imwrite(str(src_p), bgr)
        vf, _ = color_finish.graded_source_filter(info, grade=grade, raster_size=(w, h))
        srgb = ("colorspace=iall=bt709:irange=tv:all=bt709:trc=iec61966-2-1:range=tv:format=yuv444p10,"
                "scale=in_color_matrix=bt709:in_range=tv:out_range=pc,format=rgb24")
        subprocess.run([
            "ffmpeg", "-y", "-nostdin", "-v", "error", "-i", str(src_p),
            "-vf", f"{vf},{srgb}", "-frames:v", "1", str(dst_p),
        ], check=True)
        out = cv2.imread(str(dst_p))
        return _color_stats(out) if out is not None else _color_stats(bgr)


def compute_grade(shot_stats: dict, hero_stats: dict, *, strength: float = 0.72) -> tuple[dict, dict]:
    """Compute restrained, luminance-bucketed & skin-tone-protected color_finish grade."""
    s = max(0.1, min(1.0, float(strength)))
    dl = hero_stats["L_mean"] - shot_stats["L_mean"]
    cross_key = abs(dl) > 24.0
    skin_protected = shot_stats.get("skin_ratio", 0.0) >= 0.04

    # Luminance-bucket guard: don't crush bright daylight/paper scenes into muddy dark gray (or blow out night scenes).
    exp_scale = 0.35 if cross_key else 1.0
    # Skin-tone guard: damp chromatic temperature/tint shifts by 55% when human skin is present.
    chroma_scale = 0.45 if skin_protected else 1.0

    exp = max(-0.55, min(0.55, (dl / 48.0) * s * exp_scale))
    temp = max(-0.35, min(0.35, ((hero_stats["b_mean"] - shot_stats["b_mean"]) / 34.0) * s * chroma_scale))
    tint = max(-0.22, min(0.22, ((hero_stats["a_mean"] - shot_stats["a_mean"]) / 40.0) * s * chroma_scale))
    contrast = max(0.90, min(1.14, 1.0 + ((hero_stats["L_std"] - shot_stats["L_std"]) / 85.0) * s * (0.6 if cross_key else 1.0)))
    sat_ratio = hero_stats["sat_mean"] / max(0.06, shot_stats["sat_mean"])
    sat = max(0.82 if skin_protected else 0.80, min(1.14 if skin_protected else 1.18, 1.0 + (sat_ratio - 1.0) * 0.55 * s * chroma_scale))

    grade = {}
    if abs(exp) >= 0.03:
        grade["exposure"] = round(exp, 3)
    if abs(temp) >= 0.025:
        grade["temperature"] = round(temp, 3)
    if abs(tint) >= 0.025:
        grade["tint"] = round(tint, 3)
    if abs(contrast - 1.0) >= 0.02:
        grade["contrast"] = round(contrast, 3)
    if abs(sat - 1.0) >= 0.03:
        grade["saturation"] = round(sat, 3)
    guards = dict(skin_protected=skin_protected, skin_ratio=shot_stats.get("skin_ratio", 0.0),
                  luminance_bucket="cross-key" if cross_key else "matched-key")
    return grade, guards


def match_project(project: Path, hero_ref: str, *, strength: float = 0.72, write: bool = False) -> dict:
    project = project.resolve()
    edit_path = project / "edit.json"
    edit = json.loads(edit_path.read_text(encoding="utf-8"))
    shots = edit.get("shots", [])

    # Resolve hero reference (either shot id or relative/absolute file path)
    hero_shot = next((s for s in shots if s["id"] == hero_ref), None)
    if hero_shot is not None:
        hero_path = (project / hero_shot["visual"]).resolve()
        hero_seek = float(hero_shot.get("in", 0.0) or 0.0) + 0.5
    else:
        p = Path(hero_ref)
        hero_path = p if p.is_absolute() else (project / p).resolve()
        hero_seek = 0.5
    hero_stats = _color_stats(_sample_frame_bgr(hero_path, hero_seek))

    results = []
    matched_count = 0
    for shot in shots:
        sid = shot["id"]
        if hero_shot is not None and sid == hero_shot["id"]:
            results.append(dict(shot=sid, status="hero", delta_e=0.0, grade={}))
            continue
        if shot.get("grade") is False or any(k in shot for k in SKIP_KEYS):
            results.append(dict(shot=sid, status="skipped_graphic"))
            continue
        vis = shot.get("visual")
        if not vis:
            results.append(dict(shot=sid, status="no_visual"))
            continue
        src_path = (project / vis).resolve()
        if not src_path.is_file():
            results.append(dict(shot=sid, status="missing_file", visual=vis))
            continue
        seek = float(shot.get("in", 0.0) or 0.0) + 0.5
        bgr = _sample_frame_bgr(src_path, seek)
        st = _color_stats(bgr)
        de_before = round(_delta_e(st, hero_stats), 2)
        grade, guards = compute_grade(st, hero_stats, strength=strength)
        after_st = _simulate_after_stats(bgr, grade) if grade else st
        de_after = round(_delta_e(after_st, hero_stats), 2)
        if write and grade:
            existing = dict(shot.get("grade") or {}) if isinstance(shot.get("grade"), dict) else {}
            existing.update(grade)
            shot["grade"] = existing
        if grade:
            matched_count += 1
        results.append(dict(
            shot=sid, status="matched" if grade else "already_close",
            delta_e_before=de_before, delta_e_after=de_after,
            guards=guards, grade=grade,
            stats={k: round(v, 3) for k, v in st.items()},
        ))

    if write:
        edit_path.write_text(json.dumps(edit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return dict(
        project=str(project),
        hero=hero_ref,
        hero_stats={k: round(v, 2) for k, v in hero_stats.items()},
        strength=strength,
        written=write,
        matched_shots=matched_count,
        shots=results,
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--project", type=Path, default=Path("."))
    ap.add_argument("--hero", required=True, help="Hero shot id in edit.json OR path to hero reference image/video")
    ap.add_argument("--strength", type=float, default=0.72, help="Match pull strength 0.1–1.0 (default 0.72)")
    ap.add_argument("--write", action="store_true", help="Write computed per-shot grades into edit.json")
    args = ap.parse_args()
    print(json.dumps(match_project(args.project, args.hero, strength=args.strength, write=args.write), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
