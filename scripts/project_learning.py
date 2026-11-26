"""Search the personal library before outlining, bind a past finding to a shot and record its outcome."""
import argparse
import hashlib
import json
from pathlib import Path

import library


LOG = Path('qa/learning.json')


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def key(value):
    return hashlib.sha256(library.json_text(value).encode()).hexdigest()


def frozen_learning(project):
    path = Path(project) / LOG
    if not path.exists():
        return None
    data = read(path)
    if data.get('schema') != 1:
        raise ValueError('Unknown project learning record')
    return data


def lesson_path(root, ref):
    parts = ref.split('/')
    if len(parts) == 2:
        folder, card, entry = 'visual-reviews', *parts
    elif len(parts) == 3 and parts[0] == 'result':
        folder, card, entry = 'learning-results', parts[1], parts[2]
    else:
        raise ValueError('Choose a lesson ID returned by a project search')
    return library.local(root / folder, card + '/' + entry + '.json')


def find(root, project, terms, shot=None):
    terms = [term.strip() for term in terms if term.strip()]
    if not terms:
        raise ValueError('Give a subject, a question or an action to look up')
    items = library.load(root)
    reviews = []
    outcomes = []
    extra_text = {}
    for path in sorted((root / 'visual-reviews').glob('*/*.json')):
        note = read(path)
        card = note['card_id']
        if card in items:
            reviews.append((path, note))
            extra_text[card] = extra_text.get(card, '') + ' ' + ' '.join(
                str(note.get(k, '')) for k in ('lesson', 'intent', 'decision_reason'))
    for path in sorted((root / 'learning-results').glob('*/*.json')):
        card = path.parent.name
        if card in items:
            outcome = read(path)
            outcomes.append((path, outcome))
            extra_text[card] = extra_text.get(card, '') + ' ' + outcome['reason'] + ' ' + outcome['decision']['reason']
    ranked = library.search_items(items, terms, root, extra_text)
    hits = {item['id'] for item in ranked}
    notes = {}
    for path, note in reviews:
        card = note['card_id']
        if card not in hits:
            continue
        ref = card + '/' + path.stem
        notes[ref] = {'sha256': library.digest(path), **{k: note[k] for k in
                      ('card_id', 'card_revision', 'lesson', 'verdict', 'candidate_sha256', 'shot')}}
        if note.get('adaptation'):
            notes[ref]['adaptation'] = note['adaptation']
    tested = {}
    for path, result in outcomes:
        card = path.parent.name
        if card not in hits:
            continue
        ref = card + '/' + path.stem
        tested[ref] = {k: result[k] for k in ('assessment', 'reason', 'verdict', 'candidate_sha256')}
        lesson_ref = 'result/' + ref
        tested[ref]['lesson_id'] = lesson_ref
        notes[lesson_ref] = dict(sha256=library.digest(path), card_id=card,
                                 card_revision=result['decision']['revision'], lesson=result['reason'],
                                 assessment=result['assessment'], verdict=result['verdict'],
                                 candidate_sha256=result['candidate_sha256'], shot=result['decision']['shot'])
    assets = {item['id']: {k: item[k] for k in ('title', 'revision', 'scope')} for item in ranked}
    # Preserve the names and units used on the card, including legacy Chinese notes.
    fields = ('in', 'out', 'fps', 'cue', 'cue_basis', 'cut', 'cut_frame', 'cut_range', 'cut_range_frames',
              'source_in', 'source_out', 'source_windows', 'source_timecode', 'original_range',
              'shot_order', 'action', 'actual_action', 'target', 'direction', 'frame_direction',
              'observation', 'contrast', 'source_context', 'recording', 'audio', 'sync', 'limitations',
              '原作起止秒', '切点', '计时依据', '动作与反馈', '动作与剪点', '可确认范围')
    for item in ranked:
        asset = assets[item['id']]
        if item.get('preview'):
            asset['preview'] = item['preview']
        notes_on_source = {k: item['editorial'][k] for k in fields if k in item['editorial']}
        if notes_on_source:
            asset['source_notes'] = notes_on_source
        if item['editorial'].get('applications'):
            asset['applications'] = library.research_applications(item, items)
    retention_lessons = []
    ret_dir = root / 'retention-lessons'
    if ret_dir.is_dir():
        for rp in sorted(ret_dir.glob('*.json'))[-5:]:
            try:
                rdata = read(rp)
                retention_lessons.append(dict(
                    project=rdata.get('project'), platform=rdata.get('platform'),
                    lessons=rdata.get('pattern_lessons', []),
                ))
            except (OSError, ValueError):
                continue
    result = dict(terms=terms, assets=assets, lessons=notes, previous_outcomes=tested)
    if retention_lessons:
        result['retention_lessons'] = retention_lessons
    path = project / LOG
    with library.exclusive(path.parent):
        data = frozen_learning(project) or dict(schema=1, searches={}, decisions={})
        if shot is not None:
            shot = shot.strip()
            if not shot:
                raise ValueError('Use a nonempty shot ID to pair two searches')
            attempt = data.get('search_count', 0) + 1
            data['search_count'] = attempt
            result.update(shot=shot, attempt=attempt)
            misses = data.setdefault('misses', {})
            if not assets:
                misses[key([shot, terms])] = dict(shot=shot, terms=terms, attempt=attempt)
            else:
                result['earlier_misses'] = [' '.join(m['terms']) for m in misses.values() if m['shot'] == shot]
        data['searches'][key(terms) if shot is None else key([shot, terms])] = result
        library.atomic(path, library.json_text(data))
    return result


