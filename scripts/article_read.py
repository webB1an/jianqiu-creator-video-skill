"""Create a source-linked Chinese article and a complete reading with the production voice."""
import argparse
import fcntl
import hashlib
import html
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit
import wave



def records(article):
    lines = [{'id':'title', 'text':article['title'], 'spoken':article.get('spoken_title',article['title']), 'pause':.4}]
    for index, section in enumerate(article['sections'], 1):
        if section.get('heading'):
            lines.append({'id':f'h{index}', 'text':section['heading'], 'pause':.24})
        for number, row in enumerate(section['sentences'], 1):
            lines.append({**row, 'id':f's{index}-{number}'})
        lines[-1].setdefault('pause', .29)
    # Short standalone titles can fall below the model's minimum duration.
    # Read them together with the following sentence while preserving the displayed title.
    merged = []
    for line in reversed(lines):
        if len(line.get('spoken', line['text']).strip(' 。！？')) < 6 and merged:
            following = merged.pop()
            words = line.get('spoken', line['text']).rstrip('。！？') + '。' + following.get('spoken', following['text'])
            line = {**following, 'id':line['id'], 'text':line['text']+following['text'], 'spoken':words}
        merged.append(line)
    return list(reversed(merged))


def combine(lines, folder, destination):
    with wave.open(str(destination), 'wb') as out:
        for line in lines:
            with wave.open(str(folder / (line['id']+'.wav')), 'rb') as take:
                spec = (take.getnchannels(), take.getsampwidth(), take.getframerate())
                if line is lines[0]:
                    out.setparams((*spec, 0, 'NONE', 'not compressed'))
                if spec != (out.getnchannels(), out.getsampwidth(), out.getframerate()):
                    raise ValueError('Voice takes have different formats')
                out.writeframesraw(take.readframes(take.getnframes()))
                pause = float(line.get('pause', .10))
                if not 0 <= pause <= 10:
                    raise ValueError('Invalid pause')
                out.writeframesraw(bytes(round(pause * spec[2]) * spec[0] * spec[1]))


def article_html(article):
    esc = html.escape
    title = esc(article['title'])
    parts = ['<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">',
             f'<title>{title}</title><style>html{{background:#f3f1e9;color:#18211e}}body{{max-width:38rem;padding:6vh 6vw 10vh;margin:auto;font:18px/1.9 system-ui,sans-serif}}header{{border-top:4px solid #255545}}h1{{font:700 clamp(2.1rem,6vw,3.35rem)/1.18 Georgia,"Songti SC",serif;letter-spacing:-.04em}}h2{{margin-top:2em;font-size:1.18rem}}audio{{display:block;width:100%;margin:.4rem 0 2rem}}.by,.sources,label{{font-size:.85rem;color:#485a50}}.sources{{margin-top:-.5em}}a{{color:#22604c;text-underline-offset:3px}}</style><header>',
             f"<p class=by>{esc(article['byline'])}　·　{esc(article['date'])}</p><h1>{title}</h1></header>",
             '<label for="reading">听全文</label><audio id="reading" controls preload="metadata" src="article.m4a"></audio><main>']
    for section in article['sections']:
        if section.get('heading'):
            parts.append('<h2>'+esc(section['heading'])+'</h2>')
        parts.append('<p>'+esc(''.join(row['text'] for row in section['sentences']))+'</p>')
        links = []
        for key in section.get('sources', []):
            name, url = article['sources'][key]
            if urlsplit(url).scheme not in ('http','https') or not urlsplit(url).netloc:
                raise ValueError(f'Invalid source URL for {key}')
            links.append(f'<a href="{esc(url, quote=True)}">{esc(name)}</a>')
        if links:
            parts.append('<p class=sources>'+' · '.join(links)+'</p>')
    parts.append('</main></html>')
    return '\n'.join(parts)


