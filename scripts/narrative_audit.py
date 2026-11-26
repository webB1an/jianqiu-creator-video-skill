"""Audit pacing, hook timing, cut monotony, visual freshness, transitions and narration breath."""
import argparse
import json
import math
import os
from itertools import groupby
from pathlib import Path
from statistics import median, pstdev

from narration_pace import RELATIVE_YEARS, SPOKEN_FIGURE
from transitions import STILLS

DYNAMIC_SHOT_KEYS = (
    'camera', 'camera_drift', 'motion_layers', 'evidence_window',
    'remotion_effect', 'explainer_scene', 'value_comparison', 'math_motion', 'geo_map', 'parallax_25d',
)
VIDEO_EXTS = {'.mp4', '.m4v', '.mov', '.webm', '.mkv'}
ONE_BEAT_TEXT_EFFECTS = {'quote-card', 'end-card', 'chapter-title', 'kinetic-hook'}
GENERATED_SLIDE_KEYS = ('remotion_effect', 'explainer_scene', 'value_comparison')


def _footage_source(shot, visual):
    return bool(shot.get('footage') or shot.get('in') is not None or
                'assets' in Path(os.path.normpath(visual)).parts[:-1])


def _shot_visual_medium(shot):
    """Classify a shot into one of 8 fine-grained editorial mediums for visual diversity auditing."""
    if 'parallax_25d' in shot:
        return 'parallax_25d'
    if 'geo_map' in shot:
        return 'geo_map'
    if 'math_motion' in shot:
        return 'math_motion'
    if 'evidence_window' in shot:
        return 'evidence_doc'
    if 'value_comparison' in shot:
        return 'data_motion'
    rem = shot.get('remotion_effect')
    if isinstance(rem, dict):
        eff = rem.get('effect', '')
        if eff in ONE_BEAT_TEXT_EFFECTS:
            return 'text_card'
        if eff in ('broll-evidence-pop', 'text-highlight', 'callout-annotation'):
            return 'evidence_doc'
        if eff in ('stat-counter', 'bar-chart', 'metric-grid', 'comparison-table', 'timeline-progress', 'exploded-diagram'):
            return 'data_motion'
        return 'explainer_diagram'
    if 'explainer_scene' in shot:
        return 'explainer_diagram'
    visual = str(shot.get('visual') or '')
    ext = Path(visual).suffix.lower()
    if ext in VIDEO_EXTS and _footage_source(shot, visual):
        return 'live_footage'
    if ext in STILLS:
        return 'evidence_doc' if (shot.get('camera') or shot.get('camera_drift')) else 'text_card'
    return 'explainer_diagram'


def _shot_visual_kind(shot):
    """Distinguish live footage, generated motion and graphic slides."""
    if 'math_motion' in shot or 'geo_map' in shot or 'parallax_25d' in shot:
        return 'generated_motion'
    rem = shot.get('remotion_effect')
    if isinstance(rem, dict):
        bg = rem.get('background') or ''
        params = rem.get('params') or {}
        media_vals = [bg] + [str(params.get(k, '')) for k in ('source', 'main', 'insetSource', 'before', 'after', 'outgoing', 'incoming')]
        if any(Path(v).suffix.lower() in VIDEO_EXTS for v in media_vals if v):
            return 'motion_with_video_bg'
        return 'graphic_slide'
    if 'explainer_scene' in shot or 'value_comparison' in shot:
        return 'graphic_slide'
    visual = str(shot.get('visual') or '')
    ext = Path(visual).suffix.lower()
    if ext in STILLS:
        return 'graphic_slide'
    if ext in VIDEO_EXTS:
        # Generated slides/screen-motion outputs under shots/ are not live-action footage unless explicitly marked or trimmed from real video.
        if _footage_source(shot, visual):
            return 'live_video'
        return 'graphic_slide'
    return 'graphic_slide'


def _transition_name(shot):
    opt = shot.get('transition', 'cut')
    if isinstance(opt, str):
        return opt
    return opt.get('name', 'cut') if isinstance(opt, dict) else 'cut'


