"""Render a prepared edit, mix stems, stage a candidate for editorial review."""
import argparse
import fcntl
import json
import hashlib
import sys
import re
import shutil
import subprocess
import tempfile
import time
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace
import color_finish
from source_framing import crop_filter, upright_raster
import motion_layers
import shot_camera
import targeted_edit
from picture_finish import brand_image, chapter_bar_input, overlay_chain
import montage_pins
import explainer_scene_receipt
import remotion_effect_receipt
from floating_captions import caption_input, collect_highlights, kinetic_caption_ranges, top_caption_ranges
from perception_review import expected_audio
from music_direction import RATE, has_effects, mix_stems, normalization_filter, resolve_cues
from covers import SIZES, run as render_covers
from PIL import Image, __version__ as pillow_version
from transitions import STILLS, plan_bridges, shot_speed, source_handles, stitch
from screen_plan import validate as validate_screen_plan
from review_bundle import stage
from release_files import STORE as RELEASE_STORE
from release_version import installed_version
from visual_case import frozen_cards
from project_learning import LOG as LEARNING_LOG
from library_credits import build as build_library_credits
from production_run import WORK, InputSnapshot, atomic_json, digest, install_file, stage_run


def ff(*args, cwd=None, log='error'):
    return subprocess.run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', log, '-y', *map(str, args)],
                          cwd=cwd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True).stderr


def probe(path):
    return json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)]))


def levels(path):
    log = ff('-i', path, '-af', 'loudnorm=I=-16:TP=-1.5:LRA=9:print_format=json', '-vn', '-f', 'null', '-', log='info')
    return json.loads(re.findall(r'\{[^{}]+"input_i"[^{}]+\}', log)[-1])


def check_audio_request(edit):
    for key in expected_audio(edit):
        if key == 'effects' and has_effects(edit):
            continue
        if not edit.get(key) or edit.get(key + '_gain', 1) == 0:
            raise ValueError(f'{key} was requested for this cut. Supply the stem and a nonzero mix gain.')


def framing_filter(shot, video_info, target_size):
    """Build the framing filter for target_size (supports crop, cover, contain, backdrop, paper, ink)."""
    w, h = target_size
    if shot.get('crop') is not None:
        return crop_filter(shot['crop'], video_info, [w, h])
    if 'roughcut_source_sha256' in shot:
        return (f"scale=w='max(2,trunc(min({w},{h}*dar)/2)*2)':"
                f"h='max(2,trunc(min({h},{w}/dar)/2)*2)',setsar=1,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2")
    fit = shot.get('fit', 'contain')
    if fit == 'cover':
        return f'scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}'
    if fit == 'contain':
        return f'scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2'
    if fit == 'backdrop':
        sw, sh, sar = upright_raster(video_info)
        src_dar = (sw * float(sar)) / max(1, sh)
        if abs(src_dar - w / h) <= 0.02:
            return f'scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}'
        fw, fh = max(2, round(w * 0.92 / 2) * 2), max(2, round(h * 0.92 / 2) * 2)
        return (f'format=yuv444p,split[fb_bg][fb_fg];'
                f'[fb_bg]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},'
                f'gblur=sigma=28,eq=brightness=-0.16:saturation=0.72[fb_b];'
                f'[fb_fg]scale={fw}:{fh}:force_original_aspect_ratio=decrease,'
                f'pad=iw+4:ih+4:2:2:color=#F4EDDD@0.22[fb_f];'
                f'[fb_b][fb_f]overlay=(W-w)/2:(H-h)/2:format=yuv444')
    if fit in ('paper', 'ink'):
        bg = '#F4EDDD' if fit == 'paper' else '#131A17'
        fw, fh = max(2, round(w * 0.92 / 2) * 2), max(2, round(h * 0.92 / 2) * 2)
        return f'scale={fw}:{fh}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color={bg}'
    raise ValueError(f"{shot['id']}: fit must be contain, cover, backdrop, paper or ink")


def read_screen_plan(edit, timeline, pins):
    path = pins.pin(edit.get('screen_plan') or 'screen-plan.json', optional=not edit.get('screen_plan'))
    if not path.is_file():
        return None
    plan = json.loads(path.read_text())
    if edit.get('screen_plan'):
        validate_screen_plan(plan, timeline)
    return plan


