"""Render three independently composed covers from a project cover.json or edit.json."""
import json
import math
import subprocess
import tempfile
from pathlib import Path
from PIL import Image, ImageColor, ImageDraw, ImageFilter, ImageOps
from identity import AMBER, INK, PAPER, comparison_color, face, lockup, put

SIZES = {'16x9': (1920, 1080), '9x16': (1080, 1920), '3x4': (1080, 1440)}


def _rgba(color, alpha=255):
    if isinstance(color, str):
        r, g, b = ImageColor.getrgb(color)[:3]
        return (r, g, b, alpha)
    return (color[0], color[1], color[2], alpha)


def window(img, size, focus, where, span):
    # span is the displayed photo width divided by the cover width.
    w, h = size
    scale = w * span / img.width
    tx = focus[0] * img.width - where[0] * w / scale
    ty = focus[1] * img.height - where[1] * h / scale
    return img.transform(size, Image.Transform.AFFINE, (1 / scale, 0, tx, 0, 1 / scale, ty),
                         Image.Resampling.BICUBIC, fillcolor=img.getpixel((0, 0)))


def _draw_shadow_text(canvas, xy, text, size, which, color, *, stroke_width=0):
    """Render crisp editorial headline with a soft grounded shadow for bright or busy photos."""
    layer = Image.new('RGBA', canvas.size)
    offset = max(2, round(size * 0.036))
    font = face(which, size)
    ImageDraw.Draw(layer).text(
        (xy[0], xy[1] + offset), text, font=font, fill=(8, 12, 10, 215), anchor='lt',
        stroke_width=stroke_width + max(1, round(size * 0.015)), stroke_fill=(8, 12, 10, 215),
    )
    layer = layer.filter(ImageFilter.GaussianBlur(radius=max(3, round(size * 0.065))))
    canvas.alpha_composite(layer)
    if stroke_width > 0:
        ImageDraw.Draw(canvas).text(xy, text, font=font, fill=color, anchor='lt',
                                    stroke_width=stroke_width, stroke_fill=(14, 20, 17, 240))
    else:
        put(canvas, xy, text, size, which, color)