def confirmed_pair(data, item, revision, shot, query):
    normalize = library.library_search.normalize
    miss = next((m for m in data.get('misses', {}).values()
                 if m['shot'] == shot and normalize(' '.join(m['terms'])) == normalize(query)), None)
    if miss is None:
        raise ValueError('The confirmed wording must be a recorded zero-result query for this shot')
    hits = [s for s in data['searches'].values()
            if s.get('shot') == shot and s['attempt'] > miss['attempt']
            and normalize(' '.join(s['terms'])) != normalize(query)
            and s['assets'].get(item, {}).get('revision') == revision]
    if not hits:
        raise ValueError('Find this card with a later reworded query for the same shot and revision')
    retry = max(hits, key=lambda s: s['attempt'])
    return {'original': ' '.join(miss['terms']), 'retry': ' '.join(retry['terms'])}


def decide(root, project, item, shot, action, reason, lesson, confirmed_query=None):
    if not reason.strip() or not shot.strip() or action not in ('use', 'adapt', 'skip'):
        raise ValueError('Give a shot, the decision and its concrete effect or reason')
    path = project / LOG
    with library.exclusive(path.parent):
        data = frozen_learning(project)
        searches = list(data['searches'].values()) if data else []
        hits = [s['assets'][item] for s in searches if item in s['assets']]
        if not hits:
            raise ValueError('Look up this asset before recording a decision')
        current = library.load(root)[item]
        if not any(hit['revision'] == current['revision'] for hit in hits):
            raise ValueError('The library card changed; look it up again')
        row = dict(asset=item, revision=current['revision'], scope=current['scope'],
                   shot=shot.strip(), action=action, reason=reason.strip())
        if lesson:
            note_path = lesson_path(root, lesson)
            saved = next((s['lessons'][lesson] for s in searches
                          if lesson in s['lessons'] and note_path.is_file()
                          and s['lessons'][lesson]['sha256'] == library.digest(note_path)), None)
            if saved is None or saved['card_id'] != item:
                raise ValueError('The selected lesson must match an observed search result for this card')
            row.update(lesson_id=lesson, prior=saved)
        if confirmed_query is not None:
            if action == 'skip':
                raise ValueError('Only an inspected card selected for use or adaptation can learn a query')
            pair = confirmed_pair(data, item, current['revision'], shot.strip(), confirmed_query)
            library.remember_query(root, item, current['revision'], pair['original'])
            row['confirmed_query'] = pair
        decision_id = key([item, shot.strip(), lesson])
        data['decisions'][decision_id] = row
        library.atomic(path, library.json_text(data))
    return dict(decision_id=decision_id, **row)


def outcome(root, project, media, decision_id, judgement, reason):
    import editorial_review
    import review_bundle
    if not reason.strip() or judgement not in ('supported', 'failed', 'inconclusive'):
        raise ValueError('Record an observed result and what it means for next time')
    media = review_bundle.released_snapshot(media)
    sha = library.digest(media)
    bundle = review_bundle.fingerprint(media)
    if not bundle:
        raise ValueError('The outcome needs a staged candidate with frozen learning decisions')
    receipt = read(review_bundle.receipt_for(media))
    choice = receipt.get('project_learning', {}).get('decisions', {}).get(decision_id)
    if not choice:
        raise ValueError('This decision is absent from the reviewed candidate')
    ledger = project / editorial_review.DEFAULT_LEDGER
    with editorial_review.locked(ledger):
        rounds = read(ledger)['rounds']
        record = next((r for r in reversed(rounds)
                       if r['candidate_sha256'] == sha and r['bundle_sha256'] == bundle), None)
        if not record or not record.get('meeting') or not record.get('decision'):
            raise ValueError('Complete the editorial review of this version before writing an outcome')
        evidence = [dict(reviewer=r['reviewer'], start=o['start_frame'], end=o['end_frame'], observation=o['evidence'])
                    for r in record['reviews'] for o in r['observations'] if o['shot'] == choice['shot']]
        if not evidence:
            raise ValueError('No reviewed observation covers this shot')
        result = dict(decision=choice, assessment=judgement, reason=reason.strip(), evidence=evidence,
                      candidate_sha256=sha, bundle_sha256=bundle, round=record['number'],
                      verdict=record['decision']['decision'])
    return save_result(root, result, [bundle, decision_id, record['number']])


