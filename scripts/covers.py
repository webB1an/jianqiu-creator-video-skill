"""Render three independently composed covers from a project cover.json or edit.json."""
import json
import subprocess
import tempfile
from pathlib import Path
from PIL import Image,ImageDraw,ImageOps
from identity import AMBER,INK,PAPER,comparison_color,face,lockup,put

SIZES={'16x9':(1920,1080),'9x16':(1080,1920),'3x4':(1080,1440)}

def window(img,size,focus,where,span):
    # span is the displayed photo width divided by the cover width.
    w,h=size;scale=w*span/img.width
    tx=focus[0]*img.width-where[0]*w/scale;ty=focus[1]*img.height-where[1]*h/scale
    return img.transform(size,Image.Transform.AFFINE,(1/scale,0,tx,0,1/scale,ty),Image.Resampling.BICUBIC,fillcolor=img.getpixel((0,0)))

def cover(src,cfg,ratio,comparison_roles=None):
    w,h=SIZES[ratio];land=w>h
    layouts=cfg.get('layouts',{});opts=layouts.get(ratio,{})
    xy=opts.get('subject_at',[.72,.43] if land else [.53,.58])
    span=opts.get('photo_width',max(1,h/w*src.width/src.height))
    im=window(src,(w,h),cfg.get('focus',[.5,.5]),xy,span)
    # Local scrim behind the typography. Preserve the subject and its contrast.
    mask=Image.new('L',(w,1) if land else (1,h))
    vals=[round(145*max(0,1-i/(mask.width*.73 if land else mask.height*.6))) for i in range(max(mask.size))]
    mask.putdata(vals);im.paste(INK,(0,0,w,h),mask.resize((w,h)))
    x=round(w*(.065 if land else .082));top=round(h*(.071 if land else .065))
    logo=lockup(round(w*(.054 if land else .095)))
    im.paste(logo,(x,top),logo)
    lines=opts.get('lines',cfg['lines'])
    if not 1<=len(lines)<=3 or any(not l.strip() for l in lines):raise ValueError('Each layout needs 1–3 title lines')
    target=w*(.56 if land else .85)
    font_size=int(w*(.103 if land else .142))
    while max(face('serif',font_size).getlength(l) for l in lines)>target:font_size-=1
    start=round(h*(.33 if land else .214))
    if cfg.get('kicker'):
        put(im,(x+4,start-round(font_size*.47)),cfg['kicker'],max(20,round(font_size*.17)),'sans',AMBER)
    line_entities=cfg.get('line_entities',{})
    if not isinstance(line_entities,dict):
        raise ValueError('line_entities maps exact cover lines to comparison entities')
    for i,line in enumerate(lines):
        color=comparison_color(comparison_roles,line_entities.get(line)) if line in line_entities else PAPER
        put(im,(x,start+int(i*font_size*1.08)),line,font_size,'serif',color)
    return im

def run(edit,folder='out/covers', *, input_path=Path):
    cover_file = input_path('cover.json')
    cfg=json.loads(cover_file.read_text()) if cover_file.exists() else {}
    title=edit.get('title') or '见秋手记'
    cfg.setdefault('lines',[title] if len(title)<8 else [title[:(len(title)+1)//2],title[(len(title)+1)//2:]])
    source_name=cfg.get('image',edit['shots'][0]['visual'])
    if 'image' not in cfg and (assets_file := input_path('assets.json')).exists():
        for entry in json.loads(assets_file.read_text()):
            candidate=Path('assets')/entry['file']
            if candidate.suffix.lower() in {'.jpg','.jpeg','.png','.webp'} and input_path(candidate).is_file():
                source_name=candidate
                break
    source=input_path(source_name)
    with tempfile.TemporaryDirectory() as tmp:
        if source.suffix.lower() not in {'.png','.jpg','.jpeg','.webp'}:
            still=Path(tmp)/'source.png'
            subprocess.run(['ffmpeg','-v','error','-ss',str(cfg.get('time',.1)),'-i',str(source),'-frames:v','1',str(still)],check=True)
            source=still
        with Image.open(source) as f:src=ImageOps.exif_transpose(f).convert('RGB')
        folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
        for ratio in SIZES:
            cover(src,cfg,ratio,edit.get('comparison_roles')).save(folder/(ratio+'.jpg'),quality=95,subsampling=0)
if __name__=='__main__':run(json.loads(Path('edit.json').read_text()))
