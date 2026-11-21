"""Discover rated Freesound recordings and retain verified CC0 previews in the local library."""
import argparse
import hashlib
import html
import json
import re
import sys
import subprocess
import tempfile
from datetime import date
from pathlib import Path
from urllib.parse import quote, unquote, urlparse
import requests

BANK=Path(__file__).resolve().parents[1]/'assets'/'sfx'

def attrs(s):return {k:html.unescape(v) for k,v in re.findall(r'data-([\w-]+)="([^"]*)"',s)}

def get(url,**kwargs):
    response=requests.get(url,timeout=30,**kwargs)
    response.raise_for_status()
    return response

def metrics(page):
    score=re.search(r'aria-label="Average rating of ([\d.]+)',page)
    count=re.search(r'(?:Overall rating \(|title=")(\d[\d,.]*[Kk]?) ratings?',page)
    if not count:count=re.search(r'bw-rating__count[^>]*>\(([\d,.]+[Kk]?)\)',page)
    return (float(score[1]) if score else None,round(float(count[1].lower().rstrip('k').replace(',',''))*(1000 if count[1].lower().endswith('k') else 1)) if count else None)

def indexed(query):
    response=get('https://api.openverse.org/v1/audio/',params={'q':query,'source':'freesound','license':'cc0','page_size':20})
    cached={json.loads(f.read_text())['page'].rstrip('/'):json.loads(f.read_text()) for f in BANK.glob('*/source.json')}
    items=[]
    for record in response.json()['results']:
        url=record['foreign_landing_url'].rstrip('/')+'/'
        stored=cached.get(url.rstrip('/'),{})
        items.append(dict(title=record['title'],url=url,duration=(record.get('duration') or 0)/1000,stars=stored.get('stars'),votes=stored.get('votes'),downloads=stored.get('downloads'),openverse_id=record['id'],metrics_source='local dated source record' if stored else 'not supplied'))
    return sorted(items,key=lambda r:(r['stars'] or 0,r['votes'] or 0),reverse=True)

def discover(query):
    rows={}
    for order in ('Automatic by relevance','Rating (highest first)'):
        try:
            page=get('https://freesound.org/search/',params={'q':query,'f':'license:"Creative Commons 0"','s':order}).text
        except requests.HTTPError as err:
            if err.response.status_code in (403,429):
                if rows:break
                print('Freesound search unavailable; querying the Openverse CC0 audio index.',file=sys.stderr)
                return indexed(query)
            raise
        for tile in re.split(r'(?=data-sound-id="\d+")',page)[1:]:
            a=attrs(tile)
            if not all(k in a for k in ('sound-id','username','title','num-downloads')):continue
            score,count=metrics(tile)
            url='https://freesound.org/people/'+quote(a['username'])+'/sounds/'+a['sound-id']+'/'
            rows[url]=dict(title=a['title'],url=url,duration=float(a['duration']),stars=score,votes=count,downloads=int(a['num-downloads']))
    # A single five-star vote is a weaker signal than a well-established 4.9.
    def ranking(row):
        votes=row['votes'] or 0
        return (((row['stars'] or 0)*votes+4*10)/(votes+10),row['stars'] or 0,row['downloads'])
    return sorted(rows.values(),key=ranking,reverse=True)

def store(url,tag,openverse_id=None):
    if not re.fullmatch(r'https://freesound\.org/people/[^/?#]+/sounds/\d+/',url):raise ValueError('Use a Freesound sound page')
    if not re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*',tag):raise ValueError('Use a lowercase ASCII tag')
    folder=BANK/(tag+'-'+url.split('/')[-2]);BANK.mkdir(parents=True,exist_ok=True)
    if folder.exists():
        meta=json.loads((folder/'source.json').read_text())
        if hashlib.sha256((folder/'take.wav').read_bytes()).hexdigest()!=meta['wav_sha256']:raise ValueError('Cached take checksum changed')
        return meta
    if openverse_id:
        if not re.fullmatch(r'[0-9a-fA-F-]{36}',openverse_id):raise ValueError('Invalid Openverse identifier')
        r=get('https://api.openverse.org/v1/audio/'+openverse_id+'/').json()
        if (r['foreign_landing_url'].rstrip('/')!=url.rstrip('/') or r['source']!='freesound' or r['license']!='cc0'):
            raise ValueError('CC0 and the checked source page must match the indexed record')
        preview=r['url']
        details=dict(title=r['title'],author=r['creator'],license=r['license_url'],stars=None,votes=None,downloads=None,openverse_id=openverse_id)
    else:
        page=get(url).text
        a=attrs(page);license_link=re.search(r'title="Go to the full license text" href="(https?://creativecommons.org/publicdomain/zero/1.0/)"',page)
        if not license_link:raise ValueError('Verified CC0 required')
        preview=a.get('static-file-url','')
        score,count=metrics(page)
        details=dict(title=a['title'],author=unquote(url.split('/')[4]),license=license_link[1],stars=score,votes=count,downloads=int(a['num-downloads']))
    if urlparse(preview).hostname!='cdn.freesound.org' or not preview.endswith('-hq.mp3'):raise ValueError('Expected an official public HQ preview')
    with tempfile.TemporaryDirectory(dir=BANK) as temp:
        stage=Path(temp)/'item';stage.mkdir()
        raw=get(preview).content
        (stage/'source.mp3').write_bytes(raw)
        command=['ffmpeg','-v','error','-y','-i',str(stage/'source.mp3'),'-ar','48000','-c:a','pcm_s24le',str(stage/'take.wav')]
        subprocess.run(command,check=True)
        length=float(subprocess.check_output(['ffprobe','-v','error','-show_entries','format=duration','-of','csv=p=0',stage/'take.wav']))
        meta=dict(key=folder.name,tag=tag,**details,page=url,preview=preview,kind='public HQ MP3 preview decoded to 48kHz PCM',date=str(date.today()),duration=length,source_sha256=hashlib.sha256(raw).hexdigest(),wav_sha256=hashlib.sha256((stage/'take.wav').read_bytes()).hexdigest())
        (stage/'source.json').write_text(json.dumps(meta,ensure_ascii=False,indent=2))
        stage.rename(folder)
    return meta

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='cmd',required=True)
    sub.add_parser('search').add_argument('query')
    add=sub.add_parser('add');add.add_argument('tag');add.add_argument('url');add.add_argument('--openverse-id',help='Use only after checking the CC0 license on the original page')
    sub.add_parser('list')
    opt=p.parse_args()
    if opt.cmd=='search':result=discover(opt.query)
    elif opt.cmd=='add':
        result=store(opt.url,opt.tag,opt.openverse_id)
        from catalog import build
        build()
    else:result=[json.loads(p.read_text()) for p in sorted(BANK.glob('*/source.json'))]
    print(json.dumps(result,ensure_ascii=False,indent=2))
