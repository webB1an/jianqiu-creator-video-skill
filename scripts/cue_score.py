"""Render original beat arrangements from recorded hits, locked to an edit timeline."""
import argparse
import json
import math
import wave
from functools import lru_cache
from pathlib import Path

import numpy as np

RATE = 48000
LIBRARY = Path(__file__).resolve().parents[1] / 'assets/instruments'


@lru_cache(maxsize=64)
def instrument(name, semitones):
    if name not in ('kick', 'rim', 'hat', 'pluck', 'wood'):
        raise ValueError(f'Unknown instrument {name}')
    with wave.open(str(LIBRARY / f'{name}.wav')) as f:
        if (f.getframerate(), f.getnchannels(), f.getsampwidth()) != (RATE, 1, 2):
            raise ValueError(f'Invalid instrument format for {name}')
        samples = np.frombuffer(f.readframes(f.getnframes()), '<i2').astype('float32')
    peak = np.abs(samples).max()
    if not peak:
        raise ValueError(f'Silent instrument {name}')
    # Remove leading recording silence so the transient lands on the requested beat.
    on = np.flatnonzero(np.abs(samples) > peak*.014)[0]
    samples = samples[max(0, on - RATE//500):] / peak
    if semitones:
        ix = np.arange(0, len(samples)-1, 2**(semitones/12))
        samples = np.interp(ix, np.arange(len(samples)), samples).astype('float32')
    return samples


def write_stem(score, timeline, path):
    fps = timeline['fps']
    starts = {s['id']: s['start_frame']/fps for s in timeline['shots']}
    length = round(timeline['frames']*RATE/fps)
    master = np.zeros((length, 2), dtype='float32')
    for region in score['sections']:
        shot, end = region['start'], region.get('until')
        if shot not in starts or (end and end not in starts):
            raise ValueError('Score refers to an unknown shot')
        offset = round((starts[shot]+region.get('offset',0))*RATE)
        stop = round((starts[end] if end else length/RATE)*RATE)+round(region.get('end_offset',0)*RATE)
        if not 0 <= offset < stop <= length:
            raise ValueError(f'Invalid score window starting at {shot}')
        pattern = score['patterns'][region['pattern']]
        beat_seconds = 60/float(region['bpm'])
        if not math.isfinite(beat_seconds) or beat_seconds<=0 or not pattern['beats']>0:
            raise ValueError('Use a positive BPM and pattern length')
        loop = pattern['beats']*beat_seconds
        bus = np.zeros((stop-offset,2),'float32')
        for repeat in range(math.ceil(len(bus)/(RATE*loop))):
            for note in pattern['notes']:
                name, beat, db, semitones, pan = note
                if not 0<=beat<pattern['beats'] or not -1<=pan<=1:
                    raise ValueError(f'Invalid beat or stereo position in {name}')
                when=round((repeat*loop+beat*beat_seconds)*RATE)
                if when >= len(bus): continue
                take=instrument(name,semitones)
                amount=min(len(bus)-when,len(take))
                level=10**((db+region.get('gain_db',0))/20)
                bus[when:when+amount]+=take[:amount,None]*level*np.sqrt([(1-pan)/2,(1+pan)/2])
        tail=min(len(bus),max(1,round(RATE*region.get('fade_out',.065))))
        bus[-tail:] *= np.linspace(1,0,tail)[:,None]
        master[offset:stop]+=bus
    if np.abs(master).max()>1:raise ValueError('Score clips, reduce note or section levels')
    path.parent.mkdir(parents=True,exist_ok=True)
    with wave.open(str(path),'wb') as f:
        f.setparams((2,2,RATE,0,'NONE','not compressed'))
        f.writeframes((master*32767).astype('<i2').tobytes())


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('score',type=Path)
    p.add_argument('--timeline',type=Path,default=Path('timeline.json'))
    p.add_argument('--out',type=Path,default=Path('audio/music.wav'))
    a=p.parse_args()
    write_stem(json.loads(a.score.read_text()),json.loads(a.timeline.read_text()),a.out)

if __name__ == '__main__':
    main()
