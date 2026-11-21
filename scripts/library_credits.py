"""Resolve checked-out library sources used by an edit and freeze their attribution."""
import argparse
import hashlib
import json
import re
from pathlib import Path
import shutil
import sys

import attachment_credits
from attachment_credits import interval, intersect
import motion_layers
import montage_pins
import explainer_scene_receipt
import remotion_effect_receipt
import spatial_bridges

from production_run import digest

PREFIX = 'credits/library-auto/'


def local(project, name):
    path = (project / name).resolve()
    if not path.is_relative_to(project):
        raise ValueError(f'Library credit path leaves this project: {name}')
    return path


def used_inputs(edit, timeline):
    """Use the exporter's actual visual and audio entry points, never all checkout files."""
    fps = timeline['fps']
    schedule = {shot['id']: shot for shot in timeline['shots']}
    if list(schedule) != [shot['id'] for shot in edit['shots']]:
        raise ValueError('Credit timeline and edit have different shots; prepare again')
    duration = timeline['frames'] / fps
    result = []
    for shot in edit['shots']:
        timing = schedule[shot['id']]
        start = shot.get('in', 0)
        # The exporter loops still images. Time ranges only apply to moving media.
        still = Path(shot['visual']).suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp'}
        window = None if still else [start, start + timing['frames'] / fps]
        result.append(dict(file=shot['visual'], role='picture', shot=shot['id'], range=window))
        inset = spatial_bridges.window_plan(shot,timing['frames'])
        if inset is not None:
            first = timing['start_frame'] + inset['start']
            count = inset['end']-inset['start']+1
            still = Path(inset['file']).suffix.lower() in spatial_bridges.WINDOW_STILLS
            span = None if still else [inset['source_in'],inset['source_in']+count/fps]
            result.append(dict(file=inset['file'], role='picture', shot=shot['id'], range=span,
                               frames=[first,first+count]))
        for layer in motion_layers.parse(shot, timing['frames']):
            result.append(dict(file=layer.file, role='picture', shot=shot['id'], range=None,
                               frames=[timing['start_frame'] + layer.frames[0],
                                       timing['start_frame'] + (layer.end or timing['frames'])]))
    for pin in montage_pins.plan(edit, timeline):
        result.append(dict(file=pin.file, role='picture', range=None, frames=[pin.start, pin.end]))
    result.append(dict(file='audio/narration.wav', role='narration', range=[0, duration]))
    presenter = timeline.get('presenter_source')
    if presenter:
        result.append(dict(file=presenter['file'],role='voice',
                           range=[0,presenter['original_samples']/presenter['sample_rate']]))
    for key in ('music', 'effects'):
        if edit.get(key) and float(edit.get(key + '_gain', 1)) != 0:
            result.append(dict(file=edit[key], role=key, range=[0, duration]))
    # A prepared take is played in full. Legacy edits retain their line.audio entry points.
    takes = timeline.get('voice_takes')
    if takes is not None:
        result.extend(dict(file=take['file'], role='voice', shot=take['shot'], range=None)
                      for take in takes if take['shot'] in schedule)
        for take in takes:
            if 'source_clip' in take:
                clip=take['source_clip']
                result.append(dict(file=clip['file'],role='voice',shot=take['shot'],range=[clip['start'],clip['end']]))
    else:
        result.extend(dict(file=line['audio'], role='voice', shot=shot['id'], range=None)
                      for shot in edit['shots'] for line in shot.get('lines', []) if line.get('audio'))
    return result


def previous_mapped_inputs(project):
    """Recover only declarations witnessed on this project's previous frozen render."""
    from review_bundle import latest, receipt_for
    candidate = latest(project / 'out')
    if candidate is None:
        return {}
    name = PREFIX + 'usage.json'
    manifest = json.loads(receipt_for(candidate).read_text())
    expected = manifest.get('delivery_credits', {}).get(name)
    if not expected:
        return {}
    usage = candidate.parent / name
    if digest(usage) != expected:
        raise ValueError('Previous frozen credit usage changed; restore it before recovering source mappings')
    result = {}
    for asset in json.loads(usage.read_text())['assets']:
        for occurrence in asset['occurrences']:
            if occurrence['matched_by'] not in ('explicit_map', 'mapped_sha256'):
                continue
            original = occurrence.get('mapped_file', occurrence['file'])
            files = tuple(sorted(Path(f['path']).name for f in occurrence['source_files']))
            key = (original, asset['id'], asset['revision'], files)
            result.setdefault(key, set()).add((occurrence['role'], occurrence['sha256']))
    return result


