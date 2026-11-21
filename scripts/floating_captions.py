"""Make a frame-timed transparent subtitle overlay for a master or a local edit."""
from collections import defaultdict
from fractions import Fraction
from functools import lru_cache
from math import ceil
from pathlib import Path
import re
import shutil
import subprocess

from PIL import Image, ImageDraw, ImageFont

from identity import face
from picture_reservations import overlaps, protected_regions

TIMING = re.compile(r'^(\d+:\d\d:\d\d[,.]\d{3})\s*-->\s*(\d+:\d\d:\d\d[,.]\d{3})(?:\s.*)?$')


def seconds(value):
    h, m, s = value.replace(',', '.').split(':')
    return int(h)*3600 + int(m)*60 + Fraction(s)


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
        # SRT rounds exact video frame boundaries to the nearest millisecond.
        # Other boundaries are shown on the first frame at or after their time.
        def tick(t):
            n = t * fps
            near = round(n)
            return near if abs(n-near) <= fps/2000 else -(-n.numerator // n.denominator)
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


def sticker(text, font_name, size, max_width, mode):
    location = font_location(font_name)
    def get_font(px):
        return ImageFont.truetype(location[0], px, index=location[1]) if location else face('sans', px)
    font = get_font(size)
    outline = max(1, round(size * (.05 if mode == 'outline' else .032)))
    pad_x, pad_y = round(size*.38), round(size*.22)
    gap = round(size*.16)
    draw = ImageDraw.Draw(Image.new('L',(1,1)))
    def bounds():
        return draw.multiline_textbbox((0,0),text,font=font,spacing=gap,align='center',stroke_width=outline)
    left,top,right,bottom = bounds()
    if right-left+2*pad_x > max_width:
        font = get_font(max(1,int(size*(max_width-2*pad_x)/(right-left))))
        left,top,right,bottom = bounds()
    # Centered multiline text can have half-pixel ink bounds.
    w,h = ceil(right-left)+pad_x*2, ceil(bottom-top)+pad_y*2
    im=Image.new('RGBA',(max(w,1),max(h,1)))
    draw=ImageDraw.Draw(im)
    if mode == 'plate':
        draw.rounded_rectangle((0,0,w-1,h-1),radius=max(2,round(size*.25)),fill=(19,27,29,166))
    draw.multiline_text((pad_x-left,pad_y-top),text,font=font,fill=(255,255,252,255),
                        spacing=gap,align='center',stroke_width=outline,stroke_fill=(22,23,24,224))
    return im


def _auto_positions(regions, srt, fps, size, font, style, lane):
    """Choose once per uninterrupted cue (including overlapping cues), using actual sticker bounds."""
    width, height = size
    if lane is None:
        left, right, top, bottom, px = 0, width, 0, height, round(height*.045)
        virtual_height = height
    else:
        left, right, top, bottom, px = lane
        virtual_height = round(px/.045)
    margin = max(1, round(virtual_height*.028))
    events = defaultdict(list)
    for i, (a, b, text) in enumerate(cues(Path(srt), Fraction(str(fps)))):
        if a < b:
            events[a].append((i, text))
            events[b].append((i, None))
    @lru_cache(maxsize=128)
    def bounds(phrase):
        im = sticker(phrase, font, max(12,px), round((right-left)*.88), style)
        x = left + (right-left-im.width)//2
        return [(x/width, y/height, (x+im.width)/width, (y+im.height)/height)
                for y in (bottom-margin-im.height, top+margin)]

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
        if not active or i+1 == len(points):
            continue
        if group_start is None:
            group_start = frame
        phrase = '\n'.join(active[k] for k in sorted(active))
        for a, b, box, label in regions:
            if a >= points[i+1] or b <= frame:
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
            regions.extend((begin+a, begin+b, box, label) for a,b,box,label in boxes)

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
            lo = max(start_frame, begin, left)-start_frame
            hi = min(start_frame+frames, end, right)-start_frame
            if lo < hi:
                if result and result[-1][1] == lo:
                    lo, _ = result.pop()
                result.append((lo, hi))
    return result


def _styled_srt(srt):
    return re.search(r'<(?:/?(?:b|i|u|s|font)\b)[^>]*>|\{\\[^}]*\}|-->[^\n]+\sX1:\s*\d+',
                     Path(srt).read_text(encoding='utf-8-sig'), re.I)


def caption_input(folder, srt, width, height, fps, frames, *, start_frame=0, style='plate', font='Heiti SC',
                  top_ranges=()):
    """Return ffmpeg input arguments. The segment shares the source SRT's absolute frame clock."""
    if style not in ('plate','outline'):
        raise ValueError('caption_style must be plate or outline')
    if frames < 1:
        raise ValueError('Empty picture interval')
    # Let libass preserve explicit positioning and inline styles on imported subtitles.
    if _styled_srt(srt):
        return []
    rate = Fraction(str(fps))
    folder = Path(folder)
    directory=folder/'caption-layer'
    directory.mkdir()
    events=defaultdict(list)
    for idx,(first,last,text) in enumerate(cues(Path(srt),rate)):
        lo=max(0,first-start_frame)
        hi=min(frames,last-start_frame)
        if lo<hi:
            events[lo].append((idx,text))
            events[hi].append((idx,None))
    positions = {frame: at_top for lo, hi in top_ranges for frame, at_top in ((lo, True), (hi, False))}
    times=sorted({0,frames,*events,*positions})
    active={}
    spans=[]
    sprites={}
    size=max(12,round(height*.045))
    at_top=False
    for begin,end in zip(times,times[1:]):
        for idx,text in events[begin]:
            if text is None:
                active.pop(idx,None)
            else:
                active[idx]=text
        phrase='\n'.join(active[i] for i in sorted(active))
        if phrase not in sprites:
            sprites[phrase]=sticker(phrase,font,size,round(width*.88),style) if phrase else None
        at_top=positions.get(begin,at_top)
        spans.append(((phrase,at_top),end-begin))
    # Keep only a small transparent canvas under each caption; the picture fills the full frame.
    band_h=max((im.height for im in sprites.values() if im is not None),default=2)
    margin=max(1,round(height*.028))
    if band_h+margin>height:raise ValueError('Too many simultaneous caption lines for the frame')
    index = {key: i for i, key in enumerate(dict.fromkeys(key for key, _ in spans))}
    for (phrase,at_top),i in index.items():
        im=sprites[phrase]
        layer=Image.new('RGBA',(width,band_h+margin))
        if im is not None:layer.alpha_composite(im,((width-im.width)//2,margin if at_top else band_h-im.height))
        layer.save(directory/f'{i}.png')
    manifest=['ffconcat version 1.0']
    for key,count in spans:
        manifest.extend([f"file '{index[key]}.png'",f'option framerate {rate}',
                         f'duration {float(Fraction(count,1)/rate):.9f}'])
    path=directory/'captions.ffconcat'
    path.write_text('\n'.join(manifest)+'\n')
    return ['-f','concat','-safe','0','-i',str(path.resolve())]
