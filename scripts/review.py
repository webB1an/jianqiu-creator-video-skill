"""Review an exported master with frame samples, silence/black intervals and optional Mandarin ASR."""
import argparse
import io
import json
import re
import subprocess
import tempfile
from difflib import SequenceMatcher
from captioning import clean_parts, width, LIMIT, MIN_CUE_SECONDS
from pathlib import Path
from PIL import Image, ImageDraw

from voice_endings import MODEL_DIR, final_ending_checks
from narration_pace import sentence_pace
from picture_rhythm import inspect_rhythm
from section_pace import measure_sections
from review_bundle import digest, latest, receipt_for, released_snapshot


def seconds(s):
    h,m,sec=s.replace(',','.').split(':')
    return int(h)*3600+int(m)*60+float(sec)

def captions(folder):
    for block in (folder/'captions.srt').read_text().strip().split('\n\n'):
        lines=block.splitlines()
        if len(lines)<3:continue
        a,b=lines[1].split(' --> ')
        yield seconds(a),seconds(b),''.join(lines[2:])

def frame(t, master):
    raw=subprocess.check_output(['ffmpeg','-loglevel','error','-ss',f'{t:.6f}','-i',master,'-vf','scale=320:180:force_original_aspect_ratio=decrease,pad=320:180:(ow-iw)/2:(oh-ih)/2','-frames:v','1','-f','image2pipe','-c:v','mjpeg','-'])
    return Image.open(io.BytesIO(raw))

