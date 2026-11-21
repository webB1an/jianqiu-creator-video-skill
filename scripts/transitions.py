"""Apply incoming picture bridges while keeping every original speech and shot time."""
from dataclasses import dataclass
import json
import math
from color_finish import FLAGS
import spatial_bridges
from pathlib import Path

PRESETS = json.loads((Path(__file__).resolve().parents[1]/'assets/motion/transitions.json').read_text())


STILLS = {'.jpg', '.jpeg', '.png', '.webp'}


@dataclass(frozen=True)
class Bridge:
    name: str
    left: int
    right: int
    handles: str
    anchor: tuple | None = None
    zoom: float | None = None


def plan_bridges(shots, times, fps):
    """Incoming edges are indexed like shots. A bridge straddles the nominal cut."""
    edges = []
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
        handles = opt.get('handles', 'hold')
        if handles not in ('source', 'hold'):
            raise ValueError(f'Use source or hold for transition handles at {shot["id"]}')
        seconds = float(opt.get('seconds', PRESETS[name]['seconds']))
        if not math.isfinite(seconds):
            raise ValueError(f'Invalid transition duration at {shot["id"]}')
        count = round(seconds * fps)
        left, right = count // 2, count - count // 2
        if left < 1 or left >= times[i-1]['frames'] or right >= times[i]['frames']:
            raise ValueError(f'Transition does not fit near {shot["id"]}')
        if edges[i-1] and edges[i-1].right + left > times[i-1]['frames']:
            raise ValueError(f'Overlapping transition windows in {shots[i-1]["id"]}')
        anchor, zoom = None, None
        if name == 'push-through':
            anchor, zoom = opt.get('at',[.5,.5]), opt.get('zoom',2.0)
            if (not isinstance(anchor,list) or len(anchor)!=2 or
                    not all(type(v) in (float,int) and 0<=v<=1 for v in anchor)):
                raise ValueError('push-through.at must locate a point inside the output frame')
            if type(zoom) not in (float,int) or not math.isfinite(zoom) or zoom <= 1:
                raise ValueError('push-through.zoom must be greater than 1')
            anchor = tuple(anchor)
        edges.append(Bridge(name, left, right, handles, anchor, zoom))
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


def stitch(folder, shots, times, fps, ff, probe, *, edges=None, source_path=Path, windows_receipt=None):
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
            ff(*inputs,'-filter_complex_threads',2,'-filter_complex',';'.join(graph),'-map',f'[{label}]','-frames:v',accumulated,'-r',fps,'-an','-pix_fmt','yuv420p','-c:v','libx264','-preset','veryfast','-crf',16,*FLAGS,output,cwd=folder)
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
            bridges[-1].update(at=list(edge.anchor),zoom=edge.zoom)
    return bridges
