"""Small Pillow compositors for keyed typography, evidence details and paper reveals.

Rendering functions take a frame progress in [0, 1]. Compute it from the actual
speech timeline, and hold the final design long enough for the viewer to read it.
"""
import math
from functools import lru_cache
from PIL import Image, ImageChops, ImageDraw


def pin_to_surface(frame, layer, corners, *, foreground=None):
    """Place a transparent graphic on a tracked planar surface in this frame.

    Supply frame-pixel corners in top-left, top-right, bottom-right, bottom-left
    order. A white foreground mask preserves the original subject at that pixel.
    """
    import numpy as np

    if frame.mode not in ('RGB', 'RGBA') or layer.mode != 'RGBA':
        raise ValueError('Use an RGB/RGBA scene and an RGBA surface graphic')
    if foreground is not None and (foreground.mode != 'L' or foreground.size != frame.size):
        raise ValueError('Foreground matte must be a full-frame grayscale image')
    points = np.asarray(corners, dtype=float)
    if points.shape != (4, 2) or not np.isfinite(points).all():
        raise ValueError('Supply four finite surface corners')
    sides = np.roll(points, -1, axis=0)-points
    adjacent = np.roll(sides, -1, axis=0)
    turns = sides[:,0]*adjacent[:,1] - sides[:,1]*adjacent[:,0]
    if not np.all(turns > 1e-6):
        raise ValueError('Surface corners must enclose a clockwise convex quadrilateral')
    left, top = np.maximum(np.floor(points.min(axis=0)).astype(int), (0,0))
    right, bottom = np.minimum(np.ceil(points.max(axis=0)).astype(int), frame.size)
    if left >= right or top >= bottom:
        return
    w, h = layer.size
    # Pillow's perspective transform maps output coordinates to source coordinates.
    # Place the coordinate origin on the surface to keep the linear system stable.
    local = points - points[0]
    equations = []
    uv = [(0,0), (w,0), (w,h), (0,h)]
    for (x,y), (u,v) in zip(local, uv):
        equations.extend([[x,y,1,0,0,0,-u*x,-u*y], [0,0,0,x,y,1,-v*x,-v*y]])
    mapping = np.append(np.linalg.solve(equations, np.ravel(uv)), 1.).reshape(3,3)
    x0, y0 = np.array([left,top])-points[0]
    mapping[:,2] += mapping[:,0]*x0 + mapping[:,1]*y0
    if abs(mapping[2,2]) < 1e-12:
        raise ValueError('Surface projection is singular at the crop origin')
    coeffs = (mapping/mapping[2,2]).ravel()[:8]
    graphic = layer.convert('RGBa').transform((right-left,bottom-top), Image.Transform.PERSPECTIVE,
                    coeffs, Image.Resampling.BICUBIC).convert('RGBA')
    if foreground is not None:
        visible = ImageChops.invert(foreground.crop((left,top,right,bottom)))
        graphic.putalpha(ImageChops.multiply(graphic.getchannel('A'), visible))
    if frame.mode == 'RGBA':
        frame.alpha_composite(graphic, (left,top))
    else:
        frame.paste(graphic,(left,top),graphic)

def _ease(progress):
    t = min(1.0, max(0.0, float(progress)))
    return t*t*(3-2*t)

def masked_rise(canvas, layer, xy, progress):
    """Show an RGBA text layer through its final bounding box, from below."""
    if layer.mode != 'RGBA':
        raise ValueError('masked_rise expects a transparent RGBA layer')
    w,h = layer.size
    shift = round(h*(1-_ease(progress)))
    if shift == h:
        return
    visible = layer.crop((0, 0, w, h-shift))
    x,y = xy
    canvas.paste(visible,(round(x),round(y)+shift),visible)

def snapshot_number(canvas, layer, xy, text, font, progress):
    """Reveal a measured value in place from its fully typeset transparent layer.

    The layer starts at the left of the numeric run and includes its entire ink
    bounds. Use the same text and font that drew the layer. At rest, its pixels
    are pasted intact, retaining punctuation baselines, spacing, and kerning.
    """
    if not text or layer.mode != 'RGBA':
        raise ValueError('Expected a nonempty number and its transparent RGBA layer')
    x, y = map(round, xy)
    if progress <= 0:
        return
    if progress >= 1:
        canvas.paste(layer, (x, y), layer)
        return

    left = 0
    for index in range(len(text)):
        right = round(font.getlength(text[:index + 1])) if index + 1 < len(text) else layer.width
        # Each slot uses about half the interval. Its neighbours overlap in time.
        start = (47 / 95) * index / max(1, len(text) - 1)
        visible = _ease((progress - start) / (48 / 95))
        if right > left and visible > 0:
            slot = layer.crop((left, 0, right, layer.height))
            if visible >= 1:
                canvas.paste(slot, (x + left, y), slot)
            else:
                masked_rise(canvas, slot, (x + left, y), visible)
        left = right


