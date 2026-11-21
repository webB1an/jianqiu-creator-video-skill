"""Make reproducible project-local motion effects for the standard soundmix cue sheet."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import tempfile
import wave

import numpy as np

RATE = 48000
DEFAULTS = {'pass':.42, 'rise':.64, 'land':.56, 'tick':.09}


def synth(kind, seconds, seed):
    n = round(seconds*RATE)
    t = np.arange(n) / RATE
    u = np.arange(n) / (n-1)
    rng = np.random.default_rng(seed)
    noise = rng.normal(0,1,n)
    if kind in ('pass','rise'):
        # Modulating a smoothed noise carrier moves energy through the spectrum.
        kernel = np.hanning(79)
        gust = np.convolve(noise,kernel/kernel.sum(),mode='same')
        pitch = (380+1200*np.sin(np.pi*u)**2) if kind == 'pass' else 230+2500*u**1.8
        phase = 2*np.pi*np.cumsum(pitch)/RATE
        envelope = (np.sin(np.pi*u)**1.6 if kind == 'pass' else u**1.6*(1-u)**.45)
        signal = gust*(.7*np.sin(phase)+.3*np.sin(phase*1.63))*envelope
    elif kind == 'land':
        pitch = 57+95*np.exp(-t/.055)
        phase = 2*np.pi*np.cumsum(pitch)/RATE
        signal = (np.sin(phase)+.28*np.sin(2*phase))*np.exp(-7*u) + .13*noise*np.exp(-48*u)
    else:
        phase = 2*np.pi*(1560*t-370*t*t/seconds)
        signal = (.7*np.sin(phase)+.2*np.sin(phase*1.53)+.1*noise)*np.exp(-7*u)
    fade = min(n//3,max(2,round(.002*RATE)))
    signal[:fade] *= np.linspace(0,1,fade)
    signal[-fade:] *= np.linspace(1,0,fade)
    signal *= .83/np.max(np.abs(signal))
    pcm = np.rint(signal*32767).astype('<i2')
    # soundmix fades both ends of the selected take. Anchor the transient that will
    # actually enter the effects bus, including on very short ticks.
    mixed = np.abs(pcm.astype(np.float64))
    fade = min(200,n//2)
    mixed[:fade] *= np.linspace(0,1,fade)
    mixed[-fade:] *= np.linspace(1,0,fade)
    anchor = int(mixed.argmax()) / RATE
    return pcm,anchor


def create(project, name, kind, seconds, seed):
    if not re.fullmatch('[a-zA-Z0-9_-]+',name):
        raise ValueError('Use a single sound key made of letters, digits, dashes or underscores')
    target = project/'assets'/'sfx'/name
    if target.exists():
        raise ValueError(f'{target} already exists. Reuse that take or choose a new key')
    pcm,anchor = synth(kind,seconds,seed)
    target.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as tmp:
        folder = Path(tmp)/'take'
        folder.mkdir()
        take = folder/'take.wav'
        with wave.open(str(take),'wb') as stream:
            stream.setparams((1,2,RATE,0,'NONE','not compressed'))
            stream.writeframes(pcm.tobytes())
        meta = dict(page=f'assets/sfx/{name}/source.json',author='本片声音设计',stars=None,votes=None,
                    wav_sha256=hashlib.sha256(take.read_bytes()).hexdigest(),
                    generated={'method':'design_sound.py','kind':kind,'seed':seed,'rate':RATE},
                    seconds=len(pcm)/RATE,anchor=anchor)
        (folder/'source.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2))
        folder.rename(target)
    return dict(key=name,trim=[0,meta['seconds']],anchor=anchor)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('kind',choices=DEFAULTS)
    p.add_argument('--project',type=Path,default=Path('.'))
    p.add_argument('--key',help='Project-local sound key; defaults to motion-KIND')
    p.add_argument('--seconds',type=float)
    p.add_argument('--seed',type=int,default=7)
    args=p.parse_args()
    duration=DEFAULTS[args.kind] if args.seconds is None else args.seconds
    if not math.isfinite(duration) or not .02 <= duration <= 15 or args.seed < 0:
        p.error('Use a motion effect of .02 to 15 seconds and a nonnegative seed')
    try:
        print(json.dumps(create(args.project,args.key or 'motion-'+args.kind,args.kind,duration,args.seed),ensure_ascii=False))
    except (OSError,ValueError) as error:
        p.exit(1,str(error)+'\n')

if __name__=='__main__':
    main()
