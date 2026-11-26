"""Make a frame-timed transparent subtitle overlay for a master or a local edit."""
from collections import defaultdict
from fractions import Fraction
from functools import lru_cache
from math import ceil
from pathlib import Path
import re
import shutil
import subprocess

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from identity import face
from picture_reservations import overlaps, protected_regions
from prosody_markup import parse_prosody_markup

TIMING = re.compile(r'^(\d+:\d\d:\d\d[,.]\d{3})\s*-->\s*(\d+:\d\d:\d\d[,.]\d{3})(?:\s.*)?$')
STYLES = ('plate', 'outline', 'editorial', 'clean-shadow', 'kinetic-word', 'adaptive-kinetic')
SHOT_STYLES = ('plate', 'outline', 'editorial', 'clean-shadow', 'kinetic-word')
ACCENT_RGBA = (237, 169, 97, 255)      # Jianqiu amber #EDA961
PAPER_RGBA = (244, 237, 221, 255)      # Jianqiu paper #F4EDDD
INK_PLATE_RGBA = (19, 26, 23, 210)     # Jianqiu ink #131A17 frosted
INK_BORDER_RGBA = (244, 237, 221, 52)  # Subtle warm hairline


def seconds(value):
    h, m, s = value.replace(',', '.').split(':')
    return int(h) * 3600 + int(m) * 60 + Fraction(s)


