"""Geometry for voice-cued tables, terminal rows, trees, timelines, active steps and bars."""
from __future__ import annotations

import argparse
from datetime import date
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw
from identity import card_colors, card_face
from optical_layout import ink_mask


def local_captions(srt, first_frame, frames, fps):
    """Convert a project's SRT entries to clip-local, half-open display frames."""
    def frame(value):
        h,m,s=value.split(':')
        return math.ceil((int(h)*3600+int(m)*60+float(s.replace(',','.')))*fps-1e-6)
    entries=[]
    doc=Path(srt).read_text(encoding='utf-8-sig').replace('\r\n','\n')
    for block in doc.strip().split('\n\n'):
        _,span,words=block.split('\n',2)
        first,last=(frame(a.strip())-first_frame for a in span.split('-->'))
        if last>0 and first<frames:
            entries.append({'start':max(0,first),'end':min(frames,last),'text':' '.join(words.splitlines())})
    return entries


def cue(captions,phrase):
    norm=lambda s:''.join(str(s).split()).casefold()
    hits=[r for r in captions if norm(phrase) in norm(r['text'])]
    if len(hits)!=1:
        raise ValueError(f'Expected one cue for {phrase!r}, found {len(hits)}; select or narrow the localized caption.')
    return hits[0]['start']


def arrival(cue_frame,frames,advance=10,settle=4,hold=12):
    """Return start and arrival. Leave a whole-word reading hold even if the cue is near the cut."""
    if frames<2:raise ValueError('A diagram needs at least two frames')
    end=max(1,min(frames-max(1,hold),cue_frame+settle))
    return max(0,min(end-1,cue_frame-advance)),end


def ease(frame,window):
    a,b=window
    p=max(0.,min(1.,(frame-a)/max(1,b-a)))
    return p*p*(3-2*p)


def ink_group(image,box,lines,align='left',line_gap=10,opacity=1,offset=(0,0)):
    """Lay out a multiline block as one set of visible glyphs. Lines are (text,font,color)."""
    bits=[(ink_mask(font,label),color) for label,font,color in lines if label]
    if not bits:return None
    width=max(mask.width for mask,_ in bits)
    height=sum(mask.height for mask,_ in bits)+line_gap*(len(bits)-1)
    left,top,right,bottom=box
    if width>right-left+1 or height>bottom-top+1:
        raise ValueError('Text exceeds the declared cell. Widen the column, reduce the type size or edit the wording.')
    group_x={'left':left,'center':(left+right-width)/2,'right':right-width}[align]+offset[0]
    y=round((top+bottom-height)/2+offset[1]);origin_y=y
    for mask,color in bits:
        x={'left':group_x,'center':group_x+(width-mask.width)/2,'right':group_x+width-mask.width}[align]
        if opacity<1:
            mask=mask.point([round(n*max(0,opacity)) for n in range(256)])
        image.paste(color,(round(x),y,round(x)+mask.width,y+mask.height),mask);y+=mask.height+line_gap
    painted=[round(group_x),origin_y,round(group_x)+width,origin_y+height]
    return {'ink_box':painted,'reference_box':list(box),'axis':'xy' if align=='center' else 'y'}


def path_stroke(draw,points,phase,color,width=3):
    """Reveal a polyline by physical distance along its corners."""
    if phase<=0:return
    lengths=[math.dist(a,b) for a,b in zip(points,points[1:])]
    remain=min(1,phase)*sum(lengths);shown=[points[0]]
    for (a,b),span in zip(zip(points,points[1:]),lengths):
        if not span:continue
        if remain<span:
            p=remain/span;shown.append(tuple(u+(v-u)*p for u,v in zip(a,b)));break
        shown.append(b);remain-=span
    if len(shown)>1:draw.line([(round(x),round(y)) for x,y in shown],fill=color,width=width,joint='curve')


def branch_parts(root_right,trunk_x,node,phase):
    """Return a traveled path plus separate terminal-dot and label visibility."""
    x,y=root_right;nx,ny=node
    points=[(x,y),(trunk_x,y),(trunk_x,ny),(nx,ny)]
    return points,min(1,phase/.64),max(0,min(1,(phase-.51)/.23)),max(0,min(1,(phase-.60)/.40))


def step_positions(count,phases,center,distance,entry_extra=0):
    if count!=len(phases) or count<=0:raise ValueError('Supply one arrival phase for each step')
    stage=sum(phases[1:])
    return [center+(i-stage)*distance+(entry_extra*(1-phases[i]) if i else 0) for i in range(count)]


def date_positions(dates,left,right):
    """Position month or day labels on an actual calendar scale."""
    def parse(s):return date.fromisoformat(s+'-01' if len(s)==7 else s).toordinal()
    points=list(map(parse,dates));start=min(points);span=max(points)-start
    if not span:raise ValueError('Use at least two different dates on a timeline')
    return [left+(d-start)*(right-left)/span for d in points]


def bar_end(value,min_value,max_value,left,right,progress):
    """Derive both axes from one shared scale. Supports a signed diverging chart."""
    if min_value>0 or max_value<0 or min_value>=max_value:raise ValueError('The scale must include zero and have positive extent')
    if value<min_value or value>max_value:raise ValueError('Value falls outside the configured scale')
    zero=left-min_value*(right-left)/(max_value-min_value)
    return zero,zero+value*max(0,min(1,progress))*(right-left)/(max_value-min_value)


def sample(out):
    """Render three real transitions and geometry receipts on a neutral synthetic dataset."""
    out=Path(out);out.mkdir(parents=True,exist_ok=True)
    clr=card_colors('light');bounds=[];canvas=Image.new('RGB',(1280,720),clr['background'])
    rows=[('甲档',2),('乙档',4),('丙档',8)]
    frames=[5,20,45,70,90,111]
    for f in frames:
        im=canvas.copy();d=ImageDraw.Draw(im)
        for i,(lab,val) in enumerate(rows):
            q=ease(f,arrival(12+35*i,116))
            if not q:continue
            y=175+i*155;ref=(70,y-65,310,y+65)
            geometry=ink_group(im,ref,[(lab,card_face('title',44,lab),clr['text'])],'center',opacity=q)
            if q==1:bounds.append({'frame':f,'group':lab,**geometry})
            zero,tip=bar_end(val,0,8,360,985,q)
            d.rectangle((round(zero),y-18,round(tip),y+18),fill=clr['compare'])
            if q>.8:
                txt=f'{val} 单位'
                ink_group(im,(round(tip)+15,y-58,round(tip)+200,y+58),[(txt,card_face('label',38,txt),clr['text'])])
        im.save(out/f'{f:03d}.png')
    assert step_positions(3,[1,1,0],640,320)[1]==640
    assert date_positions(['2021-01','2021-07','2022-01'],128,1056)[1]>128
    assert bar_end(4,0,8,360,985,1)[1]==672.5
    assert arrival(114,116,hold=12)[1]<=104
    (out/'geometry.json').write_text(json.dumps(bounds,ensure_ascii=False,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sample',required=True,type=Path)
    sample(parser.parse_args().sample)
