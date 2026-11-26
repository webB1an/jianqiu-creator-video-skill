"""Propose and generate a restrained, transition/reveal-synced sfx.json from edit.json + timeline.json."""
import argparse
import json
from pathlib import Path

from design_sound import DEFAULTS, create as create_sound
from production_run import digest

FOLEY_BANK = Path(__file__).resolve().parents[1] / 'assets/sfx'
# Verified CC0 1.0 real recorded Foley takes preferred over synthetic hits where appropriate.
FOLEY_BANK_MAP = {
    'swipe': ('paper-464302', [0.05, 0.55], 0.22),
    'pop': ('wood-tap-cc0', None, None),
    'snap': ('click-rim-cc0', None, None),
    'tick': ('click-rim-cc0', None, None),
    'chime': ('bell-chime-cc0', None, None),
    'tension': ('cymbal-swell-cc0', None, None),
    'shutter': ('shutter-520684', [0.0, 0.55], 0.08),
    'dial': ('dial-629301', [0.0, 0.35], 0.06),
}


def _resolve_foley_take(kind):
    spec = FOLEY_BANK_MAP.get(kind)
    if not spec:
        return None
    key, trim, anchor = spec
    folder = FOLEY_BANK / key
    try:
        meta = json.loads((folder / 'source.json').read_text())
        if 'creativecommons.org/publicdomain/zero/1.0' not in meta['license']:
            return None
        if digest(folder / 'take.wav') != meta['wav_sha256']:
            return None
        if trim is None:
            trim, anchor = [0, meta['duration']], meta['anchor']
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return dict(key=key, trim=list(trim), anchor=anchor, foley=True)


TRANSITION_MAP = {
    'whip-left': ('whoosh', -19.0, -0.18, '左推转场气流', 3),
    'whip-right': ('whoosh', -19.0, 0.18, '右推转场气流', 3),
    'whip-up': ('whoosh', -19.5, 0.0, '上推转场气流', 3),
    'whip-down': ('whoosh', -19.5, 0.0, '下推转场气流', 3),
    'push-left': ('pass', -20.5, -0.15, '横推换场', 2),
    'push-up': ('pass', -20.5, 0.0, '上推换场', 2),
    'push-through': ('whoosh', -18.5, 0.0, '推穿入镜', 3),
    'pull-back': ('whoosh', -19.5, 0.0, '拉远退镜', 2),
    'match-cut': ('whoosh', -18.5, 0.0, '同坐标几何匹配剪辑', 3),
    'desk-rise': ('sub_drop', -18.0, 0.0, '原件倒桌与图表立起', 2),
    'page': ('swipe', -21.0, 0.0, '纸页翻入', 2),
    'glide': ('swipe', -21.0, 0.12, '横向移读', 2),
    'dip-ink': ('tension', -21.0, 0.0, '墨色换章停顿', 2),
    'dip-paper': ('tension', -21.5, 0.0, '纸色换章停顿', 2),
    'iris-open': ('pop', -20.0, 0.0, '光窗开幅', 2),
}
REHOOK_TRANSITIONS = {'dip-ink', 'dip-paper', 'desk-rise', 'match-cut'}


def _ensure_take(project, kind, key):
    folder = project / 'assets' / 'sfx' / key
    meta_path = folder / 'source.json'
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text())
        return dict(key=key, trim=[0, meta['seconds']], anchor=meta['anchor'])
    return create_sound(project, key, kind, DEFAULTS[kind], seed=7)


