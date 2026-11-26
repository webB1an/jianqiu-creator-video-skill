"""Frame-clock FFmpeg projections for editorial desk, push, whip, blur, dip, iris and evidence-window moves."""
import math
import subprocess
from pathlib import Path

from color_finish import graded_source_filter, icc_still
from source_framing import upright_raster


WINDOW_STILLS = {'.png', '.jpg', '.jpeg', '.webp', '.bmp', '.tif', '.tiff'}

# Exact limited-range Rec.709 YUV codes for Jianqiu brand surfaces (#131A17 ink, #F4EDDD paper).
BRAND_YUV = {
    'dip-ink': (28, 129, 126),
    'dip-paper': (220, 122, 131),
}


def easing(clock):
    """Smoothstep for a normalized clock; the expression runs once per corner per frame."""
    k = f'clip({clock},0,1)'
    return f'({k}*{k}*(3-2*{k}))'


def smootherstep(clock):
    """Quintic smootherstep with zero velocity and acceleration at both endpoints."""
    k = f'clip({clock},0,1)'
    return f'({k}*{k}*{k}*({k}*({k}*6-15)+10))'


def interpolate(a, b, ease):
    return f'{a:.12g}+({b - a:.12g})*{ease}' if a != b else f'{a:.12g}'


FULL = ((0, 0), (1, 0), (0, 1), (1, 1))


def projection(first, last, ease, *, transparent):
    coords = []
    for i, ((ax, ay), (bx, by)) in enumerate(zip(first, last)):
        coords.extend([f"x{i}='W*({interpolate(ax, bx, ease)})'",
                       f"y{i}='H*({interpolate(ay, by, ease)})'"])
    warp = 'perspective=sense=destination:eval=frame:interpolation=cubic:' + ':'.join(coords)
    if not transparent:
        return warp
    # Perspective extends its edge texels. A transparent collar lets layers reveal what is behind.
    return 'format=yuva444p,pad=iw+4:ih+4:2:2:color=black@0,' + warp + ',crop=iw-4:ih-4:2:2'


