"""Keep review candidates and the subtitles and covers that belong to them together."""
import argparse
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

import release_files
from project_learning import frozen_learning
from release_version import installed_version
from story_review import intent_from
from visual_case import frozen_cards
from perception_review import project_scope
from editorial_evidence import screen_plan_for, targets_for
from screen_plan import viewing_windows
from section_pace import freeze_cuts
from library_credits import PREFIX as LIBRARY_CREDIT_PREFIX, build as build_library_credits

SIDECARS = ('captions.srt', 'covers/16x9.jpg', 'covers/9x16.jpg', 'covers/3x4.jpg')


def digest(path):
    with Path(path).open('rb') as stream:
        checksum = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(chunk)
        return checksum.hexdigest()


def write_json(path, value):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, mode='w', encoding='utf8') as temp:
        temp_path = Path(temp.name)
        json.dump(value, temp, ensure_ascii=False, indent=2)
        temp.write('\n')
    try:
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def credit_checks(manifest):
    credits = manifest.get('delivery_credits', {})
    if not isinstance(credits, dict):
        raise ValueError('Invalid delivery credit inventory')
    for rel, sha in credits.items():
        path = Path(rel)
        if (path.is_absolute() or len(path.parts) < 2 or path.parts[0] != 'credits'
                or '..' in path.parts or str(path) != rel or not isinstance(sha, str) or len(sha) != 64):
            raise ValueError(f'Invalid frozen credit entry: {rel}')
    return credits


def release_companions(pinned):
    receipt = pinned.with_name(pinned.name + '.review.json')
    if not receipt.is_file():
        return ()
    manifest = json.loads(receipt.read_text())
    return (*SIDECARS, *credit_checks(manifest), receipt.name)


def released_snapshot(media):
    return release_files.snapshot(Path(media), release_companions)


def receipt_for(media):
    media = released_snapshot(media)
    release = media.with_name(media.name + '.review.json')
    return release if release.is_file() else media.parent / 'bundle.json'


def fingerprint(candidate):
    candidate = released_snapshot(candidate)
    manifest = receipt_for(candidate)
    if not manifest.is_file():
        return None
    data = json.loads(manifest.read_text())
    is_release = manifest == candidate.with_name(candidate.name + '.review.json')
    if (not is_release and data['media'] != candidate.name) or data['sha256'] != digest(candidate):
        raise ValueError('Candidate differs from its bundle')
    if set(data['sidecars']) != set(SIDECARS):
        raise ValueError('Incomplete captions or covers')
    if any(digest(manifest.parent / rel) != checksum for rel, checksum in data['sidecars'].items()):
        raise ValueError('Subtitle or cover changed after staging')
    if any(digest(manifest.parent / rel) != checksum for rel, checksum in credit_checks(data).items()):
        raise ValueError('Delivery credit changed after staging')
    if not is_release and 'timeline_sha256' in data and digest(manifest.parent / 'timeline.json') != data['timeline_sha256']:
        raise ValueError('Timeline differs from the reviewed candidate')
    return digest(manifest)


def legacy_project_scope(media, manifest):
    """Use only the project that owns the reviewed file, with its matching registered timeline."""
    media = media.resolve()
    if media.name == 'candidate.mp4' and media.parent.parent.name == 'review' and media.parent.parent.parent.name == 'out':
        project = media.parent.parent.parent.parent
    elif receipt_for(media) == media.with_name(media.name + '.review.json'):
        directory = media.parent
        if directory.parent.name == release_files.STORE:
            directory = directory.parent.parent
        if directory.name != 'out':
            return None
        project = directory.parent
    else:
        return None
    timeline, edit = project / 'timeline.json', project / 'edit.json'
    if not edit.is_file() or not timeline.is_file() or digest(timeline) != manifest.get('timeline_sha256'):
        return None
    declared_edit = json.loads(timeline.read_text()).get('edit_sha256')
    if declared_edit and digest(edit) != declared_edit:
        return None
    plan = screen_plan_for(edit)
    if not plan.is_file():
        return None
    return dict(targets=targets_for(edit), origin='matched_legacy_project', screen_plan_sha256=digest(plan))


