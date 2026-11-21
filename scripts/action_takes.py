"""Compare original action takes at a common contact frame and intended delivery crop."""
import argparse
import base64
from fractions import Fraction
from functools import lru_cache
from html import escape
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile

from picture_roughcut import command, probe
from source_framing import crop_box, crop_filter, upright_raster


def integers(row, count):
    return isinstance(row, list) and len(row) == count and all(type(v) is int for v in row)


@lru_cache(maxsize=16)
def source(path):
    streams = json.loads(command('ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(path)))['streams']
    video = next((s for s in streams if s['codec_type'] == 'video' and not s['disposition']['attached_pic']), None)
    if not video:
        raise ValueError(f'No moving picture stream in {path}')
    frames = json.loads(command('ffprobe', '-v', 'error', '-select_streams', str(video['index']), '-show_frames',
                                '-show_entries', 'frame=best_effort_timestamp,duration,pkt_duration',
                                '-of', 'json', str(path)))['frames']
    ticks = [int(f['best_effort_timestamp']) for f in frames]
    if not ticks or any(b < a for a, b in zip(ticks, ticks[1:])):
        raise ValueError(f'Cannot read the display order in {path}')
    # Keep the native integer clock. A nominal rate can pick the adjacent frame in a VFR recording.
    last = int(video.get('start_pts', ticks[0])) + int(video.get('duration_ts', 0))
    if last <= ticks[-1]:
        last = ticks[-1] + int(frames[-1].get('duration') or frames[-1].get('pkt_duration') or 0)
    # A missing final duration still leaves all windows ending on an earlier frame available.
    ticks.append(last)
    width, height, aspect = upright_raster(video)
    return dict(ticks=ticks, tb=Fraction(video['time_base']), video=video['index'], stream=video, raster=(width, height),
                aspect=aspect, audio=next((s['index'] for s in streams if s['codec_type']=='audio'), None))


def plan_comparison(plan, folder):
    target = plan.get('target', [1920,1080])
    fps, phone = plan.get('fps', 30), plan.get('phone_width', 390)
    window = plan.get('window', [10,24])
    if not integers(target, 2) or min(target) <= 0 or type(phone) is not int or phone < 2:
        raise ValueError('Use positive integer target dimensions and a phone_width of at least 2')
    if type(fps) is not int or fps <= 0 or not integers(window, 2) or window[0] < 0 or window[1] <= 0:
        raise ValueError('Use positive integer fps and window [frames before contact, frames from contact]')
    items = plan.get('takes')
    if not isinstance(items, list) or len(items) < 2:
        raise ValueError('Supply at least two takes showing the same action')
    takes, names = [], set()
    before, after = window
    for item in items:
        name = item['id']
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ValueError('Each take needs a unique nonempty id')
        names.add(name)
        path = (folder / item['file']).resolve(strict=True)
        info = source(path)
        ticks, tb = info['ticks'], info['tb']
        bounds = item.get('usable_frames', [0,len(ticks)-1])
        contact = item['contact_frame']
        if not integers(bounds, 2) or type(contact) is not int or not 0 <= bounds[0] <= contact < bounds[1] < len(ticks):
            raise ValueError(f'{name}: usable_frames and contact_frame must lie within the decoded frame range')
        first, anchor, end = (ticks[n] * tb for n in (bounds[0],contact,bounds[1]))
        if ticks[contact+1] <= ticks[contact] or end <= anchor:
            raise ValueError(f'{name}: choose a contact frame with a known positive display interval')
        before = min(before, math.floor((anchor-first)*fps))
        after = min(after, math.floor((end-anchor)*fps))
        crop = crop_box(item.get('crop'), *info['raster'], info['aspect'], target)
        takes.append(dict(id=name, path=path, info=info, bounds=bounds, first=first, anchor=anchor, end=end,
                          contact=contact, crop=crop, eye_trace=item.get('eye_trace')))
    pane = (phone, max(1, round(phone*target[1]/target[0])))
    return takes, target, fps, pane, before, after


def summary(takes, target, fps, pane, before, after):
    rows = []
    for row in takes:
        width, height = row['crop'][2:]
        rows.append(dict(id=row['id'], file=str(row['path']), contact_frame=row['contact'],
                         contact_pts_seconds=float(row['anchor']), usable_frames=row['bounds'],
                         available_before_seconds=float(row['anchor']-row['first']),
                         available_after_seconds=float(row['end']-row['anchor']), crop=row['crop'],
                         max_axis_scale_to_target=round(max(target[0]/width, target[1]/height),3),
                         has_audio_stream=row['info']['audio'] is not None, eye_trace=row['eye_trace']))
    return dict(target=target, pane=pane, fps=fps, contact_preview_frame=before,
                total_preview_frames=before+after, takes=rows)


def comparison_stream(takes, target, fps, pane, before, after, listen):
    if not after:
        raise ValueError('Less than one common output frame from contact. Choose a higher fps or a longer usable window.')
    inputs, filters, labels = [], [], []
    audio = next((i for i,take in enumerate(takes) if take['id'] == listen), None)
    if listen is not None and (audio is None or takes[audio]['info']['audio'] is None):
        raise ValueError(f'Choose a candidate with a source audio stream: {listen!r}')
    for i, take in enumerate(takes):
        inputs.extend(['-i', str(take['path'])])
        info = take['info']
        cut = take['anchor'] - Fraction(before, fps)
        clock = math.lcm(info['tb'].denominator, fps)
        framing = crop_filter(take['crop'], info['stream'], target, output_size=pane)
        # A change between output ticks belongs on the next tick. Contact itself is exact in this clock.
        filters.append(f"[{i}:{info['video']}]settb=1/{clock},setpts=PTS-({int(cut*clock)}),"
                       f"fps={fps}:round=up:start_time=0,trim=end_frame={before+after},"
                       f"{framing},format=yuv444p[v{i}]")
        labels.append(f'[v{i}]')
        if i == audio:
            rate = 48000
            filters.append(f"[{i}:{info['audio']}]aresample={rate},asettb=1/{rate},"
                           f"asetpts=PTS-({round(cut*rate)}),aresample={rate}:async=1:first_pts=0,"
                           f"apad,atrim=end_sample={round(Fraction((before+after)*rate, fps))}[sound]")
    filters.append(''.join(labels) + f'hstack=inputs={len(takes)}:shortest=1,pad=ceil(iw/2)*2:ceil(ih/2)*2[compare]')
    args = ['ffmpeg','-nostdin','-hide_banner','-v','error','-copyts',*inputs,
            '-filter_complex_threads','2','-filter_complex',';'.join(filters),'-map','[compare]']
    args += ['-an'] if audio is None else ['-map','[sound]','-c:a','aac','-b:a','192k']
    # MOV with an ordinary seek table also preserves AAC priming and works offline in the system browser.
    return args + ['-c:v','libx264','-pix_fmt','yuv420p','-preset','veryfast','-crf','14']


def save_view(target_path, takes, pane, before, after, fps, listen, encode):
    if target_path.suffix.lower() != '.html' or target_path.resolve() in {t['path'] for t in takes}:
        raise ValueError('Choose a separate HTML path for the source review')
    target_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target_path.parent) as tmp:
        proxy = Path(tmp)/'compare.mp4'
        subprocess.run([*encode,'-movflags','+faststart',str(proxy)],check=True)
        if int(probe(proxy,'nb_frames')['nb_frames']) != before+after:
            raise ValueError('The sources did not supply the common display window')
        title = '　|　'.join(escape(t['id']) for t in takes)
        sound = '静音' if listen is None else '独听原声　'+escape(listen)
        payload = base64.b64encode(proxy.read_bytes()).decode('ascii')
        width = 2 * math.ceil(pane[0]*len(takes)/2)
        page = Path(tmp)/'view.html'
        page.write_text(f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>动作选材</title>
<style>body{{font:16px/1.55 system-ui,sans-serif;margin:1.25rem;color:#ecebe7;background:#171b19}}
header{{margin-bottom:1em}} video{{display:block}} p{{margin:.3em 0;color:#c9cfc9}}</style>
<header><strong>{title}</strong><p>{sound} · 接触帧 F{before} · {before/fps:.3f} 秒 · 单格 {pane[0]} 像素宽</p>
<button id="contact">定位接触帧</button></header>
<video controls playsinline width="{width}" src="data:video/mp4;base64,{payload}"></video>
<script>const video=document.querySelector('video');document.querySelector('#contact').onclick=()=>{{
video.pause();video.currentTime={(before+0.5)/fps:.9f};}};</script></html>""")
        os.replace(page,target_path)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument('plan', type=Path)
    cli.add_argument('--view', type=Path, help='Write an offline HTML comparison with synchronized pictures at phone width')
    cli.add_argument('--listen', metavar='ID', help='Include only this take’s first source audio stream in the view')
    args = cli.parse_args()
    try:
        plan = json.loads(args.plan.read_text())
        takes, target, fps, pane, before, after = plan_comparison(plan, args.plan.parent)
        print(json.dumps(summary(takes, target, fps, pane, before, after),ensure_ascii=False,indent=2),flush=True)
        if args.listen is not None and not args.view:
            cli.error('--listen selects the audio of a --view HTML comparison')
        if args.view:
            if args.view.resolve() == args.plan.resolve():
                raise ValueError('Choose a separate path for the comparison')
            encode = comparison_stream(takes, target, fps, pane, before, after, args.listen)
            save_view(args.view, takes, pane, before, after, fps, args.listen, encode)
    except (KeyError, TypeError, ValueError, OSError, subprocess.CalledProcessError) as exc:
        cli.exit(1,f'{exc}\n')


if __name__ == '__main__':
    main()