def bridge_graph(old, new, tag, edge, start, fps):
    """Replace exactly one overlap with an eased spatial, optical, whip or editorial-dip transition."""
    count = edge.left + edge.right
    end = start + count
    denom = max(1, count - 1)
    # The perspective filter's output counter begins at 1, while trim/blend frame indices begin at 0.
    ease = easing(f'(on-1)/{denom}')
    graph = [
        f'[{old}]split[{tag}h0][{tag}a0]',
        f'[{tag}h0]trim=end_frame={start},setpts=PTS-STARTPTS[{tag}head]',
        f'[{tag}a0]trim=start_frame={start}:end_frame={end},setpts=PTS-STARTPTS[{tag}a]',
        f'[{new}]split[{tag}b0][{tag}t0]',
        f'[{tag}b0]trim=end_frame={count},setpts=PTS-STARTPTS[{tag}b]',
        f'[{tag}t0]trim=start_frame={count},setpts=PTS-STARTPTS[{tag}tail]',
    ]
    if edge.name == 'desk-rise':
        table = ((.12, .51), (.88, .51), (-.09, 1.025), (1.09, 1.025))
        waiting = ((.19, 1.04), (.81, 1.04), (.06, 1.58), (.94, 1.58))
        delay = min(count - 2, round((count - 1) * .16))
        rise = easing(f'(on-1-{delay})/{max(1, count - 1 - delay)}')
        graph.extend([
            f'[{tag}a]split[{tag}under][{tag}sheet]',
            f'[{tag}under]gblur=sigma=9,eq=brightness=-0.20:contrast=0.70:saturation=0.60[{tag}room]',
            f'[{tag}sheet]fade=t=out:s=0:n={2 * denom},'
            + projection(FULL, table, ease, transparent=True) + f'[{tag}table]',
            f'[{tag}room][{tag}table]overlay=format=yuv444[{tag}base]',
            f'[{tag}b]' + projection(waiting, FULL, rise, transparent=True) + f'[{tag}card]',
            f'[{tag}base][{tag}card]overlay=format=yuv444[{tag}pass]',
        ])
    elif edge.name in ('whip-left', 'whip-right', 'whip-up', 'whip-down'):
        e = smootherstep(f'n/{denom}')
        horizontal = edge.name in ('whip-left', 'whip-right')
        if edge.name == 'whip-left':
            stack, crop_expr, blur = f'[{tag}a][{tag}b]hstack', f"w=iw/2:h=ih:x='round((iw/2)*{e})':y=0", 'sigma=22:sigmaV=0.1'
        elif edge.name == 'whip-right':
            stack, crop_expr, blur = f'[{tag}b][{tag}a]hstack', f"w=iw/2:h=ih:x='round((iw/2)*(1-{e}))':y=0", 'sigma=22:sigmaV=0.1'
        elif edge.name == 'whip-up':
            stack, crop_expr, blur = f'[{tag}a][{tag}b]vstack', f"w=iw:h=ih/2:x=0:y='round((ih/2)*{e})'", 'sigma=0.1:sigmaV=22'
        else:
            stack, crop_expr, blur = f'[{tag}b][{tag}a]vstack', f"w=iw:h=ih/2:x=0:y='round((ih/2)*(1-{e}))'", 'sigma=0.1:sigmaV=22'
        # Peak directional blur occurs at midpoint velocity; frame 0 and final frame remain 100% sharp.
        bell = f'pow(sin(PI*clip(N/{denom},0,1)),1.6)'
        graph.extend([
            f'{stack}[{tag}wide]',
            f'[{tag}wide]crop={crop_expr},setpts=PTS-STARTPTS,split[{tag}sh][{tag}bl0]',
            f'[{tag}bl0]gblur={blur}[{tag}bl]',
            f"[{tag}sh][{tag}bl]blend=all_expr='A*(1-{bell})+B*{bell}'[{tag}pass]",
        ])
    elif edge.name == 'blur-dissolve':
        p = f'clip(N/{denom},0,1)'
        s = f'({p}*{p}*(3-2*{p}))'
        graph.extend([
            f'[{tag}a]split[{tag}as][{tag}ab0]',
            f'[{tag}ab0]gblur=sigma=14[{tag}ab]',
            f"[{tag}as][{tag}ab]blend=all_expr='A*(1-{p})+B*{p}'[{tag}ao]",
            f'[{tag}b]split[{tag}bs][{tag}bb0]',
            f'[{tag}bb0]gblur=sigma=14[{tag}bb]',
            f"[{tag}bs][{tag}bb]blend=all_expr='B*(1-{p})+A*{p}'[{tag}bi]",
            f"[{tag}ao][{tag}bi]blend=all_expr='A*(1-{s})+B*{s}'[{tag}pass]",
        ])
    elif edge.name in BRAND_YUV:
        cy, cu, cv = BRAND_YUV[edge.name]
        p = f'clip(N/{denom},0,1)'
        # Smooth ease down to brand surface in first half, rise out in second half.
        a_out = f'clip(1-2*{p},0,1)'
        b_in = f'clip(2*{p}-1,0,1)'
        ea = f'({a_out}*{a_out}*(3-2*{a_out}))'
        eb = f'({b_in}*{b_in}*(3-2*{b_in}))'
        graph.append(
            f"[{tag}a][{tag}b]blend="
            f"c0_expr='if(lte({p},0.5),{cy}+(A-{cy})*{ea},{cy}+(B-{cy})*{eb})':"
            f"c1_expr='if(lte({p},0.5),{cu}+(A-{cu})*{ea},{cu}+(B-{cu})*{eb})':"
            f"c2_expr='if(lte({p},0.5),{cv}+(A-{cv})*{ea},{cv}+(B-{cv})*{eb})'[{tag}pass]"
        )
    elif edge.name == 'pull-back':
        x, y = edge.anchor or (.5, .5)
        zoom = edge.zoom or 2.0
        shrunk = tuple((x + (a - x) / zoom, y + (b - y) / zoom) for a, b in FULL)
        settle = edge.settle if edge.settle is not None else 1.14
        incoming_start = tuple((x + (a - x) * settle, y + (b - y) * settle) for a, b in FULL)
        graph.extend([
            f'[{tag}b]' + projection(incoming_start, FULL, ease, transparent=False) + f'[{tag}bin]',
            f'[{tag}a]' + projection(FULL, shrunk, ease, transparent=True) + f',split[{tag}sharp][{tag}blur0]',
            f'[{tag}blur0]gblur=sigma=3.5,format=yuva444p,fade=t=in:s=0:n={denom}:alpha=1[{tag}soft]',
            f'[{tag}sharp][{tag}soft]overlay=format=yuv444,format=yuva444p,'
            f'fade=t=out:s=0:n={denom}:alpha=1[{tag}out]',
            f'[{tag}bin][{tag}out]overlay=format=yuv444[{tag}pass]',
        ])
    elif edge.name == 'iris-open':
        x, y = edge.anchor or (.5, .5)
        max_r = math.hypot(max(x, 1.0 - x) * 16.0 / 9.0, max(y, 1.0 - y)) + 0.04
        p = f'clip(N/{denom},0,1)'
        e = f'({p}*{p}*(3-2*{p}))'
        rad = f'({max_r:.6g}*{e})'
        dist = f'hypot((X-W*{x:.6g})/H,(Y-H*{y:.6g})/H)'
        mix = f'clip(({rad}-{dist})/0.018+0.5,0,1)'
        graph.append(f"[{tag}a][{tag}b]blend=all_expr='if(eq(N,0),A,if(eq(N,{denom}),B,A*(1-{mix})+B*{mix}))'[{tag}pass]")
    elif edge.name == 'match-cut':
        # Graphic-to-reality coordinate match cut: both plates zoom-lock around (x, y) while an
        # expanding geometric ring aperture hands the focal geometry cleanly from A to B.
        x, y = edge.anchor or (.5, .5)
        zoom = edge.zoom or 1.45
        settle = edge.settle if edge.settle is not None else 1.32
        expanded = tuple((x + (a - x) * zoom, y + (b - y) * zoom) for a, b in FULL)
        incoming_start = tuple((x + (a - x) * settle, y + (b - y) * settle) for a, b in FULL)
        max_r = math.hypot(max(x, 1.0 - x) * 16.0 / 9.0, max(y, 1.0 - y)) + 0.06
        p = f'clip(N/{denom},0,1)'
        e = f'({p}*{p}*(3-2*{p}))'
        rad = f'(0.08+{max_r - 0.08:.6g}*{e})'
        dist = f'hypot((X-W*{x:.6g})/H,(Y-H*{y:.6g})/H)'
        mix = f'clip(({rad}-{dist})/0.022+0.5,0,1)'
        ring = f'(clip(1-abs({dist}-{rad})/0.014,0,1)*sin(PI*{p})*0.85)'
        # Exact Rec.709 limited-range YUV for Jianqiu warm amber #EDA961: (169, 90, 161).
        graph.extend([
            f'[{tag}a]' + projection(FULL, expanded, ease, transparent=False) + f'[{tag}az]',
            f'[{tag}b]' + projection(incoming_start, FULL, ease, transparent=False) + f'[{tag}bz]',
            f"[{tag}az][{tag}bz]blend="
            f"c0_expr='if(eq(N,0),A,if(eq(N,{denom}),B,(A*(1-{mix})+B*{mix})*(1-{ring})+169*{ring}))':"
            f"c1_expr='if(eq(N,0),A,if(eq(N,{denom}),B,(A*(1-{mix})+B*{mix})*(1-{ring})+90*{ring}))':"
            f"c2_expr='if(eq(N,0),A,if(eq(N,{denom}),B,(A*(1-{mix})+B*{mix})*(1-{ring})+161*{ring}))'[{tag}pass]",
        ])
    else:
        # push-through
        x, y = edge.anchor
        expanded = tuple((x + (a - x) * edge.zoom, y + (b - y) * edge.zoom) for a, b in FULL)
        b_stream = f'{tag}b'
        if edge.settle and edge.settle > 1.0:
            # Incoming settles subtly from slightly zoomed-in, matching outgoing kinetic momentum.
            b_from = tuple((x + (a - x) * edge.settle, y + (b - y) * edge.settle) for a, b in FULL)
            graph.append(f'[{tag}b]' + projection(b_from, FULL, ease, transparent=False) + f'[{tag}bin]')
            b_stream = f'{tag}bin'
        graph.extend([
            f'[{tag}a]' + projection(FULL, expanded, ease, transparent=False) + f',split[{tag}sharp][{tag}blur0]',
            f'[{tag}blur0]gblur=sigma=4,format=yuva444p,fade=t=in:s=0:n={denom}:alpha=1[{tag}soft]',
            f'[{tag}sharp][{tag}soft]overlay=format=yuv444,format=yuva444p,'
            f'fade=t=out:s=0:n={denom}:alpha=1[{tag}out]',
            f'[{b_stream}][{tag}out]overlay=format=yuv444[{tag}pass]',
        ])
    graph.append(f'[{tag}head][{tag}pass][{tag}tail]concat=n=3:v=1:a=0,settb=1/{fps},setpts=N[{tag}]')
    return graph


