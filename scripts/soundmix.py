"""Place the transients of catalogued or project recordings at resolved picture and speech cues."""
import json
import math
from pathlib import Path
import subprocess
import wave

from production_run import InputSnapshot, atomic_json, digest, install_file, stage_run
from sound_clock import RATE, cue_sample, finite

BANK = Path(__file__).resolve().parents[1] / 'assets/sfx'


def local_directory(key):
    if not isinstance(key, str) or not key or key in {'.', '..'} or Path(key).name != key:
        raise ValueError('Invalid sound key')
    return Path('assets/sfx') / key


def planned_effect_sources(edit, project, read_path):
    """Yield project-library source intervals from the same plan used for mixing."""
    if not edit.get('sfx_plan') or edit.get('effects_gain', 1) == 0:
        return
    for event in json.loads(read_path(project / edit['sfx_plan']).read_text()):
        directory = local_directory(event['key'])
        if (project / directory).is_dir():
            yield dict(file=str(directory / 'take.wav'), role='effects', shot=event['shot'], range=event['trim'])


def _decode_take(path, highpass=None, lowpass=None):
    import numpy as np

    filters = []
    if highpass is not None:
        if not finite(highpass) or not 20 <= highpass <= 16000:
            raise ValueError('SFX highpass must be in 20..16000 Hz')
        filters.append(f'highpass=f={highpass:g}')
    if lowpass is not None:
        if not finite(lowpass) or not 100 <= lowpass <= 22000:
            raise ValueError('SFX lowpass must be in 100..22000 Hz')
        filters.append(f'lowpass=f={lowpass:g}')
    cmd = ['ffmpeg', '-v', 'error', '-i', str(path)]
    if filters:
        cmd.extend(['-af', ','.join(filters)])
    cmd.extend(['-ar', str(RATE), '-ac', '1', '-f', 'f32le', '-'])
    return np.frombuffer(subprocess.check_output(cmd), '<f4')


