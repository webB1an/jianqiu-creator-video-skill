"""Use the same upright raster coordinates for shot selection, rough cuts and delivery."""
from fractions import Fraction


def upright_raster(video):
    width, height = video['width'], video['height']
    aspect = video.get('sample_aspect_ratio', '')
    sar = Fraction(aspect.replace(':', '/')) if aspect and aspect not in ('N/A', '0:1') else Fraction(1)
    rotation = next((d['rotation'] for d in video.get('side_data_list', []) if 'rotation' in d), 0)
    if rotation % 90:
        raise ValueError('Normalize non-right-angle source rotations before specifying a crop')
    return (height, width, 1/sar) if rotation % 180 else (width, height, sar)


def crop_box(spec, width, height, sar, target):
    ratio = Fraction(*target) / sar
    if spec is None:
        w = min(width, max(1, round(height * ratio)))
        h = min(height, max(1, round(width / ratio)))
        return [(width-w)//2, (height-h)//2, w, h]
    if not isinstance(spec, list) or len(spec) != 4 or any(type(n) is not int for n in spec):
        raise ValueError('crop requires integer [x, y, width, height] in the upright source')
    x, y, w, h = spec
    if min(x,y) < 0 or min(w,h) < 1 or x+w > width or y+h > height:
        raise ValueError(f'Crop {spec} extends outside the upright {width}x{height} source')
    if abs(w-h*ratio) > max(1, ratio):
        raise ValueError(f'Crop {spec} differs from the target aspect ratio after pixel-aspect correction')
    return spec


def crop_filter(spec, video_info, size, *, output_size=None):
    """Validate a delivery crop; a smaller output_size keeps its exact field of view."""
    x, y, width, height = crop_box(spec, *upright_raster(video_info), size)
    w, h = output_size or size
    return f'crop={width}:{height}:{x}:{y}:exact=1,scale={w}:{h}:flags=lanczos,setsar=1'
