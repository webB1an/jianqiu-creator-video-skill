"""Determine which perceptual spot checks belong to a cut and validate the recorded evidence."""
import alignment_review
import json
import math
from pathlib import Path
import re

from picture_roughcut import probe
from production_run import digest

KINDS = ('music', 'effects', 'motion', 'opening_picture', 'caption_overlay', 'alignment')
MOVEMENT = re.compile(r'推近|拉远|拉近|缩放|放大|拖选|拖过|选区|鼠标|指针|移动|滚动|平移|靠近|滑动|推进|zoom|drag|cursor|scroll|camera pan|push.in', re.I)
STILL = re.compile(r'静帧|静止|静态|固定截图|停留|保持不动|静置|static|still image|read hold', re.I)
LABELS = {'music': '配乐可辨性', 'effects': '动作或环境声可辨性', 'motion': '运动连续性', 'opening_picture':'前五秒可见变化', 'caption_overlay':'字幕叠加范围','alignment':'关联元素对齐'}


def expected_audio(edit):
    narrated = any(shot.get('lines') for shot in edit.get('shots', []))
    requested = edit.get('audio_intent', ['music','effects'] if narrated else [])
    if not isinstance(requested,list) or any(k not in ('music','effects') for k in requested):
        raise ValueError('audio_intent must list the requested music and/or effects')
    return requested


def timed_layout_scope(directory, declared=(), rhythm=(), *, media=None, timeline_sha256=None, viewing=()):
    """Recover all frozen cut starts; the matching timeline or the exact media supplies the final edge."""
    timeline_path = Path(directory) / 'timeline.json'
    read_timeline = timeline_path.is_file() and (media is None or
                    timeline_sha256 and digest(timeline_path) == timeline_sha256)
    timing = json.loads(timeline_path.read_text()) if read_timeline else {}
    total = timing.get('frames')
    if (type(total) is not int or total < 1) and media is not None and media.is_file():
        count = probe(media, 'nb_frames').get('nb_frames')
        if not str(count).isdigit():
            count = probe(media, 'nb_read_frames', count=True).get('nb_read_frames')
        if not str(count).isdigit() or int(count) < 1:
            raise ValueError(f'Cannot establish the frame count of the reviewed picture: {media}')
        total = int(count)
    if not isinstance(total, int) or total < 1:
        return [dict(t, inventory=True, components=t.get('components', [])) for t in declared]
    names = {s['start_frame']: s.get('id', 'picture') for s in timing.get('shots', [])
             if type(s.get('start_frame')) is int}
    boundaries = {0, total}
    boundaries.update(r['start'] for r in rhythm if type(r.get('start')) is int
                      and not any(t['start'] < r['start'] < t['end'] for t in declared))
    for cut in declared:
        boundaries.update([cut['start'], cut['end']])
    # A frozen cut may span more than one voice shot. Prefer its authored picture boundaries.
    if not rhythm:
        boundaries.update(f for f in names if not any(t['start'] < f < t['end'] for t in declared))
    edges = sorted(f for f in boundaries if 0 <= f <= total)
    registered = {(t['start'], t['end']): t for t in declared}
    cuts = []
    for start, end in zip(edges, edges[1:]):
        cut = registered.get((start, end), {})
        title = next((w.get('subject') for w in viewing if start <= w['start'] < w['end'] <= end), None)
        cuts.append(dict(start=start, end=end, subject=cut.get('shot') or title or names.get(start, 'picture'),
                         alignment_groups=cut.get('groups', []), alignment_components=cut.get('components', [])))
    return alignment_review.scope({'cuts': cuts})