def collect(edit_path, timeline, *, pin=None, visual_cards=(), remotion_receipts=None):
    edit_path = Path(edit_path).resolve()
    project = edit_path.parent
    read_path = pin or Path
    edit = json.loads(read_path(edit_path).read_text())
    log = project / 'library-uses.json'
    mappings = edit.get('library_credits', [])
    if not isinstance(mappings, list):
        raise ValueError('edit.library_credits must be a list')
    if not log.is_file():
        if mappings or visual_cards:
            raise ValueError('Take the credited assets from the library before mapping them')
        return None
    if timeline.get('edit_sha256') and digest(read_path(edit_path)) != timeline['edit_sha256']:
        raise ValueError('Credit timeline belongs to a different edit; prepare again')
    uses = json.loads(read_path(log).read_text())
    inputs = used_inputs(edit, timeline)
    for shot, timing in zip(edit['shots'], timeline['shots']):
        if 'explainer_scene' in shot:
            inputs.extend(explainer_scene_receipt.sources(shot,timing,timeline['fps'],project,read_path))
        if 'remotion_effect' in shot:
            receipt = (remotion_receipts or {}).get(shot['id'])
            if receipt is None:
                receipt = remotion_effect_receipt.read_receipt(shot, timing, edit, timeline, project, read_path)
            inputs.extend(remotion_effect_receipt.sources(receipt, shot, timing))
    checksums = {}

    def checked(path):
        path = Path(path)
        if path not in checksums:
            checksums[path] = digest(read_path(path))
        return checksums[path]

    records = {}

    def add(asset, occurrence, files, changes, match):
        source = uses.get(asset)
        if not source:
            raise ValueError(f'Credit map references an asset absent from library-uses.json: {asset}')
        if source['scope'] == 'reference' or (source['scope'] == 'editorial' and source.get('purpose') != 'editorial'):
            raise ValueError(f'Asset is not checked out for this purpose: {asset}')
        if asset not in records:
            records[asset] = {k: source[k] for k in ('revision', 'title', 'author', 'source', 'license', 'scope')}
            records[asset].update(id=asset, occurrences=[], documents=[])
            credit = source.get('editorial', {}).get('publish_credit')
            if credit is not None:
                if not isinstance(credit, str) or not credit.strip():
                    raise ValueError(f'editorial.publish_credit must be nonempty text: {asset}')
                records[asset]['publish_credit'] = credit
        row = dict(**occurrence, source_files=files, changes=changes, matched_by=match)
        editorial = source.get('editorial', {})
        if 'file_credits' in editorial:
            # In-place and byte-identical copies retain the attachment's own clock.
            window = occurrence.get('range') if match in ('exact_path_and_sha256', 'sha256') else None
            parts = attachment_credits.selected(editorial, files, occurrence['role'], window)
            records[asset].setdefault('credit_parts', {}).update(parts)
            row['credit_sources'] = list(parts)
        if row not in records[asset]['occurrences']:
            records[asset]['occurrences'].append(row)

    file_index = {}
    hash_index = {}
    for asset, source in uses.items():
        for file in source['files']:
            path = local(project, file['path'])
            file_index.setdefault(path, []).append((asset, file))
            hash_index.setdefault(file['sha256'], []).append((asset, file))

    input_paths = {(project / entry['file']).resolve() for entry in inputs}
    parsed_maps = []
    previous = None
    warnings = []
    for mapping in mappings:
        if (not isinstance(mapping, dict) or not isinstance(mapping.get('file'), str)
                or not isinstance(mapping.get('asset'), str) or not isinstance(mapping.get('changes'), str)
                or not mapping['changes'].strip() or not isinstance(mapping.get('source_files'), list)
                or not mapping['source_files'] or not all(isinstance(v, str) for v in mapping['source_files'])):
            raise ValueError('Each library credit map needs file, asset, source_files and a changes description')
        window = interval(mapping['range'], 'Credit range') if 'range' in mapping else None
        path = (project / mapping['file']).resolve()
        declared_hash = mapping.get('input_sha256')
        if declared_hash is not None and (not isinstance(declared_hash, str)
                                          or not re.fullmatch(r'[a-f0-9]{64}', declared_hash)):
            raise ValueError('input_sha256 must be a lowercase SHA-256 digest')
        ranges = mapping.get('source_ranges', {})
        if not isinstance(ranges, dict) or set(ranges) - set(mapping['source_files']):
            raise ValueError('source_ranges must reference source_files of the same map')
        for name, span in ranges.items():
            interval(span, f'Source range in {name}')
        identities = set()
        if path not in input_paths:
            if previous is None:
                previous = previous_mapped_inputs(project)
            source = uses.get(mapping['asset'], {})
            names = tuple(sorted(Path(name).name for name in mapping['source_files']))
            key = (mapping['file'], mapping['asset'], source.get('revision'), names)
            identities = previous.get(key, set()).copy()
            if declared_hash:
                roles = {role for role, _ in identities} or {None}
                identities = {(role, declared_hash) for role in roles}
            elif not identities and path.is_relative_to(project) and path.is_file():
                identities.add((None, checked(path)))
        parsed_maps.append((path, window, mapping, identities))

    unmatched = []
    for occurrence in inputs:
        path = (project / occurrence['file']).resolve()
        if not path.is_file():
            # Old manually staged edits can omit intermediary generated voice takes.
            if occurrence['role'] in ('voice', 'narration') and not any(p == path for p, _, _, _ in parsed_maps):
                continue
            raise ValueError(f'Credited media input is missing: {occurrence["file"]}')
        checksum = checked(path)
        matching = [entry for entry in parsed_maps if entry[0] == path]
        match = 'explicit_map'
        if not matching and path not in file_index:
            matching = [entry for entry in parsed_maps
                        if any(sha == checksum and role in (None, occurrence['role'])
                               for role, sha in entry[3])]
            if len({entry[0] for entry in matching}) > 1:
                raise ValueError(f'Multiple previous credit maps match {occurrence["file"]}; map its actual name')
            match = 'mapped_sha256'
        active = [(window, mapping) for _, window, mapping, _ in matching
                  if intersect(window, occurrence['range']) is not False]
        mapped_assets = set()
        for window, mapping in active:
            if mapping.get('input_sha256', checksum) != checksum:
                raise ValueError(f'Credited derivative changed: {occurrence["file"]}; check its sources and digest')
            asset = mapping['asset']
            source = uses.get(asset)
            if not source:
                raise ValueError(f'Credit map references a missing checkout: {asset}')
            selected = []
            for name in mapping['source_files']:
                candidates = [f for f in source['files'] if f['path'] == name or Path(f['path']).name == name]
                if len(candidates) != 1:
                    raise ValueError(f'Choose one unambiguous checked-out source file: {asset} / {name}')
                file = candidates[0]
                original = local(project, file['path'])
                actual = checked(original)
                if actual != file['sha256'] and original != path:
                    raise ValueError(f'Mapped source changed from its checked-out revision: {file["path"]}')
                item = dict(path=file['path'], sha256=file['sha256'], current_sha256=actual)
                if name in mapping.get('source_ranges', {}):
                    item['source_range'] = mapping['source_ranges'][name]
                selected.append(item)
            used = dict(occurrence, range=intersect(window, occurrence['range']), sha256=checksum)
            if match == 'mapped_sha256':
                used['mapped_file'] = mapping['file']
            add(asset, used, selected, mapping['changes'].strip(), match)
            mapped_assets.add(asset)
        direct = file_index.get(path, [])
        for asset, file in direct:
            if asset in mapped_assets:
                continue
            if checksum != file['sha256']:
                raise ValueError(f'Changed library input needs an explicit credit map and changes: {occurrence["file"]}')
            add(asset, dict(occurrence, sha256=checksum), [file],
                '按时间线截取、缩放或混入本片；具体使用区间见记录。', 'exact_path_and_sha256')
        if direct or matching:
            continue
        identical = hash_index.get(checksum, [])
        owners = {asset for asset, _ in identical}
        ambiguous = len(owners) > 1
        if len(owners) == 1:
            asset = next(iter(owners))
            editorial = uses[asset].get('editorial', {})
            if 'file_credits' in editorial and len(identical) > 1:
                credits = [attachment_credits.selected(editorial, [file], occurrence['role'], occurrence['range'])
                           for _, file in identical]
                ambiguous = any(parts != credits[0] for parts in credits[1:])
            if not ambiguous:
                add(asset, dict(occurrence, sha256=checksum), [file for _, file in identical],
                    '原件复制至工程其他位置后按时间线使用。', 'sha256')
                continue
        if ambiguous:
            warnings.append(f'{occurrence["file"]} 的内容匹配到多份不同署名，请在 library_credits 中指定附件。')
        if not identical or ambiguous:
            unmatched.append(dict(occurrence, sha256=checksum, possible_assets=sorted(owners)))
            if occurrence['role'] in ('music', 'effects'):
                stale = sorted({mapping['file'] for mapped, _, mapping, identities in parsed_maps
                                if mapped not in input_paths and (
                                    any(role == occurrence['role'] for role, _ in identities)
                                    or (not mapped.is_file() and mapped.suffix.lower() in
                                        {'.wav', '.flac', '.aif', '.aiff', '.ogg', '.mp3', '.m4a'}))})
                if stale:
                    warnings.append(f'{occurrence["file"]} 可能替换了原先登记的音轨 {", ".join(stale)}。'
                                    '请核实是否仍用库内声音并更新对应映射。')

    # Existing visual-card selection is already explicit, versioned and validated upstream.
    schedule = {shot['id']: shot for shot in timeline['shots']}
    for selection in visual_cards:
        timing = schedule.get(selection['shot'])
        if timing is None or intersect([selection['start'], selection['end']],
                                     [timing['start_frame'], timing['start_frame'] + timing['frames']]) is False:
            continue
        asset = selection['id']
        if uses[asset]['revision'] != selection['revision']:
            raise ValueError(f'Visual card revision differs from checkout: {asset}')
        add(asset, dict(role='visual_card', shot=selection['shot'],
                        frames=[selection['start'], selection['end']]),
            [dict(path=name, sha256=sha) for name, sha in selection['files'].items()],
            selection.get('adaptation', {}).get('reason') or selection['intent'], 'visual_card_selection')

    for asset, record in records.items():
        for file in uses[asset]['files']:
            name = Path(file['path']).name
            text = (name + ' ' + file.get('label', '')).casefold()
            is_doc = Path(name).suffix.lower() in {'.md', '.txt', '.json', '.html'}
            if is_doc and any(term in text for term in ('credit', 'licen', 'notice', 'copying', 'source', 'attribution', '署名', '许可', '条款', '来源')):
                path = local(project, file['path'])
                if checked(path) != file['sha256']:
                    raise ValueError(f'Library attribution document changed: {file["path"]}')
                record['documents'].append(dict(file, frozen_name=asset + '/' + name))
    return dict(schema=1, edit_sha256=checked(edit_path), timeline_sha256=hashlib.sha256(
                json.dumps(timeline, ensure_ascii=False, sort_keys=True).encode()).hexdigest(),
                assets=list(records.values()), unmatched_inputs=unmatched,
                **({'warnings': warnings} if warnings else {}))


