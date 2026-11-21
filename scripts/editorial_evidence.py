"""Index evidence shots and enforce candidate-bound editorial observations at sign-off."""
from pathlib import Path
import json
import re

import story_review

FIELDS = {
    'opening': ('promise', 'first_reveal'),
    'cut': ('before', 'after', 'without_cut'),
    'payoff': ('promise', 'takeaway'),
    'source': ('locator', 'original', 'spoken', 'onscreen', 'comparison'),
    'source_scope': ('basis',),
}
LABELS = {
    'promise': '开出的承诺', 'first_reveal': '第一次兑现', 'before': '切前',
    'after': '切后', 'without_cut': '删换后的影响', 'takeaway': '观众能带走什么',
    'locator': '原件位置', 'original': '原句及限定', 'spoken': '片中说法',
    'onscreen': '画面强调', 'comparison': '核对结果', 'basis': '片型和实看依据',
}


def screen_plan_for(edit_path):
    edit_path = Path(edit_path)
    edit = json.loads(edit_path.read_text())
    return edit_path.parent / (edit.get('screen_plan') or 'screen-plan.json')


def targets_for(edit_path):
    """Freeze source-bearing pictures into the candidate bundle, including marked non-document shots."""
    edit_path = Path(edit_path)
    plan = screen_plan_for(edit_path)
    plan_data = json.loads(plan.read_text()) if plan.is_file() else {}
    targets = []
    for cut in plan_data.get('cuts', []):
        claim = cut.get('verify_claim', cut.get('kind') in ('document', 'screen'))
        if not isinstance(claim, (bool, str)) or isinstance(claim, str) and not claim.strip():
            raise ValueError('verify_claim must be a claim description or a boolean')
        if not claim:
            continue
        start, end = cut['start'], cut['end']
        if type(start) is not int or type(end) is not int or not 0 <= start < end:
            raise ValueError('Evidence shots need valid frame boundaries')
        key = cut.get('source_key') or cut.get('subject') or 'source'
        targets.append(dict(id=f'{key}@{start}', start_frame=start, end_frame=end,
                            subject=claim if isinstance(claim, str) else cut['subject']))
    edit = json.loads(edit_path.read_text())
    time_path = edit_path.parent / 'timeline.json'
    timing = json.loads(time_path.read_text()) if time_path.is_file() else {}
    if timing.get('claim_surfaces_version') or 'claims' in edit:
        targets.extend(surface_targets(edit_path.parent, edit, timing, plan_data.get('overlays', [])))
    if len({t['id'] for t in targets}) != len(targets):
        raise ValueError('Each source claim needs a distinct target ID')
    return targets


# Match Arabic digits and Chinese quantities followed by common units.
QUANTITY = re.compile(r'\d|[零〇一二两三四五六七八九十百千万亿]+(?:秒|毫秒|帧|个|位|名|次|倍|年|月|日|元|米|点|成|项)|百分之')


def surface_targets(project, edit, timing, overlays):
    total = timing.get('frames', 0)
    windows = {s['id']: (s['start_frame'], s['start_frame'] + s['frames']) for s in timing.get('shots', [])}
    if not total:
        raise ValueError('Claim review needs the prepared timeline')
    result = []

    def add(identifier, surface, subject, window, source=None):
        start, end = window
        if not subject.strip() or not 0 <= start < end <= total:
            raise ValueError('A claim needs text and a window inside the film')
        row = dict(id=identifier, surface=surface, subject=subject, start_frame=start, end_frame=end)
        if source is not None:
            if not isinstance(source, str) or not source.strip():
                raise ValueError('Give the source page or original measurement for the claim')
            row['source'] = source
        result.append(row)

    for shot in edit.get('shots', []):
        for i, line in enumerate(shot.get('lines', [])):
            text = line['text']
            spoken = line.get('spoken', text)
            if QUANTITY.search(spoken):
                add(f"voice/{shot['id']}/{i}", 'narration', spoken, windows[shot['id']])
            if spoken != text and QUANTITY.search(text):
                add(f"subtitle/{shot['id']}/{i}", 'subtitle', text, windows[shot['id']])
    for i, overlay in enumerate(overlays):
        if overlay.get('purpose') in ('measurement', 'evidence') or QUANTITY.search(overlay['text']):
            add(f'overlay/{i}', 'overlay', overlay['text'], (overlay['start'], overlay['end']))
    # Covers and their alternatives are attachments; reviewers compare the exported images as well.
    cover_path = project / 'cover.json'
    cover = json.loads(cover_path.read_text()) if cover_path.is_file() else {}
    if edit.get('title'):
        add('title/main', 'title', edit['title'], (0, total))
    for name, layout in [('default', cover), *cover.get('layouts', {}).items()]:
        text = ' '.join(x for x in [layout.get('kicker'), *layout.get('lines', [])] if x)
        if text.strip():
            add(f'cover/{name}', 'cover', text, (0, total))
    if not any(t['surface'] == 'cover' for t in result):
        add('cover/artwork', 'cover', '查三种成品封面上的主题、数字、单位及限定词，与片内原始依据逐项比对。', (0, total))
    for claim in edit.get('claims', []):
        window = windows[claim['shot']] if claim.get('shot') else (0, total)
        add('claim/' + claim['id'], claim['surface'], claim['text'], window, claim['source'])
    return result


