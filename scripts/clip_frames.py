"""Conform a source-frame interval to a numbered stage sequence, preserving source PTS provenance."""
import argparse
from bisect import bisect_right
from fractions import Fraction
import json
import math
from pathlib import Path
import shutil
import subprocess
import tempfile

from PIL import Image

from action_takes import source
from color_finish import browser_frame_filter
from source_framing import crop_box,crop_filter


def conform(ticks,base,first,stop,fps):
    if not 0 <= first < stop < len(ticks) or ticks[stop]<=ticks[first]:
        raise ValueError('Choose an ordered source-frame range with a known end time')
    times=[v*base for v in ticks[first:stop+1]]
    count=math.ceil((times[-1]-times[0])*fps)
    return [first+bisect_right(times,times[0]+Fraction(n,1)/fps)-1 for n in range(count)]


def extract(a):
    info=source(a.source)
    if a.output.exists():raise ValueError('Choose a new output directory')
    fps=Fraction(a.fps)
    if fps<=0 or min(a.size)<=0:raise ValueError('Use positive dimensions and cadence')
    stop=len(info['ticks'])-1 if a.stop is None else a.stop
    mapping=conform(info['ticks'],info['tb'],a.first,stop,fps)
    size=tuple(a.size)
    crop=crop_box(a.crop,*info['raster'],info['aspect'],size)
    cf=crop_filter(crop,info['stream'],size)
    width,height=size
    colour,interpretation=browser_frame_filter(info['stream'],cf,size,raster_size=info['raster'])
    vf=f'trim=start_frame={a.first}:end_frame={stop},{colour}'
    args=['ffmpeg','-v','error','-nostdin','-i',str(a.source),'-map',f"0:{info['video']}",'-vf',vf,
          '-fps_mode','passthrough','-frames:v',str(stop-a.first),'-f','rawvideo','-pix_fmt','rgb24','pipe:1']
    a.output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=a.output.parent) as temp:
        target=Path(temp)/'frames';target.mkdir();index=0
        length=width*height*3
        with subprocess.Popen(args,stdout=subprocess.PIPE) as decoder:
            try:
                for native in range(a.first,stop):
                    raw=decoder.stdout.read(length)
                    if len(raw)!=length:raise ValueError(f'Missing decoded source frame {native}')
                    if index>=len(mapping) or native!=mapping[index]:continue
                    file=target/f'{index+1:04}.jpg'
                    Image.frombytes('RGB',size,raw).save(file,'JPEG',quality=94,subsampling=0)
                    index+=1
                    while index<len(mapping) and native==mapping[index]:
                        shutil.copyfile(file,target/f'{index+1:04}.jpg');index+=1
            finally:
                decoder.stdout.close()
            if decoder.wait():raise ValueError('Could not decode the source interval')
        if index!=len(mapping):raise ValueError('Decoded source does not cover the output frame clock')
        tb=info['tb']
        report=dict(source=str(a.source.resolve()),video_stream=info['video'],source_range=[a.first,stop],
                    crop=crop,size=size,frames=len(mapping),fps=str(fps),time_base=str(tb),
                    input_color=interpretation,output_color='sRGB',
                    source_duration_seconds=float((info['ticks'][stop]-info['ticks'][a.first])*tb),
                    first_pts=info['ticks'][a.first],stop_pts=info['ticks'][stop],
                    source_frames=mapping,source_pts=[info['ticks'][i] for i in mapping])
        (target/'frames.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        target.rename(a.output)
    return dict(dir=str(a.output),frames=len(mapping),fps=float(fps),ext='jpg')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('source',type=Path);p.add_argument('output',type=Path)
    p.add_argument('--first',type=int,default=0,help='First native displayed frame, starting at zero')
    p.add_argument('--stop',type=int,help='Exclusive native end frame')
    p.add_argument('--fps',default='30',help='Target timeline cadence, e.g. 30 or 30000/1001')
    p.add_argument('--size',type=int,nargs=2,default=[1920,1080])
    p.add_argument('--crop',type=int,nargs=4,help='Upright original-pixel rectangle with target aspect')
    args=p.parse_args()
    try:print(json.dumps(extract(args),ensure_ascii=False))
    except (ValueError,ZeroDivisionError,OSError,subprocess.CalledProcessError) as exc:p.exit(1,str(exc)+'\n')

if __name__=='__main__':main()
