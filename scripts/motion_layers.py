"""Animate project PNGs on a shot's frame clock and composite them over graded footage."""
from bisect import bisect_right
from copy import deepcopy
from dataclasses import dataclass
import math
from pathlib import Path
import subprocess
import tempfile

from PIL import Image, ImageChops, __version__ as pillow_version

import color_finish


VERSION = 'motion-layers-3'


def flow_tangents(frames, poses):
    """Monotone cubic slopes per frame. Each joined segment shares its key's velocity."""
    resting = (0.,) * len(poses[0])
    tangents = [resting]
    for i in range(1, len(frames) - 1):
        left, right = frames[i] - frames[i-1], frames[i+1] - frames[i]
        w1, w2 = 2 * right + left, right + 2 * left
        components = []
        for a, b, c in zip(poses[i-1], poses[i], poses[i+1]):
            before, after = (b-a) / left, (c-b) / right
            if (before > 0 and after > 0) or (before < 0 and after < 0):
                components.append((w1+w2) / (w1/before + w2/after))
            else:
                components.append(0.)
        tangents.append(tuple(components))
    if len(frames) > 1:
        tangents.append(resting)
    return tuple(tangents)


@dataclass(frozen=True)
class Layer:
    file: str
    width: float
    frames: tuple
    poses: tuple  # pivot position x/y, scale, opacity, clockwise degrees, reveal fraction
    end: int | None
    curve: str = 'ease'
    tangents: tuple = ()
    pivot: tuple = (.5, .5)
    wipe: str = 'right'
    depth: float = 1.

    def pose(self, frame):
        if frame < self.frames[0] or (self.end is not None and frame >= self.end):
            return None
        return sample_keys(self.frames, self.poses, self.curve, self.tangents, frame)


def sample_keys(frames, values, curve, tangents, frame):
    index = max(0, bisect_right(frames, frame) - 1)
    first = values[index]
    if index + 1 == len(frames) or frame <= frames[index]:
        return first
    span = frames[index + 1] - frames[index]
    t = (frame - frames[index]) / span
    progress = t if curve == 'linear' else t*t*(3-2*t)
    base = tuple(a + (b-a)*progress for a, b in zip(first, values[index+1]))
    if curve != 'flow':
        return base
    start_weight, end_weight = span*t*(1-t)**2, -span*t*t*(1-t)
    return tuple(value + start_weight*m + end_weight*n
                 for value, m, n in zip(base, tangents[index], tangents[index+1]))


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


@dataclass(frozen=True)
class LayerCamera:
    frames: tuple
    views: tuple
    tangents: tuple

    def view(self, frame):
        return sample_keys(self.frames, self.views, 'flow', self.tangents, frame)


def word_fields(shot):
    """Yield declared time anchors, leaving numeric keys on their original frame clock."""
    groups = [shot.get('layer_camera')]
    layers = shot.get('motion_layers')
    if isinstance(layers, list):
        for layer in layers:
            if isinstance(layer, dict):
                if isinstance(layer.get('end'), str):
                    yield layer, 'end'
                groups.append(layer.get('keys'))
    for keys in groups:
        if isinstance(keys, list):
            for key in keys:
                if isinstance(key, dict) and isinstance(key.get('frame'), str):
                    yield key, 'frame'


def resolve_shot(shot, timeline, timing):
    """Translate spoken programme cues to local picture frames for every consumer."""
    if not any(word_fields(shot)):
        return shot
    from remotion_library import resolve_frame
    resolved = deepcopy(shot)
    # Offsets use [first, end). A layer can disappear at the cut itself.
    exclusive = dict(timing, frames=timing['frames'] + 1)
    for entry, field in word_fields(resolved):
        entry[field] = resolve_frame(entry[field], timeline, exclusive if field == 'end' else timing)
    return resolved