def trial_outcome(root, media, decision_id, folder, shot, reason, roughcut=False):
    import blind_review
    import review_bundle
    if not reason.strip():
        raise ValueError('Describe the finding and its limits')
    if roughcut:
        from picture_roughcut import reviewed_pair
        receipt, bundle = reviewed_pair(media)
        choice = receipt['decisions'].get(decision_id)
    else:
        media = review_bundle.released_snapshot(media)
        bundle = review_bundle.fingerprint(media)
        if not bundle:
            raise ValueError('Stage the candidate with its selected lesson first')
        receipt = read(review_bundle.receipt_for(media))
        choice = receipt.get('project_learning', {}).get('decisions', {}).get(decision_id)
    if not choice or choice['shot'] != shot or choice['action'] == 'skip':
        raise ValueError('Select the frozen decision used in the compared shot')
    trial = blind_review.result(folder)
    if trial.get('mode') == 'first_view':
        raise ValueError('A first-view observation describes one cut. Use an anonymous pair for a comparative outcome.')
    sha = library.digest(media)
    if trial['versions']['candidate'] != sha or not trial['responses']:
        raise ValueError('The observed pair must include this exact candidate and a recorded response')
    if roughcut and trial['versions']['baseline'] != receipt['versions']['alternate']['sha256']:
        raise ValueError('The anonymous baseline must be the alternate from this rough-cut receipt')
    preferences = [r['version'] for r in trial['responses']]
    # A tie or a split result stays inconclusive unless one version has an absolute majority.
    if preferences.count('candidate') > len(preferences) / 2:
        assessment = 'supported'
    elif preferences.count('baseline') > len(preferences) / 2:
        assessment = 'failed'
    else:
        assessment = 'inconclusive'
    result = dict(decision=choice, assessment=assessment, reason=reason.strip(), evidence=trial,
                  candidate_sha256=sha, bundle_sha256=bundle, verdict='trial_comparison',
                  evidence_type='blind_trial')
    if roughcut:
        result['roughcut'] = dict(plan_sha256=receipt['plan_sha256'], fps=receipt['fps'], versions={
            name: dict(order=[cut['id'] for cut in version['cuts']],
                       shot=next(c for c in version['cuts'] if c['id'] == shot))
            for name, version in receipt['versions'].items()})
    return save_result(root, result, [bundle, decision_id, key(trial)])


def save_result(root, result, identity):
    choice = result['decision']
    folder = library.local(root, 'learning-results/' + choice['asset'])
    if not library.local(root, 'items/' + choice['asset'] + '/' + choice['revision']).is_dir():
        raise ValueError('The selected library revision is missing')
    if choice.get('lesson_id'):
        origin = lesson_path(root, choice['lesson_id'])
        if not origin.is_file() or library.digest(origin) != choice['prior']['sha256']:
            raise ValueError('The cited lesson differs from its frozen source')
    with library.exclusive(root):
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (key(identity) + '.json')
        if path.is_file() and read(path) != result:
            raise ValueError('This decision already has a different outcome for this round')
        library.atomic(path, library.json_text(result))
        library.write_html(root, library.load(root))
    return result


def history(root, card):
    if card not in library.load(root):
        raise ValueError('Choose an existing asset')
    return [read(p) for p in sorted(library.local(root, 'learning-results/' + card).glob('*.json'))]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path)
    parser.add_argument('--project', type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest='command', required=True)
    search = commands.add_parser('find')
    search.add_argument('terms', nargs='+')
    search.add_argument('--shot', help='Group a shot’s original and reworded searches')
    choice = commands.add_parser('decide')
    choice.add_argument('asset')
    choice.add_argument('--shot', required=True)
    choice.add_argument('--action', required=True, choices=('use', 'adapt', 'skip'))
    choice.add_argument('--reason', required=True)
    choice.add_argument('--lesson')
    choice.add_argument('--confirmed-query', help='Earlier missed wording verified against this card’s actual preview')
    result = commands.add_parser('outcome')
    result.add_argument('media', type=Path)
    result.add_argument('decision_id')
    result.add_argument('--assessment', required=True, choices=('supported', 'failed', 'inconclusive'))
    result.add_argument('--reason', required=True)
    trial = commands.add_parser('trial-outcome')
    trial.add_argument('media', type=Path)
    trial.add_argument('decision_id')
    trial.add_argument('--trial', type=Path, required=True)
    trial.add_argument('--roughcut', action='store_true', help='Record a comparison of two source-footage orders')
    trial.add_argument('--shot', required=True)
    trial.add_argument('--reason', required=True)
    commands.add_parser('history').add_argument('asset')
    args = parser.parse_args()
    project = args.project.resolve()
    root = library.find_root(args.root, project)
    if args.command == 'find':
        answer = find(root, project, args.terms, args.shot)
    elif args.command == 'decide':
        answer = decide(root, project, args.asset, args.shot, args.action, args.reason, args.lesson, args.confirmed_query)
    elif args.command == 'outcome':
        answer = outcome(root, project, args.media.absolute(), args.decision_id, args.assessment, args.reason)
    elif args.command == 'trial-outcome':
        answer = trial_outcome(root, args.media.absolute(), args.decision_id, args.trial, args.shot, args.reason,
                               roughcut=args.roughcut)
    else:
        answer = history(root, args.asset)
    print(library.json_text(answer))


if __name__ == '__main__':
    main()
