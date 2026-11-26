"""Apply incoming picture bridges while keeping every original speech and shot time."""
from dataclasses import dataclass
import json
import math
import subprocess
from fractions import Fraction
from color_finish import FLAGS
import spatial_bridges
from pathlib import Path

PRESETS = json.loads((Path(__file__).resolve().parents[1]/'assets/motion/transitions.json').read_text())


STILLS = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff'}


@dataclass(frozen=True)
class Bridge:
    name: str
    left: int
    right: int
    handles: str
    anchor: tuple | None = None
    zoom: float | None = None
    settle: float | None = None


def shot_speed(shot):
    speed = shot.get('speed', 1.0)
    if type(speed) not in (int, float) or not math.isfinite(speed) or not 0.25 <= speed <= 8.0:
        raise ValueError(f"{shot['id']}: shot speed must be between 0.25 and 8.0")
    return float(speed)


def source_room(shot, frames, fps, source_path, durations):
    """Available real source frames on either side of the scheduled picture."""
    if 'parallax_25d' in shot:
        return 0, 0
    path = Path(shot['visual'])
    if path.suffix.lower() in STILLS:
        return math.inf, math.inf
    start = Fraction(str(shot.get('in', 0))) * fps
    if start < 0:
        raise ValueError(f"Negative source in-point at {shot['id']}")
    stream = 'V:0' if shot.get('crop') is not None else 'v:0'
    key = path, stream
    if key not in durations:
        source = str(source_path(path))
        entries = 'stream=start_pts,duration_ts,time_base,start_time,duration:format=start_time'
        probe = subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', stream,
                                         '-show_entries', entries, '-of', 'json', source], text=True)
        info = json.loads(probe)
        if not info.get('streams'):
            raise ValueError(f'No picture stream in {path}')
        track = info['streams'][0]
        origin = Fraction(info.get('format', {}).get('start_time', '0'))
        first = Fraction(track.get('start_time', '0'))
        if 'start_pts' in track and 'time_base' in track:
            first = int(track['start_pts']) * Fraction(track['time_base'])
        if 'duration_ts' in track and 'time_base' in track:
            end = first + int(track['duration_ts']) * Fraction(track['time_base'])
        elif track.get('duration') not in (None, 'N/A'):
            end = first + Fraction(track['duration'])
        else:
            # A container can continue playing audio after its picture ends. Use picture
            # packet timestamps in containers that omit the video stream duration.
            packets = subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', stream, '-show_packets',
                                               '-show_entries', 'packet=pts,duration', '-of', 'compact=p=0:nk=0',
                                               source], text=True)
            tick = Fraction(track['time_base'])
            end = first
            for line in packets.splitlines():
                values = dict(piece.split('=', 1) for piece in line.split('|') if '=' in piece)
                if values.get('pts') not in (None, 'N/A'):
                    length = values.get('duration')
                    last = int(values['pts']) + (int(length) if length not in (None, 'N/A') else 0)
                    end = max(end, last * tick)
        durations[key] = (first-origin) * fps, (end-origin) * fps
    # Round only numerical noise from a rolling edit expressed as source seconds.
    epsilon = Fraction(1, 10**7)
    first, end = durations[key]
    speed = Fraction(str(shot_speed(shot)))
    before = max(0, math.floor(min(start, start - first) / speed + epsilon))
    after = max(0, math.floor((end - start) / speed - frames + epsilon))
    if 'source_handle_frames' in shot:
        limit = shot['source_handle_frames']
        if not isinstance(limit, list) or len(limit) != 2 or any(type(v) is not int or v < 0 for v in limit):
            raise ValueError(f"{shot['id']}: source_handle_frames must be [usable before, usable after] in project frames")
        before, after = min(before, limit[0]), min(after, limit[1])
    return before, after


