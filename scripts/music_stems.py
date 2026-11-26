#!/usr/bin/env python3
"""Multi-stem adaptive music mixer locked to timeline.json (supports real WAV stems + optional AI music APIs).

Allows top-creator dynamic musical arc building: start with sparse piano/pad in the setup,
bring in bass + rhythmic pulse for investigation, drop to near-silence at Re-hook, and bloom
full drums + strings at the climax—using either local multi-stem packs or cue_score stems.

Usage:
  music_stems.py providers
  music_stems.py mix stems-plan.json --timeline timeline.json --out audio/music.wav
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
from pathlib import Path
import subprocess
import wave

import numpy as np

RATE = 48000
ASSETS = Path(__file__).resolve().parents[1] / "assets"
PACKS_ROOT = ASSETS / "music-stems"


def list_packs() -> dict:
    """List bundled CC0 multi-stem music packs shipped under assets/music-stems/."""
    packs = {}
    if not PACKS_ROOT.is_dir():
        return packs
    for meta_path in sorted(PACKS_ROOT.glob("*/pack.json")):
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        packs[meta_path.parent.name] = dict(
            name=meta.get("name", meta_path.parent.name),
            bpm=meta.get("bpm"),
            key=meta.get("key"),
            mood=meta.get("mood"),
            license=meta.get("license", "CC0 1.0"),
            stems=sorted(meta.get("stems", {}).keys()),
            dir=str(meta_path.parent),
        )
    return packs


def _resolve_stem_path(raw: str, base_dir: Path) -> Path:
    if isinstance(raw, str) and raw.startswith("@pack/"):
        rel = raw[len("@pack/"):]
        target = (PACKS_ROOT / rel).resolve()
        if PACKS_ROOT.resolve() not in target.parents:
            raise ValueError(f"Stem pack path escapes assets/music-stems: {raw}")
        return target
    return base_dir / raw


def build_pack_plan(pack_name: str, timeline: dict) -> dict:
    """Auto-build a 4-act dynamic stems-plan.json mapped to the shots in timeline.json."""
    pack_dir = PACKS_ROOT / pack_name
    meta_path = pack_dir / "pack.json"
    if not meta_path.is_file():
        raise FileNotFoundError(f"Unknown music stem pack {pack_name!r}; run `music_stems.py packs` to list")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    shots = timeline.get("shots") or []
    if not shots:
        raise ValueError("timeline.json has no shots")
    stems = {k: f"@pack/{pack_name}/{v}" for k, v in meta["stems"].items()}

    n = len(shots)
    if n <= 2:
        sections = [
            {"start": shots[0]["id"], "active": {"pad": 0.80, "piano": 0.85, "bass": 0.70, "percussion": 0.65}},
        ]
    elif n <= 4:
        sections = [
            {"start": shots[0]["id"], "until": shots[1]["id"], "crossfade": 0.55,
             "active": {"pad": 0.75, "piano": 0.85}},
            {"start": shots[1]["id"], "until": shots[-1]["id"], "crossfade": 0.65,
             "active": {"pad": 0.75, "piano": 0.90, "bass": 0.82, "percussion": 0.80}},
            {"start": shots[-1]["id"], "crossfade": 0.70,
             "active": {"pad": 0.72, "piano": 0.75}},
        ]
    else:
        i1 = max(1, n // 4)
        i2 = max(i1 + 1, (n * 2) // 3)
        i3 = n - 1
        sections = [
            {"start": shots[0]["id"], "until": shots[i1]["id"], "crossfade": 0.55,
             "active": {"pad": 0.75, "piano": 0.85}, "role": "setup"},
            {"start": shots[i1]["id"], "until": shots[i2]["id"], "crossfade": 0.65,
             "active": {"pad": 0.70, "piano": 0.85, "bass": 0.80, "percussion": 0.65}, "role": "investigation"},
            {"start": shots[i2]["id"], "until": shots[i3]["id"], "crossfade": 0.60,
             "active": {"pad": 0.82, "piano": 0.95, "bass": 0.88, "percussion": 0.90}, "role": "climax"},
            {"start": shots[i3]["id"], "crossfade": 0.75,
             "active": {"pad": 0.70, "piano": 0.75}, "role": "outro"},
        ]
    return {
        "schema": 1,
        "pack": pack_name,
        "bpm": meta.get("bpm"),
        "license": meta.get("license", "CC0 1.0"),
        "key": meta.get("key", "A minor"),
        "fade_in": 0.20,
        "fade_out": 0.60,
        "fills": True,
        "outro_cadence": True,
        "stems": stems,
        "sections": sections,
    }


def provider_status() -> dict:
    """Report bundled CC0 stem packs and optional AI music API availability."""
    return dict(
        bundled_cc0_packs=list_packs(),
        offline_stem_mixer={"configured": True, "note": "免 Key，支持内置 CC0 多轨分轨乐包（@pack/...）及个人库分轨 WAV"},
        optional_ai_music={
            "elevenlabs-music": {
                "configured": bool(os.getenv("ELEVENLABS_API_KEY")),
                "env": ["ELEVENLABS_API_KEY"],
                "url": "https://elevenlabs.io/app/settings/api-keys",
                "note": "可选商业 AI 配乐/音效 API",
            },
            "suno-proxy": {
                "configured": bool(os.getenv("SUNO_API_KEY")),
                "env": ["SUNO_API_KEY", "SUNO_API_BASE"],
                "url": "https://suno.com",
                "note": "可选第三方 Suno 兼容接口",
            },
            "musicgen-local": {
                "configured": False,
                "env": [],
                "url": "https://github.com/facebookresearch/audiocraft",
                "note": "可选本地开源模型（pip install audiocraft）",
            },
        },
    )


def _decode_stem(path: Path, total_samples: int, *, loop: bool = True) -> np.ndarray:
    """Decode any audio file into stereo 48kHz float32, looping smoothly if shorter than total_samples."""
    if not path.is_file():
        raise FileNotFoundError(f"Music stem not found: {path}")
    cmd = ["ffmpeg", "-nostdin", "-v", "error"]
    if loop:
        cmd.extend(["-stream_loop", "-1"])
    dur_s = total_samples / RATE + 0.05
    cmd.extend(["-i", str(path), "-t", f"{dur_s:.6f}", "-ar", str(RATE), "-ac", "2", "-f", "f32le", "-"])
    raw = subprocess.check_output(cmd)
    arr = np.frombuffer(raw, dtype="<f4").reshape(-1, 2).copy()
    if len(arr) >= total_samples:
        return arr[:total_samples]
    out = np.zeros((total_samples, 2), dtype=np.float32)
    out[:len(arr)] = arr
    return out


def _tonic(key):
    primary = str(key).split("/")[0].strip()
    match = re.fullmatch(r"([A-Ga-g])([#b♯♭]?)(?:\s*(major|minor|maj|min|m))?", primary, re.IGNORECASE)
    if match is None:
        raise ValueError(f"Unsupported key {key!r} for an outro cadence")
    note, accidental, mode = match.groups()
    root = {"C": 48, "D": 50, "E": 52, "F": 53, "G": 55, "A": 45, "B": 47}[note.upper()]
    root += {"": 0, "#": 1, "♯": 1, "b": -1, "♭": -1}[accidental.lower()]
    return root, (mode or "").lower() in ("minor", "min", "m")


def _transition_fills(master, section_starts, sections, key):
    import cue_score
    from production_run import digest

    if len(sections) < 2:
        return []
    crash_dir = ASSETS / "sfx/cymbal-swell-cc0"
    crash = crash_dir / "take.wav"
    meta = json.loads((crash_dir / "source.json").read_text())
    if digest(crash) != meta["wav_sha256"]:
        raise ValueError("Recorded cymbal fill checksum mismatch")
    anchor = round(meta["anchor"] * RATE)
    swell = _decode_stem(crash, round(meta["duration"] * RATE), loop=False)
    root, _ = _tonic(key)
    reverse_piano = cue_score.instrument("piano", root - 57, anchor / RATE)[::-1]
    piano_len = min(anchor, len(reverse_piano))
    swell[anchor-piano_len:anchor] += reverse_piano[-piano_len:, None] * .085
    swell *= np.linspace(0, 1, len(swell), dtype=np.float32)[:, None] ** .8
    kick = _decode_stem(ASSETS / "instruments/kick.wav", round(.28 * RATE), loop=False)
    hits = []
    for cut, previous, current in zip(section_starts[1:], sections, sections[1:]):
        if cut < anchor or len(master) - cut < .10 * RATE:
            continue
        increase = len(current["active_stems"]) > len(previous["active_stems"])
        start = cut - anchor
        end = min(len(master), start + len(swell))
        master[start:end] += swell[:end-start] * (.16 if increase else .085)
        if increase:
            end = min(len(master), cut + len(kick))
            master[cut:end] += kick[:end-cut] * .27
        hits.append(dict(shot=current["start"], cut_sample=cut, start_sample=start, downbeat=increase))
    return hits


def _outro_cadence(master, key):
    import cue_score

    tonic, minor = _tonic(key)
    length = min(len(master) // 3, round(2.35 * RATE))
    join = round(.42 * RATE)
    chord_length = length - join
    cad = np.zeros((length, 2), dtype=np.float32)
    # A quiet dominant leads to a spread tonic triad with a held acoustic organ pad.
    lead = cue_score.instrument("piano", [tonic + step - 57 for step in (7, 11, 14)], join / RATE)
    cad[:len(lead)] += lead[:, None] * .072
    for index, step in enumerate((0, 3 if minor else 4, 7, 12)):
        note = cue_score.instrument("piano", tonic + step - 57, chord_length / RATE)
        pan = .15 * (index - 1.5)
        gain = .14 if index < 3 else .09
        cad[join:join+len(note), 0] += note * gain * math.sqrt((1 - pan) * .5)
        cad[join:join+len(note), 1] += note * gain * math.sqrt((1 + pan) * .5)
    pad = cue_score.instrument("pad", [tonic + step - 45 for step in (0, 7)], chord_length / RATE)
    cad[join:join+len(pad)] += pad[:, None] * .095
    cad = cue_score.apply_room_reverb(cad, wet=.32)
    master[-length:] = master[-length:] * np.linspace(1, .18, length, dtype=np.float32)[:, None] + cad
    return dict(start_sample=len(master) - length, tonic_sample=len(master) - length + join)


def mix_stems_plan(plan: dict, timeline: dict, base_dir: Path, out_path: Path) -> dict:
    """Mix named stems with smooth act-by-act raised-cosine crossfades locked to timeline.json."""
    fps = timeline["fps"]
    total_frames = timeline["frames"]
    total_samples = round(total_frames * RATE / fps)
    starts = {s["id"]: round(s["start_frame"] * RATE / fps) for s in timeline["shots"]}

    stems_cfg = plan.get("stems") or {}
    if not stems_cfg:
        raise ValueError("stems-plan.json requires a non-empty 'stems' mapping")
    sections = plan.get("sections") or []
    if not sections:
        raise ValueError("stems-plan.json requires a non-empty 'sections' list")

    decoded = {}
    for name, cfg in stems_cfg.items():
        if isinstance(cfg, str):
            p, loop = _resolve_stem_path(cfg, base_dir), True
        else:
            p, loop = _resolve_stem_path(cfg["file"], base_dir), bool(cfg.get("loop", True))
        decoded[name] = _decode_stem(p, total_samples, loop=loop)

    # Build smooth per-stem gain envelopes across all sections
    envelopes = {name: np.zeros(total_samples, dtype=np.float32) for name in decoded}
    resolved_sections = []
    section_starts = []
    for idx, sec in enumerate(sections):
        shot_id = sec["start"]
        until_id = sec.get("until")
        if shot_id not in starts or (until_id and until_id not in starts):
            raise ValueError(f"Unknown shot id in stems section {idx}: {shot_id} -> {until_id}")
        s0 = max(0, starts[shot_id] + round(float(sec.get("offset", 0.0)) * RATE))
        s1 = total_samples if not until_id else min(total_samples, starts[until_id] + round(float(sec.get("end_offset", 0.0)) * RATE))
        if s1 <= s0:
            raise ValueError(f"Empty stem section window at {shot_id}")
        xfade = max(0.05, float(sec.get("crossfade", 0.65)))
        xf_n = min((s1 - s0) // 2, round(xfade * RATE))
        active = sec.get("active") or {}
        if isinstance(active, list):
            active = {k: 1.0 for k in active}
        for name in decoded:
            target_g = float(active.get(name, 0.0))
            if target_g <= 0.0:
                continue
            seg = np.full(s1 - s0, target_g, dtype=np.float32)
            if xf_n > 0:
                ramp_in = (0.5 * (1.0 - np.cos(np.pi * np.linspace(0, 1, xf_n)))).astype(np.float32)
                ramp_out = (0.5 * (1.0 + np.cos(np.pi * np.linspace(0, 1, xf_n)))).astype(np.float32)
                if s0 > 0:
                    seg[:xf_n] *= ramp_in
                if s1 < total_samples:
                    seg[-xf_n:] *= ramp_out
            envelopes[name][s0:s1] = np.maximum(envelopes[name][s0:s1], seg)
        section_starts.append(s0)
        resolved_sections.append(dict(
            start=shot_id, until=until_id, start_s=round(s0 / RATE, 3), end_s=round(s1 / RATE, 3),
            active_stems=sorted(k for k, v in active.items() if float(v) > 0),
        ))

    master = np.zeros((total_samples, 2), dtype=np.float32)
    for name, audio in decoded.items():
        master += audio * envelopes[name][:, None]

    key = plan.get("key", "A minor")
    fills = _transition_fills(master, section_starts, resolved_sections, key) if plan.get("fills") else []
    cadence = None
    if plan.get("outro_cadence") and total_samples >= 3 * RATE:
        cadence = _outro_cadence(master, key)

    # Head/tail polish & headroom normalization
    fade_in_n = min(total_samples // 4, round(float(plan.get("fade_in", 0.15)) * RATE))
    fade_out_n = min(total_samples // 4, round(float(plan.get("fade_out", 0.50)) * RATE))
    if fade_in_n > 0:
        master[:fade_in_n] *= (0.5 * (1.0 - np.cos(np.pi * np.linspace(0, 1, fade_in_n)))).astype(np.float32)[:, None]
    if fade_out_n > 0:
        master[-fade_out_n:] *= (0.5 * (1.0 + np.cos(np.pi * np.linspace(0, 1, fade_out_n)))).astype(np.float32)[:, None]

    peak = float(np.max(np.abs(master)))
    headroom = float(plan.get("peak_ceiling", 0.85))
    if peak > headroom and peak > 1e-9:
        master *= headroom / peak

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_path), "wb") as wf:
        wf.setparams((2, 2, RATE, 0, "NONE", "not compressed"))
        wf.writeframes(np.clip(master * 32767.0, -32768, 32767).astype("<i2").tobytes())

    return dict(
        output=str(out_path),
        seconds=round(total_samples / RATE, 3),
        stem_count=len(decoded),
        sections=resolved_sections,
        fills_added=len(fills),
        fills=fills,
        outro_cadence_added=cadence is not None,
        cadence=cadence,
        peak_after_mix=round(float(np.max(np.abs(master))), 4),
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("providers", help="Show bundled CC0 stem packs and optional AI music API readiness")
    sub.add_parser("packs", help="List bundled CC0 multi-stem music packs")
    ip = sub.add_parser("init-plan", help="Create a ready-to-mix multi-act stems-plan.json from a bundled CC0 pack")
    ip.add_argument("--pack", default="tech-investigation", help="Bundled pack name (see `music_stems.py packs`)")
    ip.add_argument("--timeline", type=Path, default=Path("timeline.json"))
    ip.add_argument("--out", type=Path, default=Path("stems-plan.json"))
    mx = sub.add_parser("mix", help="Mix multi-stem music plan locked to timeline.json")
    mx.add_argument("plan", type=Path)
    mx.add_argument("--timeline", type=Path, default=Path("timeline.json"))
    mx.add_argument("--out", type=Path, default=Path("audio/music.wav"))
    mx.add_argument("--beats-out", type=Path, help="Optional beat-grid JSON path when plan declares bpm")
    args = ap.parse_args()
    if args.cmd == "providers":
        print(json.dumps(provider_status(), ensure_ascii=False, indent=2))
    elif args.cmd == "packs":
        print(json.dumps(list_packs(), ensure_ascii=False, indent=2))
    elif args.cmd == "init-plan":
        tl = json.loads(args.timeline.read_text(encoding="utf-8"))
        plan = build_pack_plan(args.pack, tl)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"written": str(args.out), "pack": args.pack, "bpm": plan.get("bpm"), "sections": len(plan["sections"])}, ensure_ascii=False, indent=2))
    else:
        plan = json.loads(args.plan.read_text(encoding="utf-8"))
        tl = json.loads(args.timeline.read_text(encoding="utf-8"))
        res = mix_stems_plan(plan, tl, args.plan.resolve().parent, args.out)
        if args.beats_out and plan.get("bpm"):
            fps = tl["fps"]
            total_s = tl["frames"] / fps
            bpm = float(plan["bpm"])
            beat_s = 60.0 / bpm
            beats = []
            step = 0
            while step * beat_s < total_s - 1e-6:
                t = step * beat_s
                beats.append({"seconds": round(t, 4), "frame": round(t * fps), "section": 0, "bpm": bpm,
                              "beat_in_bar": (step % 4) + 1, "is_downbeat": (step % 4) == 0})
                step += 1
            args.beats_out.parent.mkdir(parents=True, exist_ok=True)
            args.beats_out.write_text(json.dumps({
                "schema": 1, "fps": fps, "frames": tl["frames"], "total_seconds": round(total_s, 4),
                "beat_count": len(beats), "beats": beats,
            }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            res["beats_file"] = str(args.beats_out)
        print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
