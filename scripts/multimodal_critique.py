#!/usr/bin/env python3
"""Video-Native Multimodal Viewer Critique Packet generator & Edit-Patch planner.

Supports 4 critique backends via `--backend auto|deepseek|local-vlm|gemini|offline`:
  1. Built-in Zero-Dependency Offline Visual Critic (ALWAYS runs offline: motion energy, cross-cut
     CIE Lab color clash, Apple Vision subject-vs-subtitle occlusion, exposure extremes, dead-air gaps).
  2. DeepSeek API (`DEEPSEEK_API_KEY` or `--deepseek-api-key`, customizable `DEEPSEEK_BASE_URL` &
     `DEEPSEEK_MODEL`; sends 1fps vision storyboards for VL models OR full locally extracted visual
     telemetry for text models, with a text-only retry on HTTP 400. The default `deepseek-flash`
     sends images).
  3. Local Offline VLM (`LOCAL_VLM_URL` e.g. Ollama / LM Studio / MLX-VLM OpenAI-compatible server,
     or local `mlx_vlm.generate` CLI — 100% offline, zero cloud API key required).
  4. Optional Gemini native vision critique (`GEMINI_API_KEY`).

Usage:
  multimodal_critique.py providers
  multimodal_critique.py build --project videos/my-film --media out/review/.../candidate.mp4 [--backend auto|deepseek|local-vlm|gemini|offline]
  multimodal_critique.py patch --project videos/my-film --tickets qa/critique-packet/tickets.json [--apply]
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess

import cv2
import numpy as np
from PIL import Image, ImageDraw

from production_run import atomic_json, digest

LOCAL_VLM_CANDIDATES = (
    "http://127.0.0.1:11434/v1",  # Ollama
    "http://127.0.0.1:1234/v1",   # LM Studio
    "http://127.0.0.1:8080/v1",   # MLX-VLM / llama.cpp server
)
DEFAULT_DEEPSEEK_MODEL = "deepseek-flash"


def _parse_srt(path: Path) -> list[tuple[float, float, str]]:
    if not path.is_file():
        return []
    out = []
    for block in re.split(r"\n[ \t]*\n", path.read_text(encoding="utf-8-sig").strip()):
        lines = block.splitlines()
        if len(lines) < 3:
            continue
        m = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)", lines[1].strip())
        if not m:
            continue
        g = [int(x) for x in m.groups()]
        t0 = g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000.0
        t1 = g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000.0
        out.append((t0, t1, " ".join(lines[2:]).strip()))
    return out


def _audio_rms_per_second(media: Path, duration_s: float) -> list[float]:
    raw = subprocess.check_output([
        "ffmpeg", "-nostdin", "-v", "error", "-i", str(media),
        "-ac", "1", "-ar", "16000", "-f", "s16le", "-",
    ])
    pcm = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    secs = max(1, int(math.ceil(duration_s)))
    dbs = []
    for s in range(secs):
        seg = pcm[s * 16000:(s + 1) * 16000]
        if not len(seg):
            dbs.append(-60.0)
        else:
            rms = float(np.sqrt(np.mean(seg ** 2)))
            dbs.append(round(20.0 * math.log10(max(rms, 1e-4)), 1))
    return dbs


def _extract_1fps_frames(media: Path, tmp_dir: Path) -> list[Path]:
    tmp_dir.mkdir(parents=True, exist_ok=True)
    for old_frame in tmp_dir.glob("sec_[0-9][0-9][0-9][0-9][0-9].jpg"):
        old_frame.unlink()
    subprocess.run([
        "ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(media),
        "-vf", "fps=1:round=up:eof_action=pass,scale=320:180:force_original_aspect_ratio=decrease,pad=320:180:(ow-iw)/2:(oh-ih)/2",
        "-q:v", "3", str(tmp_dir / "sec_%05d.jpg"),
    ], check=True)
    return sorted(tmp_dir.glob("sec_[0-9][0-9][0-9][0-9][0-9].jpg"))


def _lab_stats(bgr: np.ndarray) -> dict:
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB).astype(np.float32)
    L = lab[:, :, 0] * (100.0 / 255.0)
    a = lab[:, :, 1] - 128.0
    b = lab[:, :, 2] - 128.0
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    edges = cv2.moments(cv2.Canny(gray, 45, 120))
    centroid = ([round(edges["m10"] / edges["m00"] / gray.shape[1], 3),
                 round(edges["m01"] / edges["m00"] / gray.shape[0], 3)] if edges["m00"] else None)
    return dict(
        edge_centroid=centroid,
        L_mean=float(np.mean(L)),
        L_std=float(np.std(L)),
        a_mean=float(np.mean(a)),
        b_mean=float(np.mean(b)),
        clipped_white=float(np.mean(gray >= 250)),
        crushed_black=float(np.mean(gray <= 8)),
    )


def _probe_local_vlm(custom_url: str | None = None, custom_model: str | None = None) -> dict | None:
    """Probe local OpenAI-compatible VLM server (Ollama / LM Studio / MLX-VLM) without external network."""
    import requests
    candidates = [custom_url] if custom_url else ([os.getenv("LOCAL_VLM_URL")] if os.getenv("LOCAL_VLM_URL") else list(LOCAL_VLM_CANDIDATES))
    for base in candidates:
        if not base:
            continue
        b = base.rstrip("/")
        if not b.endswith("/v1") and not b.endswith("/chat/completions"):
            models_url = b + "/v1/models"
            chat_url = b + "/v1/chat/completions"
        elif b.endswith("/chat/completions"):
            models_url = b.rsplit("/chat/completions", 1)[0] + "/models"
            chat_url = b
        else:
            models_url = b + "/models"
            chat_url = b + "/chat/completions"
        try:
            r = requests.get(models_url, timeout=0.8)
            if r.status_code == 200:
                payload = r.json()
                ids = [m.get("id") for m in (payload.get("data") or []) if isinstance(m, dict) and m.get("id")]
                preferred = custom_model or os.getenv("LOCAL_VLM_MODEL")
                if not preferred and ids:
                    vlm_ids = [i for i in ids if any(k in i.lower() for k in ("vl", "vision", "llava", "minicpm", "qwen2.5-vl", "qwen2-vl", "internvl", "gemma3"))]
                    preferred = vlm_ids[0] if vlm_ids else ids[0]
                return dict(url=chat_url, models_url=models_url, model=preferred or "qwen2.5vl", available_models=ids)
        except Exception:
            continue
    return None


def _extract_json_tickets(text: str) -> list[dict]:
    """Robustly extract structured critique tickets from LLM/VLM JSON or fenced output."""
    if not text or not isinstance(text, str):
        raise ValueError("Provider response was empty")
    cleaned = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if m:
        cleaned = m.group(1).strip()
    try:
        parsed = json.loads(cleaned)
    except ValueError:
        m2 = re.search(r"(\[.*\]|\{.*\})", cleaned, flags=re.DOTALL)
        if not m2:
            raise ValueError("Provider response did not contain critique JSON") from None
        parsed = json.loads(m2.group(1))
    if isinstance(parsed, dict):
        parsed = next((parsed[key] for key in ("tickets", "issues", "critiques") if key in parsed), None)
    if not isinstance(parsed, list):
        raise ValueError("Provider response did not contain a critique ticket list")
    valid = []
    for row in parsed:
        if not isinstance(row, dict):
            continue
        if not (row.get("issue") or row.get("suggestion") or row.get("detail")):
            continue
        try:
            time_s = float(row.get("time_s"))
        except (TypeError, ValueError):
            continue
        if not math.isfinite(time_s) or time_s < 0:
            continue
        geometry = {}
        if "subject_box" in row:
            from picture_reservations import normalized_box
            try:
                geometry["subject_box"] = normalized_box(row["subject_box"], "critique subject_box")
            except ValueError:
                pass
        valid.append(dict(
            time_s=time_s, **geometry,
            shot=str(row.get("shot", "unknown")),
            issue=str(row.get("issue", "editorial_note")),
            severity=str(row.get("severity", "medium")),
            fix_type=str(row.get("fix_type", "review")),
            detail=str(row.get("suggestion") or row.get("detail") or row.get("issue")),
            **({"end_s": row["end_s"]} if type(row.get("end_s")) in (int, float) and math.isfinite(row["end_s"])
               and row["end_s"] > time_s else {}),
        ))
    if parsed and not valid:
        raise ValueError("Provider response contained no valid critique tickets")
    return valid


def _build_critique_prompt(rubric: dict, *, include_visual_telemetry: bool = True) -> str:
    compact_segs = []
    for seg in rubric.get("segments", []):
        row = {
            "segment": seg["segment"], "start_s": seg["start_s"], "end_s": seg["end_s"],
            "mean_motion": seg["mean_motion"], "mean_audio_db": seg["mean_audio_db"],
            "spoken_transcript": seg["spoken_transcript"],
            "subtitle_cues": seg.get("subtitle_cues", []),
            "hotspots": [{"time_s": h["time_s"], "shot": h["shot"], "issue": h["issue"]} for h in seg.get("hotspots", [])],
        }
        if include_visual_telemetry and seg.get("visual_telemetry"):
            row["visual_telemetry"] = seg["visual_telemetry"]
        compact_segs.append(row)
    payload = {
        "duration_seconds": rubric.get("duration_seconds"),
        "summary": rubric.get("summary"),
        "segments": compact_segs,
    }
    return (
        "你是拥有百万粉丝的科技/知识区顶级视频主编与剪辑导演。请结合提供的时间线、逐段视觉特征（及故事板图像），"
        "以真实观众 1 倍速首次观看的直觉，严格输出 JSON 对象 {\"tickets\": [...]}。\n"
        "每条 ticket 必须包含：{time_s, shot, issue, severity(high|medium|low), "
        "fix_type(add_broll_or_motion|color_match_or_bridge|caption_avoid_subject|trim_dead_air|add_j_cut|review), suggestion}。\n"
        "静音工单给出 end_s，字幕遮挡工单尽量给出主体的归一化 subject_box [左,上,右,下]。使用片中绝对秒数和原镜号。仅审看提供的时间区间，检查静停、声画脱节、色调割裂、字幕遮挡和剪点。\n"
        + json.dumps(payload, ensure_ascii=False)
    )


def _deepseek_endpoint(base_url: str) -> str:
    b = (base_url or "https://api.deepseek.com").rstrip("/")
    if b.endswith("/chat/completions"):
        return b
    if b.endswith("/v1") or "api.deepseek.com" in b:
        return b + "/chat/completions"
    return b + "/v1/chat/completions"


def _run_deepseek_critique(strip_paths: list[Path], rubric: dict, *, api_key: str | None = None,
                           base_url: str | None = None, model: str | None = None,
                           force_vision: bool = False) -> dict:
    """Run DeepSeek critique via OpenAI-compatible API (supports vision VL models & text-telemetry fallback)."""
    import requests
    key = api_key or os.getenv("DEEPSEEK_API_KEY")
    if not key:
        return {"backend": "deepseek", "configured": False, "error": "DEEPSEEK_API_KEY not set"}
    chosen_base = base_url or os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    chosen_model = model or os.getenv("DEEPSEEK_MODEL") or DEFAULT_DEEPSEEK_MODEL
    url = _deepseek_endpoint(chosen_base)
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    prompt_text = _build_critique_prompt(rubric, include_visual_telemetry=True)
    use_vision = bool(strip_paths) and (
        force_vision or chosen_model.lower() == DEFAULT_DEEPSEEK_MODEL
        or any(tok in chosen_model.lower() for tok in ("vl", "vision"))
    )

    def _call(with_images: bool):
        if with_images and strip_paths:
            content: list | str = [{"type": "text", "text": prompt_text}]
            for p in strip_paths:
                b64 = base64.b64encode(p.read_bytes()).decode("ascii")
                content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
        else:
            content = prompt_text
        body = {
            "model": chosen_model,
            "messages": [
                {"role": "system", "content": "You are a sharp documentary/tech video editor. Always return valid JSON {\"tickets\": [...]}."},
                {"role": "user", "content": content},
            ],
            "temperature": 0.2,
        }
        if "reasoner" not in chosen_model.lower():
            body["response_format"] = {"type": "json_object"}
        return requests.post(url, headers=headers, json=body, timeout=60)

    fallback_note = None
    resp = _call(use_vision)
    if use_vision and resp.status_code == 400:
        fallback_note = "Image request returned HTTP 400. Retried with text and locally extracted visual telemetry."
        use_vision = False
        resp = _call(False)
    if resp.status_code != 200:
        return {"backend": "deepseek", "model": chosen_model, "endpoint": url,
                "error": f"DeepSeek HTTP {resp.status_code}: {resp.text[:260]}",
                **({"note": fallback_note} if fallback_note else {})}
    data = resp.json()
    msg = (data.get("choices") or [{}])[0].get("message") or {}
    raw_text = msg.get("content", "")
    tickets = _extract_json_tickets(raw_text)
    out = dict(
        backend="deepseek", model=chosen_model, endpoint=url,
        vision_images_sent=use_vision, ticket_count=len(tickets),
        tickets=tickets, raw_critique=raw_text,
    )
    if fallback_note:
        out["note"] = fallback_note
    return out


def _run_local_vlm_critique(strip_paths: list[Path], rubric: dict, *, local_url: str | None = None,
                            local_model: str | None = None) -> dict:
    """Run local offline multimodal VLM critique (Ollama / LM Studio / MLX-VLM / built-in offline vision)."""
    import requests
    server = _probe_local_vlm(local_url, local_model)
    if server is not None:
        prompt_text = _build_critique_prompt(rubric, include_visual_telemetry=True)
        content: list = [{"type": "text", "text": prompt_text}]
        for p in strip_paths:
            b64 = base64.b64encode(p.read_bytes()).decode("ascii")
            content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
        body = {
            "model": server["model"],
            "messages": [{"role": "user", "content": content}],
            "temperature": 0.2,
        }
        try:
            resp = requests.post(server["url"], json=body, timeout=90)
            if resp.status_code == 200:
                raw_text = ((resp.json().get("choices") or [{}])[0].get("message") or {}).get("content", "")
                tickets = _extract_json_tickets(raw_text)
                return dict(
                    backend="local-vlm-http", endpoint=server["url"], model=server["model"],
                    vision_images_sent=bool(strip_paths), ticket_count=len(tickets), tickets=tickets, raw_critique=raw_text,
                )
            return dict(backend="local-vlm-http", endpoint=server["url"], model=server["model"],
                        error=f"Local VLM HTTP {resp.status_code}: {resp.text[:240]}")
        except requests.RequestException as exc:
            return dict(backend="local-vlm-http", endpoint=server["url"], model=server["model"],
                        error=f"Local VLM request failed ({type(exc).__name__})")

    # Optional Apple Silicon mlx_vlm CLI if installed
    mlx_cli = shutil.which("mlx_vlm.generate")
    mlx_model = local_model or os.getenv("LOCAL_VLM_MODEL")
    if mlx_cli and mlx_model and strip_paths:
        prompt_text = _build_critique_prompt(rubric, include_visual_telemetry=False)
        try:
            out_txt = subprocess.check_output(
                [mlx_cli, "--model", mlx_model, "--image", str(strip_paths[0]), "--prompt", prompt_text],
                text=True, timeout=120, env=dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1"),
            )
            tickets = _extract_json_tickets(out_txt)
            return dict(backend="mlx-vlm", model=mlx_model, vision_images_sent=True,
                        ticket_count=len(tickets), tickets=tickets, raw_critique=out_txt)
        except Exception as exc:
            return dict(backend="mlx-vlm", model=mlx_model, error=str(exc))

    return dict(backend="local-vlm", error="No compatible local VLM server or configured MLX-VLM model was found")


def _optional_gemini_critique(strip_paths: list[Path], rubric: dict) -> dict | None:
    key = os.getenv("GEMINI_API_KEY")
    if not key:
        return None
    import requests
    parts = [{"text": _build_critique_prompt(rubric, include_visual_telemetry=False)}]
    for p in strip_paths:
        parts.append({
            "inline_data": {
                "mime_type": "image/jpeg",
                "data": base64.b64encode(p.read_bytes()).decode("ascii"),
            }
        })
    model = os.environ.get("GEMINI_CRITIQUE_MODEL") or "gemini-flash-latest"
    resp = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
        headers={"x-goog-api-key": key},
        json={"contents": [{"parts": parts}]},
        timeout=60,
    )
    if resp.status_code != 200:
        return {"backend": "gemini", "error": f"Gemini HTTP {resp.status_code}: {resp.text[:240]}"}
    data = resp.json()
    text = "".join(part.get("text", "") for part in data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
                   if not part.get("thought"))
    tickets = _extract_json_tickets(text)
    return {"backend": "gemini", "model": model, "vision_images_sent": bool(strip_paths),
            "ticket_count": len(tickets), "tickets": tickets, "raw_critique": text}


def provider_status() -> dict:
    local_srv = _probe_local_vlm()
    return dict(
        builtin_offline_vision={"configured": True, "note": "免 Key 零依赖离线视觉引擎（Apple Vision 主体遮挡 + 运动/色差/曝光/静音）"},
        deepseek={
            "configured": bool(os.getenv("DEEPSEEK_API_KEY")),
            "env": ["DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL"],
            "default_base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            "default_model": os.getenv("DEEPSEEK_MODEL") or DEFAULT_DEEPSEEK_MODEL,
            "url": "https://platform.deepseek.com/api_keys",
            "note": "默认 deepseek-flash 直传故事板。纯文本模型使用本地视觉遥测，也可配置兼容网关。",
        },
        local_vlm={
            "configured": bool(local_srv or shutil.which("mlx_vlm.generate")),
            "detected_server": local_srv,
            "mlx_vlm_cli": bool(shutil.which("mlx_vlm.generate")),
            "env": ["LOCAL_VLM_URL", "LOCAL_VLM_MODEL"],
            "note": "支持 Ollama (11434) / LM Studio (1234) / MLX-VLM 本地离线视觉大模型",
        },
        gemini={
            "configured": bool(os.getenv("GEMINI_API_KEY")),
            "env": ["GEMINI_API_KEY", "GEMINI_CRITIQUE_MODEL"],
            "default_model": os.environ.get("GEMINI_CRITIQUE_MODEL") or "gemini-flash-latest",
            "url": "https://aistudio.google.com/apikey",
        },
    )


def _review_in_batches(backend, segments, paths, rubric, batch_size, review_part):
    from requests import RequestException

    tickets, coverage, notes = [], [], []
    for start in range(0, len(segments), batch_size):
        group = segments[start:start + batch_size]
        part = dict(rubric, segments=group)
        try:
            result = review_part(paths[start:start + batch_size], part)
        except RequestException as exc:
            result = dict(backend=backend, error=f"Provider request failed ({type(exc).__name__})")
        except (ValueError, TypeError, AttributeError, IndexError):
            result = dict(backend=backend, error="Provider returned an invalid critique response")
        if result.get("error"):
            return dict(result, completed_batches=coverage, failed_start_s=group[0]["start_s"])
        coverage.append(dict(start_s=group[0]["start_s"], end_s=group[-1]["end_s"],
                             images_sent=bool(result.get("vision_images_sent"))))
        for ticket in result.get("tickets", []):
            if not (group[0]["start_s"] <= ticket["time_s"] < group[-1]["end_s"]):
                return dict(backend=backend, error="Provider ticket fell outside its review interval",
                            completed_batches=coverage[:-1], failed_start_s=group[0]["start_s"])
            tickets.append(ticket)
        if result.get("note") and result["note"] not in notes:
            notes.append(result["note"])
    return dict(result, tickets=tickets, ticket_count=len(tickets), batches=coverage,
                vision_images_sent=any(row["images_sent"] for row in coverage),
                **({"note": " ".join(notes)} if notes else {}))


def _subject_telemetry(paths, fps, subs, regions, shot_at_second):
    """Evaluate sampled subject boxes against the caption compositor's frame-bound geometry."""
    import auto_focus
    from picture_reservations import overlaps

    samples, tickets, reported = [], [], set()
    fallback = None
    region_idx = 0
    for start in range(0, len(paths), 24):
        frames = [cv2.imread(str(path)) for path in paths[start:start + 24]]
        detections = auto_focus._run_apple_vision_rows(frames)
        if detections is not None and len(detections) == len(frames):
            method = "apple-vision"
        else:
            if fallback is None:
                fallback = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
            method = "unavailable" if fallback.empty() else "opencv-face"
            detections = []
            for frame in frames:
                h, w = frame.shape[:2]
                faces = [] if fallback.empty() else fallback.detectMultiScale(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY),
                                                                              scaleFactor=1.12, minNeighbors=5)
                detections.append({"faces": [[x/w, y/h, (x+fw)/w, (y+fh)/h] for x,y,fw,fh in faces]})
        for offset, row in enumerate(detections):
            sec = start + offset
            frame = round(sec * fps)
            box = None
            if regions is None:
                if any(lo <= sec < hi for lo, hi, _ in subs):
                    box = [.06, .84, .94, .975]
            else:
                while region_idx < len(regions) and regions[region_idx]["end_frame"] <= frame:
                    region_idx += 1
                if region_idx < len(regions) and regions[region_idx]["start_frame"] <= frame:
                    box = regions[region_idx]["box"]
            subjects = []
            for label in ("faces", "objects", "salient"):
                for rect in row.get(label) or []:
                    if len(rect) == 4 and (label == "faces" or 0.02 <= (rect[2]-rect[0])*(rect[3]-rect[1]) <= .52):
                        subjects.append({"kind": label, "box": [round(float(v), 5) for v in rect]})
            hit = next((item for item in subjects if box and overlaps(box, item["box"])), None)
            measured = regions is not None
            samples.append(dict(detector=method, subjects=subjects, caption_box=box, overlap=bool(hit),
                                caption_layout="rendered" if measured else "estimated-bottom"))
            sid = shot_at_second(sec)
            if hit and sid not in reported:
                reported.add(sid)
                tickets.append(dict(time_s=sec, shot=sid, issue="subtitle_subject_overlap_risk", severity="medium",
                                    fix_type="caption_avoid_subject" if measured else "review", source="offline-vision",
                                    subject_box=hit["box"], caption_box=box,
                                    detail=f"第 {sec}s 的主体检测框与" + ("已渲染字幕相交。" if measured else "估计的底部字幕区相交，需看原帧确认。")))
    return samples, tickets


