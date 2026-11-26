"""Mix the original audio of an ordinary shot, preserving its media clock across picture cuts."""
from pathlib import Path
import subprocess

from sound_clock import RATE, finite


ALLOWED_KEYS = {'source_range', 'gain_db', 'fade_in', 'fade_out', 'j_cut', 'l_cut', 'file', 'mode'}


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
        if not isinstance(spec, dict) or set(spec) - ALLOWED_KEYS:
            raise ValueError(f'{shot["id"]}: invalid location_sound fields')
        custom_file = spec.get('file')
        if not custom_file and ('remotion_effect' in shot or 'explainer_scene' in shot):
            raise ValueError(f'{shot["id"]}: location_sound without file reads the original audio track of a direct visual')
        j_cut = float(spec.get('j_cut', 0) or 0)
        l_cut = float(spec.get('l_cut', 0) or 0)
        if not all(finite(v) and v >= 0 for v in (j_cut, l_cut)):
            raise ValueError(f'{shot["id"]}: j_cut and l_cut must be nonnegative seconds')
        entry = float(shot.get('in', 0) or 0)
        if not finite(entry) or entry < 0:
            raise ValueError(f'{shot["id"]}: invalid picture in-point')
        shot_start_s = timing[shot['id']]['start_frame'] / fps
        shot_dur_s = timing[shot['id']]['frames'] / fps
        span = spec.get('source_range')
        if span is None:
            # Declarative J-Cut / L-Cut shorthand: derive source_range from shot in/duration + overlaps.
            eff_j = min(j_cut, entry if not custom_file else j_cut, shot_start_s)
            src_start = max(0.0, (0.0 if custom_file else entry) - eff_j)
            src_end = src_start + eff_j + shot_dur_s + l_cut
            span = [round(src_start, 6), round(src_end, 6)]
            prog_start_s = shot_start_s - eff_j
        else:
            if not isinstance(span, list) or len(span) != 2 or not all(finite(t) for t in span) or not 0 <= span[0] < span[1]:
                raise ValueError(f'{shot["id"]}: give an increasing source_range in seconds')
            if custom_file:
                prog_start_s = shot_start_s - j_cut
            else:
                prog_start_s = shot_start_s + (span[0] - entry) - (j_cut if abs(span[0] - entry) < 1e-6 else 0.0)
        gain = spec.get('gain_db', 0)
        default_fi = round(min(0.45, max(0.06, j_cut * 0.45)), 3) if j_cut > 0 else 0.0
        default_fo = round(min(0.45, max(0.06, l_cut * 0.45)), 3) if l_cut > 0 else 0.0
        fades = [spec.get('fade_in', default_fi), spec.get('fade_out', default_fo)]
        if not finite(gain) or not all(finite(t) and t >= 0 for t in fades):
            raise ValueError(f'{shot["id"]}: give finite gain_db and nonnegative fades')
        begin, end = (round(t * RATE) for t in span)
        fade_in, fade_out = (round(t * RATE) for t in fades)
        count = end - begin
        first = round(prog_start_s * RATE)
        if first < 0:
            trim_front = -first
            begin += trim_front
            count -= trim_front
            first = 0
        if first + count > samples:
            count = samples - first
            end = begin + count
        if count < 1 or fade_in + fade_out > count:
            raise ValueError(f'{shot["id"]}: location_sound or fades fall outside their available time')
        cues.append(dict(
            shot=shot['id'], file=custom_file or shot['visual'],
            mode=spec.get('mode', 'soundbite' if custom_file else 'location'),
            j_cut=j_cut, l_cut=l_cut,
            source_samples=[begin, end], programme_samples=[first, first + count],
            gain_db=gain, fade_in_samples=fade_in, fade_out_samples=fade_out, sample_rate=RATE,
        ))
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
