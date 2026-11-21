"""Spatial and frame reservations shared by the generated lower third and subtitle compositor."""
import math


def normalized_box(box, label):
    if (not isinstance(box, list) or len(box) != 4
            or any(type(v) not in (int, float) or not math.isfinite(v) for v in box)
            or not (0 <= box[0] < box[2] <= 1 and 0 <= box[1] < box[3] <= 1)):
        raise ValueError(f'{label}: box must contain normalized left, top, right, bottom')
    return box


def overlaps(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def protected_regions(shot, frames):
    """Protect the original readout and authored obstacles on this shot's local clock."""
    rows = shot.get('caption_avoid', [])
    if not isinstance(rows, list):
        raise ValueError(f"{shot['id']}: caption_avoid must be a list")
    reading = shot.get('evidence_read', {})
    if 'readback' in reading:
        readback = reading['readback']
        rows = [*rows, dict(label=readback['text'], frames=reading['watch'], box=readback['roi'])]
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('label'), str) or not row['label'].strip():
            raise ValueError(f"{shot['id']}: give each protected region a label")
        span = row.get('frames', [0, frames])
        if (not isinstance(span, list) or len(span) != 2 or any(type(v) is not int for v in span)
                or not 0 <= span[0] < span[1] <= frames):
            raise ValueError(f"{shot['id']}: caption_avoid.frames must be inside the shot")
        yield span[0], span[1], normalized_box(row.get('box'), f"{shot['id']}: caption_avoid"), row['label']


def place_lower_third(shot, frames, params):
    """Choose the earliest continuous interval that preserves the whole authored reading time."""
    effect = shot['remotion_effect']
    placement = effect['placement']
    if effect['effect'] != 'lower-third' or not isinstance(placement, dict):
        raise ValueError(f"{shot['id']}: placement requires a lower-third with duration_frames and box")
    box = normalized_box(placement.get('box'), f"{shot['id']}: placement")
    length = placement.get('duration_frames')
    start, end = params.get('startFrame', 0), params.get('endFrame', frames)
    if (any(type(x) is not int for x in (start, end, length))
            or not 0 <= start < end <= frames or not 1 <= length <= end-start):
        raise ValueError(f"{shot['id']}: duration_frames must fit in the lower-third window [{start}, {end})")

    blocks = sorted((max(start, a), min(end, b), label)
                    for a, b, area, label in protected_regions(shot, frames)
                    if a < end and start < b and overlaps(box, area))
    free = start
    for a, b, _ in blocks:
        if free + length <= a:
            break
        free = max(free, b)
    if free + length > end:
        labels = '、'.join(dict.fromkeys(label for _, _, label in blocks))
        raise ValueError(f"{shot['id']}: F{start}–{end} 内没有可完整显示 {length} 帧的身份条时段。"
                         f"与「{labels}」时空重合，请调整窗口、构图或显示时长。")
    return free, free + length