def render_effects(events, edit, timeline, target, pin=Path):
    import numpy as np

    if not isinstance(events, list):
        raise ValueError('An effects plan must be an array of sound events')
    schedule = {s['id']: s for s in timeline['shots']}
    bus = np.zeros((round(timeline['frames'] * RATE / timeline['fps']), 2), 'float32')
    log = []
    for event in events:
        key = event['key']
        local = local_directory(key)
        directory = local if local.is_dir() else BANK / key
        take_path = directory / 'take.wav'
        meta = json.loads(pin(directory / 'source.json').read_text())
        path = pin(take_path)
        if digest(path) != meta['wav_sha256']:
            raise ValueError('Sound hash changed: ' + key)
        take = _decode_take(path, event.get('highpass'), event.get('lowpass'))
        a, b = event['trim']
        anchor, peak_db, pan = event['anchor'], event['peak_db'], event.get('pan', 0)
        rate = event.get('rate', 1.0)
        if not all(finite(v) for v in (a, b, anchor, peak_db, pan, rate)) or not 0 <= a <= anchor < b:
            raise ValueError('Invalid source range or gain for ' + key)
        if not 0.5 <= rate <= 2.0:
            raise ValueError('SFX playback rate must be in [0.5, 2.0]: ' + key)
        if not -1 <= pan <= 1:
            raise ValueError('Pan must be in [-1,1]')
        if b > len(take) / RATE + .0001:
            raise ValueError('Effects source is shorter than trim: ' + key)
        source_start = round(a * RATE)
        clip = take[source_start:round(b * RATE)].copy()
        if len(clip) == 0 or np.max(abs(clip)) <= .001:
            raise ValueError('Empty sound excerpt: ' + key)
        if rate != 1.0:
            positions = np.arange(0, len(clip) - 1, rate, dtype=np.float64)
            clip = np.interp(positions, np.arange(len(clip), dtype=np.float64), clip).astype(np.float32)
            if len(clip) == 0 or np.max(abs(clip)) <= .001:
                raise ValueError('Resampled sound excerpt is empty: ' + key)
        clip *= 10 ** (peak_db / 20) / np.max(abs(clip))
        default_fade = min(200, len(clip) // 2)
        fade_in_n = min(len(clip) // 2, round(float(event['fade_in']) * RATE)) if 'fade_in' in event else default_fade
        fade_out_n = min(len(clip) // 2, round(float(event['fade_out']) * RATE)) if 'fade_out' in event else default_fade
        if fade_in_n < 0 or fade_out_n < 0:
            raise ValueError('SFX fade_in and fade_out must be nonnegative')
        if fade_in_n:
            clip[:fade_in_n] *= (0.5 * (1.0 - np.cos(np.pi * np.linspace(0, 1, fade_in_n)))).astype(np.float32)
        if fade_out_n:
            clip[-fade_out_n:] *= (0.5 * (1.0 + np.cos(np.pi * np.linspace(0, 1, fade_out_n)))).astype(np.float32)
        timing = schedule.get(event.get('shot'))
        if timing is None or ('offset' not in event and 'at' not in event):
            raise ValueError('Give the effect a prepared shot and cue at or offset')
        cue = cue_sample(event, edit, timeline, timing, pin)
        # Anchor offset scales inversely with playback rate so the transient still hits the exact cue.
        anchor_offset = round((anchor - a) / rate * RATE)
        if 'at' not in event and not event.get('shift_frames') and rate == 1.0:
            start = round((timing['start_frame'] / timeline['fps'] + event['offset'] - anchor + a) * RATE)
        else:
            start = cue - anchor_offset
        end = start + len(clip)
        if start < 0 or end > len(bus):
            raise ValueError('Sound extends outside timeline: ' + key)
        bus[start:end] += clip[:, None] * np.sqrt([(1 - pan) / 2, (1 + pan) / 2])
        entry = dict(key=key, event=event['event'], shot=timing['id'], cue=round(cue / RATE, 3),
                     cue_sample=cue, programme_samples=[start, end],
                     range=[source_start / RATE, round(b * RATE) / RATE],
                     file=str(take_path if directory == local else Path('@skill') / local / 'take.wav'),
                     anchor=event.get('at', event.get('offset')), shift_frames=event.get('shift_frames', 0),
                     take_anchor_seconds=anchor, wav_sha256=meta['wav_sha256'], license=meta.get('license'),
                     page=meta['page'], stars=meta['stars'], votes=meta['votes'], author=meta['author'])
        if rate != 1.0:
            entry['rate'] = rate
        log.append(entry)
    if not np.isfinite(bus).all() or np.abs(bus).max() > 1:
        raise ValueError('Effects stem clips')
    with wave.open(str(target), 'wb') as writer:
        writer.setparams((2, 2, RATE, 0, 'NONE', 'not compressed'))
        writer.writeframes((bus * 32767).astype('<i2').tobytes())
    return log


def mix():
    with stage_run('soundmix') as (folder, status):
        pins = InputSnapshot(folder / 'inputs')
        timeline = json.loads(pins.pin('timeline.json').read_text())
        edit_path = pins.pin('edit.json', optional=True)
        edit = json.loads(edit_path.read_text()) if edit_path.is_file() else {}
        events = json.loads(pins.pin(edit.get('sfx_plan', 'sfx.json')).read_text())
        if (any('at' in row for row in events) and timeline.get('edit_sha256')
                and timeline['edit_sha256'] != pins.hashes['edit.json']):
            raise ValueError('Prepare the current edit before mixing anchored effects')
        staged = folder / 'effects.wav'
        entries = render_effects(events, edit, timeline, staged, pins.pin)
        pins.verify()
        install_file(staged, 'audio/effects.wav')
        atomic_json(Path('qa/sound-cues.json'), entries)
        status.update(events=len(entries), output_sha256=digest(staged))


if __name__ == '__main__':
    mix()
