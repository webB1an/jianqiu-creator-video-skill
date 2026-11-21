"""Generate Mandarin speech using the bundled Jianqiu reference and local ZipVoice."""
import hashlib
import json
import math
import wave
from pathlib import Path

import numpy as np
from setup_zipvoice import ARCHIVE_SHA, VOCODER_SHA, install

VOICE_ID = 'jianqiu-zipvoice'
ASSETS = Path(__file__).resolve().parents[1] / 'assets/voices/jianqiu'


class JianqiuVoice:
    def __init__(self, threads=3, *, pronunciation_lexicon=None, trace_phonemes=False):
        self.trace_phonemes = trace_phonemes
        self.pronunciation_lexicon = pronunciation_lexicon
        self.profile = json.loads((ASSETS / 'voice.json').read_text(encoding='utf-8'))
        self.threads = threads
        self.engine = None
        ref = ASSETS / self.profile['prompt']
        if hashlib.sha256(ref.read_bytes()).hexdigest() != self.profile['prompt_sha256']:
            raise ValueError(f'Jianqiu reference audio checksum mismatch: {ref}')
        with wave.open(str(ref)) as wav:
            if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, self.profile['sample_rate']):
                raise ValueError(f'Invalid Jianqiu reference audio format: {ref}')
            pcm = np.frombuffer(wav.readframes(wav.getnframes()), dtype='<i2')
        self.reference = (pcm.astype(np.float32) / 32768).tolist()

    @property
    def signature(self):
        p = self.profile
        return [VOICE_ID, 'sherpa-1.12.39-v1', ARCHIVE_SHA, VOCODER_SHA, p['prompt_sha256'],
                p['sample_rate'], p['prompt_text'], p['num_steps']]

    def generate(self, words, speed, *, preserve_pauses=False):
        import sherpa_onnx as so
        if not words.strip() or not math.isfinite(speed) or speed <= 0:
            raise ValueError('ZipVoice requires nonempty text and positive finite speed')
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
        prompt = so.GenerationConfig()
        if preserve_pauses:
            prompt.silence_scale = 1.0
        prompt.speed = speed
        prompt.num_steps = self.profile['num_steps']
        prompt.reference_audio = self.reference
        prompt.reference_sample_rate = self.profile['sample_rate']
        prompt.reference_text = self.profile['prompt_text']
        return self.engine.generate(words.replace('：', '，').replace('；', '。'), prompt)
