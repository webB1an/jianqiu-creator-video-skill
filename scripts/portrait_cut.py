"""Render a separately composed 9:16 cut on a horizontal master's frame and audio clock."""
import argparse
from fractions import Fraction
import json
from math import lcm
from pathlib import Path
import tempfile

import color_finish
import shot_camera
from floating_captions import caption_input, collect_highlights, cues, kinetic_caption_ranges, top_caption_ranges
from picture_finish import brand_image, caption_y
from picture_roughcut import STILLS, command, probe
from source_framing import upright_raster


def _crop_window_dims(src_w, src_h, dst_w, dst_h):
    target_ar = dst_w / dst_h
    src_ar = src_w / max(1, src_h)
    if src_ar > target_ar:
        crop_h = src_h
        crop_w = max(2, int(round(src_h * target_ar)) // 2 * 2)
    else:
        crop_w = src_w
        crop_h = max(2, int(round(src_w / target_ar)) // 2 * 2)
    return crop_w, crop_h


def _focus_crop_filter(src_w, src_h, dst_w, dst_h, focus_spec, sar=1):
    """Compute a clamped, subject-centered static crop."""
    crop_w, crop_h = _crop_window_dims(src_w, src_h, dst_w / sar, dst_h)
    cx, cy = focus_spec
    x0 = max(0, min(src_w - crop_w, int(round(cx * src_w - crop_w * 0.5))))
    y0 = max(0, min(src_h - crop_h, int(round(cy * src_h - crop_h * 0.5))))
    return f'crop={crop_w}:{crop_h}:{x0}:{y0}:exact=1,scale={dst_w}:{dst_h}:flags=lanczos'


def _tracked_crop_at(source_size, dest_size, sar, focus, camera=None):
    """Map timeline frames to floating source coordinates, including optional manual camera movement."""
    src_w, src_h = source_size
    dst_w, dst_h = dest_size
    aspect = float(dst_w / (dst_h * sar))
    crop_w, crop_h = min(src_w, src_h * aspect), min(src_h, src_w / aspect)
    keys = focus['keyframes']
    track = shot_camera.ShotCamera(tuple(k['frame'] for k in keys),
                                   tuple((*k['at'], 1.0) for k in keys), 'linear', ())

    def at(frame):
        cx, cy, _ = track.pose(frame)
        x = max(0.0, min(src_w - crop_w, cx * src_w - crop_w / 2))
        y = max(0.0, min(src_h - crop_h, cy * src_h - crop_h / 2))
        if camera is not None:
            l, t, r, b = shot_camera.crop_box((crop_w, crop_h), camera.pose(frame))
            return x + l, y + t, min(src_w, x + r), min(src_h, y + b)
        return x, y, min(src_w, x + crop_w), min(src_h, y + crop_h)

    return at


def plan_shots(plan, base, total, fps):
    shots = plan['shots']
    starts = [shot['start_frame'] for shot in shots]
    if (not starts or starts[0] != 0 or any(type(start) is not int for start in starts)
            or any(a >= b for a, b in zip(starts, starts[1:] + [total]))):
        raise ValueError('List portrait shots in order from frame zero, within the master.')
    plan_auto = plan.get('auto_focus', False)
    prepared = []
    for shot, start, end in zip(shots, starts, starts[1:] + [total]):
        path = (base / shot['visual']).resolve(strict=True)
        shape = probe(path, 'width,height,sample_aspect_ratio,duration_ts,time_base:stream_side_data=rotation')
        sw, sh, sar = upright_raster(shape)
        is_9x16 = 16 * sw == 9 * sh and sar == 1
        crop_focus = shot.get('crop_focus', plan_auto)
        if not is_9x16 and not crop_focus:
            raise ValueError(f'{path.name}: supply a separately composed square-pixel 9:16 shot (or set crop_focus / auto_focus).')
        is_still = path.suffix.lower() in STILLS
        if not is_still:
            duration = Fraction(shape['time_base']) * int(shape['duration_ts'])
            if duration < (end - start) / fps:
                raise ValueError(f'{path.name}: source picture ends before the planned cut.')
        focus_spec = None
        if not is_9x16 and crop_focus:
            if isinstance(crop_focus, list) and len(crop_focus) == 2:
                focus_spec = [float(crop_focus[0]), float(crop_focus[1])]
            else:
                import auto_focus
                dur_s = float((end - start) / fps)
                if not is_still and crop_focus != 'static' and end - start >= 2:
                    tr = auto_focus.track_media(path, sample_interval=0.5, dur_s=dur_s, fps=fps, frames=end - start)
                    if len(tr['keyframes']) > 1:
                        focus_spec = dict(
                            mode='dynamic-track',
                            backend=tr['backend'],
                            x_span=tr['x_span'],
                            keyframes=tr['keyframes'],
                            focal_point=tr['keyframes'][0]['at'],
                        )
                    else:
                        focus_spec = tr['keyframes'][0]['at']
                else:
                    focus_spec = auto_focus.analyze_media(path, dur_s=dur_s)['focal_point']
        prepared.append((path, start, end, focus_spec))
    return prepared


def render(plan_path, output):
    base = plan_path.parent
    plan = json.loads(plan_path.read_text())
    master = (base / plan['master']).resolve(strict=True)
    subs = (base / plan['captions']).resolve(strict=True)
    width, height = plan.get('size', [1080, 1920])
    if min(width, height) < 2 or width % 2 or height % 2 or width * 16 != height * 9:
        raise ValueError('Choose even dimensions for a 9:16 picture.')
    info = probe(master, 'width,height,nb_frames,r_frame_rate,avg_frame_rate,duration,start_time')
    if info['width'] * 9 != info['height'] * 16:
        raise ValueError('Expected the original 16:9 cut as the sound and timing reference.')
    fps = Fraction(info['r_frame_rate'])
    if fps <= 0 or Fraction(info['avg_frame_rate']) != fps or abs(float(info.get('start_time', 0))) > .001:
        raise ValueError('Supply a constant-rate master starting at zero.')
    audio_rate = int(probe(master, 'sample_rate', stream='a:0')['sample_rate'])
    total = int(info.get('nb_frames') or probe(master, 'nb_read_frames', count=True)['nb_read_frames'])
    prepared = plan_shots(plan, base, total, fps)
    if output.resolve() in {master, subs, plan_path.resolve(), *(s[0] for s in prepared)}:
        raise ValueError('Write the portrait copy to its own MP4.')
    left, right, bottom, font_px = plan['captions_px']
    if not (0 <= left < right <= width and 0 < bottom <= height and 12 <= font_px < bottom):
        raise ValueError('Check caption left, right, lower edge and font size in output pixels.')
    for start, end, words in cues(subs, fps):
        if start < 0 or end > total:
            raise ValueError(f'Subtitle outside master: {words}')

    top = plan.get('captions_top_px', max(0, min(height - bottom, bottom - 2 * font_px)))
    if type(top) is not int or not 0 <= top < bottom:
        raise ValueError('captions_top_px must be above the portrait bottom lane')
    named = [dict(shot, id=str(i)) for i, shot in enumerate(plan['shots'])]
    timing = [dict(id=str(i), start_frame=a, frames=b - a) for i, (_, a, b, _) in enumerate(prepared)]
    edit = dict(shots=named, font=plan.get('font', 'Heiti SC'), caption_style=plan.get('caption_style', 'plate'),
                caption_highlights=plan.get('caption_highlights', []),
                grade=plan.get('grade'), finish=plan.get('finish'))
    timeline_path = master.parent / 'timeline.json'
    master_timeline = json.loads(timeline_path.read_text()) if timeline_path.is_file() else None
    upper = top_caption_ranges(edit, dict(fps=fps, shots=timing), 0, total, srt=subs, size=(width, height),
                               portrait_lane=(left, right, top, bottom, font_px))
    highlights = collect_highlights(edit)
    show_brand = bool(plan.get('brand', False))

    colors = []
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.portrait-', dir=output.parent) as temp:
        work = Path(temp)
        mark_file = work / 'mark.png'
        if show_brand:
            brand_image(width, width, mark_file)
        manifest = []
        for number, (source, start, end, focus_xy) in enumerate(prepared):
            folder = work / str(number)
            folder.mkdir()
            frames = end - start
            top_ranges = [(max(a, start) - start, min(b, end) - start) for a, b in upper if a < end and b > start]
            style_ranges = kinetic_caption_ranges(edit, dict(fps=fps, shots=timing), start, frames)
            cap = caption_input(folder, subs, right - left, round(font_px / .045), fps, frames,
                                start_frame=start, style=edit['caption_style'], font=edit['font'],
                                top_ranges=top_ranges, timeline=master_timeline, highlights=highlights,
                                style_ranges=style_ranges)
            if not cap:
                raise ValueError('Use a plain-text SRT so the portrait lane can be positioned.')
            pic = source.suffix.lower() in STILLS
            opt = ['-loop', '1', '-framerate', str(fps)] if pic else []
            source, profile = color_finish.icc_still(source, folder / 'icc-srgb.png')
            color_info = probe(source, 'width,height,sample_aspect_ratio,pix_fmt,codec_name,'
                               + color_finish.FIELDS + ':stream_side_data=rotation')
            sw, sh, sar = upright_raster(color_info)
            shot = named[number]
            grade = color_finish.effective_grade(edit, shot)
            finish = color_finish.effective_finish(edit, shot)
            color, report = color_finish.graded_source_filter(
                color_info, None if profile else shot.get('input_color'), grade,
                raster_size=(sw, sh), pin=lambda rel: (base / rel).resolve())
            if profile:
                report['icc_to_srgb'] = profile
            if finish:
                report['finish'] = dict(finish)
            if focus_xy is not None:
                report['crop_focus'] = focus_xy
            colors.append(dict(start_frame=start, **report))

            fin_chain = color_finish.finish_filter(finish)
            plate_cam = shot_camera.parse(shot, frames)
            if isinstance(focus_xy, dict) and focus_xy.get('mode') == 'dynamic-track':
                cam_file = folder / 'tracked.mp4'
                shot_camera.encode(
                    plate_cam, ['-i', source], f'fps={fps},{color}', fin_chain,
                    ['-frames:v', frames, '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', 0, *color_finish.FLAGS],
                    cam_file, plate_size=(sw, sh), output_size=(width, height), fps=fps, frames=frames,
                    before=0, after=0, crop_at=_tracked_crop_at((sw, sh), (width, height), sar, focus_xy, plate_cam))
                src_inputs = ['-i', str(cam_file)]
                base_vf = '[0:v]format=yuv420p[base];'
            elif plate_cam is not None:
                plate_size = shot_camera.working_size((sw, sh), (width, height), plate_cam.max_zoom)
                cam_file = folder / 'cam.mp4'
                scale_expr = (_focus_crop_filter(sw, sh, *plate_size, focus_xy, sar)
                              if focus_xy is not None else f'scale={plate_size[0]}:{plate_size[1]}')
                dec_f = f"{color},{scale_expr},setsar=1" if pic else f"fps={fps},{color},{scale_expr},setsar=1"
                shot_camera.encode(
                    plate_cam, ['-i', source] if pic else ['-i', source], dec_f, fin_chain,
                    ['-frames:v', frames, '-c:v', 'libx264', '-preset', 'veryfast', '-crf', 14, *color_finish.FLAGS],
                    cam_file, plate_size=plate_size, output_size=(width, height), fps=fps, frames=frames,
                    before=0, after=0, is_still=pic)
                src_inputs = ['-i', str(cam_file)]
                base_vf = '[0:v]format=yuv420p[base];'
            else:
                src_inputs = [*opt, '-i', str(source)]
                post = f',{fin_chain}' if fin_chain else ''
                scale_expr = (_focus_crop_filter(sw, sh, width, height, focus_xy, sar)
                              if focus_xy is not None else f'scale={width}:{height}')
                base_vf = f'[0:v]fps={fps},{color},{scale_expr},setsar=1{post},format=yuv420p[base];'

            y = caption_y(top_ranges, fps, top=str(top), bottom=f'{bottom}-overlay_h')
            extra_inputs = []
            if show_brand:
                extra_inputs = ['-i', str(mark_file)]
                filters = (f'{base_vf}[2:v]{color_finish.graphics_alpha()}[mark];'
                           f'[base][mark]overlay=W-w-{round(width * .05)}:{round(height * .042)}[branded];'
                           f'[1:v]{color_finish.graphics_alpha()}[text];'
                           f'[branded][text]overlay={left}:{y},format=yuv420p')
            else:
                filters = (f'{base_vf}[1:v]{color_finish.graphics_alpha()}[text];'
                           f'[base][text]overlay={left}:{y},format=yuv420p')
            filename = f'shot-{number}.mp4'
            command('ffmpeg', '-v', 'error', '-nostdin', *src_inputs, *cap, *extra_inputs,
                    '-filter_complex', filters, '-an', '-frames:v', str(frames),
                    '-c:v', 'libx264', '-threads', '4', '-preset', 'veryfast', '-crf', '15',
                    *color_finish.FLAGS, '-video_track_timescale', str(fps.numerator), str(work / filename))
            count = int(probe(work / filename, 'nb_frames')['nb_frames'])
            if count != frames:
                raise ValueError(f'{source.name} supplies {count} frames, expected {frames}.')
            manifest.extend([f"file '{filename}'", f'duration {float(frames / fps):.9f}'])
        listing = work / 'cuts.txt'
        listing.write_text('\n'.join(manifest) + '\n')
        movie = work / 'portrait.mp4'
        command('ffmpeg', '-v', 'error', '-nostdin', '-f', 'concat', '-safe', '0', '-i', str(listing),
                '-i', str(master), '-map', '0:v:0', '-map', '1:a:0', '-c', 'copy', '-movflags',
                '+faststart', '-video_track_timescale', str(fps.numerator),
                '-movie_timescale', str(lcm(fps.numerator, audio_rate)), str(movie))
        result = probe(movie, 'nb_frames,duration,' + color_finish.FIELDS)
        color_finish.verify(result)
        if int(result['nb_frames']) != total or abs(float(result['duration']) - total / fps) > .5 / fps:
            raise ValueError('Portrait timing differs from the source cut.')
        movie.replace(output)
    return {'output': str(output), 'frames': total, 'fps': str(fps), 'shots': len(prepared),
            'caption_top_frames': upper, 'color': {'output': color_finish.OUTPUT, 'shots': colors}}


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('plan', type=Path)
    cli.add_argument('--out', required=True, type=Path)
    args = cli.parse_args()
    try:
        print(json.dumps(render(args.plan, args.out), ensure_ascii=False))
    except (ValueError, OSError, KeyError) as err:
        cli.exit(1, f'{err}\n')
