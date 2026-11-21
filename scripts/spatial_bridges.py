"""Frame-clock FFmpeg projections for editorial desk, push and evidence-window moves."""
import math
import subprocess
from pathlib import Path

from color_finish import graded_source_filter, icc_still
from source_framing import upright_raster


WINDOW_STILLS = {'.png','.jpg','.jpeg','.webp','.bmp','.tif','.tiff'}


def easing(clock):
    """Smoothstep for a normalized clock; the expression runs once per corner per frame."""
    k = f'clip({clock},0,1)'
    return f'({k}*{k}*(3-2*{k}))'


def interpolate(a, b, ease):
    return f'{a:.12g}+({b-a:.12g})*{ease}' if a != b else f'{a:.12g}'


FULL = ((0, 0), (1, 0), (0, 1), (1, 1))


def projection(first, last, ease, *, transparent):
    coords = []
    for i, ((ax, ay), (bx, by)) in enumerate(zip(first, last)):
        coords.extend([f"x{i}='W*({interpolate(ax,bx,ease)})'",
                       f"y{i}='H*({interpolate(ay,by,ease)})'"])
    warp = 'perspective=sense=destination:eval=frame:interpolation=cubic:'+':'.join(coords)
    if not transparent:
        return warp
    # Perspective extends its edge texels. A transparent collar lets layers reveal what is behind.
    return 'format=yuva444p,pad=iw+4:ih+4:2:2:color=black@0,'+warp+',crop=iw-4:ih-4:2:2'


def bridge_graph(old, new, tag, edge, start, fps):
    """Replace exactly one overlap with a folded page and rising chart, or an anchored push."""
    count = edge.left + edge.right
    end = start + count
    # The perspective filter's output counter begins at 1, while trim frame indices begin at 0.
    ease = easing(f'(on-1)/{count-1}')
    graph = [
        f'[{old}]split[{tag}h0][{tag}a0]',
        f'[{tag}h0]trim=end_frame={start},setpts=PTS-STARTPTS[{tag}head]',
        f'[{tag}a0]trim=start_frame={start}:end_frame={end},setpts=PTS-STARTPTS[{tag}a]',
        f'[{new}]split[{tag}b0][{tag}t0]',
        f'[{tag}b0]trim=end_frame={count},setpts=PTS-STARTPTS[{tag}b]',
        f'[{tag}t0]trim=start_frame={count},setpts=PTS-STARTPTS[{tag}tail]',
    ]
    if edge.name == 'desk-rise':
        table = ((.12,.51),(.88,.51),(-.09,1.025),(1.09,1.025))
        waiting = ((.19,1.04),(.81,1.04),(.06,1.58),(.94,1.58))
        delay = min(count-2, round((count-1)*.16))
        rise = easing(f'(on-1-{delay})/{count-1-delay}')
        graph.extend([
            f'[{tag}a]split[{tag}under][{tag}sheet]',
            f'[{tag}under]gblur=sigma=9,eq=brightness=-0.20:contrast=0.70:saturation=0.60[{tag}room]',
            f'[{tag}sheet]fade=t=out:s=0:n={2*(count-1)},'
            + projection(FULL, table, ease, transparent=True)+f'[{tag}table]',
            f'[{tag}room][{tag}table]overlay=format=yuv444[{tag}base]',
            f'[{tag}b]'+projection(waiting, FULL, rise, transparent=True)+f'[{tag}card]',
            f'[{tag}base][{tag}card]overlay=format=yuv444[{tag}pass]',
        ])
    else:
        x, y = edge.anchor
        expanded = tuple((x+(a-x)*edge.zoom,y+(b-y)*edge.zoom) for a,b in FULL)
        graph.extend([
            f'[{tag}a]'+projection(FULL, expanded, ease, transparent=False)+f',split[{tag}sharp][{tag}blur0]',
            f'[{tag}blur0]gblur=sigma=4,format=yuva444p,fade=t=in:s=0:n={count-1}:alpha=1[{tag}soft]',
            f'[{tag}sharp][{tag}soft]overlay=format=yuv444,format=yuva444p,'
            f'fade=t=out:s=0:n={count-1}:alpha=1[{tag}out]',
            f'[{tag}b][{tag}out]overlay=format=yuv444[{tag}pass]',
        ])
    graph.append(f'[{tag}head][{tag}pass][{tag}tail]concat=n=3:v=1:a=0,settb=1/{fps},setpts=N[{tag}]')
    return graph


def rectangle(value, name):
    if (not isinstance(value, list) or len(value) != 4 or
            any(type(v) not in (float,int) or not math.isfinite(v) for v in value)):
        raise ValueError(f'{name}: use normalized [left,top,width,height]')
    x,y,w,h = value
    if min(x,y) < 0 or min(w,h) <= 0 or max(x+w,y+h) > 1+1e-10:
        raise ValueError(f'{name}: rectangle lies outside the programme picture')
    return ((x,y), (x+w,y), (x,y+h), (x+w,y+h))