def cover(src, cfg, ratio, comparison_roles=None):
    w, h = SIZES[ratio]
    land = w > h
    style = cfg.get('style', 'editorial')
    high_ctr = style == 'high-contrast'
    layouts = cfg.get('layouts', {})
    opts = layouts.get(ratio, {})
    xy = opts.get('subject_at', [.74, .45] if (land and high_ctr) else ([.72, .43] if land else [.53, .58]))
    default_span = max(1, h / w * src.width / src.height) * (1.16 if high_ctr else 1.0)
    span = opts.get('photo_width', default_span)
    im = window(src, (w, h), cfg.get('focus', [.5, .5]), xy, span).convert('RGBA')

    # Smooth directional scrim + subtle peripheral edge framing behind typography.
    mask = Image.new('L', (w, 1) if land else (1, h))
    span_len = mask.width * (.82 if high_ctr else .76) if land else mask.height * (.68 if high_ctr else .62)
    peak_alpha = 218 if high_ctr else 168
    vals = []
    for i in range(max(mask.size)):
        u = max(0.0, 1.0 - i / span_len)
        # Smooth cubic falloff avoids a visible band edge across light photos.
        vals.append(round(peak_alpha * (u * u * (3 - 2 * u))))
    mask.putdata(vals)
    scrim = Image.new('RGBA', (w, h), INK)
    scrim.putalpha(mask.resize((w, h)))
    im.alpha_composite(scrim)

    # Optional high-CTR focal callout ring pointing at the thumbnail's subject hook
    callout_at = opts.get('callout_at', cfg.get('callout_at'))
    if isinstance(callout_at, list) and len(callout_at) == 2:
        cx, cy = round(callout_at[0] * w), round(callout_at[1] * h)
        r = round(min(w, h) * 0.145)
        draw_c = ImageDraw.Draw(im)
        draw_c.ellipse((cx - r, cy - r, cx + r, cy + r), outline=_rgba(AMBER, 245), width=max(6, round(min(w, h) * 0.009)))
        r2 = round(r * 1.15)
        draw_c.ellipse((cx - r2, cy - r2, cx + r2, cy + r2), outline=_rgba(AMBER, 110), width=max(3, round(min(w, h) * 0.004)))

    x = round(w * (.062 if land else .082))
    top = round(h * (.068 if land else .065))
    logo = lockup(round(w * (.052 if land else .095)))
    im.alpha_composite(logo, (x, top))

    lines = opts.get('lines', cfg['lines'])
    if not 1 <= len(lines) <= 3 or any(not l.strip() for l in lines):
        raise ValueError('Each layout needs 1–3 title lines')
    target = w * (.58 if land else .86)
    font_family = 'sans' if high_ctr else 'serif'
    font_size = int(w * ((.124 if high_ctr else .103) if land else (.162 if high_ctr else .142)))
    while max(face(font_family, font_size).getlength(l) for l in lines) > target:
        font_size -= 1
    start = round(h * ((.29 if high_ctr else .33) if land else .214))

    if cfg.get('kicker'):
        k_size = max(20, round(font_size * .185))
        k_y = start - round(font_size * .46)
        bar_w = max(18, round(k_size * 0.9))
        bar_h = max(3, round(k_size * 0.16))
        ImageDraw.Draw(im).rounded_rectangle(
            (x + 2, k_y + round(k_size * 0.44), x + 2 + bar_w, k_y + round(k_size * 0.44) + bar_h),
            radius=bar_h // 2, fill=AMBER,
        )
        _draw_shadow_text(im, (x + bar_w + round(k_size * 0.55), k_y), cfg['kicker'], k_size, 'sans', AMBER)

    line_entities = cfg.get('line_entities', {})
    if not isinstance(line_entities, dict):
        raise ValueError('line_entities maps exact cover lines to comparison entities')
    hi_line = cfg.get('highlight_line')
    stroke_w = max(3, round(font_size * 0.028)) if high_ctr else 0
    for i, line in enumerate(lines):
        if line in line_entities:
            color = comparison_color(comparison_roles, line_entities.get(line))
        elif high_ctr and hi_line is not None and i == hi_line:
            color = AMBER
        else:
            color = PAPER
        _draw_shadow_text(im, (x, start + int(i * font_size * 1.11)), line, font_size, font_family, color, stroke_width=stroke_w)

    badge = opts.get('badge', cfg.get('badge'))
    if isinstance(badge, str) and badge.strip():
        b_text = badge.strip()
        b_size = max(24, round(w * ((.028 if high_ctr else .022) if land else .038)))
        b_font = face('sans', b_size)
        lb = ImageDraw.Draw(im).textbbox((0, 0), b_text, font=b_font)
        bw, bh = lb[2] - lb[0], lb[3] - lb[1]
        px, py = round(b_size * .72), round(b_size * .42)
        pill_w, pill_h = bw + px * 2, bh + py * 2
        bx = x
        by = start + int(len(lines) * font_size * 1.11) + round(font_size * .32)
        if by + pill_h < h - round(h * .04):
            fill_col = _rgba(AMBER, 245) if high_ctr else (19, 26, 23, 210)
            txt_col = INK if high_ctr else PAPER
            ImageDraw.Draw(im).rounded_rectangle(
                (bx, by, bx + pill_w, by + pill_h),
                radius=max(6, pill_h // 5),
                fill=fill_col, outline=PAPER if high_ctr else AMBER, width=max(2, round(b_size * .07)),
            )
            ImageDraw.Draw(im).text((bx + px - lb[0], by + py - lb[1]), b_text, font=b_font, fill=txt_col)
    return im.convert('RGB')


def run(edit, folder='out/covers', *, input_path=Path):
    cover_file = input_path('cover.json')
    cfg = json.loads(cover_file.read_text()) if cover_file.exists() else {}
    title = edit.get('title') or '见秋手记'
    cfg.setdefault('lines', [title] if len(title) < 8 else [title[:(len(title) + 1) // 2], title[(len(title) + 1) // 2:]])
    source_name = cfg.get('image', edit['shots'][0]['visual'])
    if 'image' not in cfg and (assets_file := input_path('assets.json')).exists():
        for entry in json.loads(assets_file.read_text()):
            candidate = Path('assets') / entry['file']
            if candidate.suffix.lower() in {'.jpg', '.jpeg', '.png', '.webp'} and input_path(candidate).is_file():
                source_name = candidate
                break
    source = input_path(source_name)
    with tempfile.TemporaryDirectory() as tmp:
        if source.suffix.lower() not in {'.png', '.jpg', '.jpeg', '.webp'}:
            still = Path(tmp) / 'source.png'
            subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(cfg.get('time', .1)), '-i', str(source),
                            '-frames:v', '1', str(still)], check=True)
            source = still
        with Image.open(source) as f:
            src = ImageOps.exif_transpose(f).convert('RGB')
        if cfg.get('focus') == 'auto':
            try:
                import auto_focus
                cfg = dict(cfg, focus=auto_focus.analyze_media(source)['focal_point'])
            except Exception:
                cfg = dict(cfg, focus=[0.5, 0.5])
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        for ratio in SIZES:
            cover(src, cfg, ratio, edit.get('comparison_roles')).save(folder / (ratio + '.jpg'), quality=95, subsampling=0)


if __name__ == '__main__':
    run(json.loads(Path('edit.json').read_text()))