def project_scope(edit_path):
    """An empty list permits any sampled interval; known screen cuts constrain moving-frame evidence."""
    edit_path = Path(edit_path)
    if not edit_path.is_file():
        return {}
    edit = json.loads(edit_path.read_text())
    checks = {kind: [] for kind in ('music','effects') if edit.get(kind) and edit.get(kind+'_gain', 1) != 0}
    for kind in expected_audio(edit):
        checks.setdefault(kind, [])
    if edit.get('caption_style') or any(shot.get('lines') for shot in edit.get('shots', [])):
        checks.update(opening_picture=[], caption_overlay=[])
    screen_plan = edit.get('screen_plan') or 'screen-plan.json'
    plan_path = edit_path.parent / screen_plan
    ranges = []
    if plan_path.is_file():
        plan = json.loads(plan_path.read_text())
        geometry = alignment_review.scope(plan)
        if geometry:
            checks['alignment'] = geometry
        for cut in plan.get('cuts', []):
            if 'motion_windows' in cut:
                windows = cut['motion_windows']
                if not isinstance(windows, list) or not windows:
                    raise ValueError('motion_windows must contain at least one interval')
                previous_end = cut['start']
                for window in windows:
                    if (not isinstance(window, list) or len(window) != 2
                            or any(type(frame) is not int for frame in window)
                            or not previous_end <= window[0] < window[1] <= cut['end']
                            or window[1] - window[0] < 2):
                        raise ValueError('Use ordered, non-overlapping motion windows of at least two frames within the cut')
                    ranges.append(window)
                    previous_end = window[1]
                continue
            action = str(cut.get('action', ''))
            screen = cut.get('kind') == 'screen' and (MOVEMENT.search(action) or not STILL.search(action))
            designed_move = (cut.get('kind') in ('photo', 'document', 'animation', 'graphic')
                             or any(k in action.lower() for k in ('镜头', '取景', '鼠标', '指针', 'cursor', 'camera')))
            if screen or (designed_move and MOVEMENT.search(action)):
                ranges.append([cut['start'], cut['end']])
    else:
        checks['alignment'] = timed_layout_scope(edit_path.parent)
    if ranges:
        checks['motion'] = ranges
    extra = edit.get('perception_checks', [])
    if not isinstance(extra, list) or not all(kind in KINDS for kind in extra):
        raise ValueError('Unknown kind in edit.perception_checks')
    for kind in extra:
        checks.setdefault(kind, [])
    return checks


def scope_for(media):
    receipt_for_release = media.with_name(media.name + '.review.json')
    receipt = receipt_for_release if receipt_for_release.exists() else media.parent / 'bundle.json'
    if receipt.is_file():
        manifest = json.loads(receipt.read_text())
        scope = manifest.get('perception_checks', {}).copy()
        if not manifest.get('alignment_scope_version'):
            # New review of a historical bundle with no authoritative frozen layout plan.
            scope.setdefault('alignment', [])
        prior = scope.get('alignment', [])
        if not any(t.get('inventory') for t in prior):
            recovered = timed_layout_scope(receipt.parent, prior, manifest.get('rhythm_cuts', []), media=media,
                                           timeline_sha256=manifest.get('timeline_sha256'),
                                           viewing=manifest.get('viewing_windows', []))
            if recovered or 'alignment' in scope:
                scope['alignment'] = recovered
        return scope
    edit = Path('edit.json')
    checks = project_scope(edit)
    plan = json.loads(edit.read_text()).get('screen_plan') if edit.is_file() else None
    if not edit.is_file() or not Path(plan or 'screen-plan.json').is_file():
        checks.setdefault('alignment', [])
    return checks


def text_field(entry, key):
    value = entry.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f'Missing perceptual evidence: {key}')
    return value.strip()


def real_number(value):
    return type(value) in (float, int) and math.isfinite(value)


def validate_spot(item, scope, frames, size=None):
    check = item['perception']
    if not isinstance(check, dict):
        raise ValueError('perception must be an object')
    kind = check.get('kind')
    if kind not in KINDS:
        raise ValueError('Invalid perception kind')
    text_field(check, 'subject')
    method = check.get('method')
    if kind == 'alignment':
        alignment_review.validate(item, scope.get('alignment', []), size, frames)
    elif kind in ('music','effects'):
        if item['start_frame'] == 0 and item['end_frame'] == frames and frames > 1:
            raise ValueError('Use a specific audible cue or phrase interval, not the entire program')
        text_field(check, 'reference')
        if method == 'device':
            text_field(check, 'device')
        elif method == 'mix_proxy':
            if not real_number(check.get('contrast_db')):
                raise ValueError('Provide finite local contrast_db from the final mix against the named reference')
        else:
            raise ValueError('For audio specify device or mix_proxy, including which sound and its local reference')
    elif kind == 'opening_picture':
        if method != 'muted_replay' or item['start_frame'] != 0:
            raise ValueError('Review the actual opening from its first frame with dialogue and subtitles ignored')
        text_field(check, 'visible_change')
        text_field(check, 'reading_hold')
    elif kind == 'caption_overlay':
        if method != 'encoded_frame':
            raise ValueError('Inspect the burned-in caption on the encoded picture')
        text_field(check, 'phrase')
        text_field(check, 'surrounding_picture')
        box = check.get('box_fraction')
        if not isinstance(box,list) or len(box)!=4 or not all(real_number(x) and 0<=x<=1 for x in box):
            raise ValueError('Supply the observed left, top, right, bottom as fractions of the rendered frame')
        if box[2]<=box[0] or box[3]<=box[1]:
            raise ValueError('Caption bounds must cover a visible region')
    else:
        if method != 'frame_trace':
            raise ValueError('Motion evidence needs a frame_trace measured on the presented picture')
        text_field(check, 'unit')
        pos = check.get('positions')
        first = check.get('first_frame')
        if not isinstance(pos, list) or len(pos) < 2 or not all(real_number(x) for x in pos):
            raise ValueError('Motion needs consecutive scalar positions/scales of the named feature')
        if type(first) is not int or first < item['start_frame'] or first+len(pos) > item['end_frame']:
            raise ValueError('Consecutive motion samples fall outside the screened interval')
        ranges = scope.get('motion') or []
        window = trace_window(check, ranges)
        if ranges and window is None:
            raise ValueError('Trace is outside the planned moving shots')
        if len(pos) < (min(4, window[1] - window[0]) if window else 4):
            raise ValueError('Sample four consecutive moving frames, or all frames of a shorter registered cut')
        if min(pos) == max(pos):
            raise ValueError('Sample the movement itself; all supplied positions are identical')


