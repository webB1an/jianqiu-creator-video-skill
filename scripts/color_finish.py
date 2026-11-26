"""Normalize each source before editing, apply 10-bit SDR grading/optical finish, and encode Rec.709."""
from functools import lru_cache
from io import BytesIO
import math
from pathlib import Path
import re
import subprocess

from PIL import Image, ImageCms, ImageOps


OUTPUT = dict(color_space='bt709', color_transfer='bt709', color_primaries='bt709', color_range='tv')
FIELDS = ','.join(OUTPUT)
FLAGS = ['-colorspace', 'bt709', '-color_trc', 'bt709', '-color_primaries', 'bt709', '-color_range', 'tv']
UNKNOWN = {None, 'unknown', 'unspecified', 'reserved'}

# Curated, restrained editorial looks calibrated for Rec.709 SDR delivery.
LOOKS = {
    'editorial-warm': {
        'temperature': 0.14, 'contrast': 1.05, 'shadows': -0.05, 'highlights': -0.06,
        'saturation': 0.96, 'vibrance': 0.10,
    },
    'tech-cool': {
        'temperature': -0.14, 'tint': -0.03, 'contrast': 1.06, 'shadows': -0.04, 'highlights': 0.04,
        'saturation': 0.94, 'vibrance': 0.12,
    },
    'archive-sepia': {
        'temperature': 0.24, 'contrast': 1.04, 'gamma': 0.98, 'shadows': 0.08, 'highlights': -0.08,
        'saturation': 0.62,
    },
    'cinema-neutral': {
        'contrast': 1.08, 'shadows': -0.06, 'highlights': -0.10, 'saturation': 0.95, 'vibrance': 0.08,
    },
    'high-contrast-doc': {
        'contrast': 1.12, 'shadows': -0.08, 'highlights': 0.04, 'saturation': 0.90, 'vibrance': 0.10,
    },
    'night-slate': {
        'temperature': -0.18, 'exposure': -0.15, 'contrast': 1.07, 'shadows': -0.06,
        'saturation': 0.88, 'vibrance': 0.14,
    },
}

GRADE_KEYS = {
    'look', 'contrast', 'gamma', 'saturation', 'white_balance',
    'exposure', 'temperature', 'tint', 'shadows', 'highlights', 'vibrance',
    'lut', 'lut_strength',
}
FINISH_KEYS = {'vignette', 'grain', 'sharpen', 'bloom', 'blur'}
GENERATED_SHOT_KEYS = ('remotion_effect', 'explainer_scene', 'value_comparison', 'math_motion', 'geo_map', 'parallax_25d')


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def effective_grade(edit, shot):
    """Resolve per-shot grade with optional project-level default (skipped on generated graphics)."""
    own = shot.get('grade')
    if own is False:
        return None
    if own is not None and not isinstance(own, dict):
        raise ValueError(f"{shot.get('id', 'shot')}: grade must be an object or false")
    base = edit.get('grade') if isinstance(edit, dict) else None
    if base is not None and not isinstance(base, dict):
        raise ValueError('edit.grade must be an object')
    if any(k in shot for k in GENERATED_SHOT_KEYS):
        return dict(own) if own else None
    if not base and not own:
        return None
    merged = {}
    if base:
        merged.update(base)
    if own:
        merged.update(own)
    return merged or None


def effective_finish(edit, shot):
    """Resolve per-shot optical finish with optional project-level default (skipped on generated graphics)."""
    own = shot.get('finish')
    if own is False:
        return None
    if own is not None and not isinstance(own, dict):
        raise ValueError(f"{shot.get('id', 'shot')}: finish must be an object or false")
    base = edit.get('finish') if isinstance(edit, dict) else None
    if base is not None and not isinstance(base, dict):
        raise ValueError('edit.finish must be an object')
    if any(k in shot for k in GENERATED_SHOT_KEYS):
        return dict(own) if own else None
    if not base and not own:
        return None
    merged = {}
    if base:
        merged.update(base)
    if own:
        merged.update(own)
    return merged or None