def plan_bridges(shots, times, fps, *, default_handles='hold', source_path=Path, decisions=None):
    """Incoming edges are indexed like shots. A bridge straddles the nominal cut."""
    if default_handles not in ('auto', 'source', 'hold'):
        raise ValueError('transition_handles must be auto, source or hold')
    edges, durations = [], {}
    for i, shot in enumerate(shots):
        opt = shot.get('transition', 'cut')
        if isinstance(opt, str):
            opt = {'name': opt}
        if not isinstance(opt, dict):
            raise ValueError(f'Invalid transition declaration at {shot["id"]}')
        name = opt.get('name')
        if name == 'cut':
            edges.append(None)
            continue
        if name not in PRESETS or i == 0:
            raise ValueError(f'Invalid incoming transition at {shot["id"]}: {name}')
        handles = opt.get('handles', default_handles)
        if handles not in ('auto', 'source', 'hold'):
            raise ValueError(f'Use auto, source or hold for transition handles at {shot["id"]}')
        seconds = float(opt.get('seconds', PRESETS[name]['seconds']))
        if not math.isfinite(seconds) or seconds <= 0:
            raise ValueError(f'Invalid transition duration at {shot["id"]}')
        count = round(seconds * fps)
        anchor, zoom, settle = None, None, None
        if name in ('push-through', 'pull-back', 'match-cut'):
            default_zoom = 1.45 if name == 'match-cut' else 2.0
            anchor, zoom = opt.get('at', [.5, .5]), opt.get('zoom', default_zoom)
            if (not isinstance(anchor, list) or len(anchor) != 2 or
                    not all(type(v) in (float, int) and 0 <= v <= 1 for v in anchor)):
                raise ValueError(f'{name}.at must locate a point inside the output frame')
            if type(zoom) not in (float, int) or not math.isfinite(zoom) or zoom <= 1:
                raise ValueError(f'{name}.zoom must be greater than 1')
            if 'settle' in opt:
                settle = opt['settle']
                if type(settle) not in (float, int) or not math.isfinite(settle) or not 1.0 <= settle <= 2.0:
                    raise ValueError(f'{name}.settle must be between 1.0 and 2.0')
                settle = float(settle)
            anchor = tuple(anchor)
        elif name == 'iris-open':
            anchor = opt.get('at', [.5, .5])
            if (not isinstance(anchor, list) or len(anchor) != 2 or
                    not all(type(v) in (float, int) and 0 <= v <= 1 for v in anchor)):
                raise ValueError('iris-open.at must locate a point inside the output frame')
            anchor = tuple(anchor)
        if handles == 'auto':
            incoming, _ = source_room(shot, times[i]['frames'], fps, source_path, durations)
            _, outgoing = source_room(shots[i-1], times[i-1]['frames'], fps, source_path, durations)
            occupied = edges[i-1].right if edges[i-1] else 0
            left_room = min(incoming, times[i-1]['frames'] - max(1, occupied))
            right_room = min(outgoing, times[i]['frames'] - 1)
            available = min(count, 2 * left_room + 1, 2 * right_room)
            if (available < count or available < 2) and decisions is not None:
                decisions.append(dict(shot=shot['id'], preset=name, requested_frames=count,
                                      actual_frames=available if available >= 2 else 0,
                                      result='shortened' if available >= 2 else 'cut'))
            count = available
            if count < 2:
                edges.append(None)
                continue
            handles = 'source'
        left, right = count // 2, count - count // 2
        if left < 1 or left >= times[i-1]['frames'] or right >= times[i]['frames']:
            raise ValueError(f'Transition does not fit near {shot["id"]}')
        if edges[i-1] and edges[i-1].right + left > times[i-1]['frames']:
            raise ValueError(f'Overlapping transition windows in {shots[i-1]["id"]}')
        edges.append(Bridge(name, left, right, handles, anchor, zoom, settle))
    return edges


def source_handles(shots, edges, i):
    """Number of real frames needed before and after this shot's scheduled source interval."""
    incoming = edges[i]
    outgoing = edges[i+1] if i+1 < len(edges) else None
    before = incoming.left if incoming and incoming.handles == 'source' else 0
    after = outgoing.right if outgoing and outgoing.handles == 'source' else 0
    if (before or after) and Path(shots[i]['visual']).suffix.lower() in STILLS:
        return 0, 0
    return before, after


