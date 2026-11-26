"""Subpixel native shot camera motion (zoom, pan, drift) on Rec.709 YUV444 plates."""
from bisect import bisect_right
from copy import deepcopy
from dataclasses import dataclass
import math
import subprocess
import tempfile

from PIL import Image, __version__ as pillow_version

from motion_layers import flow_tangents

VERSION = 'shot-camera-1'
CURVES = ('ease', 'ease_out', 'ease_in', 'flow', 'spring', 'linear')
DRIFT_PRESETS = {
    'push-in': {'from_zoom': 1.0, 'to_zoom': 1.08, 'from_at': [0.5, 0.5], 'to_at': [0.5, 0.5]},
    'pull-out': {'from_zoom': 1.08, 'to_zoom': 1.0, 'from_at': [0.5, 0.5], 'to_at': [0.5, 0.5]},
    'pan-right': {'from_zoom': 1.08, 'to_zoom': 1.08, 'from_at': [0.46, 0.5], 'to_at': [0.54, 0.5]},
    'pan-left': {'from_zoom': 1.08, 'to_zoom': 1.08, 'from_at': [0.54, 0.5], 'to_at': [0.46, 0.5]},
    'pan-up': {'from_zoom': 1.08, 'to_zoom': 1.08, 'from_at': [0.5, 0.54], 'to_at': [0.5, 0.46]},
    'pan-down': {'from_zoom': 1.08, 'to_zoom': 1.08, 'from_at': [0.5, 0.46], 'to_at': [0.5, 0.54]},
}


def _finite(v):
    return type(v) in (int, float) and math.isfinite(v)


def curve_progress(t, curve):
    t = min(1.0, max(0.0, float(t)))
    if curve == 'linear':
        return t
    if curve == 'ease_out':
        return 1.0 - (1.0 - t) ** 3
    if curve == 'ease_in':
        return t ** 3
    if curve == 'spring':
        if t <= 0.0:
            return 0.0
        if t >= 1.0:
            return 1.0
        # Lightly damped spring that lands cleanly at 1.0 with ~2.5% natural settle.
        raw = 1.0 - math.exp(-6.2 * t) * (math.cos(7.6 * t) + 0.45 * math.sin(7.6 * t))
        end = 1.0 - math.exp(-6.2) * (math.cos(7.6) + 0.45 * math.sin(7.6))
        return raw / end
    return t * t * (3.0 - 2.0 * t)


@dataclass(frozen=True)
class ShotCamera:
    frames: tuple
    poses: tuple  # (at_x, at_y, zoom)
    curve: str
    tangents: tuple

    @property
    def max_zoom(self):
        return max(p[2] for p in self.poses)

    def pose(self, frame):
        """Return (at_x, at_y, zoom) using geometric zoom and optical center interpolation."""
        idx = max(0, bisect_right(self.frames, frame) - 1)
        first = self.poses[idx]
        if idx + 1 == len(self.frames) or frame <= self.frames[idx]:
            return first
        span = self.frames[idx + 1] - self.frames[idx]
        t = (frame - self.frames[idx]) / span
        if self.curve == 'flow':
            p = t * t * (3.0 - 2.0 * t)
            w0, w1 = span * t * (1.0 - t) ** 2, -span * t * t * (1.0 - t)
            ax, ay, log_z = (
                a + (b - a) * p + w0 * m + w1 * n
                for a, b, m, n in zip(
                    (first[0], first[1], math.log(first[2])),
                    (self.poses[idx + 1][0], self.poses[idx + 1][1], math.log(self.poses[idx + 1][2])),
                    self.tangents[idx],
                    self.tangents[idx + 1],
                )
            )
            return max(0.0, min(1.0, ax)), max(0.0, min(1.0, ay)), max(1.0, math.exp(log_z))
        e = curve_progress(t, self.curve)
        nxt = self.poses[idx + 1]
        z0, z1 = first[2], nxt[2]
        zoom = max(1.0, z0 * ((z1 / z0) ** e))
        # Match optical travel to inverse-zoom crop width so zoom tracks straight into target.
        if abs(z1 - z0) > 1e-6:
            w0, w1, wc = 1.0 / z0, 1.0 / z1, 1.0 / zoom
            travel = (w0 - wc) / (w0 - w1)
        else:
            travel = e
        ax = first[0] + (nxt[0] - first[0]) * travel
        ay = first[1] + (nxt[1] - first[1]) * travel
        return max(0.0, min(1.0, ax)), max(0.0, min(1.0, ay)), zoom