def measure_visual_diversity(timeline, edit):
    """Measure planned visual coverage on the actual picture clock."""
    shots = {shot['id']: shot for shot in edit['shots']}
    fps = timeline['fps']
    medium_frames = {}
    sequence, streaks, dead_zones, jl_cuts = [], [], [], []
    for timing in timeline['shots']:
        sid, frames = timing['id'], timing['frames']
        if sid not in shots:
            raise ValueError(f'{sid}: edit and timeline have different shots. Run prepare.py.')
        shot = shots[sid]
        medium = _shot_visual_medium(shot)
        medium_frames[medium] = medium_frames.get(medium, 0) + frames
        sequence.append(dict(shot=sid, medium=medium, start_frame=timing['start_frame'], frames=frames))
        if timing.get('cut_style'):
            jl_cuts.append(dict(shot=sid, style=timing['cut_style'], shift_frames=timing['cut_shift_frames']))
        kind = _shot_visual_kind(shot)
        has_motion = kind in ('live_video', 'motion_with_video_bg', 'generated_motion') or any(
            shot.get(k) for k in ('camera', 'camera_drift', 'motion_layers', 'evidence_window',
                                  'explainer_scene', 'value_comparison'))
        effect = shot.get('remotion_effect') or {}
        if isinstance(effect, dict) and effect.get('effect') and effect['effect'] not in ONE_BEAT_TEXT_EFFECTS:
            has_motion = True
        if frames > 8 * fps and not has_motion:
            dead_zones.append(dict(shot=sid, medium=medium, seconds=round(frames / fps, 2)))
    for medium, group in groupby(sequence, key=lambda row: row['medium']):
        ids = [row['shot'] for row in group]
        if len(ids) >= 3:
            streaks.append(dict(medium=medium, shots=ids, count=len(ids)))
    total = sum(medium_frames.values())
    shares = {k: frames / total for k, frames in medium_frames.items()} if total else {}
    return dict(status='needs_review' if streaks or dead_zones else 'clear',
                unique_mediums=len(shares), shot_media=sequence,
                shannon_entropy=round(-sum(v * math.log2(v) for v in shares.values() if v), 3),
                medium_seconds={k: round(frames / fps, 2) for k, frames in medium_frames.items()},
                medium_shares={k: round(v, 3) for k, v in shares.items()},
                same_medium_streaks=streaks, dead_zones_over_8s=dead_zones, jl_cuts=jl_cuts)