def publish_text(asset, documents):
    """Use declared attribution and statements; never copy an entire internal source guide."""
    if 'credit_parts' in asset:
        blocks = []
        for source, content in asset['credit_parts'].items():
            changes = dict.fromkeys(o['changes'] for o in asset['occurrences'] if source in o['credit_sources'])
            blocks.append(content + '\n本片改动　' + ' '.join(changes))
        return '\n\n'.join(blocks)
    declared = asset.get('publish_credit')
    statements = []
    urls = []
    for name, body in documents:
        if Path(name).suffix.lower() == '.json':
            try:
                data = json.loads(body)
            except ValueError:
                continue
            if not isinstance(data, dict):
                continue
            for key in ('required_attribution', 'attribution_text', 'non_endorsement_disclaimer'):
                value = data.get(key)
                if isinstance(value, str) and value.strip():
                    statements.append(value.strip())
            for key in ('license_page', 'license_url'):
                value = data.get(key)
                if isinstance(value, str) and value.startswith(('https://', 'http://')):
                    urls.append(value)
        if Path(name).suffix.lower() == '.md':
            marked = re.search(r'<!-- publish-credit:start -->\s*([\s\S]*?)\s*<!-- publish-credit:end -->', body)
            if not declared and marked:
                declared = marked[1].strip()
            # These headings expressly identify text intended for publication.
            section = re.search(r'^## (?:发布署名|署名文本|Attribution text)\s*\n([\s\S]*?)(?=^## |\Z)', body, re.M)
            if not declared and section:
                declared = section[1].strip()
            # Older DVIDS cards place the exact required statement in a quote under this heading.
            official = re.search(r'^## 可复制到片尾或发布说明的来源\s*\n([\s\S]*?)(?=^## |\Z)', body, re.M)
            if official:
                statements.extend(re.findall(r'^> (.+)$', official[1], re.M))
    text = declared or '\n'.join((asset['title'], '作者　' + asset['author'],
                                  '原作　' + asset['source'], '许可　' + asset['license']))
    for extra in dict.fromkeys([*urls, *statements]):
        if extra not in text:
            text += '\n' + extra
    # Timing is per timeline input, which can already be a composite or a mixed stem.
    # Keep those windows in usage.json and publish only the actual source changes.
    modifications = dict.fromkeys(occurrence['changes'] for occurrence in asset['occurrences'])
    return text + '\n本片改动　' + ' '.join(modifications)


