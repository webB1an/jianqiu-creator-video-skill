"""Compare two final mixes at matched program loudness at the same time in the edit."""
import argparse
import json
import math
from pathlib import Path
import re
import subprocess
import wave


def levels(path):
    p = subprocess.run(['ffmpeg','-nostdin','-hide_banner','-nostats','-i',str(path),'-map','0:a:0',
                        '-af','loudnorm=I=-20:TP=-1.5:LRA=9:print_format=json','-f','null','-'],
                       capture_output=True,text=True,check=True)
    block = re.findall(r'\{[^{}]*"input_i"[^{}]*\}',p.stderr)
    if not block:
        raise ValueError(f'Cannot measure audio stream of {path}')
    data = json.loads(block[-1])
    result = (float(data['input_i']),float(data['input_tp']))
    if not all(map(math.isfinite,result)):
        raise ValueError(f'No measurable program audio in {path}')
    return result


def sample(path, start, end, gain, phone):
    channels = 1 if phone else 2
    filters = ['aresample=48000']
    if phone:
        filters.extend(['pan=mono|c0=0.5*c0+0.5*c1','highpass=f=240','lowpass=f=6000'])
    filters.extend([f'volume={gain:.5f}dB', f'atrim=start_sample={round(start*48000)}:end_sample={round(end*48000)}'])
    pcm = subprocess.check_output(['ffmpeg','-nostdin','-v','error','-i',str(path),'-map','0:a:0',
                                   '-ac',str(channels),'-af',','.join(filters),'-f','s16le','-'])
    if len(pcm) != (round(end*48000)-round(start*48000))*2*channels:
        raise ValueError(f'Requested interval outside {path}')
    return pcm


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('before',type=Path)
    p.add_argument('after',type=Path)
    p.add_argument('--start',required=True,type=float)
    p.add_argument('--end',required=True,type=float)
    p.add_argument('--phone',action='store_true',help='Approximate mono playback at 240–6000 Hz')
    p.add_argument('--output',type=Path,required=True,help='WAV, older mix followed by revised mix')
    args = p.parse_args()
    if not (math.isfinite(args.start) and math.isfinite(args.end) and 0<=args.start<args.end):
        p.error('Choose a finite interval on the shared timeline')
    if args.output.suffix.lower() != '.wav' or args.output.resolve() in (args.before.resolve(),args.after.resolve()):
        p.error('Choose a new WAV output path')
    source = [args.before,args.after]
    measured = [levels(path) for path in source]
    # Use the quieter master as reference, then leave identical headroom on both tracks.
    target = min(v[0] for v in measured)
    ceiling = max(0.,max(peak+target-loud+1. for loud,peak in measured))
    target -= ceiling
    gains = [target-loud for loud,_ in measured]
    parts = [sample(file,args.start,args.end,gain,args.phone) for file,gain in zip(source,gains)]
    channels = 1 if args.phone else 2
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with wave.open(str(args.output),'wb') as out:
        out.setparams((channels,2,48000,0,'NONE','not compressed'))
        out.writeframes(parts[0]+bytes(round(.38*48000)*channels*2)+parts[1])
    print(json.dumps(dict(start=args.start,end=args.end,order=['before','after'],gap_seconds=.38,
                          target_program_lufs=round(target,2),program_gain_db=gains,
                          mode='mono 240–6000 Hz' if args.phone else 'stereo'),ensure_ascii=False))


if __name__=='__main__':
    main()
