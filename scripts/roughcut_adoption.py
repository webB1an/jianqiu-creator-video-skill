"""Apply a selected source assembly while retaining the independently recorded narration clock."""
import json
import os
from fractions import Fraction
from pathlib import Path

from picture_roughcut import roll_cut
from production_run import atomic_json, digest


def narration_shots(edit):
    """Source order for the single narration track, including its authored silences."""
    picture_edit = edit.get('picture_edit')
    if not picture_edit:
        return edit['shots']
    shots = {s['id']: s for s in edit['shots']}
    ids = picture_edit['narration_order']
    if len(set(ids)) != len(ids) or any(key not in shots for key in ids):
        raise ValueError('The saved narration order no longer matches the available shots')
    for shot in shots.values():
        if shot['id'] not in ids and (shot.get('lines') or shot.get('lead') or shot.get('tail')):
            raise ValueError(f"Add {shot['id']} to picture_edit.narration_order to include its audio")
    return [shots[key] for key in ids]


def align_picture(edit, total, fps):
    result = []
    cursor = 0
    for shot in edit['shots']:
        frames = shot.get('picture_frames')
        if type(frames) is not int or frames < 1:
            raise ValueError(f"{shot['id']}: expected a positive adopted picture_frames")
        result.append(dict(id=shot['id'], start_frame=cursor, frames=frames))
        cursor += frames
    if cursor != total or fps != edit['picture_edit']['fps']:
        raise ValueError(f'Adopted picture occupies {cursor} frames but the sound clock has {total}; update the rough cut to this recording')
    return result


def picture_sample_clock(seek, fps, origin=None):
    """Same frame selection as picture_roughcut.make_segment(), including VFR in-points."""
    origin = seek if origin is None else origin
    phase = (seek - origin) * fps
    if abs(phase - round(phase)) > 1e-6:
        origin = seek  # An editor chose a new subframe in-point after adopting the assembly.
    offset = round((seek - origin) * fps)
    preroll = max(0, min(seek, origin) - 1)
    pts = f'setpts=PTS-{origin-preroll:.9f}/TB'
    start_time = min(0, offset) / fps
    clock = f'{pts},fps={fps}:round=up:start_time={start_time:.9f}'
    if offset > 0:
        clock += f',trim=start_frame={offset}'
    return preroll, clock + ',setpts=PTS-STARTPTS'