def icc_still(path, target):
    """Honor embedded photo profiles before handing RGB pixels to the compositor."""
    if path.suffix.lower() not in {'.png', '.jpg', '.jpeg', '.tif', '.tiff', '.webp', '.bmp'}:
        return path, None
    with Image.open(path) as source:
        raw = source.info.get('icc_profile')
        if not raw or getattr(source, 'n_frames', 1) != 1:
            return path, None
        profile = ImageCms.ImageCmsProfile(BytesIO(raw))
        oriented = ImageOps.exif_transpose(source)
        pixels = oriented if oriented.mode in {'CMYK', 'LAB', 'L'} else oriented.convert('RGB')
        result = ImageCms.profileToProfile(pixels, profile, ImageCms.createProfile('sRGB'), outputMode='RGB')
        if 'A' in oriented.getbands() or 'transparency' in oriented.info:
            result.putalpha(oriented.convert('RGBA').getchannel('A'))
        result.save(target)
        return target, ImageCms.getProfileDescription(profile).strip() or 'embedded ICC'


@lru_cache(maxsize=1)
def has_zscale():
    filters = subprocess.check_output(['ffmpeg', '-hide_banner', '-filters'], text=True)
    return ' zscale ' in filters


def source_filter(info, overrides=None, *, raster_size=None):
    """Return a YUV Rec.709 stream plus the source interpretation used for this shot."""
    overrides = {} if overrides is None else overrides
    if not isinstance(overrides, dict) or set(overrides) - set(OUTPUT):
        raise ValueError('input_color accepts ffprobe color_space, color_transfer, color_primaries and color_range')
    pix = info.get('pix_fmt', '')
    rgb = any(s in pix for s in ('rgb', 'bgr', 'gbr', 'pal8', 'gray', 'monow', 'monob', 'ya8', 'ya16'))
    photo = info.get('codec_name') == 'mjpeg' or pix.startswith('yuvj')
    sd = info.get('height', 1080) <= 576
    tags = {k: info[k] for k in OUTPUT if info.get(k) not in UNKNOWN}
    supplied = {**tags, **overrides}
    hdr = supplied.get('color_transfer') in {'smpte2084', 'arib-std-b67'}
    camera_default = 'bt2020nc' if hdr else ('smpte170m' if sd else 'bt709')
    matrix = supplied.get('color_space', 'gbr' if rgb else ('bt470bg' if photo else camera_default))
    family = {'bt2020nc': 'bt2020', 'bt2020c': 'bt2020', 'smpte170m': 'smpte170m', 'bt470bg': 'bt470bg'}
    defaults = dict(color_space=matrix, color_transfer='iec61966-2-1' if rgb or photo else 'bt709',
                    color_primaries='bt709' if rgb or photo else family.get(matrix, 'bt709'),
                    color_range='pc' if rgb or photo else 'tv')
    color = {**defaults, **supplied}
    if any(not isinstance(v, str) or not re.fullmatch('[a-zA-Z0-9_-]+', v) for v in color.values()):
        raise ValueError('Invalid source color value')
    matrix = {'rgb': 'gbr'}.get(color['color_space'], color['color_space'])
    trc, primaries = color['color_transfer'], color['color_primaries']
    if color['color_range'] not in ('pc', 'tv'):
        raise ValueError('Use pc or tv for input color_range')
    rng = color['color_range']
    assumed = sorted(set(OUTPUT) - set(tags) - set(overrides))
    report = dict(tagged=tags, interpreted=color, assumed=assumed)
    if color == OUTPUT and not rgb:
        return 'setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=limited', report
    if hdr:
        if not has_zscale():
            raise ValueError('For HDR to SDR, use an FFmpeg build with zscale or supply a graded SDR source')
        report['tone_map'] = 'linear-light Mobius to 100-nit SDR'
        prefix = 'format=gbrp16le,' if rgb else ''
        hdr_range = 'full' if rng == 'pc' else 'limited'
        return (prefix + f'zscale=min={matrix}:tin={trc}:pin={primaries}:rin={hdr_range}:t=linear:'
                'p=bt709:m=gbr:r=full:npl=100,format=gbrpf32le,tonemap=tonemap=mobius:desat=2,'
                'zscale=t=bt709:p=bt709:m=bt709:r=limited,format=yuv444p10le,'
                'sidedata=mode=delete:type=MASTERING_DISPLAY_METADATA,'
                'sidedata=mode=delete:type=CONTENT_LIGHT_LEVEL,sidedata=mode=delete:type=DYNAMIC_HDR_PLUS'), report
    # Work at 4:4:4 before resizing and protect odd-sized stills in the colorspace filter.
    w, h = raster_size or (info['width'], info['height'])
    prefix = f'pad={w + w % 2}:{h + h % 2},' if w % 2 or h % 2 else ''
    if rgb:
        prefix += 'scale=out_color_matrix=bt709:out_range=tv,format=yuv444p10le,'
        matrix, rng = 'bt709', 'tv'
    elif pix.startswith('yuvj'):
        prefix += 'scale=in_range=pc:out_range=tv,format=yuv444p10le,'
        rng = 'tv'
    filters = prefix + f'colorspace=ispace={matrix}:itrc={trc}:iprimaries={primaries}:irange={rng}:all=bt709:range=tv:format=yuv444p10'
    if w % 2 or h % 2:
        filters += f',crop={w}:{h}:0:0'
    return filters, report