def parse_camera(shot, frames):
    if 'layer_camera' not in shot:
        return None
    keys = shot['layer_camera']
    label = f"{shot['id']}.layer_camera"
    if not shot.get('motion_layers') or not isinstance(keys, list) or not keys:
        raise ValueError(f'{label}: provide camera keys for a shot with motion_layers')
    positions, views = [], []
    for key in keys:
        if not isinstance(key, dict):
            raise ValueError(f'{label}: each camera key needs frame, look_at and optional zoom')
        frame, look, zoom = key.get('frame'), key.get('look_at'), key.get('zoom', 1)
        if type(frame) is not int or not 0 <= frame < frames or (positions and frame <= positions[-1]):
            raise ValueError(f'{label}: camera frames must increase within the shot')
        if not isinstance(look, list) or len(look) != 2 or not all(finite(v) for v in look):
            raise ValueError(f'{label}: look_at requires two finite stage coordinates')
        if not finite(zoom) or zoom <= 0:
            raise ValueError(f'{label}: zoom must be positive')
        positions.append(frame)
        views.append((*look, zoom))
    return LayerCamera(tuple(positions), tuple(views), flow_tangents(positions, views))


def project_pose(pose, view, depth):
    """Pan and zoom relative to the fixed stage. Depth 1 is the camera's target plane."""
    if pose is None or view is None or depth == 0 or view == (.5, .5, 1):
        return pose
    x, y, scale, *rest = pose
    target_x, target_y, zoom = view
    magnification = zoom ** depth
    return (.5 + (x-.5 - depth*(target_x-.5))*magnification,
            .5 + (y-.5 - depth*(target_y-.5))*magnification,
            scale*magnification, *rest)


def parse(shot, frames):
    items = shot.get('motion_layers', [])
    if not isinstance(items, list):
        raise ValueError(f"{shot['id']}: motion_layers must be an array")
    layers = []
    for index, item in enumerate(items):
        label = f"{shot['id']}.motion_layers[{index}]"
        if not isinstance(item, dict):
            raise ValueError(f'{label}: expected a layer object')
        name = item.get('file')
        if not isinstance(name, str) or Path(name).suffix.lower() != '.png' or Path(name).is_absolute() or '..' in Path(name).parts:
            raise ValueError(f'{label}: file must be a project-relative PNG')
        width = item.get('width')
        keys = item.get('keys')
        if not finite(width) or width <= 0 or not isinstance(keys, list) or not keys:
            raise ValueError(f'{label}: provide a positive width and at least one key')
        curve = item.get('curve', 'ease')
        if curve not in ('ease', 'linear', 'flow'):
            raise ValueError(f'{label}: curve must be ease, linear or flow')
        depth = item.get('depth', 1.)
        if not finite(depth) or not 0 <= depth <= 1:
            raise ValueError(f'{label}: depth must be between 0 (screen) and 1 (target plane)')
        pivot = item.get('pivot', [.5, .5])
        if not isinstance(pivot, list) or len(pivot) != 2 or not all(finite(v) and 0 <= v <= 1 for v in pivot):
            raise ValueError(f'{label}: pivot must be two PNG-relative coordinates between 0 and 1')
        wipe = item.get('wipe', 'right')
        if wipe not in ('right', 'left', 'down', 'up'):
            raise ValueError(f'{label}: set wipe to the edge movement direction: right, left, down or up')
        positions, poses = [], []
        for key in keys:
            if not isinstance(key, dict):
                raise ValueError(f'{label}: expected a key object')
            frame, at = key.get('frame'), key.get('at')
            scale, opacity = key.get('scale', 1), key.get('opacity', 1)
            if type(frame) is not int or not 0 <= frame < frames or (positions and frame <= positions[-1]):
                raise ValueError(f'{label}: key frames must increase within 0..{frames-1}')
            if not isinstance(at, list) or len(at) != 2 or not all(finite(v) for v in at):
                raise ValueError(f'{label}: at requires two finite center coordinates')
            if not finite(scale) or scale <= 0 or not finite(opacity) or not 0 <= opacity <= 1:
                raise ValueError(f'{label}: scale must be positive and opacity between 0 and 1')
            rotation, reveal = key.get('rotation', 0), key.get('reveal', 1)
            if not finite(rotation) or not finite(reveal) or not 0 <= reveal <= 1:
                raise ValueError(f'{label}: use finite rotation degrees and a reveal fraction between 0 and 1')
            positions.append(frame)
            poses.append((*at, scale, opacity, rotation, reveal))
        end = item.get('end')
        if end is not None and (type(end) is not int or not positions[-1] < end <= frames):
            raise ValueError(f'{label}: end must follow the last key and be within this shot')
        tangents = flow_tangents(positions, poses) if curve == 'flow' else ()
        layers.append(Layer(name, width, tuple(positions), tuple(poses), end, curve, tangents, tuple(pivot), wipe, depth))
    return layers


