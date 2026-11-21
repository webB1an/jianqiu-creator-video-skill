"""Index the whole reference, including time between overview thumbnails."""
import argparse
import csv
import json
import shutil
import subprocess
from collections import deque
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw
from motionstudy import display_proxy_size


class FrameAtlas:
    """Lossy spatial proxies, one tile per displayed frame in decoder order."""
    columns, rows = 6, 8

    def __init__(self, folder, width, height):
        self.folder = folder
        self.width, self.height = width, height
        self.times = []

    def add(self, frame, time, pixels):
        page, slot = divmod(frame, self.columns * self.rows)
        if slot == 0:
            self.page = Image.new('RGB', (self.width*self.columns, self.height*self.rows))
        self.page.paste(pixels, ((slot % self.columns)*self.width, (slot // self.columns)*self.height))
        self.times.append(time)
        if slot == self.columns * self.rows - 1:
            self.save(page)

    def save(self, page):
        self.page.save(self.folder / f'frames-{page:04d}.jpg', quality=88)

    def finish(self, source):
        count = len(self.times)
        page_size = self.columns * self.rows
        if count % page_size:
            self.save(count // page_size)
        info = dict(name=source.name, width=self.width, height=self.height, columns=self.columns,
                    rows=self.rows, times=self.times)
        (self.folder/'frames.js').write_text('const film = ' + json.dumps(info) + ';\n')
        shutil.copyfile(Path(__file__).with_name('frame-atlas.html'), self.folder/'index.html')


def scan(source, folder, interval, *, every_frame=False):
    folder.mkdir(parents=True,exist_ok=True)
    timestamps=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0','-show_frames','-show_entries','frame=best_effort_timestamp_time','-of','json',str(source)]))['frames']
    if not timestamps:
        raise ValueError('Reference has no timestamped picture frames')
    w,h=160,144
    atlas = None
    if every_frame:
        w,h = display_proxy_size(source, 384)
        atlas = FrameAtlas(folder, w, h)
    scale = f'scale={w}:{h}' if atlas else f'scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2'
    cmd=['ffmpeg','-v','error','-i',str(source),'-map','0:v:0','-vf',scale,'-fps_mode','passthrough','-f','rawvideo','-pix_fmt','rgb24','-']
    last=None
    next_t=None
    thumbs=0
    first=None
    peak_window=deque(maxlen=3)
    peaks=[]
    with subprocess.Popen(cmd,stdout=subprocess.PIPE) as decoder, (folder/'frame-change.csv').open('w') as table:
        writer=csv.writer(table)
        writer.writerow(['frame','time','mean_abs_rgb_change'])
        for frame,row in enumerate(timestamps):
            t=float(row['best_effort_timestamp_time'])
            raw=decoder.stdout.read(w*h*3)
            if len(raw)!=w*h*3:raise ValueError('Incomplete decoded frame')
            data=np.frombuffer(raw,'uint8').reshape(h,w,3)
            if atlas:
                atlas.add(frame, t, Image.fromarray(data))
            d=0 if last is None else float(np.abs(data.astype('int16')-last).mean()/255)
            writer.writerow([frame,f'{t:.6f}',f'{d:.6f}'])
            last=data.astype('int16')
            peak_window.append((d,frame,t))
            if len(peak_window)==3 and peak_window[0][0]<peak_window[1][0]>=peak_window[2][0]:
                peaks.append(peak_window[1])
            if first is None:
                first=t
                next_t=t
            if t+.0001>=next_t or frame==len(timestamps)-1:
                slot=thumbs%24
                if slot==0: sheet=Image.new('RGB',(w*6,(h+19)*4),'#1b201e')
                x=slot%6*w;y=slot//6*(h+19)
                sheet.paste(Image.fromarray(data),(x,y))
                ImageDraw.Draw(sheet).text((x+4,y+h+2),f'{t:.2f}s   f{frame}',fill='white')
                thumbs+=1
                if slot==23 or frame==len(timestamps)-1:
                    sheet.save(folder/f'overview-{(thumbs-1)//24+1:02}.jpg',quality=88)
                next_t=first+(int((t-first+.0001)/interval)+1)*interval
        if decoder.stdout.read(1) or decoder.wait():raise ValueError('Frame table and video disagree')
    # A long camera move can score highly. Use these only to locate a native-frame inspection.
    selected=[]
    for diff,i,t in sorted(peaks,reverse=True):
        if all(abs(t-other['time'])>=.6 for other in selected):
            selected.append(dict(frame=i,time=round(t,4),mean_change=round(diff,4)))
            if len(selected)==24:break
    result=dict(frames=len(timestamps),first_pts=first,last_pts=float(timestamps[-1]['best_effort_timestamp_time']),thumbnails=thumbs,candidates=sorted(selected,key=lambda v:v['frame']))
    (folder/'index.json').write_text(json.dumps(result,indent=2))
    if atlas:
        atlas.finish(source)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('video',type=Path)
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--interval',type=float,default=3)
    p.add_argument('--every-frame',action='store_true',help='Also write an offline single-frame viewer and every decoded frame as a tile. Interval only controls overview sheets.')
    a=p.parse_args()
    if not 0<a.interval: p.error('Interval must be positive')
    print(scan(a.video,a.out,a.interval,every_frame=a.every_frame)['frames'],'frames indexed')
if __name__=='__main__':
    main()