def build_packet(project: Path, media: Path, out_dir: Path, *, seg_seconds: int = 20,
                 extract_hotspot_clips: bool = True, run_gemini: bool = False,
                 backend: str = "offline", deepseek_api_key: str | None = None,
                 deepseek_base_url: str | None = None, deepseek_model: str | None = None,
                 deepseek_vision: bool = False, local_vlm_url: str | None = None,
                 local_vlm_model: str | None = None, timeline_path: Path | None = None,
                 captions_path: Path | None = None) -> dict:
    if seg_seconds <= 0:
        raise ValueError("--seg-seconds must be positive")
    project = project.resolve()
    media = media.resolve()
    out_dir = out_dir.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    tl_path = timeline_path or next((p for p in (media.parent / "timeline.json", project / "timeline.json") if p.is_file()), None)
    timeline_bytes = tl_path.read_bytes() if tl_path else b""
    tl = json.loads(timeline_bytes) if timeline_bytes else {"fps": 30, "frames": 0, "shots": []}
    fps = tl.get("fps", 30)
    srt_path = captions_path or next((p for p in (media.parent / "captions.srt", project / "audio/captions.srt", project / "out/captions.srt") if p.is_file()), None)
    subs = _parse_srt(srt_path) if srt_path else []

    frames_dir = out_dir / "_frames_1fps"
    frame_paths = _extract_1fps_frames(media, frames_dir)
    n_secs = len(frame_paths)
    if not n_secs:
        raise ValueError(f"No reviewable picture frames in {media}")
    from targeted_edit import probe
    streams = probe(media, "-show_entries", "stream=codec_type,duration:format=duration")
    video = next(row for row in streams["streams"] if row["codec_type"] == "video")
    duration = float(video.get("duration") or streams["format"]["duration"])
    has_audio = any(row["codec_type"] == "audio" for row in streams["streams"])
    audio_dbs = _audio_rms_per_second(media, n_secs) if has_audio else [-80.0] * n_secs

    # Measure per-second motion delta, CIE Lab stats & exposure extremes
    motion_scores = []
    lab_rows = []
    prev_gray = None
    for p in frame_paths:
        bgr = cv2.imread(str(p))
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
        lab_rows.append(_lab_stats(bgr))
        if prev_gray is None:
            motion_scores.append(0.0)
        else:
            motion_scores.append(round(float(np.mean(np.abs(gray - prev_gray))), 2))
        prev_gray = gray

    def shot_at_second(sec: float) -> str:
        f = math.floor(sec * fps + 1e-9)
        for s in tl.get("shots", []):
            if s["start_frame"] <= f < s["start_frame"] + s["frames"]:
                return s["id"]
        return "unknown"

    # Built-in offline vision hotspots: boredom, color clash, exposure extremes, dead-air, subject-vs-caption occlusion
    hotspots = []
    quiet_run = 0
    for s in range(n_secs):
        if motion_scores[s] < 1.2:
            quiet_run += 1
        else:
            if quiet_run >= 4:
                hotspots.append(dict(
                    time_s=s - quiet_run, end_s=s, shot=shot_at_second(s - quiet_run),
                    issue="continuous_low_motion_boredom_risk", severity="high" if quiet_run >= 7 else "medium",
                    fix_type="add_broll_or_motion", source="offline-vision",
                    detail=f"{s - quiet_run}s–{s}s 连续 {quiet_run} 秒画面极低运动（均值像素差 <1.2），易被观众判为 PPT 滑走",
                ))
            quiet_run = 0
    if quiet_run >= 4:
        hotspots.append(dict(
            time_s=n_secs - quiet_run, end_s=n_secs, shot=shot_at_second(n_secs - quiet_run),
            issue="continuous_low_motion_boredom_risk", severity="high" if quiet_run >= 7 else "medium",
            fix_type="add_broll_or_motion", source="offline-vision",
            detail=f"{n_secs - quiet_run}s–{n_secs}s 连续 {quiet_run} 秒画面极低运动，建议加动态底片或切 B-roll",
        ))

    # Cross-cut color clash detection (Delta-E > 32 right across a shot boundary)
    for t_shot in tl.get("shots", [])[1:]:
        cut_s = math.ceil(t_shot["start_frame"] / fps)
        if 1 <= cut_s < len(lab_rows) and shot_at_second(cut_s) == t_shot["id"]:
            a, b = lab_rows[cut_s - 1], lab_rows[cut_s]
            de = math.sqrt((a["L_mean"] - b["L_mean"]) ** 2 + (a["a_mean"] - b["a_mean"]) ** 2 + (a["b_mean"] - b["b_mean"]) ** 2)
            if de > 32.0:
                hotspots.append(dict(
                    time_s=cut_s, end_s=min(n_secs, cut_s + 2), shot=t_shot["id"],
                    issue="cross_cut_aesthetic_clash", severity="medium",
                    fix_type="color_match_or_bridge", source="offline-vision",
                    detail=f"第 {cut_s}s 切入 {t_shot['id']} 时明暗/色温跳变 ΔE={de:.1f}，建议运行 color_match.py 统一影调或加过渡桥",
                ))

    for issue, field in (("possible_underexposure", "crushed_black"), ("possible_overexposure", "clipped_white")):
        start = None
        for sec in range(n_secs + 1):
            clipped = sec < n_secs and lab_rows[sec][field] > 0.85
            if clipped and start is None:
                start = sec
            if not clipped and start is not None:
                if sec - start >= 2:
                    hotspots.append(dict(time_s=start, end_s=sec, shot=shot_at_second(start), issue=issue,
                                         severity="medium", fix_type="review", source="offline-vision",
                                         detail=f"{start}–{sec}s 超过 85% 像素在亮度极端，核对原素材和设计意图。"))
                start = None

    # Audio dead-air detection (>= 2 consecutive seconds below -44 dB)
    dead_run = 0
    for s in range(len(audio_dbs) + 1):
        if s < len(audio_dbs) and audio_dbs[s] < -44.0:
            dead_run += 1
        else:
            if dead_run >= 2:
                hotspots.append(dict(
                    time_s=s - dead_run, end_s=s, shot=shot_at_second(s - dead_run),
                    issue="audio_dead_air_gap", severity="medium",
                    fix_type="trim_dead_air" if has_audio else "review", source="offline-vision",
                    detail=f"{s - dead_run}s–{s}s 连续 {dead_run} 秒音频低于 -44 dB（疑似冷场静音），建议收紧 tail 或补环境底噪/音乐",
                ))
            dead_run = 0

    from review_bundle import receipt_for
    layout, binding = None, None
    bundle_path = receipt_for(media)
    if bundle_path.is_file():
        bundle = json.loads(bundle_path.read_text())
        release = bundle_path.name == media.name + ".review.json"
        if (release or bundle.get("media") == media.name) and bundle.get("sha256") == digest(media):
            if (srt_path and digest(srt_path) == bundle.get("sidecars", {}).get("captions.srt")
                    and hashlib.sha256(timeline_bytes).hexdigest() == bundle.get("timeline_sha256")):
                layout = bundle.get("rendered_caption_regions")
                location = bundle_path.relative_to(project) if bundle_path.is_relative_to(project) else bundle_path
                binding = dict(receipt=str(location), sha256=digest(bundle_path), media_sha256=bundle["sha256"])
            else:
                # Standalone reviews can deliberately use alternate captions or timelines.
                binding = {"unmatched_companions": True, "media_sha256": bundle["sha256"]}
    subjects, occlusions = _subject_telemetry(frame_paths, fps, subs, layout, shot_at_second)
    hotspots.extend(occlusions)
    for hotspot in hotspots:
        if "end_s" in hotspot:
            hotspot["end_s"] = min(duration, hotspot["end_s"])

    # Build 15–25s viewer segments with 1fps annotated storyboard strips & visual telemetry
    segments = []
    strip_paths = []
    for seg_idx, start_s in enumerate(range(0, n_secs, seg_seconds)):
        end_s = min(n_secs, start_s + seg_seconds)
        seg_frames = frame_paths[start_s:end_s]
        cols = min(5, len(seg_frames))
        rows = math.ceil(len(seg_frames) / cols)
        sheet = Image.new("RGB", (cols * 320, rows * 204), (19, 26, 23))
        draw = ImageDraw.Draw(sheet)
        for i, fp in enumerate(seg_frames):
            sec = start_s + i
            r, c = divmod(i, cols)
            with Image.open(fp) as im:
                sheet.paste(im, (c * 320, r * 204))
            mot = motion_scores[sec]
            db = audio_dbs[sec] if sec < len(audio_dbs) else -60.0
            col_txt = (237, 169, 97) if mot >= 1.2 else (235, 105, 95)
            draw.text((c * 320 + 6, r * 204 + 183), f"{sec:03d}s {shot_at_second(sec)[:10]} m:{mot:.1f} {db:.0f}dB", fill=col_txt)
        strip_p = out_dir / f"segment-{seg_idx + 1:02d}-{start_s:03d}s-{end_s:03d}s.jpg"
        sheet.save(strip_p, quality=90)
        strip_paths.append(strip_p)

        seg_text = " ".join(txt for a, b, txt in subs if b > start_s and a < end_s)
        seg_hotspots = [h for h in hotspots if start_s <= h["time_s"] < end_s]
        seg_labs = lab_rows[start_s:end_s]
        vis_telem = dict(
            samples=[dict(time_s=s, shot=shot_at_second(s), motion=motion_scores[s],
                          audio_db=round(audio_dbs[s], 2), subject_detection=subjects[s], **{k: round(v, 3) if isinstance(v, float) else v
                          for k, v in lab_rows[s].items()}) for s in range(start_s, end_s)],
            shots_in_segment=sorted(dict.fromkeys(shot_at_second(s) for s in range(start_s, end_s))),
            mean_luma_L=round(float(np.mean([x["L_mean"] for x in seg_labs])), 1) if seg_labs else 0.0,
            mean_contrast_std=round(float(np.mean([x["L_std"] for x in seg_labs])), 1) if seg_labs else 0.0,
            mean_warmth_b=round(float(np.mean([x["b_mean"] for x in seg_labs])), 1) if seg_labs else 0.0,
            max_clipped_white=round(max((x["clipped_white"] for x in seg_labs), default=0.0), 3),
            caption_occlusion_detected=any(row["overlap"] for row in subjects[start_s:end_s]),
        )
        clip_path = None
        if extract_hotspot_clips and seg_hotspots:
            clip_path = out_dir / f"review-clip-{seg_idx + 1:02d}.mp4"
            subprocess.run([
                "ffmpeg", "-nostdin", "-v", "error", "-y",
                "-ss", str(start_s), "-t", str(end_s - start_s), "-i", str(media),
                "-vf", "scale=960:540", "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
                "-c:a", "aac", "-b:a", "128k", str(clip_path),
            ], check=True)
        segments.append(dict(
            segment=seg_idx + 1, start_s=start_s, end_s=min(end_s, duration),
            storyboard=str(strip_p),
            review_clip=str(clip_path) if clip_path else None,
            mean_motion=round(float(np.mean(motion_scores[start_s:end_s])), 2),
            mean_audio_db=round(float(np.mean(audio_dbs[start_s:end_s])), 1),
            spoken_transcript=seg_text,
            subtitle_cues=[dict(start_s=a, end_s=b, text=t) for a,b,t in subs if b > start_s and a < end_s],
            visual_telemetry=vis_telem,
            hotspots=seg_hotspots,
        ))

    rubric: dict = dict(
        schema=2,
        media=str(media),
        duration_seconds=duration,
        audio_track_present=has_audio,
        requested_backend="gemini" if run_gemini and backend == "auto" else backend,
        summary=dict(
            segment_count=len(segments),
            hotspot_count=len(hotspots),
            mean_motion_all=round(float(np.mean(motion_scores)) if motion_scores else 0.0, 2),
            low_motion_seconds=sum(1 for m in motion_scores if m < 1.2),
        ),
        auto_hotspots=hotspots,
        segments=segments,
    )

    requested = rubric["requested_backend"]
    attempts = []
    backends = []
    if requested == "auto":
        if deepseek_api_key or os.getenv("DEEPSEEK_API_KEY"):
            backends.append("deepseek")
        if os.getenv("GEMINI_API_KEY"):
            backends.append("gemini")
        backends.append("local-vlm")
    elif requested != "offline":
        backends.append(requested)

    ai_result = dict(backend="builtin-offline-vision", vision_images_sent=False, ticket_count=len(hotspots))
    for candidate_backend in backends:
        def review_part(paths, part):
            if candidate_backend == "deepseek":
                return _run_deepseek_critique(
                    paths, part, api_key=deepseek_api_key, base_url=deepseek_base_url,
                    model=deepseek_model, force_vision=deepseek_vision,
                )
            if candidate_backend == "gemini":
                return _optional_gemini_critique(paths, part) or {"backend": "gemini", "error": "GEMINI_API_KEY not set"}
            return _run_local_vlm_critique(paths, part, local_url=local_vlm_url, local_model=local_vlm_model)

        # The MLX command accepts one board. HTTP backends can review four per request.
        batch_size = 1 if candidate_backend == "local-vlm" else 4
        result = _review_in_batches(candidate_backend, segments, strip_paths, rubric, batch_size, review_part)
        attempts.append({key: result[key] for key in ("backend", "model", "error", "note", "failed_start_s", "completed_batches") if key in result})
        if not result.get("error"):
            ai_result = result
            break

    rubric["effective_backend"] = ai_result["backend"]
    rubric["backend_attempts"] = attempts
    rubric["ai_critique"] = ai_result
    if ai_result["backend"] == "gemini":
        rubric["gemini_critique"] = ai_result
    merged_tickets = list(hotspots)
    for tk in ai_result.get("tickets", []):
        row = dict(tk, source=ai_result["backend"])
        bound_shot = shot_at_second(row["time_s"])
        if row["fix_type"] == "caption_avoid_subject" and "subject_box" not in row:
            from picture_reservations import overlaps
            sample = subjects[int(row["time_s"])]
            hit = next((obj for obj in sample["subjects"] if sample["caption_box"] and
                        overlaps(obj["box"], sample["caption_box"])), None)
            if hit and sample["caption_layout"] == "rendered":
                row["subject_box"] = hit["box"]
            else:
                row["fix_type"] = "review"
        if not has_audio and row["fix_type"] == "trim_dead_air":
            row["fix_type"] = "review"
        if bound_shot != "unknown":
            row["shot"] = bound_shot
        if not any(abs(row["time_s"] - m["time_s"]) < 1.0 and row["issue"] == m["issue"] for m in merged_tickets):
            merged_tickets.append(row)

    rubric["summary"]["merged_ticket_count"] = len(merged_tickets)
    timeline_hash = hashlib.sha256(timeline_bytes).hexdigest() if timeline_bytes else None
    for ticket in merged_tickets:
        ticket["reviewed_timeline_sha256"] = timeline_hash
        if binding:
            ticket["reviewed_candidate"] = binding
        if ticket.get("shot") in (None, "", "unknown") and timeline_hash:
            ticket["shot"] = shot_at_second(ticket["time_s"])
    atomic_json(out_dir / "critique-rubric.json", rubric)
    atomic_json(out_dir / "tickets.json", merged_tickets)
    return rubric


