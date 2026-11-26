#!/usr/bin/env python3
"""Import and calibrate high-dynamic real human 5-mood reference recordings into assets/voices/jianqiu/voice.json.

Normalizes any WAV/MP3/M4A/FLAC recording to 24kHz mono 16-bit PCM (-18 LUFS target, trimmed edges),
computes SHA-256 provenance, verifies duration (2.0–14.0s for ZipVoice reference prompts),
and safely updates assets/voices/jianqiu/voice.json without overwriting untouched moods.

Usage:
  voice_mood_pack.py status
  voice_mood_pack.py import --mood hook --audio raw/hook_take.wav --text "你绝对想不到这行代码藏着什么秘密"
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import wave

import numpy as np

from jianqiu_voice import verified_prompt
from production_run import atomic_json
from story_craft import MOOD_PROFILES

VOICE_DIR = Path(__file__).resolve().parents[1] / "assets/voices/jianqiu"
VALID_MOODS = tuple(k for k in MOOD_PROFILES if k != "neutral")


def inspect_status(*, voice_dir=None) -> dict:
    voice_dir = VOICE_DIR if voice_dir is None else Path(voice_dir)
    cfg = json.loads((voice_dir / "voice.json").read_text(encoding="utf-8"))
    moods = {}
    for mood in VALID_MOODS:
        entry = (cfg.get("moods") or {}).get(mood)
        if not entry:
            moods[mood] = {"configured": False}
            continue
        row = {
            "configured": False,
            "prompt": entry["prompt"],
            "seconds": 0.0,
            "sha_verified": False,
            "source": entry.get("source", "bundled"),
            "prompt_text": entry.get("prompt_text", ""),
        }
        try:
            pcm, _ = verified_prompt(voice_dir, entry, cfg["sample_rate"])
        except (OSError, ValueError, KeyError) as exc:
            row["error"] = str(exc)
        else:
            row.update(configured=True, seconds=round(len(pcm) / cfg["sample_rate"], 3), sha_verified=True)
        moods[mood] = row
    return {"voice_dir": str(voice_dir), "sample_rate": cfg["sample_rate"], "moods": moods}


def import_mood(mood: str, audio_path: Path, prompt_text: str, *,
                source_note: str = "real-human-recording", voice_dir=None) -> dict:
    if mood not in VALID_MOODS:
        raise ValueError(f"Unknown mood {mood!r}; choose from {', '.join(VALID_MOODS)}")
    if not audio_path.is_file():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")
    if len(prompt_text.strip()) < 4:
        raise ValueError("Provide the exact spoken Chinese transcript for --text (>= 4 characters)")

    voice_dir = VOICE_DIR if voice_dir is None else Path(voice_dir)
    manifest = voice_dir / "voice.json"
    sr = 24000
    if json.loads(manifest.read_text(encoding="utf-8"))["sample_rate"] != sr:
        raise ValueError("ZipVoice mood packs require a 24000 Hz voice profile")

    with tempfile.TemporaryDirectory(dir=voice_dir, prefix=".mood-") as td:
        tmp = Path(td) / "prompt.wav"
        # Convert to 24kHz mono 16-bit PCM, gently highpass rumble, trim silence edges, normalize loudness
        af = (
            "highpass=f=70,"
            "silenceremove=start_periods=1:start_silence=0.04:start_threshold=-42dB,"
            "areverse,silenceremove=start_periods=1:start_silence=0.05:start_threshold=-42dB,areverse,"
            "loudnorm=I=-18:TP=-1.5:LRA=7"
        )
        subprocess.run([
            "ffmpeg", "-y", "-nostdin", "-v", "error", "-i", str(audio_path),
            "-af", af, "-ar", str(sr), "-ac", "1", "-c:a", "pcm_s16le", str(tmp),
        ], check=True)
        with wave.open(str(tmp)) as wf:
            pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
            dur = wf.getnframes() / wf.getframerate()
        if not (2.0 <= dur <= 14.0):
            raise ValueError(f"Trimmed mood prompt duration is {dur:.2f}s; ZipVoice reference prompts should be 2.0–14.0s")
        rms_db = round(20.0 * float(np.log10(max(1e-5, float(np.sqrt(np.mean(pcm ** 2)))))), 2)
        sha = hashlib.sha256(tmp.read_bytes()).hexdigest()
        out_path = voice_dir / f"prompt-{mood}-{sha}.wav"
        entry = {
            "prompt": out_path.name,
            "prompt_sha256": sha,
            "prompt_text": prompt_text.strip(),
            "seconds": round(dur, 3),
            "rms_db": rms_db,
            "source": source_note,
        }
        with (voice_dir / ".voice-moods.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            cfg = json.loads(manifest.read_text(encoding="utf-8"))
            if cfg["sample_rate"] != sr:
                raise ValueError("Voice profile sample rate changed during mood import")
            # Commit the manifest last. Concurrent readers can still use the previous file.
            tmp.replace(out_path)
            cfg.setdefault("moods", {})[mood] = entry
            atomic_json(manifest, cfg)
    return {"mood": mood, "output": str(out_path), "seconds": round(dur, 3), "rms_db": rms_db, "sha256": sha}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("status", help="Inspect current 5-mood voice prompt pack status and SHA-256 verification")
    imp = sub.add_parser("import", help="Import a real recording as a ZipVoice mood reference prompt")
    imp.add_argument("--mood", required=True, choices=VALID_MOODS)
    imp.add_argument("--audio", required=True, type=Path)
    imp.add_argument("--text", required=True, help="Exact spoken transcript of the reference audio")
    imp.add_argument("--source", default="real-human-recording")
    args = ap.parse_args()
    if args.cmd == "status":
        print(json.dumps(inspect_status(), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(import_mood(args.mood, args.audio, args.text, source_note=args.source), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
