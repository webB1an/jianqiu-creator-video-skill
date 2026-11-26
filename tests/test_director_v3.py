"""Regressions for the v3 production director and the five-domain catalog."""
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import multimodal_critique as critique
import narrative_audit
import studio
from production_run import digest


class DirectorTests(unittest.TestCase):
    def test_eight_media_and_exact_hold_threshold(self):
        configs = [{"visual": "assets/take.mp4"}, {"remotion_effect": {"effect": "quote-card"}},
                   {"visual": "evidence.png", "camera_drift": "push-in"}, {"geo_map": {}},
                   {"math_motion": {}}, {"parallax_25d": {}}, {"value_comparison": {}}, {"explainer_scene": {}}]
        shots = [dict(spec, id=str(i)) for i, spec in enumerate(configs)]
        clock = dict(fps=10, frames=640, shots=[dict(id=row['id'], start_frame=80*i, frames=80)
                                               for i, row in enumerate(shots)])
        diversity = narrative_audit.measure_visual_diversity(clock, {"shots": shots})
        self.assertEqual((diversity["status"], diversity["unique_mediums"], diversity["shannon_entropy"]), ("clear", 8, 3))
        self.assertEqual(diversity["dead_zones_over_8s"], [])
        clock["shots"] = [dict(id=str(i), start_frame=81*i, frames=81) for i in range(3)]
        cards = [dict(configs[1], id=str(i)) for i in range(3)]
        diversity = narrative_audit.measure_visual_diversity(clock, {"shots": cards})
        self.assertEqual(diversity["same_medium_streaks"][0]["count"], 3)
        self.assertEqual([r["seconds"] for r in diversity["dead_zones_over_8s"]], [8.1]*3)

    def test_all_facades_and_cli_navigation(self):
        for name, info in studio.catalog()['domains'].items():
            facade = importlib.import_module('studio.' + name)
            for module in info['modules']:
                self.assertTrue(module['present'])
                self.assertIs(getattr(facade, module['name']), importlib.import_module(module['name']))
            for command in info['entrypoints']:
                script, *args = command.split()
                subprocess.run([sys.executable, SCRIPTS/script, *args], check=True, stdout=subprocess.DEVNULL)

    def test_last_partial_second_all_boards_and_actual_caption_lane(self):
        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            media = project / 'candidate.mp4'
            subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','color=black:s=160x90:r=5',
                            '-t','100.4','-c:v','libx264','-preset','ultrafast',str(media)], check=True)
            tl = project/'timeline.json'
            tl.write_text(json.dumps(dict(fps=5, frames=502, shots=[dict(id='scene', start_frame=0, frames=502)])))
            srt = project/'captions.srt'
            srt.write_text("1\n00:00:00,000 --> 00:01:40,400\nLast cue\n")
            lower_box = [.35, .82, .66, .96]
            (project/'bundle.json').write_text(json.dumps(dict(
                media=media.name, sha256=digest(media), timeline_sha256=digest(tl), sidecars={srt.name:digest(srt)},
                rendered_caption_regions=[dict(start_frame=0, end_frame=500, box=[.3,.02,.7,.13]),
                                          dict(start_frame=500, end_frame=502, box=lower_box)])))
            received = []
            def post(url, **kwargs):
                content = kwargs['json']['messages'][1]['content']
                prompt = content[0]['text'] if isinstance(content, list) else content
                packet = json.loads(prompt.split('\n')[-1])
                received.append((packet['segments'], len(content)-1 if isinstance(content, list) else 0))
                if isinstance(content, list):
                    return Mock(status_code=400)
                reply = {"tickets": [dict(time_s=packet['segments'][-1]['start_s'], issue='inspect', shot='wrong',
                                           suggestion='Inspect last segment', fix_type='review')]}
                return Mock(status_code=200, json=lambda: {"choices":[{"message":{"content":json.dumps(reply)}}]})
            def vision(frames):
                return [dict(faces=[[.4,.85,.56,.96]]) for _ in frames]
            with patch('auto_focus._run_apple_vision_rows', side_effect=vision), patch('requests.post', side_effect=post):
                result = critique.build_packet(project, media, project/'qa', extract_hotspot_clips=False,
                                               backend='deepseek', deepseek_api_key='fake-local-test')
            self.assertEqual(result['duration_seconds'], 100.4)
            self.assertEqual([n for _,n in received], [4,0,2,0])
            rows = [r for segs,n in received if not n for seg in segs for r in seg['visual_telemetry']['samples']]
            self.assertEqual([r['time_s'] for r in rows], list(range(101)))
            self.assertFalse(rows[99]['subject_detection']['overlap'])
            self.assertTrue(rows[100]['subject_detection']['overlap'])
            self.assertEqual(result['segments'][-1]['subtitle_cues'][-1]['end_s'], 100.4)
            tickets = json.loads((project/'qa/tickets.json').read_text())
            self.assertTrue(all(t['shot'] == 'scene' for t in tickets))
            hits = [t for t in tickets if t['fix_type']=='caption_avoid_subject']
            self.assertEqual([(h['time_s'], h['caption_box']) for h in hits], [(100, lower_box)])
            self.assertEqual({h['issue'] for h in tickets} & {'possible_underexposure','audio_dead_air_gap'},
                             {'possible_underexposure','audio_dead_air_gap'})

    def test_all_five_ticket_types_apply_once(self):
        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            edit_path, clock_path = project/'edit.json', project/'timeline.json'
            fixes = ['add_broll_or_motion', 'color_match_or_bridge', 'caption_avoid_subject', 'trim_dead_air', 'add_j_cut']
            shots = [dict(id=f's{i}', visual='assets/a.png', tail=.6) for i in range(5)]
            edit_path.write_text(json.dumps(dict(shots=shots)))
            clock_path.write_text(json.dumps(dict(fps=30, edit_sha256=digest(edit_path),
                shots=[dict(id=shot['id'], start_frame=i*30, frames=30) for i, shot in enumerate(shots)])))
            tickets = [dict(time_s=i, shot=shot['id'], fix_type=fix, reviewed_timeline_sha256=digest(clock_path))
                       for i, (shot, fix) in enumerate(zip(shots, fixes))]
            tickets[2]['subject_box'] = [.2,.08,.6,.23]
            tickets[3]['time_s'] = 3.8
            path = project/'tickets.json'
            path.write_text(json.dumps(tickets))
            self.assertEqual(critique.build_patch_plan(project, path)['applied_count'], 0)
            self.assertEqual(critique.build_patch_plan(project, path, apply=True)['applied_count'], 5)
            after = json.loads(edit_path.read_text())['shots']
            self.assertEqual([after[0]['camera_drift'], after[1]['transition']['name'],
                              after[2]['caption_avoid'][0]['box'], after[3]['tail'], after[4]['j_cut']],
                             ['push-in', 'blur-dissolve', tickets[2]['subject_box'], .22, .25])
            self.assertEqual(critique.build_patch_plan(project, path, apply=True)['applied_count'], 0)

    def test_paths_insufficient_transition_handles_and_unrelated_silence(self):
        for path in ('assets/take.mp4', './assets/take.mp4', '/tmp/example/assets/take.mp4'):
            self.assertEqual(narrative_audit._shot_visual_kind(dict(visual=path)), 'live_video')
            self.assertEqual(narrative_audit._shot_visual_medium(dict(visual=path)), 'live_footage')
        clock = dict(fps=30, shots=[dict(id='a', start_frame=0, frames=4),
                                   dict(id='b', start_frame=4, frames=100, audio_start_frame=8, audio_frames=100)])
        shots = [dict(id=s['id'], visual='a.png') for s in clock['shots']]
        self.assertIsNone(critique._bridge_for_cut(Path.cwd(), dict(shots=shots), clock, 1))
        timing = clock['shots'][1]
        self.assertFalse(critique._ticket_in_tail(dict(time_s=1, end_s=2.1), timing, clock, .7))
        self.assertTrue(critique._ticket_in_tail(dict(time_s=2.9, end_s=3.5), timing, clock, .7))

    def test_changed_pixels_at_same_path_keep_review_ticket_from_applying(self):
        with tempfile.TemporaryDirectory() as td:
            project = Path(td)
            source = project/'plate.png'
            source.write_bytes(b'first image')
            edit_path = project/'edit.json'
            edit_path.write_text(json.dumps(dict(shots=[dict(id='s', visual=source.name)])))
            tl = project/'timeline.json'
            tl.write_text(json.dumps(dict(fps=30, edit_sha256=digest(edit_path),
                                          shots=[dict(id='s', start_frame=0, frames=30)])))
            bundle = project/'bundle.json'
            bundle.write_text(json.dumps(dict(sha256='movie', render_inputs={source.name:digest(source)})))
            tickets = project/'tickets.json'
            tickets.write_text(json.dumps([dict(shot='s', time_s=0, fix_type='add_broll_or_motion',
                reviewed_timeline_sha256=digest(tl), reviewed_candidate=dict(receipt=bundle.name,
                sha256=digest(bundle), media_sha256='movie'))]))
            source.write_bytes(b'new image, identical timing')
            with self.assertRaisesRegex(ValueError, 'render input changed'):
                critique.build_patch_plan(project, tickets, apply=True)
            self.assertNotIn('camera_drift', edit_path.read_text())

    def test_gemini_and_partial_backend_failure(self):
        segments = [dict(segment=i+1, start_s=i, end_s=i+1, mean_motion=1, mean_audio_db=-14,
                         spoken_transcript='text') for i in range(6)]
        with tempfile.TemporaryDirectory() as td:
            board = Path(td)/'board.jpg'
            board.write_bytes(b'jpeg-test-body')
            response = Mock(status_code=200, json=lambda: {'candidates':[{'content':{'parts':[{'text':'{"tickets":'},{'text':'[]}' }]}}]})
            with patch.dict(os.environ, GEMINI_API_KEY='fake-local-test'), patch('requests.post', return_value=response) as post:
                result = critique._review_in_batches('gemini', segments, [board]*6, {}, 4, critique._optional_gemini_critique)
            self.assertEqual([len(row.kwargs['json']['contents'][0]['parts'])-1 for row in post.call_args_list], [4,2])
            self.assertEqual(result['batches'][-1]['end_s'], 6)
            callback = Mock(side_effect=[dict(backend='fake',tickets=[]), dict(backend='fake', error='unavailable')])
            result = critique._review_in_batches('fake', segments, [board]*6, {}, 4, callback)
            self.assertEqual((len(result['completed_batches']), result['failed_start_s']), (1,4))


if __name__ == '__main__':
    unittest.main()