def source_scope(media, manifest):
    if 'editorial_targets_version' in manifest or 'editorial_targets' in manifest:
        if manifest.get('editorial_targets_version', 1) != 1:
            raise ValueError('Unsupported editorial_targets_version in the candidate bundle')
        return dict(targets=manifest['editorial_targets'], origin='bundle')
    if manifest:
        return legacy_project_scope(media, manifest) or dict(
            targets=[], origin='legacy_unindexed',
            next_step='Re-stage with the matching edit and timeline, or inventory the source-bearing shots manually.')
    edit = Path('edit.json')
    if media.resolve().is_relative_to(Path.cwd()) and edit.is_file():
        return dict(targets=targets_for(edit), origin='local_project')
    return dict(targets=[], origin='manual')


def freeze_credits(edit, sidecars, old, destination, automatic=None):
    explicit = json.loads(edit.read_text()).get('delivery_credits') if edit else None
    files = {}
    if explicit is None and old:
        for rel, sha in credit_checks(old).items():
            if automatic is None or not rel.startswith(LIBRARY_CREDIT_PREFIX):
                files[rel] = (sidecars / rel, sha)
    elif edit:
        if explicit is None:
            defaults = ('sources.md', 'visuals-sources.md', 'sound-sources.md', 'credits.md',
                        'visual-sources.json', 'picture-sources.json', 'sound/sources.json',
                        'sound/loop/percussion-sources.json', 'motion/sources.md')
            explicit = [rel for rel in defaults if (edit.parent / rel).is_file()]
        if not isinstance(explicit, list) or not all(isinstance(rel, str) for rel in explicit):
            raise ValueError('delivery_credits must list project-relative file names')
        for rel in explicit:
            path = Path(rel)
            source = (edit.parent / path).resolve()
            if (not path.parts or path.is_absolute() or '..' in path.parts or str(path) != rel
                    or not source.is_relative_to(edit.parent.resolve()) or not source.is_file()):
                raise ValueError(f'Invalid project delivery credit: {rel}')
            files['credits/' + rel] = (source, None)
    # Explicit human delivery lists may change while the same movie is restaged.
    # Keep its frozen automatic attribution unless this is a freshly rendered edit.
    if automatic is None:
        for rel, sha in credit_checks(old).items():
            if rel.startswith(LIBRARY_CREDIT_PREFIX):
                files[rel] = (sidecars / rel, sha)
    else:
        for rel, source in automatic.items():
            if not rel.startswith(LIBRARY_CREDIT_PREFIX) or '..' in Path(rel).parts:
                raise ValueError(f'Invalid automatic library credit path: {rel}')
            files[rel] = (source, digest(source))
    checks = {}
    for rel, (source, expected) in files.items():
        target = destination / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        checks[rel] = digest(target)
        if expected and checks[rel] != expected:
            raise ValueError(f'Previously staged delivery credit changed: {rel}')
    return checks