def cues(path, fps):
    content = path.read_text(encoding='utf-8-sig').strip()
    for block in re.split(r'\n[ \t]*\n', content) if content else []:
        lines = block.splitlines()
        if re.fullmatch(r'\d+', lines[0].strip()):
            lines = lines[1:]
        match = TIMING.fullmatch(lines[0].strip())
        if not match:
            raise ValueError(f'Unreadable subtitle timing in {path}: {lines[0]}')
        start, end = (seconds(x) for x in match.groups())
        if end <= start:
            raise ValueError(f'Invalid subtitle duration: {match.group(0)}')

        def tick(t):
            n = t * fps
            near = round(n)
            return near if abs(n - near) <= fps / 2000 else -(-n.numerator // n.denominator)

        if lines[1:]:
            yield tick(start), tick(end), '\n'.join(lines[1:])


@lru_cache(maxsize=32)
def font_location(name):
    if Path(name).is_file():
        return name, 0
    if shutil.which('fc-match'):
        result = subprocess.check_output(['fc-match', '-f', '%{file}\n%{index}', name], text=True)
        path, _, index = result.partition('\n')
        if Path(path).is_file():
            return path, int(index or 0)
    return None


def _line_highlights(line):
    terms = list(line.get('highlights') or [])
    for field in ('text', 'spoken'):
        terms.extend(parse_prosody_markup(line.get(field))['highlights'])
    return terms


def collect_highlights(edit):
    """Collect explicit keyword highlights from edit.caption_highlights and shots[].lines[].highlights."""
    if not isinstance(edit, dict):
        return ()
    terms = []
    for item in edit.get('caption_highlights', []) or []:
        if isinstance(item, str) and item.strip():
            terms.append(item.strip())
    for shot in edit.get('shots', []):
        for line in shot.get('lines', []):
            for item in _line_highlights(line):
                if isinstance(item, str) and item.strip():
                    terms.append(item.strip())
    # Longest first so compound highlights take precedence over substrings.
    return tuple(sorted(dict.fromkeys(terms), key=len, reverse=True))


def _highlight_mask(line_text, highlights, active_span):
    """Return per-character boolean mask (highlighted) for one displayed line."""
    flags = [False] * len(line_text)
    if active_span is not None:
        lo, hi = active_span
        for i in range(max(0, lo), min(len(line_text), hi)):
            flags[i] = True
    for term in highlights:
        if not term:
            continue
        start = 0
        while True:
            pos = line_text.find(term, start)
            if pos < 0:
                break
            for i in range(pos, pos + len(term)):
                flags[i] = True
            start = pos + len(term)
    return flags


def _draw_rich_line(draw, origin_x, origin_y, line_text, font, outline, base_fill, stroke_fill,
                    highlights, active_span, accent_fill):
    flags = _highlight_mask(line_text, highlights, active_span)
    if not any(flags):
        draw.text((origin_x, origin_y), line_text, font=font, fill=base_fill,
                  stroke_width=outline, stroke_fill=stroke_fill)
        return
    # Group contiguous runs with identical color so kerning inside each word stays intact.
    runs = []
    idx = 0
    while idx < len(line_text):
        state = flags[idx]
        j = idx + 1
        while j < len(line_text) and flags[j] == state:
            j += 1
        runs.append((line_text[idx:j], state))
        idx = j
    prefix = ''
    for seg, is_hi in runs:
        x = origin_x + (font.getlength(prefix) if prefix else 0)
        fill = accent_fill if is_hi else base_fill
        draw.text((x, origin_y), seg, font=font, fill=fill,
                  stroke_width=outline, stroke_fill=stroke_fill)
        prefix += seg


def sticker(text, font_name, size, max_width, mode, *, highlights=(), active_span=None, accent=ACCENT_RGBA):
    location = font_location(font_name)

    def get_font(px):
        return ImageFont.truetype(location[0], px, index=location[1]) if location else face('sans', px)

    font = get_font(size)
    if mode == 'outline':
        outline = max(1, round(size * .05))
    elif mode == 'clean-shadow':
        outline = max(1, round(size * .026))
    elif mode in ('editorial', 'kinetic-word'):
        outline = max(1, round(size * .018))
    else:
        outline = max(1, round(size * .032))

    pad_x = round(size * (.48 if mode in ('editorial', 'kinetic-word') else .38))
    pad_y = round(size * (.28 if mode in ('editorial', 'kinetic-word') else .22))
    shadow_pad = round(size * .22) if mode in ('editorial', 'kinetic-word', 'clean-shadow') else 0
    gap = round(size * .18)
    measure = ImageDraw.Draw(Image.new('L', (1, 1)))

    def bounds():
        return measure.multiline_textbbox((0, 0), text, font=font, spacing=gap, align='center', stroke_width=outline)

    left, top, right, bottom = bounds()
    if right - left + 2 * (pad_x + shadow_pad) > max_width:
        avail = max(1, max_width - 2 * (pad_x + shadow_pad))
        font = get_font(max(1, int(size * avail / max(1, right - left))))
        left, top, right, bottom = bounds()

    box_w = ceil(right - left) + pad_x * 2
    box_h = ceil(bottom - top) + pad_y * 2
    w = max(1, box_w + shadow_pad * 2)
    h = max(1, box_h + shadow_pad * 2)
    im = Image.new('RGBA', (w, h))

    if mode == 'plate':
        draw = ImageDraw.Draw(im)
        draw.rounded_rectangle((0, 0, w - 1, h - 1), radius=max(2, round(size * .25)), fill=(19, 27, 29, 166))
    elif mode in ('editorial', 'kinetic-word'):
        radius = max(3, round(size * .28))
        # Soft elevation shadow behind the frosted editorial pill.
        sh_mask = Image.new('L', (w, h), 0)
        ImageDraw.Draw(sh_mask).rounded_rectangle(
            (shadow_pad, shadow_pad + max(2, round(size * .06)),
             shadow_pad + box_w - 1, shadow_pad + box_h - 1 + max(2, round(size * .06))),
            radius=radius, fill=125)
        sh_mask = sh_mask.filter(ImageFilter.GaussianBlur(radius=max(2, round(size * .11))))
        shade = Image.new('RGBA', (w, h), (8, 12, 10, 0))
        shade.putalpha(sh_mask)
        im.alpha_composite(shade)
        draw = ImageDraw.Draw(im)
        draw.rounded_rectangle(
            (shadow_pad, shadow_pad, shadow_pad + box_w - 1, shadow_pad + box_h - 1),
            radius=radius, fill=INK_PLATE_RGBA, outline=INK_BORDER_RGBA, width=max(1, round(size * .022)))
    elif mode == 'clean-shadow':
        # Layered soft shadow directly behind borderless editorial typography.
        sh_layer = Image.new('RGBA', (w, h))
        sh_draw = ImageDraw.Draw(sh_layer)
        sh_draw.multiline_text((shadow_pad + pad_x - left, shadow_pad + pad_y - top + max(2, round(size * .05))),
                               text, font=font, fill=(10, 14, 12, 225), spacing=gap, align='center',
                               stroke_width=outline + max(1, round(size * .03)), stroke_fill=(10, 14, 12, 225))
        sh_layer = sh_layer.filter(ImageFilter.GaussianBlur(radius=max(2, round(size * .10))))
        im.alpha_composite(sh_layer)
        draw = ImageDraw.Draw(im)
    else:
        draw = ImageDraw.Draw(im)

    base_fill = PAPER_RGBA if mode in ('editorial', 'kinetic-word', 'clean-shadow') else (255, 255, 252, 255)
    stroke_fill = (16, 20, 18, 220) if mode in ('editorial', 'kinetic-word', 'clean-shadow') else (22, 23, 24, 224)
    has_rich = bool(highlights) or active_span is not None
    if not has_rich:
        draw.multiline_text((shadow_pad + pad_x - left, shadow_pad + pad_y - top), text, font=font,
                            fill=base_fill, spacing=gap, align='center',
                            stroke_width=outline, stroke_fill=stroke_fill)
    else:
        lines = text.split('\n')
        # Compute per-line y offsets consistent with Pillow's multiline layout.
        total_w = right - left
        cursor_y = shadow_pad + pad_y - top
        char_offset = 0
        line_h = font.getbbox('国Ag')[3] - font.getbbox('国Ag')[1]
        for line in lines:
            lb = measure.textbbox((0, 0), line, font=font, stroke_width=outline)
            lw = lb[2] - lb[0]
            lx = shadow_pad + pad_x + (total_w - lw) / 2 - lb[0]
            local_span = None
            if active_span is not None:
                a, b = active_span[0] - char_offset, active_span[1] - char_offset
                if b > 0 and a < len(line):
                    local_span = (max(0, a), min(len(line), b))
            _draw_rich_line(draw, lx, cursor_y, line, font, outline, base_fill, stroke_fill,
                            highlights, local_span, accent)
            cursor_y += max(line_h, lb[3] - lb[1]) + gap
            char_offset += len(line) + 1
    return im


def _kinetic_spans_for_cue(phrase, first_frame, last_frame, fps, timeline):
    """Map word-clock character timestamps onto phrase token chunks for kinetic-word highlighting."""
    beats = (timeline or {}).get('beats') or []
    if not beats or '\n' in phrase:
        return None
    norm = lambda s: re.sub(r'\s+', '', s).casefold()
    cue_start_s = float(first_frame / fps)
    cue_end_s = float(last_frame / fps)
    # Gather characters from beats overlapping this cue's time interval.
    chars = []
    for beat in beats:
        if beat.get('end', 0) < cue_start_s - 0.15 or beat.get('start', 0) > cue_end_s + 0.15:
            continue
        for ch, sec in beat.get('chars', []):
            if cue_start_s - 0.12 <= sec <= cue_end_s + 0.08:
                chars.append((ch, sec))
    if not chars:
        return None
    # Split phrase into readable visual tokens (ASCII words/numbers or 2-3 char Chinese groups).
    raw_tokens = []
    for m in re.finditer(r'[A-Za-z0-9]+(?:[.,:/-][A-Za-z0-9]+)*|[\u3400-\u9fff]{1,3}|[^\s]', phrase):
        raw_tokens.append((m.start(), m.end(), m.group(0)))
    if len(raw_tokens) < 2:
        return None
    # Match each token's first alphanumeric/CJK symbol sequentially against beat chars.
    joined_chars = [c.casefold() for c, _ in chars]
    cursor = 0
    timed = []
    for lo, hi, tok in raw_tokens:
        clean = norm(tok)
        if not clean:
            continue
        found = None
        for probe in range(cursor, min(len(joined_chars), cursor + 8)):
            if joined_chars[probe] == clean[0]:
                found = probe
                break
        if found is not None:
            frame = max(first_frame, min(last_frame - 1, round(chars[found][1] * float(fps))))
            timed.append((frame, (lo, hi)))
            cursor = found + len(clean)
    if len(timed) < 2:
        return None
    # Ensure strictly increasing frame boundaries.
    segments = []
    for i, (f0, span) in enumerate(timed):
        a = first_frame if i == 0 else max(segments[-1][1], f0)
        b = last_frame if i + 1 == len(timed) else max(a + 1, min(last_frame, timed[i + 1][0]))
        if a < b:
            segments.append((a, b, span))
    if not segments:
        return None
    if segments[-1][1] < last_frame:
        a, _, span = segments[-1]
        segments[-1] = (a, last_frame, span)
    return segments


def _shot_wants_kinetic(shot, timing, fps):
    """Decide whether a shot should use kinetic-word word-clock highlighting in adaptive-kinetic mode."""
    if timing['start_frame'] < round(6.0 * fps):
        return True
    if shot.get('hook') or shot.get('emphasis') or shot.get('rehook'):
        return True
    rem = shot.get('remotion_effect')
    if isinstance(rem, dict) and rem.get('effect') in ('kinetic-hook', 'quote-card'):
        return True
    for line in shot.get('lines', []):
        if line.get('emphasis') or _line_highlights(line):
            return True
    return False


def kinetic_caption_ranges(edit, timeline, start_frame=0, frames=None):
    """Resolve per-shot caption styles clipped to [start_frame, start_frame + frames).

    Supports edit.caption_style == 'adaptive-kinetic' (auto kinetic-word on opening 0–6s hook,
    emphasis/rehook shots and key-quote shots; crisp editorial elsewhere) and per-shot
    shots[].caption_style overrides.
    """
    base_style = edit.get('caption_style', 'plate')
    if base_style not in STYLES:
        raise ValueError(f"caption_style must be one of {', '.join(STYLES)}")
    fps = timeline['fps']
    total = timeline['frames'] if frames is None else frames
    shots_by_id = {s['id']: s for s in edit.get('shots', [])}
    ranges = []
    for timing in timeline.get('shots', []):
        shot = shots_by_id.get(timing['id'], {})
        override = shot.get('caption_style')
        if override is not None:
            if override not in SHOT_STYLES:
                raise ValueError(f"shots[].caption_style must be one of {', '.join(SHOT_STYLES)}")
            mode = override
        elif base_style == 'adaptive-kinetic':
            mode = 'kinetic-word' if _shot_wants_kinetic(shot, timing, fps) else 'editorial'
        else:
            mode = base_style
        lo = max(start_frame, timing['start_frame']) - start_frame
        hi = min(start_frame + total, timing['start_frame'] + timing['frames']) - start_frame
        if lo < hi:
            if ranges and ranges[-1][1] == lo and ranges[-1][2] == mode:
                prev_lo, _, _ = ranges.pop()
                ranges.append((prev_lo, hi, mode))
            else:
                ranges.append((lo, hi, mode))
    return ranges


def _auto_positions(regions, srt, fps, size, font, style, lane):
    """Choose once per uninterrupted cue (including overlapping cues), using actual sticker bounds."""
    measure_style = 'editorial' if style == 'adaptive-kinetic' else style
    width, height = size
    if lane is None:
        left, right, top, bottom, px = 0, width, 0, height, round(height * .045)
        virtual_height = height
    else:
        left, right, top, bottom, px = lane
        virtual_height = round(px / .045)
    margin = max(1, round(virtual_height * .028))
    events = defaultdict(list)
    for i, (a, b, text) in enumerate(cues(Path(srt), Fraction(str(fps)))):
        if a < b:
            events[a].append((i, text))
            events[b].append((i, None))

    @lru_cache(maxsize=128)
    def bounds(phrase):
        im = sticker(phrase, font, max(12, px), round((right - left) * .88), measure_style)
        x = left + (right - left - im.width) // 2
        return [(x / width, y / height, (x + im.width) / width, (y + im.height) / height)
                for y in (bottom - margin - im.height, top + margin)]

    active, blocked, group_start = {}, [set(), set()], None
    ranges = []
    points = sorted(events)
    for i, frame in enumerate(points):
        for idx, text in events[frame]:
            if text is None:
                active.pop(idx, None)
        if not active and group_start is not None:
            if blocked[0] and blocked[1]:
                labels = '、'.join(sorted(blocked[0] | blocked[1]))
                raise ValueError(f'字幕 F{group_start}–{frame} 的上下位置均与「{labels}」重合。'
                                 '请调整包装出场时机、保护区或指定本镜 caption_position。')
            if blocked[0]:
                ranges.append((group_start, frame))
            group_start, blocked = None, [set(), set()]
        for idx, text in events[frame]:
            if text is not None:
                active[idx] = text
        if not active or i + 1 == len(points):
            continue
        if group_start is None:
            group_start = frame
        phrase = '\n'.join(active[k] for k in sorted(active))
        for a, b, box, label in regions:
            if a >= points[i + 1] or b <= frame:
                continue
            for index, caption_box in enumerate(bounds(phrase)):
                if overlaps(caption_box, box):
                    blocked[index].add(label)
    return ranges


def top_caption_ranges(edit, timeline, start_frame, frames, *, srt=None, size=None, portrait_lane=None):
    """Compute positions on the full programme, then clip to the requested local window."""
    shots = {shot['id']: shot for shot in edit.get('shots', [])}
    planned, regions = [], []
    for timing in timeline.get('shots', []):
        shot = shots.get(timing['id'], {})
        position = shot.get('caption_position')
        if position is not None and position not in ('top', 'bottom'):
            raise ValueError('shots[].caption_position must be top or bottom')
        begin = timing['start_frame']
        planned.append((begin, begin + timing['frames'], position))
        boxes = list(protected_regions(shot, timing['frames']))
        if position is None:
            settings = shot.get('remotion_effect', {})
            if portrait_lane is None and 'placement' in settings:
                from remotion_library import placement_record, project_params
                location = placement_record(settings, project_params(shot, timeline, timing))
                a, b = location['frames']
                boxes.append((a, b, location['box'], '人物身份条'))
            regions.extend((begin + a, begin + b, box, label) for a, b, box, label in boxes)

    auto = []
    if regions:
        if srt is None or size is None:
            raise ValueError('Timed caption reservations require the SRT and output size')
        if not _styled_srt(srt):
            auto = _auto_positions(regions, srt, timeline['fps'], size, edit.get('font', 'Heiti SC'),
                                   edit.get('caption_style', 'plate'), portrait_lane)
    result = []
    for begin, end, position in planned:
        intervals = [(begin, end)] if position == 'top' else auto if position is None else []
        for left, right in intervals:
            lo = max(start_frame, begin, left) - start_frame
            hi = min(start_frame + frames, end, right) - start_frame
            if lo < hi:
                if result and result[-1][1] == lo:
                    lo, _ = result.pop()
                result.append((lo, hi))
    return result


def _styled_srt(srt):
    return re.search(r'<(?:/?(?:b|i|u|s|font)\b)[^>]*>|\{\\[^}]*\}|-->[^\n]+\sX1:\s*\d+',
                     Path(srt).read_text(encoding='utf-8-sig'), re.I)


def _fade_sprite(im, alpha_scale):
    if alpha_scale >= 1.0:
        return im
    out = im.copy()
    out.putalpha(out.getchannel('A').point(lambda a: round(a * alpha_scale)))
    return out


def caption_input(folder, srt, width, height, fps, frames, *, start_frame=0, style='plate', font='Heiti SC',
                  top_ranges=(), timeline=None, highlights=(), style_ranges=(), rendered_regions=None):
    """Return ffmpeg input arguments. The segment shares the source SRT's absolute frame clock."""
    if style not in STYLES:
        raise ValueError(f"caption_style must be one of {', '.join(STYLES)}")
    if frames < 1:
        raise ValueError('Empty picture interval')
    if _styled_srt(srt):
        return []
    rate = Fraction(str(fps))
    folder = Path(folder)
    directory = folder / 'caption-layer'
    directory.mkdir()
    events = defaultdict(list)
    for idx, (first, last, text) in enumerate(cues(Path(srt), rate)):
        lo = max(0, first - start_frame)
        hi = min(frames, last - start_frame)
        if lo < hi:
            events[lo].append((idx, text))
            events[hi].append((idx, None))
    positions = {frame: at_top for lo, hi in top_ranges for frame, at_top in ((lo, True), (hi, False))}
    times = sorted({0, frames, *events, *positions})
    active = {}
    raw_spans = []
    size = max(12, round(height * .045))
    at_top = False
    for begin, end in zip(times, times[1:]):
        for idx, text in events[begin]:
            if text is None:
                active.pop(idx, None)
            else:
                active[idx] = text
        phrase = '\n'.join(active[i] for i in sorted(active))
        at_top = positions.get(begin, at_top)
        raw_spans.append((begin, end, phrase, at_top))

    def resolve_mode(begin, end):
        mid = (begin + end) // 2
        for r_lo, r_hi, r_mode in style_ranges or ():
            if r_lo <= mid < r_hi:
                return r_mode
        if style == 'adaptive-kinetic':
            return 'kinetic-word' if (begin + start_frame) < round(6.0 * float(rate)) else 'editorial'
        return style

    # Expand spans for subtle entrance polish and per-shot kinetic-word active highlights.
    any_animated = style in ('editorial', 'clean-shadow', 'kinetic-word', 'adaptive-kinetic') or any(
        m in ('editorial', 'clean-shadow', 'kinetic-word') for _, _, m in (style_ranges or ())
    )
    rise_px = max(2, round(size * .14))
    spans = []  # list of ((phrase, at_top, mode, active_span, entry_step), frame_count)
    sprites = {}

    def get_sprite(phrase, mode, active_span):
        key = (phrase, mode, active_span)
        if key not in sprites:
            sprites[key] = sticker(phrase, font, size, round(width * .88), mode,
                                   highlights=highlights, active_span=active_span) if phrase else None
        return sprites[key]

    prev_phrase = ''
    for begin, end, phrase, at_top in raw_spans:
        dur = end - begin
        mode = resolve_mode(begin, end)
        animate_entry = mode in ('editorial', 'clean-shadow', 'kinetic-word')
        if not phrase:
            spans.append((('', at_top, mode, None, 0), dur))
            prev_phrase = ''
            continue
        is_fresh = phrase != prev_phrase
        prev_phrase = phrase
        kinetic = (_kinetic_spans_for_cue(phrase, begin + start_frame, end + start_frame, rate, timeline)
                   if mode == 'kinetic-word' else None)
        if kinetic:
            for k_idx, (ka, kb, act_span) in enumerate(kinetic):
                sub_dur = kb - ka
                get_sprite(phrase, mode, act_span)
                if k_idx == 0 and is_fresh and sub_dur >= 6:
                    spans.append(((phrase, at_top, mode, act_span, 1), 1))
                    spans.append(((phrase, at_top, mode, act_span, 2), 1))
                    spans.append(((phrase, at_top, mode, act_span, 0), sub_dur - 2))
                else:
                    spans.append(((phrase, at_top, mode, act_span, 0), sub_dur))
        else:
            get_sprite(phrase, mode, None)
            if animate_entry and is_fresh and dur >= 7:
                spans.append(((phrase, at_top, mode, None, 1), 1))
                spans.append(((phrase, at_top, mode, None, 2), 1))
                spans.append(((phrase, at_top, mode, None, 0), dur - 2))
            else:
                spans.append(((phrase, at_top, mode, None, 0), dur))

    band_h = max((im.height for im in sprites.values() if im is not None), default=2) + (rise_px if any_animated else 0)
    margin = max(1, round(height * .028))
    if band_h + margin > height:
        raise ValueError('Too many simultaneous caption lines for the frame')
    index = {key: i for i, key in enumerate(dict.fromkeys(key for key, _ in spans))}
    boxes = {}
    for (phrase, at_top, mode, act_span, step), i in index.items():
        im = sprites.get((phrase, mode, act_span))
        layer = Image.new('RGBA', (width, band_h + margin))
        if im is not None:
            dy = 0
            if step == 1:
                im = _fade_sprite(im, 0.42)
                dy = rise_px
            elif step == 2:
                im = _fade_sprite(im, 0.84)
                dy = max(1, rise_px // 3)
            base_y = (margin - dy) if at_top else (band_h - im.height + dy)
            base_y = max(0, min(layer.height - im.height, base_y))
            layer.alpha_composite(im, ((width - im.width) // 2, base_y))
        if rendered_regions is not None and im is not None:
            box = layer.getchannel('A').getbbox()
            if box:
                y = 0 if at_top else height - layer.height
                boxes[i] = [round(x, 6) for x in (box[0]/width, (y+box[1])/height,
                                                    box[2]/width, (y+box[3])/height)]
        layer.save(directory / f'{i}.png')
    manifest = ['ffconcat version 1.0']
    frame = start_frame
    for key, count in spans:
        if rendered_regions is not None and index[key] in boxes:
            box = boxes[index[key]]
            if rendered_regions and rendered_regions[-1]['end_frame'] == frame and rendered_regions[-1]['box'] == box:
                rendered_regions[-1]['end_frame'] += count
            else:
                rendered_regions.append(dict(start_frame=frame, end_frame=frame + count, box=box))
        frame += count
        manifest.extend([f"file '{index[key]}.png'", f'option framerate {rate}',
                         f'duration {float(Fraction(count, 1) / rate):.9f}'])
    path = directory / 'captions.ffconcat'
    path.write_text('\n'.join(manifest) + '\n')
    return ['-f', 'concat', '-safe', '0', '-i', str(path.resolve())]
