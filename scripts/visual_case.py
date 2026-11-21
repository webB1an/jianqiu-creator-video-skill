"""Select a versioned visual card for a cut and return a screened finding to its library card."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

import library
from editorial_evidence import screen_plan_for


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


def plan_path(project):
    edit = project / 'edit.json'
    return screen_plan_for(edit) if edit.is_file() else project / 'screen-plan.json'


def choose(root, project, cut_start, card_id, shot, intent, settings, purpose):
    if not isinstance(settings, dict) or not intent.strip() or not shot.strip():
        raise ValueError('Give the shot ID, the intended visible result and a JSON object of settings.')
    item = library.load(root)[card_id]
    if not item['editorial'].get('visual'):
        raise ValueError(f'{card_id} is not a visual card.')
    path = plan_path(project)
    with library.exclusive(path.parent):
        plan = read(path)
        cuts = [c for c in plan['cuts'] if c['start'] == cut_start]
        if len(cuts) != 1:
            raise ValueError('Choose the start frame of an existing cut in the screen plan.')
        cut = cuts[0]
        if cut.get('shot', shot) != shot:
            raise ValueError('Shot ID disagrees with the existing cut.')
        selection = dict(id=card_id, revision=item['revision'], intent=intent.strip(), settings=settings)
        cards = cut.setdefault('visual_cards', [])
        entry = next((i for i, c in enumerate(cards) if c['id'] == card_id), None)
        checkout = project / 'library-uses.json'
        pinned = read(checkout).get(card_id) if checkout.is_file() else None
        if pinned and pinned['revision'] != item['revision']:
            raise ValueError(f"{card_id} is pinned to {pinned['revision']} in this project; "
                             f"the library currently offers {item['revision']}. Reconcile the revision before selecting it.")
        library.use(root, item, project, purpose)
        cut['shot'] = shot
        if entry is None:
            cards.append(selection)
        else:
            cards[entry] = selection
        library.atomic(path, library.json_text(plan))
    return selection


def checkout_inputs(project, card_id, source):
    files = source.get('files', [])
    if not files:
        raise ValueError(f'No pinned files for {card_id}')
    expected = {row['path']: row['sha256'] for row in files}
    actual = {}
    for relative in expected:
        path = library.local(project, relative)
        if not path.is_file():
            raise ValueError(f'Missing project input for {card_id}: {relative}')
        actual[relative] = library.digest(path)
    return expected, actual


def derive(project, cut_start, card_id, reason):
    if not reason.strip():
        raise ValueError('Describe what changed in this project copy')
    path = plan_path(project)
    with library.exclusive(path.parent):
        plan = read(path)
        choices = [c for cut in plan['cuts'] if cut['start'] == cut_start
                   for c in cut.get('visual_cards', []) if c['id'] == card_id]
        if len(choices) != 1:
            raise ValueError('First select the card for this cut')
        source = read(project / 'library-uses.json')[card_id]
        if source['revision'] != choices[0]['revision']:
            raise ValueError('The selected revision differs from the checkout')
        expected, actual = checkout_inputs(project, card_id, source)
        if actual == expected:
            raise ValueError('Project files still match the pinned originals')
        adaptation = dict(reason=reason.strip(), files=actual)
        choices[0]['adaptation'] = adaptation
        library.atomic(path, library.json_text(plan))
        return adaptation


def frozen_cards(edit):
    """Record only visual choices actually pinned to this project, using the plan at export time."""
    path = screen_plan_for(edit)
    if not path.is_file():
        return []
    result = []
    uses = None
    fingerprints = {}
    for cut in read(path)['cuts']:
        for choice in cut.get('visual_cards', []):
            if uses is None:
                uses = read(edit.parent / 'library-uses.json')
            card_id = choice['id']
            source = uses.get(card_id, {})
            if (not source.get('editorial', {}).get('visual') or source.get('revision') != choice['revision']
                    or source['scope'] == 'reference'
                    or (source['scope'] == 'editorial' and source.get('purpose') != 'editorial')):
                raise ValueError(f'Visual card differs from the project checkout: {card_id}')
            if not choice['intent'].strip() or not cut['shot'].strip() or not isinstance(choice['settings'], dict):
                raise ValueError(f'Invalid storyboard selection for {card_id}')
            if card_id not in fingerprints:
                fingerprints[card_id] = checkout_inputs(edit.parent, card_id, source)
            expected, actual = fingerprints[card_id]
            adaptation = choice.get('adaptation')
            if adaptation is not None:
                if not adaptation['reason'].strip() or actual != adaptation['files']:
                    raise ValueError(f'Derived project inputs changed for {card_id}; record the new adaptation')
            elif actual != expected:
                raise ValueError(f'Project inputs changed for {card_id}; restore them or use visual_case.py derive')
            frozen = dict(id=card_id, revision=choice['revision'], shot=cut['shot'],
                          start=cut['start'], end=cut['end'], intent=choice['intent'],
                          settings=choice['settings'], files=actual)
            if adaptation:
                frozen['adaptation'] = adaptation
            result.append(frozen)
    return result


def writeback(root, project, media, card_id, cut_start, lesson):
    # The candidate and its exact selection are frozen at review time. Today's plan can have changed.
    import review_bundle
    import editorial_review
    if not lesson.strip():
        raise ValueError('Describe what to repeat or change with this card on the next shot.')
    media = review_bundle.released_snapshot(media)
    ledger_path = project / editorial_review.DEFAULT_LEDGER
    sha = library.digest(media)
    bundle_sha = review_bundle.fingerprint(media)
    with editorial_review.locked(ledger_path):
        records = read(ledger_path)['rounds']
        record = next((r for r in reversed(records)
                       if r['candidate_sha256'] == sha and r['bundle_sha256'] == bundle_sha), None)
        if not record or not record.get('meeting') or not record.get('decision'):
            raise ValueError('This candidate and its companions need a completed editorial discussion and decision.')
        choices = [c for c in record.get('visual_cards', [])
                   if c['id'] == card_id and c['start'] == cut_start]
        if len(choices) != 1:
            raise ValueError('This reviewed cut has no matching frozen selection.')
        card = choices[0]
        findings = []
        for review in record['reviews']:
            for row in review['observations']:
                if (row['shot'] == card['shot'] and row['start_frame'] < card['end']
                        and card['start'] < row['end_frame']):
                    findings.append(dict(reviewer=review['reviewer'], role=review['role'],
                                         start_frame=row['start_frame'], end_frame=row['end_frame'],
                                         evidence=row['evidence']))
        if not findings:
            raise ValueError('No independent screening observation covers this card and shot.')
        decision = record['decision']
        note = dict(card_id=card_id, card_revision=card['revision'], shot=card['shot'],
                    start_frame=card['start'], end_frame=card['end'], intent=card['intent'],
                    settings=card['settings'], candidate_sha256=sha, bundle_sha256=bundle_sha,
                    round=record['number'], reviewed_on=decision['recorded_at'],
                    verdict=decision['decision'], lesson=lesson.strip(), evidence=findings,
                    decision_reason=decision['reason'])
        if card.get('adaptation'):
            note['adaptation'] = card['adaptation']
    with library.exclusive(root):
        items = library.load(root)
        if card_id not in items or not library.local(root, f'items/{card_id}/{card["revision"]}').is_dir():
            raise ValueError('Return this finding to the library that owns the selected version.')
        parent = library.local(root, f'visual-reviews/{card_id}')
        parent.mkdir(parents=True, exist_ok=True)
        key = [sha, bundle_sha, card['revision'], card['start'], note['round'], note['reviewed_on']]
        filename = hashlib.sha256(library.json_text(key).encode()).hexdigest() + '.json'
        path = parent / filename
        if path.exists() and read(path) != note:
            raise ValueError('The same review already has a different lesson. Preserve it or explicitly edit the saved note.')
        library.atomic(path, library.json_text(note))
        library.write_html(root, items)
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path)
    sub = parser.add_subparsers(dest='action', required=True)
    lessons = sub.add_parser('lessons', help='Read screened findings for a card, most recent first')
    lessons.add_argument('card')
    lessons.add_argument('--project', type=Path, default=Path.cwd())
    lessons.add_argument('--revision', help='Restrict findings to one selected card revision')
    select = sub.add_parser('select')
    derived = sub.add_parser('derive', help='Record intentionally changed files in the project copy')
    derived.add_argument('--reason', required=True)
    remember = sub.add_parser('writeback')
    for command in (select, derived, remember):
        command.add_argument('card')
        command.add_argument('--project', type=Path, default=Path.cwd())
        command.add_argument('--cut-start', required=True, type=int)
    select.add_argument('--shot', required=True)
    select.add_argument('--intent', required=True)
    select.add_argument('--settings', type=Path, help='JSON object of render settings for this shot')
    select.add_argument('--purpose', choices=('production', 'editorial'), default='production')
    remember.add_argument('media', type=Path)
    remember.add_argument('--lesson', required=True)
    args = parser.parse_args()
    project = args.project.resolve()
    root = library.find_root(args.root, project)
    if args.action == 'lessons':
        if args.card not in library.load(root):
            raise ValueError('Unknown library card: ' + args.card)
        folder = library.local(root, 'visual-reviews/' + args.card)
        notes = [read(p) for p in folder.glob('*.json')]
        if args.revision:
            notes = [note for note in notes if note['card_revision'] == args.revision]
        print(library.json_text(sorted(notes, key=lambda n: (n['reviewed_on'], n['round']), reverse=True)))
    elif args.action == 'derive':
        print(library.json_text(derive(project, args.cut_start, args.card, args.reason)))
    elif args.action == 'select':
        settings = read(args.settings) if args.settings else {}
        choice = choose(root, project, args.cut_start, args.card, args.shot, args.intent, settings, args.purpose)
        print(library.json_text(choice))
    else:
        print(writeback(root, project, args.media.absolute(), args.card, args.cut_start, args.lesson))


if __name__ == '__main__':
    try:
        main()
    except (KeyError, ValueError, OSError) as error:
        sys.exit(str(error))