def render_shot(folder, edit, timeline, pins, index, shot, timing, size, before, after,
                color_sources, remotion_receipts, *, cache=None, encoder=None, proxy=False, black_intervals=None):
    fps = timeline['fps']
    w, h = size
    if 'remotion_effect' in shot:
        remotion_receipts[shot['id']] = remotion_effect_receipt.read_receipt(
            shot, timing, edit, timeline, Path.cwd(), pins.pin, pins)
    if 'roughcut_source_sha256' in shot:
        pins.expect(shot['visual'], shot['roughcut_source_sha256'])
    parallax = None
    if 'parallax_25d' in shot:
        import parallax_25d

        parallax = parallax_25d.verify_project(edit, shot, timing, fps, pins)
        if before or after:
            raise ValueError(f"{shot['id']}: use hold handles at a parallax plate boundary")
    path = pins.pin(shot['visual'])
    if 'explainer_scene' in shot:
        explainer_scene_receipt.verify(shot, timing, fps, pins)
    frames = timing['frames']
    pic = path.suffix.lower() in STILLS
    speed = shot_speed(shot)
    if pic and speed != 1.0:
        raise ValueError(f"{shot['id']}: speed requires a moving video source, not a still")
    render_frames_count = before + frames + after
    shot = motion_layers.resolve_shot(shot, timeline, timing)
    shot = shot_camera.resolve_shot(shot, timeline, timing)
    layers = motion_layers.parse(shot, frames)
    layer_cam = motion_layers.parse_camera(shot, frames)
    plate_cam = shot_camera.parse(shot, frames)
    layer_paths = [pins.pin(layer.file) for layer in layers]
    seek = shot.get('in', 0)
    if before:
        seek = float(seek) - before * speed / fps
    if before and seek < -1e-8:
        raise ValueError(f"Shot {shot['id']} needs {before} source frames before its in point for the transition")
    seek = max(0., seek) if before else seek
    profile = None
    if pic:
        path, profile = color_finish.icc_still(path, folder / f'color-{index}.png')
    source = probe(path)
    video_streams = [s for s in source['streams'] if s['codec_type'] == 'video']
    if not video_streams:
        raise ValueError(f"{shot['id']}: visual contains no picture stream")
    video_info = video_streams[0]
    if parallax:
        parallax_25d.verify_plate(video_info, parallax)
    if shot.get('crop') is not None:
        video_info = next((s for s in video_streams if not s.get('disposition', {}).get('attached_pic')), video_info)
    video_map = '0:v:0' if video_info is video_streams[0] else f"0:{video_info['index']}"
    src_w, src_h, _ = upright_raster(video_info)
    raster_size = (src_w, src_h) if shot.get('crop') is not None else None
    grade = color_finish.effective_grade(edit, shot)
    finish = color_finish.effective_finish(edit, shot)
    color, color_sources[shot['id']] = color_finish.graded_source_filter(
        video_info, None if profile else shot.get('input_color'), grade, raster_size=raster_size, pin=pins.pin)
    if parallax:
        color_sources[shot['id']]['parallax_25d'] = {
            key: parallax[key] for key in ('matte_method', 'foreground_coverage', 'source_sha256')
        }
    if profile:
        color_sources[shot['id']]['icc_to_srgb'] = profile
    if finish:
        color_sources[shot['id']]['finish'] = dict(finish)
    if not pic and float(source['format']['duration']) - seek + speed / fps < render_frames_count * speed / fps:
        raise ValueError(f'{path.name} is too short for {render_frames_count * speed / fps:.2f}s from {seek:.3f}s including transition handles')
    sample_clock = f'setpts=(PTS-STARTPTS)/{speed:.12g},fps={fps}' if speed != 1.0 else f'fps={fps}'
    if not pic and 'roughcut_source_sha256' in shot:
        if speed != 1.0:
            raise ValueError(f"{shot['id']}: adopted roughcut shots keep 1.0x playback speed")
        from roughcut_adoption import picture_sample_clock
        seek, sample_clock = picture_sample_clock(seek, fps, shot.get('roughcut_clock_in', shot.get('in', 0)))
    inputs = ['-loop', '1', '-framerate', fps] if pic else ['-ss', seek]
    post_finish = color_finish.finish_filter(finish)
    name = f'shot{index:04}.mp4'
    preset, crf = ('ultrafast', 26) if proxy else ('veryfast', 16)
    video_args = ['-frames:v', render_frames_count, '-c:v', 'libx264', '-preset', preset, '-crf', crf, *color_finish.FLAGS]

    if plate_cam is not None:
        plate_size = shot_camera.working_size((src_w, src_h), (w, h), plate_cam.max_zoom)
        vf = framing_filter(shot, video_info, plate_size)
        decode_filter = f'{color},{vf},setsar=1' if pic else f'{sample_clock},{color},{vf},setsar=1'
        encode = None
        recipe = [encoder, pins.hashes[pins.key(shot['visual'])], inputs, decode_filter, post_finish, video_args,
                  shot_camera.recipe(shot, plate_cam, plate_size, before, after)]
    else:
        vf = framing_filter(shot, video_info, (w, h))
        base_filter = f'{sample_clock},{color},{vf},setsar=1'
        if post_finish:
            base_filter += ',' + post_finish
        encode = ['-map', video_map, '-an', '-vf', base_filter + ',format=yuv420p', *video_args]
        recipe = [encoder, pins.hashes[pins.key(shot['visual'])], inputs, encode]
    if grade and 'lut' in grade:
        recipe.append(['lut', pins.hashes[pins.key(grade['lut'])]])
    if layers:
        recipe.append(motion_layers.recipe(shot, layers, pins, before, after))
    key = hashlib.sha256(json.dumps(recipe).encode()).hexdigest()
    reused = False
    if cache is not None:
        receipt = cache / (name + '.json')
        try:
            previous = json.loads(receipt.read_text())
        except (FileNotFoundError, ValueError):
            previous = {}
        if not isinstance(previous, dict):
            previous = {}
        cached = cache / name
        reused = previous.get('recipe') == key and cached.is_file() and digest(cached) == previous.get('sha256')
    if reused:
        shutil.copyfile(cached, folder / name)
    elif plate_cam is not None:
        cam_inputs = ['-i', path] if pic else [*inputs, '-i', path]
        shot_camera.encode(plate_cam, cam_inputs, decode_filter, post_finish, video_args, folder / name,
                           plate_size=plate_size, output_size=(w, h), fps=fps, frames=frames,
                           before=before, after=after, video_map=video_map, is_still=pic,
                           layers=layers, layer_paths=layer_paths, layer_camera=layer_cam)
    elif layers:
        motion_layers.encode(layers, layer_paths, [*inputs, '-i', path], base_filter, video_args,
                             folder / name, size=(w, h), fps=fps, frames=frames, before=before, after=after,
                             video_map=video_map, camera=layer_cam)
    else:
        ff(*inputs, '-i', path, *encode, folder / name)
    encoded = probe(folder / name)['streams'][0]
    if int(encoded['nb_frames']) != render_frames_count:
        raise ValueError(f"Shot {shot['id']} did not supply {render_frames_count} frames including source handles")
    if black_intervals is not None:
        black_filter = 'blackdetect=d=0.35:pix_th=0.04:pic_th=0.99'
        if before or after:
            black_filter = f'trim=start_frame={before}:end_frame={before + frames},setpts=PTS-STARTPTS,' + black_filter
        check = ff('-i', folder / name, '-vf', black_filter, '-an', '-f', 'null', '-', log='info')
        for a, b in re.findall(r'black_start:([\d.]+) black_end:([\d.]+)', check):
            offset = timing['start_frame'] / fps
            black_intervals.append(dict(shot=shot['id'], start=round(offset + float(a), 3), end=round(offset + float(b), 3),
                                        intentional=bool(shot.get('allow_black'))))
        if not shot.get('allow_black', False):
            if any(float(v) >= (frames - 2) / fps for v in re.findall(r'black_duration:([\d.]+)', check)):
                raise ValueError(f"Shot {shot['id']} is black throughout. Inspect source or explicitly set allow_black.")
    if cache is not None and not reused:
        install_file(folder / name, cached)
        atomic_json(receipt, {'recipe': key, 'sha256': digest(cached)})
    return reused


