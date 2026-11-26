"""Generate Mandarin speech using the bundled Jianqiu reference and local ZipVoice."""
import hashlib
import io
import json
import math
import subprocess
import tempfile
import types
import wave
from pathlib import Path

import numpy as np
from prosody_markup import parse_prosody_markup
from setup_zipvoice import ARCHIVE_SHA, VOCODER_SHA, install
from story_craft import MOOD_PROFILES

VOICE_ID = 'jianqiu-zipvoice'
ASSETS = Path(__file__).resolve().parents[1] / 'assets/voices/jianqiu'


def _word_clock(samples, sample_rate, text):
    """Reuse local speech alignment when available, with acoustic timing as a fallback."""
    from speech_clock import MODEL_DIR, align, observations

    if not (MODEL_DIR / 'model.int8.onnx').is_file():
        return None
    source_pcm = np.clip(samples * 32768, -32768, 32767).astype('<i2')
    pcm = np.interp(np.arange(round(len(samples) * 48000 / sample_rate)) * sample_rate / 48000,
                    np.arange(len(samples)), source_pcm).astype('<i2')
    try:
        return align([('voice', 'prosody', text)], observations(pcm, 48000), len(pcm) / 48000)[0]
    except (ValueError, RuntimeError):
        return None


def _shape_prosody(result, parsed):
    samples = np.asarray(result.samples, dtype=np.float32)
    sample_rate = result.sample_rate
    text = parsed['clean_text']
    if not len(samples):
        return result
    clock = _word_clock(samples, sample_rate, text)
    # An acoustic valley provides a local edit point if a symbol could not be aligned.
    hop = max(1, round(sample_rate * .01))
    levels = np.mean(samples[:len(samples) // hop * hop].reshape(-1, hop) ** 2, axis=1)
    weights = np.r_[0, np.cumsum([1.0 if char.isalnum() else 0.0 for char in text])]
    boundaries = {}

    def boundary(index):
        if index in boundaries:
            return boundaries[index]
        if index == 0 or index == len(text):
            return 0 if index == 0 else len(samples)
        estimated = clock and index in clock['estimated_indices']
        if clock and not estimated:
            sample = round(clock['chars'][index][1] * sample_rate)
        else:
            sample = round(len(samples) * weights[index] / max(1, weights[-1]))
            center = round(sample / hop)
            lo = max(0, center - 7)
            hi = min(len(levels), center + 8)
            if hi > lo:
                distance = (np.arange(lo, hi) - center) / 8
                score = levels[lo:hi] + distance ** 2 * max(1e-7, float(levels.max())) * .06
                sample = (lo + int(np.argmin(score))) * hop
        boundaries[index] = min(len(samples), max(0, sample))
        return boundaries[index]

    pauses = {}
    for index, seconds in parsed['pauses']:
        sample = boundary(index)
        pauses[sample] = pauses.get(sample, 0) + round(seconds * sample_rate)
    spans = [(boundary(start), boundary(end)) for start, end in parsed['emphasis']]
    edges = sorted({0, len(samples), *pauses, *(point for pair in spans for point in pair)})
    chunks = []
    for start, end in zip(edges, edges[1:]):
        if start in pauses:
            chunks.append(np.zeros(pauses[start], dtype=np.float32))
        segment = samples[start:end].copy()
        if any(a <= start < end <= b for a, b in spans):
            length = max(len(segment), round(len(segment) / .96))
            segment = np.interp(np.linspace(0, len(segment) - 1, length),
                                np.arange(len(segment)), segment).astype(np.float32)
            ramp = min(round(.025 * sample_rate), len(segment) // 2)
            gain = np.full(len(segment), 1.14, dtype=np.float32)
            if ramp:
                gain[:ramp] = np.linspace(1, 1.14, ramp)
                gain[-ramp:] = gain[:ramp][::-1]
            segment = np.clip(segment * gain, -0.99, 0.99)
        fade = min(round(.006 * sample_rate), len(segment) // 2)
        if fade > 1:
            ramp = np.linspace(0, 1, fade, dtype=np.float32)
            if start in pauses:
                segment[:fade] *= ramp
            if end in pauses:
                segment[-fade:] *= ramp[::-1]
        chunks.append(segment)
    if len(samples) in pauses:
        chunks.append(np.zeros(pauses[len(samples)], dtype=np.float32))
    return types.SimpleNamespace(samples=np.concatenate(chunks).tolist(), sample_rate=sample_rate)


def apply_mood_polish(wav_path, mood):
    """Apply subtle, broadcast-safe acoustic presence/proximity shaping for non-neutral voice moods."""
    if not mood or mood == 'neutral':
        return
    prof = MOOD_PROFILES.get(mood)
    if not prof:
        raise ValueError(f"Unknown voice mood {mood!r}; use {', '.join(MOOD_PROFILES)}")
    eq = prof.get('eq', '')
    if not eq:
        return
    wav_path = Path(wav_path)
    with tempfile.NamedTemporaryFile(dir=wav_path.parent, suffix='.wav', delete=False) as handle:
        tmp = Path(handle.name)
    try:
        subprocess.run(
            ['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(wav_path),
             '-af', f'{eq},alimiter=limit=0.96:attack=3:release=35',
             '-c:a', 'pcm_s16le', str(tmp)],
            check=True,
        )
        tmp.replace(wav_path)
    finally:
        tmp.unlink(missing_ok=True)


def verified_prompt(voice_dir, entry, sample_rate):
    """Read one manifest-pinned mono PCM prompt and validate the exact bytes used by ZipVoice."""
    name = entry['prompt']
    path = (voice_dir / name).resolve()
    if path.parent != voice_dir.resolve():
        raise ValueError(f'Voice prompt escapes its pack: {name}')
    data = path.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    if sha != entry['prompt_sha256']:
        raise ValueError(f'Jianqiu reference audio checksum mismatch: {path}')
    try:
        with wave.open(io.BytesIO(data)) as wav:
            if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, sample_rate):
                raise ValueError(f'Invalid Jianqiu reference audio format: {path}')
            pcm = np.frombuffer(wav.readframes(wav.getnframes()), dtype='<i2')
    except (EOFError, wave.Error) as exc:
        raise ValueError(f'Invalid Jianqiu reference audio file: {path}') from exc
    if not len(pcm) or not pcm.any():
        raise ValueError(f'Empty or silent Jianqiu reference audio: {path}')
    return pcm, sha


class JianqiuVoice:
    def __init__(self, threads=3, *, pronunciation_lexicon=None, trace_phonemes=False, voice_dir=None):
        self.trace_phonemes = trace_phonemes
        self.pronunciation_lexicon = pronunciation_lexicon
        voice_dir = ASSETS if voice_dir is None else Path(voice_dir)
        self.profile = json.loads((voice_dir / 'voice.json').read_text(encoding='utf-8'))
        self.threads = threads
        self.engine = None
        pcm, _ = verified_prompt(voice_dir, self.profile, self.profile['sample_rate'])
        self.reference = (pcm.astype(np.float32) / 32768).tolist()
        self._mood_refs = {}
        for mood, entry in (self.profile.get('moods') or {}).items():
            pcm, sha = verified_prompt(voice_dir, entry, self.profile['sample_rate'])
            prompt_text = entry.get('prompt_text', self.profile['prompt_text'])
            self._mood_refs[mood] = ((pcm.astype(np.float32) / 32768).tolist(), prompt_text, sha)

    @property
    def signature(self):
        p = self.profile
        return [VOICE_ID, 'sherpa-1.12.39-v1', ARCHIVE_SHA, VOCODER_SHA, p['prompt_sha256'],
                p['sample_rate'], p['prompt_text'], p['num_steps']]

    def reference_sha256(self, mood):
        return self._mood_refs[mood][2] if mood in self._mood_refs else self.profile['prompt_sha256']

    def mood_signature(self, mood):
        if not mood or mood == 'neutral':
            return self.signature
        if mood not in MOOD_PROFILES:
            raise ValueError(f"Unknown voice mood {mood!r}; use {', '.join(MOOD_PROFILES)}")
        _, ref_text, ref_sha = self._mood_refs.get(
            mood, (self.reference, self.profile['prompt_text'], self.profile['prompt_sha256']))
        return [*self.signature, 'mood-v2', mood, ref_sha, ref_text, MOOD_PROFILES[mood]['eq']]

    def generate(self, words, speed, *, preserve_pauses=False, mood='neutral', timing_text=None):
        import sherpa_onnx as so
        if not words.strip() or not math.isfinite(speed) or speed <= 0:
            raise ValueError('ZipVoice requires nonempty text and positive finite speed')
        if mood and mood not in MOOD_PROFILES:
            raise ValueError(f"Unknown voice mood {mood!r}; use {', '.join(MOOD_PROFILES)}")
        if self.engine is None:
            root = install()
            model = so.OfflineTtsZipvoiceModelConfig(
                encoder=str(root / 'encoder.int8.onnx'), decoder=str(root / 'decoder.int8.onnx'),
                vocoder=str(root / 'vocos.onnx'), tokens=str(root / 'tokens.txt'),
                data_dir=str(root / 'espeak-ng-data'), lexicon=
                str(root / 'lexicon.txt') + (',' + str(self.pronunciation_lexicon) if self.pronunciation_lexicon else ''))
            config = so.OfflineTtsConfig(model=so.OfflineTtsModelConfig(zipvoice=model, num_threads=self.threads, debug=self.trace_phonemes))
            if not config.validate():
                raise RuntimeError(f'Invalid ZipVoice configuration: {root}')
            self.engine = so.OfflineTts(config)
        if mood in self._mood_refs:
            ref_audio, ref_text, _ = self._mood_refs[mood]
        else:
            ref_audio = self.reference
            ref_text = self.profile['prompt_text']
        ref_sr = self.profile['sample_rate']

        def _make_prompt(eff_speed):
            p = so.GenerationConfig()
            if preserve_pauses:
                p.silence_scale = 1.0
            p.speed = eff_speed
            p.num_steps = self.profile['num_steps']
            p.reference_audio = ref_audio
            p.reference_text = ref_text
            p.reference_sample_rate = ref_sr
            return p

        parsed = parse_prosody_markup(words)
        spoken = parsed['clean_text'] if parsed['has_markup'] else words
        if not spoken.strip():
            raise ValueError('Prosody tags require spoken text')
        result = self.engine.generate(spoken.replace('：', '，').replace('；', '。'), _make_prompt(speed))
        shape = parse_prosody_markup(timing_text) if timing_text is not None else parsed
        return _shape_prosody(result, shape) if shape['has_markup'] else result