def _bridge_for_cut(project, edit, timeline, index):
    from transitions import plan_bridges, source_room

    shots, clock, fps = list(edit["shots"]), timeline["shots"], timeline["fps"]
    candidate = {"name": "blur-dissolve", "seconds": .37}
    shots[index] = dict(shots[index], transition=candidate)
    def source(path):
        return project / path
    try:
        edge = plan_bridges(shots, clock, fps, default_handles=edit.get("transition_handles", "hold"),
                            source_path=source)[index]
        if edge and edge.handles == "source":
            durations = {}
            before, _ = source_room(shots[index], clock[index]["frames"], fps, source, durations)
            _, after = source_room(shots[index-1], clock[index-1]["frames"], fps, source, durations)
            if edge.left > before or edge.right > after:
                return None
        return candidate if edge else None
    except (ValueError, OSError, subprocess.CalledProcessError):
        return None


def _ticket_in_tail(ticket, timing, timeline, configured_tail):
    fps = timeline["fps"]
    end = (timing.get("audio_start_frame", timing["start_frame"]) + timing.get("audio_frames", timing["frames"])) / fps
    start = end - configured_tail
    for take in timeline.get("voice_takes", []):
        if take.get("shot") == timing["id"] and take.get("sample_rate") and "end_sample" in take:
            start = max(start, take["end_sample"] / take["sample_rate"])
    position = float(ticket["time_s"])
    if "end_s" in ticket:
        return position < end and start < float(ticket["end_s"])
    return start <= position < end


