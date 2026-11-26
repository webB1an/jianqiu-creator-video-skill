"""Three-route hybrid B-roll Media Router:
  Route A (real-footage): Personal semantic library + Pixabay/Pexels/NASA/Archive/Commons HD video
  Route B (ai-video-broll): Style-locked AI generative video (Veo / Runway / Kling / Seedance) + local procedural fallback
  Route C (remotion-data-over-video): Precision charts, typography & timelines composited over Route A/B video backgrounds
"""
import argparse
import base64
import fcntl
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

import numpy as np
import requests
from PIL import Image, ImageOps

import media
from production_run import atomic_json, digest
import semantic_broll

OPENING_DISSOLVE_SECONDS = .75

JIANQIU_STYLE_BIBLE = (
    "editorial documentary cinematography, moody deep emerald-ink shadows (#131A17), "
    "subtle warm amber rim lighting (#EDA961) and cool cyan highlights (#8AD7CD), "
    "shallow depth of field, slow steady 35mm cinema push-in, subtle organic film grain, "
    "clean dark negative space for foreground typography, strictly NO text, NO letters, NO watermarks, NO logos"
)

ABSTRACT_HINTS = (
    '抽象', '隐喻', '概念', '拓扑', '高维', '复平面', '无限', '宇宙', '历史重演', '未来', '信息流',
    '量级', '秩序', '鸿沟', '思维', '神经网络', '光脉', '量子', '时空', '演进', '微观',
    'abstract', 'metaphor', 'historical', 'quantum', 'lattice', 'manifold', 'dimension', 'neural', 'cosmos', 'evolution',
)
DATA_HINTS = (
    '%', '％', 'gb', 'tb', 'mb', 'ms', 'fps', '百分点', '时间线', '对比', '差异', '增速', '下降', '占比',
    '步骤', '阶段', '指标', '营收', '成本', '耗时', '清单', '声明', '总结', '排名', '基准',
)


def available_ai_providers():
    """Inspect configured generative video B-roll API credentials."""
    providers = []
    if os.getenv('GEMINI_API_KEY'):
        providers.append('veo')
    if os.getenv('RUNWAYML_API_SECRET'):
        providers.append('runway')
    if os.getenv('KLING_ACCESS_KEY') and os.getenv('KLING_SECRET_KEY'):
        providers.append('kling')
    if os.getenv('ARK_API_KEY'):
        providers.append('seedance')
    providers.append('procedural')
    return providers


def classify_shot(shot):
    """Route a shot to Route A (real-footage), Route B (ai-video-broll), or Route C (remotion-data-over-video)."""
    text = ' '.join(l.get('text', '') for l in shot.get('lines', []))
    low = text.lower()
    has_remotion = bool(shot.get('remotion_effect'))
    has_data = has_remotion or bool(shot.get('value_comparison')) or any(k in low for k in DATA_HINTS)
    has_abstract = any(k in low for k in ABSTRACT_HINTS)
    if has_data:
        bg_route = 'ai-video-broll' if has_abstract else 'real-footage'
        return dict(route='remotion-data-over-video', backdrop_route=bg_route,
                    reason='含精确数值/结构化清单/引语，文字与图表走 Remotion 透明前景，底片走动态视频背景')
    if has_abstract:
        return dict(route='ai-video-broll', backdrop_route='ai-video-broll',
                    reason='含抽象隐喻或不可直拍的历史/数学意象，适合风格锁定 AI 视频 B-roll 或程序化光影底片')
    return dict(route='real-footage', backdrop_route='real-footage',
                reason='具象现实主体与动作，优先从个人语义库与高清实拍库（Pixabay/NASA/Archive/Commons）取真实视频')


def _load_style_bible(project_dir):
    if not project_dir:
        return None
    p = Path(project_dir) / 'style-bible.json'
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return None
    return None


def build_style_prompt(subject, *, mood='dark', camera='slow push-in', style_ref=None, style_bible=None):
    suffix = style_bible['prompt_suffix'] if (style_bible and style_bible.get('prompt_suffix')) else JIANQIU_STYLE_BIBLE
    neg = (style_bible.get('negative_prompt') if style_bible else None) or 'text, watermark, logo, subtitles, blurry distortion, jitter, slideshow, static frame'
    prompt = f"{subject.strip()}, {camera}, {mood} editorial atmosphere, {suffix}"
    spec = dict(
        prompt=prompt,
        aspect_ratio='16:9',
        duration_seconds=5,
        fps=24,
        negative_prompt=neg,
    )
    if style_bible and style_bible.get('preset'):
        spec['style_bible'] = style_bible['preset']
    if style_ref:
        spec['style_reference_image'] = str(style_ref)
    return spec


