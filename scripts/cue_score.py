"""Render original beat & atmospheric harmonic arrangements locked to an edit timeline."""
import argparse
import json
import math
import wave
from functools import lru_cache
from pathlib import Path

import numpy as np

RATE = 48000
LIBRARY = Path(__file__).resolve().parents[1] / 'assets/instruments'
RECORDED = ('kick', 'rim', 'hat', 'pluck', 'wood', 'piano', 'pad', 'bell', 'bass', 'strings', 'harp')
SUSTAINED_RECORDED = {'piano', 'pad', 'bell', 'bass', 'strings', 'harp'}
SYNTHETIC = ('bass', 'pad', 'bell', 'piano', 'strings', 'pulse', 'sub_boom')


@lru_cache(maxsize=128)
def _load_recorded_single(name, semitones):
    with wave.open(str(LIBRARY / f'{name}.wav')) as f:
        if (f.getframerate(), f.getnchannels(), f.getsampwidth()) != (RATE, 1, 2):
            raise ValueError(f'Invalid instrument format for {name}')
        samples = np.frombuffer(f.readframes(f.getnframes()), '<i2').astype('float32')
    peak = np.abs(samples).max()
    if not peak:
        raise ValueError(f'Silent instrument {name}')
    on = np.flatnonzero(np.abs(samples) > peak * .014)[0]
    samples = samples[max(0, on - RATE // 500):] / peak
    if semitones:
        ix = np.arange(0, len(samples) - 1, 2 ** (float(semitones) / 12.0))
        samples = np.interp(ix, np.arange(len(samples)), samples).astype('float32')
    return samples


def instrument(name, semitones, duration_seconds=None):
    """Load real recorded CC0 acoustic sample (supports chords & duration shaping for sustained instruments)."""
    if name not in RECORDED:
        raise ValueError(f'Unknown instrument {name}')
    notes = semitones if isinstance(semitones, (list, tuple)) else [semitones]
    parts = [_load_recorded_single(name, float(st)) for st in notes]
    if len(parts) == 1:
        out = parts[0].copy()
    else:
        max_len = max(len(p) for p in parts)
        out = np.zeros(max_len, dtype='float32')
        for p in parts:
            out[:len(p)] += p
        peak = float(np.abs(out).max())
        if peak > 1e-6:
            out /= peak
    if name in SUSTAINED_RECORDED and duration_seconds is not None:
        target_n = max(int(0.08 * RATE), round(float(duration_seconds) * RATE))
        if len(out) > target_n:
            out = out[:target_n].copy()
        elif name in ('pad', 'strings') and len(out) < target_n and len(out) > RATE // 2:
            # Smooth crossfade-loop sustain tail for long harmonic beds.
            core = out[len(out) // 5:int(len(out) * 0.85)]
            buf = np.zeros(target_n, dtype='float32')
            head = min(len(out), target_n)
            buf[:head] = out[:head]
            xf = min(len(core) // 3, int(0.18 * RATE))
            pos = max(0, head - xf)
            while pos < target_n and len(core) > xf:
                rem = min(len(core), target_n - pos)
                seg = core[:rem].copy()
                r_in = min(xf, rem)
                seg[:r_in] *= np.linspace(0, 1, r_in, dtype='float32')
                buf[pos:pos + r_in] *= np.linspace(1, 0, r_in, dtype='float32')
                buf[pos:pos + rem] += seg
                pos += max(1, len(core) - xf)
            out = buf
        if name in ('pad', 'strings'):
            att = min(len(out) // 3, max(1, round(min(0.28, len(out) / RATE * 0.25) * RATE)))
            out[:att] *= (0.5 * (1.0 - np.cos(np.pi * np.linspace(0, 1, att)))).astype('float32')
        rel = min(len(out) // 2, max(1, round(min(0.35 if name in ('pad', 'strings') else 0.14, len(out) / RATE * 0.35) * RATE)))
        out[-rel:] *= (0.5 * (1.0 + np.cos(np.pi * np.linspace(0, 1, rel)))).astype('float32')
    return out


def synth_voice(name, semitones, duration_seconds):
    """Synthesize warm, broadcast-safe harmonic bass, pad or bell tones (A3=220Hz reference)."""
    notes = semitones if isinstance(semitones, (list, tuple)) else [semitones]
    dur = max(0.08, float(duration_seconds))
    n = round(dur * RATE)
    t = np.arange(n, dtype=np.float64) / RATE
    out = np.zeros(n, dtype=np.float64)
    if name == 'bass':
        # Warm rounded sub/electric bass rooted at A1 (55 Hz).
        for st in notes:
            f0 = 55.0 * (2.0 ** (float(st) / 12.0))
            out += 0.72 * np.sin(2 * np.pi * f0 * t) + 0.24 * np.sin(2 * np.pi * 2 * f0 * t) * np.exp(-t / 0.22)
        env = (1.0 - np.exp(-t / 0.008)) * np.exp(-t / max(0.18, dur * 0.65))
        tail = min(n // 3, round(0.03 * RATE))
        if tail:
            env[-tail:] *= 0.5 * (1.0 + np.cos(np.pi * np.linspace(0, 1, tail)))
        out *= env
    elif name == 'pad':
        # Lush slightly detuned warm harmonic bed rooted at A2 (110 Hz), gentle low-mid warmth.
        for st in notes:
            f0 = 110.0 * (2.0 ** (float(st) / 12.0))
            out += (0.42 * np.sin(2 * np.pi * f0 * t)
                    + 0.28 * np.sin(2 * np.pi * (f0 * 1.0035) * t + 0.4)
                    + 0.22 * np.sin(2 * np.pi * (f0 * 0.9965) * t + 1.1)
                    + 0.14 * np.sin(2 * np.pi * 2 * f0 * t))
        att = min(n // 2, max(1, round(min(0.35, dur * 0.28) * RATE)))
        rel = min(n // 2, max(1, round(min(0.45, dur * 0.32) * RATE)))
        env = np.ones(n, dtype=np.float64)
        env[:att] = 0.5 * (1.0 - np.cos(np.pi * np.linspace(0, 1, att)))
        env[-rel:] = 0.5 * (1.0 + np.cos(np.pi * np.linspace(0, 1, rel)))
        out *= env
    elif name == 'bell':
        # Soft Rhodes-like harmonic mallet rooted at A4 (440 Hz).
        for st in notes:
            f0 = 440.0 * (2.0 ** (float(st) / 12.0))
            out += (0.68 * np.sin(2 * np.pi * f0 * t) * np.exp(-t / max(0.22, dur * 0.48))
                    + 0.24 * np.sin(2 * np.pi * 2 * f0 * t) * np.exp(-t / max(0.08, dur * 0.20))
                    + 0.08 * np.sin(2 * np.pi * 3 * f0 * t) * np.exp(-t / 0.05))
        att = min(n // 4, max(1, round(0.004 * RATE)))
        out[:att] *= np.linspace(0, 1, att)
    elif name == 'piano':
        # Warm felt-piano tone rooted at C4/A3 (220 Hz) with subtle hammer transient & rich harmonics.
        for st in notes:
            f0 = 220.0 * (2.0 ** (float(st) / 12.0))
            out += (0.58 * np.sin(2 * np.pi * f0 * t) * np.exp(-t / max(0.38, dur * 0.72))
                    + 0.25 * np.sin(2 * np.pi * 2 * f0 * t) * np.exp(-t / max(0.18, dur * 0.36))
                    + 0.11 * np.sin(2 * np.pi * 3 * f0 * t) * np.exp(-t / 0.10)
                    + 0.05 * np.sin(2 * np.pi * 4 * f0 * t) * np.exp(-t / 0.045))
        att = min(n // 6, max(1, round(0.006 * RATE)))
        out[:att] *= np.linspace(0, 1, att)
        tail = min(n // 4, round(0.045 * RATE))
        if tail:
            out[-tail:] *= 0.5 * (1.0 + np.cos(np.pi * np.linspace(0, 1, tail)))
    elif name == 'strings':
        # Lush bowed ensemble swell (A2=110 Hz) with gentle vibrato for cinematic documentary arcs.
        vib = 1.0 + 0.0028 * np.sin(2 * np.pi * 5.2 * t)
        for st in notes:
            f0 = 110.0 * (2.0 ** (float(st) / 12.0))
            ph = 2 * np.pi * np.cumsum(f0 * vib) / RATE
            out += (0.36 * np.sin(ph)
                    + 0.28 * np.sin(ph * 1.004 + 0.5)
                    + 0.24 * np.sin(ph * 0.996 + 1.3)
                    + 0.18 * np.sin(ph * 2.0)
                    + 0.09 * np.sin(ph * 3.0))
        att = min(n // 2, max(1, round(min(0.45, dur * 0.35) * RATE)))
        rel = min(n // 2, max(1, round(min(0.55, dur * 0.38) * RATE)))
        env = np.ones(n, dtype=np.float64)
        env[:att] = 0.5 * (1.0 - np.cos(np.pi * np.linspace(0, 1, att)))
        env[-rel:] = 0.5 * (1.0 + np.cos(np.pi * np.linspace(0, 1, rel)))
        out *= env
    elif name == 'pulse':
        # Tight investigative analog synth pulse (Veritasium / Vox style, rooted at A1=55 Hz).
        for st in notes:
            f0 = 55.0 * (2.0 ** (float(st) / 12.0))
            out += np.tanh(1.5 * (0.65 * np.sin(2 * np.pi * f0 * t)
                                  + 0.35 * np.sin(2 * np.pi * 2 * f0 * t) * np.exp(-t / 0.09)
                                  + 0.16 * np.sin(2 * np.pi * 3 * f0 * t) * np.exp(-t / 0.05)))
        env = (1.0 - np.exp(-t / 0.005)) * np.exp(-t / max(0.08, dur * 0.45))
        out *= env
    elif name == 'sub_boom':
        # Deep cinema chapter downbeat boom (55 -> 36 Hz glide).
        f_inst = 36.0 + 38.0 * np.exp(-t / 0.09)
        ph = 2 * np.pi * np.cumsum(f_inst) / RATE
        out = np.tanh(1.3 * np.sin(ph)) * (1.0 - np.exp(-t / 0.007)) * np.exp(-t / max(0.28, dur * 0.65))
    else:
        raise ValueError(f'Unknown instrument {name}')
    peak = np.max(np.abs(out))
    return (out / max(1e-9, peak)).astype('float32')


def _comb_echo(x, delay_s, decay):
    """Multi-bounce damped delay line (vectorized Schroeder comb approximation) for smooth acoustic tail."""
    n = len(x)
    d = max(1, round(delay_s * RATE))
    out = np.zeros(n, dtype='float32')
    gain = float(decay)
    shift = d
    for _ in range(10):
        if shift >= n or gain < 0.015:
            break
        out[shift:] += x[:n - shift] * gain
        shift += d
        gain *= decay
    return out


def apply_room_reverb(stereo, wet):
    """Stereo warm studio/hall reverb using decorrelated prime comb lines + LP damping (smooth, zero metallic ring)."""
    if wet <= 0:
        return stereo
    n = len(stereo)
    mono = 0.5 * (stereo[:, 0] + stereo[:, 1])
    # Warm acoustic air absorption on the reverb send (keeps transients and voice band crisp).
    kernel = np.hanning(27).astype('float32')
    kernel /= kernel.sum()
    damped = np.convolve(mono, kernel, mode='same').astype('float32')

    # Four mutually prime comb delays per channel, slightly decorrelated L vs R for wide studio bloom.
    combs_l = ((0.0297, 0.48), (0.0371, 0.44), (0.0411, 0.40), (0.0437, 0.36))
    combs_r = ((0.0313, 0.48), (0.0389, 0.44), (0.0431, 0.40), (0.0467, 0.36))
    rev = np.zeros((n, 2), dtype='float32')
    for ch, combs in ((0, combs_l), (1, combs_r)):
        acc = np.zeros(n, dtype='float32')
        for delay_s, decay in combs:
            acc += _comb_echo(damped, delay_s, decay)
        # Gentle second-stage LP diffusion smooths late reflections.
        acc = np.convolve(acc * 0.28, kernel, mode='same').astype('float32')
        rev[:, ch] = acc
    return stereo * (1.0 - wet * 0.22) + rev * (wet * 0.85)


def write_stem(score, timeline, path):
    fps = timeline['fps']
    starts = {s['id']: s['start_frame'] / fps for s in timeline['shots']}
    length = round(timeline['frames'] * RATE / fps)
    master = np.zeros((length, 2), dtype='float32')
    global_reverb = float(score.get('reverb', 0.0))
    if not math.isfinite(global_reverb) or not 0.0 <= global_reverb <= 0.5:
        raise ValueError('score.reverb must be between 0 and 0.5')

    for region in score['sections']:
        shot, end = region['start'], region.get('until')
        if shot not in starts or (end and end not in starts):
            raise ValueError('Score refers to an unknown shot')
        offset = round((starts[shot] + region.get('offset', 0)) * RATE)
        stop = round((starts[end] if end else length / RATE) * RATE) + round(region.get('end_offset', 0) * RATE)
        if not 0 <= offset < stop <= length:
            raise ValueError(f'Invalid score window starting at {shot}')
        pattern = score['patterns'][region['pattern']]
        beat_seconds = 60 / float(region['bpm'])
        if not math.isfinite(beat_seconds) or beat_seconds <= 0 or not pattern['beats'] > 0:
            raise ValueError('Use a positive BPM and pattern length')
        swing = float(region.get('swing', 0.0))
        if not math.isfinite(swing) or not 0.0 <= swing <= 0.35:
            raise ValueError('Section swing must be in [0, 0.35]')
        loop = pattern['beats'] * beat_seconds
        bus = np.zeros((stop - offset, 2), 'float32')
        include_v = set(region.get('include_voices') or [])
        exclude_v = set(region.get('exclude_voices') or [])
        for repeat in range(math.ceil(len(bus) / (RATE * loop))):
            for note in pattern['notes']:
                if len(note) == 5:
                    name, beat, db, semitones, pan = note
                    dur_beats = 1.0
                elif len(note) == 6:
                    name, beat, db, semitones, pan, dur_beats = note
                else:
                    raise ValueError(f'Note requires 5 or 6 entries: {note}')
                if (include_v and name not in include_v) or (name in exclude_v):
                    continue
                if not 0 <= beat < pattern['beats'] or not -1 <= pan <= 1:
                    raise ValueError(f'Invalid beat or stereo position in {name}')
                # Subtle micro-swing on off-beat eighth positions (x.5).
                swung = beat + (swing * 0.5 if abs((beat % 1.0) - 0.5) < 0.05 else 0.0)
                when = round((repeat * loop + swung * beat_seconds) * RATE)
                if when >= len(bus):
                    continue
                if name in RECORDED and (LIBRARY / f'{name}.wav').is_file():
                    take = instrument(name, semitones, dur_beats * beat_seconds if name in SUSTAINED_RECORDED else None)
                elif name in SYNTHETIC:
                    take = synth_voice(name, semitones, dur_beats * beat_seconds)
                else:
                    raise ValueError(f'Unknown instrument {name}')
                amount = min(len(bus) - when, len(take))
                level = 10 ** ((db + region.get('gain_db', 0)) / 20)
                bus[when:when + amount] += take[:amount, None] * level * np.sqrt([(1 - pan) / 2, (1 + pan) / 2])
        sec_reverb = float(region.get('reverb', global_reverb))
        if sec_reverb > 0:
            bus = apply_room_reverb(bus, sec_reverb)
        fade_in_s = float(region.get('fade_in', 0.0))
        if fade_in_s > 0:
            head = min(len(bus), max(1, round(RATE * fade_in_s)))
            bus[:head] *= (0.5 * (1.0 - np.cos(np.pi * np.linspace(0, 1, head))))[:, None]
        tail = min(len(bus), max(1, round(RATE * region.get('fade_out', .065))))
        bus[-tail:] *= (0.5 * (1.0 + np.cos(np.pi * np.linspace(0, 1, tail))))[:, None]
        master[offset:stop] += bus
    if np.abs(master).max() > 1:
        raise ValueError('Score clips, reduce note or section levels')
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as f:
        f.setparams((2, 2, RATE, 0, 'NONE', 'not compressed'))
        f.writeframes((master * 32767).astype('<i2').tobytes())


def beat_grid(score, timeline):
    """Export every musical beat and bar downbeat in programme seconds and frames for beat-synced editing."""
    fps = timeline['fps']
    starts = {s['id']: s['start_frame'] / fps for s in timeline['shots']}
    total_s = timeline['frames'] / fps
    beats = []
    sections_meta = []
    for idx, region in enumerate(score['sections']):
        shot, end = region['start'], region.get('until')
        if shot not in starts or (end and end not in starts):
            raise ValueError('Score refers to an unknown shot')
        t0 = starts[shot] + float(region.get('offset', 0))
        t1 = (starts[end] if end else total_s) + float(region.get('end_offset', 0))
        bpm = float(region['bpm'])
        beat_s = 60.0 / bpm
        pattern = score['patterns'][region['pattern']]
        beats_per_bar = int(round(pattern.get('beats', 4)))
        bar_beats = max(1, min(8, beats_per_bar))
        sections_meta.append(dict(
            index=idx, start_shot=shot, until_shot=end, start_seconds=round(t0, 4),
            end_seconds=round(t1, 4), bpm=bpm, pattern=region['pattern'],
        ))
        step = 0
        while True:
            t = t0 + step * beat_s
            if t >= t1 - 1e-6 or t >= total_s:
                break
            beats.append(dict(
                seconds=round(t, 4),
                frame=round(t * fps),
                section=idx,
                bpm=bpm,
                beat_in_bar=(step % bar_beats) + 1,
                is_downbeat=(step % bar_beats) == 0,
            ))
            step += 1
    beats.sort(key=lambda b: b['seconds'])
    return dict(
        schema=1,
        fps=fps,
        frames=timeline['frames'],
        total_seconds=round(total_s, 4),
        beat_count=len(beats),
        sections=sections_meta,
        beats=beats,
    )


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('score', type=Path)
    p.add_argument('--timeline', type=Path, default=Path('timeline.json'))
    p.add_argument('--out', type=Path, default=Path('audio/music.wav'))
    p.add_argument('--beats-out', type=Path, help='Optional JSON output path for beat-grid timestamps (e.g. qa/beat-grid.json)')
    a = p.parse_args()
    score = json.loads(a.score.read_text())
    timeline = json.loads(a.timeline.read_text())
    write_stem(score, timeline, a.out)
    if a.beats_out:
        a.beats_out.parent.mkdir(parents=True, exist_ok=True)
        a.beats_out.write_text(json.dumps(beat_grid(score, timeline), ensure_ascii=False, indent=2) + '\n')


if __name__ == '__main__':
    main()
