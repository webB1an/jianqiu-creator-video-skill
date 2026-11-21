"""Bind an unprompted account of the whole film to its editorial intent and delivery decision."""
from pathlib import Path


def intent_from(data):
    if 'story_intent' not in data:
        return None
    intent = data['story_intent']
    if not isinstance(intent, dict) or any(not isinstance(intent.get(k), str) or not intent[k].strip()
                                           for k in ('kind', 'main_point', 'viewer_value')):
        raise ValueError('story_intent requires kind, main_point and viewer_value as nonempty text')
    return dict(intent)


def open_round(record, previous, manifest):
    intent = intent_from(manifest) or (previous or {}).get('story_intent')
    if not intent:
        return
    record['story_intent'] = intent
    record['first_views'] = {}
    if (previous and previous['candidate_sha256'] == record['candidate_sha256']
            and previous.get('story_intent') == intent):
        record['first_views'].update(previous.get('first_views', {}))


def record_trial(record, prior_rounds, trial):
    import blind_review
    if record.get('decision') or not record.get('story_intent'):
        raise ValueError('Import first-view responses into an undecided round with a registered story_intent')
    public = blind_review.result(Path(trial))
    if (public.get('mode') != 'first_view' or public.get('story_version') != 1
            or public.get('media_sha256') != record['candidate_sha256']):
        raise ValueError('Use a first-view --story trial of this exact candidate')
    earlier = set()
    for old in prior_rounds:
        earlier.update(r['reviewer'].strip().casefold() for r in old['reviews'])
        earlier.update(old.get('first_views', {}))
        if old['meeting']:
            earlier.add(old['meeting']['chair'].strip().casefold())
            earlier.update(t['implementer'].strip().casefold() for t in old['meeting']['tickets'] if t.get('implementer'))
    if not public['responses']:
        raise ValueError('Record at least one independent response before importing the trial')
    merged = dict(record['first_views'])
    for row in public['responses']:
        key = row['reviewer'].strip().casefold()
        entry = dict(question=public['question'], response=row)
        if key in merged and merged[key] != entry:
            raise ValueError('Keep the original first-view statement from this reviewer')
        if key not in merged and key in earlier and row['unbriefed']:
            raise ValueError('A viewer credited on an earlier round cannot register a fresh unbriefed viewing')
        merged[key] = entry
    if record['meeting'] and merged != record['first_views']:
        record.setdefault('prior_meetings', []).append(record['meeting'])
        record['meeting'] = None
    record['first_views'] = merged


def eligible(record):
    return {k: entry['response'] for k, entry in record.get('first_views', {}).items()
            if entry['response']['unbriefed'] and entry['response']['playback'] == 'continuous'}


def validate_meeting(record, meeting):
    if not record.get('story_intent'):
        return
    viewers = eligible(record)
    readings = meeting.get('story_readings', [])
    if not isinstance(readings, list):
        raise ValueError('story_readings must list the chair’s comparison with each independent first view')
    seen = set()
    for finding in readings:
        if not isinstance(finding, dict) or not isinstance(finding.get('reviewer'), str):
            raise ValueError('Each story reading must identify the first viewer')
        key = finding['reviewer'].strip().casefold()
        if key not in viewers or key in seen:
            raise ValueError('Compare each qualifying first viewer’s original response once')
        seen.add(key)
        if any(finding.get(k) not in ('matched', 'partial', 'missed') for k in ('main_point', 'viewer_value')):
            raise ValueError('Rate main_point and viewer_value separately: matched, partial, or missed')
        if finding.get('attention') not in ('keep', 'revise'):
            raise ValueError('Compare the viewer’s account of attention and choose keep or revise')
        if not isinstance(finding.get('basis'), str) or not finding['basis'].strip():
            raise ValueError('Give the comparison with the intended story and the concrete moment that held or lost attention')
        if key == meeting['chair'].strip().casefold():
            raise ValueError('The chair cannot independently first-view and adjudicate the same film')
    if seen != set(viewers):
        raise ValueError('Compare every continuous unbriefed response at the meeting')


def missing(record):
    if not record.get('story_intent'):
        return []
    if not eligible(record):
        return ['story:first_view']
    if not record.get('meeting'):
        return ['story:meeting']
    validate_meeting(record, record['meeting'])
    result = []
    for r in record['meeting']['story_readings']:
        for field in ('main_point', 'viewer_value', 'attention'):
            if r[field] != ('keep' if field == 'attention' else 'matched'):
                result.append(f"story:{r['reviewer']}:{field}")
    return result
