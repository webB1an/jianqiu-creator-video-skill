"""The same brand mark and absolute-time subtitles on a full render or a shot revision."""
from PIL import Image, ImageDraw
from identity import lockup
from color_finish import graphics_alpha


def brand_sticker(height):
    icon = lockup(max(25, round(height * .039)), short=True)
    pad = max(4, round(height * .008))
    bug = Image.new('RGBA', (icon.width + pad * 2, icon.height + pad))
    ImageDraw.Draw(bug).rounded_rectangle((0, 0, bug.width - 1, bug.height - 1),
                                          radius=pad, fill=(15, 19, 16, 154))
    bug.alpha_composite(icon, (pad, pad // 2))
    return bug


def brand_image(width, height, path):
    brand_sticker(height).save(path)


def caption_y(top_ranges, fps, *, top='0', bottom='H-h'):
    if not top_ranges:
        return bottom
    # FFmpeg timestamps can round either side of the nominal frame boundary.
    active = '+'.join(f'between(t*{fps},{lo-.5},{hi-.5})' for lo,hi in top_ranges)
    return f"'if({active},{top},{bottom})'"


def overlay_chain(width, height, font, *, brand_index, caption_index=None, start_frame=0, fps=30,
                  source_filter=None, top_ranges=(), montage_pins=()):
    style = f'FontName={font},FontSize=13,Outline=1.1,Shadow=0,MarginV=16,Alignment=2'
    layers = f'[{brand_index}:v]{graphics_alpha()}[mark];'
    base = '[0:v]'
    if source_filter:
        layers += f'[0:v]{source_filter},format=yuv420p[base];'
        base = '[base]'
    for index, x, y, lo, hi in montage_pins:
        layers += (f'[{index}:v]{graphics_alpha()}[pin{index}];'
                   f"{base}[pin{index}]overlay={x}:{y}:enable='between(n,{lo},{hi-1})'[pinned{index}];")
        base = f'[pinned{index}]'
    picture = f'{base}[mark]overlay=W-w-{round(width*.042)}:{round(height*.055)}'
    if caption_index is not None:
        layers += f'[{caption_index}:v]{graphics_alpha()}[text];'
        y = caption_y(top_ranges, fps)
        return layers + picture + f'[branded];[branded][text]overlay=0:{y},format=yuv420p[picture]'
    if start_frame:
        picture += f',settb=1/{fps},setpts=PTS-STARTPTS+{start_frame}'
    picture += f",subtitles=captions.srt:force_style='{style}'"
    if start_frame:
        picture += ',setpts=PTS-STARTPTS'
    return layers + picture + '[picture]'
