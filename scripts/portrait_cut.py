"""Render a separately composed 9:16 cut on a horizontal master's frame and audio clock."""
import argparse
from fractions import Fraction
import json
from pathlib import Path
import tempfile

import color_finish
from floating_captions import caption_input, cues
from picture_roughcut import STILLS, command, probe


def plan_shots(plan, base, total, fps):
    shots = plan['shots']
    starts = [shot['start_frame'] for shot in shots]
    if (not starts or starts[0] != 0 or any(type(start) is not int for start in starts)
            or any(a >= b for a, b in zip(starts, starts[1:] + [total]))):
        raise ValueError('List portrait shots in order from frame zero, within the master.')
    prepared = []
    for shot, start, end in zip(shots, starts, starts[1:] + [total]):
        path = (base / shot['visual']).resolve(strict=True)
        shape = probe(path, 'width,height,sample_aspect_ratio,duration_ts,time_base')
        if (16 * shape['width'] != 9 * shape['height']
                or shape.get('sample_aspect_ratio') not in (None, 'N/A', '0:1', '1:1')):
            raise ValueError(f'{path.name}: supply a separately composed square-pixel 9:16 shot.')
        if path.suffix.lower() not in STILLS:
            duration = Fraction(shape['time_base']) * int(shape['duration_ts'])
            if duration < (end-start)/fps:
                raise ValueError(f'{path.name}: source picture ends before the planned cut.')
        prepared.append((path, start, end))
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

    colors = []
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.portrait-', dir=output.parent) as temp:
        work = Path(temp)
        manifest = []
        for number, (source, start, end) in enumerate(prepared):
            folder = work / str(number)
            folder.mkdir()
            frames = end - start
            # The shared subtitle renderer sizes type from the virtual band height.
            cap = caption_input(folder, subs, right-left, round(font_px/.045), fps, frames,
                                start_frame=start, style='plate')
            if not cap:
                raise ValueError('Use a plain-text SRT so the portrait lane can be positioned.')
            opt = ['-loop', '1', '-framerate', str(fps)] if source.suffix.lower() in STILLS else []
            source, profile = color_finish.icc_still(source, folder/'icc-srgb.png')
            color_info = probe(source, 'width,height,pix_fmt,codec_name,' + color_finish.FIELDS)
            shot = plan['shots'][number]
            color, report = color_finish.graded_source_filter(
                color_info, None if profile else shot.get('input_color'), shot.get('grade'))
            if profile:
                report['icc_to_srgb'] = profile
            colors.append(dict(start_frame=start, **report))
            filters = (f'[0:v]fps={fps},{color},scale={width}:{height},setsar=1,format=yuv420p[base];'
                       f'[1:v]{color_finish.graphics_alpha()}[text];'
                       f'[base][text]overlay={left}:{bottom}-overlay_h,format=yuv420p')
            filename = f'shot-{number}.mp4'
            command('ffmpeg', '-v', 'error', '-nostdin', *opt, '-i', str(source), *cap,
                    '-filter_complex', filters, '-an', '-frames:v', str(frames),
                    '-c:v', 'libx264', '-threads', '4', '-preset', 'veryfast', '-crf', '15',
                    *color_finish.FLAGS, '-video_track_timescale', str(fps.numerator), str(work / filename))
            count = int(probe(work / filename, 'nb_frames')['nb_frames'])
            if count != frames:
                raise ValueError(f'{source.name} supplies {count} frames, expected {frames}.')
            manifest.extend([f"file '{filename}'", f'duration {float(frames/fps):.9f}'])
        listing = work / 'cuts.txt'
        listing.write_text('\n'.join(manifest) + '\n')
        movie = work / 'portrait.mp4'
        command('ffmpeg', '-v', 'error', '-nostdin', '-f', 'concat', '-safe', '0', '-i', str(listing),
                '-i', str(master), '-map', '0:v:0', '-map', '1:a:0', '-c', 'copy', '-movflags',
                '+faststart', '-video_track_timescale', str(fps.numerator), str(movie))
        result = probe(movie, 'nb_frames,duration,' + color_finish.FIELDS)
        color_finish.verify(result)
        if int(result['nb_frames']) != total or abs(float(result['duration']) - total/fps) > .5/fps:
            raise ValueError('Portrait timing differs from the source cut.')
        movie.replace(output)
    return {'output': str(output), 'frames': total, 'fps': str(fps), 'shots': len(prepared), 'color':{'output':color_finish.OUTPUT, 'shots':colors}}


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('plan', type=Path)
    cli.add_argument('--out', required=True, type=Path)
    args = cli.parse_args()
    try:
        print(json.dumps(render(args.plan, args.out), ensure_ascii=False))
    except (ValueError, OSError, KeyError) as err:
        cli.exit(1, f'{err}\n')