def stage(media, sidecars, *, out=Path('out'), technical=None, timeline=None, edit=None,
          render_inputs=None, render_skill_version=None, before_commit=None, automatic_credits=None):
    media, sidecars, out = Path(media), Path(sidecars), Path(out)
    pinned = released_snapshot(media)
    if pinned != media and sidecars.resolve() == media.parent.resolve():
        sidecars = pinned.parent
    media = pinned
    store = out / 'review'
    store.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.candidate-', dir=store) as tmp:
        root = Path(tmp) / 'bundle'
        (root / 'covers').mkdir(parents=True)
        # Copy rather than link mutable input files into a reviewed candidate.
        shutil.copyfile(media, root / 'candidate.mp4')
        sha = digest(root / 'candidate.mp4')
        checks = {}
        for rel in SIDECARS:
            shutil.copyfile(sidecars / rel, root / rel)
            checks[rel] = digest(root / rel)
        receipt = dict(media='candidate.mp4', sha256=sha, sidecars=checks, staged_with_skill=installed_version())
        old_receipt = sidecars / 'bundle.json'
        if not old_receipt.is_file():
            old_receipt = sidecars / (media.name + '.review.json')
        if not old_receipt.is_file():
            old_receipt = sidecars / 'final.mp4.review.json'
        old = json.loads(old_receipt.read_text()) if old_receipt.is_file() else {}
        # When a signed MP4 is restaged with *new* loose sidecars, its own receipt
        # still identifies the original renderer and the frozen credit files.
        source_receipt = receipt_for(media)
        if source_receipt.is_file() and source_receipt != old_receipt:
            source_context = json.loads(source_receipt.read_text())
            if source_context.get('sha256') == sha:
                old, old_receipt = source_context, source_receipt
        # Restaging can change a subtitle or cover without rerendering the MP4.
        # Preserve the rendering identity for the same bytes; staging has its own version.
        origin = old if old.get('sha256') == sha else {}
        same_timeline = bool(origin) and (timeline is None or origin.get('timeline_sha256') == digest(timeline))
        if origin.get('viewing_windows') and same_timeline:
            receipt['viewing_windows'] = origin['viewing_windows']
        if origin.get('rhythm_cuts') and same_timeline and timeline:
            receipt['rhythm_cuts'] = origin['rhythm_cuts']
        old_components = origin.get('component_motion_windows') or origin.get('footage_source_checks', [])
        if old_components and same_timeline and (timeline or any(p.get('mode') == 'footage' for p in old_components)):
            receipt['component_motion_windows'] = old_components
        for field, value in (('render_inputs', render_inputs), ('render_skill_version', render_skill_version)):
            if value is not None:
                receipt[field] = value
            elif field in origin:
                receipt[field] = origin[field]
        if edit:
            edit_path = Path(edit)
            if not edit_path.is_file():
                raise FileNotFoundError(edit_path)
        elif timeline and (Path(timeline).parent / 'edit.json').is_file():
            edit_path = Path(timeline).parent / 'edit.json'
        elif media.resolve().is_relative_to(Path.cwd()) and Path('edit.json').is_file():
            edit_path = Path('edit.json')
        else:
            edit_path = None
        if edit_path:
            visual_cards = frozen_cards(edit_path)
            learning = frozen_learning(edit_path.parent)
            scope = project_scope(edit_path)
            plan_path = screen_plan_for(edit_path)
            if not plan_path.is_file():
                scope.setdefault('alignment', [])  # Hand-built edit; inspect the actual cut for related elements.
            else:
                plan = json.loads(plan_path.read_text())
                if not receipt.get('component_motion_windows') and (same_timeline or not timeline):
                    # A standalone staging call can have a plan but no detached timeline.
                    # Freeze only explicitly opted-in footage cuts in that case.
                    footage_cuts = [c for c in plan.get('cuts', [])
                                    if any(p.get('mode') == 'footage' for p in c.get('motion_probes', []))]
                    if footage_cuts:
                        from component_motion import probes_from_plan
                        total = json.loads(Path(timeline).read_text())['frames'] if timeline else max(c['end'] for c in footage_cuts)
                        receipt['component_motion_windows'] = probes_from_plan({'cuts': footage_cuts}, total)
            if plan_path.is_file() and not same_timeline:
                windows = viewing_windows(plan)
                if timeline:
                    total = json.loads(Path(timeline).read_text())['frames']
                    from component_motion import probes_from_plan
                    probes = probes_from_plan(plan, total)
                    if probes:
                        receipt['component_motion_windows'] = probes
                    cuts = freeze_cuts(plan, total)
                    if cuts:
                        receipt['rhythm_cuts'] = cuts
                if windows:
                    if timeline and max(row['end'] for row in windows) > total:
                        raise ValueError('A planned observation extends beyond the reviewed timeline')
                    receipt['viewing_windows'] = windows
            targets = targets_for(edit_path)
        else:
            same_companions = origin.get('sidecars') == checks
            if timeline:
                same_companions = same_companions and origin.get('timeline_sha256') == digest(timeline)
            learning = origin.get('project_learning') if same_companions else None
            visual_cards = origin.get('visual_cards', [])
            scope = origin.get('perception_checks', {}).copy()
            if not origin.get('alignment_scope_version'):
                scope.setdefault('alignment', [])  # No plan available; infer nothing from an old omission.
            targets = origin.get('editorial_targets', [])
            if origin and 'editorial_targets' not in origin:
                owner = old_receipt.parent / ('candidate.mp4' if old_receipt.name == 'bundle.json' else old_receipt.name.removesuffix('.review.json'))
                if owner.is_file() and old.get('timeline_sha256') and timeline and digest(timeline) == old['timeline_sha256']:
                    recovered = legacy_project_scope(owner, old)
                    if recovered:
                        targets = recovered['targets']
        component_windows = receipt.get('component_motion_windows', [])
        if any(p.get('mode') == 'footage' for p in component_windows):
            from footage_audit import freeze_footage_sources
            frozen = freeze_footage_sources(
                component_windows, edit_path.parent if edit_path else None,
                reuse_frozen=same_timeline)
            if timeline:
                receipt['component_motion_windows'] = frozen
            else:
                # Source eligibility can be checked without binding these screen coordinates to an edit clock.
                receipt.pop('component_motion_windows', None)
                receipt['footage_source_checks'] = [p for p in frozen if p.get('mode') == 'footage']
        intent = intent_from(json.loads(edit_path.read_text())) if edit_path else None
        intent = intent or intent_from(origin)
        if intent:
            receipt['story_intent'] = intent
        if automatic_credits is None and not origin and edit_path and timeline:
            automatic_credits = build_library_credits(
                edit_path, json.loads(Path(timeline).read_text()), Path(tmp)/'library-credits',
                visual_cards=visual_cards)
        credits = freeze_credits(edit_path, old_receipt.parent, origin, root, automatic_credits)
        if credits:
            receipt['delivery_credits'] = credits
        if learning:
            receipt['project_learning'] = learning
        if visual_cards:
            receipt['visual_cards'] = visual_cards
        if scope:
            receipt['perception_checks'] = scope
        receipt['alignment_scope_version'] = 1
        receipt['editorial_targets_version'] = 1
        receipt['editorial_targets'] = targets
        if timeline:
            shutil.copyfile(timeline, root / 'timeline.json')
            receipt['timeline_sha256'] = digest(root / 'timeline.json')
        write_json(root / 'bundle.json', receipt)
        if technical:
            shutil.copyfile(technical, root / 'technical.json')
        bundle_id = fingerprint(root / 'candidate.mp4')
        # The same encoded picture can have a different external subtitle file or cover.
        dest = store / (sha[:16] + '-' + bundle_id[:16])
        if before_commit is not None:
            before_commit()
        try:
            os.rename(root, dest)
        except OSError:
            if not dest.is_dir() or fingerprint(dest / 'candidate.mp4') != bundle_id:
                raise
    return dest / 'candidate.mp4'


