"""Locate voiced passages where the viewer may have run out of new visual evidence."""
import json
import math
from pathlib import Path


def speech_windows(takes, captions):
    # Merge overlaps but keep natural pauses out of the voiced duration.
    if takes is None or any(not all(k in t for k in ('start_sample', 'end_sample', 'sample_rate')) for t in takes):
        spans = [(a, b) for a, b, _ in captions]
    else:
        spans = [(t['start_sample']/t['sample_rate'], t['end_sample']/t['sample_rate']) for t in takes]
    merged = []
    for a, b in sorted(spans):
        if b <= a:
            continue
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    return merged


def select_picture_holds(shots, fps, captions, *, takes=None, components=(), windows=()):
    """Intersect actual take windows with still pictures, consecutive cards and declared subject regions."""
    speech = speech_windows(takes, captions)
    choices = []
    def consider(start, end, ids, kind, region=None, platforms=()):
        # Break at longer silent gaps. A few words next to a long music-only hold are fine.
        groups = []
        for a, b in speech:
            a, b = max(a, start/fps), min(b, end/fps)
            if a >= b:
                continue
            if groups and a-groups[-1][-1][1] <= .5:
                groups[-1].append((a, b))
            else:
                groups.append([(a, b)])
        for group in groups:
            first = max(start, math.floor(group[0][0]*fps + 1e-8))
            stop = min(end, math.ceil(group[-1][1]*fps - 1e-8))
            voiced = sum(b-a for a, b in group)
            if stop-first < fps*2.5 or voiced < 2:
                continue
            overlapping = [s['shot'] for s in shots if s['shot'] in ids and s['start_frame'] < stop and first < s['end_frame']]
            if not overlapping or (platforms and sum(a < stop and first < b for a,b in platforms) < 2):
                continue
            label = '+'.join(overlapping)
            suffix = f'/{region}' if region else ''
            row = dict(target=f'{label}{suffix}@{first}..{stop}', shot=label, shots=overlapping,
                       kind=kind, start_frame=first, end_frame=stop, voiced_seconds=round(voiced, 3),
                       captions=[t for a,b,t in captions if a < stop/fps and first/fps < b])
            if region:
                row['region'] = region
            reading = [w for w in windows if w['start'] < stop and first < w['end']]
            if reading:
                row['viewing_windows'] = reading
            choices.append(row)

    # An edit may contain separate still shots or multiple cards baked into the same clip.
    chain = []
    def finish_chain():
        if len(chain) >= 2:
            consider(chain[0][0], chain[-1][1], {row[2] for row in chain}, 'static_sequence',
                     platforms=[(a, b) for a, b, *_ in chain])
        chain.clear()

    for index, shot in enumerate(shots):
        spans = shot.get('quiet_spans', shot['longest_quiet_spans'])
        for span in spans:
            a, b = span['start_frame'], span['end_frame']
            consider(a, b, [shot['shot']], 'static_picture')
            if chain:
                _, prev_end, _, prev_index = chain[-1]
                if index == prev_index:
                    adjacent = a-prev_end <= .4*fps
                else:
                    adjacent = (index == prev_index+1 and a-shot['start_frame'] <= .4*fps
                                and shots[prev_index]['end_frame']-prev_end <= .4*fps)
                if not adjacent:
                    finish_chain()
            chain.append((a, b, shot['shot'], index))
    finish_chain()
    for p in components:
        if not p.get('authored') or p.get('mode') == 'still':
            continue
        ids = [s['shot'] for s in shots if s['start_frame'] < p['end'] and p['start'] < s['end_frame']]
        for span in p.get('quiet_spans', []):
            consider(span['start_frame'], span['end_frame'], ids, 'static_subject', p.get('region') or p['subject'])
    # One editorial decision covers an entire voiced sequence of static cards.
    sequences = [r for r in choices if r['kind'] == 'static_sequence']
    unique = {}
    for r in choices:
        if r['kind'] == 'static_picture' and any(r['shot'] in seq['shots'] and
                seq['start_frame'] <= r['start_frame'] and r['end_frame'] <= seq['end_frame'] for seq in sequences):
            continue
        unique[r['target']] = r
    broad = [r for r in unique.values() if r['kind'] != 'static_subject']
    for r in list(unique.values()):
        if r['kind'] != 'static_subject':
            continue
        parent = next((q for q in broad if q['start_frame'] <= r['start_frame']
                       and r['end_frame'] <= q['end_frame'] and set(r['shots']) <= set(q['shots'])), None)
        if parent:
            parent.setdefault('regions', []).append(r['region'])
            del unique[r['target']]
    return sorted(unique.values(), key=lambda r: (r['start_frame'], r['end_frame'], r['target']))


