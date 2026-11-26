"""Render shot-relative gain moves, vocal polish, music pocket EQ and dialogue ducking for the final mix."""
import json
import math
from pathlib import Path

from location_sound import extract_clip, location_cues
from production_run import digest
from sound_clock import RATE, cue_sample


VOICE_POLISH_PRESETS = {
    'off': '',
    'clean': (
        'highpass=f=75,'
        'equalizer=f=240:t=q:w=1.2:g=-1.5,'
        'deesser=i=0.22:m=0.45:f=0.5:s=o,'
        'alimiter=limit=0.95:attack=4:release=40'
    ),
    'broadcast': (
        'highpass=f=80,'
        'equalizer=f=260:t=q:w=1.1:g=-2.0,'
        'equalizer=f=3200:t=q:w=1.2:g=1.9,'
        'deesser=i=0.35:m=0.5:f=0.5:s=o,'
        'acompressor=threshold=0.07:ratio=2.4:attack=12:release=140:makeup=1.25:knee=2.5,'
        'alimiter=limit=0.94:attack=3:release=40'
    ),
    'studio-master': (
        'highpass=f=80,'
        'equalizer=f=255:t=q:w=1.15:g=-2.2,'
        'equalizer=f=3100:t=q:w=1.2:g=1.8,'
        'deesser=i=0.38:m=0.5:f=0.5:s=o,'
        'acompressor=threshold=0.068:ratio=2.5:attack=11:release=135:makeup=1.26:knee=2.6,'
        'aecho=0.95:0.92:14|23:0.042|0.022,'
        'alimiter=limit=0.94:attack=3:release=40'
    ),
    'warm': (
        'highpass=f=72,'
        'equalizer=f=165:t=q:w=1.0:g=1.6,'
        'equalizer=f=2900:t=q:w=1.2:g=1.3,'
        'deesser=i=0.28:m=0.48:f=0.5:s=o,'
        'acompressor=threshold=0.08:ratio=2.1:attack=15:release=160:makeup=1.20:knee=2.8,'
        'alimiter=limit=0.94:attack=3:release=40'
    ),
}

DUCKING_PRESETS = {
    'sidechain': dict(threshold=0.028, ratio=3.0, attack=18.0, release=240.0),
    'gentle': dict(threshold=0.032, ratio=2.3, attack=24.0, release=340.0),
    'firm': dict(threshold=0.022, ratio=4.2, attack=12.0, release=210.0),
}