def validate(item, record, tickets):
    spot = item['editorial']
    if not isinstance(spot, dict):
        raise ValueError('editorial must be an object')
    kind = spot.get('kind')
    if not isinstance(kind, str) or kind not in FIELDS:
        raise ValueError('Unknown editorial observation kind')
    for field in FIELDS[kind]:
        if not isinstance(spot.get(field), str) or not spot[field].strip():
            raise ValueError(f'Editorial {kind} requires {field}')
    start, end = item['start_frame'], item['end_frame']
    if kind == 'opening' and start != 0:
        raise ValueError('The opening observation must start at the first frame')
    if kind == 'payoff' and end != record['frames']:
        raise ValueError('The payoff observation must include the last frame')
    targets = record.get('editorial_targets', [])
    if kind == 'source_scope' and targets:
        raise ValueError('Compare the identified source-bearing shots with their originals')
    if kind == 'source' and 'target' in spot:
        key = spot.get('target')
        target = next((t for t in targets if t['id'] == key), None)
        if not target or not (target['start_frame'] <= start < end <= target['end_frame']):
            raise ValueError('Identify a registered source target and screen its frame window')
    verdict = spot.get('verdict')
    if verdict not in ('keep', 'fix'):
        raise ValueError('Editorial verdict must be keep or fix')
    if verdict == 'fix':
        key = spot.get('issue_id')
        ticket = tickets.get(key) if isinstance(key, str) else None
        if not ticket or ticket['start_frame'] >= end or start >= ticket['end_frame']:
            raise ValueError('A fix finding needs an overlapping formal issue_id')
        if kind == 'source' and ticket['priority'] != 'critical':
            raise ValueError('Unresolved source-meaning discrepancies require a critical ticket')
    elif 'issue_id' in spot:
        raise ValueError('Only a fix finding can link to an issue_id')


def missing(record):
    spots = [r['editorial'] for review in record['reviews'] for r in review['observations'] if 'editorial' in r]
    spots.extend(item['observation']['editorial'] for item in record.get('unchanged_sources', []))
    kinds = {spot['kind'] for spot in spots}
    result = [kind for kind in ('opening', 'cut', 'payoff') if kind not in kinds]
    targets = record.get('editorial_targets', [])
    if targets:
        checked = {spot.get('target') for spot in spots if spot['kind'] == 'source'}
        result.extend(f"source:{t['id']}" for t in targets if t['id'] not in checked)
    elif not kinds.intersection(('source', 'source_scope')):
        result.append('source/source_scope')
    if not result:
        independent = {review['reviewer'].strip().casefold() for review in record['reviews']
                       if any('editorial' in row for row in review['observations'])}
        if len(independent) < 2:
            result.append('second_editorial_reviewer')
    return result + story_review.missing(record)


def unchanged_sources(previous, record, changed):
    """Carry only local text comparisons from proven identical sound, picture, timing and captions."""
    if not changed or not record.get('text_tracks') or previous.get('text_tracks') != record['text_tracks']:
        return []
    current = {t['id']: t for t in record['editorial_targets']}
    same_targets = {t['id'] for t in previous.get('editorial_targets', []) if t == current.get(t['id'])}
    prior = list(previous.get('unchanged_sources', []))
    prior.extend(dict(round=previous['number'], reviewer=review['reviewer'], observation=row)
                 for review in previous['reviews'] for row in review['observations'] if 'editorial' in row)
    kept = []
    for item in prior:
        row = item['observation']
        spot = row['editorial']
        if spot['kind'] != 'source' or spot['verdict'] != 'keep' or spot.get('target') not in same_targets:
            continue
        target = current[spot['target']]
        if target.get('surface') in ('cover', 'title'):
            continue
        left = min(target['start_frame'], row['start_frame'])
        right = max(target['end_frame'], row['end_frame'])
        disputed = any(issue['start_frame'] < right and left < issue['end_frame']
                       for issue in record['inherited'].values())
        if not disputed and (right < changed[0] or changed[1] < left):
            kept.append(item)
    return kept


def describe(item):
    spot = item['editorial']
    details = [f"{LABELS[field]}：{spot[field]}" for field in FIELDS[spot['kind']]]
    if spot['verdict'] == 'fix':
        details.append(f"退改票 {spot['issue_id']}")
    else:
        details.append('审片人建议保留')
    return '　'.join(details)
