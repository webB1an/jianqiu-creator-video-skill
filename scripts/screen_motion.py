"""Sample a continuous camera on an authentic browser plate and smoothly reveal the original selected pixels."""
import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile

from PIL import Image, ImageChops, ImageColor


def smooth(t):
    t=min(1.,max(0.,t))
    return t*t*(3-2*t)

def interp(a,b,t):return a+(b-a)*t

def ease_frames(frame, start, end):
    if end <= start:
        raise ValueError("End frame must follow start")
    return smooth((frame-start)/(end-start))


def css_box(rect, clip, d):
    return ((rect['x']-clip['x'])*d,(rect['y']-clip['y'])*d,
            (rect['x']+rect['w']-clip['x'])*d,(rect['y']+rect['h']-clip['y'])*d)


def sample_view(im, clip, dpr, cx, cy, width, size=(1920,1080)):
    """Return an RGB view and its CSS (left, top, width). Keep subpixel precision to final resampling."""
    if min(size) <= 0 or width <= 0 or dpr <= 0:
        raise ValueError("Positive camera and image sizes required")
    d=dpr;x=cx;y=cy
    h=width*size[1]/size[0]
    if width > clip['width']+1e-5 or h > clip['height']+1e-5:
        raise ValueError("Requested camera is larger than captured source")
    x=max(width/2+clip['x'],min(clip['x']+clip['width']-width/2,x))
    y=max(h/2+clip['y'],min(clip['y']+clip['height']-h/2,y))
    left=(x-width/2-clip['x'])*d
    top=(y-h/2-clip['y'])*d
    box=(left,top,left+width*d,top+h*d)
    out=im.resize(size, Image.Resampling.BICUBIC,box)
    return out,(x-width/2,y-h/2,width)


def reveal_selection(clean, selected, lines, clip, dpr, progress):
    """Reveal actual matching selected pixels along one or several DOM rects in reading order."""
    if clean.size != selected.size or min(clean.size) <= 0 or not lines:
        raise ValueError('Matched source plates and nonempty DOM text bounds required')
    if progress<=0:return clean
    # Crop only the actual browser-selected ink. Prevent unrelated webpage changes from
    # entering the motion. Line order follows DOM Range.getClientRects().
    d=dpr
    result=clean.copy()
    boxes=[css_box(line,clip,d) for line in lines]
    length=sum(b[2]-b[0] for b in boxes)
    remain=progress*length
    for l,t,r,b in boxes:
        w=r-l
        if remain<=0:break
        l0,t0,r0,b0=round(l)-3,round(t)-4,round(r)+3,round(b)+4
        active=min(remain,w)
        feather=2.5*d
        boundary=active+3
        maskrow=Image.new('L',(r0-l0,1))
        maskrow.putdata([round(255*max(0,min(1,(boundary-i)/feather))) for i in range(r0-l0)] if active<w else [255]*(r0-l0))
        patch=selected.crop((l0,t0,r0,b0))
        result.paste(patch,(l0,t0),maskrow.resize(patch.size))
        remain-=w
    return result


def reveal_ink(clean, tinted, boxes, progress):
    """Sweep a translucent marker over source-relative glyph runs in reading order."""
    if progress <= 0:
        return clean
    remaining = progress * sum(right-left for left, _, right, _ in boxes)
    mask = Image.new('L', clean.size)
    for left, top, right, bottom in boxes:
        if remaining <= 0:
            break
        edge = min(right, left+remaining)
        remaining -= right-left
        area = (math.floor(left), math.floor(top), math.ceil(edge), math.ceil(bottom))
        x, y, r, b = area
        if r <= x or b <= y:
            continue
        width, height = r-x, b-y
        # Fractional edge coverage holds the brush to the original DOM bounds at any DPR.
        columns = bytes(round(255*(min(i+1,edge)-max(i,left))) for i in range(x,r))
        rows = bytes(round(255*(min(i+1,bottom)-max(i,top))) for i in range(y,b))
        horizontal = Image.frombytes('L',(width,1),columns).resize((width,height),Image.Resampling.NEAREST)
        vertical = Image.frombytes('L',(1,height),rows).resize((width,height),Image.Resampling.NEAREST)
        brush = ImageChops.multiply(horizontal, vertical)
        mask.paste(ImageChops.lighter(mask.crop(area),brush),area)
    return Image.composite(tinted, clean, mask)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('spec',type=Path,help='Camera JSON with a clean plate, optional selected plate or ink marker, and CSS line bounds')
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    cfg=json.loads(args.spec.read_text())
    base=args.spec.resolve().parent
    frames=cfg['frames'];fps=cfg.get('fps',30);size=tuple(cfg.get('size',[1920,1080]))
    if frames<2 or fps<=0 or any(v<=0 or v%2 for v in size):
        parser.error('Use at least two frames and even canvas dimensions')
    with Image.open(base/cfg['clean']) as input_file:clean=input_file.convert('RGB')
    selected=None
    tinted=None
    if 'selected' in cfg:
        with Image.open(base/cfg['selected']) as source:selected=source.convert('RGB')
    if 'marker' in cfg:
        if selected is not None:
            parser.error('Choose a marker color or a selected plate for this passage')
        boxes = [css_box(line,cfg['clip'],cfg['dpr']) for line in cfg['lines']]
        if not boxes or not all(0 <= l < r <= clean.width and 0 <= t < b <= clean.height for l,t,r,b in boxes):
            parser.error('Marker rectangles must lie inside the captured source')
        color = ImageColor.getrgb(cfg['marker'])
        tinted = ImageChops.multiply(clean,Image.new('RGB',clean.size,color))
    move = cfg.get('move_frames',[0,cfg.get('settle_frame',frames-1)])
    reveal = cfg.get('reveal_frames',[0,frames-1])
    for interval in ([move,reveal] if (selected or tinted) else [move]):
        if len(interval) != 2 or not all(math.isfinite(v) for v in interval) or interval[0] >= interval[1]:
            parser.error('Use an ordered pair of frame positions for each time window')
    output=args.output.resolve();output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as tmp:
        target=Path(tmp)/'clip.mp4'
        cmd=['ffmpeg','-y','-nostdin','-v','error','-f','rawvideo','-pixel_format','rgb24','-video_size',f'{size[0]}x{size[1]}','-framerate',str(fps),'-i','-','-an','-c:v','libx264','-pix_fmt','yuv420p','-crf','14','-preset','veryfast',str(target)]
        finished_ink=None
        with subprocess.Popen(cmd,stdin=subprocess.PIPE) as enc:
            for n in range(frames):
                progress=ease_frames(n,*move)
                cx,cy,w=[interp(a,b,progress) for a,b in zip(cfg['view_from'],cfg['view_to'])]
                picture=clean
                if selected:
                    picture=reveal_selection(clean,selected,cfg['lines'],cfg['clip'],cfg['dpr'],ease_frames(n,*reveal))
                elif tinted:
                    if finished_ink is None:
                        picture=reveal_ink(clean,tinted,boxes,ease_frames(n,*reveal))
                        if n >= reveal[1]:
                            finished_ink=picture
                    else:
                        picture=finished_ink
                frame,_=sample_view(picture,cfg['clip'],cfg['dpr'],cx,cy,w,size)
                enc.stdin.write(frame.tobytes())
            enc.stdin.close()
            if enc.wait(): raise RuntimeError('Screen render failed')
        os.replace(target,output)
if __name__=='__main__':main()