def word_fields(shot):
    keys = shot.get('camera')
    if isinstance(keys, list):
        for key in keys:
            if isinstance(key, dict) and isinstance(key.get('frame'), str):
                yield key, 'frame'


def resolve_shot(shot, timeline, timing):
    if not any(word_fields(shot)):
        return shot
    from remotion_library import resolve_frame
    resolved = deepcopy(shot)
    for entry, field in word_fields(resolved):
        entry[field] = resolve_frame(entry[field], timeline, timing)
    return resolved


def parse(shot, frames):
    if 'camera' in shot and 'camera_drift' in shot:
        raise ValueError(f"{shot['id']}: choose either camera keyframes or camera_drift shorthand")
    curve = shot.get('camera_curve', 'ease')
    if curve not in CURVES:
        raise ValueError(f"{shot['id']}: camera_curve must be one of {', '.join(CURVES)}")
    if 'camera_drift' in shot:
        drift = shot['camera_drift']
        if isinstance(drift, str):
            if drift not in DRIFT_PRESETS:
                raise ValueError(f"{shot['id']}: unknown camera_drift preset {drift!r}; choose {', '.join(sorted(DRIFT_PRESETS))}")
            drift = DRIFT_PRESETS[drift]
        if not isinstance(drift, dict):
            raise ValueError(f"{shot['id']}: camera_drift must be a preset name or object")
        z0 = float(drift.get('from_zoom', 1.0))
        z1 = float(drift.get('to_zoom', drift.get('zoom', 1.08)))
        at0 = drift.get('from_at', drift.get('at', [0.5, 0.5]))
        at1 = drift.get('to_at', drift.get('at', [0.5, 0.5]))
        keys = [{'frame': 0, 'zoom': z0, 'at': at0}, {'frame': max(0, frames - 1), 'zoom': z1, 'at': at1}]
        if frames == 1:
            keys = keys[:1]
        curve = shot.get('camera_curve', drift.get('curve', 'linear' if abs(z1 - z0) <= 0.10 else 'ease'))
        if curve not in CURVES:
            raise ValueError(f"{shot['id']}: camera_curve must be one of {', '.join(CURVES)}")
    elif 'camera' in shot:
        keys = shot['camera']
        if not isinstance(keys, list) or not keys:
            raise ValueError(f"{shot['id']}: camera must be a nonempty keyframe list")
    else:
        return None

    positions, poses = [], []
    for key in keys:
        if not isinstance(key, dict):
            raise ValueError(f"{shot['id']}: each camera key needs frame, zoom and optional at")
        frame = key.get('frame')
        zoom = key.get('zoom', 1.0)
        at = key.get('at', [0.5, 0.5])
        if type(frame) is not int or not 0 <= frame < frames or (positions and frame <= positions[-1]):
            raise ValueError(f"{shot['id']}: camera key frames must strictly increase within 0..{frames - 1}")
        if not _finite(zoom) or not 1.0 <= zoom <= 8.0:
            raise ValueError(f"{shot['id']}: camera zoom must be between 1.0 and 8.0")
        if not isinstance(at, list) or len(at) != 2 or not all(_finite(v) and 0.0 <= v <= 1.0 for v in at):
            raise ValueError(f"{shot['id']}: camera at must be normalized [x, y] in [0, 1]")
        positions.append(frame)
        poses.append((float(at[0]), float(at[1]), float(zoom)))
    log_poses = tuple((x, y, math.log(z)) for x, y, z in poses)
    tangents = flow_tangents(positions, log_poses) if curve == 'flow' else ()
    return ShotCamera(tuple(positions), tuple(poses), curve, tangents)


