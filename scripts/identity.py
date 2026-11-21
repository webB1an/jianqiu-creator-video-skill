"""Jianqiu brand marks, card typography and colors."""
import json
import shutil
import subprocess
import unicodedata
from functools import lru_cache
from pathlib import Path
from PIL import Image,ImageDraw,ImageFont

ROOT=Path(__file__).resolve().parents[1]/'assets'/'brand'
STYLE=json.loads((ROOT/'style.json').read_text())
PAPER,AMBER,INK=(STYLE[k] for k in ('paper','amber','ink'))
# Two planes share a vein of negative space and a sharp apex.
SHAPES=[
 [(9,82),((8,38),(35,12),(89,8)),((71,23),(34,50),(9,82))],
 [(18,85),((34,61),(60,35),(91,13)),((90,59),(65,86),(18,85))]
]
@lru_cache(None)
def face(which,size,index=0):
    path = STYLE['latin_font' if which == 'latin' else which]
    for candidate_index in dict.fromkeys((index, 0)):
        try:
            return ImageFont.truetype(path, int(size), index=candidate_index)
        except OSError:
            pass
    return _fallback_face(which, int(size))


@lru_cache(maxsize=512)
def _has_glyph(font, char):
    glyph, missing = font.getmask(char), font.getmask('\U0010ffff')
    return (glyph.size, bytes(glyph)) != (missing.size, bytes(missing))


@lru_cache(None)
def _fallback_face(which, size):
    family = {'serif': 'Songti SC,Noto Serif CJK SC:style=Bold',
              'sans': 'Heiti SC,Noto Sans CJK SC',
              'latin': 'Avenir Next,DejaVu Sans:style=Demi Bold'}[which]
    candidates = []
    if shutil.which('fc-match'):
        pattern = family if which == 'latin' else family + ':lang=zh-cn:charset=79cb'
        match = subprocess.run(['fc-match', '-f', '%{file}\n%{index}', pattern], capture_output=True, text=True)
        parts = match.stdout.strip().splitlines()
        if len(parts) == 2 and parts[1].isdigit():
            candidates.append((parts[0], int(parts[1])))
    if which == 'latin':
        candidates.append(('DejaVuSans.ttf', 0))
    for path, index in candidates:
        try:
            font = ImageFont.truetype(path, size, index=index)
            if which == 'latin' or _has_glyph(font, '秋'):
                return font
        except OSError:
            continue
    raise OSError(f'未找到可用的 {which} 字体。请在 {ROOT / "style.json"} 设置可加载的字体文件。')


def card_face(role, size, text=''):
    """Choose a card font at final canvas size. Pass the displayed text for mixed units."""
    which, index = STYLE['card_faces'][role]
    if role == 'metric' and any(unicodedata.east_asian_width(c) in ('W', 'F') for c in text):
        which, index = STYLE['card_faces']['label']
    font = face(which, size, index)
    if which == 'latin' and any(not _has_glyph(font, c) for c in set(text) if not c.isascii()):
        which, index = STYLE['card_faces']['label']
        return face(which, size, index)
    return font


def card_colors(surface='dark'):
    """Resolve the five card color roles against the current brand palette."""
    return {role: STYLE.get(color, color) for role, color in STYLE['card_colors'][surface].items()}


def comparison_color(roles, entity=None, surface='dark'):
    """Resolve a project entity on the chosen surface. An unassigned label stays neutral."""
    palette = card_colors(surface)
    if entity is None:
        return palette['text']
    if not isinstance(entity, str) or not isinstance(roles, dict) or entity not in roles:
        raise ValueError(f'Unknown comparison entity {entity!r} in edit.json comparison_roles')
    role = roles[entity]
    if role not in ('text', 'muted', 'accent', 'compare'):
        raise ValueError(f'Choose text, muted, accent or compare for comparison entity {entity!r}')
    return palette[role]

@lru_cache(None)
def mark(size,color=AMBER):
    side=size*3
    im=Image.new('RGBA',(side,side))
    for poly in SHAPES:
        points=[poly[0]];p0=poly[0]
        for p1,p2,p3 in poly[1:]:
            for i in range(1,33):
                t=i/32;z=1-t
                points.append(tuple(z**3*p0[d]+3*z*z*t*p1[d]+3*z*t*t*p2[d]+t**3*p3[d] for d in range(2)))
            p0=p3
        ImageDraw.Draw(im).polygon([(round(x*side/100),round(y*side/100)) for x,y in points],fill=color)
    return im.resize((size,size),Image.Resampling.LANCZOS)

def put(im,xy,t,size,which='sans',color=PAPER,index=0,anchor='lt'):
    ImageDraw.Draw(im).text(xy,t,font=face(which,size,index),fill=color,anchor=anchor)

def lockup(height,short=False,color=PAPER):
    name=STYLE['short' if short else 'name'];size=round(height*.63);icon=height
    wid=round(icon*.99+face('sans',size).getlength(name))+4
    im=Image.new('RGBA',(wid,height))
    im.alpha_composite(mark(icon),(0,0))
    put(im,(round(icon*.99),round(height*.48)),name,size,color=color,anchor='lm')
    return im

def assets():
    doc=['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">']
    for shape in SHAPES:
        x,y=shape[0]
        path=f'M{x},{y} '+ ' '.join('C'+' '.join(f'{a},{b}' for a,b in seg) for seg in shape[1:])+' Z'
        doc.append(f'<path fill="{AMBER}" d="{path}"/>')
    (ROOT/'leaf.svg').write_text(''.join(doc)+'</svg>')
    lockup(160).save(ROOT/'signature.png')
    lockup(160,color=INK).save(ROOT/'signature-on-paper.png')
    lockup(96,True).save(ROOT/'bug.png')
    im=Image.new('RGB',(1024,1024),INK);m=mark(490)
    im.paste(m,(267,128),m);put(im,(512,674),STYLE['short'],165,'serif',PAPER,0,'mt')
    im.save(ROOT/'avatar.png')
if __name__=='__main__':assets()
