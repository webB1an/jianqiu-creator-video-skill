"""Bring at most two low-change spoken passages into the chair's candidate-level decision."""
import json
from pathlib import Path


def select_picture_holds(shots, fps, captions):
    """Rank by overlapping speech time; a brief subtitle crossing earns no extra cue."""
    choices = []
    for shot in shots:
        for span in shot['longest_quiet_spans']:
            first, end = span['start_frame'], span['end_frame']
            if (end-first)/fps < 2.5:
                continue
            spoken_seconds = 0.
            lines = []
            for a, b, text in captions:
                overlap = min(b, end/fps) - max(a, first/fps)
                if overlap >= min(.3, .8*(b-a)) and overlap > 0:
                    spoken_seconds += overlap
                    lines.append(text)
            if len(lines) >= 2:
                choices.append(dict(target=f"{shot['shot']}@{first}..{end}", shot=shot['shot'],
                                    start_frame=first, end_frame=end, captions=lines,
                                    voiced_seconds=round(spoken_seconds, 3)))
    choices.sort(key=lambda c: (-c['voiced_seconds'], c['start_frame']))
    return choices[:2]


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
    for row, s in zip(shots, timing['shots']):
        if ((row['shot'], row['start_frame'], row['end_frame']) !=
                (s['id'], s['start_frame'], s['start_frame']+s['frames'])
                or not isinstance(row.get('longest_quiet_spans'), list)):
            raise ValueError('Rhythm intervals do not correspond to the candidate timeline')
        for span in row['longest_quiet_spans']:
            if not row['start_frame'] <= span['start_frame'] < span['end_frame'] <= row['end_frame']:
                raise ValueError('Quiet interval outside its measured shot')
    targets = select_picture_holds(shots, timing['fps'], list(captions(folder)))
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