def trace_window(spot, ranges):
    first = spot['first_frame']
    end = first + len(spot['positions'])
    return next(((a, b) for a, b in ranges if a <= first < end <= b), None)


def motion_evidence(record):
    """Tie a trace to its sampled frames, independently of its enclosing observation window."""
    ranges = record.get('perception_checks', {}).get('motion', [])
    evidence = {tuple(item['window']): item for item in record.get('unchanged_motion', [])}
    for review in record['reviews']:
        for row in review['observations']:
            spot = row.get('perception', {})
            if spot.get('kind') == 'motion':
                window = trace_window(spot, ranges)
                if window:
                    evidence[window] = dict(window=list(window), reviewer=review['reviewer'],
                                            round=record['number'], trace_start=spot['first_frame'])
    return evidence


def unchanged_motion(previous, record, changed):
    """Reuse a reviewed motion window only when its entire registered range is pixel-identical."""
    if not changed:
        return []
    previous_checks = motion_evidence(previous)
    retained = []
    for a, b in record.get('perception_checks', {}).get('motion', []):
        if (a, b) not in previous_checks or a < changed[1] and changed[0] < b:
            continue
        if any(ticket['start_frame'] < b and a < ticket['end_frame']
               for ticket in record['inherited'].values()):
            continue
        retained.append(previous_checks[a, b])
    return retained


def missing_spots(record, *, legacy_motion=False):
    submitted = {row['perception']['kind'] for review in record['reviews'] for row in review['observations']
                 if 'perception' in row}
    scope = record.get('perception_checks', {})
    ranges = scope.get('motion') or []
    measured_windows_required = bool(ranges) and not legacy_motion
    missing = [kind for kind in scope if kind != 'alignment' and kind not in submitted
               and not (kind == 'motion' and measured_windows_required)]
    if measured_windows_required:
        observed = motion_evidence(record)
        missing.extend(f'motion@{a}..{b}' for a, b in ranges if b - a >= 2 and (a, b) not in observed)
    if 'alignment' in scope:
        rows = [row for review in record['reviews'] for row in review['observations']
                if row.get('perception', {}).get('kind') == 'alignment']
        missing.extend(alignment_review.missing(scope['alignment'], rows))
    return missing


def describe_spot(item):
    check = item['perception']
    if check['kind'] == 'alignment':
        detail = alignment_review.describe(check)
    elif check['method'] == 'frame_trace':
        first = check['first_frame']
        positions = '、'.join(f'{x:g}' for x in check['positions'])
        detail = f"从第 {first} 帧起，各帧依次为 {positions} {check['unit']}。"
    elif check['method'] == 'mix_proxy':
        detail = f"终混片段相对{check['reference']}为 {check['contrast_db']:g} dB。本项依据终混测量，实机听感未核验。"
    elif check['kind'] == 'opening_picture':
        detail = f"{check['visible_change']}　{check['reading_hold']}"
    elif check['kind'] == 'caption_overlay':
        detail = f"{check['phrase']}，画幅内范围 {check['box_fraction']}。{check['surrounding_picture']}"
    else:
        detail = f"使用 {check['device']} 实际试听，对照{check['reference']}。"
    return f"{LABELS[check['kind']]}，{check['subject']}。{detail}"
