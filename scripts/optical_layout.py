"""Place editable labels and their containers from visible ink in output pixels."""
from functools import lru_cache

from PIL import Image


@lru_cache(maxsize=256)
def ink_mask(font, text):
    """Return the raster used for both measuring and painting, without invisible font bearings."""
    if not isinstance(text, str) or not text.strip() or '\n' in text:
        raise ValueError('Need a visible single-line label')
    raster = font.getmask(text, mode='L')
    bounds = raster.getbbox()
    if bounds is None:
        raise ValueError('Label has no visible glyphs in this font')
    return Image.frombytes('L', raster.size, bytes(raster)).crop(bounds)


def draw_ink(image, box, text, font, color, align='center', valign='center'):
    """Place visible ink inside a half-open (left, top, right, bottom) rectangle.

    The supplied rect is the content area, such as the part of a toggle left free by its thumb.
    For a number followed by a small unit, place the runs on a shared bottom edge.
    Returns the painted bounds for crop inspection. Drawing outside the frame is
    supported for authored entrance / exit motion.
    """
    left, top, right, bottom = box
    if right < left or bottom < top:
        raise ValueError('Content box must have ordered edges')
    mask = ink_mask(font, text)
    w, h = mask.size
    x = {'left': left, 'center': (left + right - w) / 2, 'right': right - w}[align]
    y = {'top': top, 'center': (top + bottom - h) / 2, 'bottom': bottom - h}[valign]
    x, y = round(x), round(y)
    painted = x, y, x + w, y + h
    image.paste(color, painted, mask)
    return painted


def draw_ink_plate(image, origin, text, font, color, fill, padding):
    """Paint a tight plate from its ink plus symmetric (horizontal, vertical) padding."""
    left, top = origin
    px, py = padding
    if min(px, py) < 0:
        raise ValueError('Plate padding must be nonnegative')
    width, height = ink_mask(font, text).size
    box = (left, top, left + width + 2 * px, top + height + 2 * py)
    image.paste(fill, box)
    return box, draw_ink(image, box, text, font, color)


def draw_bottom_runs(image, left, bottom, runs, gap):
    """Draw (text, font, color) runs on one visible bottom and return their measured boxes."""
    if gap < 0 or not runs:
        raise ValueError('Supply text runs and a nonnegative gap')
    boxes = []
    for text, font, color in runs:
        width, height = ink_mask(font, text).size
        box = draw_ink(image, (left, bottom - height, left + width, bottom),
                       text, font, color, 'left', 'bottom')
        boxes.append(box)
        left = box[2] + gap
    return boxes


def editable_row_boxes(box, row_edges):
    """Split card contents at y coordinates or (top, bottom) stroke bands; exclude rule pixels."""
    left, top, right, bottom = box
    if left >= right or top >= bottom:
        raise ValueError('Use a visible card content box')
    rows = []
    for edge in row_edges:
        upper, lower = edge if isinstance(edge, (tuple, list)) else (edge, edge)
        if not top < upper <= lower < bottom:
            raise ValueError('Row dividers must increase strictly inside the card')
        rows.append((left, top, right, upper))
        top = lower
    return [*rows, (left, top, right, bottom)]