def audit(timeline, edit=None, *, subs=None, sections=None, bridges=None, beat_grid=None,
          cover_cfg=None, packaging_cfg=None):
    """Compute objective, frame-referenced editorial pacing & narrative diagnostics."""
    fps = timeline['fps']
    total_frames = timeline['frames']
    total_s = total_frames / fps
    t_shots = timeline.get('shots', [])
    e_shots = {s['id']: s for s in (edit or {}).get('shots', [])}
    takes = timeline.get('voice_takes', [])
    suggestions = []

    # 1. Opening Hook Audit (0–3s & 0–5s)
    first_shot = t_shots[0] if t_shots else None
    first_shot_s = round(first_shot['frames'] / fps, 3) if first_shot else 0.0
    first_speech_s = None
    if takes and all(k in takes[0] for k in ('start_sample', 'sample_rate')):
        first_speech_s = round(takes[0]['start_sample'] / takes[0]['sample_rate'], 3)
    elif subs:
        first_speech_s = round(subs[0][0], 3)
    cuts_3s = sum(1 for s in t_shots[1:] if s['start_frame'] < round(3 * fps))
    cuts_5s = sum(1 for s in t_shots[1:] if s['start_frame'] < round(5 * fps))

    first_edit = e_shots.get(first_shot['id'], {}) if first_shot else {}
    first_is_still = Path(first_edit.get('visual', '')).suffix.lower() in STILLS if first_edit else False
    first_has_motion = (not first_is_still) or any(k in first_edit for k in DYNAMIC_SHOT_KEYS)
    intent = (edit or {}).get('story_intent') or {}
    intent_complete = all(isinstance(intent.get(k), str) and intent[k].strip() for k in ('kind', 'main_point', 'viewer_value'))

    hook = dict(
        story_intent_complete=intent_complete if edit is not None else None,
        first_shot=first_shot['id'] if first_shot else None,
        first_shot_seconds=first_shot_s,
        first_speech_seconds=first_speech_s,
        cuts_in_first_3s=cuts_3s,
        cuts_in_first_5s=cuts_5s,
        opening_has_visual_motion=first_has_motion if edit is not None else None,
    )
    if edit is not None and not intent_complete:
        suggestions.append('story_intent 尚未写清 kind / main_point / viewer_value，建议在配音前明确本片唯一核心判断与观众收获。')
    if first_speech_s is not None and first_speech_s > 0.65 and cuts_3s == 0:
        suggestions.append(f'开场首句延迟 {first_speech_s:.2f}s 且前 3 秒无换镜，建议压缩首镜 lead 或用动作/钩子直入。')
    if edit is not None and first_shot_s > 4.5 and not first_has_motion:
        suggestions.append(f"首镜 {first_shot['id']} 为静态图且持续 {first_shot_s:.2f}s，建议添加 camera 推近、分层动效或提前切入第一条证据。")
    if edit is not None and cuts_5s == 0 and first_shot_s > 6.5:
        suggestions.append(f"开场前 5 秒无换镜且首镜 {first_shot['id']} 长达 {first_shot_s:.1f}s，容易像开场 PPT；建议在前 3–5 秒切入实拍 B-roll、第二景别或核心证据特写。")

    # 2. Cut Cadence & Metronome Monotony Detection
    durations = [s['frames'] / fps for s in t_shots]
    mean_d = sum(durations) / len(durations) if durations else 0.0
    cv = round(pstdev(durations) / mean_d, 3) if len(durations) >= 2 and mean_d > 0 else 0.0
    monotonous_runs = []
    i = 0
    while i < len(durations):
        j = i + 1
        while j < len(durations):
            window = durations[i:j + 1]
            med = median(window)
            if med > 0 and all(abs(d - med) / med <= 0.15 for d in window):
                j += 1
            else:
                break
        if j - i >= 3:
            monotonous_runs.append(dict(
                shots=[s['id'] for s in t_shots[i:j]],
                start_frame=t_shots[i]['start_frame'],
                end_frame=t_shots[j - 1]['start_frame'] + t_shots[j - 1]['frames'],
                median_seconds=round(median(durations[i:j]), 2),
            ))
            i = j
        else:
            i += 1
    if monotonous_runs:
        for run in monotonous_runs:
            suggestions.append(
                f"镜头 {' → '.join(run['shots'])} 连续 {len(run['shots'])} 镜近乎等长（约 {run['median_seconds']}s），"
                '容易形成节拍器单调感，建议按信息密度拉开快慢对比。'
            )

    long_static_shots = []
    if edit is not None:
        for t_shot in t_shots:
            shot = e_shots.get(t_shot['id'], {})
            dur = t_shot['frames'] / fps
            is_still = Path(shot.get('visual', '')).suffix.lower() in STILLS
            if is_still and dur >= 4.0 and not any(k in shot for k in DYNAMIC_SHOT_KEYS):
                long_static_shots.append(dict(
                    shot=t_shot['id'], start_frame=t_shot['start_frame'],
                    end_frame=t_shot['start_frame'] + t_shot['frames'], seconds=round(dur, 2),
                ))
        if long_static_shots:
            names = '、'.join(f"{r['shot']}({r['seconds']}s)" for r in long_static_shots)
            suggestions.append(f'静图镜头 {names} 停留较长且无 camera/图层动效，建议补充定点推近、局部聚光或拆分证据镜头。')

    cadence = dict(
        shot_count=len(t_shots),
        min_seconds=round(min(durations), 2) if durations else 0.0,
        median_seconds=round(median(durations), 2) if durations else 0.0,
        max_seconds=round(max(durations), 2) if durations else 0.0,
        duration_cv=cv,
        monotonous_runs=monotonous_runs,
        long_static_shots=long_static_shots,
    )

    # 3. Transition Hygiene
    edge_names = []
    if edit is not None:
        edge_names = [_transition_name(e_shots.get(s['id'], {})) for s in t_shots[1:]]
    elif bridges is not None:
        by_shot = {b['shot']: b['preset'] for b in bridges}
        edge_names = [by_shot.get(s['id'], 'cut') for s in t_shots[1:]]
    non_cuts = [n for n in edge_names if n != 'cut']
    dup_transitions = []
    for idx in range(1, len(edge_names)):
        if edge_names[idx] != 'cut' and edge_names[idx] == edge_names[idx - 1]:
            dup_transitions.append(dict(
                preset=edge_names[idx],
                shots=[t_shots[idx]['id'], t_shots[idx + 1]['id']],
            ))
    non_cut_ratio = round(len(non_cuts) / len(edge_names), 3) if edge_names else 0.0
    if dup_transitions:
        for d in dup_transitions:
            suggestions.append(f"相邻切点重复使用同一转场 {d['preset']}（{' / '.join(d['shots'])}），建议交错硬切或改用不同空间桥。")
    if len(edge_names) >= 4 and non_cut_ratio > 0.65:
        suggestions.append(f'非硬切转场占比达 {non_cut_ratio:.0%}，建议把大部分同段推进还给干脆硬切，只在话题/空间转换处用桥接转场。')

    transition_hygiene = dict(
        total_boundaries=len(edge_names),
        non_cut_count=len(non_cuts),
        non_cut_ratio=non_cut_ratio,
        adjacent_duplicate_transitions=dup_transitions,
    )

    # 4. Narration Breath & Pause Rhythm
    tight_numeric_pauses = []
    breathless_runs = []
    ending_tail_s = None
    if takes and all(k in takes[0] for k in ('start_sample', 'end_sample', 'sample_rate')):
        spans = [(t['start_sample'] / t['sample_rate'], t['end_sample'] / t['sample_rate'], t) for t in takes]
        ending_tail_s = round(max(0.0, total_s - spans[-1][1]), 3)
        run_start = spans[0][0]
        for idx, (a, b, take) in enumerate(spans):
            nxt_a = spans[idx + 1][0] if idx + 1 < len(spans) else total_s
            gap = nxt_a - b
            spoken = take.get('spoken', '')
            if idx + 1 < len(spans) and gap < 0.14 and SPOKEN_FIGURE.search(RELATIVE_YEARS.sub(' ', spoken)):
                tight_numeric_pauses.append(dict(
                    shot=take['shot'], text=spoken, gap_seconds=round(gap, 3),
                ))
            if idx + 1 == len(spans) or gap >= 0.25:
                if b - run_start > 12.0:
                    breathless_runs.append(dict(
                        start_seconds=round(run_start, 2), end_seconds=round(b, 2),
                        duration_seconds=round(b - run_start, 2),
                    ))
                if idx + 1 < len(spans):
                    run_start = nxt_a
    if tight_numeric_pauses:
        suggestions.append(f'共有 {len(tight_numeric_pauses)} 处含关键数值的句子停顿不足 0.14s，建议适当加大 line.pause 留出消化时间。')
    if breathless_runs:
        suggestions.append(f'发现 {len(breathless_runs)} 段超过 12 秒无明显气口（>=0.25s）的连续口播，建议在转折或揭示后留出呼吸空拍。')
    if ending_tail_s is not None and ending_tail_s < 0.35:
        suggestions.append(f'末句结束后余量仅 {ending_tail_s:.2f}s，建议将末镜 tail 增至 0.45–0.80s，避免收尾突兀切断。')

    narration_rhythm = dict(
        tight_numeric_pauses=tight_numeric_pauses,
        breathless_runs=breathless_runs,
        ending_tail_seconds=ending_tail_s,
    )

    # 5. Section Pacing Contrast
    section_contrast = None
    segs = (sections or {}).get('segments', []) if isinstance(sections, dict) else []
    named_segs = [s for s in segs if s.get('section') and s.get('median_cut_seconds')]
    if len(named_segs) >= 2:
        meds = [s['median_cut_seconds'] for s in named_segs]
        fastest = min(named_segs, key=lambda s: s['median_cut_seconds'])
        slowest = max(named_segs, key=lambda s: s['median_cut_seconds'])
        ratio = round(slowest['median_cut_seconds'] / max(0.01, fastest['median_cut_seconds']), 2)
        section_contrast = dict(
            section_count=len(named_segs),
            fastest_section=fastest['section'], fastest_median_seconds=fastest['median_cut_seconds'],
            slowest_section=slowest['section'], slowest_median_seconds=slowest['median_cut_seconds'],
            pace_contrast_ratio=ratio,
        )
        if ratio < 1.25:
            suggestions.append(f'各段落中位镜长对比仅 {ratio}x（最快 {fastest["section"]} vs 最慢 {slowest["section"]}），可进一步拉开铺垫与揭示的快慢张力。')

    # 5b. Anti-PPT Dynamic Visuals & Video B-Roll Coverage Audit
    visual_dynamism = None
    if edit is not None and t_shots:
        live_s = video_bg_s = generated_s = slide_s = 0.0
        opaque_run = max_opaque_run = 0
        overlong_slides = []
        stalled_text_cards = []
        shot_kinds = []
        for t_shot in t_shots:
            sid = t_shot['id']
            shot = e_shots.get(sid, {})
            dur = t_shot['frames'] / fps
            kind = _shot_visual_kind(shot)
            shot_kinds.append(dict(shot=sid, kind=kind, seconds=round(dur, 2)))
            if kind == 'live_video':
                live_s += dur
                opaque_run = 0
            elif kind == 'generated_motion':
                generated_s += dur
                opaque_run = 0
            elif kind == 'motion_with_video_bg':
                video_bg_s += dur
                opaque_run = 0
            else:
                slide_s += dur
                opaque_run += 1
                max_opaque_run = max(max_opaque_run, opaque_run)
                rem = shot.get('remotion_effect') or {}
                eff = rem.get('effect')
                if eff in ONE_BEAT_TEXT_EFFECTS and dur > 6.5:
                    stalled_text_cards.append(dict(shot=sid, effect=eff, seconds=round(dur, 2)))
                elif dur > 10.0:
                    overlong_slides.append(dict(shot=sid, seconds=round(dur, 2)))
        video_backed_ratio = round((live_s + video_bg_s) / max(0.01, total_s), 3)
        opaque_slide_ratio = round(slide_s / max(0.01, total_s), 3)
        visual_dynamism = dict(
            generated_motion_seconds=round(generated_s, 2),
            live_video_seconds=round(live_s, 2),
            motion_with_video_bg_seconds=round(video_bg_s, 2),
            opaque_slide_seconds=round(slide_s, 2),
            video_backed_ratio=video_backed_ratio,
            opaque_slide_ratio=opaque_slide_ratio,
            max_consecutive_opaque_slides=max_opaque_run,
            stalled_text_cards=stalled_text_cards,
            overlong_slides=overlong_slides,
        )
        if cadence['median_seconds'] > 8.5:
            suggestions.append(
                f"全片中位镜长达 {cadence['median_seconds']}s（超过 8.5s 反 PPT 阈值），整体易显幻灯片感；建议把长段落拆成 4–7s 短镜并穿插实拍 B-roll 或景别切换。"
            )
        if video_backed_ratio < 0.35:
            suggestions.append(
                f"动态视频素材/视频背景仅占全片 {video_backed_ratio:.0%}（纯图卡/静态底板占 {opaque_slide_ratio:.0%}），观感易像 PPT；建议先用 library.py 与 media.py search-video 补齐实拍 B-roll，并为数据/引语/结尾卡配置 remotion_effect.background 动态视频背景。"
            )
        if max_opaque_run >= 3:
            suggestions.append(
                f"存在连续 {max_opaque_run} 镜纯色图卡/无视频背景镜头连播，建议每 1–2 镜穿插实拍 B-roll、档案视频或视频铺底图卡作为视觉节奏打断。"
            )
        if stalled_text_cards:
            names = '、'.join(f"{r['shot']}({r['effect']}, {r['seconds']}s)" for r in stalled_text_cards)
            suggestions.append(
                f"单拍文字卡 {names} 停留超过 6.5s 且无动态视频背景（进场不到 1 秒后即长时间僵停），建议压短至 4–6s、拆分出 B-roll 切镜，或为 remotion_effect 添加 background 视频铺底。"
            )
        if overlong_slides:
            names = '、'.join(f"{r['shot']}({r['seconds']}s)" for r in overlong_slides)
            suggestions.append(
                f"非实拍图卡镜头 {names} 单镜超过 10s，建议拆镜、加实拍 B-roll 切入，或改为多阶段视觉演进（每 3–5s 有新视觉事件）。"
            )

    # 5c. First-30s Hook Retention & In-Media-Res Pattern-Interrupt Audit
    hook_30s = None
    if edit is not None and t_shots:
        win_s = min(30.0, total_s)
        win_frames = round(win_s * fps)
        cuts_30s = [s for s in t_shots[1:] if s['start_frame'] < win_frames]
        first_cut_s = round(t_shots[1]['start_frame'] / fps, 2) if len(t_shots) > 1 else round(total_s, 2)
        v_backed_30 = 0.0
        max_30_dur = 0.0
        max_30_shot = None
        for t_shot in t_shots:
            s0 = t_shot['start_frame']
            s1 = s0 + t_shot['frames']
            ov = max(0, min(s1, win_frames) - max(s0, 0))
            if ov <= 0:
                continue
            ov_s = ov / fps
            shot = e_shots.get(t_shot['id'], {})
            if _shot_visual_kind(shot) in ('live_video', 'motion_with_video_bg'):
                v_backed_30 += ov_s
            dur_s = t_shot['frames'] / fps
            if dur_s > max_30_dur:
                max_30_dur = dur_s
                max_30_shot = t_shot['id']
        v_ratio_30 = round(v_backed_30 / max(0.01, win_s), 3)
        rehook_15_35 = False
        prev_kind = None
        for t_shot in t_shots:
            t_s = t_shot['start_frame'] / fps
            shot = e_shots.get(t_shot['id'], {})
            kind = _shot_visual_kind(shot)
            tname = _transition_name(shot)
            if 15.0 <= t_s <= 35.0:
                if (shot.get('rehook') or shot.get('chapter')
                        or tname in ('match-cut', 'dip-ink', 'dip-paper', 'desk-rise', 'whip-left', 'whip-right')
                        or (prev_kind is not None and prev_kind != kind)):
                    rehook_15_35 = True
            prev_kind = kind
        cap_style = edit.get('caption_style', 'plate')
        hook_30s = dict(
            window_seconds=round(win_s, 2),
            first_cut_seconds=first_cut_s,
            cuts_in_first_30s=len(cuts_30s),
            video_backed_ratio_first_30s=v_ratio_30,
            longest_shot_in_first_30s=dict(shot=max_30_shot, seconds=round(max_30_dur, 2)),
            rehook_or_pattern_interrupt_15_35s=rehook_15_35 if total_s >= 35.0 else None,
            caption_style=cap_style,
            kinetic_hook_captions=cap_style in ('kinetic-word', 'adaptive-kinetic') or any(
                e_shots.get(s['id'], {}).get('caption_style') == 'kinetic-word'
                for s in t_shots if s['start_frame'] < round(6.0 * fps)
            ),
        )
        if total_s >= 25.0 and len(cuts_30s) < 3:
            suggestions.append(
                f"前 30 秒仅有 {len(cuts_30s)} 次换镜（首切在 {first_cut_s}s），黄金前 30 秒节奏偏慢；建议采用 In-Media-Res 开场（0–2s 动态实拍冲击 + 2–5s 核心结论切入），前 30 秒保持 4–6 次有效视觉切换。"
            )
        if total_s >= 15.0 and v_ratio_30 < 0.40:
            suggestions.append(
                f"前 30 秒动态视频素材/视频铺底占比仅 {v_ratio_30:.0%}，建议开场优先上高质感实拍 B-roll 或实拍铺底双层弹窗（broll-evidence-pop / kinetic-hook + background）。"
            )
        if total_s >= 40.0 and not rehook_15_35:
            suggestions.append(
                '15–35 秒区间缺少明显视觉节奏打断或二次钩子（Re-hook），建议在此区间安排一次景别/媒介切换、match-cut 或 sub-bass riser + 0.3s 刻意静音断拍重置观众注意力。'
            )

    # 5d. Optional Beat-Grid Cut Alignment Audit
    beat_alignment = None
    if isinstance(beat_grid, dict) and beat_grid.get('beats') and len(t_shots) > 1:
        beat_frames = [int(b['frame']) for b in beat_grid['beats']]
        downbeat_frames = [int(b['frame']) for b in beat_grid['beats'] if b.get('is_downbeat')]
        tol = max(2, round(0.10 * fps))  # ±3 frames at 30fps
        aligned_cuts = []
        offbeat_cuts = []
        for t_shot in t_shots[1:]:
            cf = t_shot['start_frame']
            nearest = min(beat_frames, key=lambda bf: abs(bf - cf))
            delta = cf - nearest
            on_downbeat = bool(downbeat_frames) and min(abs(df - cf) for df in downbeat_frames) <= tol
            rec = dict(shot=t_shot['id'], cut_frame=cf, nearest_beat_frame=nearest, delta_frames=delta, on_downbeat=on_downbeat)
            if abs(delta) <= tol:
                aligned_cuts.append(rec)
            else:
                offbeat_cuts.append(rec)
        ratio = round(len(aligned_cuts) / max(1, len(t_shots) - 1), 3)
        beat_alignment = dict(
            tolerance_frames=tol,
            total_cuts=len(t_shots) - 1,
            beat_aligned_cuts=len(aligned_cuts),
            alignment_ratio=ratio,
            downbeat_cuts=sum(1 for c in aligned_cuts if c['on_downbeat']),
            offbeat_examples=offbeat_cuts[:6],
        )

    visual_diversity = (measure_visual_diversity(timeline, edit) if edit is not None and t_shots
                        else timeline.get('visual_diversity'))
    if visual_diversity:
        for st in visual_diversity['same_medium_streaks']:
            suggestions.append(
                f"视觉媒介单调：镜头 {' → '.join(st['shots'])} 连续 {st['count']} 镜均为 {st['medium']}，"
                '建议穿插实拍 B-roll、2.5D 视差、地图或数据演进打破同类连播。'
            )
        dead_zones = visual_diversity['dead_zones_over_8s']
        if dead_zones:
            names = '、'.join(f"{d['shot']}({d['seconds']}s)" for d in dead_zones)
            suggestions.append(f"超 8 秒视觉信息死区 {names}（缺少中段动态事件或镜头推进），建议拆镜、加局部动画或切入 B-roll。")

    # 6. Polyphonic / ZipVoice Lexicon Audit
    pronunciation = None
    if edit is not None:
        from pronunciation_review import audit_edit as audit_pronunciation
        pronunciation = audit_pronunciation(edit, timeline)
        for row in pronunciation.get('unresolved_lines', []):
            for risk in row['risks']:
                tag = '（已绑定 line.audio 绕过定向词表）' if row.get('audio_bypass') else ''
                suggestions.append(
                    f"多音字风险 {row['shot']}/{row['line_id']}{tag}：{risk['detail']}"
                    + (f"；建议：{risk['suggestion']}" if risk.get('suggestion') else '')
                )

    # 7. Micro-Story Loop, Spoken-Word Cliché & Packaging Payoff Audit
    story = None
    if edit is not None:
        from story_craft import analyze_script
        story = analyze_script(edit, timeline, cover_cfg=cover_cfg, packaging_cfg=packaging_cfg)
        for s_msg in story.get('suggestions', []):
            suggestions.append(s_msg)

    return dict(
        schema=1,
        programme_seconds=round(total_s, 3),
        hook=hook,
        hook_30s=hook_30s,
        cut_cadence=cadence,
        transition_hygiene=transition_hygiene,
        narration_rhythm=narration_rhythm,
        section_contrast=section_contrast,
        visual_dynamism=visual_dynamism,
        visual_diversity=visual_diversity,
        beat_alignment=beat_alignment,
        pronunciation=pronunciation,
        story_craft=story,
        suggestions=suggestions,
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project', type=Path, default=Path('.'))
    p.add_argument('--beats', type=Path, help='Optional beat-grid JSON produced by cue_score.py --beats-out')
    args = p.parse_args()
    edit_path = args.project / 'edit.json'
    tl_path = args.project / 'timeline.json'
    beats_path = args.beats or (args.project / 'qa' / 'beat-grid.json')
    cov_path = args.project / 'cover.json'
    pkg_path = args.project / 'packaging.json'
    edit = json.loads(edit_path.read_text()) if edit_path.is_file() else None
    timeline = json.loads(tl_path.read_text())
    bg = json.loads(beats_path.read_text()) if beats_path.is_file() else None
    cov = json.loads(cov_path.read_text()) if cov_path.is_file() else None
    pkg = json.loads(pkg_path.read_text()) if pkg_path.is_file() else None
    report = audit(timeline, edit, beat_grid=bg, cover_cfg=cov, packaging_cfg=pkg)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
