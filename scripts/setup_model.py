"""Install pinned offline Mandarin narration model with file integrity checks."""
import hashlib
import os
import tempfile
from pathlib import Path

import requests

REV='a0d5c6a264c0ef92d70d8661d8cc502d79627cd6'
REPO='csukuangfj/vits-melo-tts-zh_en'
root=Path.home()/'.cache/creator-video-models/melo'

def main():
    r=requests.get(f'https://huggingface.co/api/models/{REPO}/tree/{REV}',params={'recursive':'true','expand':'false'},timeout=30)
    r.raise_for_status()
    for row in r.json():
        name=row['path']
        if row['type']!='file' or not (name.startswith('dict/') or name in ('model.int8.onnx','tokens.txt','lexicon.txt','LICENSE','README.md')):continue
        path=root/name
        path.parent.mkdir(parents=True,exist_ok=True)
        def valid(p):
            if not p.exists() or p.stat().st_size!=row['size']:return False
            data=p.read_bytes()
            if 'lfs' in row:return hashlib.sha256(data).hexdigest()==row['lfs']['oid']
            return hashlib.sha1(f'blob {len(data)}\0'.encode()+data).hexdigest()==row['oid']
        if valid(path):continue
        with tempfile.NamedTemporaryFile(dir=path.parent,delete=False) as out:
            part=Path(out.name)
            try:
                with requests.get(f'https://huggingface.co/{REPO}/resolve/{REV}/{name}',stream=True,timeout=90) as res:
                    res.raise_for_status()
                    for chunk in res.iter_content(1024*1024):out.write(chunk)
                out.flush()
                if not valid(part):raise ValueError('Model checksum failed: '+name)
                os.replace(part,path)
            finally:
                part.unlink(missing_ok=True)
if __name__=='__main__':
    main()