def render_procedural_broll(preset, output, *, seconds=6.0, fps=30, width=1920, height=1080, seed=42,
                            first_frame=None):
    """Offline deterministic 1080p cinema-grade atmospheric B-roll backdrop generator (zero API key fallback)."""
    valid = ('code-stream', 'bokeh-amber', 'topo-grid', 'paper-light')
    if preset not in valid:
        raise ValueError(f"preset must be one of {', '.join(valid)}")
    frames = max(2, int(round(seconds * fps)))
    out = Path(output).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)

    # Render smoothly at 960x540 then bicubic upscale in FFmpeg with organic grain for crisp fast 1080p BT.709.
    rw, rh = 960, 540
    opening = None
    if first_frame:
        with Image.open(first_frame) as image:
            upright = ImageOps.exif_transpose(image).convert('RGB')
            opening = np.asarray(ImageOps.fit(upright, (rw, rh)), dtype=np.float32)
    yy, xx = np.mgrid[0:rh, 0:rw].astype(np.float32)
    xn = xx / rw
    yn = yy / rh

    if preset == 'bokeh-amber':
        orbs = [(rng.uniform(0.05, 0.95), rng.uniform(0.08, 0.92), rng.uniform(0.05, 0.16),
                 rng.uniform(-0.08, 0.08), rng.uniform(-0.05, 0.05), rng.choice([0, 1])) for _ in range(14)]
    elif preset == 'code-stream':
        cols = [(rng.uniform(0.04, 0.96), rng.uniform(0.0, 1.0), rng.uniform(0.18, 0.55),
                 rng.uniform(0.008, 0.018), rng.choice([0, 1])) for _ in range(36)]
    elif preset == 'paper-light':
        motes = [(rng.uniform(0.05, 0.95), rng.uniform(0.05, 0.95), rng.uniform(0.006, 0.02),
                  rng.uniform(0.03, 0.09), rng.uniform(-0.04, 0.04)) for _ in range(28)]

    # Subtle tactile multi-octave paper/film surface texture reduces synthetic look across all presets.
    tex_rng = np.random.default_rng(seed + 101)
    coarse = tex_rng.normal(0.0, 1.0, (rh // 6, rw // 6)).astype(np.float32)
    coarse = np.repeat(np.repeat(coarse, 6, axis=0), 6, axis=1)[:rh, :rw]
    fine = tex_rng.normal(0.0, 1.0, (rh, rw)).astype(np.float32)
    surface_tex = (coarse * 2.2 + fine * 1.1)[:, :, None]

    with tempfile.TemporaryDirectory(dir=out.parent) as tmp:
        stage = Path(tmp) / 'clip.mp4'
        vf = f'scale={width}:{height}:flags=lanczos:out_color_matrix=bt709:out_range=tv,format=yuv420p'
        if opening is None:
            vf += ',noise=alls=3:allf=t+u'
        cmd = [
            'ffmpeg', '-y', '-nostdin', '-v', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
            '-s', f'{rw}x{rh}', '-r', str(fps), '-i', '-', '-vf', vf,
            '-an', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '18', '-preset', 'fast',
            '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709', str(stage),
        ]
        with subprocess.Popen(cmd, stdin=subprocess.PIPE) as enc:
            for n in range(frames):
                t = n / max(1, frames - 1)
                phase = 2.0 * math.pi * (n / frames)
                if preset == 'bokeh-amber':
                    img = np.zeros((rh, rw, 3), dtype=np.float32)
                    img[:, :, 0] = 15 + 8 * (1 - yn)
                    img[:, :, 1] = 24 + 12 * (1 - yn)
                    img[:, :, 2] = 21 + 10 * (1 - yn)
                    for x0, y0, rad, vx, vy, warm in orbs:
                        cx = (x0 + vx * math.sin(phase) + 0.03 * t) % 1.0
                        cy = (y0 + vy * math.cos(phase)) % 1.0
                        d2 = ((xn - cx) * 1.77) ** 2 + (yn - cy) ** 2
                        glow = np.exp(-d2 / (2 * rad * rad))
                        col = (237, 169, 97) if warm else (120, 210, 198)
                        for c in range(3):
                            img[:, :, c] += glow * col[c] * 0.28
                elif preset == 'code-stream':
                    img = np.zeros((rh, rw, 3), dtype=np.float32)
                    img[:, :, 0] = 10 + 6 * yn
                    img[:, :, 1] = 17 + 10 * yn
                    img[:, :, 2] = 16 + 12 * yn
                    for x0, y0, spd, w_col, accent in cols:
                        head = (y0 + spd * t) % 1.25 - 0.15
                        dx = np.abs(xn - x0)
                        col_mask = np.exp(-(dx / w_col) ** 2)
                        trail = np.clip(1.0 - (head - yn) / 0.32, 0.0, 1.0) * (yn <= head)
                        # Segmented pulse blocks simulating flowing data/code tokens.
                        pulse = 0.55 + 0.45 * np.sin((yn * 48.0 + n * 0.18))
                        val = col_mask * trail * pulse
                        col = (237, 169, 97) if accent else (138, 215, 205)
                        for c in range(3):
                            img[:, :, c] += val * col[c] * 0.70
                elif preset == 'topo-grid':
                    img = np.zeros((rh, rw, 3), dtype=np.float32)
                    img[:, :, 0] = 13
                    img[:, :, 1] = 20 + 10 * yn
                    img[:, :, 2] = 18 + 12 * yn
                    # Perspective 3D receding wireframe plane in lower 72% + gentle wave relief.
                    horizon = 0.24
                    depth = np.clip((yn - horizon) / (1.0 - horizon), 0.02, 1.0)
                    u_coord = (xn - 0.5) / depth
                    v_coord = 1.4 / depth + t * 2.8
                    wave = 0.16 * np.sin(u_coord * 2.2 + phase) * np.cos(v_coord * 1.4)
                    gx = np.abs(np.sin((u_coord + wave) * math.pi * 1.4))
                    gy = np.abs(np.sin(v_coord * math.pi * 1.2))
                    lines = np.maximum(np.exp(-((gx / 0.055) ** 2)), np.exp(-((gy / 0.055) ** 2)))
                    fade = np.clip((yn - horizon) * 3.5, 0.0, 1.0) * (0.3 + 0.7 * depth)
                    img[:, :, 0] += lines * fade * 115
                    img[:, :, 1] += lines * fade * 205
                    img[:, :, 2] += lines * fade * 190
                else:  # paper-light
                    img = np.zeros((rh, rw, 3), dtype=np.float32)
                    beam = np.exp(-((xn - (0.32 + 0.22 * t) + 0.35 * yn) ** 2) / 0.08)
                    img[:, :, 0] = 236 + 16 * beam
                    img[:, :, 1] = 228 + 14 * beam
                    img[:, :, 2] = 212 + 8 * beam
                    for x0, y0, rad, vx, vy in motes:
                        cx = (x0 + vx * t) % 1.0
                        cy = (y0 + vy * math.sin(phase)) % 1.0
                        d2 = ((xn - cx) * 1.77) ** 2 + (yn - cy) ** 2
                        mote = np.exp(-d2 / (2 * rad * rad))
                        img[:, :, 0] += mote * 18
                        img[:, :, 1] += mote * 14
                        img[:, :, 2] += mote * 4
                img += surface_tex
                if opening is not None:
                    u = min(1.0, n / max(1, min(frames - 1, round(OPENING_DISSOLVE_SECONDS * fps))))
                    mix = u * u * (3 - 2 * u)
                    img = opening * (1 - mix) + img * mix
                enc.stdin.write(np.clip(img, 0, 255).astype(np.uint8).tobytes())
            enc.stdin.close()
            if enc.wait():
                raise RuntimeError('Procedural B-roll encoding failed')
        os.replace(stage, out)
    return dict(file=str(out), provider='procedural', preset=preset, frames=frames, fps=fps, size=[width, height])


def _kling_jwt(ak, sk):
    header = base64.urlsafe_b64encode(json.dumps({'alg': 'HS256', 'typ': 'JWT'}).encode()).rstrip(b'=')
    now = int(time.time())
    payload = base64.urlsafe_b64encode(json.dumps({'iss': ak, 'exp': now + 1800, 'nbf': now - 5}).encode()).rstrip(b'=')
    sig = base64.urlsafe_b64encode(hmac.new(sk.encode(), header + b'.' + payload, hashlib.sha256).digest()).rstrip(b'=')
    return (header + b'.' + payload + b'.' + sig).decode()


def _download_verified_mp4(url, output, *, headers=None, params=None):
    """Download a generated video URL, verify decodable H.264/video stream with ffprobe, and atomically replace."""
    out = Path(output).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=out.parent, suffix='.mp4', delete=False) as tmp:
        stage = Path(tmp.name)
    try:
        with requests.get(url, headers=headers, params=params, stream=True, timeout=(20, 180)) as resp:
            resp.raise_for_status()
            with stage.open('wb') as wf:
                for chunk in resp.iter_content(1024 * 512):
                    if chunk:
                        wf.write(chunk)
        streams = subprocess.check_output(
            ['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height,codec_name',
             '-of', 'json', str(stage)], text=True)
        v_list = json.loads(streams).get('streams') or []
        if not v_list:
            raise RuntimeError(f'Downloaded AI B-roll contains no video stream: {url}')
        os.replace(stage, out)
        return dict(file=str(out), width=v_list[0].get('width'), height=v_list[0].get('height'),
                    codec=v_list[0].get('codec_name'))
    finally:
        stage.unlink(missing_ok=True)


def _poll_veo(operation_name, key, output, *, timeout_s=300, interval_s=6):
    deadline = time.time() + timeout_s
    poll_url = f'https://generativelanguage.googleapis.com/v1beta/{operation_name}'
    while time.time() < deadline:
        r = requests.get(poll_url, params={'key': key}, timeout=30)
        r.raise_for_status()
        data = r.json()
        if data.get('done'):
            if data.get('error'):
                raise RuntimeError(f"Veo generation failed: {data['error']}")
            resp = data.get('response') or {}
            samples = ((resp.get('generateVideoResponse') or {}).get('generatedSamples')
                       or resp.get('generatedSamples') or [])
            uri = None
            for s in samples:
                uri = (s.get('video') or {}).get('uri') or s.get('uri')
                if uri:
                    break
            if not uri:
                raise RuntimeError(f'Veo completed without video URI: {json.dumps(resp)[:240]}')
            return _download_verified_mp4(uri, output, params={'key': key} if 'key=' not in uri else None)
        time.sleep(interval_s)
    raise TimeoutError(f'Veo operation timed out after {timeout_s}s: {operation_name}')


def _poll_runway(task_id, headers, output, *, timeout_s=300, interval_s=5):
    deadline = time.time() + timeout_s
    url = f'https://api.dev.runwayml.com/v1/tasks/{task_id}'
    while time.time() < deadline:
        r = requests.get(url, headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json()
        st = (data.get('status') or '').upper()
        if st == 'SUCCEEDED':
            outs = data.get('output') or []
            video_url = outs[0] if isinstance(outs, list) and outs else data.get('outputUrl')
            if not video_url:
                raise RuntimeError(f'Runway task succeeded without output URL: {data}')
            return _download_verified_mp4(video_url, output)
        if st in ('FAILED', 'CANCELED', 'CANCELLED'):
            raise RuntimeError(f"Runway task {st}: {data.get('failure') or data}")
        time.sleep(interval_s)
    raise TimeoutError(f'Runway task timed out after {timeout_s}s: {task_id}')


def _poll_kling(task_id, ak, sk, output, *, task_type='text2video', timeout_s=360, interval_s=6):
    deadline = time.time() + timeout_s
    url = f'https://api.klingai.com/v1/videos/{task_type}/{task_id}'
    while time.time() < deadline:
        headers = {'Authorization': f'Bearer {_kling_jwt(ak, sk)}'}
        r = requests.get(url, headers=headers, timeout=30)
        r.raise_for_status()
        body = r.json().get('data') or {}
        st = (body.get('task_status') or '').lower()
        if st == 'succeed':
            videos = (body.get('task_result') or {}).get('videos') or []
            if not videos or not videos[0].get('url'):
                raise RuntimeError(f'Kling task succeeded without video URL: {body}')
            return _download_verified_mp4(videos[0]['url'], output)
        if st == 'failed':
            raise RuntimeError(f"Kling generation failed: {body.get('task_status_msg') or body}")
        time.sleep(interval_s)
    raise TimeoutError(f'Kling task timed out after {timeout_s}s: {task_id}')


def _poll_seedance(task_id, headers, output, *, timeout_s=360, interval_s=6):
    deadline = time.time() + timeout_s
    url = f'https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks/{task_id}'
    while time.time() < deadline:
        r = requests.get(url, headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json()
        st = (data.get('status') or '').lower()
        if st in ('succeeded', 'completed'):
            content = data.get('content') or {}
            video_url = content.get('video_url') if isinstance(content, dict) else None
            if not video_url and isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get('video_url'):
                        video_url = item['video_url']
                        break
            if not video_url:
                raise RuntimeError(f'Seedance task completed without video_url: {data}')
            return _download_verified_mp4(video_url, output)
        if st in ('failed', 'cancelled', 'expired'):
            raise RuntimeError(f"Seedance task {st}: {data.get('error') or data}")
        time.sleep(interval_s)
    raise TimeoutError(f'Seedance task timed out after {timeout_s}s: {task_id}')


def _is_text_swatch_board(path):
    """Guard against accidentally feeding the text-annotated style-bible.png swatch card into image_to_video."""
    if path is None:
        return False
    path = Path(path)
    return 'style-bible.png' in (path.name.lower(), path.resolve().name.lower())


def extract_last_frame(video_path, out_png):
    """Extract the final clean upright frame of a video shot for visual continuity chaining."""
    src = Path(video_path).resolve()
    if not src.is_file():
        raise FileNotFoundError(f'Previous shot video not found for tail-frame chaining: {src}')
    out_p = Path(out_png).resolve()
    out_p.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=out_p.parent) as temp:
        frame = Path(temp) / 'last.png'
        # A long audio tail can place a seek past the final video packet.
        for seek in (['-sseof', '-0.25'], []):
            result = subprocess.run([
                'ffmpeg', '-y', '-nostdin', '-v', 'error', *seek, '-i', str(src),
                '-map', '0:v:0', '-fps_mode', 'passthrough', '-update', '1', str(frame),
            ], stderr=subprocess.PIPE, check=False)
            if result.returncode == 0 and frame.is_file() and frame.stat().st_size:
                frame.replace(out_p)
                break
            frame.unlink(missing_ok=True)
        else:
            raise RuntimeError(f'Failed to extract tail frame from {src}')
    return out_p


def _opening_dissolve_detections(video_path, report):
    """Check low-rate opening detections against actual adjacent decoded frames."""
    step = max(.5, report['duration'] / 24)
    potential = [cut for cut in report['internal_scene_cuts'] if cut['seconds'] - step < OPENING_DISSOLVE_SECONDS]
    if not potential:
        return []
    width, height = 160, 90
    stop = max(cut['seconds'] for cut in potential) + .10
    raw = subprocess.check_output([
        'ffmpeg', '-nostdin', '-v', 'error', '-i', str(video_path), '-an', '-t', str(stop),
        '-vf', f'scale={width}:{height}', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-',
    ])
    frames = np.frombuffer(raw, np.uint8).reshape(-1, height, width, 3)
    if len(frames) < 2:
        return []
    changes = np.abs(np.diff(frames.astype(np.int16), axis=0)).mean(axis=(1, 2, 3))
    times = np.arange(1, len(frames)) / report['fps']
    smooth = []
    for cut in potential:
        interval = (times > max(-.001, cut['seconds'] - step - .02)) & (times <= cut['seconds'] + .04)
        if interval.any() and float(changes[interval].max()) <= 24:
            smooth.append(cut)
    return smooth


def _run_post_qc(video_path, *, target_fps=30, procedural_preset=None, opening_dissolve=False):
    """Check all clips using the media gate, with motion verification for a designed local dissolve."""
    try:
        report = media.inspect_quality(Path(video_path), target_fps=target_fps)
        if not procedural_preset:
            return report
        advisory = []
        if opening_dissolve and report.get('internal_scene_cuts') and report.get('fps', 0) > 0:
            smooth = _opening_dissolve_detections(video_path, report)
            if smooth:
                report['opening_dissolve_detections'] = smooth
                hard = [cut for cut in report['internal_scene_cuts'] if cut not in smooth]
                report['internal_scene_cuts'] = hard
                report['warnings'] = [item for item in report['warnings'] if '原片内部跳切' not in item]
                if hard:
                    timestamps = '、'.join(f"{cut['seconds']}s" for cut in hard[:4])
                    report['warnings'].append(f'检测到 {len(hard)} 处原片内部跳切（{timestamps}）')
        if procedural_preset in {'bokeh-amber', 'paper-light'} and report.get('sharpness', 0) >= 16:
            advisory = [item for item in report['warnings'] if '画面锐度偏低' in item]
            if advisory:
                report['warnings'] = [item for item in report['warnings'] if item not in advisory]
                report['intentional_soft_focus'] = advisory
        report['ok'] = not report['warnings']
        return report
    except Exception as exc:
        return {'ok': False, 'warnings': [f'QC error: {exc}']}


class BrollQcError(RuntimeError):
    """Generated candidates exhausted the configured quality gate."""


def generate_ai_broll(subject, output, *, provider='auto', preset='code-stream', style_ref=None,
                      first_frame=None, chain_from=None, seconds=5.0, fps=30, dry_run=False, poll=True,
                      timeout_s=300, fallback_procedural=False, run_qc=True, max_retries=1):
    """Generate a clip in staging and publish only an approved candidate."""
    out = Path(output).resolve()
    if max_retries < 0:
        raise ValueError('--max-retries must be nonnegative')
    if first_frame and chain_from:
        raise ValueError('Choose --first-frame or --chain-from for a single opening image')
    if chain_from and Path(chain_from).resolve() == out:
        raise ValueError('B-roll output must differ from the previous shot')
    if _is_text_swatch_board(first_frame):
        raise ValueError('Supply a clean scene keyframe instead of the text-annotated style-bible.png')
    chained_frame = out.with_suffix('.chain-first.png') if chain_from else None
    spec = build_style_prompt(subject, style_ref=style_ref)
    avail = available_ai_providers()
    chosen = avail[0] if provider == 'auto' else provider
    if chosen not in ('veo', 'runway', 'kling', 'seedance', 'procedural'):
        raise ValueError(f'Unknown AI video provider: {chosen}')
    if dry_run:
        opening = first_frame or chained_frame
        return dict(dry_run=True, selected_provider=chosen, available_providers=avail,
                    spec=spec, first_frame=str(opening) if opening else None,
                    chained_from=str(chain_from) if chain_from else None, output=str(out))

    out.parent.mkdir(parents=True, exist_ok=True)
    with (out.parent / f'.{out.name}.lock').open('a') as lock, tempfile.TemporaryDirectory(dir=out.parent) as work:
        fcntl.flock(lock, fcntl.LOCK_EX)
        stage = Path(work)
        opening = first_frame or (style_ref if chosen == 'runway' else None)
        if _is_text_swatch_board(opening):
            raise ValueError('Supply a clean scene keyframe instead of the text-annotated style-bible.png')
        if chain_from:
            opening = extract_last_frame(chain_from, stage / 'last.png')
        elif opening:
            source = Path(opening)
            opening = stage / f'opening{source.suffix.lower()}'
            shutil.copyfile(source, opening)
        return _generate_candidate(
            chosen, preset, spec, seconds, fps, out, stage / 'candidate.mp4', opening, first_frame,
            chain_from, chained_frame, poll=poll, timeout_s=timeout_s, fallback_procedural=fallback_procedural,
            run_qc=run_qc, max_retries=max_retries)


def _generate_candidate(chosen, preset, spec, seconds, fps, out, candidate, opening, explicit_frame,
                        chain_from, chained_frame, *, poll, timeout_s, fallback_procedural, run_qc, max_retries):
    cache_meta = out.with_suffix(out.suffix + '.broll.json')
    failure_meta = out.with_suffix(out.suffix + '.broll.failed.json')
    image_sha = digest(opening) if opening else None
    request = [chosen, preset, spec['prompt'], spec['negative_prompt'], image_sha, str(chain_from or ''),
               bool(explicit_frame), round(float(seconds), 2), int(fps)]
    req_hash = hashlib.sha256(json.dumps(request, ensure_ascii=False).encode()).hexdigest()
    lineage = {}
    if opening:
        lineage['first_frame_sha256'] = image_sha
    if chain_from:
        lineage['chained_from'] = str(chain_from)
    if out.is_file() and cache_meta.is_file():
        try:
            prev = json.loads(cache_meta.read_text(encoding='utf-8'))
            if (prev.get('request_sha256') == req_hash
                    and (fallback_procedural or not prev.get('fallback_from'))
                    and prev.get('output_sha256') == digest(out)
                    and ('qc' not in prev or prev['qc'].get('ok'))):
                if run_qc and 'qc' not in prev:
                    prev['qc'] = _run_post_qc(
                        out, target_fps=fps, procedural_preset=prev.get('preset') if prev.get('provider') == 'procedural' else None,
                        opening_dissolve=bool(chained_frame or explicit_frame))
                    if prev['qc'].get('ok'):
                        atomic_json(cache_meta, prev)
                if not run_qc or prev['qc'].get('ok'):
                    if chained_frame and (not chained_frame.is_file() or digest(chained_frame) != image_sha):
                        os.replace(opening, chained_frame)
                    return dict(prev, **lineage, cached=True)
        except (OSError, ValueError):
            pass

    qc_attempts = []

    def review(result, *, procedural_chain=False):
        result.update(lineage)
        if not run_qc:
            return True
        qc = _run_post_qc(
            candidate, target_fps=fps, procedural_preset=result.get('preset') if result['provider'] == 'procedural' else None,
            opening_dissolve=procedural_chain)
        qc.pop('file', None)
        result['qc'] = qc
        qc_attempts.append(dict(provider=result['provider'], attempt=result['attempt'],
                                candidate_sha256=digest(candidate), qc=qc))
        return qc.get('ok')

    def publish(result):
        result.update(file=str(out), output_sha256=digest(candidate), status='completed',
                      qc_attempts=qc_attempts)
        if chained_frame:
            os.replace(opening, chained_frame)
            result['first_frame'] = str(chained_frame)
        os.replace(candidate, out)
        atomic_json(cache_meta, result)
        failure_meta.unlink(missing_ok=True)
        return result

    def reject():
        result = dict(provider=chosen, status='rejected', file=str(out), spec=spec, request_sha256=req_hash,
                      qc=qc_attempts[-1]['qc'], qc_attempts=qc_attempts, **lineage)
        atomic_json(failure_meta if out.exists() else cache_meta, result)
        raise BrollQcError(f"B-roll failed QC after {len(qc_attempts)} candidate(s): {result['qc'].get('warnings')}")

    def procedural_result(**extra):
        use_opening = opening if explicit_frame or chain_from else None
        res = dict(**render_procedural_broll(preset, candidate, seconds=seconds, fps=fps, first_frame=use_opening),
                   spec=spec, request_sha256=req_hash, attempt=1, **extra)
        if not review(res, procedural_chain=bool(use_opening)):
            return reject()
        return publish(res)

    if chosen == 'procedural':
        return procedural_result()
    attempts = 1 + int(max_retries) if run_qc else 1
    first_frame = opening if explicit_frame or chain_from else None
    try:
        for attempt in range(1, attempts + 1):
            attempt_prompt = spec['prompt'] if attempt == 1 else (
                spec['prompt'] + ' Strict QC retry: single continuous uncut camera shot, full-frame edge-to-edge without black bars, sharp crisp optical focus.'
            )
            if chosen == 'veo':
                key = os.getenv('GEMINI_API_KEY')
                if not key:
                    raise ValueError('Veo requires GEMINI_API_KEY (https://aistudio.google.com/apikey). Or use --provider procedural.')
                model = os.getenv('VEO_MODEL', 'veo-2.0-generate-001')
                url = f'https://generativelanguage.googleapis.com/v1beta/models/{model}:predictLongRunning?key={key}'
                instance = {'prompt': attempt_prompt}
                if first_frame:
                    instance['image'] = {
                        'bytesBase64Encoded': base64.b64encode(Path(first_frame).read_bytes()).decode(),
                        'mimeType': 'image/jpeg' if Path(first_frame).suffix.lower() in ('.jpg', '.jpeg') else 'image/png',
                    }
                body = {'instances': [instance], 'parameters': {'aspectRatio': '16:9', 'durationSeconds': int(seconds)}}
                res = requests.post(url, json=body, timeout=30)
                res.raise_for_status()
                op = res.json()
                op_name = op.get('name')
                if not op_name:
                    raise RuntimeError('Veo returned no operation name')
                if not poll:
                    return dict(provider='veo', status='submitted', operation=op_name, spec=spec, **lineage)
                dl = _poll_veo(op_name, key, candidate, timeout_s=timeout_s)
                out_rec = dict(provider='veo', status='completed', operation=op_name, spec=spec, request_sha256=req_hash, attempt=attempt, **dl)

            elif chosen == 'runway':
                key = os.getenv('RUNWAYML_API_SECRET')
                if not key:
                    raise ValueError('Runway requires RUNWAYML_API_SECRET (https://dev.runwayml.com/). Or use --provider procedural.')
                frame_src = opening
                if not frame_src or not Path(frame_src).is_file():
                    raise ValueError('Runway image_to_video requires --first-frame pointing to a clean scene keyframe image (NOT style-bible.png).')
                if _is_text_swatch_board(frame_src):
                    raise ValueError('Refusing to feed text swatch board style-bible.png into Runway image_to_video; pass a clean scene keyframe via --first-frame.')
                img_b64 = base64.b64encode(Path(frame_src).read_bytes()).decode()
                mime = 'image/jpeg' if Path(frame_src).suffix.lower() in ('.jpg', '.jpeg') else 'image/png'
                headers = {'Authorization': f'Bearer {key}', 'X-Runway-Version': '2024-11-06', 'Content-Type': 'application/json'}
                body = {'model': os.getenv('RUNWAY_MODEL', 'gen4_turbo'), 'promptText': attempt_prompt[:950],
                        'promptImage': f'data:{mime};base64,{img_b64}', 'ratio': '1280:720', 'duration': 5}
                res = requests.post('https://api.dev.runwayml.com/v1/image_to_video', headers=headers, json=body, timeout=30)
                res.raise_for_status()
                task = res.json()
                task_id = task.get('id')
                if not task_id:
                    raise RuntimeError('Runway returned no task ID')
                if not poll:
                    return dict(provider='runway', status='submitted', task=task, spec=spec, **lineage)
                dl = _poll_runway(task_id, headers, candidate, timeout_s=timeout_s)
                out_rec = dict(provider='runway', status='completed', task_id=task_id, spec=spec, request_sha256=req_hash, attempt=attempt, **dl)

            elif chosen == 'kling':
                ak, sk = os.getenv('KLING_ACCESS_KEY'), os.getenv('KLING_SECRET_KEY')
                if not (ak and sk):
                    raise ValueError('Kling requires KLING_ACCESS_KEY and KLING_SECRET_KEY (https://app.klingai.com/global/dev/document-api).')
                token = _kling_jwt(ak, sk)
                headers = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
                body = {'model_name': os.getenv('KLING_MODEL', 'kling-v2-master'), 'prompt': attempt_prompt[:1000],
                        'negative_prompt': spec['negative_prompt'], 'aspect_ratio': '16:9', 'duration': '5'}
                task_type = 'image2video' if first_frame else 'text2video'
                if first_frame:
                    body['image'] = base64.b64encode(Path(first_frame).read_bytes()).decode()
                res = requests.post(f'https://api.klingai.com/v1/videos/{task_type}', headers=headers, json=body, timeout=30)
                res.raise_for_status()
                task = res.json()
                task_id = (task.get('data') or {}).get('task_id')
                if not task_id:
                    raise RuntimeError('Kling returned no task ID')
                if not poll:
                    return dict(provider='kling', status='submitted', task=task, spec=spec, **lineage)
                dl = _poll_kling(task_id, ak, sk, candidate, task_type=task_type, timeout_s=timeout_s)
                out_rec = dict(provider='kling', status='completed', task_id=task_id, spec=spec, request_sha256=req_hash, attempt=attempt, **dl)

            else:
                # seedance (Volcengine Ark)
                key = os.getenv('ARK_API_KEY')
                if not key:
                    raise ValueError('Seedance requires ARK_API_KEY (https://console.volcengine.com/ark/region:ark+cn-beijing/apiKey).')
                headers = {'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'}
                content = [{'type': 'text', 'text': f"{attempt_prompt} --ratio 16:9 --dur 5"}]
                if first_frame:
                    mime = 'image/jpeg' if Path(first_frame).suffix.lower() in ('.jpg', '.jpeg') else 'image/png'
                    b64 = base64.b64encode(Path(first_frame).read_bytes()).decode()
                    content.append({'type': 'image_url', 'role': 'first_frame', 'image_url': {'url': f'data:{mime};base64,{b64}'}})
                body = {'model': os.getenv('SEEDANCE_MODEL', 'doubao-seedance-1-0-pro-250528'), 'content': content}
                res = requests.post('https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks', headers=headers, json=body, timeout=30)
                res.raise_for_status()
                task = res.json()
                task_id = task.get('id')
                if not task_id:
                    raise RuntimeError('Seedance returned no task ID')
                if not poll:
                    return dict(provider='seedance', status='submitted', task=task, spec=spec, **lineage)
                dl = _poll_seedance(task_id, headers, candidate, timeout_s=timeout_s)
                out_rec = dict(provider='seedance', status='completed', task_id=task_id, spec=spec, request_sha256=req_hash, attempt=attempt, **dl)

            if review(out_rec):
                return publish(out_rec)
    except (ValueError, OSError, RuntimeError, requests.RequestException, subprocess.SubprocessError) as exc:
        if not fallback_procedural:
            raise
        return procedural_result(fallback_from=chosen, fallback_reason=type(exc).__name__)
    if fallback_procedural:
        return procedural_result(fallback_from=chosen, fallback_reason='BrollQcError')
    return reject()


def route_edit(edit_path, *, root=None, search_online=False):
    """Build a complete 3-route B-roll & background acquisition plan for every shot in edit.json."""
    edit = json.loads(Path(edit_path).read_text(encoding='utf-8'))
    seq = semantic_broll.plan_sequence(edit_path, root=root, top=2)
    by_shot = {r['shot']: r for r in seq['sequence']}
    ai_avail = available_ai_providers()
    routes = []
    edit_dir = Path(edit_path).resolve().parent
    bible = _load_style_bible(edit_dir)
    # Only use the textless mood plate (never the text-annotated style-bible.png swatch card) for mood ref.
    mood_rel = bible.get('mood_plate_image') if bible else None
    default_mood_ref = (edit_dir / mood_rel) if (mood_rel and (edit_dir / mood_rel).is_file()) else None
    for shot in edit.get('shots', []):
        sid = shot['id']
        cls = classify_shot(shot)
        rec = by_shot.get(sid, {})
        text = ' '.join(l.get('text', '') for l in shot.get('lines', []))
        entry = dict(
            shot=sid,
            route=cls['route'],
            backdrop_route=cls['backdrop_route'],
            reason=cls['reason'],
            desired_scale=rec.get('desired_scale'),
            matched_concepts=rec.get('matched_concepts', []),
            local_library_picks=rec.get('recommended', []),
        )
        if cls['backdrop_route'] == 'ai-video-broll':
            entry['ai_broll_spec'] = build_style_prompt(text[:120] or sid, style_ref=default_mood_ref, style_bible=bible)
            entry['recommended_ai_provider'] = ai_avail[0]
        if search_online and cls['backdrop_route'] == 'real-footage' and rec.get('matched_concepts'):
            q = rec['matched_concepts'][0].replace('_', ' ')
            try:
                entry['online_video_preview'] = media.search_video(q)
            except Exception as exc:
                entry['online_video_preview'] = {'error': str(exc)}
        routes.append(entry)
    counts = {}
    for r in routes:
        counts[r['route']] = counts.get(r['route'], 0) + 1
    return dict(
        edit=str(edit_path),
        available_ai_providers=ai_avail,
        pixabay_enabled=bool(os.getenv('PIXABAY_API_KEY')),
        pexels_enabled=bool(os.getenv('PEXELS_API_KEY')),
        route_counts=counts,
        routes=routes,
    )


def provider_status():
    """Report live readiness and official acquisition URLs for all real-footage & AI B-roll providers."""
    return dict(
        real_footage_sources={
            'pixabay-video': {'configured': bool(os.getenv('PIXABAY_API_KEY')), 'env': ['PIXABAY_API_KEY'], 'url': 'https://pixabay.com/api/docs/', 'note': '免费账号即取即用（推荐）'},
            'pexels': {'configured': bool(os.getenv('PEXELS_API_KEY')), 'env': ['PEXELS_API_KEY'], 'url': 'https://www.pexels.com/api/key/', 'note': '官方暂停新 Key 签发，仅老 Key 可用'},
            'nasa-video': {'configured': True, 'env': [], 'url': 'https://images-api.nasa.gov', 'note': '免 Key 公有领域高清科研/超算视频'},
            'archive-video': {'configured': True, 'env': [], 'url': 'https://archive.org', 'note': '免 Key 开放许可历史档案视频'},
            'commons-video': {'configured': True, 'env': [], 'url': 'https://commons.wikimedia.org', 'note': '免 Key Wikimedia 开源视频'},
        },
        ai_video_providers={
            'veo': {'configured': bool(os.getenv('GEMINI_API_KEY')), 'env': ['GEMINI_API_KEY'], 'url': 'https://aistudio.google.com/apikey'},
            'runway': {'configured': bool(os.getenv('RUNWAYML_API_SECRET')), 'env': ['RUNWAYML_API_SECRET'], 'url': 'https://dev.runwayml.com/'},
            'kling': {'configured': bool(os.getenv('KLING_ACCESS_KEY') and os.getenv('KLING_SECRET_KEY')), 'env': ['KLING_ACCESS_KEY', 'KLING_SECRET_KEY'], 'url': 'https://platform.klingai.com/'},
            'seedance': {'configured': bool(os.getenv('ARK_API_KEY')), 'env': ['ARK_API_KEY', 'SEEDANCE_MODEL_ID'], 'url': 'https://console.volcengine.com/ark'},
            'procedural': {'configured': True, 'env': [], 'url': 'builtin', 'note': '免 Key 离线 1080p 程序化风格锁定动态底片'},
        },
        active_ai_order=available_ai_providers(),
    )


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('providers', help='Show configured status and official key URLs for all B-roll sources & AI models')
    rt = sub.add_parser('route', help='Produce a 3-route B-roll plan for edit.json')
    rt.add_argument('edit', type=Path)
    rt.add_argument('--root', type=Path, help='Personal media library root')
    rt.add_argument('--online', action='store_true', help='Also query online video providers')
    rt.add_argument('--out', type=Path)

    gb = sub.add_parser('generate', help='Generate style-locked AI video B-roll or procedural 1080p backdrop')
    gb.add_argument('subject', help='Visual scene description')
    gb.add_argument('--out', type=Path, required=True)
    gb.add_argument('--provider', choices=['auto', 'veo', 'runway', 'kling', 'seedance', 'procedural'], default='auto')
    gb.add_argument('--preset', choices=['code-stream', 'bokeh-amber', 'topo-grid', 'paper-light'], default='code-stream')
    gb.add_argument('--style-ref', type=Path, help='Optional textless lighting/color mood plate reference')
    gb.add_argument('--first-frame', type=Path, help='Optional clean scene keyframe image for image-to-video models (never style-bible.png)')
    gb.add_argument('--chain-from', type=Path, help='Previous shot video MP4; automatically extracts its last frame as --first-frame for continuity')
    gb.add_argument('--seconds', type=float, default=5.0)
    gb.add_argument('--fps', type=int, default=30)
    gb.add_argument('--no-wait', action='store_true', help='Submit async AI job without polling to download')
    gb.add_argument('--timeout', type=int, default=300, help='Polling timeout in seconds (default 300)')
    gb.add_argument('--no-qc', action='store_true', help='Skip automatic post-generation media.py QC gate')
    gb.add_argument('--max-retries', type=int, default=1, help='Extra generation attempts after failed QC (default 1, two attempts total)')
    gb.add_argument('--fallback-procedural', action='store_true', help='Fallback cleanly to procedural backdrop if remote API fails')
    gb.add_argument('--dry-run', action='store_true')

    args = ap.parse_args()
    if args.cmd == 'providers':
        print(json.dumps(provider_status(), ensure_ascii=False, indent=2))
    elif args.cmd == 'route':
        res = route_edit(args.edit, root=args.root, search_online=args.online)
        txt = json.dumps(res, ensure_ascii=False, indent=2) + '\n'
        if args.out:
            args.out.parent.mkdir(parents=True, exist_ok=True)
            args.out.write_text(txt, encoding='utf-8')
            print(args.out)
        else:
            print(txt)
    else:
        try:
            res = generate_ai_broll(
                args.subject, args.out, provider=args.provider, preset=args.preset,
                style_ref=args.style_ref, first_frame=args.first_frame, chain_from=args.chain_from,
                seconds=args.seconds, fps=args.fps, dry_run=args.dry_run,
                poll=not args.no_wait, timeout_s=args.timeout,
                fallback_procedural=args.fallback_procedural,
                run_qc=not args.no_qc, max_retries=args.max_retries,
            )
        except BrollQcError as exc:
            gb.exit(1, f"{exc}\n")
        print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
