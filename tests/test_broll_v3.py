import base64
from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

import broll_router as router


@dataclass
class MockReply:
    payload: dict | None = None
    content: bytes = b''

    def json(self):
        return self.payload

    def raise_for_status(self):
        return None

    def iter_content(self, size):
        for offset in range(0, len(self.content), size):
            yield self.content[offset:offset+size]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None


class BrollV3Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.good = cls.root / 'clean.mp4'
        cls.bad = cls.root / 'bars-blur.mp4'
        cls.cut = cls.root / 'hard-cut.mp4'
        cls.blur = cls.root / 'blur.mp4'
        cls.tail = cls.root / 'tail.png'
        fixture_args = ['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi']
        encode = ['-an', '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p']
        subprocess.run([*fixture_args, '-i', 'testsrc2=size=1280x720:rate=30:duration=1.2',
                        *encode, str(cls.good)], check=True)
        subprocess.run([*fixture_args, '-i', 'testsrc2=size=1280x540:rate=30:duration=1.2',
                        '-vf', 'gblur=sigma=12,pad=1280:720:0:90:black', *encode, str(cls.bad)], check=True)
        subprocess.run([*fixture_args, '-i', 'testsrc2=size=1280x720:rate=30:duration=1.2',
                        '-vf', 'gblur=sigma=26', *encode, str(cls.blur)], check=True)
        subprocess.run([*fixture_args, '-i', 'testsrc2=size=1280x720:rate=30:duration=1.2', '-vf',
                        "drawbox=x=0:y=0:w=1100:h=720:color=0xe85a20:t=fill,"
                        "drawbox=x=0:y=0:w=1100:h=720:color=0x2062ee:t=fill:enable='gte(t,0.4)'", *encode, str(cls.cut)], check=True)
        router.extract_last_frame(cls.good, cls.tail)

    def provider_mock(self, provider, clips):
        post_response, wait_response, credential = {
            'veo': ({'name': 'operations/test-run'}, {'done': True, 'response': {'generatedSamples': [
                {'video': {'uri': 'https://local.invalid/clip.mp4'}}]}}, {'GEMINI_API_KEY': 'test'}),
            'runway': ({'id': 'task'}, {'status': 'SUCCEEDED', 'output': ['https://local.invalid/clip.mp4']},
                       {'RUNWAYML_API_SECRET': 'test'}),
            'kling': ({'data': {'task_id': 'task'}}, {'data': {'task_status': 'succeed', 'task_result': {
                'videos': [{'url': 'https://local.invalid/clip.mp4'}]}}}, {'KLING_ACCESS_KEY': 'test', 'KLING_SECRET_KEY': 'test'}),
            'seedance': ({'id': 'task'}, {'status': 'succeeded', 'content': {'video_url': 'https://local.invalid/clip.mp4'}},
                         {'ARK_API_KEY': 'test'}),
        }[provider]
        remaining = iter(clips)
        def get(url, **kwargs):
            if url == 'https://local.invalid/clip.mp4':
                return MockReply(content=next(remaining).read_bytes())
            return MockReply(wait_response)
        return patch.dict(os.environ, {'PATH': os.environ['PATH'], **credential}, clear=True), patch.object(
            router.requests, 'post', return_value=MockReply(post_response)), patch.object(router.requests, 'get', side_effect=get)

    def test_tail_frame_is_true_last_decoded_frame(self):
        decoded = self.root / 'final-frame.png'
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(self.good),
                        '-vf', 'select=eq(n\\,35)', '-frames:v', '1', str(decoded)], check=True)
        with Image.open(self.tail) as extracted, Image.open(decoded) as actual:
            np.testing.assert_array_equal(np.asarray(extracted), np.asarray(actual))

    def test_four_provider_requests_carry_true_tail_and_poll_results(self):
        for provider in ('veo', 'runway', 'kling', 'seedance'):
            with self.subTest(provider=provider):
                out = self.root / f'{provider}.mp4'
                env, post_calls, get_calls = self.provider_mock(provider, [self.good])
                with env, post_calls as post, get_calls as get:
                    result = router.generate_ai_broll('scene', out, provider=provider, chain_from=self.good, max_retries=0)
                    self.assertTrue(router.generate_ai_broll('scene', out, provider=provider, chain_from=self.good,
                                                            max_retries=0)['cached'])
                self.assertEqual(post.call_count, 1)
                payload = post.call_args.kwargs['json']
                if provider == 'veo':
                    encoded = payload['instances'][0]['image']['bytesBase64Encoded']
                elif provider == 'kling':
                    self.assertIn('/image2video', post.call_args.args[0])
                    self.assertIn('/image2video/task', get.call_args_list[0].args[0])
                    encoded = payload['image']
                else:
                    url = payload['promptImage'] if provider == 'runway' else payload['content'][1]['image_url']['url']
                    self.assertTrue(url.startswith('data:image/png;base64,'))
                    if provider == 'seedance':
                        self.assertEqual(payload['content'][1]['role'], 'first_frame')
                    encoded = url.split(',', 1)[1]
                self.assertEqual(base64.b64decode(encoded), self.tail.read_bytes())
                self.assertEqual(out.read_bytes(), self.good.read_bytes())
                self.assertEqual(Path(result['first_frame']).read_bytes(), self.tail.read_bytes())
                self.assertTrue(result['qc']['ok'])

    def test_remote_qc_retries_bad_clip_and_never_replaces_good_output_on_failure(self):
        out = self.root / 'retry.mp4'
        env, post_calls, get_calls = self.provider_mock('veo', [self.bad, self.good])
        with env, post_calls as post, get_calls:
            result = router.generate_ai_broll('recover once', out, provider='veo', max_retries=1)
        self.assertEqual(post.call_count, 2)
        self.assertIn('Strict QC retry', post.call_args.kwargs['json']['instances'][0]['prompt'])
        self.assertEqual([row['qc']['ok'] for row in result['qc_attempts']], [False, True])
        self.assertTrue(result['qc_attempts'][0]['qc']['letterbox'])
        blurred_qc = router._run_post_qc(self.blur)
        self.assertLess(blurred_qc['sharpness'], 45)
        self.assertFalse(blurred_qc['ok'])
        previous = out.read_bytes()
        receipt = out.with_suffix('.mp4.broll.json')
        success = receipt.read_bytes()

        env, post_calls, get_calls = self.provider_mock('veo', [self.cut, self.bad])
        with env, post_calls as post, get_calls:
            with self.assertRaisesRegex(router.BrollQcError, 'after 2 candidate'):
                router.generate_ai_broll('reject both', out, provider='veo', max_retries=1)
        self.assertEqual(post.call_count, 2)
        self.assertEqual(out.read_bytes(), previous)
        self.assertEqual(receipt.read_bytes(), success)
        rejected = json.loads(out.with_suffix('.mp4.broll.failed.json').read_text())
        self.assertEqual(rejected['status'], 'rejected')
        self.assertTrue(rejected['qc_attempts'][0]['qc']['internal_scene_cuts'])
        self.assertEqual(len(rejected['qc_attempts']), 2)

    def test_zero_retry_cli_rejects_and_publishes_only_failure_receipt(self):
        out = self.root / 'rejected.mp4'
        env, post_calls, get_calls = self.provider_mock('veo', [self.bad])
        args = ['broll_router.py', 'generate', 'reject once', '--provider', 'veo', '--out', str(out), '--max-retries', '0']
        with env, post_calls as post, get_calls, patch.object(sys, 'argv', args):
            with self.assertRaises(SystemExit) as exit_context:
                router.main()
        self.assertEqual(exit_context.exception.code, 1)
        self.assertEqual(post.call_count, 1)
        self.assertFalse(out.exists())
        receipt = json.loads(out.with_suffix('.mp4.broll.json').read_text())
        self.assertEqual(receipt['status'], 'rejected')
        self.assertEqual(len(receipt['qc_attempts']), 1)

    def test_opening_verification_keeps_genuine_hard_cuts(self):
        report = router._run_post_qc(self.cut, procedural_preset='topo-grid', opening_dissolve=True)
        self.assertFalse(report['ok'])
        self.assertTrue(report['internal_scene_cuts'])

    def test_swatch_card_is_never_submitted(self):
        out = self.root / 'blocked.mp4'
        with patch.object(router.requests, 'post') as post:
            for kwargs in ({'provider': 'veo', 'first_frame': self.root / 'style-bible.png'},
                           {'provider': 'runway', 'style_ref': self.root / 'style-bible.png'}):
                with self.assertRaisesRegex(ValueError, 'style-bible.png'):
                    router.generate_ai_broll('reject swatch', out, **kwargs)
        post.assert_not_called()


if __name__ == '__main__':
    unittest.main()