def pacing_review(master, timeline, subs, windows, bridges, folder):
    fps = timeline['fps']
    total = timeline['frames']
    edit = []
    for shot in timeline['shots']:
        first = shot['start_frame']
        last = first + shot['frames']
        caption_time = sum(max(0., min(end, last/fps) - max(start, first/fps)) for start, end, _ in subs)
        edit.append(dict(shot=shot['id'], start=first, end=last, seconds=round(shot['frames']/fps, 3),
                         program_percent=round(100*shot['frames']/total, 1), caption_seconds=round(caption_time, 3)))
    notes = []
    for window in windows:
        start, end = window['start'], window['end']
        if not 0 <= start < end <= total:
            raise ValueError('Observation lies outside the selected timeline')
        frames = sorted({start, (start+end-1)//2, end-1})
        notes.append(dict(**window, seconds=round((end-start)/fps, 3), sampled_frames=frames,
                          shots=[s['shot'] for s in edit if s['start'] < end and start < s['end']],
                          captions=[s for a,b,s in subs if a < end/fps and start/fps < b],
                          transitions=None if bridges is None else [b['preset'] for b in bridges
                              if b['first_frame'] < end and start < b['end_frame']]))
    if notes:
        picked = sorted({n for row in notes for n in row['sampled_frames']})
        expr = '+'.join(f'eq(n\\,{n})' for n in picked)
        with tempfile.TemporaryDirectory(dir=folder) as tmp:
            subprocess.run(['ffmpeg','-v','error','-i',master,'-map','0:v:0','-an','-vf',
                f"select={expr},scale=390:'max(1,round(390/dar))':flags=lanczos,setsar=1",
                '-fps_mode','passthrough','-frames:v',str(len(picked)),str(Path(tmp)/'%08d.png')],check=True)
            images = dict(zip(picked, sorted(Path(tmp).glob('*.png')), strict=True))
            for i, row in enumerate(notes, start=1):
                samples = row['sampled_frames']
                with Image.open(images[samples[0]]) as first:
                    sheet = Image.new('RGB', (390*len(samples), first.height+24), '#17221e')
                for col, n in enumerate(samples):
                    with Image.open(images[n]) as image:
                        sheet.paste(image,(390*col,0))
                    ImageDraw.Draw(sheet).text((390*col+8,sheet.height-19),f'F{n}   {n/fps:.3f}s', fill='white')
                row['sample_sheet'] = f'watch-{i:02}.png'
                sheet.save(folder/row['sample_sheet'])
    return dict(shots=edit, viewing_windows=notes)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--asr',type=Path,help='Local SenseVoice ONNX model directory')
    p.add_argument('--listen-term', action='append', default=[], metavar='TERM',
                   help='Extract mixed-audio examples for this caption term, repeatable without ASR')
    p.add_argument('--timeline',type=Path,help='Timeline for standalone or historical media')
    p.add_argument('--media',type=Path,help='Specific candidate or historical master')
    p.add_argument('--sidecars',type=Path,help='Captions and covers directory for standalone media')
    p.add_argument('--report-dir',type=Path,help='Report directory, by default next to staged media under qa/')
    args=p.parse_args()
    requested=args.media or latest() or Path('out/final.mp4')
    master=released_snapshot(requested)
    sidecars=args.sidecars or master.parent
    if args.sidecars and args.sidecars.resolve()==requested.parent.resolve():
        sidecars=master.parent
    report_dir=args.report_dir or (master.parent/'qa' if (master.parent/'bundle.json').exists() else Path('qa'))
    report_dir.mkdir(parents=True,exist_ok=True)
    snapshot = master.parent/'timeline.json'
    timeline_path = args.timeline or (snapshot if snapshot.is_file() else Path('timeline.json'))
    tl=json.loads(timeline_path.read_text())
    fps=tl['fps'];end=tl['frames']/fps
    subs=list(captions(sidecars))
    for i,(a,b,_) in enumerate(subs):
        if a<0 or b<=a or b>end+.001 or i and a<subs[i-1][1]:raise ValueError('Invalid SRT timing')
    # Save a sheet per eight shots to keep long videos inspectable.
    for batch in range(0,len(tl['shots']),8):
        shots=tl['shots'][batch:batch+8]
        sheet=Image.new('RGB',(960,206*len(shots)),(27,29,30))
        draw=ImageDraw.Draw(sheet)
        for row,s in enumerate(shots):
            for col,offset in enumerate([0,s['frames']//2,s['frames']-1]):
                t=(s['start_frame']+offset)/fps
                sheet.paste(frame(t,master),(320*col,206*row))
                draw.text((320*col+8,206*row+183),f"{s['id']}   {t:06.2f}s",fill='white')
        sheet.save(report_dir/f'frames-{batch//8+1:02}.jpg',quality=91)
    run=subprocess.run(['ffmpeg','-hide_banner','-nostats','-i',master,'-vf','blackdetect=d=0.45:pix_th=0.05','-af','silencedetect=noise=-50dB:d=0.8','-f','null','-'],capture_output=True,text=True,check=True)
    result=dict(subtitle_cues=len(subs),black_or_silent_intervals=[s.split('] ')[-1] for s in run.stderr.splitlines() if 'black_start:' in s or 'silence_start:' in s or 'silence_end:' in s])
    result['caption_layout']=dict(max_columns=max((width(s) for _,_,s in subs),default=0),overlong=[round(a,3) for a,b,s in subs if width(s)>LIMIT],joined_clauses=[round(a,3) for a,b,s in subs if len(clean_parts(s))!=1],short_flashes=[round(a,3) for a,b,s in subs if round(b-a, 3)<MIN_CUE_SECONDS])
    # Measure actual sentence windows instead of interpreting a model speed as a WPM.
    voice_seconds=sum(b-a for a,b,_ in subs)
    han=sum(len(re.findall(r'[\u3400-\u9fff]',v)) for _,_,v in subs)
    result['pace']=dict(han_characters=han,voice_seconds=round(voice_seconds,3),han_per_program_minute=round(han*60/end,1) if end else None,han_per_voiced_minute=round(han*60/voice_seconds,1) if voice_seconds else None,mean_shot_seconds=round(end/len(tl['shots']),2))
    receipt = receipt_for(master)
    manifest = json.loads(receipt.read_text()) if receipt.is_file() else {}
    windows = manifest.get('viewing_windows', [])
    rhythm_cuts = manifest.get('rhythm_cuts', [])
    if (windows or rhythm_cuts) and (manifest.get('sha256') != digest(master)
                                      or manifest.get('timeline_sha256') not in (None, digest(timeline_path))):
        raise ValueError('Observation windows or rhythm cuts belong to a different timeline or movie')
    technical = master.parent/'technical.json'
    bridges = None
    if receipt.name == 'bundle.json' and receipt.is_file() and technical.is_file():
        bridges = json.loads(technical.read_text()).get('transitions')
    result['pace'].update(pacing_review(master, tl, subs, windows, bridges, report_dir))
    result['pace']['sections'] = measure_sections(tl, rhythm_cuts, subs, windows)
    result['pace']['narration'] = sentence_pace(tl, subs)
    if any('source_clip' in take for take in tl.get('voice_takes', [])):
        from quote_levels import compare
        result['quote_levels'] = compare(master, tl)
    component_windows = manifest.get('component_motion_windows', [])
    if component_windows:
        if manifest.get('sha256') != digest(master) or manifest.get('timeline_sha256') != digest(timeline_path):
            raise ValueError('Component motion windows belong to a different movie or timeline')
        from component_motion import inspect
        result['component_motion'] = inspect(master, component_windows, fps)
    result['pace']['picture_rhythm'] = inspect_rhythm(master, tl, bridges, subs)
    from covers import SIZES
    thumbs=[]
    for name,size in SIZES.items():
        with Image.open((sidecars/'covers')/(name+'.jpg')) as cover:
            if cover.size!=size:raise ValueError('Bad cover size: '+name)
            preview=cover.convert('RGB');preview.thumbnail((320,400));thumbs.append(preview)
    sheet=Image.new('RGB',(sum(im.width for im in thumbs)+48,max(im.height for im in thumbs)+24),'#222823')
    x=12
    for im in thumbs:
        sheet.paste(im,(x,12));x+=im.width+12
    sheet.save(report_dir/'mobile-covers.jpg',quality=92)
    result['cover_sizes']=SIZES
    # New prepared timelines automatically use the installed recognizer, including in standard review runs.
    if args.asr is None and any('ending_check' in t for t in tl.get('voice_takes', [])):
        if all((MODEL_DIR / name).is_file() for name in ('model.int8.onnx', 'tokens.txt')):
            args.asr = MODEL_DIR
    recognize = None
    if args.asr:
        import sherpa_onnx as so
        import numpy as np
        engine=so.OfflineRecognizer.from_sense_voice(model=str(args.asr/'model.int8.onnx'),tokens=str(args.asr/'tokens.txt'),language='zh',num_threads=3,use_itn=False)
        raw=subprocess.check_output(['ffmpeg','-v','error','-i',master,'-vn','-ac','1','-ar','16000','-f','f32le','-'])
        audio=np.frombuffer(raw,'<f4')
        def recognize(a,b):
            stream=engine.create_stream()
            stream.accept_waveform(16000,audio[max(0,round(a*16000)):round(b*16000)])
            engine.decode_stream(stream)
            return re.sub(r'<\|[^>]*?\|>','',stream.result.text).strip()

        def letters(text):
            return re.sub(r'[^\w]','',text).lower()

        pairs=[]
        for a,b,s in subs:
            heard=recognize(a-.06,b+.06)
            similarity=SequenceMatcher(None,letters(s),letters(heard)).ratio()
            pairs.append(dict(start=a,caption=s,recognized=heard,similarity=round(similarity,3)))
        result['recognized_cues']=pairs
        # Short isolated Mandarin clauses lack context; recheck weak readings across the shot.
        result['recognized_shots']=[]
        for shot in tl['shots']:
            lo=shot['start_frame']/fps
            hi=(shot['start_frame']+shot['frames'])/fps
            if any(lo<=p['start']<hi and p['similarity']<.6 for p in pairs):
                result['recognized_shots'].append(dict(id=shot['id'],start=round(lo,3),recognized=recognize(lo,hi)))
    result.update(media_sha256=digest(master), timeline_sha256=digest(timeline_path),
                  captions_sha256=digest(sidecars/'captions.srt'),
                  voice_endings=final_ending_checks(tl, subs, recognize))
    if tl.get('voice_takes'):
        from pronunciation_review import summary
        result['pronunciation_review'] = tl.get('pronunciation_review') or summary(tl['voice_takes'])
    if args.asr or args.listen_term:
        from listening_review import listening_report
        result['listening'] = listening_report(master, report_dir, tl, subs, result.get('recognized_cues'), args.listen_term)
    (report_dir/'content-check.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