def programme_graphics(folder, edit, timeline, captions, size, screen_plan, *, caption_regions=None):
    """Prepare the overlay assets at the full programme's frame clock."""
    fps, total = timeline['fps'], timeline['frames']
    w, h = size
    brand_image(w, h, folder / 'mark.png')
    srt = folder / 'captions.srt'
    shutil.copyfile(captions, srt)
    top_ranges = top_caption_ranges(edit, timeline, 0, total, srt=srt, size=size)
    caption_args = caption_input(folder, srt, w, h, fps, total, style=edit.get('caption_style', 'plate'),
                                 font=edit.get('font', 'Heiti SC'), top_ranges=top_ranges,
                                 timeline=timeline, highlights=collect_highlights(edit),
                                 style_ranges=kinetic_caption_ranges(edit, timeline, 0, total), rendered_regions=caption_regions)
    chapter_args, chapters = chapter_bar_input(
        edit, timeline, folder, w, h, fps, frames=total, screen_plan=screen_plan, top_ranges=top_ranges)
    return (folder / 'mark.png', caption_args, chapter_args, top_ranges), chapters


def picture_overlays(folder, edit, timeline, pins, size, graphics, *, brand_index, start=0, frames=None):
    mark, caption_args, chapter_args, top_ranges = graphics
    fps = timeline['fps']
    frames = timeline['frames'] if frames is None else frames
    caption_index = brand_index + 1 if caption_args else None
    next_index = brand_index + 1 + bool(caption_args)
    pin_args, pin_layers = montage_pins.inputs(
        montage_pins.plan(edit, timeline), folder / 'montage-art', size, start=start, frames=frames,
        first_index=next_index, source_path=pins.pin)
    chapter_index = next_index + len(pin_layers) if chapter_args else None
    overlay = overlay_chain(*size, edit.get('font', 'Heiti SC'), brand_index=brand_index, caption_index=caption_index,
                            fps=fps, top_ranges=top_ranges, montage_pins=pin_layers, chapter_index=chapter_index,
                            source_filter=f'settb=1/{fps},setpts=N+{start}' if start else None)
    return ['-i', mark, *caption_args, *pin_args, *chapter_args], overlay


