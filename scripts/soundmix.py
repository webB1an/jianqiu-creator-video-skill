"""Place the transients of catalogued or project recordings at resolved picture and speech cues."""
import json
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
        take = np.frombuffer(subprocess.check_output([
            'ffmpeg', '-v', 'error', '-i', str(path), '-ar', str(RATE), '-ac', '1', '-f', 'f32le', '-'
        ]), '<f4')
        a, b = event['trim']
        anchor, peak_db, pan = event['anchor'], event['peak_db'], event.get('pan', 0)
        if not all(finite(v) for v in (a, b, anchor, peak_db, pan)) or not 0 <= a <= anchor < b:
            raise ValueError('Invalid source range or gain for ' + key)
        if not -1 <= pan <= 1:
            raise ValueError('Pan must be in [-1,1]')
        if b > len(take) / RATE + .0001:
            raise ValueError('Effects source is shorter than trim: ' + key)
        source_start = round(a * RATE)
        clip = take[source_start:round(b * RATE)].copy()
        if len(clip) == 0 or np.max(abs(clip)) <= .001:
            raise ValueError('Empty sound excerpt: ' + key)
        clip *= 10**(peak_db / 20) / np.max(abs(clip))
        fade = min(200, len(clip) // 2)
        if fade:
            clip[:fade] *= np.linspace(0, 1, fade)
            clip[-fade:] *= np.linspace(1, 0, fade)
        timing = schedule.get(event.get('shot'))
        if timing is None or ('offset' not in event and 'at' not in event):
            raise ValueError('Give the effect a prepared shot and cue at or offset')
        cue = cue_sample(event, edit, timeline, timing, pin)
        # Retain the legacy nearest-sample quantization when both values are in seconds.
        if 'at' not in event and not event.get('shift_frames'):
            start = round((timing['start_frame'] / timeline['fps'] + event['offset'] - anchor + a) * RATE)
        else:
            start = cue - round(anchor * RATE) + source_start
        end = start + len(clip)
        if start < 0 or end > len(bus):
            raise ValueError('Sound extends outside timeline: ' + key)
        bus[start:end] += clip[:, None] * np.sqrt([(1-pan)/2, (1+pan)/2])
        log.append(dict(key=key, event=event['event'], shot=timing['id'], cue=round(cue/RATE, 3),
                        cue_sample=cue, programme_samples=[start, end],
                        range=[source_start/RATE, (source_start+len(clip))/RATE],
                        file=str(take_path if directory == local else Path('@skill') / local / 'take.wav'),
                        anchor=event.get('at', event.get('offset')), shift_frames=event.get('shift_frames', 0),
                        take_anchor_seconds=anchor, wav_sha256=meta['wav_sha256'], license=meta.get('license'),
                        page=meta['page'], stars=meta['stars'], votes=meta['votes'], author=meta['author']))
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