def set_latest(candidate):
    candidate = Path(candidate)
    store = candidate.parent.parent
    write_json(store / 'latest.json', {'candidate': str(candidate.relative_to(store)), 'sha256': digest(candidate)})


def latest(out=Path('out')):
    store = Path(out) / 'review'
    pointer = store / 'latest.json'
    if not pointer.exists():
        return None
    entry = json.loads(pointer.read_text())
    path = (store / entry['candidate']).resolve()
    if not path.is_relative_to(store.resolve()) or digest(path) != entry['sha256']:
        raise ValueError('Invalid review candidate pointer')
    return path


def promote(candidate, output, approved_sha, approved_bundle_sha=None):
    """Publish only after the caller checks the editorial decision under its lock."""
    candidate, output = released_snapshot(candidate), Path(output)
    if output.resolve() == candidate.resolve():
        raise ValueError('Choose a release destination separate from the candidate')
    if fingerprint(candidate) != approved_bundle_sha:
        raise ValueError('Bundle changed after sign-off')
    receipt = receipt_for(candidate)
    manifest = json.loads(receipt.read_text()) if approved_bundle_sha else {}
    checks = {**manifest.get('sidecars', {}), **credit_checks(manifest)}
    release_files.promote_group(candidate, output, approved_sha, approved_bundle_sha, checks, receipt, release_companions)
    return output


def main():
    p = argparse.ArgumentParser(description='Stage a localized revision with its exact captions and covers')
    p.add_argument('media', type=Path)
    p.add_argument('--sidecars', type=Path, required=True, help='Directory with captions.srt and covers/')
    p.add_argument('--out', type=Path, default=Path('out'))
    p.add_argument('--timeline', type=Path, help='Timeline associated with this revision')
    p.add_argument('--edit', type=Path, help='Edit with music, effects and associated screen plan')
    args = p.parse_args()
    timeline = args.timeline or next((t for t in (args.sidecars/'timeline.json', Path('timeline.json')) if t.is_file()), None)
    print(stage(args.media, args.sidecars, out=args.out, timeline=timeline, edit=args.edit))


if __name__ == '__main__':
    main()
