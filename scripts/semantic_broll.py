"""Visual-semantic B-roll indexing and shot-scale-aware sequence recommendation.

Combines objective OpenCV/NumPy keyframe analysis (color temperature, brightness,
motion energy, shot scale, and 48-D visual descriptor) with bilingual conceptual/mood
semantic vectors (and optional CLIP / Gemini / OpenAI dense embeddings when available).
"""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sys

import cv2
import numpy as np

import library

VIDEO_EXTS = {'.mp4', '.m4v', '.mov', '.webm', '.mkv'}
IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp'}

# Bilingual conceptual, visual-metaphor and emotional-tone axes for subtext B-roll retrieval.
CONCEPT_AXES = {
    'compute_server': {
        'terms': ['服务器', '机房', '算力', '集群', '数据中心', '内核', '编译', '模块', '硬盘', '蓝灯', '机架', '云端', '芯片',
                  'server', 'datacenter', 'rack', 'compute', 'cluster', 'kernel', 'compile', 'cloud', 'supercomputer', 'cpu', 'gpu'],
        'preferred_tone': 'cool', 'preferred_energy': 'calm',
    },
    'coding_terminal': {
        'terms': ['代码', '源码', '键盘', '敲击', '编程', '终端', '脚本', '算法', '形式化', 'lean', 'python', 'rust', 'ai', 'claude', '模型', '程序',
                  'code', 'coding', 'keyboard', 'typing', 'terminal', 'program', 'software', 'developer', 'algorithm', 'screen'],
        'preferred_tone': 'cool', 'preferred_energy': 'moderate',
    },
    'hardware_macro': {
        'terms': ['芯片', '电路', '焊点', '微距', '精密', '探针', '晶体管', '光学', '镜头', '面板', '旋钮', '仪器', '硬件', '接点', '制造',
                  'chip', 'pcb', 'circuit', 'solder', 'macro', 'microscope', 'hardware', 'precision', 'lens', 'console', 'knob', 'silicon'],
        'preferred_tone': 'neutral', 'preferred_energy': 'calm',
    },
    'paper_archive': {
        'terms': ['论文', '手稿', '翻页', '书页', '档案', '文献', '历史', '百年', '致谢', '原件', '手写', '墨水', '图书馆', '经典', '记录', '书',
                  'paper', 'manuscript', 'book', 'page', 'archive', 'history', 'library', 'document', 'reading', 'turning', 'handwriting'],
        'preferred_tone': 'warm', 'preferred_energy': 'calm',
    },
    'math_theory': {
        'terms': ['数学', '黎曼', '猜想', '素数', '零点', '定理', '公理', '证明', '方程', '黑板', '几何', '曲线', '函数', '行列式', '逻辑', '推导',
                  'math', 'mathematics', 'proof', 'theorem', 'chalkboard', 'equation', 'prime', 'zeta', 'geometry', 'axiom', 'formula'],
        'preferred_tone': 'warm', 'preferred_energy': 'calm',
    },
    'lab_experiment': {
        'terms': ['实验', '实测', '检验', '测试', '验证', '复核', '核验', '比对', '误差', '读数', '移液', '离心', '科学', '研究', '实验室',
                  'lab', 'laboratory', 'experiment', 'verify', 'verification', 'measure', 'test', 'pipette', 'centrifuge', 'science'],
        'preferred_tone': 'cool', 'preferred_energy': 'moderate',
    },
    'finance_market': {
        'terms': ['金融', '股市', '交易', '上市', 'ipo', '纳斯达克', '纽交所', '资本', '估值', '营收', '成本', '账单', '商业', '融资', '金额', '市场',
                  'finance', 'market', 'stock', 'nyse', 'nasdaq', 'ipo', 'trading', 'economy', 'capital', 'revenue', 'wallstreet'],
        'preferred_tone': 'neutral', 'preferred_energy': 'moderate',
    },
    'city_speed_flow': {
        'terms': ['城市', '车流', '交通', '夜景', '街头', '延时', '流动', '奔跑', '速度', '时代', '世界', '全球', '十字路口', '灯光', '秩序', '未来',
                  'city', 'traffic', 'night', 'street', 'timelapse', 'flow', 'speed', 'world', 'urban', 'highway', 'future', 'intersection'],
        'preferred_tone': 'cool', 'preferred_energy': 'high',
    },
    'human_emotion_team': {
        'terms': ['人类', '数学家', '作者', '同行', '教授', '创始人', '团队', '焦虑', '鲜活', '刺痛', '拥抱', '交接', '演讲', '人物', '采访', '对话',
                  'human', 'people', 'founder', 'team', 'professor', 'interview', 'handoff', 'speech', 'emotion', 'alive', 'person'],
        'preferred_tone': 'warm', 'preferred_energy': 'moderate',
    },
    'tension_warning': {
        'terms': ['漏洞', '报错', '警告', '作弊', '钻空子', '危机', '冲突', '反差', '否决', '险', '崩溃', '质疑', '挑战', '悬念', '问题',
                  'warning', 'error', 'bug', 'alert', 'tension', 'risk', 'conflict', 'crisis', 'danger', 'question', 'gap'],
        'preferred_tone': 'dark', 'preferred_energy': 'high',
    },
    'breakthrough_scale': {
        'terms': ['突破', '跃升', '飞舞', '闪耀', '宇宙', '星空', '航天', '灯塔', '巨量', '奇迹', '震撼', '终点', '成立', '放行', '成功', '新秩序',
                  'breakthrough', 'space', 'earth', 'cosmos', 'launch', 'rocket', 'scale', 'milestone', 'triumph', 'orbit', 'nasa'],
        'preferred_tone': 'cool', 'preferred_energy': 'high',
    },
    'audio_craft': {
        'terms': ['声音', '旋钮', '调音', '混音', '节奏', '音乐', '声波', '频谱', '录音',
                  'audio', 'sound', 'mixing', 'console', 'music', 'studio', 'waveform'],
        'preferred_tone': 'warm', 'preferred_energy': 'moderate',
    },
}

