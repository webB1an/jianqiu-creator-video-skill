"""Compare each original quote with its nearest narration in the delivered mix."""
import json
import math
import re
import subprocess

import numpy as np


def levels(samples):
    rms=float(np.mean(np.square(samples,dtype=np.float64))) if samples.size else 0.
    integrated=None
    if len(samples)>=19200:  # A 400 ms EBU gating block at 48 kHz.
        cmd=['ffmpeg','-nostdin','-hide_banner','-nostats','-f','f32le','-ar','48000','-ac','2',
             '-i','pipe:0','-af','loudnorm=print_format=json','-f','null','-']
        log=subprocess.run(cmd,input=samples.tobytes(),stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,check=True).stderr.decode()
        match=re.search(r'\{\s*"input_i"[\s\S]*?\}',log)
        if match:
            value=float(json.loads(match[0])['input_i'])
            if math.isfinite(value):integrated=value
    return integrated, 10*math.log10(rms) if rms else None


def compare(media,timeline):
    takes=timeline.get('voice_takes',[])
    quotes=[i for i,t in enumerate(takes) if 'source_clip' in t]
    if not quotes:return []
    raw=subprocess.check_output(['ffmpeg','-nostdin','-v','error','-i',str(media),'-map','0:a:0',
                                 '-ac','2','-ar','48000','-f','f32le','pipe:1'])
    samples=np.frombuffer(raw,'<f4').reshape(-1,2)
    notes=[i for i,t in enumerate(takes) if 'source_clip' not in t]
    measured={}
    def level(i):
        if i not in measured:
            t=takes[i];scale=48000/t['sample_rate']
            measured[i]=levels(samples[round(t['start_sample']*scale):round(t['end_sample']*scale)])
        return measured[i]
    result=[]
    for i in quotes:
        t=takes[i]
        ref=min(notes,key=lambda j:abs(j-i),default=None)
        source=level(i)
        neighbor=level(ref) if ref is not None else (None,None)
        k=0 if source[0] is not None and (ref is None or neighbor[0] is not None) else 1
        left,right=source[k],neighbor[k]
        result.append(dict(shot=t['shot'],source=t['source_clip'],unit=('LUFS','dBFS RMS')[k],
                           quote=round(left,2) if left is not None else None,
                           neighboring_shot=takes[ref]['shot'] if ref is not None else None,
                           neighbor=round(right,2) if right is not None else None,
                           difference_db=round(left-right,2) if left is not None and right is not None else None))
    return result