def rectangle(value, name):
    if (not isinstance(value, list) or len(value) != 4 or
            any(type(v) not in (float, int) or not math.isfinite(v) for v in value)):
        raise ValueError(f'{name}: use normalized [left,top,width,height]')
    x, y, w, h = value
    if min(x, y) < 0 or min(w, h) <= 0 or max(x + w, y + h) > 1 + 1e-10:
        raise ValueError(f'{name}: rectangle lies outside the programme picture')
    return ((x, y), (x + w, y), (x, y + h), (x + w, y + h))


def window_plan(shot, frames):
    """All frame offsets are local to the unpadded shot, inclusive at the four marked poses."""
    if 'evidence_window' not in shot:
        return None
    spec = shot['evidence_window']
    if not isinstance(spec, dict):
        raise ValueError('evidence_window needs a file, two rectangles and frame ranges')
    source = spec.get('file')
    if not isinstance(source, str) or not source or Path(source).is_absolute() or '..' in Path(source).parts:
        raise ValueError('Window artwork must be a project-relative picture or video')
    times = [spec.get('open'), spec.get('close')]
    if any(not isinstance(t, list) or len(t) != 2 or any(type(n) is not int for n in t) for t in times):
        raise ValueError('Use open/close frame pairs for the evidence window')
    a, b = times[0]
    c, d = times[1]
    if not 0 <= a < b <= c < d < frames:
        raise ValueError(f'{shot["id"]}: open and close the evidence window inside 0..{frames - 1}')
    source_in = spec.get('in', 0)
    if type(source_in) not in (float, int) or not math.isfinite(source_in) or source_in < 0:
        raise ValueError('Window source in must be a nonnegative time in seconds')
    dim = spec.get('dim', .65)
    if type(dim) not in (float, int) or not 0 <= dim <= 1:
        raise ValueError('Use a background brightness from 0 to 1')
    blur = spec.get('blur', 0)
    if type(blur) not in (float, int) or not math.isfinite(blur) or not 0 <= blur <= 24:
        raise ValueError('Use a background blur radius from 0 to 24')
    rectangle(spec.get('from'), 'from')
    rectangle(spec.get('to'), 'to')
    return dict(file=source, start=a, open_end=b, close_start=c, end=d, source_in=source_in,
                origin=spec['from'], destination=spec['to'], dim=dim, blur=blur)