def planned_still_holds(edit, timeline):
    """Early editorial note for still files played under speech. Encoded-picture checks cover every input type."""
    from transitions import STILLS
    shots = []
    for shot, t in zip(edit['shots'], timeline['shots']):
        start, end = t['start_frame'], t['start_frame'] + t['frames']
        plain = Path(shot.get('visual', '')).suffix.lower() in STILLS and not shot.get('motion_layers')
        spans = [dict(start_frame=start, end_frame=end)] if plain else []
        shots.append(dict(shot=t['id'], start_frame=start, end_frame=end, longest_quiet_spans=spans))
    takes = timeline.get('voice_takes', [])
    lines = [(t['start_sample']/t['sample_rate'], t['end_sample']/t['sample_rate'], t['spoken'])
             for t in takes if all(k in t for k in ('start_sample', 'end_sample', 'sample_rate', 'spoken'))]
    return select_picture_holds(shots, timeline['fps'], lines, takes=takes)


def check_picture_holds(media, record, decision):
    """Existing films lacking the news/explainer story-intent contract keep their review path."""
    if record.get('story_intent', {}).get('kind') not in ('news', 'explainer'):
        return
    from review import captions
    from review_bundle import digest

    folder = Path(media).parent
    timeline_path = folder/'timeline.json'
    caption_path = folder/'captions.srt'
    path = Path(decision.get('picture_report') or decision.get('ending_report') or folder/'qa/content-check.json')
    if not path.is_file():
        raise ValueError('Run review.py on this candidate or supply decision.picture_report')
    report = json.loads(path.read_text())
    if (report.get('media_sha256') != record['candidate_sha256']
            or report.get('timeline_sha256') != digest(timeline_path)
            or report.get('captions_sha256') != digest(caption_path)):
        raise ValueError('Picture rhythm report differs from the reviewed media, timeline or captions.')
    timing = json.loads(timeline_path.read_text())
    rhythm = report.get('pace', {}).get('picture_rhythm', {})
    shots = rhythm.get('shots')
    if (rhythm.get('method') != 'luma_change' or not isinstance(shots, list)
            or len(shots) != len(timing['shots'])):
        raise ValueError('Review the actual encoded picture with picture_rhythm before delivery')
    if rhythm.get('spoken_hold_version') != 2:
        raise ValueError('Run review.py again to check single-line and multi-shot voiced holds')
    for row, s in zip(shots, timing['shots']):
        if ((row['shot'], row['start_frame'], row['end_frame']) !=
                (s['id'], s['start_frame'], s['start_frame']+s['frames'])
                or not isinstance(row.get('quiet_spans'), list)):
            raise ValueError('Rhythm intervals do not correspond to the candidate timeline')
        for span in row['quiet_spans']:
            if not row['start_frame'] <= span['start_frame'] < span['end_frame'] <= row['end_frame']:
                raise ValueError('Quiet interval outside its measured shot')
    targets = select_picture_holds(shots, timing['fps'], list(captions(folder)), takes=timing.get('voice_takes'),
                                   components=report.get('component_motion', []),
                                   windows=report['pace'].get('viewing_windows', []))
    if [t['target'] for t in targets] != [t['target'] for t in rhythm.get('hold_candidates', [])]:
        raise ValueError('Voiced hold candidates differ from the reviewed intervals')
    notes = decision.get('picture_holds', [])
    if not isinstance(notes, list) or not all(isinstance(n, dict) for n in notes):
        raise ValueError('decision.picture_holds must list the chair’s observations')
    expected = {t['target'] for t in targets}
    answered = [n.get('target') for n in notes]
    if len(answered) != len(expected) or set(answered) != expected:
        raise ValueError(f'Review each candidate hold exactly once in decision.picture_holds: {sorted(expected)}')
    for note in notes:
        if (note.get('action') not in ('read', 'listen', 'observe')
                or not all(isinstance(note.get(k), str) and note[k].strip() for k in ('focus', 'reason'))):
            raise ValueError(f"For {note['target']}, identify what to read, hear or observe and why the hold serves it. "
                             'If the picture needs a new action, open a revision ticket before delivery.')
    decision['picture_hold_evidence'] = dict(report_sha256=digest(path), targets=targets)
