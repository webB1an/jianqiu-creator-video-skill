"""The same brand mark, optional chapter bar and absolute-time subtitles on full renders or shot revisions."""
from fractions import Fraction
import json
from pathlib import Path
from PIL import Image, ImageDraw
from identity import AMBER, INK, PAPER, card_face, lockup
from color_finish import graphics_alpha


def brand_sticker(height):
    icon = lockup(max(25, round(height * .039)), short=True)
    pad = max(4, round(height * .008))
    bug = Image.new('RGBA', (icon.width + pad * 2, icon.height + pad))
    ImageDraw.Draw(bug).rounded_rectangle(
        (0, 0, bug.width - 1, bug.height - 1),
        radius=pad, fill=(19, 26, 23, 168), outline=(244, 237, 221, 38), width=1,
    )
    bug.alpha_composite(icon, (pad, pad // 2))
    return bug


def brand_image(width, height, path):
    brand_sticker(height).save(path)


def resolve_chapters(edit, timeline, screen_plan=None):
    """Resolve ordered chapter segments [(start_frame, end_frame, title)] from edit.chapters or screen_plan sections."""
    total = timeline['frames']
    by_shot = {s['id']: s for s in timeline['shots']}
    raw = edit.get('chapters')
    entries = []
    if isinstance(raw, list) and raw:
        for item in raw:
            if not isinstance(item, dict) or not isinstance(item.get('title'), str) or not item['title'].strip():
                raise ValueError('Each chapter requires a nonempty title and shot or start_frame')
            if 'shot' in item:
                if item['shot'] not in by_shot:
                    raise ValueError(f"Unknown chapter shot: {item['shot']}")
                start = by_shot[item['shot']]['start_frame']
            elif type(item.get('start_frame')) is int:
                start = item['start_frame']
            else:
                raise ValueError('Chapter needs shot or integer start_frame')
            if not 0 <= start < total:
                raise ValueError('Chapter start_frame outside timeline')
            entries.append((start, item['title'].strip()))
    elif isinstance(screen_plan, dict):
        for cut in screen_plan.get('cuts', []):
            sec = cut.get('section')
            if isinstance(sec, str) and sec.strip():
                entries.append((cut['start'], sec.strip()))
    if not entries:
        return []
    entries.sort(key=lambda r: r[0])
    chapters = []
    for i, (start, title) in enumerate(entries):
        end = entries[i + 1][0] if i + 1 < len(entries) else total
        if start < end:
            chapters.append((start, end, title))
    return chapters


def chapter_bar_input(edit, timeline, folder, width, height, fps, *, start_frame=0, frames=None,
                      screen_plan=None, top_ranges=()):
    """Render a minimal editorial top-edge progress hairline + quiet top-left chapter pill."""
    cfg = edit.get('chapter_bar')
    if not cfg:
        return [], None
    if cfg is True:
        cfg = {}
    if not isinstance(cfg, dict):
        raise ValueError('chapter_bar must be boolean or an options object')
    chapters = resolve_chapters(edit, timeline, screen_plan=screen_plan)
    if not chapters:
        return [], None
    total = timeline['frames']
    frames = total if frames is None else frames
    show_title = cfg.get('show_title', True)
    bar_h = max(3, int(cfg.get('height', max(3, round(height * 0.0042)))))
    pill_size = max(14, round(height * 0.022))
    font = card_face('label', pill_size, ''.join(t for _, _, t in chapters))
    pad_x = max(8, round(pill_size * 0.55))
    pad_y = max(4, round(pill_size * 0.30))
    dot_r = max(3, round(pill_size * 0.16))
    pill_x = round(width * 0.042)
    pill_y = round(height * 0.055)
    # Only allocate a slim top strip so compositing is tiny and fast.
    strip_h = pill_y + pill_size + pad_y * 2 + 12 if show_title else bar_h + 2

    directory = Path(folder) / 'chapter-bar'
    directory.mkdir(parents=True, exist_ok=True)
    # Sample progress every 2 frames (~15 updates/sec) — visually fluid hairline with minimal PNGs.
    step = max(1, round(fps / 15))
    rate = Fraction(str(fps))
    manifest = ['ffconcat version 1.0']
    top_set = [(lo, hi) for lo, hi in top_ranges]

    def pill_hidden(local_f):
        return any(lo <= local_f < hi for lo, hi in top_set)

    idx = 0
    cursor = 0
    while cursor < frames:
        span_end = min(frames, cursor + step)
        # Split if chapter or top-caption state changes inside this step.
        prog_f = start_frame + cursor
        title = next((t for a, b, t in chapters if a <= prog_f < b), chapters[0][2])
        hide_pill = pill_hidden(cursor)
        for check in range(cursor + 1, span_end):
            pf = start_frame + check
            t2 = next((t for a, b, t in chapters if a <= pf < b), chapters[0][2])
            if t2 != title or pill_hidden(check) != hide_pill:
                span_end = check
                break
        im = Image.new('RGBA', (width, strip_h))
        draw = ImageDraw.Draw(im)
        # Subtle dark track + Jianqiu amber progress bar along top edge.
        draw.rectangle((0, 0, width - 1, bar_h - 1), fill=(19, 26, 23, 140))
        filled = max(1, round(width * ((prog_f + 0.5) / max(1, total))))
        draw.rectangle((0, 0, min(width - 1, filled), bar_h - 1), fill=(237, 169, 97, 235))
        # Subtle chapter boundary ticks.
        for a, _, _ in chapters[1:]:
            tx = round(width * (a / max(1, total)))
            draw.rectangle((max(0, tx - 1), 0, min(width - 1, tx + 1), bar_h - 1), fill=(19, 26, 23, 240))
        if show_title and not hide_pill:
            lb = draw.textbbox((0, 0), title, font=font)
            tw, th = lb[2] - lb[0], lb[3] - lb[1]
            pw = pad_x * 2 + dot_r * 2 + max(6, round(pill_size * 0.35)) + tw
            ph = max(th + pad_y * 2, pill_size + pad_y * 2)
            rad = max(3, ph // 5)
            draw.rounded_rectangle(
                (pill_x, pill_y, pill_x + pw, pill_y + ph),
                radius=rad, fill=(19, 26, 23, 168), outline=(244, 237, 221, 38), width=1,
            )
            cy = pill_y + ph // 2
            cx = pill_x + pad_x + dot_r
            draw.ellipse((cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r), fill=(237, 169, 97, 255))
            tx = cx + dot_r + max(6, round(pill_size * 0.35)) - lb[0]
            ty = pill_y + (ph - th) // 2 - lb[1]
            draw.text((tx, ty), title, font=font, fill=(244, 237, 221, 245))
        im.save(directory / f'{idx}.png')
        manifest.extend([f"file '{idx}.png'", f'option framerate {rate}',
                         f'duration {float(Fraction(span_end - cursor, 1) / rate):.9f}'])
        idx += 1
        cursor = span_end

    concat = directory / 'chapters.ffconcat'
    concat.write_text('\n'.join(manifest) + '\n')
    return ['-f', 'concat', '-safe', '0', '-i', str(concat.resolve())], chapters


def caption_y(top_ranges, fps, *, top='0', bottom='H-h'):
    if not top_ranges:
        return bottom
    active = '+'.join(f'between(t*{fps},{lo - .5},{hi - .5})' for lo, hi in top_ranges)
    return f"'if({active},{top},{bottom})'"


def overlay_chain(width, height, font, *, brand_index, caption_index=None, start_frame=0, fps=30,
                  source_filter=None, top_ranges=(), montage_pins=(), chapter_index=None):
    style = f'FontName={font},FontSize=13,Outline=1.1,Shadow=0,MarginV=16,Alignment=2'
    layers = f'[{brand_index}:v]{graphics_alpha()}[mark];'
    base = '[0:v]'
    if source_filter:
        layers += f'[0:v]{source_filter},format=yuv420p[base];'
        base = '[base]'
    for index, x, y, lo, hi in montage_pins:
        layers += (f'[{index}:v]{graphics_alpha()}[pin{index}];'
                   f"{base}[pin{index}]overlay={x}:{y}:enable='between(n,{lo},{hi - 1})'[pinned{index}];")
        base = f'[pinned{index}]'
    if chapter_index is not None:
        layers += (f'[{chapter_index}:v]{graphics_alpha()}[chap];'
                   f'{base}[chap]overlay=0:0[chapped];')
        base = '[chapped]'
    picture = f'{base}[mark]overlay=W-w-{round(width * .042)}:{round(height * .055)}'
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