def window_input(plan, project_path, folder, index, size, fps, probe):
    """Decode one verified source on the project clock, independently of the running plate."""
    path = project_path(plan['file'])
    still = Path(path).suffix.lower() in WINDOW_STILLS
    if still and plan['source_in']:
        raise ValueError('A still evidence source begins at zero')
    path, profile = icc_still(path, folder / f'window-color-{index}.png') if still else (path, None)
    media = probe(path)
    video = next((s for s in media['streams'] if s['codec_type'] == 'video' and not s.get('disposition', {}).get('attached_pic')), None)
    if video is None:
        raise ValueError(f'Evidence source has no picture: {path}')
    length = plan['end'] - plan['start'] + 1
    args = ['-loop', '1', '-framerate', fps] if still else ['-ss', plan['source_in']]
    if not still:
        check = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-progress', 'pipe:1', *map(str, args),
                                '-i', str(path), '-map', f"0:{video['index']}", '-an', '-vf', f'fps={fps}',
                                '-frames:v', str(length), '-f', 'null', '-'], capture_output=True, text=True, check=True)
        counts = [int(line[6:]) for line in check.stdout.splitlines() if line.startswith('frame=')]
        if not counts or counts[-1] != length:
            raise ValueError(f'Evidence source needs {length} usable picture frames from its in point: {path}')
    sw, sh, sar = upright_raster(video)
    color, report = graded_source_filter(video, raster_size=(sw, sh))
    if profile:
        report['icc_to_srgb'] = profile
    w, h = size
    _, _, dw, dh = plan['destination']
    cw, ch = max(2, round(dw * w / 2) * 2), max(2, round(dh * h / 2) * 2)
    fit = min(cw / (sw * sar), ch / sh)
    pw, ph = max(1, round(sw * sar * fit)), max(1, round(sh * fit))
    framing = (f'fps={fps},{color},format=yuv444p,scale={pw}:{ph},'
               f'pad={cw}:{ch}:(ow-iw)/2:(oh-ih)/2,setsar=1,scale={w}:{h},setsar=1,'
               f'trim=end_frame={length},settb=1/{fps},setpts=N')
    return [*args, '-i', str(Path(path).resolve())], video['index'], framing, report


