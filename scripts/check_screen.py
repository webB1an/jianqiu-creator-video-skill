"""Read sampled movie frames at source resolution or a specified playback width."""
import argparse
from fractions import Fraction
import json
from pathlib import Path
import re
import subprocess
import tempfile
from PIL import Image

SK=Path(__file__).resolve().parents[1]
from production_run import digest
from screen_plan import PRODUCTION_NOTE
from evidence_frames import cue_frames, readback_report


def recognize_images(directory, images, *, binary=None):
    """Use the native Vision reader, batching paths to stay below the OS argument limit."""
    if binary is None:
        binary = directory/'ocr'
        subprocess.run(['swiftc', SK/'scripts/read_screen.swift', '-o', binary], check=True)
    output = directory/'read.json'
    rows = []
    for first in range(0, len(images), 128):
        batch = images[first:first+128]
        subprocess.run([binary, output, *batch], check=True)
        result = json.loads(output.read_text())
        if [r['file'] for r in result] != [p.name for p in batch]:
            raise ValueError('OCR results must identify the images in the order supplied')
        rows.extend(result)
    return rows


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('movie',type=Path)
    p.add_argument('--plan',type=Path)
    p.add_argument('--timeline',type=Path,help='Prepared timeline, optional with a native rough-cut plan')
    p.add_argument('--view-width',type=int,help='OCR the full frame at this playback width in pixels')
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--executable',type=Path)
    args=p.parse_args()
    if args.view_width is not None and args.view_width < 1:
        p.error('--view-width must be a positive pixel width')
    plan=json.loads(args.plan.read_text()) if args.plan else None
    timeline = None
    if args.timeline:
        timeline=json.loads(args.timeline.read_text())
        frames=timeline['frames'];fps=timeline['fps']
        ranges=plan['cuts'] if plan else [dict(start=s['start_frame'],end=s['start_frame']+s['frames']) for s in timeline['shots']]
    else:
        if not plan or not plan.get('fps') or not plan.get('cuts'):
            p.error('Provide --timeline or a --plan with fps and cuts')
        ranges=plan['cuts']
        fps=plan['fps'];frames=ranges[-1]['end']
    if not args.timeline or timeline.get('evidence_readings'):
        info=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','v:0',
            '-show_entries','stream=nb_frames,avg_frame_rate','-of','json',args.movie]))['streams'][0]
        if Fraction(info['avg_frame_rate']) != Fraction(str(fps)) or (
            info.get('nb_frames','N/A') != 'N/A' and int(info['nb_frames']) != frames):
            p.error('Movie frame rate or length differs from the supplied frame clock')
    picked=set(range(0,frames,max(1,round(fps*.6))))|{frames-1}
    for item in ranges:
        a,b=item['start'],item['end'];picked.update([a,(a+b)//2,b-1])
    for reading in (timeline or {}).get('evidence_readings', []):
        picked.update(cue_frames(reading, frames))
    picked=sorted(picked)
    expr='+'.join(f'eq(n\\,{n})' for n in picked)
    with tempfile.TemporaryDirectory() as td:
        work=Path(td)
        binary=args.executable or work/'ocr'
        if not args.executable:
            subprocess.run(['swiftc',SK/'scripts/read_screen.swift','-o',binary],check=True)
        filters='select='+expr
        image_options=['-q:v','2']
        suffix='jpg'
        if args.view_width:
            w=args.view_width
            filters+=f",scale={w}:'max(1,round({w}/dar))':flags=lanczos,setsar=1"
            image_options=['-pix_fmt','rgb24']
            suffix='png'
        subprocess.run(['ffmpeg','-v','error','-i',args.movie,'-vf',filters,'-fps_mode','passthrough',
                        *image_options,work/f'fr-%04d.{suffix}'],check=True)
        full=sorted(work.glob(f'fr-*.{suffix}'))
        if len(full)!=len(picked):raise ValueError('Frame index mismatch')
        foot=[]
        view=None
        for img in full:
            with Image.open(img) as im:
                w,h=im.size
                if args.view_width:
                    if w!=args.view_width or (view and view['height']!=h):
                        raise ValueError('Sampled images differ in playback dimensions')
                    view=dict(width=w,height=h,region='full_frame',footer_magnified=False)
                    continue
                lower=im.crop((0,int(h*.925),w,h))
                f=work/('lower'+img.name)
                lower.resize((lower.width*2,lower.height*2),Image.Resampling.LANCZOS).save(f,quality=94)
                foot.append(f)
        readings=recognize_images(work, [*full, *foot], binary=binary)
        count=len(picked)
        samples=[]
        notes=[]
        chrome_text=[]
        distractions=re.compile(r'拒[绝絕]可[选選]|全部接受|管理.*cookie|accept all cookies|reject optional|join tom.{0,3}s hardware|newsletter sign.?up',re.I)
        for idx,n in enumerate(picked):
            wide=readings[idx]['words']; strip=readings[idx+count]['words'] if foot else []
            for b in wide+strip:
                compact=re.sub(r'[\s/|／]','',b['text'])
                if PRODUCTION_NOTE.search(compact):
                    notes.append(dict(frame=n,text=b['text']))
            for cell in wide:
                if distractions.search(cell['text']):
                    chrome_text.append(dict(frame=n,text=cell['text']))
            sample=dict(frame=n,time=round(n/fps,3),words=wide)
            if foot:
                sample['footer']=[b['text'] for b in strip]
            samples.append(sample)
        result=dict(source=args.movie.name,sha256=digest(args.movie),frames=frames,sampled=len(samples),note_matches=notes,possible_site_chrome=chrome_text,samples=samples)
        args.out.parent.mkdir(parents=True,exist_ok=True)
        if view:
            result['view']=view
            checks = readback_report(args.movie, timeline or {}, args.view_width,
                                     lambda folder, images: recognize_images(folder, images, binary=binary),
                                     proof_dir=args.out.parent)
            if checks:
                result['evidence_readback'] = checks
        args.out.write_text(json.dumps(result,ensure_ascii=False,indent=2))
        print(f'{len(samples)} rendered frames, {len(notes)} exact production gloss matches')
if __name__=='__main__':main()