def count_to(start, end, progress):
    """Ease a numeric *magnitude* to its labeled endpoint. Keep units fixed.

    Intermediate frames are an animated build, not measured intermediate data.
    Show dates, IDs and real discrete states with digit_roll instead.
    """
    t = min(1.0, max(0.0, float(progress)))
    return round(start + (end-start)*(1-(1-t)**3))

def digit_roll(canvas, xy, old, new, font, fill, progress):
    """Roll changed digits through fixed cells. Separators and unchanged digits hold.

    Pass two formatted strings of the same length. For different date or number
    widths, right-align them first with spaces; this also supports a reveal from
    a blank field. Only the supplied endpoint digits appear in the animation.
    """
    if len(old) != len(new) or not new:
        raise ValueError('Use equally wide nonempty number strings')
    supported='0123456789.,: -+/%'
    if any(char not in supported for char in old+new):
        raise ValueError('Use number strings; draw the unit separately')
    cell = math.ceil(max(font.getlength(digit) for digit in '0123456789'))
    ascent,descent=font.getmetrics()
    height=ascent+descent+2
    offset=round(height*_ease(progress))
    left,top=map(round,xy)
    for before,after in zip(old,new):
        narrow = before==after and before in ',.:/'
        width = max(1,math.ceil(font.getlength(before))) if narrow else cell
        slot=Image.new('RGBA',(width,height))
        d=ImageDraw.Draw(slot)
        baseline=ascent
        if before==after or offset==0:
            d.text((width/2,baseline),before,font=font,fill=fill,anchor='ms')
        elif offset==height:
            d.text((width/2,baseline),after,font=font,fill=fill,anchor='ms')
        else:
            d.text((width/2,baseline-offset),before,font=font,fill=fill,anchor='ms')
            d.text((width/2,baseline+height-offset),after,font=font,fill=fill,anchor='ms')
        canvas.paste(slot,(left,top),slot)
        left+=width
    return left

def evidence_zoom(source, size, overview, detail, progress):
    """Move from a full document to a legible passage. Boxes are source pixels.

    Both boxes must share the output aspect ratio. Geometric zoom keeps the
    apparent scale change even. Supply the desired crop around the real source
    to keep the highlighted passage visible throughout the move.
    """
    tw,th=size
    if min(tw,th)<=0: raise ValueError('Positive output size required')
    for box in (overview,detail):
        l,t,r,b=box
        if not (0<=l<r<=source.width and 0<=t<b<=source.height):
            raise ValueError('Document crop lies outside source')
        if not math.isclose((r-l)/(b-t),tw/th,rel_tol=.004):
            raise ValueError('Document crop and output must have the same aspect ratio')
    e=_ease(progress)
    l,t,r,b=overview;u,v,w,z=detail
    # Interpolate centers by normalized crop size so zoom tracks toward the
    # shared optical target instead of drifting past it then sliding back.
    first_width=r-l;last_width=w-u
    width=first_width*(last_width/first_width)**e
    travel=(first_width-width)/(first_width-last_width) if first_width!=last_width else e
    cx=(l+r)/2*(1-travel)+(u+w)/2*travel
    cy=(t+b)/2*(1-travel)+(v+z)/2*travel
    height=width*th/tw
    left=min(max(0,cx-width/2),source.width-width)
    top=min(max(0,cy-height/2),source.height-height)
    return source.resize(size,Image.Resampling.BICUBIC,
                         box=(left,top,left+width,top+height))

@lru_cache(maxsize=16)
def _torn_profile(height, amplitude):
    # Deterministic micro-fibres on one sheet; the contour never boils from frame to frame.
    count=max(4,min(120,height//7))
    return [(round(i*height/count),amplitude*(.65*math.sin(i*.91)+.23*math.sin(i*2.71)+.12*math.sin(i*5.3)))
            for i in range(count+1)]

def paper_reveal(outgoing,incoming,progress,edge_width=None):
    """Uncover incoming from the left along a stable, irregular paper edge."""
    if outgoing.size!=incoming.size or outgoing.mode!=incoming.mode:
        raise ValueError('Paper transition frames need the same mode and size')
    if progress<=0:return outgoing.copy()
    if progress>=1:return incoming.copy()
    w,h=outgoing.size
    amp=max(2,round(w*.009)) if edge_width is None else max(1,int(edge_width))
    x=-amp*2+(w+amp*4)*_ease(progress)
    edge=[(round(x+dx),y) for y,dx in _torn_profile(h,amp)]
    mask=Image.new('L',(w,h),0)
    ImageDraw.Draw(mask).polygon([(0,0),*edge,(0,h)],fill=255)
    return Image.composite(incoming,outgoing,mask)
