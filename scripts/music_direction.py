"""Render shot-relative gain moves on the music bus before dialogue ducking and the final mix."""
import json
import math
from pathlib import Path

from location_sound import extract_clip, location_cues
from production_run import digest
from sound_clock import RATE, cue_sample


def has_effects(edit):
    return ((bool(edit.get('effects') or edit.get('sfx_plan')) and edit.get('effects_gain', 1) != 0)
            or any('location_sound' in shot for shot in edit.get('shots', [])))


def resolve_cues(edit, timeline, pin=Path):
    rows = edit.get('music_cues', [])
    if not isinstance(rows, list):
        raise ValueError('music_cues must be a list of shot-relative gain moves')
    if not rows:
        return []
    import numpy as np

    if not edit.get('music') or edit.get('music_gain', 1) == 0:
        raise ValueError('Supply an audible music stem for music_cues')
    shots = {shot['id']: shot for shot in timeline['shots']}
    fps = timeline['fps']
    length = round(timeline['frames'] * RATE / fps)
    resolved = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('shot'), str) or row['shot'] not in shots:
            raise ValueError('Each music cue must reference a shot in the prepared timeline')
        timing = shots[row['shot']]
        offset, fade, gain = row.get('offset', 0), row.get('fade', 0), row.get('gain')
        if not all(type(x) in (int, float) and math.isfinite(x) and x >= 0 for x in (offset, fade, gain)):
            raise ValueError('Music cue offset, fade and gain must be finite nonnegative numbers')
        if offset > timing['frames'] / fps or gain > np.finfo(np.float32).max:
            raise ValueError(f'Music cue outside shot or gain range: {row}')
        start = cue_sample(row, edit, timeline, timing, pin)
        end = start + round(fade * RATE)
        if not 0 <= start < length or end > length:
            raise ValueError(f'Music cue starts or ends outside the film: {row}')
        resolved.append(dict(row, start_sample=start, end_sample=end))
    resolved.sort(key=lambda row: row['start_sample'])
    for previous, following in zip(resolved, resolved[1:]):
        if (previous['end_sample'] > following['start_sample']
                or previous['start_sample'] == following['start_sample']):
            raise ValueError('Music cue fades overlap. Choose one gain at each instant.')
    return resolved


def write_envelope(path, resolved, samples):
    """Stream a mono float control signal. A stereo pan in the mixer copies it at unity."""
    import numpy as np

    def segment(stream, start, end, initial, target):
        for pos in range(start, end, 65536):
            stop = min(end, pos + 65536)
            if initial == target:
                block = np.full(stop - pos, initial, dtype='<f4')
            else:
                fraction = (np.arange(pos, stop, dtype=np.float64) - start) / (end - start)
                block = (initial + fraction * (target - initial)).astype('<f4')
            stream.write(block.tobytes())

    with path.open('wb') as stream:
        cursor, current = 0, 1.
        for cue in resolved:
            start, end, target = cue['start_sample'], cue['end_sample'], cue['gain']
            segment(stream, cursor, start, current, current)
            segment(stream, start, end, current, target)
            cursor, current = end, target
        segment(stream, cursor, samples, current, current)


def mix_stems(ff, folder, edit, timeline, pin, cues, *, audio_report=None):
    tracks = ['-i', pin('audio/narration.wav')]
    filters = ['[0:a]aformat=sample_rates=48000:channel_layouts=stereo[vo]']
    names = ['[vo]']
    generated = None
    plan = edit.get('sfx_plan')
    if plan and edit.get('effects'):
        raise ValueError('Choose sfx_plan for generated effects or effects for a prepared stem')
    if plan and edit.get('effects_gain', 1) != 0:
        from soundmix import render_effects
        events = json.loads(pin(plan).read_text())
        if not events:
            raise ValueError('sfx_plan must name the effects to mix')
        generated = folder/'directed-effects.wav'
        effects = render_effects(events, edit, timeline, generated, pin)
        if audio_report is not None:
            audio_report['sfx_cues'] = effects
    for key in ('music', 'effects'):
        stem = generated if key == 'effects' and generated else None
        if edit.get(key) or stem:
            index = len(names)
            tracks += ['-i', stem or pin(edit[key])]
            gain = float(edit.get(key + '_gain', 1))
            filters.append(f'[{index}:a]aformat=sample_rates=48000:channel_layouts=stereo,volume={gain}[{key}]')
            names.append(f'[{key}]')
    if cues:
        envelope = folder/'music-gain.f32'
        write_envelope(envelope, cues, round(timeline['frames'] * RATE / timeline['fps']))
        index = len(names)
        tracks += ['-f', 'f32le', '-ar', RATE, '-ac', 1, '-i', envelope]
        filters.extend([f'[{index}:a]pan=stereo|c0=c0|c1=c0[control]',
                        '[music][control]amultiply[directed]'])
        names[1] = '[directed]'
    ducking = edit.get('music_ducking', 'sidechain')
    if ducking not in ('sidechain', 'prebalanced'):
        raise ValueError('music_ducking must be sidechain or prebalanced')
    if edit.get('music') and ducking == 'sidechain':
        filters.extend(['[vo]asplit=2[speech][key]',
                        names[1] + '[key]sidechaincompress=threshold=0.028:ratio=3:attack=18:release=240[bed]'])
        names[:2] = ['[speech]', '[bed]']
    locations = location_cues(edit, timeline)
    if locations and audio_report is not None:
        checksums = {name: digest(pin(name)) for name in dict.fromkeys(c['file'] for c in locations)}
        for cue in locations:
            cue['source_sha256'] = checksums[cue['file']]
        audio_report['location_sound'] = locations
    # An envelope adds an audio input without adding a bus to names.
    index = len(names) + bool(cues)
    for i, cue in enumerate(locations):
        tracks.extend(['-i', extract_clip(ff, folder, cue, i, pin)])
        label = f'[location{i}]'
        start = cue['programme_samples'][0]
        filters.append(f'[{index+i}:a]adelay={start}S:all=1{label}')
        names.append(label)
    filters.append(''.join(names) + f'amix=inputs={len(names)}:duration=first:normalize=0[mix]')
    mix = folder/'mix.wav'
    ff(*tracks, '-filter_complex', ';'.join(filters), '-map', '[mix]', '-c:a', 'pcm_f32le', mix)
    return mix


def normalization_filter(measured):
    keys = [('measured_I','input_i'), ('measured_TP','input_tp'), ('measured_LRA','input_lra'),
            ('measured_thresh','input_thresh'), ('offset','target_offset')]
    return 'loudnorm=I=-16:TP=-1.5:LRA=9:linear=true:' + ':'.join(f'{tag}={measured[key]}' for tag,key in keys)
