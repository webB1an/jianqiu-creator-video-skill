"""Make a frame-timed transparent subtitle overlay for a master or a local edit."""
from collections import defaultdict
from fractions import Fraction
from functools import lru_cache
from pathlib import Path
import re
import shutil
import subprocess

from PIL import Image, ImageDraw, ImageFont

from identity import face

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
    w,h = right-left+pad_x*2, bottom-top+pad_y*2
    im=Image.new('RGBA',(max(w,1),max(h,1)))
    draw=ImageDraw.Draw(im)
    if mode == 'plate':
        draw.rounded_rectangle((0,0,w-1,h-1),radius=max(2,round(size*.25)),fill=(19,27,29,166))
    draw.multiline_text((pad_x-left,pad_y-top),text,font=font,fill=(255,255,252,255),
                        spacing=gap,align='center',stroke_width=outline,stroke_fill=(22,23,24,224))
    return im


def top_caption_ranges(edit, timeline, start_frame, frames):
    """Local, half-open frame ranges for shots whose lower third contains evidence."""
    top_ids = set()
    for shot in edit.get('shots', []):
        position = shot.get('caption_position', 'bottom')
        if position not in ('top', 'bottom'):
            raise ValueError('shots[].caption_position must be top or bottom')
        if position == 'top':
            top_ids.add(shot['id'])
    if not top_ids:
        return []
    ranges = []
    for shot in timeline.get('shots', []):
        if shot['id'] not in top_ids:
            continue
        start = shot['start_frame'] - start_frame
        lo, hi = max(0, start), min(frames, start + shot['frames'])
        if lo < hi:
            if ranges and ranges[-1][1] == lo:
                lo, _ = ranges.pop()
            ranges.append((lo, hi))
    return ranges


def caption_input(folder, srt, width, height, fps, frames, *, start_frame=0, style='plate', font='Heiti SC',
                  top_ranges=()):
    """Return ffmpeg input arguments. The segment shares the source SRT's absolute frame clock."""
    if style not in ('plate','outline'):
        raise ValueError('caption_style must be plate or outline')
    if frames < 1:
        raise ValueError('Empty picture interval')
    # Let libass preserve explicit positioning and inline styles on imported subtitles.
    if re.search(r'<(?:/?(?:b|i|u|s|font)\b)[^>]*>|\{\\[^}]*\}|-->[^\n]+\sX1:\s*\d+',
                 Path(srt).read_text(encoding='utf-8-sig'), re.I):
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