def article_markdown(article):
    blocks=['# '+article['title'], article['byline']+' · '+article['date']]
    for section in article['sections']:
        if section.get('heading'):
            blocks.append('## '+section['heading'])
        blocks.append(''.join(r['text'] for r in section['sentences']))
        refs=[article['sources'][k] for k in section.get('sources',[])]
        if refs:
            blocks.append(' · '.join(f'[{name}]({url})' for name,url in refs))
    return '\n\n'.join(blocks)+'\n'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('article', type=Path)
    parser.add_argument('--out', type=Path, default=Path('out'))
    parser.add_argument('--takes', type=Path, default=Path('audio/article'))
    parser.add_argument('--speed', type=float, default=1.04)
    args=parser.parse_args()
    article=json.loads(args.article.read_text())
    page=article_html(article)
    lines=records(article)
    args.out.mkdir(parents=True, exist_ok=True)
    args.takes.mkdir(parents=True, exist_ok=True)
    with (args.takes/'.article.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        build(args, page, lines, article_markdown(article))


def build(args, page, lines, markdown):
    from jianqiu_voice import JianqiuVoice
    with tempfile.TemporaryDirectory(dir=args.out) as tmp, tempfile.TemporaryDirectory(dir=args.takes) as cache:
        staging=Path(tmp)
        takes=Path(cache)
        manifest=args.takes/'voicecast.json'
        old=json.loads(manifest.read_text()) if manifest.exists() else {}
        engine=JianqiuVoice()
        previous={r['id']:r for r in old.get('takes',[])} if old.get('reference_sha256')==engine.profile['prompt_sha256'] and old.get('num_steps')==engine.profile['num_steps'] and old.get('synthesis_signature',engine.signature)==engine.signature else {}
        kept={}
        missing=[]
        for line in lines:
            row=previous.get(line['id'])
            path=args.takes/(line['id']+'.wav')
            if (row and path.is_file() and row['text']==line['text'] and
                    row['spoken']==line.get('spoken',line['text']) and row['speed']==line.get('speed',args.speed) and
                    row.get('voice')=='jianqiu-zipvoice' and hashlib.sha256(path.read_bytes()).hexdigest()==row['sha256']):
                os.link(path,takes/path.name)
                kept[line['id']]=row
            else:
                missing.append(line)
        report=old
        if missing:
            (staging/'lines.json').write_text(json.dumps(missing,ensure_ascii=False))
            subprocess.run([sys.executable, Path(__file__).with_name('voicecast.py'), staging/'lines.json',
                            '--out',takes,'--voice','jianqiu-zipvoice','--speed',str(args.speed)],check=True)
            report=json.loads((takes/'voicecast.json').read_text())
            kept.update({row['id']:row for row in report['takes']})
        report['takes']=[kept[line['id']] for line in lines]
        report['synthesis_signature']=engine.signature
        (takes/'voicecast.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
        combine(lines,takes,staging/'reading.wav')
        audio=staging/'article.m4a'
        subprocess.run(['ffmpeg','-v','error','-i',staging/'reading.wav','-af','loudnorm=I=-16:TP=-1.5:LRA=8',
                        '-c:a','aac','-b:a','128k','-ar','48000','-movflags','+faststart', audio],check=True)
        subprocess.run(['ffmpeg','-v','error','-xerror','-i',audio,'-f','null','-'],check=True)
        (staging/'article.html').write_text(page,encoding='utf-8')
        (staging/'article.md').write_text(markdown,encoding='utf-8')
        replacements=[(takes/(line['id']+'.wav'),args.takes/(line['id']+'.wav')) for line in missing]
        replacements.extend([(takes/'voicecast.json',manifest),(audio,args.out/audio.name),(staging/'article.html',args.out/'article.html'),(staging/'article.md',args.out/'article.md')])
        done=[]
        try:
            for src,dst in replacements:
                backup=src.with_suffix(src.suffix+'.previous')
                if dst.exists():
                    os.link(dst,backup)
                os.replace(src,dst)
                done.append((dst,backup))
        except OSError:
            for dst,backup in reversed(done):
                if backup.exists():
                    os.replace(backup,dst)
                else:
                    dst.unlink(missing_ok=True)
            raise


if __name__ == '__main__':
    main()