def window_plan(shot, frames):
    """All frame offsets are local to the unpadded shot, inclusive at the four marked poses."""
    if 'evidence_window' not in shot:
        return None
    spec = shot['evidence_window']
    if not isinstance(spec, dict):
        raise ValueError('evidence_window needs a file, two rectangles and frame ranges')
    source = spec.get('file')
    if not isinstance(source,str) or not source or Path(source).is_absolute() or '..' in Path(source).parts:
        raise ValueError('Window artwork must be a project-relative picture or video')
    times = [spec.get('open'), spec.get('close')]
    if any(not isinstance(t,list) or len(t)!=2 or any(type(n) is not int for n in t) for t in times):
        raise ValueError('Use open/close frame pairs for the evidence window')
    a,b = times[0]
    c,d = times[1]
    if not 0 <= a < b <= c < d < frames:
        raise ValueError(f'{shot["id"]}: open and close the evidence window inside 0..{frames-1}')
    source_in = spec.get('in',0)
    if type(source_in) not in (float,int) or not math.isfinite(source_in) or source_in < 0:
        raise ValueError('Window source in must be a nonnegative time in seconds')
    dim = spec.get('dim',.65)
    if type(dim) not in (float,int) or not 0 <= dim <= 1:
        raise ValueError('Use a background brightness from 0 to 1')
    rectangle(spec.get('from'), 'from')
    rectangle(spec.get('to'), 'to')
    return dict(file=source, start=a, open_end=b, close_start=c, end=d, source_in=source_in,
                origin=spec['from'], destination=spec['to'], dim=dim)


def window_input(plan, project_path, folder, index, size, fps, probe):
    """Decode one verified source on the project clock, independently of the running plate."""
    path = project_path(plan['file'])
    still = Path(path).suffix.lower() in WINDOW_STILLS
    if still and plan['source_in']:
        raise ValueError('A still evidence source begins at zero')
    path, profile = icc_still(path, folder/f'window-color-{index}.png') if still else (path,None)
    media = probe(path)
    video = next((s for s in media['streams'] if s['codec_type']=='video' and not s.get('disposition',{}).get('attached_pic')),None)
    if video is None:
        raise ValueError(f'Evidence source has no picture: {path}')
    length = plan['end']-plan['start']+1
    args = ['-loop','1','-framerate',fps] if still else ['-ss',plan['source_in']]
    if not still:
        # Container duration may include a longer audio track. Check the actual selected picture
        # after the same seek and fps conversion or framesync could silently drop the window early.
        check = subprocess.run(['ffmpeg','-nostdin','-v','error','-progress','pipe:1',*map(str,args),
                                '-i',str(path),'-map',f"0:{video['index']}",'-an','-vf',f'fps={fps}',
                                '-frames:v',str(length),'-f','null','-'],capture_output=True,text=True,check=True)
        counts = [int(line[6:]) for line in check.stdout.splitlines() if line.startswith('frame=')]
        if not counts or counts[-1] != length:
            raise ValueError(f'Evidence source needs {length} usable picture frames from its in point: {path}')
    sw,sh,sar = upright_raster(video)
    color, report = graded_source_filter(video,raster_size=(sw,sh))
    if profile:
        report['icc_to_srgb'] = profile
    # Letterbox to the final display rectangle. A square node may start more compressed;
    # only the settled rectangle is for reading. Keep an entire original document visible.
    w,h = size
    _,_,dw,dh = plan['destination']
    cw,ch = max(2,round(dw*w/2)*2), max(2,round(dh*h/2)*2)
    fit = min(cw/(sw*sar),ch/sh)
    pw,ph = max(1,round(sw*sar*fit)),max(1,round(sh*fit))
    framing = (f'fps={fps},{color},format=yuv444p,scale={pw}:{ph},'
               f'pad={cw}:{ch}:(ow-iw)/2:(oh-ih)/2,setsar=1,scale={w}:{h},setsar=1,'
               f'trim=end_frame={length},settb=1/{fps},setpts=N')
    return [*args,'-i',str(Path(path).resolve())], video['index'], framing, report


def window_graph(base, source, label, plan, padding, fps):
    """Replace the middle of this plate with a moving inset; the scene clock never pauses."""
    a,b,c,d = (plan[k] for k in ('start','open_end','close_start','end'))
    entering = easing(f'(on-1)/{b-a}')
    leaving = easing(f'({d-a}-(on-1))/{d-c}')
    path = f'({entering}*{leaving})'
    origin, dest = rectangle(plan['origin'],'from'), rectangle(plan['destination'],'to')
    length = d-a+1
    start = padding+a
    # Keep the full-length plate as main input to framesync. The insert and dim layer
    # cover precisely the scheduled window and use transparent endpoints.
    darken = 1-plan['dim']
    fade = f'fade=t=in:s=0:n={b-a}:alpha=1,fade=t=out:s={c-a}:n={d-c}:alpha=1'
    return [
        f'[{base}]split[{label}src][{label}shade]',
        f'[{label}shade]trim=start_frame={start}:end_frame={start+length},setpts=PTS-STARTPTS,'
        f'format=yuva444p,lutyuv=y=16:u=128:v=128:a={round(255*darken)},{fade},setpts=PTS+{start}[{label}dim]',
        f'[{label}src][{label}dim]overlay=format=yuv444:eof_action=pass:repeatlast=0[{label}bed]',
        f'[{source}]'+projection(origin,dest,path,transparent=True)+f',{fade},settb=1/{fps},setpts=N+{start}[{label}insert]',
        f'[{label}bed][{label}insert]overlay=format=yuv444:eof_action=pass:repeatlast=0[{label}]',
    ]
