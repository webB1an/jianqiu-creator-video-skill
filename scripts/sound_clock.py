"""Resolve word and original-frame cues on the prepared programme's 48 kHz sound clock."""
from fractions import Fraction
import json
import math
from pathlib import Path
import subprocess

from remotion_library import resolve_frame

RATE = 48000


def finite(value):
    return type(value) in (float, int) and math.isfinite(value)


def cue_sample(row, edit, timeline, timing, pin=Path):
    fps = timeline['fps']
    first = round(timing['start_frame'] * RATE / fps)
    shift = row.get('shift_frames', 0)
    if type(shift) is not int:
        raise ValueError('shift_frames must be a signed number of programme frames')
    if 'at' not in row:
        offset = row.get('offset', 0)
        if not finite(offset):
            raise ValueError('Audio cue offset must be finite seconds')
        sample = first + round(offset * RATE)
    elif 'offset' in row:
        raise ValueError('Choose at or offset for the cue; shift an anchor with shift_frames')
    else:
        at = row['at']
        if isinstance(at, str):
            picture = timing['start_frame']
            voice = timing.get('audio_start_frame', picture)
            start = min(picture, voice)
            end = max(picture + timing['frames'], voice + timing.get('audio_frames', timing['frames']))
            frame = resolve_frame(at, timeline, dict(start_frame=start, frames=end - start))
            return round((start + frame + shift) * RATE / fps)
        elif isinstance(at, dict) and set(at) == {'source_frame'}:
            from action_takes import source
            shot = next(s for s in edit['shots'] if s['id'] == timing['id'])
            path = pin(shot['visual'])
            info = source(path)
            ticks, unit = info['ticks'], info['tb']
            index = at['source_frame']
            if type(index) is not int or not 0 <= index < len(ticks) - 1:
                raise ValueError(f'Unknown native source_frame {index} for {timing["id"]}')
            fmt = json.loads(subprocess.check_output([
                'ffprobe', '-v', 'error', '-show_entries', 'format=start_time', '-of', 'json', str(path)
            ]))['format']
            origin = Fraction(fmt.get('start_time', '0'))
            if abs(origin - ticks[0] * unit) <= Fraction(1, 2_000_000):
                origin = ticks[0] * unit
            local = ticks[index] * unit - origin - Fraction(str(shot.get('in', 0)))
            # A decimal edit in-point may be the rounded representation of this exact timestamp.
            if abs(local) < Fraction(1, 1_000_000_000):
                local = Fraction(0)
            if not 0 <= local < Fraction(timing['frames'], fps):
                raise ValueError(f'source_frame {index} falls outside the visible interval of {timing["id"]}')
            return round((Fraction(timing['start_frame'] + shift, fps) + local) * RATE)
        else:
            raise ValueError('Use a spoken @line:word or {"source_frame":N} as the audio cue')
    return sample + round(shift * RATE / fps)