def build(edit_path, timeline, destination, *, pin=None, visual_cards=(), remotion_receipts=None):
    """Write a fresh, dedicated delivery directory. Human-authored credits are never edited."""
    report = collect(edit_path, timeline, pin=pin, visual_cards=visual_cards, remotion_receipts=remotion_receipts)
    if report is None:
        return None
    project = Path(edit_path).resolve().parent
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    paths = {}
    lines = ['# 本片使用的个人库素材', '']
    publish = []
    for warning in report.get('warnings', []):
        print('素材署名复核：' + warning, file=sys.stderr)
        lines.extend([warning, ''])
    for asset in report['assets']:
        lines.extend(['## ' + asset['title'], ''])
        if 'credit_parts' in asset:
            for content in asset['credit_parts'].values():
                lines.extend([content, ''])
        else:
            lines.extend(['作者　' + asset['author'], '', '原作　' + asset['source'], '',
                          '使用许可　' + asset['license'], ''])
        for occurrence in asset['occurrences']:
            context = occurrence.get('shot', occurrence['role'])
            lines.append('- ' + context + '　' + occurrence['changes'])
        lines.append('')
        document_text = []
        for doc in asset['documents']:
            rel = doc['frozen_name']
            target = destination / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            source = local(project, doc['path'])
            shutil.copyfile((pin or Path)(source), target)
            if digest(target) != doc['sha256']:
                raise ValueError(f'Attribution changed while copying: {doc["path"]}')
            paths[PREFIX + rel] = target
            document_text.append((rel, target.read_text(encoding='utf-8-sig', errors='replace')))
            lines.extend(['[' + doc['label'] + '](' + rel.replace(' ', '%20') + ')', ''])
        publish.append(publish_text(asset, document_text))
    if not report['assets']:
        lines.append('本次没有按原件校验或明确映射识别出的库内素材。')
    (destination / 'sources.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    (destination / 'usage.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    text = '\n\n'.join(filter(None, publish))
    (destination / 'publish.txt').write_text(text + ('\n' if text else ''), encoding='utf-8')
    paths[PREFIX + 'sources.md'] = destination / 'sources.md'
    paths[PREFIX + 'usage.json'] = destination / 'usage.json'
    paths[PREFIX + 'publish.txt'] = destination / 'publish.txt'
    return paths


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', type=Path, default=Path.cwd())
    args = parser.parse_args()
    from visual_case import frozen_cards
    edit = args.project / 'edit.json'
    timeline = json.loads((args.project / 'timeline.json').read_text())
    print(json.dumps(collect(edit, timeline, visual_cards=frozen_cards(edit)), ensure_ascii=False, indent=2))
