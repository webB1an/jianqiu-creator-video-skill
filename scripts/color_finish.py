"""Normalize each source before editing and encode a consistent SDR Rec.709 master."""
from functools import lru_cache
from io import BytesIO
import math
import re
import subprocess

from PIL import Image, ImageCms, ImageOps


OUTPUT = dict(color_space='bt709', color_transfer='bt709', color_primaries='bt709', color_range='tv')
FIELDS = ','.join(OUTPUT)
FLAGS = ['-colorspace', 'bt709', '-color_trc', 'bt709', '-color_primaries', 'bt709', '-color_range', 'tv']
UNKNOWN = {None, 'unknown', 'unspecified', 'reserved'}


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
    rgb = any(s in pix for s in ('rgb','bgr','gbr','pal8','gray','monow','monob','ya8','ya16'))
    photo = info.get('codec_name') == 'mjpeg' or pix.startswith('yuvj')
    sd = info.get('height', 1080) <= 576
    tags = {k:info[k] for k in OUTPUT if info.get(k) not in UNKNOWN}
    supplied = {**tags, **overrides}
    hdr = supplied.get('color_transfer') in {'smpte2084','arib-std-b67'}
    camera_default = 'bt2020nc' if hdr else ('smpte170m' if sd else 'bt709')
    matrix = supplied.get('color_space', 'gbr' if rgb else ('bt470bg' if photo else camera_default))
    family = {'bt2020nc':'bt2020','bt2020c':'bt2020','smpte170m':'smpte170m','bt470bg':'bt470bg'}
    defaults = dict(color_space=matrix, color_transfer='iec61966-2-1' if rgb or photo else 'bt709',
                    color_primaries='bt709' if rgb or photo else family.get(matrix,'bt709'),
                    color_range='pc' if rgb or photo else 'tv')
    color = {**defaults, **supplied}
    if any(not isinstance(v, str) or not re.fullmatch('[a-zA-Z0-9_-]+',v) for v in color.values()):
        raise ValueError('Invalid source color value')
    matrix = {'rgb':'gbr'}.get(color['color_space'], color['color_space'])
    trc, primaries = color['color_transfer'], color['color_primaries']
    if color['color_range'] not in ('pc','tv'):
        raise ValueError('Use pc or tv for input color_range')
    rng = color['color_range']
    assumed = sorted(set(OUTPUT)-set(tags)-set(overrides))
    report = dict(tagged=tags, interpreted=color, assumed=assumed)
    if color == OUTPUT and not rgb:
        return 'setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709:range=limited', report
    if hdr:
        if not has_zscale():
            raise ValueError('For HDR to SDR, use an FFmpeg build with zscale or supply a graded SDR source')
        report['tone_map'] = 'linear-light Mobius to 100-nit SDR'
        prefix = 'format=gbrp16le,' if rgb else ''
        hdr_range = 'full' if rng=='pc' else 'limited'
        return (prefix + f'zscale=min={matrix}:tin={trc}:pin={primaries}:rin={hdr_range}:t=linear:'
                'p=bt709:m=gbr:r=full:npl=100,format=gbrpf32le,tonemap=tonemap=mobius:desat=2,'
                'zscale=t=bt709:p=bt709:m=bt709:r=limited,format=yuv444p10le,'
                'sidedata=mode=delete:type=MASTERING_DISPLAY_METADATA,'
                'sidedata=mode=delete:type=CONTENT_LIGHT_LEVEL,sidedata=mode=delete:type=DYNAMIC_HDR_PLUS'), report
    # Work at 4:4:4 before resizing and protect odd-sized stills in the colorspace filter.
    w, h = raster_size or (info['width'], info['height'])
    prefix = f'pad={w+w%2}:{h+h%2},' if w%2 or h%2 else ''
    if rgb:
        prefix += 'scale=out_color_matrix=bt709:out_range=tv,format=yuv444p10le,'
        matrix, rng = 'bt709', 'tv'
    elif pix.startswith('yuvj'):
        prefix += 'scale=in_range=pc:out_range=tv,format=yuv444p10le,'
        rng = 'tv'
    filters = prefix + f'colorspace=ispace={matrix}:itrc={trc}:iprimaries={primaries}:irange={rng}:all=bt709:range=tv:format=yuv444p10'
    if w%2 or h%2:
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
        return [v/4.5 if v < .081 else ((v+.099)/1.099)**(1/.45) for v in x]
    sample = linear_triplet(balance['sample'])
    target = linear_triplet(balance['reference'])
    weights = (.2126, .7152, .0722)
    source_y = sum(k*v for k, v in zip(weights,sample))
    target_y = sum(k*v for k, v in zip(weights,target))
    return [b/a * source_y/target_y for a,b in zip(sample,target)]


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