def crop_box(plate_size, pose):
    """Compute floating-point subpixel crop (left, top, right, bottom) clamped strictly inside the plate."""
    pw, ph = plate_size
    ax, ay, zoom = pose
    cw, ch = pw / zoom, ph / zoom
    # Keep target point inside visible crop while never leaking past edges.
    left = max(0.0, min(pw - cw, ax * pw - cw * 0.5))
    top = max(0.0, min(ph - ch, ay * ph - ch * 0.5))
    return (left, top, left + cw, top + ch)


def sample_yuv_frame(y_im, u_im, v_im, output_size, box):
    if box == (0.0, 0.0, float(y_im.width), float(y_im.height)) and y_im.size == output_size:
        return y_im.tobytes() + u_im.tobytes() + v_im.tobytes()
    out_y = y_im.resize(output_size, Image.Resampling.BICUBIC, box=box)
    out_u = u_im.resize(output_size, Image.Resampling.BICUBIC, box=box)
    out_v = v_im.resize(output_size, Image.Resampling.BICUBIC, box=box)
    return out_y.tobytes() + out_u.tobytes() + out_v.tobytes()


def working_size(source_size, output_size, max_zoom):
    """Retain real high-res source detail up to max_zoom (capped at 2x output) before cropping."""
    sw, sh = source_size
    ow, oh = output_size
    factor = min(max_zoom, 2.0, sw / ow if sw >= ow else 1.0, sh / oh if sh >= oh else 1.0)
    factor = max(1.0, factor)
    rw = max(ow, round(ow * factor / 2) * 2)
    rh = max(oh, round(oh * factor / 2) * 2)
    return rw, rh


def encode(camera, source_inputs, decode_filter, post_filter, output_args, target, *,
           plate_size, output_size, fps, frames, before, after, video_map='0:v:0', is_still=False,
           layers=(), layer_paths=(), layer_camera=None, crop_at=None):
    """Render graded source -> subpixel YUV444 camera -> optional finish & motion_layers -> target MP4."""
    pw, ph = plate_size
    ow, oh = output_size
    plane_bytes = pw * ph
    frame_bytes = plane_bytes * 3
    total = before + frames + after
    if crop_at is None:
        def crop_at(frame):
            return crop_box(plate_size, camera.pose(frame))

    if is_still:
        # Decode exactly one graded high-res YUV444 frame for stills.
        dec_cmd = ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
                   *source_inputs, '-map', video_map, '-an',
                   '-vf', f'{decode_filter},format=yuv444p', '-frames:v', '1',
                   '-f', 'rawvideo', '-pix_fmt', 'yuv444p', 'pipe:1']
        still_buf = subprocess.check_output(list(map(str, dec_cmd)))
        if len(still_buf) != frame_bytes:
            raise ValueError('Failed to decode graded still plate for camera motion')
        y0 = Image.frombytes('L', (pw, ph), still_buf[:plane_bytes])
        u0 = Image.frombytes('L', (pw, ph), still_buf[plane_bytes:2 * plane_bytes])
        v0 = Image.frombytes('L', (pw, ph), still_buf[2 * plane_bytes:])
        decoder = None
    else:
        dec_cmd = ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error',
                   *source_inputs, '-map', video_map, '-an',
                   '-vf', f'{decode_filter},format=yuv444p', '-frames:v', str(total),
                   '-f', 'rawvideo', '-pix_fmt', 'yuv444p', 'pipe:1']
        decoder = subprocess.Popen(list(map(str, dec_cmd)), stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    # Encode via pipe into libx264 (with optional finish & motion_layers).
    if layers:
        import color_finish
        import motion_layers
        post = f',{post_filter}' if post_filter else ''
        graph = (f'[0:v]setpts=PTS-STARTPTS{post},format=yuv444p[base];'
                 f'[1:v]{color_finish.graphics_alpha()}[art];'
                 '[base][art]overlay=format=yuv444:shortest=1,format=yuv420p[composite]')
        with tempfile.TemporaryDirectory(dir=target.parent) as tmp:
            # Stage camera-rendered base plate into a fast lossless/near-lossless temp file,
            # then composite motion_layers using the standard compositor to avoid dual-pipe deadlock.
            base_plate = f'{tmp}/camera-base.mp4'
            base_vf = (f'{post_filter},format=yuv420p' if post_filter else 'format=yuv420p')
            enc_cmd = ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                       '-f', 'rawvideo', '-pix_fmt', 'yuv444p', '-s', f'{ow}x{oh}',
                       '-framerate', str(fps), '-i', 'pipe:0',
                       '-an', '-vf', base_vf, '-frames:v', str(total),
                       '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '10', base_plate]
            _pipe_frames(crop_at, decoder, y0 if is_still else None, u0 if is_still else None,
                         v0 if is_still else None, plate_size, output_size, before, frames, after, enc_cmd)
            motion_layers.encode(layers, layer_paths, ['-i', base_plate], 'null', output_args,
                                 target, size=output_size, fps=fps, frames=frames, before=before, after=after,
                                 video_map='0:v:0', camera=layer_camera)
        return

    post_vf = f'{post_filter},format=yuv420p' if post_filter else 'format=yuv420p'
    enc_cmd = ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
               '-f', 'rawvideo', '-pix_fmt', 'yuv444p', '-s', f'{ow}x{oh}',
               '-framerate', str(fps), '-i', 'pipe:0',
               '-an', '-vf', post_vf, *map(str, output_args), str(target)]
    _pipe_frames(crop_at, decoder, y0 if is_still else None, u0 if is_still else None,
                 v0 if is_still else None, plate_size, output_size, before, frames, after, enc_cmd)


