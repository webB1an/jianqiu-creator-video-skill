"""Inspect consecutive decoded frames with their actual presentation times."""
import argparse
import json
import os
import subprocess
import tempfile
from io import BytesIO
from pathlib import Path
from fractions import Fraction
from PIL import Image, ImageDraw


def display_proxy_size(video, edge):
    """Use the decoded orientation and display aspect ratio, including anamorphic sources."""
    scale = (f"scale=w='if(gte(dar,1),{edge},max(1,round({edge}*dar)))':"
             f"h='if(gte(dar,1),max(1,round({edge}/dar)),{edge})',setsar=1")
    frame = subprocess.check_output(['ffmpeg','-v','error','-i',str(video),'-map','0:v:0',
        '-vf',scale,'-frames:v','1','-c:v','png','-f','image2pipe','-'])
    with Image.open(BytesIO(frame)) as image:
        return image.size


def write_clip(video, target, start, end, first_pts, last_pts, end_pts):
    """Keep the source frame order, variable timing and the coincident mixed audio."""
    if target.suffix.lower() != '.mp4' or target.resolve() == video.resolve():
        raise ValueError('Choose a separate MP4 for the study window')
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as tmp:
        movie = Path(tmp)/'window.mp4'
        audio_end = '' if end_pts is None else f':end={end_pts:.9f}'
        # MP4 derives most sample durations from consecutive DTS values. Its final
        # sample has no successor, so explicitly preserve that source display interval.
        timing = (['-bsf:v',f"setts=duration='if(eq(N,{end-start-1}),round({end_pts-last_pts:.9f}/TB),DURATION)'"]
                  if end_pts is not None else ['-shortest'])
        audio = f'atrim=start={first_pts:.9f}{audio_end},asetpts=PTS-({first_pts:.9f})/TB'
        if end_pts is None:
            audio += ',apad'
        subprocess.run(['ffmpeg','-nostdin','-v','error','-copyts','-i',str(video),
            '-map','0:v:0','-map','0:a:0?',
            '-vf',f'trim=start_frame={start}:end_frame={end},setpts=PTS-STARTPTS,pad=ceil(iw/2)*2:ceil(ih/2)*2',
            '-af',audio,
            '-fps_mode','passthrough','-enc_time_base:v','1:1000000','-video_track_timescale','1000000',
            '-c:v','libx264','-preset','veryfast',
            '-crf','16','-bf','0',*timing,'-pix_fmt','yuv420p','-c:a','aac','-b:a','192k',
            '-movie_timescale','1000000','-movflags','+faststart',str(movie)],check=True)
        stream = json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0','-count_frames',
            '-show_entries','stream=nb_read_frames,time_base,duration','-of','json',str(movie)]))['streams'][0]
        if int(stream['nb_read_frames']) != end-start:
            raise ValueError('Study clip frame count differs from selected source interval')
        if end_pts is not None and abs(float(stream['duration']) - (end_pts-first_pts)) > float(Fraction(stream['time_base']))+2e-6:
            raise ValueError('Study clip did not retain the final source frame duration')
        os.replace(movie, target)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('video',type=Path)
    p.add_argument('--start-frame',required=True,type=int)
    p.add_argument('--frames',type=int,default=24)
    p.add_argument('--out',type=Path,help='Consecutive frame sheet, up to 96 frames')
    p.add_argument('--clip',type=Path,help='Playable native-cadence MP4 with the corresponding source audio if present')
    args=p.parse_args()
    if not (args.out or args.clip) or args.start_frame<0 or args.frames<1 or (args.out and args.frames>96):
        p.error('Select --out and/or --clip and a positive frame count. A single sheet holds up to 96 frames.')
    if args.out and (args.out.resolve()==args.video.resolve() or args.clip and args.out.resolve()==args.clip.resolve()):
        p.error('Write the sheet, study clip and source to different paths')
    info=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0','-show_frames','-show_entries','frame=best_effort_timestamp_time,duration_time,pkt_duration_time:stream=start_time,duration','-of','json',str(args.video)]))
    rows=info['frames']
    start=args.start_frame
    end=min(start+args.frames,len(rows))
    if start>=end: p.error('Starting frame is outside the video')
    if args.clip:
        end_pts = None
        if end<len(rows):
            end_pts=float(rows[end]['best_effort_timestamp_time'])
        elif 'duration' in info['streams'][0]:
            stream = info['streams'][0]
            end_pts=float(stream.get('start_time',0))+float(stream['duration'])
        if end_pts is None or end_pts <= float(rows[end-1]['best_effort_timestamp_time']):
            duration=rows[end-1].get('duration_time') or rows[end-1].get('pkt_duration_time')
            end_pts=(float(rows[end-1]['best_effort_timestamp_time'])+float(duration)
                     if duration and float(duration)>0 else None)
        write_clip(args.video,args.clip,start,end,float(rows[start]['best_effort_timestamp_time']),
                   float(rows[end-1]['best_effort_timestamp_time']),end_pts)
    if not args.out:
        return
    w,h=display_proxy_size(args.video, 288)
    vf=f'trim=start_frame={start}:end_frame={end},setpts=PTS-STARTPTS,scale={w}:{h},setsar=1'
    data=subprocess.check_output(['ffmpeg','-v','error','-i',str(args.video),'-map','0:v:0','-vf',vf,'-fps_mode','passthrough','-frames:v',str(end-start),'-f','rawvideo','-pix_fmt','rgb24','-'])
    if len(data)!=(end-start)*w*h*3: raise ValueError('Decoded frames and timestamps disagree')
    sheet=Image.new('RGB',(4*w,((end-start+3)//4)*(h+19)),'#1a1e1c')
    draw=ImageDraw.Draw(sheet)
    for i in range(end-start):
        offset=i*w*h*3
        im=Image.frombytes('RGB',(w,h),data[offset:offset+w*h*3])
        x=i%4*w; y=i//4*(h+19)
        sheet.paste(im,(x,y))
        draw.text((x+5,y+h+2),f'F{start+i}   {float(rows[start+i]["best_effort_timestamp_time"]):.3f}s',fill='white')
    args.out.parent.mkdir(parents=True,exist_ok=True)
    sheet.save(args.out)
if __name__=='__main__':
    main()
