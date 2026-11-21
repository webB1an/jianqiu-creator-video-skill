"""Use one continuous on-camera performance as the show's sound and frame clock."""
import argparse
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import wave

import numpy as np

from color_finish import browser_frame_filter
from production_run import atomic_json, digest
from source_framing import upright_raster
from speech_clock import align, line_identity, observations, timed_captions

RATE=48000


def read_performance(source):
    cmd=['ffmpeg','-nostdin','-v','error','-copyts','-start_at_zero','-i',str(source),'-map','0:a:0',
         '-af','aresample=48000:async=1:first_pts=0','-ac','1','-ar',str(RATE),'-f','s16le','pipe:1']
    pcm=np.frombuffer(subprocess.check_output(cmd),'<i2')
    if not len(pcm) or np.max(np.abs(pcm.astype('i4'))) < 32:
        raise ValueError('The presenter recording has no audible performance')
    info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(source)]))
    video=next((s for s in info['streams'] if s['codec_type']=='video' and not s.get('disposition',{}).get('attached_pic')),None)
    origin=Fraction(info['format'].get('start_time','0'))
    end=Fraction(len(pcm),RATE)
    if video:
        if video.get('duration_ts') is not None:
            picture_end=Fraction(video.get('start_time',str(origin)))+int(video['duration_ts'])*Fraction(video['time_base'])-origin
        else:
            picture_end=Fraction(info['format'].get('duration',str(end)))
        end=max(end,picture_end)
    return pcm,end


def take_wav(data):
    """Keep whole-file consumers working while retaining the uncut programme bus."""
    fingerprint=hashlib.sha256(data).hexdigest()
    target=Path('audio/lines')/f'presenter-{fingerprint}.wav'
    target.parent.mkdir(parents=True,exist_ok=True)
    # Write into the cache atomically. Repair a truncated or modified cache entry.
    try:
        with wave.open(str(target),'rb') as old:
            if (old.getnchannels(),old.getsampwidth(),old.getframerate())==(1,2,RATE) and old.readframes(old.getnframes())==data:
                return target
    except (FileNotFoundError, EOFError, wave.Error):
        pass
    with tempfile.TemporaryDirectory(dir=target.parent) as tmp:
        stage=Path(tmp)/'line.wav'
        with wave.open(str(stage),'wb') as out:
            out.setparams((1,2,RATE,0,'NONE','not compressed'));out.writeframes(data)
        os.replace(stage,target)
    return target


def source_bite(spec,pins):
    """Extract a cited original soundbite on the same programme clock used for picture."""
    name=spec['file']
    start,end=spec['in'],spec['out']
    if (any(type(v) not in (int,float) or not math.isfinite(v) for v in (start,end))
            or not 0 <= start < end):
        raise ValueError('source_audio needs finite in/out seconds in ascending order')
    lo,hi=(round(v*RATE) for v in (start,end))
    source=pins.pin(name)
    af=f'aresample=48000:async=1:first_pts=0,atrim=start_sample={lo}:end_sample={hi},asetpts=N/SR/TB'
    data=subprocess.check_output(['ffmpeg','-nostdin','-v','error','-copyts','-start_at_zero','-i',str(source),
                                  '-map','0:a:0','-af',af,'-ac','1','-ar',str(RATE),'-f','s16le','pipe:1'])
    if not data or len(data)!=(hi-lo)*2:raise ValueError(f'Original soundbite exceeds its audio source: {name}')
    gain=spec.get('gain_db',0)
    if type(gain) not in (float,int) or not math.isfinite(gain):raise ValueError('source_audio.gain_db must be finite')
    if gain:
        values=np.frombuffer(data,'<i2').astype('float64')
        peak=np.abs(values).max()
        if peak and gain>20*math.log10(32767/peak):raise ValueError('The requested soundbite gain would clip')
        if peak:data=np.rint(values*10**(gain/20)).astype('<i2').tobytes()
    return take_wav(data),dict(file=name,sha256=pins.hashes[pins.key(name)],start=lo/RATE,end=hi/RATE,gain_db=gain)