def render_frames(folder, edit, timeline, prepared_covers, captions, pins, status, skill_version):
    fps = timeline['fps']
    w, h = edit.get('size', [1920, 1080])
    if w * 9 != h * 16:
        raise ValueError('Jianqiu video masters require a 16:9 frame')
    if w % 2 or h % 2 or min(w, h) <= 0:
        raise ValueError('Use positive even frame dimensions')
    if timeline.get('edit_sha256') != pins.hashes['edit.json']:
        raise ValueError('edit.json changed. Re-run prepare.py and rebuild dependent shots and audio.')
    if [s['id'] for s in edit['shots']] != [s['id'] for s in timeline['shots']]:
        raise ValueError('Re-run prepare.py after editing shots')
    music_cues = resolve_cues(edit, timeline, pin=pins.pin)
    cut_frames = {s['start_frame'] for s in timeline['shots']}
    pins.pin('library-uses.json', optional=True)
    pins.pin(LEARNING_LOG, optional=True)
    pins.pin('cover.json', optional=True)
    for card in frozen_cards(Path('edit.json')):
        for path, checksum in card['files'].items():
            pins.expect(path, checksum)
    screen_plan = read_screen_plan(edit, timeline, pins)
    if edit.get('screen_plan'):
        cut_frames.update(c['start'] for c in screen_plan['cuts'])
    black_intervals = []
    color_sources = {}
    status['shots_rendered'] = status['shots_reused'] = 0
    cache = WORK / 'shots'
    cache.mkdir(exist_ok=True)
    transition_adjustments = []
    edges = plan_bridges(edit['shots'], timeline['shots'], fps,
                         default_handles=edit.get('transition_handles', 'hold'), source_path=pins.pin,
                         decisions=transition_adjustments)
    for edge, timing in zip(edges, timeline['shots']):
        if edge:
            cut = timing['start_frame']
            cut_frames.update((cut - edge.left, cut + edge.right))
    cut_times = ','.join(f'{n / fps:.9f}' for n in sorted(cut_frames))
    remotion_receipts = {}
    encoder = subprocess.check_output(['ffmpeg', '-version'], text=True).splitlines()[0]
    for index, (shot, timing) in enumerate(zip(edit['shots'], timeline['shots'])):
        before, after = source_handles(edit['shots'], edges, index)
        reused = render_shot(folder, edit, timeline, pins, index, shot, timing, (w, h), before, after,
                             color_sources, remotion_receipts, cache=cache, encoder=encoder,
                             black_intervals=black_intervals)
        status['shots_reused' if reused else 'shots_rendered'] += 1

    windows = []
    bridges = stitch(folder, edit['shots'], timeline['shots'], fps, ff, probe, edges=edges,
                     source_path=pins.pin, windows_receipt=windows)
    duration = timeline['frames'] / fps
    audio_report = {}
    mix = mix_stems(ff, folder, edit, timeline, pins.pin, music_cues, audio_report=audio_report)
    measured = levels(mix)
    samples = timeline['frames'] * RATE // fps
    norm = (normalization_filter(measured) + f',aformat=sample_rates={RATE}:channel_layouts=stereo,'
            f'atrim=end_sample={samples},asettb=1/{RATE},asetpts=N')
    master = folder / 'master.mp4'
    caption_regions = []
    graphics, chapters = programme_graphics(folder, edit, timeline, captions, (w, h), screen_plan,
                                           caption_regions=caption_regions)
    overlay_args, overlay = picture_overlays(folder, edit, timeline, pins, (w, h), graphics, brand_index=2)
    ff('-i', 'picture.mp4', '-i', 'mix.wav', *overlay_args,
       '-map', '[picture]', '-map', '1:a:0', '-filter_complex', overlay, '-af', norm, '-r', fps,
       '-force_key_frames', cut_times, '-c:v', 'libx264', '-crf', 17, '-preset', 'fast', '-pix_fmt', 'yuv420p',
       *color_finish.FLAGS, '-c:a', 'aac', '-b:a', '256k', '-ar', RATE, '-movie_timescale', RATE,
       '-movflags', '+faststart', master, cwd=folder)
    ff('-xerror', '-i', master, '-f', 'null', '-')
    streams = probe(master)['streams']
    v = next(s for s in streams if s['codec_type'] == 'video')
    a = next(s for s in streams if s['codec_type'] == 'audio')
    actual = levels(master)
    encoded_samples = int(a['duration_ts']) * Fraction(a['time_base']) * RATE
    if (int(v['nb_frames']) != timeline['frames'] or [v['width'], v['height']] != [w, h]
            or Fraction(v['avg_frame_rate']) != fps or encoded_samples != samples
            or abs(float(actual['input_i']) + 16) > 1.5 or float(actual['input_tp']) > -.8):
        raise RuntimeError(f'Encoded master differs from timeline or loudness target: {v}, {a}, {actual}')
    qa = dict(frames=int(v['nb_frames']), duration=duration, size=[w, h], fps=fps, video=v['codec_name'],
              audio=a['codec_name'], channels=a['channels'], LUFS=float(actual['input_i']), dBTP=float(actual['input_tp']),
              decoded=True, transitions=bridges, black_intervals=black_intervals)
    if windows:
        qa['evidence_windows'] = windows
    if transition_adjustments:
        qa['transition_adjustments'] = transition_adjustments
    if chapters:
        qa['chapters'] = [dict(start_frame=a, end_frame=b, title=t) for a, b, t in chapters]
    qa.update(audio_report)
    qa['audio_samples'] = int(encoded_samples)
    qa['mix_normalization'] = measured
    if music_cues:
        qa['music_cues'] = music_cues
    qa['color'] = {'output': color_finish.verify(v), 'shots': color_sources}
    Path('qa').mkdir(exist_ok=True)
    if prepared_covers:
        (folder / 'covers').mkdir()
        for label, size in SIZES.items():
            src = pins.pin(prepared_covers / (label + '.jpg'))
            with Image.open(src) as im:
                if im.size != size:
                    raise ValueError(f'{src} must be {size}')
                im.verify()
            shutil.copyfile(src, folder / 'covers' / (label + '.jpg'))
    else:
        render_covers(edit, folder / 'covers', input_path=lambda path: pins.pin(path, optional=True))
    (folder / 'technical.json').write_text(json.dumps(qa, indent=2))

    def verify_snapshot():
        pins.verify()
        if installed_version() != skill_version:
            raise ValueError('The installed skill changed during export. Retry with one stable version.')

    automatic_credits = build_library_credits(Path('edit.json'), timeline, folder / 'library-credits',
                                              pin=pins.pin, visual_cards=frozen_cards(Path('edit.json')),
                                              remotion_receipts=remotion_receipts) or {}
    return stage(master, folder, technical=folder / 'technical.json', timeline=pins.pin('timeline.json'),
                 edit=Path('edit.json'), render_inputs=pins.recorded(), render_skill_version=skill_version,
                 before_commit=verify_snapshot, automatic_credits=automatic_credits,
                 caption_regions=caption_regions if graphics[1] else None)


