"""Make reproducible project-local motion & editorial effects for the standard soundmix cue sheet."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import tempfile
import wave

import numpy as np

RATE = 48000
DEFAULTS = {
    'pass': .42,
    'rise': .64,
    'land': .56,
    'tick': .09,
    'whoosh': .34,
    'pop': .11,
    'chime': .65,
    'sub_drop': .72,
    'swipe': .22,
    'snap': .06,
    'tension': 1.10,
    'sub_riser': 1.15,
}


def _one_pole_bandpass(noise, f_lo, f_hi):
    """Lightweight vectorized resonant sweep using STFT spectral shaping."""
    n = len(noise)
    win = 512
    hop = 128
    pad = win
    padded = np.pad(noise, (pad, pad))
    out = np.zeros_like(padded)
    window = np.hanning(win)
    freqs = np.fft.rfftfreq(win, 1.0 / RATE)
    frames = max(1, (len(padded) - win) // hop + 1)
    for idx in range(frames):
        pos = idx * hop
        seg = padded[pos:pos + win] * window
        spec = np.fft.rfft(seg)
        u = idx / max(1, frames - 1)
        fc = f_lo + (f_hi - f_lo) * (math.sin(math.pi * u) ** 1.4 if f_hi > f_lo else u)
        bw = max(180.0, fc * 0.72)
        gain = np.exp(-0.5 * ((freqs - fc) / bw) ** 2)
        out[pos:pos + win] += np.fft.irfft(spec * gain, win) * window
    return out[pad:pad + n]


def synth(kind, seconds, seed):
    n = round(seconds * RATE)
    t = np.arange(n) / RATE
    u = np.arange(n) / max(1, n - 1)
    rng = np.random.default_rng(seed)
    noise = rng.normal(0, 1, n)

    if kind in ('pass', 'rise'):
        kernel = np.hanning(79)
        gust = np.convolve(noise, kernel / kernel.sum(), mode='same')
        pitch = (380 + 1200 * np.sin(np.pi * u) ** 2) if kind == 'pass' else 230 + 2500 * u ** 1.8
        phase = 2 * np.pi * np.cumsum(pitch) / RATE
        envelope = (np.sin(np.pi * u) ** 1.6 if kind == 'pass' else u ** 1.6 * (1 - u) ** .45)
        signal = gust * (.7 * np.sin(phase) + .3 * np.sin(phase * 1.63)) * envelope
    elif kind == 'land':
        pitch = 57 + 95 * np.exp(-t / .055)
        phase = 2 * np.pi * np.cumsum(pitch) / RATE
        signal = (np.sin(phase) + .28 * np.sin(2 * phase)) * np.exp(-7 * u) + .13 * noise * np.exp(-48 * u)
    elif kind == 'tick':
        phase = 2 * np.pi * (1560 * t - 370 * t * t / seconds)
        signal = (.7 * np.sin(phase) + .2 * np.sin(phase * 1.53) + .1 * noise) * np.exp(-7 * u)
    elif kind == 'whoosh':
        # Wideband swept air movement peaking cleanly near center for whip-pan / push-through bridges.
        air = _one_pole_bandpass(noise, 240.0, 2600.0)
        body_pitch = 95.0 + 165.0 * np.sin(np.pi * u) ** 1.5
        body_phase = 2 * np.pi * np.cumsum(body_pitch) / RATE
        env = np.sin(np.pi * u) ** 1.8
        signal = (0.85 * air / max(1e-6, np.std(air)) + 0.25 * np.sin(body_phase)) * env
    elif kind == 'pop':
        # Warm wooden/bubble upward pitch pop for callouts, cards and badges.
        pitch = 260.0 + 460.0 * (1.0 - np.exp(-t / 0.018))
        phase = 2 * np.pi * np.cumsum(pitch) / RATE
        env = (1.0 - np.exp(-t / 0.0025)) * np.exp(-t / max(0.018, seconds * 0.24))
        click = noise * np.exp(-t / 0.0022) * 0.14
        signal = (0.78 * np.sin(phase) + 0.18 * np.sin(2 * phase)) * env + click
    elif kind == 'chime':
        # Restrained warm C5 + G5 Rhodes/mallet harmonic interval for key insight reveals.
        env1 = (1.0 - np.exp(-t / 0.004)) * np.exp(-t / max(0.14, seconds * 0.38))
        env2 = (1.0 - np.exp(-t / 0.006)) * np.exp(-t / max(0.08, seconds * 0.20))
        f1, f2 = 523.25, 783.99
        signal = (0.55 * np.sin(2 * np.pi * f1 * t) * env1
                  + 0.34 * np.sin(2 * np.pi * f2 * t) * env1
                  + 0.14 * np.sin(2 * np.pi * f1 * 2 * t) * env2
                  + 0.08 * np.sin(2 * np.pi * f2 * 2 * t) * env2)
    elif kind == 'sub_drop':
        # Clean cinema sub-bass impact + downward glide for chapter breaks / big numeric reveals.
        pitch = 42.0 + 88.0 * np.exp(-t / 0.095)
        phase = 2 * np.pi * np.cumsum(pitch) / RATE
        body = np.tanh(1.35 * np.sin(phase) + 0.22 * np.sin(2 * phase))
        env = (1.0 - np.exp(-t / 0.006)) * np.exp(-t / max(0.16, seconds * 0.38))
        thump = _one_pole_bandpass(noise, 180.0, 520.0) * np.exp(-t / 0.025) * 0.16
        signal = body * env + thump
    elif kind == 'swipe':
        # Tactile paper / highlighter marker brush sweep.
        brush = _one_pole_bandpass(noise, 900.0, 3800.0)
        brush = brush / max(1e-6, np.std(brush))
        env = (u ** 0.7) * ((1.0 - u) ** 1.3)
        signal = brush * env
    elif kind == 'snap':
        # Crisp mechanical detent click for toggles, ticks and step completions.
        env = np.exp(-t / max(0.005, seconds * 0.16))
        signal = (0.55 * np.sin(2 * np.pi * 1380 * t)
                  + 0.32 * np.sin(2 * np.pi * 3150 * t)
                  + 0.22 * noise * np.exp(-t / 0.003)) * env
    elif kind == 'tension':
        # Subtle rising harmonic + filtered air swell leading right into a cut/reveal.
        swell = u ** 2.2 * (1.0 - np.maximum(0.0, (u - 0.92) / 0.08) ** 2)
        f0 = 110.0 + 18.0 * u
        ph = 2 * np.pi * np.cumsum(f0) / RATE
        air = _one_pole_bandpass(noise, 260.0, 1900.0)
        air = air / max(1e-6, np.std(air))
        signal = (0.52 * np.sin(ph) + 0.30 * np.sin(ph * 1.5) + 0.28 * air) * swell
    elif kind == 'sub_riser':
        # Exponential cinema sub-bass riser + filtered breath build designed to end cleanly
        # right before a Re-hook micro-silence or major chapter cut.
        pitch = 38.0 * ((118.0 / 38.0) ** (u ** 1.35))
        ph = 2 * np.pi * np.cumsum(pitch) / RATE
        air = _one_pole_bandpass(noise, 220.0, 2600.0)
        air = air / max(1e-6, np.std(air))
        # Build steadily to u=0.94, then clip sharply over the last 6% so micro-silence feels stark.
        cutoff = 1.0 - np.maximum(0.0, (u - 0.94) / 0.06) ** 1.8
        swell = (u ** 2.0) * cutoff
        body = np.tanh(1.25 * np.sin(ph) + 0.28 * np.sin(2 * ph))
        signal = (0.74 * body + 0.34 * air) * swell
    else:
        raise ValueError(f'Unknown motion sound kind: {kind}')

    fade = min(n // 3, max(2, round(.002 * RATE)))
    signal[:fade] *= np.linspace(0, 1, fade)
    signal[-fade:] *= np.linspace(1, 0, fade)
    peak = np.max(np.abs(signal))
    if peak <= 1e-9:
        raise ValueError(f'Synthesized signal is silent for {kind}')
    signal *= .83 / peak
    pcm = np.rint(signal * 32767).astype('<i2')
    # Mirror soundmix raised-cosine edge fades so the recorded anchor matches the mixed transient.
    mixed = np.abs(pcm.astype(np.float64))
    fade_mix = min(200, n // 2)
    if fade_mix:
        mixed[:fade_mix] *= 0.5 * (1.0 - np.cos(np.pi * np.linspace(0, 1, fade_mix)))
        mixed[-fade_mix:] *= 0.5 * (1.0 + np.cos(np.pi * np.linspace(0, 1, fade_mix)))
    anchor = int(mixed.argmax()) / RATE
    return pcm, anchor


AMBIENCE_PRESETS = ('studio', 'server-room', 'office', 'paper-desk')


def synth_ambience(preset, samples, seed=19):
    """Synthesize a seamless, subtle stereo room-tone / ambience bed to eliminate sterile TTS dead-air."""
    if preset not in AMBIENCE_PRESETS:
        raise ValueError(f"Unknown ambience preset {preset!r}; choose from {', '.join(AMBIENCE_PRESETS)}")
    rng = np.random.default_rng(seed)
    n = int(samples)
    # Generate warm 1/f-like pink noise in stereo via FFT shaping (vectorized and fast).
    freqs = np.fft.rfftfreq(n, 1.0 / RATE)
    freqs[0] = 1.0
    if preset == 'studio':
        shape = (1.0 / (freqs ** 0.72)) * np.exp(-freqs / 1800.0) * (freqs / (freqs + 45.0))
    elif preset == 'server-room':
        shape = (1.0 / (freqs ** 0.65)) * np.exp(-freqs / 2400.0) * (freqs / (freqs + 35.0))
    elif preset == 'office':
        shape = (1.0 / (freqs ** 0.75)) * np.exp(-freqs / 2100.0) * (freqs / (freqs + 55.0))
    else:  # paper-desk
        shape = (1.0 / (freqs ** 0.80)) * np.exp(-freqs / 1400.0) * (freqs / (freqs + 40.0))

    stereo = np.zeros((n, 2), dtype=np.float32)
    for ch in range(2):
        white = rng.normal(0.0, 1.0, n)
        spec = np.fft.rfft(white) * shape
        bed = np.fft.irfft(spec, n)
        if preset == 'server-room':
            t = np.arange(n, dtype=np.float64) / RATE
            # Gentle restrained cooling-fan harmonic (116 Hz + soft 232 Hz)
            bed += 0.09 * np.std(bed) * (np.sin(2 * np.pi * 116.0 * t + ch * 0.7) + 0.35 * np.sin(2 * np.pi * 232.0 * t))
        rms = float(np.sqrt(np.mean(bed ** 2)))
        if rms > 1e-9:
            bed = bed * (0.08 / rms)
        stereo[:, ch] = bed.astype(np.float32)
    fade = min(n // 4, round(0.30 * RATE))
    if fade > 0:
        stereo[:fade] *= (0.5 * (1.0 - np.cos(np.pi * np.linspace(0, 1, fade)))).astype(np.float32)[:, None]
        stereo[-fade:] *= (0.5 * (1.0 + np.cos(np.pi * np.linspace(0, 1, fade)))).astype(np.float32)[:, None]
    return np.clip(stereo, -0.95, 0.95)


def write_ambience_wav(path, preset, samples, seed=19):
    stereo = synth_ambience(preset, samples, seed=seed)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as wf:
        wf.setparams((2, 2, RATE, 0, 'NONE', 'not compressed'))
        wf.writeframes((stereo * 32767.0).astype('<i2').tobytes())
    return path


def create(project, name, kind, seconds, seed):
    if not re.fullmatch('[a-zA-Z0-9_-]+', name):
        raise ValueError('Use a single sound key made of letters, digits, dashes or underscores')
    target = project / 'assets' / 'sfx' / name
    if target.exists():
        raise ValueError(f'{target} already exists. Reuse that take or choose a new key')
    pcm, anchor = synth(kind, seconds, seed)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as tmp:
        folder = Path(tmp) / 'take'
        folder.mkdir()
        take = folder / 'take.wav'
        with wave.open(str(take), 'wb') as stream:
            stream.setparams((1, 2, RATE, 0, 'NONE', 'not compressed'))
            stream.writeframes(pcm.tobytes())
        meta = dict(page=f'assets/sfx/{name}/source.json', author='本片声音设计', stars=None, votes=None,
                    wav_sha256=hashlib.sha256(take.read_bytes()).hexdigest(),
                    generated={'method': 'design_sound.py', 'kind': kind, 'seed': seed, 'rate': RATE},
                    seconds=len(pcm) / RATE, anchor=anchor)
        (folder / 'source.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2))
        folder.rename(target)
    return dict(key=name, trim=[0, meta['seconds']], anchor=anchor)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('kind', choices=sorted(DEFAULTS))
    p.add_argument('--project', type=Path, default=Path('.'))
    p.add_argument('--key', help='Project-local sound key; defaults to motion-KIND')
    p.add_argument('--seconds', type=float)
    p.add_argument('--seed', type=int, default=7)
    args = p.parse_args()
    duration = DEFAULTS[args.kind] if args.seconds is None else args.seconds
    if not math.isfinite(duration) or not .02 <= duration <= 15 or args.seed < 0:
        p.error('Use a motion effect of .02 to 15 seconds and a nonnegative seed')
    try:
        print(json.dumps(create(args.project, args.key or 'motion-' + args.kind, args.kind, duration, args.seed), ensure_ascii=False))
    except (OSError, ValueError) as error:
        p.exit(1, str(error) + '\n')


if __name__ == '__main__':
    main()