def balance_gains(balance):
    """Map a measured neutral patch to the reference chromaticity while keeping patch luminance."""
    if not isinstance(balance, dict) or set(balance) != {'sample', 'reference'}:
        raise ValueError('grade.white_balance requires sample and reference RGB triples')

    def linear_triplet(value):
        if (not isinstance(value, list) or len(value) != 3
                or any(type(v) not in (int, float) or not math.isfinite(v) or not 0 < v < 255 for v in value)):
            raise ValueError('Measure an unclipped neutral patch in full-range Rec.709 RGB codes (0 < value < 255)')
        x = [v / 255 for v in value]
        return [v / 4.5 if v < .081 else ((v + .099) / 1.099) ** (1 / .45) for v in x]

    sample = linear_triplet(balance['sample'])
    target = linear_triplet(balance['reference'])
    weights = (.2126, .7152, .0722)
    source_y = sum(k * v for k, v in zip(weights, sample))
    target_y = sum(k * v for k, v in zip(weights, target))
    return [b / a * source_y / target_y for a, b in zip(sample, target)]


def chromatic_gains(temperature=0.0, tint=0.0):
    """Luminance-preserving linear-RGB chromatic multipliers for warm/cool and green/magenta shifts."""
    r = (1.0 + 0.28 * temperature) * (1.0 + 0.12 * tint)
    g = (1.0 + 0.02 * temperature) * (1.0 - 0.18 * tint)
    b = (1.0 - 0.28 * temperature) * (1.0 + 0.12 * tint)
    luma = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return [r / luma, g / luma, b / luma]


def balance_filter(gains):
    # The input is already normalised to Rec.709. Work in full-range RGB with the 709 transfer pair.
    curves = []
    for channel, gain in zip('rgb', gains):
        forward = 'if(lt(val/maxval,0.081),val/maxval/4.5,pow((val/maxval+0.099)/1.099,1/0.45))'
        encoded = 'if(lt(ld(0),0.018),4.5*ld(0),1.099*pow(ld(0),0.45)-0.099)'
        curves.append(f"{channel}='st(0,min(1,{gain:.12g}*{forward}));round(maxval*{encoded})'")
    return ('scale=in_color_matrix=bt709:in_range=tv:out_range=pc,format=gbrp16le,lutrgb='
            + ':'.join(curves)
            + ',scale=out_color_matrix=bt709:in_range=pc:out_range=tv,format=yuv444p10le')


def _escape_filter_path(path):
    return str(Path(path).resolve()).replace('\\', '\\\\').replace(':', '\\:').replace("'", "\\'")


def lut_filter(path, strength=1.0):
    escaped = _escape_filter_path(path)
    to_rgb = 'scale=in_color_matrix=bt709:in_range=tv:out_range=pc,format=gbrp16le'
    to_yuv = 'scale=out_color_matrix=bt709:in_range=pc:out_range=tv,format=yuv444p10le'
    if strength >= 1.0 - 1e-6:
        return f"{to_rgb},lut3d=file='{escaped}':interp=tetrahedral,{to_yuv}"
    return (f"{to_rgb},split[lut_orig][lut_in];"
            f"[lut_in]lut3d=file='{escaped}':interp=tetrahedral[lut_out];"
            f"[lut_orig][lut_out]blend=all_expr='A*(1-{strength:.6g})+B*{strength:.6g}',{to_yuv}")