class Sprite:
    def __init__(self, image):
        self.source = image.convert('RGBa')
        self.sample_size = None
        self.padded = None

    def samples(self, size):
        if size != self.sample_size:
            # Low-pass before shrinking. Leave transparent texels beyond every edge
            # so affine interpolation also works when opaque artwork touches its border.
            reduced = self.source.resize(size, Image.Resampling.LANCZOS) if size != self.source.size else self.source
            self.padded = Image.new('RGBa', (size[0] + 4, size[1] + 4))
            self.padded.paste(reduced, (2, 2))
            self.sample_size = size
        return self.padded


def wipe_alpha(patch, inverse, sample_size, sample_scale, direction, fraction):
    """A one-output-pixel antialiased edge in the rotating PNG's own coordinates."""
    import numpy as np

    horizontal = direction in ('right', 'left')
    axis = 0 if horizontal else 1
    a, b, offset = inverse[:3] if horizontal else inverse[3:]
    # Axis-aligned wipes only need one row or column of coverage values.
    x = np.arange(patch.width if a else 1) + .5
    y = np.arange(patch.height if b else 1)[:, None] + .5
    local = a*x + b*y + offset - 2  # remove the transparent sampling border
    length = sample_size[axis]
    distance = (fraction*length - local if direction in ('right', 'down')
                else local - (1-fraction)*length)
    coverage = np.clip(.5 + distance/sample_scale[axis], 0., 1.)
    mask = Image.fromarray(np.rint(255*coverage).astype('uint8'))
    if mask.size != patch.size:
        mask = mask.resize(patch.size, Image.Resampling.NEAREST)
    patch.putalpha(ImageChops.multiply(patch.getchannel('A'), mask))


def stamp(canvas, sprite, width, pose, *, pivot=(.5, .5), wipe='right'):
    cx, cy, scale, opacity = pose[:4]
    rotation, reveal = pose[4:] if len(pose) > 4 else (0, 1)
    if opacity == 0 or reveal == 0:
        return
    w, h = canvas.size
    width_px = w * width * scale
    height_px = width_px * sprite.source.height / sprite.source.width
    sample_w = min(sprite.source.width, max(1, math.ceil(width_px)))
    sample_h = min(sprite.source.height, max(1, math.ceil(height_px)))
    sx, sy = sample_w / width_px, sample_h / height_px
    radians = math.radians(rotation % 360)
    cos, sin = round(math.cos(radians), 15), round(math.sin(radians), 15)
    anchor_x, anchor_y = pivot[0]*width_px, pivot[1]*height_px
    left = cx*w - cos*anchor_x + sin*anchor_y
    top = cy*h - sin*anchor_x - cos*anchor_y
    # Keep all four rotated corners and the interpolation footprint within the clip.
    dx, dy = cos*width_px, sin*width_px
    ex, ey = -sin*height_px, cos*height_px
    pad_x, pad_y = abs(cos)*2/sx + abs(sin)*2/sy, abs(sin)*2/sx + abs(cos)*2/sy
    x0 = max(0, math.floor(left + min(0, dx) + min(0, ex) - pad_x))
    y0 = max(0, math.floor(top + min(0, dy) + min(0, ey) - pad_y))
    x1 = min(w, math.ceil(left + max(0, dx) + max(0, ex) + pad_x))
    y1 = min(h, math.ceil(top + max(0, dy) + max(0, ey) + pad_y))
    if x1 <= x0 or y1 <= y0:
        return
    x, y = x0-left, y0-top
    inverse = (cos*sx, sin*sx, 2 + (cos*x+sin*y)*sx,
               -sin*sy, cos*sy, 2 + (-sin*x+cos*y)*sy)
    patch = sprite.samples((sample_w, sample_h)).transform(
        (x1-x0, y1-y0), Image.Transform.AFFINE, inverse, Image.Resampling.BICUBIC
    ).convert('RGBA')
    if reveal < 1:
        wipe_alpha(patch, inverse, (sample_w, sample_h), (sx, sy), wipe, reveal)
    if opacity != 1:
        patch.putalpha(patch.getchannel('A').point(lambda a: round(a * opacity)))
    canvas.alpha_composite(patch, (x0, y0))


