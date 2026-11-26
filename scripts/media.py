"""Find candidate media and cache checked, credited source files."""
import argparse
import html
import re
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from datetime import date
from urllib.parse import quote
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

session = requests.Session()
session.headers['User-Agent'] = 'CreatorVideoResearch/1.0 (media discovery; attribution retained)'
session.mount('https://', HTTPAdapter(max_retries=Retry(total=2, backoff_factor=1, status_forcelist=(502,503,504))))

def search(provider, query):
    if provider == 'archive-video':
        terms=' '.join(re.findall(r'\w+',query))
        if not terms: raise ValueError('Provide at least one search term')
        expression=f'(title:({terms}) OR subject:({terms})) AND mediatype:movies AND licenseurl:(*creativecommons.org*)'
        res=session.get('https://archive.org/advancedsearch.php',params={'q':expression,'fl[]':['identifier','title','creator','licenseurl'],'rows':30,'output':'json'},timeout=25)
        res.raise_for_status()
        items=[]
        for doc in res.json()['response']['docs']:
            rights=doc.get('licenseurl','')
            if not isinstance(rights,str) or not any(tag in rights for tag in ['/licenses/by/', '/publicdomain/zero/', '/publicdomain/mark/']):continue
            if not doc.get('creator'):continue
            ident=quote(doc['identifier'],safe='')
            meta=session.get('https://archive.org/metadata/'+ident,timeout=25)
            meta.raise_for_status()
            files=[f for f in meta.json().get('files',[]) if f['name'].lower().endswith('.mp4') and int(f.get('size') or 0)>0]
            if files:
                variants=[dict(url='https://archive.org/download/'+ident+'/'+quote(f['name']),bytes=int(f['size']),width=f.get('width'),height=f.get('height'),source=f.get('source')) for f in files]
                # Keep all versions available. Choose an original at a manageable size when possible.
                manageable=[v for v in variants if v['bytes'] <= 150_000_000]
                chosen=max(manageable,key=lambda v:(v['source']=='original',int(v.get('width') or 0),v['bytes'])) if manageable else min(variants,key=lambda v:v['bytes'])
                items.append(dict(title=doc['title'],author=doc['creator'],source='https://archive.org/details/'+ident,rights=rights,url=chosen['url'],bytes=chosen['bytes'],variants=variants))
            if len(items)>=4:break
        return items
    if provider == 'commons-video':
        params=dict(action='query', generator='search', gsrsearch=query+' filetype:video', gsrnamespace=6, gsrlimit=8, prop='imageinfo', iiprop='url|mime|size|extmetadata', format='json', formatversion=2)
        page=session.get('https://commons.wikimedia.org/w/api.php',params=params,timeout=25)
        page.raise_for_status()
        body=page.json()
        if 'error' in body: raise ValueError(body['error'])
        rows=[]
        for item in sorted(body.get('query',{}).get('pages',[]),key=lambda p:p.get('index',0)):
            info=next(iter(item.get('imageinfo',[])),{})
            if not info.get('duration'): continue
            meta=info.get('extmetadata',{})
            def field(key):return html.unescape(re.sub('<[^>]*>','',meta.get(key,{}).get('value',''))).strip()
            rows.append(dict(title=item['title'],url=info['url'],source=info['descriptionurl'],author=field('Artist'),rights=field('LicenseUrl') or field('UsageTerms'),license=field('LicenseShortName'),duration=info['duration'],bytes=info['size'],width=info['width'],height=info['height']))
        return rows
    if provider == 'openverse':
        rows = session.get('https://api.openverse.org/v1/images/', params={'q': query, 'license':'cc0,pdm,by','page_size':8},timeout=25)
        rows.raise_for_status()
        return [dict(title=r['title'], author=r['creator'], source=r['foreign_landing_url'], url=r['url'], rights=r['license_url']) for r in rows.json()['results']]
    if provider == 'pexels':
        key = os.getenv('PEXELS_API_KEY')
        if not key:
            raise ValueError('Pexels video search requires PEXELS_API_KEY (note: Pexels currently paused new key issuance at https://www.pexels.com/api/key/). Use PIXABAY_API_KEY (free at https://pixabay.com/api/docs/), nasa-video, archive-video, or commons-video.')
        rows = session.get('https://api.pexels.com/v1/videos/search',params={'query':query,'per_page':8},headers={'Authorization':key},timeout=25)
        rows.raise_for_status()
        result = []
        for r in rows.json()['videos']:
            files = [f for f in r['video_files'] if f['file_type']=='video/mp4' and f.get('width')]
            if files:
                result.append(dict(title=query,author=r['user']['name'],source=r['url'],url=min(files,key=lambda f:abs(f['width']-1920))['link'],rights='https://www.pexels.com/license/'))
        return result
    if provider == 'nasa-video':
        rows = session.get('https://images-api.nasa.gov/search', params={'q': query, 'media_type': 'video', 'page_size': 8}, timeout=25)
        rows.raise_for_status()
        result = []
        for item in rows.json().get('collection', {}).get('items', []):
            info = (item.get('data') or [{}])[0]
            nasa_id = info.get('nasa_id')
            if not nasa_id:
                continue
            try:
                manifest = session.get('https://images-api.nasa.gov/asset/' + quote(nasa_id, safe=''), timeout=20)
                manifest.raise_for_status()
                hrefs = [x.get('href', '') for x in manifest.json().get('collection', {}).get('items', []) if x.get('href', '').lower().endswith('.mp4')]
            except Exception:
                hrefs = []
            if not hrefs:
                continue
            # Prefer medium/orig HD MP4 over tiny mobile previews.
            chosen = next((u for u in hrefs if '~orig.mp4' in u), None) or next((u for u in hrefs if '~medium.mp4' in u), None) or hrefs[0]
            result.append(dict(
                title=info.get('title', nasa_id),
                author=info.get('photographer') or info.get('center') or 'NASA',
                source='https://images.nasa.gov/details/' + nasa_id,
                url=chosen,
                description=(info.get('description') or '')[:240],
                rights='https://www.nasa.gov/nasa-brand-center/images-and-media/',
                kind='video',
            ))
            if len(result) >= 6:
                break
        return result
    if provider == 'pixabay-video':
        key = os.getenv('PIXABAY_API_KEY')
        if not key:
            raise ValueError('Pixabay video search requires PIXABAY_API_KEY (get a free key after logging in at https://pixabay.com/api/docs/, then export it in ~/.zshenv and ~/.zshrc). Or use keyless nasa-video, archive-video, commons-video.')
        rows = session.get('https://pixabay.com/api/videos/', params={'key': key, 'q': query, 'per_page': 8}, timeout=25)
        rows.raise_for_status()
        result = []
        for hit in rows.json().get('hits', []):
            vids = [v for v in (hit.get('videos') or {}).values() if isinstance(v, dict) and v.get('url')]
            hd = [v for v in vids if int(v.get('width') or 0) >= 1920]
            best = min(hd, key=lambda v: int(v.get('width') or 99999)) if hd else (max(vids, key=lambda v: int(v.get('width') or 0)) if vids else {})
            if best.get('url'):
                result.append(dict(
                    title=hit.get('tags', query),
                    author=hit.get('user', 'Pixabay'),
                    source=hit.get('pageURL', ''),
                    url=best['url'],
                    width=best.get('width'),
                    height=best.get('height'),
                    duration=hit.get('duration'),
                    rights='https://pixabay.com/service/license-summary/',
                    kind='video',
                ))
        return result
    rows = session.get('https://images-api.nasa.gov/search', params={'q':query,'media_type':'image','page_size':10},timeout=25)
    rows.raise_for_status()
    result = []
    for item in rows.json()['collection']['items']:
        info = item['data'][0]
        previews = item.get('links',[])
        if previews:
            result.append(dict(title=info['title'], author=info.get('photographer') or info.get('center'),source='https://images.nasa.gov/details/'+info['nasa_id'], url=previews[0]['href'], description=info.get('description',''),rights='https://www.nasa.gov/nasa-brand-center/images-and-media/'))
    return result

