"""Regression coverage for independent picture and speech clocks and subpixel crops."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from editorial_evidence import surface_targets
from portrait_cut import _tracked_crop_at
from prepare import _apply_jl_cuts
from production_run import InputSnapshot, digest
from shot_camera import sample_yuv_frame
from sound_clock import RATE, cue_sample
from transitions import plan_bridges


class PictureTimingTest(unittest.TestCase):
    def test_j_and_l_cuts_keep_audio_spans_and_anchor_picture_cues(self):
        shots = [
            dict(id='a'),
            dict(id='b', j_cut=0.3, transition=dict(name='page', seconds=0.3)),
            dict(id='c', l_cut=0.3),
        ]
        original = [dict(id=shot['id'], start_frame=i * 60, frames=60) for i, shot in enumerate(shots)]
        adjusted = _apply_jl_cuts(shots, original, 30)
        self.assertEqual([(s['start_frame'], s['frames']) for s in adjusted], [(0, 69), (69, 42), (111, 69)])
        self.assertEqual([(s['audio_start_frame'], s['audio_frames']) for s in adjusted],
                         [(0, 60), (60, 60), (120, 60)])
        edge = plan_bridges(shots, adjusted, 30)[1]
        self.assertEqual((adjusted[1]['start_frame'] - edge.left, adjusted[1]['start_frame'] + edge.right), (65, 74))
        sample = cue_sample(dict(offset=0, shift_frames=-3), {}, dict(fps=30), adjusted[1])
        self.assertEqual(sample, 66 * RATE // 30)
        line = dict(id='b-line', start=2.0, end=3.0)
        self.assertEqual(cue_sample(dict(at='@b-line'), {}, dict(fps=30, beats=[line]), adjusted[1]), 2 * RATE)
        shots[0]['lines'] = [dict(text='Look ahead [pause 0.35] *here*')]
        shots[1]['lines'] = [dict(text='[停顿 0.5]*9 frames*')]
        take = dict(shot='b', sample_rate=RATE, start_sample=2 * RATE, end_sample=3 * RATE)
        with tempfile.TemporaryDirectory() as directory:
            targets = surface_targets(Path(directory), dict(shots=shots),
                                      dict(frames=180, fps=30, shots=adjusted, voice_takes=[take]), [])
        narration = next(t for t in targets if t['id'] == 'voice/b/0')
        self.assertEqual((narration['start_frame'], narration['end_frame']), (60, 90))
        self.assertEqual(narration['subject'], '9 frames')
        self.assertNotIn('voice/a/0', {target['id'] for target in targets})
        with self.assertRaisesRegex(ValueError, 'picture_edit'):
            _apply_jl_cuts(shots, original, 30, adopted_picture=True)

    def test_subpixel_crop_changes_pixels_and_keeps_adjacent_cut_frames(self):
        size, output = (40, 16), (9, 16)
        focus = dict(keyframes=[
            dict(frame=0, at=[19.5 / 40, .5]),
            dict(frame=4, at=[21.5 / 40, .5]),
            dict(frame=5, at=[32.5 / 40, .5]),
        ])
        crop = _tracked_crop_at(size, output, 1, focus)
        self.assertEqual([crop(n)[0] for n in (0, 1, 2, 4, 5)], [15, 15.5, 16, 17, 28])
        plane = Image.frombytes('L', size, bytes(range(0, 240, 6)) * 16)
        sampled = [sample_yuv_frame(plane, plane, plane, output, crop(n))[4] for n in range(3)]
        self.assertLess(sampled[0], sampled[1])
        self.assertLess(sampled[1], sampled[2])

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg and FFprobe required')
    def test_project_parallax_plate_rejects_stale_timing_and_source(self):
        import parallax_25d

        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            photo = folder / 'subject.png'
            picture = Image.new('RGB', (160, 90), (28, 52, 76))
            ImageDraw.Draw(picture).ellipse((60, 16, 100, 74), fill=(212, 111, 63))
            picture.save(photo)
            shot = dict(id='photo', visual=str(folder / 'plate.mp4'),
                        parallax_25d=dict(source=str(photo), mask_box=[.25, .1, .75, .95]))
            edit = dict(size=[160, 90], shots=[shot])
            edit_path = folder / 'edit.json'
            edit_path.write_text(json.dumps(edit))
            row = dict(id='photo', start_frame=0, frames=7)
            timeline = dict(edit_sha256=digest(edit_path), shots=[row], fps=30)
            (folder / 'timeline.json').write_text(json.dumps(timeline))
            result = parallax_25d.render_project(folder, 'photo')
            self.assertEqual((result['frames'], result['matte_method']), (7, 'grabcut'))
            pins = InputSnapshot(folder / 'valid')
            parallax_25d.verify_project(edit, shot, row, 30, pins)
            with self.assertRaisesRegex(ValueError, r'frame clock changed.*--shot photo'):
                parallax_25d.verify_project(edit, shot, dict(row, frames=8), 30, pins)
            picture.putpixel((0, 0), (220, 130, 80))
            picture.save(photo)
            with self.assertRaisesRegex(ValueError, r'plate changed.*--shot photo'):
                parallax_25d.verify_project(edit, shot, row, 30, InputSnapshot(folder / 'changed'))


if __name__ == '__main__':
    unittest.main()