def encode(layers, paths, base_args, base_filter, output_args, target, *, size, fps, frames, before, after,
           video_map='0:v:0', camera=None):
    w, h = size
    graph = (f'[{video_map}]{base_filter},setpts=PTS-STARTPTS,format=yuv444p[base];'
             f'[1:v]{color_finish.graphics_alpha()}[art];'
             '[base][art]overlay=format=yuv444:shortest=1,format=yuv420p[composite]')
    command = ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y', *base_args,
               '-f', 'rawvideo', '-pixel_format', 'rgba', '-video_size', f'{w}x{h}',
               '-framerate', fps, '-i', 'pipe:0', '-filter_complex_threads', 2, '-filter_complex', graph,
               '-map', '[composite]', '-an', *output_args, target]
    with tempfile.TemporaryDirectory(dir=target.parent) as tmp:
        sprites = {}
        for index, (layer, path) in enumerate(zip(layers, paths)):
            if layer.file in sprites:
                continue
            with Image.open(path) as image:
                if image.format != 'PNG' or image.n_frames != 1 or not ('A' in image.getbands() or 'transparency' in image.info):
                    raise ValueError(f'Motion layer requires a single PNG with alpha: {layer.file}')
            corrected, _ = color_finish.icc_still(path, Path(tmp)/f'{index}.png')
            with Image.open(corrected) as image:
                sprites[layer.file] = Sprite(image.convert('RGBA'))
        with tempfile.TemporaryFile() as log, subprocess.Popen(list(map(str, command)), stdin=subprocess.PIPE, stderr=log) as encoder:
            last_poses = None
            try:
                with encoder.stdin as stream:
                    for frame in range(-before, frames + after):
                        view = camera.view(frame) if camera else None
                        poses = tuple(project_pose(layer.pose(frame), view, layer.depth) for layer in layers)
                        if poses != last_poses:
                            canvas = Image.new('RGBA', size)
                            for layer, pose in zip(layers, poses):
                                if pose is not None:
                                    stamp(canvas, sprites[layer.file], layer.width, pose,
                                          pivot=layer.pivot, wipe=layer.wipe)
                            pixels = canvas.tobytes()
                            last_poses = poses
                        stream.write(pixels)
            except BrokenPipeError:
                # A short source may also close the graph early; export checks the actual frame count.
                pass
            if encoder.wait():
                log.seek(0)
                raise subprocess.CalledProcessError(encoder.returncode, command, stderr=log.read().decode(errors='replace'))


def recipe(shot, layers, pins, before, after):
    fields = [VERSION, pillow_version, color_finish.graphics_alpha(), before, after, shot['motion_layers'],
              [pins.hashes[pins.key(layer.file)] for layer in layers]]
    if 'layer_camera' in shot:
        fields.extend(['layer-camera-1', shot['layer_camera']])
    return fields
