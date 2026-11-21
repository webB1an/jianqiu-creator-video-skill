"""Decode an original file for a scene in sRGB on its exact display clock."""
import argparse
from bisect import bisect_right
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import subprocess

from PIL import Image

from action_takes import source
from color_finish import browser_frame_filter
from explainer_scene_receipt import CLIP_ENGINE
from production_run import digest


def extract(path, target, start, seconds, fps, overrides):
    root = Path(__file__).resolve().parents[1]
    engine = {name: digest(root / name) for name in CLIP_ENGINE}
    info = source(path)
    ticks, unit = info['ticks'], info['tb']
    # A media-file offset is relative to its container start, as with its sound.
    fmt = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries',
                     'format=start_time', '-of', 'json', str(path)]))['format']
    origin = Fraction(fmt.get('start_time', '0'))
    if abs(origin - ticks[0] * unit) <= Fraction(1, 2_000_000):
        origin = ticks[0] * unit  # ffprobe serializes format.start_time in microseconds
    begin = (origin + start) / unit
    frame_count = seconds * fps
    if round(frame_count) > 0 and abs(round(frame_count) - frame_count) <= Fraction(1, 10**9):
        seconds = Fraction(round(frame_count), fps)  # JSON seconds can represent a repeating decimal
    if start < 0 or seconds <= 0 or fps <= 0 or begin < ticks[0] or begin + seconds / unit > ticks[-1]:
        raise ValueError('The video stream does not cover the requested clip window')
    count = math.ceil(seconds * fps)
    indices = [bisect_right(ticks, begin + Fraction(n, fps) / unit) - 1 for n in range(count)]
    first, stop = indices[0], indices[-1] + 1
    width, height = info['raster']
    size = (max(1, round(width * info['aspect'])), height)
    # CSS frames the full, upright square-pixel image separately in each scene.
    framing = f'scale={size[0]}:{size[1]}:flags=lanczos,setsar=1'
    colour, interpretation = browser_frame_filter(info['stream'], framing, size,
                                                 raster_size=info['raster'], overrides=overrides)
    filters = f'trim=start_frame={first}:end_frame={stop},{colour}'
    cmd = ['ffmpeg', '-nostdin', '-v', 'error', '-i', str(path), '-map', f"0:{info['video']}",
           '-vf', filters, '-fps_mode', 'passthrough', '-frames:v', str(stop - first),
           '-pix_fmt', 'rgb24', '-f', 'rawvideo', 'pipe:1']
    target.mkdir()
    position = 0
    frame_bytes = size[0] * size[1] * 3
    with subprocess.Popen(cmd, stdout=subprocess.PIPE) as decoder:
        try:
            for native in range(first, stop):
                raw = decoder.stdout.read(frame_bytes)
                if len(raw) != frame_bytes:
                    raise ValueError(f'Missing decoded source frame {native}')
                if indices[position] != native:
                    continue
                frame = target / f'{position+1:04}.jpg'
                Image.frombytes('RGB', size, raw).save(frame, 'JPEG', quality=94, subsampling=0)
                position += 1
                while position < count and indices[position] == native:
                    os.link(frame, target / f'{position+1:04}.jpg')
                    position += 1
        finally:
            decoder.stdout.close()
        if decoder.wait() or position != count:
            raise ValueError('The source could not be conformed to the scene clock')
    return dict(engine=engine, frames=count, size=size, input_color=interpretation, output_color='sRGB',
                time_base=str(unit), container_start=str(origin), source_frames=indices,
                source_pts=[ticks[i] for i in indices])


if __name__ == '__main__':
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('source', type=Path)
    cli.add_argument('output', type=Path)
    cli.add_argument('start', type=Fraction)
    cli.add_argument('seconds', type=Fraction)
    cli.add_argument('fps', type=int)
    cli.add_argument('input_color', type=json.loads)
    args = cli.parse_args()
    try:
        print(json.dumps(extract(args.source, args.output, args.start, args.seconds, args.fps, args.input_color)))
    except (KeyError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        cli.exit(1, str(exc) + '\n')