def resolve_grade(grade):
    """Expand a look preset and validate every grading parameter."""
    if grade is None:
        return None
    if not isinstance(grade, dict) or set(grade) - GRADE_KEYS:
        raise ValueError('grade accepts ' + ', '.join(sorted(GRADE_KEYS)))
    resolved = {}
    if 'look' in grade:
        look = grade['look']
        if not isinstance(look, str) or look not in LOOKS:
            raise ValueError(f"Unknown grade.look {look!r}; choose from {', '.join(sorted(LOOKS))}")
        resolved.update(LOOKS[look])
    for key, value in grade.items():
        if key != 'look':
            resolved[key] = value
    for key in ('gamma', 'contrast', 'saturation'):
        value = resolved.get(key, 1)
        if not _finite(value) or value < 0:
            raise ValueError(f'grade.{key} must be a finite nonnegative number')
    if resolved.get('gamma', 1) <= 0:
        raise ValueError('grade.gamma must be positive')
    exposure = resolved.get('exposure', 0)
    if not _finite(exposure) or not -3.0 <= exposure <= 3.0:
        raise ValueError('grade.exposure must be in [-3, 3] stops')
    for key in ('temperature', 'tint', 'shadows', 'highlights'):
        value = resolved.get(key, 0)
        if not _finite(value) or not -1.0 <= value <= 1.0:
            raise ValueError(f'grade.{key} must be in [-1, 1]')
    vibrance = resolved.get('vibrance', 0)
    if not _finite(vibrance) or not -0.6 <= vibrance <= 0.8:
        raise ValueError('grade.vibrance must be in [-0.6, 0.8]')
    if 'lut' in resolved:
        lut = resolved['lut']
        if not isinstance(lut, str) or not lut.lower().endswith('.cube') or Path(lut).is_absolute() or '..' in Path(lut).parts:
            raise ValueError('grade.lut must be a project-relative .cube file')
    strength = resolved.get('lut_strength', 1.0)
    if not _finite(strength) or not 0.0 <= strength <= 1.0:
        raise ValueError('grade.lut_strength must be in [0, 1]')
    return resolved


def graded_source_filter(info, overrides=None, grade=None, *, raster_size=None, pin=None):
    """Match SDR picture before compositing graphic layers, using 10-bit video-range codes."""
    filters, report = source_filter(info, overrides, raster_size=raster_size)
    if grade is None:
        return filters, report
    resolved = resolve_grade(grade)
    report['grade'] = dict(grade)
    if 'look' in grade:
        report['grade_resolved'] = dict(resolved)

    gains = [1.0, 1.0, 1.0]
    if 'white_balance' in resolved:
        wb = balance_gains(resolved['white_balance'])
        report['white_balance_linear_gains'] = wb
        gains = [a * b for a, b in zip(gains, wb)]
    exposure = float(resolved.get('exposure', 0))
    if exposure != 0:
        ev = 2.0 ** exposure
        gains = [g * ev for g in gains]
    temp = float(resolved.get('temperature', 0))
    tint = float(resolved.get('tint', 0))
    if temp != 0 or tint != 0:
        cg = chromatic_gains(temp, tint)
        gains = [a * b for a, b in zip(gains, cg)]
    if any(abs(v - 1.0) > 1e-10 for v in gains):
        filters += ',' + balance_filter(gains)

    if 'lut' in resolved:
        strength = float(resolved.get('lut_strength', 1.0))
        lut_path = pin(resolved['lut']) if pin is not None else Path(resolved['lut'])
        if not Path(lut_path).is_file():
            raise ValueError(f"Missing 3D LUT file: {resolved['lut']}")
        if strength > 0:
            filters += ',' + lut_filter(lut_path, strength)

    gamma = float(resolved.get('gamma', 1))
    contrast = float(resolved.get('contrast', 1))
    saturation = float(resolved.get('saturation', 1))
    shadows = float(resolved.get('shadows', 0))
    highlights = float(resolved.get('highlights', 0))
    vibrance = float(resolved.get('vibrance', 0))

    planes = []
    if gamma != 1 or contrast != 1 or shadows != 0 or highlights != 0:
        # Gamma preserves reference black and white; monotonic toe/shoulder shapes shadows/highlights cleanly.
        tone = f'pow(clip((val-64)/876,0,1),1/{gamma:.12g})'
        if shadows != 0 or highlights != 0:
            s_k = 0.22 * shadows
            h_k = 0.22 * highlights
            shaped = f'(st(0,{tone});clip(ld(0)+({s_k:.12g})*ld(0)*(1-ld(0))*(1-ld(0))+({h_k:.12g})*ld(0)*ld(0)*(1-ld(0)),0,1))'
        else:
            shaped = tone
        planes.append(f"y='clip(round(502+876*{contrast:.12g}*({shaped}-0.5)),64,940)'")
    if saturation != 1 or vibrance != 0:
        if vibrance != 0:
            chroma = (f"'clip(round(512+{saturation:.12g}*(val-512)*"
                      f"(1+({vibrance:.12g})*(1-min(1,abs(val-512)/320)))),64,960)'")
        else:
            chroma = f"'clip(round(512+{saturation:.12g}*(val-512)),64,960)'"
        planes.extend([f'u={chroma}', f'v={chroma}'])
    if planes:
        filters += ',format=yuv444p10le,lutyuv=' + ':'.join(planes)
    return filters, report