def has_effects(edit):
    return ((bool(edit.get('effects') or edit.get('sfx_plan')) and edit.get('effects_gain', 1) != 0)
            or bool(edit.get('ambience'))
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
        curve = row.get('curve', 'smooth')
        if curve not in ('smooth', 'linear'):
            raise ValueError('Music cue curve must be smooth or linear')
        if 'micro_silence' in row:
            hold = row['micro_silence']
            floor_gain = row.get('floor', 0.02)
            recover_gain = row.get('recover', 1.0)
            fade_down = row.get('fade_down', 0.05)
            fade_up = row.get('fade_up', 0.06)
            if not all(type(x) in (int, float) and math.isfinite(x) for x in (hold, floor_gain, recover_gain, fade_down, fade_up)):
                raise ValueError(f'Invalid micro_silence parameters: {row}')
            if not 0.05 <= hold <= 2.0 or not 0 <= floor_gain <= 0.4 or recover_gain < 0 or fade_down <= 0 or fade_up <= 0:
                raise ValueError(f'micro_silence out of safe range (0.05–2.0s hold): {row}')
            anchor = cue_sample(row, edit, timeline, timing, pin)
            fd_n = max(1, round(fade_down * RATE))
            hold_n = max(1, round(hold * RATE))
            fu_n = max(1, round(fade_up * RATE))
            dip_start = max(0, anchor - fd_n)
            dip_end = dip_start + fd_n
            rec_start = dip_end + hold_n
            rec_end = rec_start + fu_n
            if not (0 <= dip_start < dip_end < rec_start < rec_end <= length):
                raise ValueError(f'micro_silence window lies outside the film: {row}')
            resolved.append(dict(row, gain=float(floor_gain), fade=float(fade_down), curve=curve,
                                 micro_silence_role='dip', start_sample=dip_start, end_sample=dip_end))
            resolved.append(dict(row, gain=float(recover_gain), fade=float(fade_up), curve=curve,
                                 micro_silence_role='recover', start_sample=rec_start, end_sample=rec_end))
            continue
        offset, fade, gain = row.get('offset', 0), row.get('fade', 0), row.get('gain')
        if not all(type(x) in (int, float) and math.isfinite(x) and x >= 0 for x in (offset, fade, gain)):
            raise ValueError('Music cue offset, fade and gain must be finite nonnegative numbers')
        if offset > timing['frames'] / fps or gain > np.finfo(np.float32).max:
            raise ValueError(f'Music cue outside shot or gain range: {row}')
        start = cue_sample(row, edit, timeline, timing, pin)
        end = start + round(fade * RATE)
        if not 0 <= start < length or end > length:
            raise ValueError(f'Music cue starts or ends outside the film: {row}')
        resolved.append(dict(row, curve=curve, start_sample=start, end_sample=end))
    resolved.sort(key=lambda row: row['start_sample'])
    for previous, following in zip(resolved, resolved[1:]):
        if (previous['end_sample'] > following['start_sample']
                or previous['start_sample'] == following['start_sample']):
            raise ValueError('Music cue fades overlap. Choose one gain at each instant.')
    return resolved


def write_envelope(path, resolved, samples):
    """Stream a mono float control signal with perceptual smooth-power or linear transitions."""
    import numpy as np

    def segment(stream, start, end, initial, target, curve='smooth'):
        for pos in range(start, end, 65536):
            stop = min(end, pos + 65536)
            if initial == target:
                block = np.full(stop - pos, initial, dtype='<f4')
            else:
                fraction = (np.arange(pos, stop, dtype=np.float64) - start) / (end - start)
                if curve == 'linear':
                    vals = initial + fraction * (target - initial)
                else:
                    # Raised-cosine in square-root amplitude domain: perceptually even and C1 smooth.
                    s = 0.5 * (1.0 - np.cos(np.pi * fraction))
                    vals = (np.sqrt(initial) + s * (np.sqrt(target) - np.sqrt(initial))) ** 2
                block = vals.astype('<f4')
            stream.write(block.tobytes())

    with path.open('wb') as stream:
        cursor, current = 0, 1.
        for cue in resolved:
            start, end, target = cue['start_sample'], cue['end_sample'], cue['gain']
            segment(stream, cursor, start, current, current, 'linear')
            segment(stream, start, end, current, target, cue.get('curve', 'smooth'))
            cursor, current = end, target
        segment(stream, cursor, samples, current, current, 'linear')


def _voice_polish_chain(edit):
    mode = edit.get('voice_polish', 'off')
    if mode is False or mode is None:
        return 'off', ''
    if mode is True:
        mode = 'broadcast'
    if not isinstance(mode, str) or mode not in VOICE_POLISH_PRESETS:
        raise ValueError(f"voice_polish must be one of {', '.join(VOICE_POLISH_PRESETS)}")
    return mode, VOICE_POLISH_PRESETS[mode]


def _pocket_eq_db(edit):
    raw = edit.get('music_vocal_pocket', False)
    if raw is False or raw is None:
        return 0.0
    if raw is True:
        return -2.8
    if type(raw) not in (int, float) or not math.isfinite(raw):
        raise ValueError('music_vocal_pocket must be boolean or attenuation in dB (0 to -8)')
    db = -abs(float(raw))
    if not -8.0 <= db <= 0.0:
        raise ValueError('music_vocal_pocket attenuation must be within 0 to 8 dB')
    return db


def _ducking_config(edit):
    raw = edit.get('music_ducking', 'sidechain')
    if isinstance(raw, str):
        if raw == 'prebalanced':
            return 'prebalanced', None
        if raw not in DUCKING_PRESETS:
            raise ValueError('music_ducking must be sidechain, gentle, firm, prebalanced or a config object')
        return raw, dict(DUCKING_PRESETS[raw])
    if isinstance(raw, dict):
        mode = raw.get('mode', 'sidechain')
        if mode == 'prebalanced':
            return 'prebalanced', None
        if mode not in DUCKING_PRESETS:
            raise ValueError('music_ducking.mode must be sidechain, gentle, firm or prebalanced')
        cfg = dict(DUCKING_PRESETS[mode])
        for key, lo, hi in (('threshold', 0.001, 0.5), ('ratio', 1.0, 12.0),
                            ('attack', 1.0, 200.0), ('release', 20.0, 2000.0)):
            if key in raw:
                v = raw[key]
                if type(v) not in (int, float) or not math.isfinite(v) or not lo <= v <= hi:
                    raise ValueError(f'music_ducking.{key} must be between {lo} and {hi}')
                cfg[key] = float(v)
        return mode, cfg
    raise ValueError('music_ducking must be sidechain, gentle, firm, prebalanced or a config object')


def mix_stems(ff, folder, edit, timeline, pin, cues, *, audio_report=None):
    total_samples = round(timeline['frames'] * RATE / timeline['fps'])
    tracks = ['-i', pin('audio/narration.wav')]
    polish_name, polish_chain = _voice_polish_chain(edit)
    vo_chain = 'aformat=sample_rates=48000:channel_layouts=stereo'
    if polish_chain:
        vo_chain += ',' + polish_chain
    filters = [f'[0:a]{vo_chain}[vo]']
    names = ['[vo]']
    if audio_report is not None and polish_name != 'off':
        audio_report['voice_polish'] = polish_name

    generated = None
    plan = edit.get('sfx_plan')
    if plan and edit.get('effects'):
        raise ValueError('Choose sfx_plan for generated effects or effects for a prepared stem')
    if plan and edit.get('effects_gain', 1) != 0:
        from soundmix import render_effects
        events = json.loads(pin(plan).read_text())
        if not events:
            raise ValueError('sfx_plan must name the effects to mix')
        generated = folder / 'directed-effects.wav'
        effects = render_effects(events, edit, timeline, generated, pin)
        if audio_report is not None:
            audio_report['sfx_cues'] = effects

    pocket_db = _pocket_eq_db(edit)
    fade_in = float(edit.get('music_fade_in', 0) or 0)
    fade_out = float(edit.get('music_fade_out', 0) or 0)
    if not all(math.isfinite(v) and v >= 0 for v in (fade_in, fade_out)):
        raise ValueError('music_fade_in and music_fade_out must be nonnegative seconds')

    for key in ('music', 'effects'):
        stem = generated if key == 'effects' and generated else None
        if edit.get(key) or stem:
            index = len(names)
            tracks += ['-i', stem or pin(edit[key])]
            gain = float(edit.get(key + '_gain', 1))
            chain = [f'aformat=sample_rates={RATE}:channel_layouts=stereo', f'volume={gain}']
            if key == 'music':
                if pocket_db < 0:
                    chain.append(f'equalizer=f=2400:t=q:w=1.1:g={pocket_db:.3g}')
                    if audio_report is not None:
                        audio_report['music_vocal_pocket_db'] = pocket_db
                if fade_in > 0 or fade_out > 0:
                    chain.append(f'atrim=end_sample={total_samples}')
                    fi_samples = min(total_samples, round(fade_in * RATE))
                    fo_samples = min(total_samples, round(fade_out * RATE))
                    if fi_samples > 0:
                        chain.append(f'afade=t=in:ss=0:ns={fi_samples}:curve=qsin')
                    if fo_samples > 0:
                        chain.append(f'afade=t=out:ss={max(0, total_samples - fo_samples)}:ns={fo_samples}:curve=qsin')
            filters.append(f'[{index}:a]' + ','.join(chain) + f'[{key}]')
            names.append(f'[{key}]')

    if cues:
        envelope = folder / 'music-gain.f32'
        write_envelope(envelope, cues, total_samples)
        index = len(names)
        tracks += ['-f', 'f32le', '-ar', RATE, '-ac', 1, '-i', envelope]
        filters.extend([f'[{index}:a]pan=stereo|c0=c0|c1=c0[control]',
                        '[music][control]amultiply[directed]'])
        names[1] = '[directed]'

    duck_mode, duck_cfg = _ducking_config(edit)
    if edit.get('music') and duck_cfg is not None:
        filters.extend([
            '[vo]asplit=2[speech][key]',
            (f"{names[1]}[key]sidechaincompress=threshold={duck_cfg['threshold']:g}"
             f":ratio={duck_cfg['ratio']:g}:attack={duck_cfg['attack']:g}:release={duck_cfg['release']:g}[bed]"),
        ])
        names[:2] = ['[speech]', '[bed]']
        if audio_report is not None and duck_mode != 'sidechain':
            audio_report['music_ducking'] = duck_mode

    locations = location_cues(edit, timeline)
    if locations and audio_report is not None:
        checksums = {name: digest(pin(name)) for name in dict.fromkeys(c['file'] for c in locations)}
        for cue in locations:
            cue['source_sha256'] = checksums[cue['file']]
        audio_report['location_sound'] = locations
    index = len(names) + bool(cues)
    for i, cue in enumerate(locations):
        tracks.extend(['-i', extract_clip(ff, folder, cue, i, pin)])
        label = f'[location{i}]'
        start = cue['programme_samples'][0]
        filters.append(f'[{index + i}:a]adelay={start}S:all=1{label}')
        names.append(label)
    index += len(locations)

    # Optional Room Tone / Ambience Bed (built-in acoustic presets or looped project WAV)
    amb_spec = edit.get('ambience')
    if amb_spec:
        from design_sound import AMBIENCE_PRESETS, write_ambience_wav
        if isinstance(amb_spec, str):
            if amb_spec in AMBIENCE_PRESETS:
                amb_cfg = {'preset': amb_spec, 'gain_db': -28.0}
            else:
                amb_cfg = {'file': amb_spec, 'gain_db': -26.0, 'loop': True}
        elif isinstance(amb_spec, dict):
            amb_cfg = dict(amb_spec)
        else:
            raise ValueError('edit.ambience must be a preset name, file path or config object')
        amb_db = float(amb_cfg.get('gain_db', -28.0))
        if not math.isfinite(amb_db) or not -60.0 <= amb_db <= 0.0:
            raise ValueError('ambience.gain_db must be between -60 and 0 dB')
        if amb_cfg.get('preset'):
            amb_file = write_ambience_wav(folder / 'ambience-bed.wav', amb_cfg['preset'], total_samples)
            tracks.extend(['-i', amb_file])
            filters.append(f'[{index}:a]aformat=sample_rates={RATE}:channel_layouts=stereo,volume={amb_db:.3g}dB[ambience]')
        elif amb_cfg.get('file'):
            amb_src = pin(amb_cfg['file'])
            if amb_cfg.get('loop', True):
                tracks.extend(['-stream_loop', '-1', '-t', f'{total_samples / RATE + 0.5:.3f}', '-i', amb_src])
            else:
                tracks.extend(['-i', amb_src])
            fade_n = min(total_samples // 4, round(0.30 * RATE))
            filters.append(
                f'[{index}:a]aformat=sample_rates={RATE}:channel_layouts=stereo,atrim=end_sample={total_samples},'
                f'asetpts=PTS-STARTPTS,volume={amb_db:.3g}dB,'
                f'afade=t=in:ss=0:ns={fade_n}:curve=qsin,afade=t=out:ss={max(0, total_samples - fade_n)}:ns={fade_n}:curve=qsin[ambience]'
            )
        else:
            raise ValueError('ambience object needs preset or file')
        names.append('[ambience]')
        if audio_report is not None:
            audio_report['ambience'] = dict(amb_cfg, gain_db=amb_db)

    filters.append(''.join(names) + f'amix=inputs={len(names)}:duration=first:normalize=0[mix]')
    mix = folder / 'mix.wav'
    ff(*tracks, '-filter_complex', ';'.join(filters), '-map', '[mix]', '-c:a', 'pcm_f32le', mix)
    return mix


def normalization_filter(measured):
    keys = [('measured_I', 'input_i'), ('measured_TP', 'input_tp'), ('measured_LRA', 'input_lra'),
            ('measured_thresh', 'input_thresh'), ('offset', 'target_offset')]
    return 'loudnorm=I=-16:TP=-1.5:LRA=9:linear=true:' + ':'.join(f'{tag}={measured[key]}' for tag, key in keys)