def render(folder=None, edit=None, timeline=None, prepared_covers=None, *, captions=None,
           begin_review=False, previous=None):
    if edit is not None:
        check_audio_request(edit)
    with stage_run('export') as (work, status):
        skill_version = installed_version()
        folder = Path(folder).resolve() if folder is not None else work / 'encode'
        folder.mkdir(parents=True, exist_ok=True)
        pins = InputSnapshot(work / 'inputs')
        source_edit = json.loads(pins.pin('edit.json').read_text())
        source_timeline = json.loads(pins.pin('timeline.json').read_text())
        if ((edit is not None and edit != source_edit) or (timeline is not None and timeline != source_timeline)):
            raise ValueError('In-memory edit or timeline differs from the project. Re-read the project.')
        check_audio_request(source_edit)
        generated = source_timeline.get('prepared_files', {})
        if captions is not None:
            pinned_captions = pins.pin(captions)
        else:
            pinned_captions = pins.pin('audio/captions.srt', optional=not generated)
            if not pinned_captions.is_file():
                pinned_captions = pins.pin('out/captions.srt')
        narration = pins.pin('audio/narration.wav')
        if generated:
            presenter = source_timeline.get('presenter_source', {})
            for path_key, hash_key in [('file', 'sha256'), ('timing_file', 'timing_sha256')]:
                if path_key in presenter:
                    pins.expect(presenter[path_key], presenter[hash_key])
            if digest(narration) != generated['audio/narration.wav']:
                raise ValueError('Prepared narration changed. Re-run prepare.py.')
            if captions is None and digest(pinned_captions) != generated['audio/captions.srt']:
                raise ValueError('Prepared subtitles changed. Re-run prepare.py or select approved SRT with --captions.')
            for take in source_timeline.get('voice_takes', []):
                if 'source_clip' in take:
                    original = take['source_clip']
                    pins.expect(original['file'], original['sha256'])
                pins.pin(take['file'])
                if pins.hashes[pins.key(take['file'])] != take['sha256']:
                    raise ValueError(f"Prepared voice take changed: {take['file']}. Re-run prepare.py.")
        status['prepared_binding'] = 'verified' if generated else 'legacy_edit_only'
        status['edit_sha256'] = pins.hashes['edit.json']
        if captions is not None:
            status['caption_override'] = pins.key(captions)
        candidate = render_frames(folder, source_edit, source_timeline, prepared_covers, pinned_captions,
                                  pins, status, skill_version)
        status.update(candidate=str(candidate), candidate_bytes=candidate.stat().st_size, frames=source_timeline['frames'])
        if begin_review:
            cmd = [sys.executable, str(Path(__file__).with_name('editorial_review.py')), 'begin', str(candidate)]
            if previous:
                cmd += ['--from', previous]
            subprocess.run(cmd, check=True)
        return candidate


def read_project(pins, captions, *, check_prepared=False):
    edit = json.loads(pins.pin('edit.json').read_text())
    timeline = json.loads(pins.pin('timeline.json').read_text())
    shots, times = edit['shots'], timeline['shots']
    if [s['id'] for s in shots] != [t['id'] for t in times] or edit.get('fps', 30) != timeline['fps']:
        raise ValueError('Re-run prepare.py after changing the shot order or frame rate')
    cursor = 0
    for timing in times:
        if timing['start_frame'] != cursor or timing['frames'] <= 0:
            raise ValueError('Prepared shot timings must form a contiguous sequence')
        cursor += timing['frames']
    if cursor != timeline['frames'] or cursor <= 0:
        raise ValueError('Prepared shot timings disagree with the programme frame count')
    if timeline.get('edit_sha256') != pins.hashes['edit.json']:
        raise ValueError('edit.json changed. Re-run prepare.py before exporting the revised project.')
    if check_prepared:
        prepared = timeline.get('prepared_files', {})
        pins.pin('audio/narration.wav')
        if prepared:
            pins.expect('audio/narration.wav', prepared['audio/narration.wav'])
            if captions is None:
                pins.expect('audio/captions.srt', prepared['audio/captions.srt'])
        check_audio_request(edit)
    if captions is None:
        srt = pins.pin('audio/captions.srt', optional=True)
        if not srt.is_file():
            srt = pins.pin('out/captions.srt')
    else:
        srt = pins.pin(captions)
    edges = plan_bridges(shots, times, timeline['fps'], source_path=pins.pin,
                         default_handles=edit.get('transition_handles', 'hold'))
    return edit, timeline, srt, edges


def render_picture_group(folder, edit, timeline, pins, size, edges, first, end, *, proxy=False):
    shots, times = edit['shots'][first:end], timeline['shots'][first:end]
    local_edges = [None, *edges[first + 1:end]]
    for i, (shot, timing) in enumerate(zip(shots, times)):
        before, after = source_handles(shots, local_edges, i)
        render_shot(folder, edit, timeline, pins, i, shot, timing, size, before, after, {}, {}, proxy=proxy)
    stitch(folder, shots, times, timeline['fps'], ff, probe, edges=local_edges, source_path=pins.pin, proxy=proxy)


