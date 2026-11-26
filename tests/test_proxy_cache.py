"""Verify proxy picture reuse against revised inputs and damaged cache files."""
import json
import math
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import wave

from PIL import Image

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from production_run import digest


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg and FFprobe required')
class ProxyCacheTest(unittest.TestCase):
    def test_proxy_picture_cache_and_current_optional_graphics(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            audio = project / 'audio'
            audio.mkdir()
            with wave.open(str(audio / 'narration.wav'), 'wb') as track:
                track.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
                samples = (round(7000 * math.sin(math.tau * 300 * n / 48000)) for n in range(48000))
                track.writeframes(b''.join(struct.pack('<h', sample) for sample in samples))
            (audio / 'captions.srt').write_text('1\n00:00:00,000 --> 00:00:01,000\nProxy review\n')
            visual = project / 'visual.png'
            Image.new('RGB', (320, 180), 'steelblue').save(visual)
            edit = project / 'edit.json'
            edit.write_text(json.dumps(dict(shots=[dict(id='s1', visual=visual.name)], fps=24,
                                            chapter_bar=True, audio_intent=[])))
            (project / 'timeline.json').write_text(json.dumps(dict(
                fps=24, frames=24, edit_sha256=digest(edit),
                shots=[dict(id='s1', start_frame=0, frames=24)])))

            def preview(name):
                run = subprocess.run([sys.executable, SCRIPTS / 'export.py', '--proxy', '--out', name],
                                     cwd=project, capture_output=True, text=True)
                self.assertEqual(run.returncode, 0, run.stderr)
                return json.loads(run.stdout)

            original = preview('original.mp4')
            self.assertFalse(original['picture_reused'])
            self.assertTrue(preview('repeated.mp4')['picture_reused'])
            self.assertEqual(digest(project / 'original.mp4'), digest(project / 'repeated.mp4'))

            (project / 'screen-plan.json').write_text(json.dumps(dict(cuts=[dict(start=0, section='New chapter')])))
            self.assertTrue(preview('new-chapter.mp4')['picture_reused'])
            self.assertNotEqual(digest(project / 'original.mp4'), digest(project / 'new-chapter.mp4'))

            Image.new('RGB', (320, 180), 'tomato').save(visual)
            self.assertFalse(preview('new-picture.mp4')['picture_reused'])
            self.assertNotEqual(digest(project / 'new-chapter.mp4'), digest(project / 'new-picture.mp4'))

            cached_picture = project / '.creator-run/proxy-picture/picture.mp4'
            cached_picture.write_bytes(b'corrupt cache artifact')
            self.assertFalse(preview('repaired.mp4')['picture_reused'])
            self.assertEqual(digest(project / 'new-picture.mp4'), digest(project / 'repaired.mp4'))
            self.assertTrue(preview('reused-repair.mp4')['picture_reused'])

            good_output = digest(project / 'reused-repair.mp4')
            visual.write_bytes(b'invalid image')
            failure = subprocess.run([sys.executable, SCRIPTS / 'export.py', '--proxy', '--out', 'reused-repair.mp4'],
                                     cwd=project, capture_output=True, text=True)
            self.assertNotEqual(failure.returncode, 0)
            self.assertEqual(digest(project / 'reused-repair.mp4'), good_output)


if __name__ == '__main__':
    unittest.main()