def finish_filter(finish):
    """Build post-framing optical finishing filters (blur, sharpen, bloom, vignette, grain)."""
    if not finish:
        return ''
    if not isinstance(finish, dict) or set(finish) - FINISH_KEYS:
        raise ValueError('finish accepts ' + ', '.join(sorted(FINISH_KEYS)))
    blur = finish.get('blur', 0)
    sharpen = finish.get('sharpen', 0)
    bloom = finish.get('bloom', 0)
    vignette = finish.get('vignette', 0)
    grain = finish.get('grain', 0)
    if not _finite(blur) or not 0 <= blur <= 40:
        raise ValueError('finish.blur must be in [0, 40]')
    for name, value in (('sharpen', sharpen), ('bloom', bloom), ('vignette', vignette), ('grain', grain)):
        if not _finite(value) or not 0 <= value <= 1:
            raise ValueError(f'finish.{name} must be in [0, 1]')
    parts = []
    if blur > 0:
        parts.append(f'gblur=sigma={blur:.4g}')
    if sharpen > 0:
        parts.append(f'unsharp=5:5:{0.9 * sharpen:.4g}:5:5:0')
    if bloom > 0:
        parts.append(
            f"format=yuv444p,split[bl_s][bl_h];"
            f"[bl_h]lutyuv=y='clip((val-180)*3.2,0,235)':u=128:v=128,gblur=sigma=18[bl_g];"
            f"[bl_s][bl_g]blend=c0_expr='min(235,A+B*{0.30 * bloom:.4g})':c1_expr=A:c2_expr=A"
        )
    if vignette > 0:
        angle = 0.22 + 0.52 * vignette
        parts.append(f'vignette=angle={angle:.4g}:eval=init')
    if grain > 0:
        strength = round(1.5 + 8.5 * grain, 2)
        parts.append(f'noise=c0s={strength}:c0f=t+u:c1s=0:c2s=0')
    return ','.join(parts)


def browser_frame_filter(info, framing, size, *, raster_size, overrides=None):
    """Normalize the source to programme SDR, frame it, then undo the 709 transfer for an sRGB image."""
    colour, report = source_filter(info, overrides, raster_size=raster_size)
    width, height = size
    srgb = ('colorspace=iall=bt709:irange=tv:all=bt709:trc=iec61966-2-1:range=tv:format=yuv444p10,'
            'scale=in_color_matrix=bt709:in_range=tv:out_range=pc,format=rgb24')
    if width % 2 or height % 2:
        srgb = f'pad={width + width % 2}:{height + height % 2},' + srgb + f',crop={width}:{height}:0:0'
    return f'{colour},{framing},{srgb}', report


def graphics_alpha():
    """Convert the skill's sRGB RGBA caption and logo sprites; keep their alpha."""
    # Alpha stays linear. The SDR transfer applies to the three RGB channels only.
    transfer = ("'st(0,if(lte(val,10.31475),val/3294.6,pow((val/255+0.055)/1.055,2.4)));"
                "255*if(lt(ld(0),0.018),4.5*ld(0),1.099*pow(ld(0),0.45)-0.099)'")
    return (f'format=rgba,lutrgb=r={transfer}:g={transfer}:b={transfer},'
            'scale=out_color_matrix=bt709:out_range=tv,format=yuva444p')


def verify(info):
    actual = {k: info.get(k) for k in OUTPUT}
    if actual != OUTPUT:
        raise ValueError(f'Encoded SDR master is missing its matching Rec.709 signal description: {actual}')
    return actual
