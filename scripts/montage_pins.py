"""Place a shared piece of artwork on the stitched programme, across shot edits."""
from dataclasses import dataclass
import math
from pathlib import Path

from PIL import Image

from color_finish import icc_still
from motion_layers import Sprite, stamp


@dataclass(frozen=True)
class Pin:
    file: str
    start: int
    end: int
    at: tuple
    width: float


def plan(edit, timeline):
    rows = edit.get('montage_pins', [])
    if not isinstance(rows, list):
        raise ValueError('montage_pins must be a list')
    if not rows:
        return []
    shots = {s['id']: s for s in timeline['shots']}
    result = []
    for i, row in enumerate(rows):
        label = f'montage_pins[{i}]'
        if not isinstance(row, dict):
            raise ValueError(f'{label}: expected a pin object')
        name = row.get('file')
        if not isinstance(name, str) or Path(name).suffix.lower() != '.png' or Path(name).is_absolute() or '..' in Path(name).parts:
            raise ValueError(f'{label}: file must be a project-relative transparent PNG')
        ids = [row.get(key) for key in ('from_shot', 'through_shot')]
        if any(not isinstance(s, str) or s not in shots for s in ids):
            raise ValueError(f'{label}: from_shot and through_shot must identify programme shots')
        first, last = (shots[s] for s in ids)
        entry, exit_frame = row.get('in_frame', 0), row.get('out_frame', last['frames'])
        if (type(entry) is not int or not 0 <= entry < first['frames'] or
                type(exit_frame) is not int or not 0 < exit_frame <= last['frames']):
            raise ValueError(f'{label}: in_frame and out_frame must be inside their respective shots')
        start, end = first['start_frame'] + entry, last['start_frame'] + exit_frame
        if end <= start:
            raise ValueError(f'{label}: pin must end after it begins')
        at, width = row.get('at'), row.get('width')
        if not isinstance(at, list) or len(at) != 2 or not all(type(v) in (int, float) and 0 <= v <= 1 for v in at):
            raise ValueError(f'{label}: at requires a normalized screen centre [x,y]')
        if type(width) not in (int, float) or not math.isfinite(width) or not 0 < width <= 1:
            raise ValueError(f'{label}: width must be a positive fraction of the screen width, up to 1')
        result.append(Pin(name, start, end, tuple(at), width))
    return result


def inputs(pins, folder, size, *, start, frames, first_index, source_path=Path):
    """Prepare just the overlapping artwork, retaining the full master's screen coordinates."""
    args, layers = [], []
    for i, pin in enumerate(pins):
        lo, hi = max(0, pin.start - start), min(frames, pin.end - start)
        if lo >= hi:
            continue
        folder.mkdir(exist_ok=True)
        path = source_path(pin.file)
        with Image.open(path) as original:
            if original.format != 'PNG' or original.n_frames != 1 or not ('A' in original.getbands() or 'transparency' in original.info):
                raise ValueError(f'Montage pin requires a single PNG with alpha: {pin.file}')
        corrected, _ = icc_still(path, folder/f'{i}-srgb.png')
        with Image.open(corrected) as im:
            canvas = Image.new('RGBA', size)
            stamp(canvas, Sprite(im.convert('RGBA')), pin.width, (*pin.at, 1., 1.))
        box = canvas.getchannel('A').getbbox()
        if box is None:
            raise ValueError(f'Montage pin has no visible pixels: {pin.file}')
        # Keep chroma sampling aligned to the programme, including odd artwork origins.
        x, y = box[0] // 2 * 2, box[1] // 2 * 2
        right, bottom = (box[2]+1)//2*2, (box[3]+1)//2*2
        dest = folder/f'{i}.png'
        canvas.crop((x, y, right, bottom)).save(dest)
        args.extend(['-i', str(dest.resolve())])
        layers.append((first_index + len(layers), x, y, lo, hi))
    return args, layers