def graded_source_filter(info, overrides=None, grade=None, *, raster_size=None):
    """Match SDR picture before compositing graphic layers, using 10-bit video-range codes."""
    filters, report = source_filter(info, overrides, raster_size=raster_size)
    if grade is None:
        return filters, report
    if not isinstance(grade, dict) or set(grade) - {'contrast', 'gamma', 'saturation', 'white_balance'}:
        raise ValueError('grade accepts contrast, gamma, saturation and white_balance')
    for key in ('gamma', 'contrast', 'saturation'):
        value = grade.get(key, 1)
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError(f'grade.{key} must be a finite nonnegative number')
    gamma = grade.get('gamma', 1)
    if gamma <= 0:
        raise ValueError('grade.gamma must be positive')
    contrast = grade.get('contrast', 1)
    saturation = grade.get('saturation', 1)
    report['grade'] = dict(grade)
    if 'white_balance' in grade:
        gains = balance_gains(grade['white_balance'])
        report['white_balance_linear_gains'] = gains
        if any(abs(v-1) > 1e-10 for v in gains):
            filters += ',' + balance_filter(gains)
    planes = []
    if gamma != 1 or contrast != 1:
        # Gamma preserves reference black and white. Contrast pivots around half signal.
        tone = f'pow(clip((val-64)/876,0,1),1/{gamma})'
        planes.append(f"y='clip(round(502+876*{contrast}*({tone}-0.5)),64,940)'")
    if saturation != 1:
        chroma = f"'clip(round(512+{saturation}*(val-512)),64,960)'"
        planes.extend([f'u={chroma}', f'v={chroma}'])
    if planes:
        filters += ',format=yuv444p10le,lutyuv=' + ':'.join(planes)
    return filters, report


def browser_frame_filter(info, framing, size, *, raster_size, overrides=None):
    """Normalize the source to programme SDR, frame it, then undo the 709 transfer for an sRGB image."""
    colour, report = source_filter(info, overrides, raster_size=raster_size)
    width,height = size
    srgb = ('colorspace=iall=bt709:irange=tv:all=bt709:trc=iec61966-2-1:range=tv:format=yuv444p10,'
            'scale=in_color_matrix=bt709:in_range=tv:out_range=pc,format=rgb24')
    if width%2 or height%2:
        srgb = f'pad={width+width%2}:{height+height%2},'+srgb+f',crop={width}:{height}:0:0'
    return f'{colour},{framing},{srgb}', report


def graphics_alpha():
    """Convert the skill's sRGB RGBA caption and logo sprites; keep their alpha."""
    # Alpha stays linear. The SDR transfer applies to the three RGB channels only.
    transfer = ("'st(0,if(lte(val,10.31475),val/3294.6,pow((val/255+0.055)/1.055,2.4)));"
                "255*if(lt(ld(0),0.018),4.5*ld(0),1.099*pow(ld(0),0.45)-0.099)'")
    return (f'format=rgba,lutrgb=r={transfer}:g={transfer}:b={transfer},'
            'scale=out_color_matrix=bt709:out_range=tv,format=yuva444p')


def verify(info):
    actual = {k:info.get(k) for k in OUTPUT}
    if actual != OUTPUT:
        raise ValueError(f'Encoded SDR master is missing its matching Rec.709 signal description: {actual}')
    return actual
