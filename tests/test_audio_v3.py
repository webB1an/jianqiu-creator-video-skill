import concurrent.futures
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from jianqiu_voice import JianqiuVoice, _shape_prosody
from music_direction import VOICE_POLISH_PRESETS
from music_stems import RATE, build_pack_plan, list_packs, mix_stems_plan
from pronunciation_review import audit_tech_terms
from prosody_markup import parse_prosody_markup
from sfx_scaffold import scaffold
from soundmix import render_effects
from voice_mood_pack import VALID_MOODS, import_mood, inspect_status


def read_pcm(path):
    with wave.open(str(path)) as audio:
        return audio.getframerate(), np.frombuffer(audio.readframes(audio.getnframes()), '<i2')


class AudioV3Test(unittest.TestCase):
    def test_marked_occurrence_and_exact_pause(self):
        text = '核心*核心*[pause 0.35]核心'
        parsed = parse_prosody_markup(text)
        self.assertEqual(parsed['clean_text'], '核心核心核心')
        self.assertEqual(parsed['emphasis'], [(2, 4)])
        self.assertEqual(parsed['highlights'], ['核心'])
        sr = 24000
        char_size = sr // 5
        original = np.ones(6 * char_size, dtype=np.float32) * .2
        clock = {'chars': [[c, i * .2] for i, c in enumerate(parsed['clean_text'])], 'estimated_indices': []}
        with patch('jianqiu_voice._word_clock', return_value=clock):
            marked = np.array(_shape_prosody(SimpleNamespace(samples=original, sample_rate=sr), parsed).samples)
        pause_start = 2 * char_size + round(2 * char_size / .96)
        pause_end = pause_start + round(.35 * sr)
        np.testing.assert_array_equal(marked[:2 * char_size], original[:2 * char_size])
        self.assertAlmostEqual(marked[3 * char_size] / .2, 1.14, places=5)
        self.assertTrue(np.all(marked[pause_start:pause_end] == 0))
        np.testing.assert_array_equal(marked[pause_end + 150:], original[4 * char_size + 150:])

    def test_tech_terms_use_actual_spoken_and_directed_terms(self):
        result = audit_tech_terms('测试 WebGPU 和多次 GPU，再对比 KV   Cache、QFX。', {'GPU': 'g0 u1'})
        self.assertEqual({row['term'] for row in result}, {'WebGPU', 'KV Cache', 'QFX'})

    def test_concurrent_mood_import_and_checksum_on_read(self):
        voice_assets = ROOT / 'assets/voices/jianqiu'
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp)
            cfg = json.loads((voice_assets / 'voice.json').read_text())
            cfg.pop('moods')
            (dest / 'voice.json').write_text(json.dumps(cfg))
            (dest / cfg['prompt']).write_bytes((voice_assets / cfg['prompt']).read_bytes())
            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
                imports = list(pool.map(
                    lambda m: import_mood(m, voice_assets / cfg['prompt'], cfg['prompt_text'],
                                          source_note='local test fixture', voice_dir=dest), VALID_MOODS))
            voice = JianqiuVoice(voice_dir=dest)
            status = inspect_status(voice_dir=dest)
            self.assertEqual(set(voice._mood_refs), set(VALID_MOODS))
            self.assertTrue(all(row['sha_verified'] for row in status['moods'].values()))
            first = imports[0]
            self.assertEqual(voice.reference_sha256(first['mood']), first['sha256'])
            path = Path(first['output'])
            path.write_bytes(b'corrupt')
            self.assertFalse(inspect_status(voice_dir=dest)['moods'][first['mood']]['configured'])
            with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
                JianqiuVoice(voice_dir=dest)

    def test_four_recorded_foley_types_schedule_and_render(self):
        edit = {'shots': [
            {'id': 's1', 'evidence_window': {'open': [40, 80]}},
            {'id': 's2', 'remotion_effect': {'effect': 'sticker-callout', 'params': {'startFrame': 45}}},
            {'id': 's3', 'value_comparison': {'gap': [45, 70]}},
            {'id': 's4', 'transition': 'dip-ink', 'chapter': 'next'},
        ]}
        timeline = {'fps': 30, 'frames': 720, 'shots': [
            {'id': f's{i + 1}', 'start_frame': i * 180, 'frames': 180} for i in range(4)]}
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / 'edit.json').write_text(json.dumps(edit))
            (project / 'timeline.json').write_text(json.dumps(timeline))
            effects, _, _ = scaffold(project, write=True, apply_edit=True)
            self.assertEqual({row['key'] for row in effects},
                             {'wood-tap-cc0', 'bell-chime-cc0', 'click-rim-cc0', 'cymbal-swell-cc0'})
            output = project / 'foley.wav'
            render_effects(effects, edit, timeline, output)
            rate, samples = read_pcm(output)
            self.assertEqual(len(samples), 2 * 24 * rate)
            self.assertGreater(np.abs(samples).max(), 500)

    def test_bundled_pack_fills_and_cadence_change_audio_at_exact_cuts(self):
        timeline = {'fps': 30, 'frames': 340, 'shots': [
            {'id': 's1', 'start_frame': 0}, {'id': 's2', 'start_frame': 103}, {'id': 's3', 'start_frame': 213}]}
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            for key in list_packs():
                with self.subTest(key=key):
                    plan = build_pack_plan(key, timeline)
                    result = mix_stems_plan(plan, timeline, work, work / 'music.wav')
                    baseline = dict(plan, fills=False, outro_cadence=False)
                    mix_stems_plan(baseline, timeline, work, work / 'baseline.wav')
                    self.assertEqual(result['fills_added'], 2)
                    self.assertEqual(result['fills'][0]['cut_sample'], round(103 * RATE / 30))
                    self.assertTrue(result['outro_cadence_added'])
                    _, samples = read_pcm(work / 'music.wav')
                    _, base = read_pcm(work / 'baseline.wav')
                    self.assertEqual(len(samples), 2 * round(340 * RATE / 30))
                    fill = result['fills'][0]
                    cadence = result['cadence']
                    diff = np.abs(samples.astype(float) - base.astype(float)).reshape(-1, 2)
                    self.assertGreater(diff[fill['start_sample']:fill['cut_sample']].max(), 800)
                    self.assertGreater(diff[cadence['tonic_sample']:].max(), 1500)
                    self.assertLessEqual(np.max(abs(samples)), 27853)
                    self.assertEqual(samples[-1], 0)

    def test_actual_ffmpeg_deesser_changes_acoustic_result(self):
        raw = '-f lavfi -i aevalsrc=0.18*sin(2*PI*300*t)+0.32*sin(2*PI*6500*t):s=48000:d=0.60'.split()
        for name in ('broadcast', 'studio-master'):
            with self.subTest(name=name):
                chain = VOICE_POLISH_PRESETS[name]
                no_deess = ','.join(part for part in chain.split(',') if not part.startswith('deesser='))
                def capture(graph):
                    data = subprocess.check_output(['ffmpeg', '-v', 'error', *raw, '-af', graph,
                                                    '-f', 'f32le', '-'])
                    return np.frombuffer(data, '<f4')[4800:24000]
                filtered, bypass = capture(chain), capture(no_deess)
                self.assertGreater(np.sqrt(np.mean((filtered-bypass)**2)), .001)


if __name__ == '__main__':
    unittest.main()