def _pipe_frames(crop_at, decoder, y0, u0, v0, plate_size, output_size, before, frames, after, enc_cmd):
    pw, ph = plate_size
    plane_bytes = pw * ph
    frame_bytes = plane_bytes * 3
    with tempfile.TemporaryFile() as log, subprocess.Popen(enc_cmd, stdin=subprocess.PIPE, stderr=log) as encoder:
        last_box = None
        last_bytes = None
        try:
            with encoder.stdin as stream:
                for n in range(-before, frames + after):
                    box = crop_at(n)
                    if decoder is None:
                        if box != last_box:
                            last_bytes = sample_yuv_frame(y0, u0, v0, output_size, box)
                            last_box = box
                        stream.write(last_bytes)
                    else:
                        raw = decoder.stdout.read(frame_bytes)
                        if len(raw) != frame_bytes:
                            raise ValueError('Moving source ended early during camera render')
                        y_im = Image.frombytes('L', (pw, ph), raw[:plane_bytes])
                        u_im = Image.frombytes('L', (pw, ph), raw[plane_bytes:2 * plane_bytes])
                        v_im = Image.frombytes('L', (pw, ph), raw[2 * plane_bytes:])
                        stream.write(sample_yuv_frame(y_im, u_im, v_im, output_size, box))
        except BrokenPipeError:
            pass
        finally:
            if decoder is not None:
                decoder.stdout.close()
                dec_err = decoder.stderr.read().decode(errors='replace') if decoder.stderr else ''
                if decoder.stderr:
                    decoder.stderr.close()
                if decoder.wait():
                    raise RuntimeError(f'Camera source decode failed: {dec_err}')
        if encoder.wait():
            log.seek(0)
            raise subprocess.CalledProcessError(encoder.returncode, enc_cmd, stderr=log.read().decode(errors='replace'))


def recipe(shot, camera, plate_size, before, after):
    return [VERSION, pillow_version, camera.curve, list(camera.frames),
            [list(p) for p in camera.poses], list(plate_size), before, after]
