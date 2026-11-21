"""Make repeatable listening excerpts from the selected movie's mixed audio."""
import hashlib
import html
import json
import re
import subprocess
import tempfile
import wave
from itertools import chain
from pathlib import Path

from review_bundle import digest


def normalized(text):
    return re.sub(r'[^\w]', '', text.casefold())


def review_lines(timeline, subs):
    """Use production sentence boundaries only if they still match the delivered SRT."""
    takes = timeline.get('voice_takes', [])
    units = [take.get('caption_units', []) for take in takes]
    if units and all(units) and list(chain.from_iterable(units)) == [s for _, _, s in subs]:
        first = 0
        for parts in units:
            yield range(first, first + len(parts))
            first += len(parts)
    else:
        for i in range(len(subs)):
            yield range(i, i + 1)


def listening_report(master, folder, timeline, subs, recognized, terms):
    terms = list(dict.fromkeys(t.strip() for t in terms if normalized(t)))
    program_end = timeline['frames'] / timeline['fps']
    matched = set()
    rows = []
    for indices in review_lines(timeline, subs):
        text = ''.join(subs[i][2] for i in indices)
        hits = [term for term in terms if normalized(term) in normalized(text)]
        matched.update(hits)
        changes = []
        if recognized is not None:
            for i in indices:
                caption, heard = subs[i][2], recognized[i]['recognized']
                if normalized(caption) != normalized(heard):
                    changes.append(dict(caption=caption, recognized=heard, at=subs[i][0]))
        if hits or changes:
            rows.append(dict(text=text, terms=hits, differences=changes,
                             start=max(0, round(subs[indices.start][0] - .4, 3)),
                             end=min(program_end, round(subs[indices.stop-1][1] + .4, 3))))
    result = dict(media_sha256=digest(master), lines=rows, unmatched_terms=[t for t in terms if t not in matched])
    if not rows:
        return result
    with tempfile.TemporaryDirectory(dir=folder) as tmp:
        work = Path(tmp)
        decoded = work/'mix.wav'
        subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-copyts', '-start_at_zero', '-i', master,
                        '-map', '0:a:0', '-af', 'aresample=async=1:first_pts=0',
                        '-c:a', 'pcm_s16le', decoded], check=True)
        clips = folder/'listening-clips'
        clips.mkdir(exist_ok=True)
        with wave.open(str(decoded)) as source:
            rate = source.getframerate()
            for row in rows:
                first = round(row['start'] * rate)
                last = min(source.getnframes(), round(row['end'] * rate))
                if first >= last:
                    raise ValueError('No decoded audio at the selected caption interval')
                source.setpos(first)
                clip = work/'line.wav'
                with wave.open(str(clip), 'wb') as out:
                    out.setparams(source.getparams())
                    out.writeframes(source.readframes(last-first))
                dest = clips/(digest(clip) + '.wav')
                clip.replace(dest)
                row.update(clip=str(dest.relative_to(folder)), samples=last-first, sample_rate=rate)
        sections = []
        for row in rows:
            details = ''.join('<p>字幕　' + html.escape(ch['caption']) + '<br>自动转写　' +
                              (html.escape(ch['recognized']) or '（空）') + '</p>' for ch in row['differences'])
            sections.append(f'<section><h2>{row["start"]:.2f} – {row["end"]:.2f} 秒</h2>'
                            f'<audio controls preload="none" src="{row["clip"]}"></audio>'
                            f'<details><summary>核对字幕与转写</summary><p>{html.escape(row["text"])}</p>{details}</details></section>')
        page = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
                '<title>成片听审</title><style>body{max-width:44rem;margin:2rem auto;padding:0 1rem;font:1rem/1.6 system-ui}'
                'section{border-top:1px solid #bbb;margin-top:1rem}h2{font-size:1.1rem}audio{max-width:100%}</style>'
                '<main><h1>成片听审</h1><p>先听整句，再展开字幕核对专名、数字与单位。</p>' + ''.join(sections) + '</main></html>')
        key = hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]
        result['page'] = f'listening-{key}.html'
        temp_page = work/'index.html'
        temp_page.write_text(page, encoding='utf-8')
        temp_page.replace(folder/result['page'])
    return result