def prepare_performance(edit, pins, fps, stamp):
    config=edit['presenter']
    recording=Path(config['recording'])
    source=pins.pin(recording)
    pcm,duration=read_performance(source)
    frames=math.ceil(duration*fps)
    samples=frames*(RATE//fps)
    rows=[(shot['id'],line,i) for shot in edit['shots'] for i,line in enumerate(shot['lines'])]
    if not rows:raise ValueError('Write the spoken transcript in shots[].lines')
    if any('audio' in line or 'source_audio' in line for _,line,_ in rows):
        raise ValueError('A continuous presenter recording already supplies all spoken lines; assemble edited soundbites before import')
    identities=[line_identity(sid,line,index) for sid,line,index in rows]
    if len({i[1] for i in identities})!=len(identities): raise ValueError('Use unique spoken line ids')
    timing_file=config.get('word_times')
    words=json.loads(pins.pin(timing_file).read_text()) if timing_file else observations(pcm,RATE)
    beats=align(identities,words,len(pcm)/RATE,method='reviewed' if timing_file else 'sensevoice')
    first={}
    for beat in beats:first.setdefault(beat['shot'],math.floor(beat['start']*fps))
    starts=[s.get('start_frame',0 if i==0 else first.get(s['id'])) for i,s in enumerate(edit['shots'])]
    if (not starts or starts[0]!=0 or any(type(n) is not int for n in starts)
            or any(b<=a for a,b in zip(starts,starts[1:]+[frames]))):
        raise ValueError('Order presenter shots from frame 0; specify start_frame for silent shots')
    shots=[dict(id=s['id'],start_frame=a,frames=b-a) for s,a,b in zip(edit['shots'],starts,starts[1:]+[frames])]
    caps=[];takes=[]
    for (sid,line,_),beat in zip(rows,beats):
        a,b=round(beat['start']*RATE),min(len(pcm),round(beat['end']*RATE))
        if b<=a:raise ValueError(f"Empty speech interval in {beat['id']}")
        arr=pcm[a:b]
        path=take_wav(arr.tobytes())
        pins.pin(path)
        units,cuts=timed_captions(line,arr,RATE,beat=beat,origin=a)
        for lo,hi,unit in zip(cuts,cuts[1:],units):
            caps.append(f'{len(caps)+1}\n{stamp(a+lo)} --> {stamp(a+hi)}\n{unit}\n')
        takes.append(dict(shot=sid,file=str(path),sha256=pins.hashes[pins.key(path)],voice='recording',
                          spoken=beat['text'],start_sample=a,end_sample=b,sample_rate=RATE,caption_units=units))
    metadata=dict(file=str(recording),sha256=pins.hashes[pins.key(recording)],sample_rate=RATE,original_samples=len(pcm))
    if timing_file:
        metadata.update(timing_file=timing_file,timing_sha256=pins.hashes[pins.key(timing_file)])
    return [pcm,np.zeros(samples-len(pcm),dtype='<i2')],samples,shots,caps,takes,beats,metadata


def room_frame_filter(video, size, overrides=None):
    width,height,sar=upright_raster(video)
    dar=width*sar/height
    out_w,out_h=size
    scaled_w=max(2,2*(min(out_w,out_h*dar)//2))
    scaled_h=max(2,2*(min(out_h,out_w/dar)//2))
    framing=(f'scale={scaled_w}:{scaled_h}:flags=lanczos,setsar=1,'
             f'pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2')
    colour,report=browser_frame_filter(video,framing,size,raster_size=(width,height),overrides=overrides)
    # Browser JPEG decoders interpret JFIF components using the full-range 601 matrix.
    return colour+',scale=out_color_matrix=bt470bg:out_range=pc,format=yuvj444p',report


def extract_frames(project, out):
    project=project.resolve()
    edit_path=project/'edit.json'
    edit=json.loads(edit_path.read_text())
    timeline=json.loads((project/'timeline.json').read_text())
    binding=timeline.get('presenter_source',{})
    source=(project/edit['presenter']['recording']).resolve(strict=True)
    if timeline.get('edit_sha256')!=digest(edit_path) or binding.get('sha256')!=digest(source):
        raise ValueError('Prepare this edit and recording before extracting room frames')
    output=out if out.is_absolute() else project/out
    output=output.resolve()
    if not output.is_relative_to(project) or output.exists():
        raise ValueError('Choose a new room-frame directory inside the project')
    output.parent.mkdir(parents=True,exist_ok=True)
    fps,frames=timeline['fps'],timeline['frames']
    streams=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','V:0','-show_streams',
                                                '-of','json',str(source)]))['streams']
    if not streams:raise ValueError('The presenter source has no decodable picture')
    picture=streams[0]
    colour,interpretation=room_frame_filter(picture,(1920,1080),edit['presenter'].get('input_color'))
    # A source frame first appears on the project tick at or after its timestamp.
    filters=f'fps={fps}:start_time=0:round=up,{colour}'
    with tempfile.TemporaryDirectory(dir=output.parent) as tmp:
        stage=Path(tmp)/'room';stage.mkdir()
        subprocess.run(['ffmpeg','-nostdin','-v','error','-copyts','-start_at_zero','-i',str(source),'-map',f"0:{picture['index']}",
                        '-vf',filters,'-frames:v',str(frames),'-q:v','2',str(stage/'%04d.jpg')],check=True)
        count=sum(1 for _ in stage.glob('*.jpg'))
        if not count:raise ValueError('The presenter source has no decodable picture')
        metadata=dict(frames=count,fps=fps,source_sha256=binding['sha256'],width=1920,height=1080,
                      input_color=interpretation,output_color='sRGB')
        atomic_json(stage/'sequence.json',metadata)
        if digest(source)!=binding['sha256']:raise ValueError('Recording changed during frame extraction')
        stage.rename(output)
    return dict(room=str(output.relative_to(project)), **metadata)


if __name__=='__main__':
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--project',type=Path,default=Path('.'))
    cli.add_argument('--room',type=Path,required=True,help='New project directory for the synchronized full-frame JPGs')
    args=cli.parse_args()
    try:print(json.dumps(extract_frames(args.project,args.room),ensure_ascii=False))
    except (ValueError,OSError,KeyError,subprocess.CalledProcessError) as e:cli.exit(1,str(e)+'\n')
