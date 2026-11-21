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
        marks = {'contact': contact}
        for phase in ('onset', 'result'):
            field = phase + '_frame'
            if field in item:
                frame = item[field]
                if type(frame) is not int or not 0 <= frame < len(ticks)-1 or ticks[frame+1] <= ticks[frame]:
                    raise ValueError(f'{name}: {field} must name a source frame with a positive display interval')
                marks[phase] = frame
        if not marks.get('onset', contact) <= contact <= marks.get('result', contact):
            raise ValueError(f'{name}: expected onset_frame <= contact_frame <= result_frame')
        first, anchor, end = (ticks[n] * tb for n in (bounds[0],contact,bounds[1]))
        if ticks[contact+1] <= ticks[contact] or end <= anchor:
            raise ValueError(f'{name}: choose a contact frame with a known positive display interval')
        before = min(before, math.floor((anchor-first)*fps))
        after = min(after, math.floor((end-anchor)*fps))
        crop = crop_box(item.get('crop'), *info['raster'], info['aspect'], target)
        takes.append(dict(id=name, path=path, info=info, bounds=bounds, first=first, anchor=anchor, end=end,
                          contact=contact, marks=marks, crop=crop, target=target, eye_trace=item.get('eye_trace')))
    pane = (phone, max(1, round(phone*target[1]/target[0])))
    return takes, target, fps, pane, before, after


def action_marks(take, fps, before, after):
    """Locate an exact source frame on the same round-up output clock used by comparison_stream."""
    ticks, unit = take['info']['ticks'], take['info']['tb']
    start = take['anchor'] - Fraction(before, fps)
    stop = start + Fraction(before + after, fps)
    result = {}
    for phase in ('onset', 'contact', 'result'):
        if phase not in take['marks']:
            continue
        frame = take['marks'][phase]
        lo, hi = ticks[frame]*unit, ticks[frame+1]*unit
        first = max(0, math.ceil((lo - start)*fps))
        visible = first < min(before + after, math.ceil((hi - start)*fps))
        if not take['bounds'][0] <= frame < take['bounds'][1]:
            status = 'outside_usable'
        elif hi <= start or lo >= stop:
            status = 'outside_window'
        else:
            status = 'visible' if visible else 'between_ticks'
        result[phase] = dict(source_frame=frame, source_pts_seconds=float(lo),
                             from_contact_seconds=float(lo - take['anchor']),
                             preview_frame=first if visible else None, status=status)
    return result


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
        if len(row['marks']) > 1:
            rows[-1]['action_marks'] = action_marks(row, fps, before, after)
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


def mark_controls(takes, pane, before, after, fps, folder):
    """Link sampled marks into the comparison and show exact source stills for marks it omits."""
    labels = {'onset':'起势', 'contact':'接触', 'result':'结果首次可辨'}
    reasons = {'outside_usable':'在此机位可用段外', 'outside_window':'在并排窗口外',
               'between_ticks':'位于两格预览帧之间'}
    groups = []
    for i, take in enumerate(takes):
        if len(take['marks']) == 1:
            continue
        marks = action_marks(take, fps, before, after)
        missing = sorted({m['source_frame'] for m in marks.values() if m['preview_frame'] is None})
        pictures = {}
        if missing:
            # A short VFR source frame can fall entirely between two proxy ticks.
            # Extract by native display index so that the exact authored frame remains inspectable.
            select = '+'.join(f'eq(n,{n})' for n in missing)
            framing = crop_filter(take['crop'], take['info']['stream'], take['target'], output_size=pane)
            stem = folder/f'take-{i}-%d.png'
            subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(take['path']),
                            '-map', f"0:{take['info']['video']}", '-vf', f"select='{select}',{framing}",
                            '-fps_mode', 'passthrough', '-frames:v', str(len(missing)), str(stem)], check=True)
            for index, source_frame in enumerate(missing, 1):
                data = (folder/f'take-{i}-{index}.png').read_bytes()
                pictures[source_frame] = base64.b64encode(data).decode('ascii')
        items = []
        for phase, mark in marks.items():
            source_frame, preview = mark['source_frame'], mark['preview_frame']
            name = f'{labels[phase]} · 源 F{source_frame}'
            if preview is not None:
                items.append(f'<button data-frame="{preview}">{name} · 对照 F{preview}</button>')
            else:
                items.append(f'<details><summary>{name} · {reasons[mark["status"]]} · 查看原帧</summary>'
                             f'<img width="{pane[0]}" alt="{name}" src="data:image/png;base64,{pictures[source_frame]}"></details>')
        groups.append('<section><strong>' + escape(take['id']) + '</strong> ' + ' '.join(items) + '</section>')
    return '\n'.join(groups)


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
        controls = mark_controls(takes, pane, before, after, fps, Path(tmp))
        page = Path(tmp)/'view.html'
        page.write_text(f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>动作选材</title>
<style>body{{font:16px/1.55 system-ui,sans-serif;margin:1.25rem;color:#ecebe7;background:#171b19}}
header{{margin-bottom:1em}} video{{display:block}} p{{margin:.3em 0;color:#c9cfc9}}</style>
<header><strong>{title}</strong><p>{sound} · 接触帧 F{before} · {before/fps:.3f} 秒 · 单格 {pane[0]} 像素宽</p>
<button data-step="-1">上一帧</button> <button id="contact" data-frame="{before}">定位接触帧</button>
<button data-step="1">下一帧</button> <output id="position"></output></header>
<video controls playsinline width="{width}" src="data:video/mp4;base64,{payload}"></video>
{controls}
<script>const video=document.querySelector('video'),fps={fps},last={before+after-1};
function currentFrame(){{return Math.min(last,Math.floor(video.currentTime*fps));}}
function showFrame(frame){{video.pause();video.currentTime=(Math.max(0,Math.min(last,frame))+0.5)/fps;}}
for(const button of document.querySelectorAll('[data-frame]'))button.onclick=()=>showFrame(Number(button.dataset.frame));
for(const button of document.querySelectorAll('[data-step]'))button.onclick=()=>showFrame(currentFrame()+Number(button.dataset.step));
video.ontimeupdate=()=>{{document.querySelector('#position').textContent='对照 F'+currentFrame();}};</script></html>""")
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
