"""Source clocks for Remotion media layers. All frame spans are half-open."""
from math import floor
from pathlib import Path
import re


MOVING = {'.mp4', '.m4v', '.mov', '.webm', '.mkv'}
OFFSET_KEYS = {'outgoing': 'outStart', 'incoming': 'inStart', 'insetSource': 'insetStart'}


def translucent(color):
    color = color.strip().lower()
    if color.startswith('#'):
        return ((len(color) == 9 and color[-2:] != 'ff') or
                (len(color) == 5 and color[-1] != 'f'))
    if color in ('transparent', 'none', 'unset', 'inherit', 'initial'):
        return True
    alpha = color.rsplit('/', 1)[-1] if '/' in color else (
        color.rsplit(',', 1)[-1] if color.startswith(('rgba(', 'hsla(')) else None)
    if alpha is not None:
        value = alpha.rstrip(')').strip()
        try:
            return float(value.rstrip('%')) < (100 if value.endswith('%') else 1)
        except ValueError:
            return True
    return not re.fullmatch(r'[a-z]+|(?:rgb|hsl)\([^)]*\)', color)


def overlap_window(params, frames, fps):
    count = params.get('overlap', min(frames, max(2, floor(fps * .5 + .5))))
    at = params.get('atFrame', (frames - count) // 2)
    if not 0 <= at < at + count <= frames:
        raise ValueError('Transition overlap must be inside the shot')
    return at, count


def background_window(effect, params, frames, fps):
    if effect in ('flip', 'cube'):
        at, count = overlap_window(params, frames, fps)
        return at + 1, at + count
    if effect == 'lower-third':
        visible = translucent(params.get('background', 'transparent'))
    elif effect in ('grid-pulse', 'logo-stroke-draw', 'credits-roll'):
        visible = params.get('transparent') or translucent(params.get('background', '#131a17'))
    elif effect == 'gradient-shift':
        visible = any(translucent(color) for key in ('from', 'to') for color in params.get(key, []))
    elif effect == 'letterbox-reveal':
        visible = translucent(params.get('background', '#131a17'))
    else:
        visible = False
    return 0, frames if visible else 0


def reveal_window(frames, start, end, visible_before, visible_after):
    """The eased reveal has interior values between its two endpoints."""
    if not visible_before and not visible_after:
        return 0, 0
    first = 0 if visible_before else start + (start != end)
    return first, frames if visible_after else end


def media_usage(spec, entry, key, file):
    """Return the layer's project file, local frames and original video time range."""
    frames, fps, p = spec['frames'], spec['fps'], spec['params']
    effect = entry['id']
    start, end, sequence_start = 0, frames, 0
    offset = p.get(OFFSET_KEYS.get(key, f'{key}Start'), 0) if key is not None else 0
    if type(offset) is not int or offset < 0:
        raise ValueError(f'{key} requires a nonnegative integer source offset')

    if key is None:
        start, end = background_window(effect, p, frames, fps)
    elif effect == 'photo-stack' and key == 'second':
        start = sequence_start = p.get('secondAt', floor((frames - 1) * .48 + .5))
        if type(start) is not int or not 0 <= start < frames:
            raise ValueError('secondAt must be a frame inside the shot')
    elif entry['group'] == 'transitions' and key in ('outgoing', 'incoming'):
        if effect == 'focus-pair':
            # The aperture unmounts when its rounded width reaches zero.
            selected = 'outgoing' if p.get('side', 'left') == 'left' else 'incoming'
            if key != selected:
                at = p.get('startFrame', floor(frames * .25))
                done = p.get('endFrame', floor(frames * .7))
                if not 0 <= at < done < frames:
                    raise ValueError('Focus pair needs two ordered frames inside the shot')
                width = spec['width']
                for frame in range(at, done + 1):
                    t = (frame - at) / (done - at)
                    eased = t**3 * (t * (t * 6 - 15) + 10)
                    seam = floor(width * (.5 + (.5 if selected == 'outgoing' else -.5) * eased) + .5)
                    if seam in (0, width):
                        end = frame
                        break
        else:
            at, overlap = overlap_window(p, frames, fps)
            if key == 'outgoing':
                end = at + overlap
            else:
                start = sequence_start = at
    elif effect in ('image-comparison-slider', 'picture-in-picture', 'letterbox-reveal'):
        at = p.get('startFrame', 0)
        if effect == 'image-comparison-slider':
            fraction = .83
            a, b = p.get('from', 0), p.get('to', 1)
            before, after = (a > 0, b > 0) if key == 'after' else (a < 1, b < 1)
        elif effect == 'letterbox-reveal':
            fraction = .6
            unmasked = translucent(p.get('color', '#131a17'))
            before, after = unmasked or p.get('from', .5) < .5, unmasked or p.get('to', .065) < .5
        else:
            fraction = .45
            before, after = (False, True) if key == 'main' else (True, True)
        field = 'settleFrame' if effect == 'letterbox-reveal' else 'endFrame'
        done = p.get(field, max(at, floor((frames - 1) * fraction + .5)))
        if not 0 <= at <= done < frames:
            raise ValueError('Reveal needs ordered frames inside the shot')
        start, end = reveal_window(frames, at, done, before, after)

    if end <= start:
        return None
    window = None
    if Path(file).suffix.lower() in MOVING:
        window = [(offset + frame - sequence_start) / fps for frame in (start, end)]
    return dict(file=file, frames=[start, end], range=window)