def fetch(manifest):
    rows = json.loads(manifest.read_text())
    folder = manifest.parent / 'assets'
    folder.mkdir(exist_ok=True)
    for row in rows:
        if not all(row.get(k) for k in ('file','source','url','rights','author')) or Path(row['file']).name != row['file']:
            raise ValueError('Each selected source needs a local basename, page, download URL, rights and credit')
        dest = folder / row['file']
        if dest.exists() and row.get('sha256') == digest(dest):
            continue
        with tempfile.NamedTemporaryFile(dir=folder) as tmp:
            with session.get(row['url'],stream=True,timeout=(20,90)) as response:
                response.raise_for_status()
                for chunk in response.iter_content(1024*1024):
                    tmp.write(chunk)
            tmp.flush()
            media = subprocess.check_output(['ffprobe','-v','error','-show_entries','stream=codec_type','-of','csv=p=0',tmp.name],text=True)
            if not any(t in media for t in ('video','audio')):
                raise ValueError('Not decodable media: '+row['file'])
            checksum = digest(Path(tmp.name))
            if row.get('sha256') and row['sha256'] != checksum:
                raise ValueError('Source bytes changed: '+row['file'])
            staged = Path(tmp.name+'.ready')
            shutil.copyfile(tmp.name,staged)
            os.replace(staged,dest)
        row.update(sha256=checksum, retrieved=str(date.today()))
        with tempfile.NamedTemporaryFile('w',dir=manifest.parent,delete=False) as tmp:
            json.dump(rows,tmp,ensure_ascii=False,indent=2)
        os.replace(tmp.name,manifest)

