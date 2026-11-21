"""Decode the opening and compare actual caption footprints with the matching clean edit."""
import argparse
from fractions import Fraction
import json
from pathlib import Path
import subprocess

import numpy as np
from PIL import Image, ImageDraw

from floating_captions import cues, top_caption_ranges
from picture_finish import brand_sticker


def info(path):
    data = json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0',
        '-show_entries','stream=width,height,avg_frame_rate,nb_frames','-of','json',str(path)]))['streams'][0]
    return data['width'],data['height'],Fraction(data['avg_frame_rate']),int(data['nb_frames'])


def decode(path,w,h,select):
    data = subprocess.check_output(['ffmpeg','-nostdin','-v','error','-i',str(path),'-an','-vf',
        select+f',scale={w}:{h}:flags=area','-fps_mode','passthrough','-pix_fmt','rgb24','-f','rawvideo','-'])
    return np.frombuffer(data,np.uint8).reshape(-1,h,w,3)


def report(video,folder,clean=None,srt=None,project=None):
    w,h,fps,total=info(video)
    upper = []
    if project:
        timeline = json.loads((project/'timeline.json').read_text())
        if timeline['frames'] != total or timeline['fps'] != fps:
            raise ValueError('Select the project matching the full video timeline')
        upper = top_caption_ranges(json.loads((project/'edit.json').read_text()), timeline, 0, total,
                                   srt=srt or project/'audio/captions.srt', size=(w, h))
    def at_top(frame):
        return any(lo <= frame < hi for lo,hi in upper)
    pw=384
    ph=round(pw*h/w)
    count=min(total,round(5*fps))
    opening=decode(video,pw,ph,f'trim=end_frame={count}')
    if len(opening)!=count:raise ValueError('Opening decode length differs from the picture stream')
    # Exclude the subtitle lane. Motion numbers locate suspect holds; a reviewer identifies what changed.
    lag=max(1,round(float(fps)*.20))
    fractions=[]
    for i in range(lag,count):
        pair=(at_top(i-lag),at_top(i))
        a=round(ph*.22) if any(pair) else 0
        b=ph if all(pair) else round(ph*.78)
        diff=np.abs(opening[i,a:b].astype(np.int16)-opening[i-lag,a:b].astype(np.int16))
        fractions.append((diff.max(axis=2)>8).mean())
    fractions=np.asarray(fractions)
    out=dict(frames=total,fps=str(fps),size=[w,h],opening_frames=count,comparison_stride_frames=lag,
             opening_pairs=len(fractions),opening_pairs_under_one_percent=int((fractions<.01).sum()),
             changed_fraction_every_pair=fractions.round(5).tolist())
    folder.mkdir(parents=True,exist_ok=True)
    indices=np.unique(np.linspace(0,count-1,min(10,count),dtype=int))
    board=Image.new('RGB',(pw*2,(ph+25)*((len(indices)+1)//2)),'#15191b')
    d=ImageDraw.Draw(board)
    for n,i in enumerate(indices):
        x,y=(n%2)*pw,(n//2)*(ph+25)
        board.paste(Image.fromarray(opening[i]),(x,y))
        d.text((x+8,y+ph+3),f'F{i}   {float(i/fps):.2f}s')
    board.save(folder/'opening.jpg',quality=85)
    if clean is not None:
        if srt is None or info(clean)!=(w,h,fps,total):
            raise ValueError('Caption comparison needs an SRT and a frame-aligned clean picture')
        reads=[(max(0,a),min(b,total),text) for a,b,text in cues(srt,fps) if max(0,a)<min(b,total)]
        boundaries = [edge for pair in upper for edge in pair]
        sample=set()
        for a,b,_ in reads:
            edges=[a,*(edge for edge in boundaries if a<edge<b),b]
            sample.update((lo+hi-1)//2 for lo,hi in zip(edges,edges[1:]))
        sample=sorted(sample)
        if sample:
            select="select='" + '+'.join(f'eq(n,{i})' for i in sample) + "'"
            final=decode(video,pw,ph,select)
            bare=decode(clean,pw,ph,select)
            if len(final)!=len(sample) or final.shape!=bare.shape:raise ValueError('Unequal decoded sample counts')
            notes=[]
            if upper:
                mark_w,mark_h = brand_sticker(h).size
                x,y = w-mark_w-round(w*.042),round(h*.055)
            for j,frame in enumerate(sample):
                is_top=at_top(frame)
                top,bottom=(0,round(ph*.21)) if is_top else (round(ph*.79),ph)
                diff=np.abs(final[j,top:bottom].astype(np.int16)-bare[j,top:bottom].astype(np.int16)).max(axis=2)>12
                if is_top:
                    # The export also adds a brand mark. Exclude its pixels and the nearby encoder bleed.
                    diff[max(0,round(y*ph/h)-2):round((y+mark_h)*ph/h)+2,
                         max(0,round(x*pw/w)-2):round((x+mark_w)*pw/w)+2]=False
                cols=np.flatnonzero(diff.sum(axis=0)>=max(2,round(ph*.012)))
                rows=np.flatnonzero(diff.sum(axis=1)>=max(3,round(pw*.018)))
                rect=([int(cols[0]),int(rows[0])+top,int(cols[-1])+1,int(rows[-1])+top+1]
                      if len(rows) and len(cols) else None)
                notes.append(dict(frame=frame,text=' / '.join(t for a,b,t in reads if a<=frame<b),
                                  caption_position='top' if is_top else 'bottom',
                                  observed_box=rect,changed_fraction_of_whole=round(float(diff.sum()/(pw*ph)),4),
                                  left_and_right_change=round(float(diff[:,np.r_[0:pw//10,9*pw//10:pw]].mean()),4)))
            out.update(probe_size=[pw,ph],caption_samples=notes)
    (folder/'presentation.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
    return out


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('video',type=Path)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--clean',type=Path)
    p.add_argument('--srt',type=Path)
    p.add_argument('--project',type=Path,help='Project folder for plain SRTs with per-shot caption positions')
    args=p.parse_args()
    data=report(args.video,args.out,args.clean,args.srt,args.project)
    print(f"Opening: {data['opening_pairs_under_one_percent']}/{data['opening_pairs']} low-change pairs. "
          f"Caption samples: {len(data.get('caption_samples',[]))}. View {args.out}.")
