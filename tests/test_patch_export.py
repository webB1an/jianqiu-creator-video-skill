"""Local FFmpeg regression for the 540p proxy and frame-accurate shot patch."""
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

from PIL import Image, ImageDraw


SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
from review_bundle import fingerprint, promote
from targeted_edit import audio_signature, digest, frame_hashes


def make_project(project):
    assets, audio = project / 'assets', project / 'audio'
    assets.mkdir()
    audio.mkdir()
    shots = []
    for index, color in enumerate(('red', 'green', 'blue', 'maroon', 'teal'), 1):
        image = Image.new('RGB', (320, 180), color)
        ImageDraw.Draw(image).line((0, 0, 320, 180), fill='white', width=3)
        image.save(assets / f'{index}.png')
        shots.append(dict(id=f's{index}', visual=f'assets/{index}.png'))
    for name, color in (('old', 'yellow'), ('new', 'magenta'), ('pin', 'cyan')):
        image = Image.new('RGBA', (56, 40))
        ImageDraw.Draw(image).rectangle((2, 2, 52, 36), fill=color)
        image.save(assets / f'{name}.png')
    Image.new('RGB', (320, 180), 'orange').save(assets / 'replacement.png')
    shots[1]['motion_layers'] = [dict(file='assets/old.png', width=.16,
                                      keys=[dict(frame=0, at=[.48, .36]), dict(frame=22, at=[.57, .36])])]
    shots[2]['transition'] = dict(name='blur-dissolve', seconds=.5)
    shots[2]['camera_drift'] = 'push-in'
    edit = dict(title='Shot patch regression', fps=24, size=[320, 180], caption_style='editorial',
                audio_intent=[], chapter_bar=True, chapters=[dict(title='ONE', shot='s1'),
                                                           dict(title='TWO', shot='s4')],
                montage_pins=[dict(file='assets/pin.png', from_shot='s1', through_shot='s5',
                                   width=.15, at=[.24, .30])], shots=shots)
    edit_path = project / 'edit.json'
    edit_path.write_text(json.dumps(edit))
    timeline = dict(fps=24, frames=120, edit_sha256=digest(edit_path),
                    shots=[dict(id=s['id'], start_frame=i * 24, frames=24) for i, s in enumerate(shots)])
    (project / 'timeline.json').write_text(json.dumps(timeline))
    (audio / 'captions.srt').write_text('1\n00:00:00,250 --> 00:00:04,750\nCaption crossing every cut\n\n')
    with wave.open(str(audio / 'narration.wav'), 'wb') as track:
        track.setparams((1, 2, 48000, 0, 'NONE', 'not compressed'))
        samples = (round(7000 * math.sin(math.tau * 300 * n / 48000)) for n in range(240000))
        track.writeframes(b''.join(struct.pack('<h', sample) for sample in samples))
    return edit_path, edit


def read_frame(path, n):
    pixels = subprocess.check_output([
        'ffmpeg', '-nostdin', '-v', 'error', '-i', str(path), '-map', '0:v:0',
        '-vf', f'select=eq(n\\,{n})', '-frames:v', '1', '-vsync', '0',
        '-threads:v', '1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-',
    ])
    return Image.frombytes('RGB', (320, 180), pixels)


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg and FFprobe required')
class ShotPatchExportTest(unittest.TestCase):
    def test_proxy_and_disjoint_shot_patches(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            edit_path, edit = make_project(project)

            def cli(*args, check=True):
                result = subprocess.run([sys.executable, SCRIPTS / 'export.py', *args],
                                        cwd=project, capture_output=True, text=True)
                if check:
                    self.assertEqual(result.returncode, 0, result.stderr)
                return result

            base = project / cli().stdout.strip().splitlines()[-1]
            base_sha = digest(base)
            proxy = json.loads(cli('--proxy').stdout)
            self.assertEqual((proxy['size'], proxy['frames']), ([960, 540], 120))
            self.assertEqual(len(frame_hashes(Path(proxy['proxy']))), 120)

            edit['shots'][1]['motion_layers'][0]['file'] = 'assets/new.png'
            edit['shots'][4]['visual'] = 'assets/replacement.png'
            edit_path.write_text(json.dumps(edit))
            patched = project / 'out/patch.mp4'
            stale = cli('--patch-shots', 's2', '--patch-from', base, '--out', patched, check=False)
            self.assertNotEqual(stale.returncode, 0)
            self.assertIn('Re-run prepare.py', stale.stderr)
            self.assertFalse(patched.exists())
            timeline_path = project / 'timeline.json'
            timeline = json.loads(timeline_path.read_text())
            timeline['edit_sha256'] = digest(edit_path)
            timeline_path.write_text(json.dumps(timeline))

            latest = project / 'out/review/latest.json'
            review_head = latest.read_bytes()
            technical_cut = project / cli('--stage-only').stdout.strip().splitlines()[-1]
            self.assertTrue(technical_cut.is_file())
            self.assertNotEqual(digest(technical_cut), base_sha)
            self.assertEqual(latest.read_bytes(), review_head)

            released = project / 'out/final.mp4'
            promote(base, released, base_sha, fingerprint(base))
            report = json.loads(cli('--patch-shots', 's2,s5,s2', '--patch-from', released, '--out', patched).stdout)
            self.assertEqual([row['affected_frames'] for row in report['intervals']], [[24, 54], [96, 120]])
            self.assertEqual(report['verified_outside_frames'], 66)

            original, revised = frame_hashes(base), frame_hashes(patched)
            self.assertEqual(len(revised), 120)
            self.assertEqual(original[:24] + original[54:96], revised[:24] + revised[54:96])
            for n in (35, 49, 100):
                self.assertNotEqual(original[n], revised[n])
            self.assertEqual(audio_signature(base), audio_signature(patched))
            self.assertEqual(digest(base), base_sha)

            picture = read_frame(patched, 35)
            magenta = picture.getpixel((162, 64))
            cyan = picture.getpixel((76, 53))
            amber = picture.getpixel((10, 0))
            self.assertTrue(magenta[0] > 200 and magenta[1] < 50 and magenta[2] > 200)
            self.assertTrue(cyan[0] < 50 and cyan[1] > 200 and cyan[2] > 200)
            self.assertTrue(amber[0] > 175 and 90 < amber[1] < 200 and amber[2] < 120)
            caption_band = picture.crop((80, 145, 240, 177))
            white_pixels = sum(min(caption_band.getpixel((x, y))) > 200
                               for y in range(caption_band.height) for x in range(caption_band.width))
            self.assertGreater(white_pixels, 30)

            legacy = project / 'out/legacy.mp4'
            subprocess.run([
                'ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', base, '-map', '0',
                '-c:v', 'libx264', '-sc_threshold', '0', '-force_key_frames', '0,1,2,3,4',
                '-c:a', 'copy', legacy,
            ], check=True)
            denied = project / 'out/denied.mp4'
            result = cli('--patch-shots', 's2', '--patch-from', legacy, '--out', denied, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('independent AVC boundaries enclose [24, 72)', result.stderr)
            self.assertFalse(denied.exists())


if __name__ == '__main__':
    unittest.main()