def _verify_ticket_candidate(project, binding):
    if binding.get("unmatched_companions"):
        raise ValueError("Review this candidate with its own timeline and captions before applying tickets.")
    receipt = project / binding["receipt"]
    if not receipt.is_file() or digest(receipt) != binding["sha256"]:
        raise ValueError("The reviewed candidate receipt changed or is missing.")
    manifest = json.loads(receipt.read_text())
    if binding["media_sha256"] != manifest.get("sha256"):
        raise ValueError("The ticket belongs to a different candidate.")
    for name, checksum in manifest.get("render_inputs", {}).items():
        if name in ("cover.json", "library-uses.json", "qa/learning.json"):
            continue
        source = project / name
        if not source.is_file() or digest(source) != checksum:
            raise ValueError(f"Reviewed render input changed: {name}. Review the new cut before applying tickets.")


def build_patch_plan(project: Path, tickets_path: Path, *, apply: bool = False) -> dict:
    """Translate timestamped viewer critique tickets into concrete edit.json shot patches (optionally applying safe ones)."""
    project = project.resolve()
    edit_path = project / "edit.json"
    edit_bytes = edit_path.read_bytes()
    edit = json.loads(edit_bytes)
    edit_hash = hashlib.sha256(edit_bytes).hexdigest()
    tl_path = project / "timeline.json"
    timeline_bytes = tl_path.read_bytes()
    tl = json.loads(timeline_bytes)
    timeline_hash = hashlib.sha256(timeline_bytes).hexdigest()
    tickets = json.loads(tickets_path.read_text(encoding="utf-8"))
    fps = tl["fps"]
    shots_by_id = {s["id"]: s for s in edit.get("shots", [])}
    shot_index = {s["id"]: i for i, s in enumerate(edit.get("shots", []))}
    timings = {s["id"]: s for s in tl["shots"]}

    patches = []
    applied_count = 0
    bindings = {}
    for tk in tickets:
        t_s = float(tk.get("time_s", 0.0))
        sid = tk.get("shot")
        if (not sid or (sid == "unknown" and "reviewed_timeline_sha256" not in tk)) and "time_s" in tk:
            f = math.floor(t_s * fps + 1e-9)
            sid = next((s["id"] for s in tl["shots"] if s["start_frame"] <= f < s["start_frame"] + s["frames"]), None)
        if not sid or sid not in shots_by_id:
            continue
        shot = shots_by_id[sid]
        fix = tk.get("fix_type", "review")
        applied = False
        if fix == "add_broll_or_motion":
            if "remotion_effect" in shot:
                action = {"field": "remotion_effect.background", "suggest": "绑定实拍 B-roll 视频或 broll_router.py 生成动态底片"}
            else:
                action = {"field": "camera_drift", "suggest": "添加 camera_drift: 'push-in' 或拆出 4s 实拍特写镜"}
                if apply and not any(key in shot for key in ("camera", "camera_drift")):
                    shot["camera_drift"] = "push-in"
                    applied = True
                    applied_count += 1
        elif fix == "color_match_or_bridge":
            action = {"field": "grade / transition", "suggest": "运行 color_match.py 对齐色温明暗，或改用 blur-dissolve / match-cut"}
            transition = shot.get("transition", "cut")
            if isinstance(transition, dict):
                transition = transition.get("name")
            if apply and shot_index[sid] > 0 and transition == "cut":
                bridge = _bridge_for_cut(project, edit, tl, shot_index[sid])
                if bridge:
                    shot["transition"] = bridge
                    applied = True
                    applied_count += 1
                else:
                    action["reason"] = "当前画面长度或相邻转场没有足够余量，需人工调色或调整剪点。"
        elif fix == "caption_avoid_subject":
            action = {"field": "caption_avoid", "suggest": "按主体位置避让字幕，检查本镜可用字幕区。"}
            if apply and shot.get("caption_position") is None and not shot.get("caption_avoid"):
                from picture_reservations import normalized_box
                box = normalized_box(tk.get("subject_box", [0.18, 0.72, 0.82, 0.96]), "ticket subject_box")
                shot["caption_avoid"] = [{"label": "审片主体避让", "box": box}]
                srt = next((p for p in (project/"audio/captions.srt", project/"out/captions.srt") if p.is_file()), None)
                try:
                    if srt:
                        from floating_captions import top_caption_ranges
                        top_caption_ranges(edit, tl, 0, tl["frames"], srt=srt, size=edit.get("size", [1920, 1080]))
                except ValueError as exc:
                    shot.pop("caption_avoid")
                    action["reason"] = str(exc)
                else:
                    applied = True
                    applied_count += 1
        elif fix == "trim_dead_air":
            action = {"field": "tail", "suggest": "收紧过长尾静音至 0.22s，消除冷场空拍"}
            tail = float(shot.get("tail", 0.18))
            if (apply and not edit.get("picture_edit") and tail > .38 and "time_s" in tk and sid in timings
                    and _ticket_in_tail(tk, timings[sid], tl, tail)):
                shot["tail"] = 0.22
                applied = True
                applied_count += 1
        elif fix == "add_j_cut":
            action = {"field": "j_cut", "suggest": "添加 0.25s J-cut 音频先行，消除硬切机械感"}
            if (apply and not edit.get("picture_edit") and shot_index[sid] > 0
                    and not any(k in shot for k in ("j_cut", "l_cut", "cut_offset"))):
                shot["j_cut"] = 0.25
                applied = True
                applied_count += 1
        else:
            action = {"field": "shot", "suggest": tk.get("detail") or tk.get("suggestion") or "按审片意见局部返修"}
        if applied and tk.get("reviewed_candidate"):
            binding = tk["reviewed_candidate"]
            bindings[json.dumps(binding, sort_keys=True)] = binding
        if applied and "reviewed_timeline_sha256" in tk:
            if (tk["reviewed_timeline_sha256"] != timeline_hash
                    or tl.get("edit_sha256") not in (None, edit_hash)):
                raise ValueError("The critique ticket belongs to another cut. Review the current edit before applying it.")
        patches.append(dict(
            shot=sid, time_s=t_s, severity=tk.get("severity", "medium"),
            issue=tk.get("issue"), source=tk.get("source", "critique"), action=action, applied=applied,
        ))
    if apply and applied_count > 0:
        for binding in bindings.values():
            _verify_ticket_candidate(project, binding)
        if digest(edit_path) != edit_hash or digest(tl_path) != timeline_hash:
            raise ValueError("The edit or timeline changed while applying critique tickets.")
        atomic_json(edit_path, edit)
    out = dict(project=str(project), patch_count=len(patches), applied_count=applied_count, patches=patches)
    out_path = tickets_path.parent / "patch-plan.json"
    atomic_json(out_path, out)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("providers", help="Show configured status for built-in offline vision, DeepSeek, local VLM & Gemini")

    bd = sub.add_parser("build", help="Build 1x viewer multimodal critique packet from exported MP4")
    bd.add_argument("--project", type=Path, default=Path("."))
    bd.add_argument("--media", type=Path, required=True)
    bd.add_argument("--out", type=Path, default=Path("qa/critique-packet"))
    bd.add_argument("--seg-seconds", type=int, default=20)
    bd.add_argument("--no-clips", action="store_true")
    bd.add_argument("--backend", choices=["auto", "deepseek", "local-vlm", "gemini", "offline"], default="auto",
                    help="Critique backend: auto (DeepSeek->Gemini->Local VLM->Offline), deepseek, local-vlm, gemini, or offline")
    bd.add_argument("--deepseek-api-key", help="DeepSeek API key (or set DEEPSEEK_API_KEY env var)")
    bd.add_argument("--deepseek-base-url", help="DeepSeek OpenAI-compatible base URL (default https://api.deepseek.com)")
    bd.add_argument("--deepseek-model", help=f"DeepSeek model (default {DEFAULT_DEEPSEEK_MODEL}; also set via DEEPSEEK_MODEL)")
    bd.add_argument("--deepseek-vision", action="store_true", help="Send base64 storyboard images to DeepSeek-compatible vision gateway")
    bd.add_argument("--local-vlm-url", help="Local OpenAI-compatible VLM server URL (e.g. http://127.0.0.1:11434/v1)")
    bd.add_argument("--local-vlm-model", help="Local VLM model name (e.g. qwen2.5vl)")
    bd.add_argument("--run-gemini", action="store_true", help="Select Gemini in auto mode using GEMINI_API_KEY")

    pt = sub.add_parser("patch", help="Convert critique tickets into concrete edit.json shot patch proposals")
    pt.add_argument("--project", type=Path, default=Path("."))
    pt.add_argument("--tickets", type=Path, required=True)
    pt.add_argument("--apply", action="store_true", help="Automatically apply safe deterministic patches into edit.json")
    args = ap.parse_args()

    if args.cmd == "providers":
        print(json.dumps(provider_status(), ensure_ascii=False, indent=2))
    elif args.cmd == "build":
        res = build_packet(
            args.project, args.media, args.out,
            seg_seconds=args.seg_seconds, extract_hotspot_clips=not args.no_clips,
            run_gemini=args.run_gemini, backend=args.backend,
            deepseek_api_key=args.deepseek_api_key, deepseek_base_url=args.deepseek_base_url,
            deepseek_model=args.deepseek_model, deepseek_vision=args.deepseek_vision,
            local_vlm_url=args.local_vlm_url, local_vlm_model=args.local_vlm_model,
        )
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(build_patch_plan(args.project, args.tickets, apply=args.apply), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