def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):
            value.update(block)
    return value.hexdigest()

def search_video(query):
    """Fan out across all available HD video B-roll providers (keyed if configured, keyless always)."""
    providers = []
    if os.getenv('PEXELS_API_KEY'):
        providers.append('pexels')
    if os.getenv('PIXABAY_API_KEY'):
        providers.append('pixabay-video')
    providers.extend(['nasa-video', 'archive-video', 'commons-video'])
    found = {}
    for prov in providers:
        try:
            found[prov] = search(prov, query)
        except Exception as exc:
            found[prov] = {'error': str(exc)}
    return found


def inspect_quality(path, *, target_fps=30):
    """Pre-edit B-roll quality gate: detect internal scene cuts, blur, black bars, resolution & FPS cadence."""
    import cv2
    import numpy as np
    from fractions import Fraction

    p = Path(path).resolve()
    if not p.is_file():
        raise FileNotFoundError(f'Media not found: {p}')
    raw = subprocess.check_output([
        'ffprobe', '-v', 'error', '-select_streams', 'v:0',
        '-show_entries', 'stream=width,height,r_frame_rate,avg_frame_rate,codec_name:format=duration',
        '-of', 'json', str(p),
    ], text=True)
    meta = json.loads(raw)
    streams = meta.get('streams') or []
    if not streams:
        return dict(file=str(p), ok=False, warnings=['文件不含视频画面轨道'])
    v = streams[0]
    w, h = int(v.get('width') or 0), int(v.get('height') or 0)
    dur = float((meta.get('format') or {}).get('duration') or 0.0)
    r_fps = float(Fraction(v.get('r_frame_rate', '0/1'))) if '/' in str(v.get('r_frame_rate', '')) else 0.0
    a_fps = float(Fraction(v.get('avg_frame_rate', '0/1'))) if '/' in str(v.get('avg_frame_rate', '')) else r_fps
    is_still = p.suffix.lower() in {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.tif', '.tiff'}

    warnings = []
    if min(w, h) < 720:
        warnings.append(f'分辨率偏低（{w}x{h} < 720p），大屏播放易发糊')
    if h > w:
        warnings.append(f'竖屏素材（{w}x{h}），横屏成片需配置 fit: "backdrop" 或竖屏专用裁切')
    if not is_still and r_fps > 0 and abs(r_fps - a_fps) > 0.5:
        warnings.append(f'检测到可变帧率 VFR（r={r_fps:.2f}, avg={a_fps:.2f}），建议核对音画与动作同步')
    elif not is_still and a_fps > 0 and target_fps and abs(a_fps - target_fps) > 0.6 and (target_fps % round(a_fps) != 0):
        warnings.append(f'帧率 {a_fps:.2f}fps 与成片 {target_fps}fps 非整倍关系，快速摇镜可能有轻微顿挫')

    # Sample frames at 2fps (capped at 24 frames) for sharpness, black-bar, and internal scene-cut detection.
    frames = []
    if is_still:
        im = cv2.imread(str(p))
        if im is not None:
            frames.append((0.0, cv2.resize(im, (320, 180), interpolation=cv2.INTER_AREA)))
    else:
        step = max(0.5, dur / 24.0) if dur > 0 else 0.5
        t = 0.0
        while t < max(0.5, dur - 0.1) and len(frames) < 24:
            img_bytes = subprocess.check_output([
                'ffmpeg', '-nostdin', '-v', 'error', '-ss', f'{t:.3f}', '-i', str(p),
                '-frames:v', '1', '-vf', 'scale=320:180', '-f', 'image2pipe', '-vcodec', 'png', '-',
            ])
            if img_bytes:
                im = cv2.imdecode(np.frombuffer(img_bytes, np.uint8), cv2.IMREAD_COLOR)
                if im is not None:
                    frames.append((round(t, 2), im))
            t += step

    sharpness_vals = []
    black_bar_hits = {'top': 0, 'bottom': 0, 'left': 0, 'right': 0}
    scene_cuts = []
    prev_hist = None
    prev_luma = None

    for ts, bgr in frames:
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        sharpness_vals.append(float(cv2.Laplacian(gray, cv2.CV_64F).var()))
        fh, fw = gray.shape
        strip_y, strip_x = max(4, fh // 14), max(4, fw // 14)
        if gray[:strip_y, :].mean() < 14 and gray[:strip_y, :].std() < 6:
            black_bar_hits['top'] += 1
        if gray[-strip_y:, :].mean() < 14 and gray[-strip_y:, :].std() < 6:
            black_bar_hits['bottom'] += 1
        if gray[:, :strip_x].mean() < 14 and gray[:, :strip_x].std() < 6:
            black_bar_hits['left'] += 1
        if gray[:, -strip_x:].mean() < 14 and gray[:, -strip_x:].std() < 6:
            black_bar_hits['right'] += 1

        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [16, 16], [0, 180, 0, 256])
        cv2.normalize(hist, hist)
        luma = float(gray.mean())
        if prev_hist is not None:
            corr = float(cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CORREL))
            dl = abs(luma - prev_luma)
            if corr < 0.42 or (corr < 0.62 and dl > 26.0):
                scene_cuts.append(dict(seconds=ts, hist_corr=round(corr, 3), luma_jump=round(dl, 1)))
        prev_hist, prev_luma = hist, luma

    med_sharp = round(float(np.median(sharpness_vals)), 1) if sharpness_vals else 0.0
    n_f = max(1, len(frames))
    letterbox = (black_bar_hits['top'] / n_f >= 0.7 and black_bar_hits['bottom'] / n_f >= 0.7)
    pillarbox = (black_bar_hits['left'] / n_f >= 0.7 and black_bar_hits['right'] / n_f >= 0.7)
    if med_sharp < 45.0:
        warnings.append(f'画面锐度偏低（Laplacian={med_sharp} < 45），疑似虚焦或过度压缩')
    if letterbox:
        warnings.append('检测到上下黑边（letterbox），建议裁切或设置 crop 去除硬黑边')
    if pillarbox:
        warnings.append('检测到左右黑边（pillarbox），建议裁切或改用 fit: "backdrop"')
    if scene_cuts:
        ts_str = '、'.join(f"{c['seconds']}s" for c in scene_cuts[:4])
        warnings.append(f'检测到 {len(scene_cuts)} 处原片内部跳切（{ts_str}），选入点时请避开跳切取单一连续段')

    return dict(
        file=str(p), width=w, height=h, duration=round(dur, 2), fps=round(a_fps, 3),
        sharpness=med_sharp, letterbox=letterbox, pillarbox=pillarbox,
        internal_scene_cuts=scene_cuts, warnings=warnings, ok=len(warnings) == 0,
    )


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='cmd',required=True)
    s=sub.add_parser('search')
    s.add_argument('provider',choices=['nasa','nasa-video','openverse','commons-video','archive-video','pexels','pixabay-video'])
    s.add_argument('query')
    sv=sub.add_parser('search-video', help='Search all available moving-video B-roll providers at once')
    sv.add_argument('query')
    sub.add_parser('fetch').add_argument('manifest',type=Path)
    qc=sub.add_parser('qc', help='Run B-roll quality gate (internal scene cuts, sharpness, black bars, FPS)')
    qc.add_argument('target',type=Path, help='Video/image file or assets directory')
    qc.add_argument('--target-fps',type=int,default=30)
    a=p.parse_args()
    if a.cmd=='search':
        print(json.dumps(search(a.provider,a.query),ensure_ascii=False,indent=2))
    elif a.cmd=='search-video':
        print(json.dumps(search_video(a.query),ensure_ascii=False,indent=2))
    elif a.cmd=='qc':
        exts = {'.mp4', '.m4v', '.mov', '.webm', '.mkv', '.jpg', '.jpeg', '.png', '.webp'}
        if a.target.is_dir():
            files = sorted(f for f in a.target.iterdir() if f.is_file() and f.suffix.lower() in exts)
            rows = [inspect_quality(f, target_fps=a.target_fps) for f in files]
            print(json.dumps({'directory': str(a.target), 'checked': len(rows),
                              'flagged': sum(1 for r in rows if not r['ok']), 'files': rows},
                             ensure_ascii=False, indent=2))
        else:
            print(json.dumps(inspect_quality(a.target, target_fps=a.target_fps), ensure_ascii=False, indent=2))
    else:
        fetch(a.manifest)