def adopt(receipt_path, plan_path, version, pins, guide_dir=None):
    receipt = json.loads(pins.pin(receipt_path).read_text())
    plan = json.loads(pins.pin(plan_path).read_text())
    if digest(pins.pin(plan_path)) != receipt['plan_sha256']:
        raise ValueError('The selected rough cut was built from a different plan')
    selected = receipt['versions'].get(version)
    if not selected or selected.get('file') != version + '.mp4':
        raise ValueError('Select an existing first or alternate version from the receipt')
    preview = receipt_path.parent / selected['file']
    pins.expect(preview, selected['sha256'])
    edit_path = Path('edit.json')
    edit = json.loads(pins.pin(edit_path).read_text())
    timing_dir = guide_dir or Path('.')
    timing = json.loads(pins.pin(timing_dir/'timeline.json').read_text())
    if timing['edit_sha256'] != pins.hashes[pins.key(edit_path)]:
        raise ValueError('Prepare the current narration before adopting its picture cuts')
    fps = plan['fps']
    if fps != receipt['fps'] or fps != timing['fps']:
        raise ValueError('Match the source assembly and prepared programme fps')
    guide = receipt.get('soundtrack')
    if guide:
        pins.expect(plan_path.parent / guide['file'], guide['sha256'])
    cuts = plan['cuts']
    first_cuts = {cut['id']: cut for cut in cuts}
    origins = {key: cut.get('in', 0) for key, cut in first_cuts.items()}
    if 'cut_shift' in receipt:
        shift = receipt['cut_shift']
        alternate = roll_cut(cuts, fps, shift['incoming'], shift['frames'])
        origins = {cut['id']: min(origins[cut['id']], cut.get('in', 0)) for cut in alternate}
        if version == 'alternate':
            cuts = alternate
    by_id = {cut['id']: cut for cut in cuts}
    ids = [row['id'] for row in selected['cuts']]
    if (not ids or len(set(ids)) != len(ids) or set(ids) != set(by_id)
            or (version == 'first' or 'cut_shift' in receipt) and ids != [c['id'] for c in cuts]):
        raise ValueError('The selected cut order differs from its source plan')
    originals = {shot['id']: shot for shot in edit['shots']}
    covered = [i for i, shot in enumerate(edit['shots']) if shot['id'] in by_id]
    if not covered or covered != list(range(covered[0], covered[-1] + 1)):
        raise ValueError('Match one contiguous run of existing shot IDs in the source assembly')
    schedule = {t['id']: t for t in timing['shots']}
    first, last = covered[0], covered[-1]+1
    if guide_dir and (first != 0 or last != len(edit['shots'])):
        raise ValueError('A new narration guide requires a full programme rough cut covering the current pictures')
    programme_start = 0 if guide_dir else timing['shots'][first]['start_frame']
    available = timing['frames'] if guide_dir else sum(schedule[s['id']]['frames'] for s in edit['shots'][first:last])
    new, generated = [], []
    cursor = 0
    for row in selected['cuts']:
        key = row['id']
        cut = by_id[key]
        frames = cut['end'] - cut['start']
        if (type(frames) is not int or frames < 1 or row['start'] != cursor or row['end'] != cursor+frames
                or row['frames'] != frames or row['visual'] != cut['visual'] or row.get('crop') != cut.get('crop')
                or Fraction(str(row.get('in', 0))) != Fraction(str(cut.get('in', 0)))):
            raise ValueError(f'{key}: selected source window differs from the original assembly')
        path = (plan_path.parent / cut['visual']).resolve(strict=True)
        pins.expect(path, row['sha256'])
        shot = dict(originals.get(key, {'id':key, 'lines':[]}))
        live_effect = 'remotion_effect' in shot or 'explainer_scene' in shot
        if live_effect:
            if Path(shot['visual']).resolve() != path or cut.get('in', 0):
                raise ValueError(f'{key}: keep a live effect at its own frame zero or use an independent rendered source')
            generated.append(key)
            # These receipts bind the rebuilt shot to the new programme clock and project inputs.
            shot.pop('roughcut_source_sha256', None)
        else:
            shot['roughcut_source_sha256'] = row['sha256']
        shot.update(visual=os.path.relpath(path), picture_frames=frames, fit='contain')
        shot.pop('roughcut_clock_in', None)
        if origins[key] != cut.get('in', 0):
            shot['roughcut_clock_in'] = origins[key]
        for field in ('in', 'crop'):
            shot.pop(field, None)
            if field in cut:
                shot[field] = cut[field]
        if new or first == 0:
            shot.pop('transition', None)  # The selected source assembly uses straight cuts.
        if 'reveal_word' in cut:
            if not cut.get('watch') or not cut.get('discovery'):
                raise ValueError(f'{key}: the selected edit must retain its evidence watch interval and discovery')
            reading = {field: cut[field] for field in
                       ('discovery','watch','reveal_word','next_information','min_read_frames',
                        'min_preview_frames','readback') if field in cut}
            if type(reading.get('next_information')) is int:
                original_frame = first_cuts[key]['start'] + reading['next_information']
                reading['next_information'] = original_frame - cut['start']
                if not 0 <= reading['next_information'] <= frames:
                    raise ValueError(f'{key}: next_information falls outside the selected cut; use a programme word cue')
            shot['evidence_read'] = reading
        new.append(shot)
        cursor += frames
    if cursor != receipt['frames'] or cursor != available:
        raise ValueError(f'Selected assembly occupies {cursor} frames; the same prepared span occupies {available}. Align its boundaries before adoption.')
    ordered = [s['id'] for s in narration_shots(edit)]
    previous = edit.get('picture_edit', {})
    for shot in [*edit['shots'][:first], *edit['shots'][last:]]:
        shot['picture_frames'] = schedule[shot['id']]['frames']
    metadata = dict(previous, fps=fps, narration_order=ordered)
    if guide:
        if programme_start and (plan_path.parent / guide['file']).resolve() == Path('audio/narration.wav').resolve():
            raise ValueError('Trim the rough-cut guide to the programme span for a mid-programme assembly')
        expected = timing['prepared_files']['audio/narration.wav']
        pins.expect(timing_dir/'audio/narration.wav', expected)
        metadata['guided_narration_sha256'] = expected
    elif first == 0 and last == len(edit['shots']):
        metadata.pop('guided_narration_sha256', None)
    metadata['selection'] = dict(version=version, receipt=os.path.relpath(receipt_path),
                                 receipt_sha256=pins.hashes[pins.key(receipt_path)], preview_sha256=selected['sha256'])
    edit.update(shots=[*edit['shots'][:first], *new, *edit['shots'][last:]], picture_edit=metadata)
    align_picture(edit, timing['frames'], fps)
    pins.verify()
    atomic_json(edit_path, edit)
    result = dict(version=version, cuts=ids, start_frame=programme_start, frames=cursor)
    if generated:
        result['regenerate_after_prepare'] = generated
    return result