def reuse_proxy_picture(cache, picture, recipe, pins, edit, timeline):
    try:
        receipt = json.loads((cache / 'picture.json').read_text())
        inputs = receipt['inputs']
        if receipt['recipe'] != recipe or not isinstance(inputs, dict):
            return False
        if any(name not in inputs or inputs[name] != checksum for name, checksum in pins.hashes.items()):
            return False
        for name, expected in inputs.items():
            path = Path(name)
            current = digest(path) if path.is_file() else None
            if current != expected or (expected is None and path.exists()):
                return False
        shutil.copyfile(cache / 'picture.mp4', picture)
        if digest(picture) != receipt['sha256']:
            return False
    except (OSError, KeyError, TypeError, ValueError):
        return False
    for name, checksum in inputs.items():
        pins.expect(name, checksum)
    for shot, timing in zip(edit['shots'], timeline['shots']):
        # Receipt paths can resolve differently after a symlink is retargeted.
        if 'remotion_effect' in shot:
            remotion_effect_receipt.read_receipt(shot, timing, edit, timeline, Path.cwd(), pins.pin, pins)
        if 'explainer_scene' in shot:
            explainer_scene_receipt.verify(shot, timing, timeline['fps'], pins)
    return True


def proxy_picture(folder, edit, timeline, pins, size, edges, cache, skill_version):
    """Reuse only the stitched picture. Build programme overlays and audio from current inputs."""
    recipe = dict(project=str(Path.cwd()), skill=skill_version, size=list(size), pillow=pillow_version,
                  ffmpeg=subprocess.check_output(['ffmpeg', '-version'], text=True),
                  ffprobe=subprocess.check_output(['ffprobe', '-version'], text=True))
    picture = folder / 'picture.mp4'
    cache.mkdir(parents=True, exist_ok=True)
    with (cache / 'lock').open('a') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if reuse_proxy_picture(cache, picture, recipe, pins, edit, timeline):
            return True
        render_picture_group(folder, edit, timeline, pins, size, edges, 0, len(edges), proxy=True)
        video = next(stream for stream in probe(picture)['streams'] if stream['codec_type'] == 'video')
        if int(video['nb_frames']) != timeline['frames'] or (video['width'], video['height']) != size:
            raise ValueError('Stitched proxy picture differs from the prepared frame count or size')
        pins.verify()
        if installed_version() != skill_version:
            raise ValueError('The installed skill changed during proxy export. Retry with one stable version.')
        install_file(picture, cache / 'picture.mp4')
        atomic_json(cache / 'picture.json', dict(recipe=recipe, inputs=pins.hashes, sha256=digest(picture)))
    return False


def render_proxy(out_path=None, *, width=960, height=540, captions=None):
    """Render a low-resolution pacing preview with the prepared mix and programme overlays."""
    t0 = time.monotonic()
    size = (width, height)
    if min(size) <= 0 or any(value % 2 for value in size):
        raise ValueError('Use positive even proxy dimensions')
    out_path = Path(out_path or 'out/proxy-preview.mp4').resolve()
    cache = (WORK / 'proxy-picture').resolve()
    if out_path.is_relative_to(cache):
        raise ValueError('Choose a proxy output path outside the picture cache')
    skill_version = installed_version()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.proxy-', dir=out_path.parent) as td:
        folder = Path(td)
        pins = InputSnapshot(folder / 'inputs')
        edit, timeline, srt, edges = read_project(pins, captions, check_prepared=True)
        fps, frames = timeline['fps'], timeline['frames']
        picture_reused = proxy_picture(folder, edit, timeline, pins, size, edges, cache, skill_version)
        mix = mix_stems(ff, folder, edit, timeline, pins.pin, resolve_cues(edit, timeline, pins.pin))
        screen_plan = read_screen_plan(edit, timeline, pins)
        graphics, _ = programme_graphics(folder, edit, timeline, srt, size, screen_plan)
        overlay_args, overlay = picture_overlays(folder, edit, timeline, pins, size, graphics, brand_index=2)
        samples = frames * RATE // fps
        proxy_tmp = folder / 'proxy.mp4'
        ff('-filter_complex_threads', 2, '-i', 'picture.mp4', '-i', mix.name, *overlay_args,
           '-map', '[picture]', '-map', '1:a:0', '-filter_complex', overlay,
           '-af', f'loudnorm=I=-16:TP=-1.5:LRA=11,aformat=sample_rates={RATE}:channel_layouts=stereo,'
                  f'atrim=end_sample={samples},asettb=1/{RATE},asetpts=N',
           '-r', fps, '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', 25, '-pix_fmt', 'yuv420p',
           *color_finish.FLAGS, '-c:a', 'aac', '-b:a', '128k', '-movie_timescale', RATE,
           '-movflags', '+faststart', proxy_tmp, cwd=folder)
        streams = probe(proxy_tmp)['streams']
        video, audio = (next(s for s in streams if s['codec_type'] == kind) for kind in ('video', 'audio'))
        if (int(video['nb_frames']) != frames or (video['width'], video['height']) != size or
                Fraction(video['avg_frame_rate']) != fps or
                int(audio['duration_ts']) * Fraction(audio['time_base']) * RATE != samples):
            raise ValueError('Encoded proxy differs from the prepared picture or audio clock')
        if out_path in {Path(name).resolve() for name in pins.hashes}:
            raise ValueError('Choose a proxy output path distinct from the project inputs')
        pins.verify()
        if installed_version() != skill_version:
            raise ValueError('The installed skill changed during proxy export. Retry with one stable version.')
        proxy_tmp.replace(out_path)
    return dict(proxy=str(out_path), size=list(size), frames=frames, picture_reused=picture_reused,
                elapsed_seconds=round(time.monotonic() - t0, 2))


