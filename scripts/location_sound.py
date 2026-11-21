"""Mix the original audio of an ordinary shot, preserving its media clock across picture cuts."""
from pathlib import Path
import subprocess

from sound_clock import RATE, finite


def location_cues(edit, timeline):
    shots = [s for s in edit.get('shots', []) if 'location_sound' in s]
    if not shots:
        return []
    timing = {s['id']: s for s in timeline['shots']}
    fps = timeline['fps']
    samples = round(timeline['frames'] * RATE / fps)
    cues = []
    for shot in shots:
        spec = shot['location_sound']
        if (not isinstance(spec, dict) or set(spec) - {'source_range', 'gain_db', 'fade_in', 'fade_out'}
                or 'remotion_effect' in shot or 'explainer_scene' in shot):
            raise ValueError(f'{shot["id"]}: location_sound reads the original audio track of a direct visual')
        span = spec.get('source_range')
        gain = spec.get('gain_db', 0)
        fades = [spec.get(k, 0) for k in ('fade_in', 'fade_out')]
        if (not isinstance(span, list) or len(span) != 2 or not all(finite(t) for t in span)
                or not 0 <= span[0] < span[1] or not finite(gain)
                or not all(finite(t) and t >= 0 for t in fades)):
            raise ValueError(f'{shot["id"]}: give an increasing source_range in seconds, finite gain_db and fades')
        begin, end = (round(t * RATE) for t in span)
        fade_in, fade_out = (round(t * RATE) for t in fades)
        count = end - begin
        entry = shot.get('in', 0)
        if not finite(entry) or entry < 0:
            raise ValueError(f'{shot["id"]}: invalid picture in-point')
        first = round(timing[shot['id']]['start_frame'] * RATE / fps) + begin - round(entry * RATE)
        if count < 1 or first < 0 or first + count > samples or fade_in + fade_out > count:
            raise ValueError(f'{shot["id"]}: location_sound or fades fall outside their available time')
        cues.append(dict(shot=shot['id'], file=shot['visual'], source_samples=[begin, end],
                         programme_samples=[first, first + count], gain_db=gain,
                         fade_in_samples=fade_in, fade_out_samples=fade_out, sample_rate=RATE))
    return cues


def location_sources(edit, timeline):
    fps = timeline['fps']
    for cue in location_cues(edit, timeline):
        start, stop = cue['programme_samples']
        yield dict(file=cue['file'], role='effects', shot=cue['shot'],
                   range=[t / RATE for t in cue['source_samples']],
                   frames=[start * fps // RATE, (stop * fps + RATE - 1) // RATE])


def extract_clip(ff, directory, cue, index, pin):
    begin, end = cue['source_samples']
    count = end - begin
    chain = [f'aresample={RATE}:async=1:first_pts=0', f'atrim=start_sample={begin}:end_sample={end}',
             'asetpts=PTS-STARTPTS', 'aformat=channel_layouts=stereo', f'volume={cue["gain_db"]}dB']
    if cue['fade_in_samples']:
        chain.append(f'afade=t=in:ss=0:ns={cue["fade_in_samples"]}')
    if cue['fade_out_samples']:
        duration = cue['fade_out_samples']
        chain.append(f'afade=t=out:ss={count-duration}:ns={duration}')
    target = Path(directory) / f'location-{index}.wav'
    ff('-copyts', '-start_at_zero', '-i', pin(cue['file']), '-map', '0:a:0', '-af', ','.join(chain),
       '-c:a', 'pcm_f32le', target)
    duration = subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', 'a:0',
                                        '-show_entries', 'stream=duration_ts', '-of', 'csv=p=0',
                                        str(target)], text=True).strip()
    if duration != str(count):
        raise ValueError(f'{cue["shot"]}: original audio ends before the requested source range')
    return target