def stitch(folder, shots, times, fps, ff, probe, *, edges=None, source_path=Path, windows_receipt=None, proxy=False):
    edges = plan_bridges(shots, times, fps) if edges is None else edges
    windows = [spatial_bridges.window_plan(s,t['frames']) for s,t in zip(shots,times)]
    ends = [i for i in range(1,len(shots)) if edges[i] is None]+[len(shots)]
    start = 0
    listing = []
    for end in ends:
        if end == start+1 and windows[start] is None:
            listing.append(f'shot{start:04}.mp4')
        else:
            inputs,graph = [],[]
            for k in range(start,end):
                inputs.extend(['-threads',2,'-i',f'shot{k:04}.mp4'])
            extra_index = end-start
            for k in range(start,end):
                before, after = source_handles(shots, edges, k)
                lead = edges[k].left - before if k > start else 0
                tail = edges[k+1].right - after if k+1 < end else 0
                out = f'plate{k}' if windows[k] is not None else f'v{k}'
                graph.append(f'[{k-start}:v]tpad=start={lead}:stop={tail}:start_mode=clone:stop_mode=clone,format=yuv444p,settb=1/{fps},setpts=N[{out}]')
                if windows[k] is not None:
                    pin = windows[k]
                    shot_info = probe(folder/f'shot{k:04}.mp4')['streams'][0]
                    size = (shot_info['width'], shot_info['height'])
                    args, stream, framing, color = spatial_bridges.window_input(pin,source_path,folder,k,size,fps,probe)
                    inputs.extend(args)
                    graph.append(f'[{extra_index}:{stream}]{framing}[card{k}]')
                    extra_index += 1
                    graph.extend(spatial_bridges.window_graph(out,f'card{k}',f'v{k}',pin,lead+before,fps))
                    if windows_receipt is not None:
                        windows_receipt.append(dict(shot=shots[k]['id'], **pin, color=color,
                                                    first_frame=times[k]['start_frame']+pin['start'],
                                                    end_frame=times[k]['start_frame']+pin['end']+1))
            accumulated = times[start]['frames']
            label = f'v{start}'
            for k in range(start+1,end):
                edge = edges[k]
                name, left, right = edge.name, edge.left, edge.right
                effect = PRESETS[name]['filter']
                if effect=='custom':
                    axis=PRESETS[name]['axis']
                    # A narrow moving edge avoids ghosted type; P decreases from 1 to 0.
                    blend=f'clip(({axis}-(P*P*(3-2*P)*1.04-0.02))/0.02,0,1)'
                    effect=f"custom:expr='A*(1-{blend})+B*{blend}'"
                if effect=='spatial':
                    graph.extend(spatial_bridges.bridge_graph(label,f'v{k}',f'm{k}',edge,accumulated-left,fps))
                else:
                    graph.append(f'[{label}][v{k}]xfade=transition={effect}:duration={(left+right)/fps:.9f}:offset={(accumulated-left)/fps:.9f}[m{k}]')
                accumulated += times[k]['frames']
                label = f'm{k}'
            output = f'group{start:04}.mp4'
            preset, crf = ('ultrafast', 23) if proxy else ('veryfast', 16)
            ff(*inputs,'-filter_complex_threads',2,'-filter_complex',';'.join(graph),'-map',f'[{label}]','-frames:v',accumulated,'-r',fps,'-an','-pix_fmt','yuv420p','-c:v','libx264','-preset',preset,'-crf',crf,*FLAGS,output,cwd=folder)
            if int(probe(folder/output)['streams'][0]['nb_frames'])!=accumulated:
                raise ValueError('Transition group changed the scheduled frame count')
            listing.append(output)
        start=end
    (folder/'list.txt').write_text(''.join(f"file '{name}'\n" for name in listing))
    ff('-f','concat','-safe','1','-i','list.txt','-c','copy','picture.mp4',cwd=folder)
    bridges = []
    for k, edge in enumerate(edges):
        if edge is None:
            continue
        _, postroll = source_handles(shots, edges, k-1)
        preroll, _ = source_handles(shots, edges, k)
        bridges.append(dict(shot=shots[k]['id'], preset=edge.name,
                            first_frame=times[k]['start_frame']-edge.left,
                            end_frame=times[k]['start_frame']+edge.right,
                            cut_frame=times[k]['start_frame'], handles=edge.handles,
                            outgoing_source_frames=postroll, incoming_source_frames=preroll))
        if edge.anchor is not None:
            bridges[-1]['at'] = list(edge.anchor)
        if edge.zoom is not None:
            bridges[-1]['zoom'] = edge.zoom
        if edge.settle is not None:
            bridges[-1]['settle'] = edge.settle
    return bridges