def window_graph(base, source, label, plan, padding, fps):
    """Replace the middle of this plate with a moving inset; the scene clock never pauses."""
    a, b, c, d = (plan[k] for k in ('start', 'open_end', 'close_start', 'end'))
    entering = easing(f'(on-1)/{b - a}')
    leaving = easing(f'({d - a}-(on-1))/{d - c}')
    path = f'({entering}*{leaving})'
    origin, dest = rectangle(plan['origin'], 'from'), rectangle(plan['destination'], 'to')
    length = d - a + 1
    start = padding + a
    darken = 1 - plan['dim']
    blur = plan.get('blur', 0)
    fade = f'fade=t=in:s=0:n={b - a}:alpha=1,fade=t=out:s={c - a}:n={d - c}:alpha=1'
    if blur > 0:
        # Softly defocus + dim background during evidence window focus.
        bg_chain = [
            f'[{base}]split[{label}src][{label}shade]',
            f'[{label}shade]trim=start_frame={start}:end_frame={start + length},setpts=PTS-STARTPTS,'
            f'gblur=sigma={blur:.4g},eq=brightness={-0.38 * darken:.4g},format=yuva444p,{fade},setpts=PTS+{start}[{label}dim]',
            f'[{label}src][{label}dim]overlay=format=yuv444:eof_action=pass:repeatlast=0[{label}bed]',
        ]
    else:
        bg_chain = [
            f'[{base}]split[{label}src][{label}shade]',
            f'[{label}shade]trim=start_frame={start}:end_frame={start + length},setpts=PTS-STARTPTS,'
            f'format=yuva444p,lutyuv=y=16:u=128:v=128:a={round(255 * darken)},{fade},setpts=PTS+{start}[{label}dim]',
            f'[{label}src][{label}dim]overlay=format=yuv444:eof_action=pass:repeatlast=0[{label}bed]',
        ]
    return [
        *bg_chain,
        f'[{source}]' + projection(origin, dest, path, transparent=True) + f',{fade},settb=1/{fps},setpts=N+{start}[{label}insert]',
        f'[{label}bed][{label}insert]overlay=format=yuv444:eof_action=pass:repeatlast=0[{label}]',
    ]
