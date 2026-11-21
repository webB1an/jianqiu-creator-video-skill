"""Place the transient of a catalogued recording at a picture cue; print an auditable edit list."""
import json
import subprocess
import wave
from pathlib import Path
import numpy as np
from soundbank import BANK

def mix():
    timeline=json.loads(Path('timeline.json').read_text());fps=timeline['fps']
    starts={x['id']:x['start_frame']/fps for x in timeline['shots']}
    rate=48000;mix=np.zeros((round(timeline['frames']/fps*rate),2),'float32')
    log=[]
    for e in json.loads(Path('sfx.json').read_text()):
        key=e['key']
        if Path(key).name!=key:raise ValueError('Invalid sound key')
        source=BANK/key
        local=Path('assets/sfx')/key
        if local.is_dir():source=local
        meta=json.loads((source/'source.json').read_text())
        import hashlib
        if hashlib.sha256((source/'take.wav').read_bytes()).hexdigest()!=meta['wav_sha256']:raise ValueError('Sound hash changed')
        take=np.frombuffer(subprocess.check_output(['ffmpeg','-v','error','-i',source/'take.wav','-ar',str(rate),'-ac','1','-f','f32le','-']),'float32')
        a,b,anchor=e['trim'][0],e['trim'][1],e['anchor']
        if not 0<=a<=anchor<b<=len(take)/rate+.0001:raise ValueError('Invalid source range: '+key)
        x=take[round(a*rate):round(b*rate)].copy()
        peak=np.max(abs(x))
        if peak<=.001:raise ValueError('Empty sound excerpt: '+key)
        x*=10**(e['peak_db']/20)/peak
        fade=min(200,len(x)//2)
        if fade:x[:fade]*=np.linspace(0,1,fade);x[-fade:]*=np.linspace(1,0,fade)
        cue=starts[e['shot']]+e['offset'];start=round((cue-anchor+a)*rate)
        if start<0 or start+len(x)>len(mix):raise ValueError('Sound extends outside timeline: '+key)
        pan=e.get('pan',0)
        if not -1<=pan<=1:raise ValueError('Pan must be in [-1,1]')
        mix[start:start+len(x)]+=x[:,None]*np.sqrt([(1-pan)/2,(1+pan)/2])
        log.append(dict(key=key,event=e['event'],cue=round(cue,3),page=meta['page'],stars=meta['stars'],votes=meta['votes'],author=meta['author']))
    if abs(mix).max()>1:raise ValueError('Effects stem clips')
    Path('audio').mkdir(exist_ok=True);Path('qa').mkdir(exist_ok=True)
    with wave.open('audio/effects.wav','wb') as f:
        f.setparams((2,2,rate,0,'NONE','not compressed'));f.writeframes((mix*32767).astype('<i2').tobytes())
    Path('qa/sound-cues.json').write_text(json.dumps(log,ensure_ascii=False,indent=2))
if __name__=='__main__':mix()
