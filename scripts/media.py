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
            raise ValueError('Pexels video search requires PEXELS_API_KEY. Use Openverse, a source library, or local video.')
        rows = session.get('https://api.pexels.com/v1/videos/search',params={'query':query,'per_page':8},headers={'Authorization':key},timeout=25)
        rows.raise_for_status()
        result = []
        for r in rows.json()['videos']:
            files = [f for f in r['video_files'] if f['file_type']=='video/mp4' and f.get('width')]
            if files:
                result.append(dict(title=query,author=r['user']['name'],source=r['url'],url=min(files,key=lambda f:abs(f['width']-1920))['link'],rights='https://www.pexels.com/license/'))
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

if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    sub=p.add_subparsers(dest='cmd',required=True)
    s=sub.add_parser('search')
    s.add_argument('provider',choices=['nasa','openverse','commons-video','archive-video','pexels'])
    s.add_argument('query')
    sub.add_parser('fetch').add_argument('manifest',type=Path)
    a=p.parse_args()
    if a.cmd=='search':
        print(json.dumps(search(a.provider,a.query),ensure_ascii=False,indent=2))
    else:
        fetch(a.manifest)