def patch_ranges(shots, timings, edges, wanted, previous_windows):
    """Include neighboring transition frames that depend on any selected source."""
    affected = []
    for i, (shot, timing) in enumerate(zip(shots, timings)):
        if shot['id'] not in wanted:
            continue
        lo, hi = timing['start_frame'], timing['start_frame'] + timing['frames']
        if edges[i]:
            lo -= edges[i].left
        if i + 1 < len(edges) and edges[i + 1]:
            hi += edges[i + 1].right
        if shot['id'] in previous_windows:
            lo = min(lo, previous_windows[shot['id']]['first_frame'])
        if i + 1 < len(shots) and shots[i + 1]['id'] in previous_windows:
            hi = max(hi, previous_windows[shots[i + 1]['id']]['end_frame'])
        if affected and lo <= affected[-1][1]:
            affected[-1] = (affected[-1][0], max(hi, affected[-1][1]))
        else:
            affected.append((lo, hi))
    return affected


def patch_bundle(base, sha, timeline, pins):
    """Read the matching staged copy of a candidate or published review manifest."""
    release_receipt = base.with_name(base.name + '.review.json')
    if release_receipt.is_file():
        manifest = json.loads(pins.pin(release_receipt).read_text())
        if manifest.get('sha256') != sha:
            raise ValueError('Released master differs from its review receipt')
        receipt_sha = pins.hashes[pins.key(release_receipt)]
        release_dir = base.parent
        if release_dir.parent.name == RELEASE_STORE:
            release_dir = release_dir.parent.parent
        candidate_id = f'{sha[:16]}-{receipt_sha[:16]}'
        candidates = (Path('out/review') / candidate_id, release_dir / 'review' / candidate_id)
        staged = next((folder for folder in candidates if (folder / 'bundle.json').is_file()), None)
        if staged is None:
            raise ValueError('The released master needs its original staged review bundle for shot patching')
        pins.pin(staged / 'bundle.json')
        pins.expect(staged / 'bundle.json', receipt_sha)
    else:
        receipt = base.parent / 'bundle.json'
        if not receipt.is_file():
            return {}, None
        manifest = json.loads(pins.pin(receipt).read_text())
        if manifest.get('media') != base.name:
            return {}, None
        if manifest.get('sha256') != sha:
            raise ValueError('Candidate differs from its review bundle')
        staged = base.parent

    previous_file = staged / 'timeline.json'
    previous = json.loads(pins.pin(previous_file).read_text())
    pins.expect(previous_file, manifest['timeline_sha256'])
    fields = ('id', 'start_frame', 'frames')
    if [[t[k] for k in fields] for t in previous['shots']] != [[t[k] for k in fields] for t in timeline['shots']]:
        raise ValueError('Shot timings differ from the reviewed master. Export the revised timeline in full.')
    narration_sha = manifest.get('render_inputs', {}).get('audio/narration.wav')
    if narration_sha:
        pins.pin('audio/narration.wav')
        if pins.hashes['audio/narration.wav'] != narration_sha:
            raise ValueError('Prepared narration differs from the reviewed master. Export the revised mix in full.')

    base_captions = staged / 'captions.srt'
    pins.pin(base_captions)
    pins.expect(base_captions, manifest['sidecars']['captions.srt'])
    technical = json.loads(pins.pin(staged / 'technical.json').read_text())
    return {row['shot']: row for row in technical.get('transitions', [])}, pins.pin(base_captions)


def patch_picture_groups(times, edges, affected):
    groups = []
    for lo, hi in affected:
        overlapping = {i for i, t in enumerate(times)
                       if lo < t['start_frame'] + t['frames'] and hi > t['start_frame']}
        for i, edge in enumerate(edges):
            cut = times[i]['start_frame']
            if edge and lo < cut + edge.right and hi > cut - edge.left:
                overlapping.update((i - 1, i))
        first, end = min(overlapping), max(overlapping) + 1
        if groups and first < groups[-1][1]:
            groups[-1] = (min(first, groups[-1][0]), max(end, groups[-1][1]))
        else:
            groups.append((first, end))
    return groups


def render_patch_picture(work, edit, timeline, pins, size, edges, graphics, affected):
    """Render the intersecting shots and the source plates needed for their bridges."""
    replacements = {}
    times, fps = timeline['shots'], timeline['fps']
    for first, end in patch_picture_groups(times, edges, affected):
        start_frame = times[first]['start_frame']
        last = times[end - 1]
        end_frame = last['start_frame'] + last['frames']
        folder = work / f'group-{first}'
        folder.mkdir()
        render_picture_group(folder, edit, timeline, pins, size, edges, first, end)
        args, overlay = picture_overlays(folder, edit, timeline, pins, size, graphics, brand_index=1,
                                         start=start_frame, frames=end_frame - start_frame)
        for lo, hi in affected:
            if not start_frame <= lo < hi <= end_frame:
                continue
            replacement = work / f'rendered-{lo}.mp4'
            trim = f'trim=start_frame={lo - start_frame}:end_frame={hi - start_frame},setpts=PTS-STARTPTS'
            ff('-i', folder / 'picture.mp4', *args, '-filter_complex', overlay + f';[picture]{trim}[local]',
               '-map', '[local]', '-an', '-r', fps, '-frames:v', hi - lo, '-c:v', 'libx264',
               '-preset', 'fast', '-crf', 17, '-pix_fmt', 'yuv420p', *color_finish.FLAGS, replacement, cwd=work)
            replacements[(lo, hi)] = replacement
    return replacements