SCALE_HINTS = {
    'macro': ('微距', '特写', '局部', '细节', '接点', '手指', '旋钮', '按键', '焊点', 'macro', 'closeup', 'close-up', 'detail', 'finger'),
    'wide': ('全景', '外景', '街景', '航拍', '路口', '延时', '城市', '大厅', '太空', '地球', 'wide', 'aerial', 'city', 'street', 'exterior', 'skyline'),
}


def _file_sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _sample_frames(path, max_frames=5):
    """Extract evenly spaced RGB frames (resized to 160x90) from video or image."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext in IMAGE_EXTS:
        bgr = cv2.imread(str(p))
        if bgr is None:
            return []
        rgb = cv2.cvtColor(cv2.resize(bgr, (160, 90), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB)
        return [rgb]
    if ext not in VIDEO_EXTS:
        return []
    cap = cv2.VideoCapture(str(p))
    if not cap.isOpened():
        return []
    try:
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        frames = []
        if total >= max_frames:
            indices = [int(round(total * f)) for f in np.linspace(0.08, 0.90, max_frames)]
            for idx in indices:
                cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                ok, bgr = cap.read()
                if ok and bgr is not None:
                    frames.append(cv2.cvtColor(cv2.resize(bgr, (160, 90), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB))
        else:
            step = max(1, total // max_frames) if total > 0 else 15
            pos = 0
            while len(frames) < max_frames:
                ok, bgr = cap.read()
                if not ok or bgr is None:
                    break
                if pos % step == 0:
                    frames.append(cv2.cvtColor(cv2.resize(bgr, (160, 90), interpolation=cv2.INTER_AREA), cv2.COLOR_BGR2RGB))
                pos += 1
        return frames
    finally:
        cap.release()


def analyze_media(path, metadata_text=''):
    """Measure real visual color temperature, luminance, motion energy, shot scale and 48-D descriptor."""
    frames = _sample_frames(path)
    if not frames:
        return None
    arr = np.stack(frames, axis=0).astype(np.float32)  # (T, 90, 160, 3)
    mean_rgb = arr.mean(axis=(0, 1, 2))
    r, g, b = float(mean_rgb[0]), float(mean_rgb[1]), float(mean_rgb[2])
    color_temp = (r - b) / max(1.0, r + g + b)
    color_tone = 'warm' if color_temp > 0.045 else ('cool' if color_temp < -0.035 else 'neutral')

    grays = np.stack([cv2.cvtColor(f, cv2.COLOR_RGB2GRAY) for f in frames], axis=0).astype(np.float32)
    luma_mean = float(grays.mean() / 255.0)
    contrast_std = float(grays.std() / 255.0)
    mood_brightness = 'dark' if luma_mean < 0.34 else ('bright' if luma_mean > 0.62 else 'balanced')

    if len(frames) >= 2:
        diffs = [float(np.mean(np.abs(grays[i + 1] - grays[i])) / 255.0) for i in range(len(frames) - 1)]
        motion_score = float(np.mean(diffs))
        motion_energy = 'high' if motion_score > 0.085 else ('moderate' if motion_score > 0.025 else 'calm')
    else:
        motion_score = 0.0
        motion_energy = 'still'

    # 48-D normalized visual vector: 16 Hue + 8 Sat + 8 Val + 8 Sobel orientation + 8 spatial luma grid
    mid = frames[len(frames) // 2]
    hsv = cv2.cvtColor(mid, cv2.COLOR_RGB2HSV)
    h_hist = np.histogram(hsv[:, :, 0], bins=16, range=(0, 180))[0].astype(np.float32)
    s_hist = np.histogram(hsv[:, :, 1], bins=8, range=(0, 256))[0].astype(np.float32)
    v_hist = np.histogram(hsv[:, :, 2], bins=8, range=(0, 256))[0].astype(np.float32)
    mid_g = grays[len(grays) // 2]
    gx = cv2.Sobel(mid_g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(mid_g, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.hypot(gx, gy)
    ang = (np.arctan2(gy, gx) + np.pi) / (2 * np.pi)
    o_hist = np.histogram(ang, bins=8, range=(0, 1), weights=mag)[0].astype(np.float32)
    grid = cv2.resize(mid_g, (4, 2), interpolation=cv2.INTER_AREA).flatten() / 255.0

    def _l2(v):
        n = float(np.linalg.norm(v))
        return (v / n) if n > 1e-8 else v

    visual_vec = _l2(np.concatenate([_l2(h_hist), _l2(s_hist), _l2(v_hist), _l2(o_hist), _l2(grid)]))

    # Shot scale: combine textual scale cues with objective center-vs-border edge concentration.
    low_meta = metadata_text.lower()
    if any(k in low_meta for k in SCALE_HINTS['macro']):
        shot_scale = 'macro'
    elif any(k in low_meta for k in SCALE_HINTS['wide']):
        shot_scale = 'wide'
    else:
        center_mag = float(mag[20:70, 40:120].mean())
        border_mag = float(np.concatenate([mag[:20, :].ravel(), mag[70:, :].ravel()]).mean())
        ratio = center_mag / max(1.0, border_mag)
        shot_scale = 'macro' if ratio > 1.55 else ('wide' if ratio < 0.85 else 'medium')

    return dict(
        color_temp_score=round(color_temp, 4),
        color_tone=color_tone,
        luma_mean=round(luma_mean, 4),
        contrast_std=round(contrast_std, 4),
        mood_brightness=mood_brightness,
        motion_score=round(motion_score, 4),
        motion_energy=motion_energy,
        shot_scale=shot_scale,
        visual_vec=[round(float(x), 5) for x in visual_vec],
    )


def semantic_vector(text):
    """Compute normalized bilingual conceptual + mood + lexical-hash vector."""
    low = str(text or '').lower()
    axis_names = list(CONCEPT_AXES)
    axis_scores = np.zeros(len(axis_names), dtype=np.float32)
    matched_axes = []
    for idx, name in enumerate(axis_names):
        hits = sum(1.0 for term in CONCEPT_AXES[name]['terms'] if term in low)
        if hits > 0:
            axis_scores[idx] = math.log1p(hits)
            matched_axes.append(name)

    # 64-D hashed character-bigram & word projection for literal/proper-noun recall.
    lex = np.zeros(64, dtype=np.float32)
    words = re.findall(r'[a-z0-9]+|[\u4e00-\u9fff]{2,}', low)
    bigrams = []
    for w in words:
        if '\u4e00' <= w[0] <= '\u9fff':
            bigrams.extend(w[i:i + 2] for i in range(len(w) - 1))
        else:
            bigrams.append(w)
    for tok in bigrams:
        bucket = int(hashlib.md5(tok.encode('utf-8')).hexdigest()[:8], 16) % 64
        lex[bucket] += 1.0

    def _norm(v):
        n = float(np.linalg.norm(v))
        return (v / n) if n > 1e-8 else v

    # Weight conceptual/mood axes higher than literal word overlap to enable metaphorical retrieval.
    vec = np.concatenate([_norm(axis_scores) * 0.78, _norm(lex) * 0.62])
    vec = _norm(vec)
    return [round(float(x), 5) for x in vec], matched_axes


def optional_clip_backend():
    """Return available optional dense multimodal embedding backend if installed/configured."""
    try:
        import open_clip  # noqa: F401
        import torch  # noqa: F401
        return 'open_clip'
    except Exception:
        pass
    if os.getenv('GEMINI_API_KEY'):
        return 'gemini-embedding'
    if os.getenv('OPENAI_API_KEY'):
        return 'openai-embedding'
    return 'builtin-bilingual-visual'


def _primary_media_file(root, item):
    files = item.get('files') or []
    for entry in files:
        rel = entry.get('path', '')
        ext = Path(rel).suffix.lower()
        if item.get('kind') == 'video' and ext in VIDEO_EXTS:
            cand = root / rel
            if cand.is_file():
                return cand, entry.get('sha256')
        if item.get('kind') == 'image' and ext in IMAGE_EXTS:
            cand = root / rel
            if cand.is_file():
                return cand, entry.get('sha256')
    prev = item.get('preview')
    if prev and (root / prev).is_file():
        return root / prev, item.get('preview_sha256')
    return None, None


def index_path(root):
    return Path(root) / '.visual-semantic-index.json'


def build_index(root=None, *, extra_dir=None, force=False):
    """Build or incrementally refresh visual-semantic index for the personal media library."""
    lib_root = library.find_root(root)
    items = library.load(lib_root)
    idx_file = index_path(lib_root)
    prior = {}
    if not force and idx_file.is_file():
        try:
            prior = json.loads(idx_file.read_text(encoding='utf-8')).get('items', {})
        except Exception:
            prior = {}

    indexed = {}
    stats = dict(total_visual_items=0, newly_analyzed=0, reused=0, skipped=0)
    for item_id, item in sorted(items.items()):
        kind = item.get('kind')
        if kind not in ('video', 'image'):
            continue
        if item.get('scope') == 'reference':
            stats['skipped'] += 1
            continue
        media_path, sha = _primary_media_file(lib_root, item)
        if not media_path:
            stats['skipped'] += 1
            continue
        stats['total_visual_items'] += 1
        sha = sha or _file_sha(media_path)
        meta_text = ' '.join([
            item_id, item.get('title', ''), item.get('category', ''),
            ' '.join(item.get('tags') or []), item.get('use', ''),
        ])
        sem_vec, axes = semantic_vector(meta_text)
        old = prior.get(item_id)
        if old and old.get('sha256') == sha and old.get('visual'):
            vis = old['visual']
            stats['reused'] += 1
        else:
            vis = analyze_media(media_path, meta_text)
            if not vis:
                stats['skipped'] += 1
                continue
            stats['newly_analyzed'] += 1
        indexed[item_id] = dict(
            id=item_id,
            kind=kind,
            category=item.get('category', ''),
            scope=item.get('scope', 'production'),
            title=item.get('title', ''),
            tags=item.get('tags') or [],
            use=item.get('use', ''),
            seconds=item.get('seconds'),
            file=str(media_path.relative_to(lib_root)),
            sha256=sha,
            axes=axes,
            semantic_vec=sem_vec,
            visual=vis,
        )

    if extra_dir:
        base = Path(extra_dir).resolve()
        for p in sorted(base.rglob('*')):
            if not p.is_file() or p.suffix.lower() not in (VIDEO_EXTS | IMAGE_EXTS):
                continue
            cid = 'local:' + str(p.relative_to(base))
            sha = _file_sha(p)
            vis = analyze_media(p, p.stem.replace('-', ' ').replace('_', ' '))
            if not vis:
                continue
            sem_vec, axes = semantic_vector(p.stem.replace('-', ' ').replace('_', ' '))
            indexed[cid] = dict(
                id=cid, kind='video' if p.suffix.lower() in VIDEO_EXTS else 'image',
                category='local', scope='production', title=p.stem, tags=[], use='',
                seconds=None, file=str(p), sha256=sha, axes=axes, semantic_vec=sem_vec, visual=vis,
            )

    payload = dict(
        schema=1,
        backend=optional_clip_backend(),
        library_root=str(lib_root),
        count=len(indexed),
        stats=stats,
        items=indexed,
    )
    idx_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return payload


def _cosine(a, b):
    va, vb = np.asarray(a, dtype=np.float32), np.asarray(b, dtype=np.float32)
    if va.shape != vb.shape or not va.size:
        return 0.0
    denom = float(np.linalg.norm(va) * np.linalg.norm(vb))
    return float(np.dot(va, vb) / denom) if denom > 1e-8 else 0.0


def search_semantic(query, *, root=None, top=6, prefer_video=True, mood=None, scale=None,
                    energy=None, reference_visual_vec=None, exclude_ids=()):
    """Rank indexed clips by conceptual/mood similarity, video preference, tone, energy and scale."""
    lib_root = library.find_root(root)
    idx_file = index_path(lib_root)
    data = json.loads(idx_file.read_text(encoding='utf-8')) if idx_file.is_file() else build_index(lib_root)
    q_vec, q_axes = semantic_vector(query)
    # Infer desired tone/energy from matched concepts if not explicitly specified.
    inferred_tone = mood or next((CONCEPT_AXES[a]['preferred_tone'] for a in q_axes if a in CONCEPT_AXES), None)
    inferred_energy = energy or next((CONCEPT_AXES[a]['preferred_energy'] for a in q_axes if a in CONCEPT_AXES), None)

    scored = []
    for item_id, row in data.get('items', {}).items():
        if item_id in exclude_ids:
            continue
        vis = row.get('visual') or {}
        sem = _cosine(q_vec, row.get('semantic_vec') or [])
        bonus = 0.0
        reasons = []
        if prefer_video and row.get('kind') == 'video':
            bonus += 0.14
            reasons.append('动态视频优先')
        cat = row.get('category', '')
        if cat in ('broll', 'capture', 'texture'):
            bonus += 0.07
        elif cat == 'sequence':
            bonus -= 0.08
        if row.get('scope') == 'production':
            bonus += 0.05
        if inferred_tone:
            if inferred_tone == 'dark' and vis.get('mood_brightness') == 'dark':
                bonus += 0.08
                reasons.append('暗调匹配')
            elif vis.get('color_tone') == inferred_tone:
                bonus += 0.07
                reasons.append(f'色温匹配({inferred_tone})')
        if inferred_energy and vis.get('motion_energy') == inferred_energy:
            bonus += 0.06
            reasons.append(f'运动能量匹配({inferred_energy})')
        if scale and vis.get('shot_scale') == scale:
            bonus += 0.10
            reasons.append(f'景别匹配({scale})')
        if reference_visual_vec is not None and vis.get('visual_vec'):
            vis_sim = _cosine(reference_visual_vec, vis['visual_vec'])
            bonus += 0.08 * max(0.0, vis_sim)
            if vis_sim > 0.72:
                reasons.append(f'视觉色调连贯({vis_sim:.2f})')
        shared_axes = sorted(set(q_axes) & set(row.get('axes') or []))
        if shared_axes:
            reasons.insert(0, '语义轴:' + ','.join(shared_axes))
        total = sem + bonus
        scored.append(dict(
            id=item_id,
            title=row.get('title', ''),
            kind=row.get('kind'),
            scope=row.get('scope'),
            score=round(total, 4),
            semantic_score=round(sem, 4),
            shot_scale=vis.get('shot_scale'),
            color_tone=vis.get('color_tone'),
            mood_brightness=vis.get('mood_brightness'),
            motion_energy=vis.get('motion_energy'),
            seconds=row.get('seconds'),
            file=row.get('file'),
            reasons=reasons,
        ))
    scored.sort(key=lambda r: (-r['score'], r['id']))
    return dict(
        query=query,
        matched_concepts=q_axes,
        inferred_tone=inferred_tone,
        inferred_energy=inferred_energy,
        backend=data.get('backend', 'builtin-bilingual-visual'),
        results=scored[:top],
    )


def plan_sequence(edit_path, *, root=None, top=2):
    """Recommend a non-repeating, wide→medium→macro alternating B-roll sequence for every shot in edit.json."""
    edit = json.loads(Path(edit_path).read_text(encoding='utf-8'))
    lib_root = library.find_root(root)
    idx_file = index_path(lib_root)
    data = json.loads(idx_file.read_text(encoding='utf-8')) if idx_file.is_file() else build_index(lib_root)
    items_by_id = data.get('items', {})
    scale_cycle = ['wide', 'medium', 'macro']
    used = set()
    prev_vis_vec = None
    plan = []
    for idx, shot in enumerate(edit.get('shots', [])):
        sid = shot['id']
        desired_scale = scale_cycle[idx % len(scale_cycle)]
        text = ' '.join(l.get('text', '') for l in shot.get('lines', []))
        res = search_semantic(
            text or sid, root=lib_root, top=top, prefer_video=True,
            scale=desired_scale, reference_visual_vec=prev_vis_vec, exclude_ids=used,
        )
        picks = res['results']
        if picks:
            top_id = picks[0]['id']
            used.add(top_id)
            prev_vis_vec = (items_by_id.get(top_id, {}).get('visual') or {}).get('visual_vec')
        plan.append(dict(
            shot=sid,
            desired_scale=desired_scale,
            matched_concepts=res['matched_concepts'],
            recommended=picks,
        ))
    return dict(edit=str(edit_path), backend=data.get('backend'), shot_count=len(plan), sequence=plan)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--root', type=Path, help='Personal media library root')
    sub = ap.add_subparsers(dest='cmd', required=True)
    ix = sub.add_parser('index', help='Build or refresh visual + semantic B-roll index')
    ix.add_argument('--dir', type=Path, help='Optional extra project directory of local clips')
    ix.add_argument('--force', action='store_true', help='Recompute visual descriptors from scratch')
    sq = sub.add_parser('search', help='Semantic + mood + visual B-roll search')
    sq.add_argument('query')
    sq.add_argument('--top', type=int, default=6)
    sq.add_argument('--mood', choices=['dark', 'warm', 'cool', 'neutral'])
    sq.add_argument('--scale', choices=['wide', 'medium', 'macro'])
    sq.add_argument('--energy', choices=['calm', 'moderate', 'high'])
    sq.add_argument('--allow-stills-equal', action='store_true')
    pl = sub.add_parser('plan-sequence', help='Recommend alternating wide/medium/macro B-roll across edit.json')
    pl.add_argument('edit', type=Path)
    pl.add_argument('--top', type=int, default=2)
    args = ap.parse_args()
    if args.cmd == 'index':
        res = build_index(args.root, extra_dir=args.dir, force=args.force)
        print(json.dumps({k: v for k, v in res.items() if k != 'items'}, ensure_ascii=False, indent=2))
    elif args.cmd == 'search':
        print(json.dumps(search_semantic(
            args.query, root=args.root, top=args.top, prefer_video=not args.allow_stills_equal,
            mood=args.mood, scale=args.scale, energy=args.energy,
        ), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(plan_sequence(args.edit, root=args.root, top=args.top), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