def plan_events(edit, timeline, *, min_gap=0.70, lead_frames=2, max_per_minute=9.0, include_rehooks=True, foley_takes=None):
    """Scan transitions, Re-hooks, evidence windows and Remotion cues for psychoacoustically-timed beats.

    Transition SFX pre-trigger by 2–3 frames (`shift_frames: -2/-3`) to create auditory anticipation,
    Re-hook chapter breaks receive a sub-bass riser + recommended 0.30s micro-silence cue, and total
    SFX density is capped (`max_per_minute`) to prevent listener fatigue.
    """
    foley_takes = foley_takes or {}
    fps = timeline['fps']
    total_s = timeline['frames'] / fps
    schedule = {s['id']: s for s in timeline['shots']}
    proposals = []
    recommended_silences = []
    last_riser_s = -99.0

    for idx, shot in enumerate(edit['shots']):
        sid = shot['id']
        timing = schedule[sid]
        shot_start_s = timing['start_frame'] / fps
        shot_dur_s = timing['frames'] / fps

        # 1. Incoming picture bridge (transitions) with psychoacoustic pre-trigger lead.
        trans = shot.get('transition', 'cut')
        tname = trans if isinstance(trans, str) else (trans.get('name', 'cut') if isinstance(trans, dict) else 'cut')
        is_rehook = bool(shot.get('rehook')) or (idx > 0 and shot_start_s >= 12.0 and (bool(shot.get('chapter')) or tname in REHOOK_TRANSITIONS))

        if include_rehooks and is_rehook and shot_start_s >= 1.45 and (shot_start_s - last_riser_s) >= 10.0:
            riser_shift = -max(2, lead_frames + 1) if lead_frames > 0 else 0
            riser_kind = 'tension' if shot.get('chapter') or tname in {'dip-ink', 'dip-paper'} else 'sub_riser'
            riser_label = '结构转折吊镲蓄势' if riser_kind == 'tension' else '结构转折低频蓄势'
            proposals.append(dict(
                prog_s=shot_start_s + riser_shift / fps, shot=sid, offset=0.0,
                shift_frames=riser_shift, kind=riser_kind,
                peak_db=-19.5, pan=0.0,
                event=f'{riser_label}（{sid}）', priority=0,
            ))
            recommended_silences.append(dict(
                shot=sid, offset=0.0, shift_frames=-min(2, max(0, lead_frames)),
                micro_silence=0.30, floor=0.02, recover=1.0, fade_down=0.05, fade_up=0.06,
                reason=f'Re-hook / 章节转折前 0.30s 刻意静音断拍（{sid}）',
            ))
            last_riser_s = shot_start_s
        elif idx > 0 and tname in TRANSITION_MAP:
            kind, db, pan, label, rec_lead = TRANSITION_MAP[tname]
            shift = -rec_lead if lead_frames == 2 else (-lead_frames if lead_frames > 0 else 0)
            if shot_start_s + shift / fps < DEFAULTS[kind] * 0.6:
                shift = 0
            proposals.append(dict(
                prog_s=shot_start_s + shift / fps, shot=sid, offset=0.0,
                shift_frames=shift, kind=kind, peak_db=db, pan=pan,
                event=f'{label}（{sid}）', priority=1,
            ))

        # 2. Evidence window open.
        ew = shot.get('evidence_window')
        if isinstance(ew, dict) and isinstance(ew.get('open'), list) and len(ew['open']) == 2:
            open_off = ew['open'][0] / fps
            proposals.append(dict(
                prog_s=shot_start_s + open_off, shot=sid, offset=round(open_off, 4),
                shift_frames=-1 if lead_frames > 0 and shot_start_s + open_off > 0.25 else 0,
                kind='pop', peak_db=-19.5, pan=0.0, event=f'展开证据窗（{sid}）', priority=2,
            ))

        # 3. Value comparison gap reveal.
        vc = shot.get('value_comparison')
        if isinstance(vc, dict) and isinstance(vc.get('gap'), list) and len(vc['gap']) == 2 and type(vc['gap'][0]) is int:
            gap_off = vc['gap'][0] / fps
            proposals.append(dict(
                prog_s=shot_start_s + gap_off, shot=sid, offset=round(gap_off, 4), shift_frames=0,
                kind='chime', peak_db=-20.0, pan=0.0, event=f'差值括线揭示（{sid}）', priority=2,
            ))

        # 4. Remotion graphic settle, popup, explosion, sticker or match-cut arrival.
        re_spec = shot.get('remotion_effect')
        if isinstance(re_spec, dict):
            eff = re_spec.get('effect')
            params = re_spec.get('params', {})
            if eff in ('stat-counter', 'bar-chart', 'metric-grid') and 'settleFrame' in params and type(params['settleFrame']) is int:
                off = params['settleFrame'] / fps
                if 0.05 <= off < shot_dur_s - 0.15:
                    proposals.append(dict(
                        prog_s=shot_start_s + off, shot=sid, offset=round(off, 4), shift_frames=0,
                        kind='land', peak_db=-20.5, pan=0.0, event=f'数据落定（{sid}）', priority=3,
                    ))
            elif eff == 'callout-annotation':
                sf = params.get('startFrame', 0)
                if type(sf) is int and 0 <= sf < timing['frames']:
                    off = sf / fps
                    proposals.append(dict(
                        prog_s=shot_start_s + off, shot=sid, offset=round(off, 4), shift_frames=0,
                        kind='pop', peak_db=-20.5, pan=0.0, event=f'标注抵达（{sid}）', priority=3,
                    ))
            elif eff == 'sticker-callout':
                sf = params.get('startFrame', 0)
                if type(sf) is int and 0 <= sf < timing['frames']:
                    off = sf / fps
                    proposals.append(dict(
                        prog_s=shot_start_s + off, shot=sid, offset=round(off, 4), shift_frames=0,
                        kind='snap', peak_db=-20.0, pan=0.0, event=f'贴纸弹入（{sid}）', priority=3,
                    ))
            elif eff == 'broll-evidence-pop':
                popups = params.get('popups') if isinstance(params.get('popups'), list) else [{'enterFrame': params.get('enterFrame', round(fps * 0.6))}]
                for p_idx, pop in enumerate(popups):
                    ef = pop.get('enterFrame', round(fps * 0.6))
                    if type(ef) is int and 0 <= ef < timing['frames']:
                        off = ef / fps
                        proposals.append(dict(
                            prog_s=shot_start_s + off, shot=sid, offset=round(off, 4),
                            shift_frames=-1 if lead_frames > 0 and shot_start_s + off > 0.25 else 0,
                            kind='pop', peak_db=-19.5, pan=0.0, event=f'实拍证据弹窗 #{p_idx + 1}（{sid}）', priority=2,
                        ))
            elif eff == 'exploded-diagram':
                ef = params.get('explodeFrame', params.get('startFrame', 0))
                if type(ef) is int and 0 <= ef < timing['frames']:
                    off = ef / fps
                    proposals.append(dict(
                        prog_s=shot_start_s + off, shot=sid, offset=round(off, 4), shift_frames=0,
                        kind='swipe', peak_db=-20.0, pan=0.0, event=f'结构层爆炸展开（{sid}）', priority=2,
                    ))
            elif eff == 'match-cut-ring':
                af = params.get('atFrame', timing['frames'] // 2)
                if type(af) is int and 0 <= af < timing['frames']:
                    off = af / fps
                    proposals.append(dict(
                        prog_s=shot_start_s + off, shot=sid, offset=round(off, 4),
                        shift_frames=-min(3, max(0, lead_frames)), kind='whoosh',
                        peak_db=-18.5, pan=0.0, event=f'几何圆环匹配转场（{sid}）', priority=1,
                    ))
            elif eff == 'text-highlight':
                segs = params.get('segments', [])
                for seg in segs:
                    if isinstance(seg, dict) and type(seg.get('at')) is int:
                        off = seg['at'] / fps
                        proposals.append(dict(
                            prog_s=shot_start_s + off, shot=sid, offset=round(off, 4), shift_frames=0,
                            kind='swipe', peak_db=-22.0, pan=0.0, event=f'原句划亮（{sid}）', priority=3,
                        ))
                        break

    # Keep sound design restrained: sort by time, enforce min_gap, avoid timeline boundary overflows.
    proposals.sort(key=lambda r: (r['prog_s'], r['priority']))
    kept = []
    for row in proposals:
        dur = DEFAULTS[row['kind']]
        if take := foley_takes.get(row['kind']):
            start, end = take['trim']
            head_need, tail_need = (take['anchor'] - start) / .96, (end - take['anchor']) / .96
        else:
            is_riser = row['kind'] in ('sub_riser', 'tension')
            head_need = dur * (0.96 if is_riser else 0.55)
            tail_need = dur * (0.10 if is_riser else 0.65)
        if row['prog_s'] < head_need or row['prog_s'] > total_s - tail_need:
            continue
        if kept and row['prog_s'] - kept[-1]['prog_s'] < min_gap:
            if row['priority'] < kept[-1]['priority']:
                kept[-1] = row
            continue
        kept.append(row)

    # Anti-fatigue cap: keep highest-priority hits within max_per_minute budget.
    if max_per_minute and max_per_minute > 0 and total_s > 0:
        budget = max(2, round(max_per_minute * total_s / 60.0))
        if len(kept) > budget:
            ranked = sorted(range(len(kept)), key=lambda i: (kept[i]['priority'], kept[i]['prog_s']))[:budget]
            kept = [kept[i] for i in sorted(ranked)]
    return kept, recommended_silences


def _merge_micro_silences(edit, timeline, recommended_silences):
    """Safely merge non-overlapping micro_silence cues into edit.music_cues when an audible music track exists."""
    if not recommended_silences or not edit.get('music') or edit.get('music_gain', 1) == 0:
        return 0
    from music_direction import resolve_cues
    existing = list(edit.get('music_cues') or [])
    existing_shots = {c.get('shot') for c in existing if isinstance(c, dict) and 'micro_silence' in c}
    added = 0
    for rec in recommended_silences:
        if rec['shot'] in existing_shots:
            continue
        candidate = {k: v for k, v in rec.items() if k != 'reason'}
        candidate['event'] = rec.get('reason', f"Re-hook 静音断拍（{rec['shot']}）")
        trial = dict(edit, music_cues=[*existing, candidate])
        try:
            resolve_cues(trial, timeline)
        except ValueError:
            continue
        existing.append(candidate)
        existing_shots.add(rec['shot'])
        added += 1
    if added:
        edit['music_cues'] = existing
    return added


def scaffold(project, *, write=False, apply_edit=False, min_gap=0.70, lead_frames=2, max_per_minute=9.0,
             rehooks_out=None, prefer_foley=True):
    edit_path = project / 'edit.json'
    edit = json.loads(edit_path.read_text())
    timeline = json.loads((project / 'timeline.json').read_text())
    foley_takes = {kind: _resolve_foley_take(kind) for kind in FOLEY_BANK_MAP} if prefer_foley else {}
    proposals, recommended_silences = plan_events(
        edit, timeline, min_gap=min_gap, lead_frames=lead_frames, max_per_minute=max_per_minute, foley_takes=foley_takes,
    )
    events = []
    # Micro pitch variation across repeated uses of the same kind avoids mechanical repetition.
    counts = {}
    for row in proposals:
        kind = row['kind']
        idx = counts.get(kind, 0)
        counts[kind] = idx + 1
        rate = round(1.0 + ((idx % 3) - 1) * 0.04, 3) if idx > 0 and kind in ('pop', 'whoosh', 'swipe', 'snap') else 1.0
        foley = foley_takes.get(kind)
        if foley is not None:
            take = foley
        else:
            key = f'auto-{kind}'
            if write or apply_edit:
                take = _ensure_take(project, kind, key)
            else:
                default_anchor = round(DEFAULTS[kind] * (0.93 if kind == 'sub_riser' else 0.45), 4)
                take = dict(key=key, trim=[0, DEFAULTS[kind]], anchor=default_anchor)
        ev = dict(
            shot=row['shot'], offset=row['offset'], key=take['key'],
            trim=take['trim'], anchor=take['anchor'], peak_db=row['peak_db'],
            pan=row['pan'], event=row['event'],
        )
        if row.get('shift_frames'):
            ev['shift_frames'] = row['shift_frames']
        if rate != 1.0:
            ev['rate'] = rate
        events.append(ev)
    if write or apply_edit:
        (project / 'sfx.json').write_text(json.dumps(events, ensure_ascii=False, indent=2) + '\n')
    applied_silences = 0
    if apply_edit:
        if events and not edit.get('effects'):
            edit['sfx_plan'] = 'sfx.json'
            edit.setdefault('effects_gain', 1.0)
        applied_silences = _merge_micro_silences(edit, timeline, recommended_silences)
        edit_path.write_text(json.dumps(edit, ensure_ascii=False, indent=2) + '\n')
    if rehooks_out:
        rehooks_out.parent.mkdir(parents=True, exist_ok=True)
        rehooks_out.write_text(json.dumps(recommended_silences, ensure_ascii=False, indent=2) + '\n')
    return events, recommended_silences, applied_silences


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--project', type=Path, default=Path('.'))
    p.add_argument('--min-gap', type=float, default=0.70, help='Minimum spacing between auto-placed hits in seconds')
    p.add_argument('--lead-frames', type=int, default=2, help='Psychoacoustic pre-trigger lead frames for transition SFX (default: 2)')
    p.add_argument('--max-per-minute', type=float, default=9.0, help='Anti-fatigue SFX density ceiling per minute (default: 9.0)')
    p.add_argument('--rehooks-out', type=Path, help='Optional JSON path to save recommended micro_silence music_cues for Re-hooks')
    p.add_argument('--synth-only', action='store_true', help='Use design_sound.py synthesis instead of bundled CC0 Foley recordings')
    p.add_argument('--write', action='store_true', help='Generate local assets/sfx takes and write sfx.json')
    p.add_argument('--apply-edit', action='store_true', help='Also register sfx_plan and merge non-overlapping micro_silence cues into edit.json')
    args = p.parse_args()
    events, silences, applied_silences = scaffold(
        args.project, write=args.write, apply_edit=args.apply_edit, min_gap=args.min_gap,
        lead_frames=args.lead_frames, max_per_minute=args.max_per_minute,
        rehooks_out=args.rehooks_out, prefer_foley=not args.synth_only,
    )
    print(json.dumps({'events': events, 'recommended_micro_silences': silences, 'applied_micro_silences': applied_silences}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