def require_patch_boundaries(order, packets, start, end):
    lo, hi = targeted_edit.bounds(order, packets, start, end)
    if (lo, hi) != (start, end):
        raise ValueError(
            f'Cannot stream-copy around patch frames [{start}, {end}); independent AVC boundaries '
            f'enclose [{lo}, {hi}). Export a full master with the current transition windows before patching.'
        )


def patch_master_shots(base_master, shot_ids, out_path, *, captions=None):
    """Replace selected picture ranges and affected transitions while preserving the base audio."""
    t0 = time.monotonic()
    base_master = Path(base_master).resolve(strict=True)
    out_path = Path(out_path).resolve()
    if out_path == base_master:
        raise ValueError('Choose an output path distinct from --patch-from')
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wanted = {sid.strip() for sid in shot_ids if sid.strip()}
    if not wanted:
        raise ValueError('Specify at least one shot ID for --patch-shots')

    with tempfile.TemporaryDirectory(prefix='.patch-', dir=out_path.parent) as td:
        work = Path(td)
        pins = InputSnapshot(work / 'inputs')
        edit, timeline, srt, edges = read_project(pins, captions)
        unknown = wanted - {s['id'] for s in edit['shots']}
        if unknown:
            raise ValueError(f"Unknown shot IDs for patch export: {', '.join(sorted(unknown))}")
        timings = timeline['shots']
        size = tuple(edit.get('size', [1920, 1080]))
        fps, frames = timeline['fps'], timeline['frames']
        current = pins.pin(base_master)
        base_sha = pins.hashes[pins.key(base_master)]
        stream = targeted_edit.video(current)
        base_fps, order, packets = targeted_edit.timing(current, stream)
        if ((stream['width'], stream['height']) != size or base_fps != fps or len(order) != frames):
            raise ValueError('Patch base frame size, rate and count must match the project timeline')
        color_finish.verify(stream)
        previous, base_srt = patch_bundle(base_master, base_sha, timeline, pins)
        if captions is None and base_srt is not None:
            srt = base_srt
        affected = patch_ranges(edit['shots'], timings, edges, wanted, previous)
        for start, end in affected:
            require_patch_boundaries(order, packets, start, end)
        graphics, _ = programme_graphics(work, edit, timeline, srt, size, read_screen_plan(edit, timeline, pins))
        replacements = render_patch_picture(work, edit, timeline, pins, size, edges, graphics, affected)

        report = []
        for index, (start, end) in enumerate(affected):
            folder = work / f'patch-{index}'
            folder.mkdir()
            replacement = replacements[(start, end)]
            result = targeted_edit.build(
                SimpleNamespace(base=current, replacement=replacement, start_frame=start, end_frame=end), folder,
                dict(base_sha256=digest(current), replacement_sha256=digest(replacement)))
            report.append(dict(affected_frames=[start, end], changed_span=result['changed_span'],
                               audio_tracks=result['unchanged_audio_tracks']))
            current = folder / 'candidate.mp4'
        if pins.key(out_path) in pins.hashes:
            raise ValueError('Choose a patch output path distinct from the project inputs')
        pins.verify()
        current.replace(out_path)

    patched = [dict(shot=t['id'], start_frame=t['start_frame'], end_frame=t['start_frame'] + t['frames'],
                    frames=t['frames']) for t in timings if t['id'] in wanted]
    return dict(output=str(out_path), base_master=str(base_master), patched_shots=patched, intervals=report,
                verified_outside_frames=frames - sum(hi - lo for lo, hi in affected),
                elapsed_seconds=round(time.monotonic() - t0, 2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared-covers', type=Path, help='Directory with three project-designed JPG covers')
    parser.add_argument('--captions', type=Path, help='Approved replacement SRT, used both for the picture and sidecar')
    parser.add_argument('--from', dest='previous', help='Parent SHA-256 for a revision with review tickets')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--stage-only', action='store_true', help='Build a full candidate for technical postcheck before opening an editorial review round')
    mode.add_argument('--proxy', action='store_true', help='Render a 540p pacing preview to --out (default out/proxy-preview.mp4)')
    mode.add_argument('--patch-shots', help='Comma-separated shot IDs to update in the --patch-from master')
    parser.add_argument('--patch-from', type=Path, help='Existing CFR master MP4 to patch when using --patch-shots')
    parser.add_argument('--out', type=Path, help='Output path for --proxy or --patch-shots')
    args = parser.parse_args()
    if args.stage_only and args.previous:
        parser.error('--from starts a review round and cannot be used with --stage-only')
    try:
        if args.proxy:
            print(json.dumps(render_proxy(args.out, captions=args.captions), ensure_ascii=False, indent=2))
        elif args.patch_shots:
            if not args.patch_from:
                raise ValueError('--patch-shots requires --patch-from <master.mp4>')
            target = args.out or Path('out/patched-master.mp4')
            print(json.dumps(patch_master_shots(args.patch_from, args.patch_shots.split(','), target, captions=args.captions), ensure_ascii=False, indent=2))
        else:
            print(render(prepared_covers=args.prepared_covers, captions=args.captions,
                         begin_review=not args.stage_only, previous=args.previous))
    except subprocess.CalledProcessError as error:
        raise SystemExit(error.stderr or str(error))
    except (ValueError, OSError, KeyError) as error:
        raise SystemExit(str(error))


if __name__ == '__main__':
    main()
